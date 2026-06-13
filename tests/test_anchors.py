# tests/test_anchors.py
from datetime import datetime, timezone

import pytest

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.anchors import BOOT_TIMER_MAX, AnchorDetector

# ACT_IDLE is a PASSIVE_ACTIONS member — using it as the snap default means
# a snap-pair that never leaves idle produces mario_acted=False in payloads.
ACT_IDLE = 0x0C400201
ACT_WALKING = 0x04000440  # not in PASSIVE_ACTIONS -> counts as "acted"
ACT_QUICKSAND_DEATH = 0x00021312  # in DEATH_ACTIONS -> involuntary


def snap(timer: int, igt: int = 0, action: int = ACT_IDLE,
         level: int = 0, area: int = 0) -> GameSnapshot:
    return GameSnapshot(
        wall_time_utc=datetime(2026, 6, 10, tzinfo=timezone.utc),
        global_timer=timer, mario_action=action, mario_action_timer=0,
        num_stars=5, last_completed_course=1, last_completed_star=3,
        igt_overall=igt, curr_level=level, curr_area=area)


def test_igt_drop_to_zero_emits_practice_reset():
    events = AnchorDetector().process(snap(1000, igt=500), snap(1002, igt=0))
    assert len(events) == 1
    ev = events[0]
    assert ev.type == "practice_reset" and ev.frame == 1002
    assert ev.payload == {"igt_frames_before": 500, "mario_acted": False,
                          "paused_frames_before": 0, "acted_tracking": True,
                          "action": ACT_IDLE, "prev_action": ACT_IDLE,
                          "save_pending": False, "frames_since_door": None}


def test_igt_drop_to_small_value_still_practice_reset():
    # the poll may land a few frames after the zeroing
    events = AnchorDetector().process(snap(1000, igt=500), snap(1004, igt=4))
    assert len(events) == 1 and events[0].type == "practice_reset"


def test_igt_running_normally_is_silent():
    assert AnchorDetector().process(snap(1000, igt=500), snap(1001, igt=501)) == []
    assert AnchorDetector().process(snap(1000, igt=500), snap(1001, igt=500)) == []


def test_igt_drop_to_large_value_is_not_a_practice_reset():
    # e.g. a Usamune timer-mode change; not a retry anchor
    assert AnchorDetector().process(snap(1000, igt=500), snap(1001, igt=300)) == []


def test_backward_global_timer_emits_state_loaded():
    events = AnchorDetector().process(snap(5000, igt=900), snap(3000, igt=120))
    assert len(events) == 1
    ev = events[0]
    assert ev.type == "state_loaded" and ev.frame == 3000
    assert ev.payload == {"igt_frames_restored": 120, "mario_acted": False,
                          "paused_frames_before": 0, "acted_tracking": True,
                          "action": ACT_IDLE, "prev_action": ACT_IDLE,
                          "save_pending": False, "frames_since_door": None}


def test_backward_jump_into_boot_range_is_left_to_game_reset():
    assert AnchorDetector().process(snap(5000, igt=900), snap(50, igt=0)) == []


def test_state_loaded_takes_priority_over_practice_reset():
    # a load that also restores a near-zero IGT must classify as state_loaded
    events = AnchorDetector().process(snap(5000, igt=900), snap(3000, igt=3))
    assert [e.type for e in events] == ["state_loaded"]


def test_u16_wraparound_is_not_a_practice_reset():
    assert AnchorDetector().process(snap(1000, igt=65535), snap(1002, igt=0)) == []


def test_igt_drop_to_threshold_exactly_fires():
    events = AnchorDetector().process(snap(1000, igt=500), snap(1002, igt=30))
    assert len(events) == 1 and events[0].type == "practice_reset"


def test_igt_drop_just_above_threshold_is_silent():
    assert AnchorDetector().process(snap(1000, igt=500), snap(1002, igt=31)) == []


def test_backward_jump_to_exactly_boot_max_is_state_loaded():
    events = AnchorDetector().process(snap(5000, igt=900), snap(BOOT_TIMER_MAX, igt=5))
    assert len(events) == 1 and events[0].type == "state_loaded"


