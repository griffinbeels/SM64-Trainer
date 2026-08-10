# tests/test_caused.py
"""The caused-moment detector: what the PLAYER caused, read off the object.

Every rule here was measured before it was written — his 2026-08-07 pool
capture (`data/object_pool_probe.jsonl`, 1,386 changes), replayed against
these exact rules, journals 9 rows: 3 blue-coin-switch presses, 4 goomba
squishes, 2 bob-omb explosions, and nothing from any level load. The numbers
and the two mis-readings the capture corrected (oHealth transitions are the
engine ARMING a hitbox on proximity, not a defeat; the ledger's "goomba
2048 -> 0" was pool initialisation) are in detectors/caused.py's docstring.
"""
import struct
from datetime import datetime, timezone

from sm64_events.core.snapshot import CausedState, GameSnapshot, SnapshotReader
from sm64_events.detectors.caused import CausedMomentDetector
from sm64_events.detectors.moment import MOMENTS, MomentDetector
from sm64_events.memory import addresses as A
from sm64_events.memory.buffer import BufferMemory

SWITCH = A.CAUSED_BEHAVIOURS["bhvBlueCoinSwitch"][0]
GOOMBA = A.CAUSED_BEHAVIOURS["bhvGoomba"][0]
BOBOMB = A.CAUSED_BEHAVIOURS["bhvBobomb"][0]

SWITCH_POS = (-2500.0, 384.0, -250.0)   # his WF switch, 3 presses, byte-identical


def obj(slot, behaviour, action=0, health=0, home=(0.0, 0.0, 0.0),
        pos=(0.0, 0.0, 0.0)) -> CausedState:
    return CausedState(slot=slot, behaviour=behaviour, action=action,
                       health=health, home=home, pos=pos)


def snap(timer, caused=(), level=24, area=1, **overrides) -> GameSnapshot:
    defaults = dict(
        wall_time_utc=datetime(2026, 8, 7, tzinfo=timezone.utc),
        global_timer=timer, mario_action=0x04000440, mario_action_timer=0,
        num_stars=5, last_completed_course=1, last_completed_star=3,
        igt_overall=300, curr_level=level, curr_area=area,
        caused=tuple(caused))
    defaults.update(overrides)
    return GameSnapshot(**defaults)


def run(snaps, detector=None):
    det = detector or CausedMomentDetector()
    events = []
    for prev, curr in zip(snaps, snaps[1:]):
        events.extend(det.process(prev, curr))
    return events


# -- the switch press ---------------------------------------------------------
# Measured: oAction IDLE 0 -> RECEDING 1 at the press, 1 -> 2 six frames later
# as the switch stays down (decomp BLUE_COIN_SWITCH_ACT_* agrees). The press
# is the 0 -> 1 edge and nothing else.

def test_a_switch_press_fires_on_the_0_to_1_edge():
    events = run([snap(100, [obj(9, SWITCH, action=0, pos=SWITCH_POS)]),
                  snap(101, [obj(9, SWITCH, action=1, pos=SWITCH_POS)])])
    assert len(events) == 1
    ev = events[0]
    assert ev.type == "moment_reached" and ev.frame == 101
    assert ev.payload["kind"] == "switch_press"
    assert ev.payload["ordinal"] == 1
    assert ev.payload["level"] == 24 and ev.payload["area"] == 1


def test_the_switch_staying_down_is_not_a_second_press():
    events = run([snap(100, [obj(9, SWITCH, action=0, pos=SWITCH_POS)]),
                  snap(101, [obj(9, SWITCH, action=1, pos=SWITCH_POS)]),
                  snap(107, [obj(9, SWITCH, action=2, pos=SWITCH_POS)])])
    assert len(events) == 1


def test_the_landmark_takes_the_AT_REST_position():
    """The switch's model SINKS while pressed (his capture: y 384 at the
    0 -> 1 edge, 344 one press-tick later), and a scriptless object is keyed
    by where it stands — so the key must come from the tick BEFORE the edge,
    or a slow poll names a half-sunk switch a different switch."""
    sunk = (-2500.0, 374.0, -250.0)
    events = run([snap(100, [obj(9, SWITCH, action=0, pos=SWITCH_POS)]),
                  snap(101, [obj(9, SWITCH, action=1, pos=sunk)])])
    landmark = events[0].payload["landmark"]
    assert landmark["pos"] == [-2500, 384, -250]
    assert landmark["nameable"] is True


# -- enemy defeats ------------------------------------------------------------
# A goomba dies into the engine's SHARED attacked actions (knockback 100/101,
# squished 102 — decomp object_constants.h); its walk/aggro/jump cycle is
# 0/1/2 and must stay silent. A bob-omb dies into ITS OWN explode state
# (action 3), and its oHealth is proximity noise (caused.py docstring).