# ---------------------------------------------------------------------------
# Activity flag tests
# ---------------------------------------------------------------------------

def test_action_excursion_then_reset_yields_mario_acted_true():
    d = AnchorDetector()
    # Frame 1: idle -> walking (non-passive: sets _acted=True)
    d.process(snap(1000, igt=100), snap(1001, igt=101, action=ACT_WALKING))
    # Frame 2: reset arrives
    events = d.process(snap(1001, igt=500), snap(1002, igt=0))
    assert len(events) == 1
    assert events[0].payload["mario_acted"] is True


def test_activity_flag_resets_after_anchor():
    d = AnchorDetector()
    # First excursion + reset
    d.process(snap(1000, igt=100), snap(1001, igt=101, action=ACT_WALKING))
    d.process(snap(1001, igt=500), snap(1002, igt=0))  # anchor fires, flag resets
    # Second pair — no action, then another reset
    events = d.process(snap(1002, igt=200), snap(1003, igt=0))
    assert len(events) == 1
    assert events[0].payload["mario_acted"] is False


def test_action_on_anchor_tick_itself_is_swallowed():
    # prev=idle, curr=walking+igt_drop: anchor fires with mario_acted=False
    # (the walk on the anchor tick belongs to the warp/spawn, not the attempt)
    d = AnchorDetector()
    prev = snap(1000, igt=500)
    curr = snap(1001, igt=0, action=ACT_WALKING)
    events = d.process(prev, curr)
    assert len(events) == 1
    assert events[0].payload["mario_acted"] is False


def test_idle_only_pairs_produce_mario_acted_false_in_state_loaded():
    events = AnchorDetector().process(snap(5000, igt=900), snap(3000, igt=120))
    assert events[0].payload["mario_acted"] is False


def test_all_passive_spawn_actions_do_not_set_acted():
    # All spawn actions are passive; cycling through them must not set acted
    from sm64_events.memory.addresses import PASSIVE_ACTIONS
    d = AnchorDetector()
    for action in PASSIVE_ACTIONS:
        d.process(snap(1000, igt=100), snap(1001, igt=101, action=action))
    events = d.process(snap(1001, igt=500), snap(1002, igt=0))
    assert events[0].payload["mario_acted"] is False


# ---------------------------------------------------------------------------
# Pause-streak tests (AFK rule, spec §1)
# ---------------------------------------------------------------------------

def test_pause_streak_stamped_on_practice_reset():
    d = AnchorDetector()
    # paused: global_timer advances, igt frozen at 500
    assert d.process(snap(1000, igt=500), snap(1100, igt=500)) == []
    assert d.process(snap(1100, igt=500), snap(1200, igt=500)) == []
    events = d.process(snap(1200, igt=500), snap(1202, igt=0))
    assert events[0].type == "practice_reset"
    assert events[0].payload["paused_frames_before"] == 200


def test_pause_streak_resets_when_igt_advances():
    d = AnchorDetector()
    d.process(snap(1000, igt=500), snap(1100, igt=500))   # +100 paused
    d.process(snap(1100, igt=500), snap(1101, igt=501))   # igt moved -> 0
    events = d.process(snap(1101, igt=501), snap(1103, igt=0))
    assert events[0].payload["paused_frames_before"] == 0


def test_pause_streak_stamped_on_state_loaded():
    d = AnchorDetector()
    d.process(snap(1000, igt=500), snap(1100, igt=500))   # +100 paused
    events = d.process(snap(1100, igt=500), snap(900, igt=120))  # backward, mid-range
    assert events[0].type == "state_loaded"
    assert events[0].payload["paused_frames_before"] == 100


def test_console_reset_path_resets_pause_streak():
    d = AnchorDetector()
    d.process(snap(1000, igt=500), snap(1100, igt=500))      # +100 paused
    assert d.process(snap(1100, igt=500), snap(50, igt=5)) == []  # boot range: no anchor
    assert d.process(snap(50, igt=5), snap(80, igt=5)) == []      # +30 paused
    events = d.process(snap(80, igt=5), snap(82, igt=0))
    assert events[0].payload["paused_frames_before"] == 30        # not 130


def test_equal_global_timer_does_not_grow_streak():
    d = AnchorDetector()
    d.process(snap(1000, igt=500), snap(1000, igt=500))   # same frame polled twice
    events = d.process(snap(1000, igt=500), snap(1002, igt=0))
    assert events[0].payload["paused_frames_before"] == 0


def test_streak_resets_after_anchor_fires():
    d = AnchorDetector()
    d.process(snap(1000, igt=500), snap(1200, igt=500))   # +200 paused
    d.process(snap(1200, igt=500), snap(1202, igt=0))     # anchor: stamps 200, resets
    events = d.process(snap(1202, igt=400), snap(1204, igt=0))
    assert events[0].payload["paused_frames_before"] == 0


# ---------------------------------------------------------------------------
# mario_acted event tests (spec §2)
# ---------------------------------------------------------------------------

def test_first_nonpassive_action_emits_mario_acted_event():
    d = AnchorDetector()
    events = d.process(snap(1000, igt=100), snap(1001, igt=101, action=ACT_WALKING))
    assert [e.type for e in events] == ["mario_acted"]
    assert events[0].frame == 1001
    assert events[0].payload == {}


def test_mario_acted_emitted_once_per_anchor_period():
    d = AnchorDetector()
    d.process(snap(1000, igt=100), snap(1001, igt=101, action=ACT_WALKING))
    assert d.process(snap(1001, igt=101),
                     snap(1002, igt=102, action=ACT_WALKING)) == []


def test_mario_acted_re_emitted_after_anchor():
    d = AnchorDetector()
    d.process(snap(1000, igt=100), snap(1001, igt=101, action=ACT_WALKING))
    d.process(snap(1001, igt=500), snap(1002, igt=0))     # anchor resets the period
    events = d.process(snap(1002, igt=1), snap(1003, igt=2, action=ACT_WALKING))
    assert [e.type for e in events] == ["mario_acted"]


def test_anchor_payloads_carry_acted_tracking_marker():
    events = AnchorDetector().process(snap(1000, igt=500), snap(1002, igt=0))
    assert events[0].payload["acted_tracking"] is True
    events = AnchorDetector().process(snap(5000, igt=900), snap(3000, igt=120))
    assert events[0].payload["acted_tracking"] is True


def test_death_action_does_not_emit_mario_acted_or_set_acted():
    d = AnchorDetector()
    # AFK then Mario dies to quicksand with zero input: NOT activity
    assert d.process(snap(1000, igt=100),
                     snap(1001, igt=101, action=ACT_QUICKSAND_DEATH)) == []
    events = d.process(snap(1001, igt=500), snap(1002, igt=0))
    assert events[0].payload["mario_acted"] is False


def test_action_after_swallowed_anchor_tick_action_still_emits_event():
    # a non-passive action ON the anchor tick is swallowed and must not
    # consume the once-per-period mario_acted budget
    d = AnchorDetector()
    events = d.process(snap(1000, igt=500), snap(1001, igt=0, action=ACT_WALKING))
    assert [e.type for e in events] == ["practice_reset"]
    events = d.process(snap(1001, igt=1), snap(1002, igt=2, action=ACT_WALKING))
    assert [e.type for e in events] == ["mario_acted"]


def test_streak_survives_equal_timer_tick():
    d = AnchorDetector()
    d.process(snap(1000, igt=500), snap(1100, igt=500))   # +100 paused
    d.process(snap(1100, igt=500), snap(1100, igt=500))   # equal tick: preserved
    events = d.process(snap(1100, igt=500), snap(1102, igt=0))
    assert events[0].payload["paused_frames_before"] == 100


def test_console_reset_clears_acted_flags():
    d = AnchorDetector()
    d.process(snap(1000, igt=100), snap(1001, igt=101, action=ACT_WALKING))  # acted
    assert d.process(snap(1001, igt=101), snap(50, igt=5)) == []   # console reset
    # latch cleared: a fresh action emits a fresh event
    events = d.process(snap(50, igt=5), snap(51, igt=6, action=ACT_WALKING))
    assert [e.type for e in events] == ["mario_acted"]