def test_a_squished_goomba_fires_enemy_defeated():
    home = (-2713.0, 152.0, 5778.0)
    events = run([snap(100, [obj(19, GOOMBA, action=0, home=home)]),
                  snap(101, [obj(19, GOOMBA, action=102, home=home)])])
    assert len(events) == 1
    assert events[0].payload["kind"] == "enemy_defeated"
    assert events[0].payload["landmark"]["home"] == [-2713, 152, 5778]


def test_a_goomba_jumping_or_aggroing_is_not_a_defeat():
    events = run([snap(100, [obj(19, GOOMBA, action=0)]),
                  snap(101, [obj(19, GOOMBA, action=2)]),
                  snap(102, [obj(19, GOOMBA, action=0)]),
                  snap(103, [obj(19, GOOMBA, action=1)])])
    assert events == []


def test_a_bobomb_exploding_fires_and_its_chase_does_not():
    events = run([snap(100, [obj(63, BOBOMB, action=0)]),
                  snap(101, [obj(63, BOBOMB, action=2)]),    # chase = aggro
                  snap(102, [obj(63, BOBOMB, action=3)])])   # explode
    assert [e.payload["kind"] for e in events] == ["enemy_defeated"]
    assert events[0].frame == 102


def test_a_bobomb_health_write_is_proximity_not_a_defeat():
    """The hitbox struct inits health 0, written when the object's logic first
    runs with Mario in range — six far-apart bob-ombs 'died' in 50 frames of
    his capture while Mario FLEW past them. Health is not a defeat signal."""
    events = run([snap(100, [obj(63, BOBOMB, action=0, health=2048)]),
                  snap(101, [obj(63, BOBOMB, action=0, health=0)])])
    assert events == []


# -- the filters --------------------------------------------------------------

def test_a_level_load_fires_nothing_for_its_whole_tail():
    """At frame 1392613 of his capture a goomba, two bob-ombs, King Bob-omb
    and an exclamation box all 'changed' on ONE frame — the pool initialising.
    Every diff inside the load tail is the arrival's, not the player's."""
    events = run([
        snap(100, [obj(19, GOOMBA, action=0)], level=24),
        # the edge tick itself: same slot now holds a goomba mid-anything
        snap(101, [obj(19, GOOMBA, action=102)], level=9),
        # still inside the 60-frame tail
        snap(140, [obj(19, GOOMBA, action=0)], level=9),
        snap(150, [obj(19, GOOMBA, action=102)], level=9),
    ])
    assert events == []


def test_after_the_tail_expires_the_same_edge_fires():
    # Dense polls, as the real 60 Hz stream is — a fixture that leaps the
    # clock trips the discontinuity guard instead of testing the tail.
    quiet = [snap(frame, [obj(19, GOOMBA, action=0)], level=9)
             for frame in range(101, 170, 7)]
    events = run([snap(100, [obj(19, GOOMBA, action=0)], level=24)]
                 + quiet
                 + [snap(170, [obj(19, GOOMBA, action=102)], level=9)])
    assert len(events) == 1


def test_an_area_edge_suppresses_like_a_level_edge():
    events = run([
        snap(100, [obj(9, SWITCH, action=0, pos=SWITCH_POS)], area=1),
        snap(101, [obj(9, SWITCH, action=1, pos=SWITCH_POS)], area=2),
    ])
    assert events == []


def test_a_forward_timer_jump_is_a_discontinuity_not_a_gesture():
    """A savestate loaded to a later point in the SAME area moves nothing the
    place filters can see — only the clock jumps. A dead goomba restored by
    the state must not read as a fresh kill."""
    events = run([snap(100, [obj(19, GOOMBA, action=0)]),
                  snap(400, [obj(19, GOOMBA, action=102)])])
    assert events == []


def test_a_slot_reused_by_another_behaviour_is_first_sight_not_an_edge():
    events = run([snap(100, [obj(19, BOBOMB, action=0)]),
                  snap(101, [obj(19, GOOMBA, action=102)])])
    assert events == []


def test_a_pool_wide_burst_is_dropped_even_outside_a_tail():
    """Three 'defeats' on one tick is a pool write, not a player gesture —
    the backstop for whatever discontinuity the place and clock rules miss."""
    goombas = lambda action: [obj(19, GOOMBA, action=action, home=(1, 0, 1)),
                              obj(20, GOOMBA, action=action, home=(2, 0, 2)),
                              obj(21, GOOMBA, action=action, home=(3, 0, 3))]
    events = run([snap(100, goombas(0)), snap(101, goombas(102))])
    assert events == []


def test_backward_timer_self_heals_and_resets_ordinals():
    det = CausedMomentDetector()
    run([snap(100, [obj(9, SWITCH, action=0, pos=SWITCH_POS)]),
         snap(101, [obj(9, SWITCH, action=1, pos=SWITCH_POS)])], det)
    # savestate back: ordinals belong to a run no longer happening
    events = run([snap(101, [obj(9, SWITCH, action=1, pos=SWITCH_POS)]),
                  snap(50, [obj(9, SWITCH, action=0, pos=SWITCH_POS)]),
                  snap(55, [obj(9, SWITCH, action=0, pos=SWITCH_POS)]),
                  snap(56, [obj(9, SWITCH, action=1, pos=SWITCH_POS)])], det)
    assert len(events) == 1
    assert events[0].payload["ordinal"] == 1