# ---------------------------------------------------------------------------
# action field — door-echo classifier (live gate 2026-06-12)
# ---------------------------------------------------------------------------

def test_practice_reset_payload_carries_curr_and_prev_action():
    """The emitted practice_reset payload must include action = curr.mario_action
    AND prev_action = prev.mario_action so the segment engine can correctly
    classify intra-area door echoes vs L-resets that respawn AT a door.
    The discriminator keys on prev_action: a door crossing has prev_action in
    DOOR_ACTIONS (inputs locked on the prior tick); an L-reset has a gameplay
    prev_action (e.g. ACT_WALKING = the action when L was pressed)."""
    from sm64_events.memory.addresses import ACT_WARP_DOOR_SPAWN
    events = AnchorDetector().process(
        snap(1000, igt=500, action=ACT_WALKING),
        snap(1002, igt=0, action=ACT_WARP_DOOR_SPAWN))
    assert len(events) == 1
    assert events[0].type == "practice_reset"
    assert events[0].payload["action"] == ACT_WARP_DOOR_SPAWN
    assert events[0].payload["prev_action"] == ACT_WALKING


def test_state_loaded_payload_carries_curr_and_prev_action():
    """state_loaded payload must include both action and prev_action for
    symmetry with practice_reset — consumers classify load echoes the same way."""
    from sm64_events.memory.addresses import ACT_PULLING_DOOR
    events = AnchorDetector().process(
        snap(5000, igt=900, action=ACT_WALKING),
        snap(3000, igt=120, action=ACT_PULLING_DOOR))
    assert len(events) == 1
    assert events[0].type == "state_loaded"
    assert events[0].payload["action"] == ACT_PULLING_DOOR
    assert events[0].payload["prev_action"] == ACT_WALKING


# ---------------------------------------------------------------------------
# frames_since_door — non-warp door recency (live gate 2026-06-12)
# NON-WARP doors (ACT_PULLING/PUSHING_DOOR, NOT WARP_DOOR_SPAWN) end the
# Usamune section AFTER the door animation: the IGT reset is detected 1-5
# frames later when Mario is already idle/landing.  Neither prev_action nor
# action carries door context at that point, so prev_action alone cannot
# classify these echoes.  The recency field frames_since_door bridges the gap.
# ---------------------------------------------------------------------------

def test_frames_since_door_present_after_door_action():
    """A door action at tick N followed by a practice_reset at tick N+4 must
    carry frames_since_door=4 in the payload."""
    from sm64_events.memory.addresses import ACT_PUSHING_DOOR
    d = AnchorDetector()
    # Tick N: Mario is pushing a door
    d.process(snap(1296, igt=100, action=ACT_WALKING),
              snap(1300, igt=104, action=ACT_PUSHING_DOOR))
    # Tick N+4: Usamune resets IGT; Mario is already idle/landing (no door action)
    events = d.process(snap(1300, igt=104, action=ACT_PUSHING_DOOR),
                       snap(1304, igt=0, action=ACT_WALKING))
    assert len(events) == 1
    assert events[0].type == "practice_reset"
    assert events[0].payload["frames_since_door"] == 4


def test_frames_since_door_none_when_no_door_seen():
    """If no door action has been observed, frames_since_door must be None."""
    events = AnchorDetector().process(snap(1000, igt=500), snap(1002, igt=0))
    assert events[0].payload["frames_since_door"] is None


def test_frames_since_door_cleared_on_backward_jump_self_heal():
    """domain rule 4: if global_timer jumps backward, _last_door_frame must be
    cleared so a stale recency value cannot poison anchors after the jump."""
    from sm64_events.memory.addresses import ACT_PULLING_DOOR
    d = AnchorDetector()
    # Door observed at a high frame
    d.process(snap(5000, igt=200, action=ACT_WALKING),
              snap(5010, igt=210, action=ACT_PULLING_DOOR))
    # Timer jumps backward to a low value (self-heal path in process())
    # A practice_reset arriving now must NOT carry the stale door recency
    events = d.process(snap(5010, igt=210, action=ACT_PULLING_DOOR),
                       snap(2000, igt=0))
    # This fires state_loaded (backward jump, mid-range). frames_since_door
    # must be None because _last_door_frame was cleared.
    assert len(events) == 1
    assert events[0].type == "state_loaded"
    assert events[0].payload["frames_since_door"] is None


def test_existing_payload_pins_include_frames_since_door():
    """Full payload pin for practice_reset — new key must be present and None
    when no door was recently seen (updates existing exact-dict tests)."""
    events = AnchorDetector().process(snap(1000, igt=500), snap(1002, igt=0))
    assert events[0].payload == {
        "igt_frames_before": 500, "mario_acted": False,
        "paused_frames_before": 0, "acted_tracking": True,
        "action": ACT_IDLE, "prev_action": ACT_IDLE,
        "save_pending": False, "frames_since_door": None}


# ---------------------------------------------------------------------------
# Star/key door actions — the BitS Entry regression (live journal 2026-06-12)
# The 30/70-star doors and the basement key doors run their OWN cutscene
# actions (ACT_ENTERING_STAR_DOOR 0x1331 / ACT_UNLOCKING_STAR_DOOR 0x132F /
# ACT_UNLOCKING_KEY_DOOR 0x132E), not PUSH/PULL — Usamune still ends the
# section after the animation, so the anchor lands frames later with Mario
# already idle/walking.  If the recency tracker doesn't treat these as doors,
# frames_since_door stays stale (1976 observed live, event 3594) and the
# segment engine closes + rebases the armed segment at the door.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("door_action", [
    0x0000132E,  # ACT_UNLOCKING_KEY_DOOR
    0x0000132F,  # ACT_UNLOCKING_STAR_DOOR
    0x00001331,  # ACT_ENTERING_STAR_DOOR
])
def test_frames_since_door_tracks_star_and_key_doors(door_action):
    """A star/key door action at tick N followed by a practice_reset at tick
    N+4 must carry frames_since_door=4, exactly like push/pull doors."""
    d = AnchorDetector()
    d.process(snap(1296, igt=100, action=ACT_WALKING),
              snap(1300, igt=104, action=door_action))
    events = d.process(snap(1300, igt=104, action=door_action),
                       snap(1304, igt=0, action=ACT_WALKING))
    assert len(events) == 1
    assert events[0].type == "practice_reset"
    assert events[0].payload["frames_since_door"] == 4


# ---------------------------------------------------------------------------
# Pause-warp anchor (live feedback 2026-06-12)
# Menu warp executed straight from the pause menu without ever unpausing:
# the Usamune section timer sits at 0 on BOTH sides of the warp, so no IGT
# drop edge exists and the classifier above is blind — the segment engine
# never gets its anchor (no swap re-validation, no attempt_anchor arming).
# Discriminator: (curr_level, curr_area) changed while the pause streak was
# running, with the section timer near zero on both sides.  Walked
# transitions cannot match: walking runs gameplay frames (IGT grows past
# NEAR_ZERO and the streak resets).  Emitted one STABLE tick later so the
# area detector (earlier in main.py order) journals the settled position
# first — cross-level warps update the area byte a tick after the level
# byte (live journal events 3572/3573).
# ---------------------------------------------------------------------------

def test_pause_warp_without_gameplay_emits_anchor_after_settle():
    """THE LIVE REPORT: warp upstairs, pause again immediately (IGT frozen
    at 0), warp back — no IGT edge.  The position change while paused must
    fire a practice_reset once the position is stable, stamped at the warp
    tick with the menu pause streak."""
    d = AnchorDetector()
    # paused at the lobby: global_timer advances, igt frozen at 0
    assert d.process(snap(1000, igt=0, level=6, area=1),
                     snap(1030, igt=0, level=6, area=1)) == []
    # warp tick: area flips, igt still 0 — pending, nothing emitted yet
    assert d.process(snap(1030, igt=0, level=6, area=1),
                     snap(1031, igt=0, level=6, area=2)) == []
    # next tick: position stable -> anchor emits, stamped at the warp tick
    events = d.process(snap(1031, igt=0, level=6, area=2),
                       snap(1032, igt=0, level=6, area=2))
    assert [e.type for e in events] == ["practice_reset"]
    ev = events[0]
    assert ev.frame == 1031
    assert ev.payload["paused_frames_before"] == 30
    assert ev.payload["igt_frames_before"] == 0
    assert ev.payload["mario_acted"] is False
    assert ev.payload["acted_tracking"] is True