def test_ordinals_count_per_kind_and_reset_resets_them():
    det = CausedMomentDetector()
    first = run([snap(100, [obj(9, SWITCH, action=0, pos=SWITCH_POS)]),
                 snap(101, [obj(9, SWITCH, action=1, pos=SWITCH_POS)]),
                 snap(105, [obj(9, SWITCH, action=0, pos=SWITCH_POS)]),
                 snap(106, [obj(9, SWITCH, action=1, pos=SWITCH_POS)])], det)
    assert [e.payload["ordinal"] for e in first] == [1, 2]
    det.reset()
    again = run([snap(300, [obj(9, SWITCH, action=0, pos=SWITCH_POS)]),
                 snap(301, [obj(9, SWITCH, action=1, pos=SWITCH_POS)])], det)
    assert again[0].payload["ordinal"] == 1


def test_the_payload_matches_the_moment_shape():
    """One event type, one shape: a consumer that reads a door moment must be
    able to read a caused one without a second code path."""
    events = run([snap(100, [obj(9, SWITCH, action=0, pos=SWITCH_POS)]),
                  snap(101, [obj(9, SWITCH, action=1, pos=SWITCH_POS)])])
    payload = events[0].payload
    for key in ("kind", "ordinal", "landmark", "level", "area", "action",
                "igt_frames", "igt_source", "igt", "action_timer", "counter"):
        assert key in payload, key
    assert payload["igt_frames"] == 300 + 1 + MomentDetector.DISPLAY_LAG_FRAMES


# -- the registry -------------------------------------------------------------

def test_every_caused_kind_is_a_MOMENTS_row():
    """The kind registry stays ONE registry: labels, the builder's vocabulary
    and the timeline all read MOMENTS, so a caused kind missing there ships a
    kind no surface can say."""
    kinds = {m.kind for m in MOMENTS}
    for _, kind, _ in A.CAUSED_BEHAVIOURS.values():
        assert kind in kinds, kind


def test_the_caused_rows_never_collide_with_mario_action_rows():
    """A MOMENTS row with actions is Mario's; a row without is supplied by
    caused.py. A kind claiming both would double-journal one gesture."""
    for moment in MOMENTS:
        caused_kinds = {kind for _, kind, _ in A.CAUSED_BEHAVIOURS.values()}
        if moment.kind in caused_kinds:
            assert moment.actions == frozenset(), moment.kind


def test_the_pointer_table_matches_the_shipped_catalogue():
    """src cannot import tools/ (the frozen exe does not carry it), so the
    pointers are stated twice and COMPARED — the same pattern
    test_cross_language_parity.py holds the JS copies to."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import corpus_behaviors
    segmented = {sym: seg for seg, sym in corpus_behaviors.BEHAVIORS}
    for symbol, (pointer, _, _) in A.CAUSED_BEHAVIOURS.items():
        assert symbol in segmented, f"{symbol} is not in the catalogue"
        derived = (corpus_behaviors.BEHAVIOR_SEGMENT_BASE_US
                   + (segmented[symbol] - 0x13000000))
        assert pointer == derived, (
            f"{symbol}: table says {pointer:#010x}, catalogue derives "
            f"{derived:#010x}")


# -- the reader ---------------------------------------------------------------

def _write_object(mem, slot, behaviour, action=0, health=0,
                  home=(0.0, 0.0, 0.0), pos=(0.0, 0.0, 0.0)):
    base = A.OBJECT_POOL + slot * A.OBJECT_SIZE
    mem.write_u32(base + A.OBJECT_BEHAVIOR, behaviour)
    mem.write_u32(base + A.OBJECT_ACTION, action & 0xFFFFFFFF)
    mem.write_u32(base + A.OBJECT_HEALTH, health & 0xFFFFFFFF)
    for offset, triple in ((A.OBJECT_HOME_POS, home), (A.OBJECT_POS, pos)):
        for axis, value in enumerate(triple):
            word = struct.unpack(">I", struct.pack(">f", value))[0]
            mem.write_u32(base + offset + 4 * axis, word)


def test_the_reader_reads_a_watched_slot_end_to_end():
    """One known input through the REAL reader: the instrument's first output
    is asserted, not just its parts (memory lesson: verifying parts passes
    while the chain is broken)."""
    mem = BufferMemory()
    _write_object(mem, 9, SWITCH, action=1, home=(0.0, 0.0, 0.0),
                  pos=SWITCH_POS)
    _write_object(mem, 10, 0x80221234)   # unwatched behaviour: invisible
    caught = SnapshotReader(mem).read().caused
    assert len(caught) == 1
    state = caught[0]
    assert state.slot == 9 and state.behaviour == SWITCH
    assert state.action == 1
    assert state.pos == SWITCH_POS