def test_pause_warp_cross_level_waits_for_area_byte_to_settle():
    """Cross-level pause-warp: the level byte flips a tick before the area
    byte (journal 3572/3573).  The pending anchor must NOT emit until both
    have settled, and must carry the ORIGINAL pause streak."""
    d = AnchorDetector()
    d.process(snap(1000, igt=0, level=17, area=1),
              snap(1050, igt=0, level=17, area=1))          # +50 paused
    # tick 1: level byte flips, area byte stale
    assert d.process(snap(1050, igt=0, level=17, area=1),
                     snap(1051, igt=0, level=6, area=1)) == []
    # tick 2: area byte settles — still changing, still pending
    assert d.process(snap(1051, igt=0, level=6, area=1),
                     snap(1052, igt=0, level=6, area=2)) == []
    # tick 3: stable — emit with the streak captured at the warp tick
    events = d.process(snap(1052, igt=0, level=6, area=2),
                       snap(1053, igt=0, level=6, area=2))
    assert [e.type for e in events] == ["practice_reset"]
    assert events[0].payload["paused_frames_before"] == 50


def test_position_change_without_pause_is_not_a_pause_warp():
    """Walked-style transition with no pause streak must not fire the
    pause-warp branch (the normal IGT-drop classifier owns walked loads)."""
    d = AnchorDetector()
    # igt running -> streak stays 0
    d.process(snap(1000, igt=10, level=6, area=1),
              snap(1001, igt=11, level=6, area=1))
    assert d.process(snap(1001, igt=11, level=6, area=1),
                     snap(1002, igt=12, level=6, area=2)) == []
    assert d.process(snap(1002, igt=12, level=6, area=2),
                     snap(1003, igt=13, level=6, area=2)) == []


def test_position_change_after_timestop_with_large_igt_is_not_a_pause_warp():
    """A door-cutscene time-stop grows the streak, but the player walked
    there: IGT is far past NEAR_ZERO on both sides -> no pause-warp."""
    d = AnchorDetector()
    d.process(snap(1000, igt=500, level=6, area=1),
              snap(1030, igt=500, level=6, area=1))         # +30 streak
    assert d.process(snap(1030, igt=500, level=6, area=1),
                     snap(1031, igt=500, level=6, area=3)) == []
    assert d.process(snap(1031, igt=500, level=6, area=3),
                     snap(1032, igt=500, level=6, area=3)) == []


def test_pause_warp_pending_cleared_on_backward_jump():
    """A rewind (console reset / savestate) between the warp tick and the
    settle tick invalidates the pending anchor — it must never emit."""
    d = AnchorDetector()
    d.process(snap(1000, igt=0, level=6, area=1),
              snap(1030, igt=0, level=6, area=1))           # +30 paused
    assert d.process(snap(1030, igt=0, level=6, area=1),
                     snap(1031, igt=0, level=6, area=2)) == []   # pending
    # console reset into boot range: pending must die with the rewind
    assert d.process(snap(1031, igt=0, level=6, area=2),
                     snap(50, igt=0, level=6, area=2)) == []
    assert d.process(snap(50, igt=0, level=6, area=2),
                     snap(52, igt=0, level=6, area=2)) == []


def test_pause_warp_superseded_by_real_igt_drop():
    """If a classified anchor fires while a pause-warp is pending, the
    pending anchor is dropped (never double-anchor one load)."""
    d = AnchorDetector()
    d.process(snap(1000, igt=0, level=6, area=1),
              snap(1030, igt=0, level=6, area=1))           # +30 paused
    assert d.process(snap(1030, igt=0, level=6, area=1),
                     snap(1031, igt=0, level=6, area=2)) == []   # pending
    # mid-range backward jump: state_loaded classifies -> supersedes pending
    events = d.process(snap(1031, igt=0, level=6, area=2),
                       snap(900, igt=120, level=6, area=2))
    assert [e.type for e in events] == ["state_loaded"]
    assert d.process(snap(900, igt=120, level=6, area=2),
                     snap(902, igt=120, level=6, area=2)) == []


# ---------------------------------------------------------------------------
# save_pending — post-star "SAVE & CONTINUE?" screen (live report 2026-06-12)
# Mario holds ACT_EXIT_LAND_SAVE_DIALOG (0x1327) for the whole course-complete
# save menu (live watch); confirming an option reloads the area and resets
# Usamune's IGT a few frames later, after Mario is already back to idle.  That
# reset is involuntary — the segment engine must ignore it (shape 4).  A latch
# set while the action is seen tags the following anchor with save_pending.
# ---------------------------------------------------------------------------

def test_save_dialog_action_latches_save_pending_on_following_reset():
    """Faithful replay of the live trace: 0x1327 held during the menu, reverts
    to idle on confirm, then the reload resets the IGT — the practice_reset
    must carry save_pending=True."""
    from sm64_events.memory.addresses import ACT_EXIT_LAND_SAVE_DIALOG
    d = AnchorDetector()
    # menu up: Mario in the save-dialog action while the IGT still runs
    d.process(snap(1000, igt=100, action=ACT_IDLE),
              snap(1001, igt=101, action=ACT_EXIT_LAND_SAVE_DIALOG))
    # confirm: action reverts to idle (live: 0x1327 -> ACT_IDLE)
    d.process(snap(1001, igt=101, action=ACT_EXIT_LAND_SAVE_DIALOG),
              snap(1002, igt=102, action=ACT_IDLE))
    # reload a few frames later resets the IGT, Mario still idle
    events = d.process(snap(1002, igt=102, action=ACT_IDLE),
                       snap(1004, igt=0, action=ACT_IDLE))
    assert len(events) == 1 and events[0].type == "practice_reset"
    assert events[0].payload["save_pending"] is True


def test_save_pending_latch_consumed_by_its_reset():
    """One-shot: the reload reset consumes the latch, so a LATER genuine
    L-reset is not wrongly suppressed."""
    from sm64_events.memory.addresses import ACT_EXIT_LAND_SAVE_DIALOG
    d = AnchorDetector()
    d.process(snap(1000, igt=100, action=ACT_IDLE),
              snap(1001, igt=101, action=ACT_EXIT_LAND_SAVE_DIALOG))
    # save reload reset — fires save_pending=True and clears the latch
    save = d.process(snap(1001, igt=101, action=ACT_EXIT_LAND_SAVE_DIALOG),
                     snap(1003, igt=0, action=ACT_IDLE))
    assert save[0].payload["save_pending"] is True
    # later real L-reset (idle Mario, never re-saw the menu): save_pending False
    events = d.process(snap(1003, igt=200, action=ACT_IDLE),
                       snap(1005, igt=0, action=ACT_IDLE))
    assert len(events) == 1 and events[0].type == "practice_reset"
    assert events[0].payload["save_pending"] is False


def test_ordinary_reset_has_save_pending_false():
    """No save menu seen → save_pending False (the segment engine still
    records the reset row)."""
    events = AnchorDetector().process(snap(1000, igt=500), snap(1002, igt=0))
    assert events[0].payload["save_pending"] is False


def test_save_dialog_sets_save_pending_on_state_loaded():
    """Symmetry with practice_reset: a state_loaded during the save menu also
    carries the latch."""
    from sm64_events.memory.addresses import ACT_EXIT_LAND_SAVE_DIALOG
    d = AnchorDetector()
    d.process(snap(1000, igt=100, action=ACT_IDLE),
              snap(1001, igt=101, action=ACT_EXIT_LAND_SAVE_DIALOG))
    events = d.process(snap(1001, igt=101, action=ACT_EXIT_LAND_SAVE_DIALOG),
                       snap(800, igt=120, action=ACT_IDLE))  # backward jump
    assert events[0].type == "state_loaded"
    assert events[0].payload["save_pending"] is True
