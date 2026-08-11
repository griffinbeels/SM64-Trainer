# tests/test_moment.py
from datetime import datetime, timezone

import pytest

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.moment import MOMENTS, MomentDetector
from sm64_events.memory.addresses import (ACT_IN_CANNON, ACT_PULLING_DOOR,
                                          ACT_PUSHING_DOOR,
                                          ACT_READING_AUTOMATIC_DIALOG,
                                          ACT_READING_NPC_DIALOG,
                                          ACT_SPAWN_SPIN_AIRBORNE,
                                          ACT_WAITING_FOR_DIALOG,
                                          BOX_OPENS_AT_STATE)

ACT_IDLE = 0x0C400201
ACT_WALKING = 0x04000440


def snap(action, timer, level=24, **overrides) -> GameSnapshot:
    defaults = dict(
        wall_time_utc=datetime(2026, 8, 5, tzinfo=timezone.utc),
        global_timer=timer, mario_action=action, mario_action_timer=0,
        num_stars=5, last_completed_course=1, last_completed_star=3,
        igt_overall=300, curr_level=level, curr_area=1)
    defaults.update(overrides)
    return GameSnapshot(**defaults)


def run(snaps, detector=None):
    """Feed consecutive (prev, curr) pairs like the poller does — plus one
    trailing settle poll, because a moment is a one-poll HELD emit since
    round 9 item 4 (the landmark settles from the poll after the edge) and
    the poller never stops polling the way a finite list does."""
    det = detector or MomentDetector()
    events = []
    for prev, curr in zip(snaps, snaps[1:]):
        events.extend(det.process(prev, curr))
    last = snaps[-1]
    settle = snap(last.mario_action, last.global_timer + 1,
                  level=last.curr_level, curr_area=last.curr_area,
                  igt_overall=last.igt_overall,
                  landmark_behaviour=last.landmark_behaviour,
                  landmark_home=last.landmark_home)
    events.extend(det.process(last, settle))
    return events


# -- the entry edge -----------------------------------------------------------
# An action byte reads the same for every frame of a door animation, so a
# moment is the frame Mario ENTERED it. Same discipline as the star grab's
# action edge, and the reason re-collection works there.

def test_a_door_pull_emits_one_moment_on_the_entry_edge_only():
    events = run([snap(ACT_WALKING, 100), snap(ACT_PULLING_DOOR, 101),
                  snap(ACT_PULLING_DOOR, 102), snap(ACT_PULLING_DOOR, 103)])
    assert len(events) == 1
    ev = events[0]
    assert ev.type == "moment_reached" and ev.frame == 101
    assert ev.payload["kind"] == "door_open"
    assert ev.payload["ordinal"] == 1


def test_the_payload_carries_where_it_happened():
    events = run([snap(ACT_WALKING, 100),
                  snap(ACT_PULLING_DOOR, 101, level=6, curr_area=3)])
    assert events[0].payload["level"] == 6
    assert events[0].payload["area"] == 3
    assert events[0].payload["action"] == ACT_PULLING_DOOR


# -- ordinals -----------------------------------------------------------------
# The ordinal exists for START triggers: waypoints already order everything
# after the arm, but "the 5th door in Big Boo's Haunt" is a start, and a start
# has no arm to count from.

def test_the_ordinal_counts_each_occurrence():
    events = run([snap(ACT_WALKING, 100), snap(ACT_PULLING_DOOR, 101),
                  snap(ACT_WALKING, 102), snap(ACT_PULLING_DOOR, 103),
                  snap(ACT_WALKING, 104), snap(ACT_PULLING_DOOR, 105)])
    assert [e.payload["ordinal"] for e in events] == [1, 2, 3]


def test_two_actions_of_the_SAME_kind_share_one_counter():
    """Pulling and pushing are both `door_open` -- the fifth door in Big
    Boo's Haunt is the fifth DOOR, not the fifth door-you-happened-to-pull."""
    events = run([snap(ACT_WALKING, 100), snap(ACT_PULLING_DOOR, 101),
                  snap(ACT_WALKING, 102), snap(ACT_PUSHING_DOOR, 103)])
    assert [e.payload["ordinal"] for e in events] == [1, 2]
    assert {e.payload["kind"] for e in events} == {"door_open"}


def test_reset_restarts_the_ordinals():
    det = MomentDetector()
    run([snap(ACT_WALKING, 100), snap(ACT_PULLING_DOOR, 101)], det)
    det.reset()
    again = run([snap(ACT_WALKING, 200), snap(ACT_PULLING_DOOR, 201)], det)
    assert again[0].payload["ordinal"] == 1


# -- the one-poll landmark settle (round 9 item 4) ----------------------------
# His first WF tree grab (journal id 2794, 2 s after spawning) resolved to
# bhvSpinAirborneWarp — Mario's own SPAWN MARKER, the previous thing the
# engaged-object pointer held — while every later grab read the tree. The
# pointer lags the action byte by a read, so the landmark is settled from the
# poll AFTER the edge; everything else in the payload stays the edge's.

SPAWN_MARKER, TREE = 0x800EE0F4, 0x800EDC24
ACT_GRAB_POLE = next(iter(next(m for m in MOMENTS
                               if m.kind == "pole_grab").actions))


def test_the_landmark_settles_from_the_poll_after_the_edge():
    events = run([
        snap(ACT_WALKING, 100,
             landmark_behaviour=SPAWN_MARKER, landmark_home=(0.0, 0.0, 0.0)),
        snap(ACT_GRAB_POLE, 101,     # the edge still reads the STALE pointer
             landmark_behaviour=SPAWN_MARKER, landmark_home=(0.0, 0.0, 0.0)),
        snap(ACT_GRAB_POLE, 102,     # one poll later the game has settled it
             landmark_behaviour=TREE, landmark_home=(0.0, 0.0, 0.0)),
    ])
    assert len(events) == 1
    assert events[0].frame == 101, "the frame stays the edge's"
    assert events[0].payload["landmark"]["behaviour"] == TREE


def test_a_backward_jump_on_the_settle_poll_still_publishes_the_moment():
    """A reset right after the edge must not eat the moment — it happened.
    The edge's own landmark stands, since a post-reset snapshot describes a
    different world."""
    det = MomentDetector()
    det.process(snap(ACT_WALKING, 100),
                snap(ACT_GRAB_POLE, 101, landmark_behaviour=TREE))
    released = det.process(snap(ACT_GRAB_POLE, 101, landmark_behaviour=TREE),
                           snap(ACT_IDLE, 5))
    assert [e.payload["kind"] for e in released] == ["pole_grab"]
    assert released[0].payload["landmark"]["behaviour"] == TREE


def test_the_settle_never_crosses_a_place_change():
    """If the settle poll is somewhere else, `curr`'s engaged object belongs
    to that other place — the edge's own reading stands."""
    events = run([
        snap(ACT_WALKING, 100),
        snap(ACT_PULLING_DOOR, 101, landmark_behaviour=SPAWN_MARKER),
        snap(ACT_PULLING_DOOR, 102, level=6, curr_area=3,
             landmark_behaviour=TREE),
    ])
    assert events[0].payload["landmark"]["behaviour"] == SPAWN_MARKER


# -- a lingering pointer names nothing (2026-08-10) ---------------------------
# *"when I FIRST TRIGGER the 70 Star door in Castle Inside, it actually gets
# incorrectly marked as 'Trigger the WOODEN door in castle inside'... If I
# rename it, it also renames the ACTUAL wooden door, which is wrong."*
# A star door's dialog engages nothing, so the pointer still held the wooden
# door he had walked through 453 frames earlier (journal ids 28313/28316).

WOODEN_DOOR, STAR_DOOR = 0x800EBC8C, 0x800EB180
# These landmark tests only need SOME textbox action, at its box-OPEN state —
# they are about the engaged-object pointer, not about the open-state gate
# itself (that gate has its own section below). ACT_READING_NPC_DIALOG opens
# at state 8, addresses.BOX_OPENS_AT_STATE.
ACT_TEXTBOX_DIALOG = ACT_READING_NPC_DIALOG
TEXTBOX_OPEN_STATE = BOX_OPENS_AT_STATE[ACT_TEXTBOX_DIALOG]


def test_a_moment_that_engaged_nothing_new_names_nothing():
    events = run([
        snap(ACT_WALKING, 100, level=6,
             landmark_behaviour=WOODEN_DOOR, landmark_home=(-997.0, 1203.0, 1178.0)),
        # 453 frames of walking later the pointer STILL holds that door, and
        # the star door's textbox is not about it.
        snap(ACT_WALKING, 553, level=6,
             landmark_behaviour=WOODEN_DOOR, landmark_home=(-997.0, 1203.0, 1178.0)),
        snap(ACT_TEXTBOX_DIALOG, 554, level=6, mario_action_state=TEXTBOX_OPEN_STATE,
             landmark_behaviour=WOODEN_DOOR, landmark_home=(-997.0, 1203.0, 1178.0)),
    ])
    assert [e.payload["kind"] for e in events] == ["textbox"]
    assert events[0].payload["landmark"] is None, (
        "a lingering pointer must not lend its name to the next moment")


def test_opening_the_SAME_door_again_keeps_its_name():
    """The regression the blanket gate caused, 2026-08-10: he practices BLJs by
    reloading in front of the Upstairs Door and opening it over and over, so
    the pointer already holds that door and nothing retargets. *"the door that
    opens at 00\"06 is ALWAYS the Upstairs Door... these two are the same
    thing!"* — 21 of 51 door rows had degraded to "Open a door in Castle
    Inside". Pulling a door IS operating it, so the pointer is authoritative
    whether or not its value moved."""
    events = run([
        snap(ACT_WALKING, 100, level=6),
        # He opened it once; the pointer took it here and never moves again.
        snap(ACT_WALKING, 101, level=6,
             landmark_behaviour=WOODEN_DOOR, landmark_home=(-997.0, 1203.0, 1178.0)),
        snap(ACT_WALKING, 600, level=6,
             landmark_behaviour=WOODEN_DOOR, landmark_home=(-997.0, 1203.0, 1178.0)),
        snap(ACT_PULLING_DOOR, 601, level=6,
             landmark_behaviour=WOODEN_DOOR, landmark_home=(-997.0, 1203.0, 1178.0)),
    ])
    assert [e.payload["kind"] for e in events] == ["door_open"]
    assert events[0].payload["landmark"]["behaviour"] == WOODEN_DOOR
    assert events[0].payload["engaged_age_frames"] == 601 - 101, (
        "the evidence field still records how old the engagement was")


def test_a_moment_that_engaged_something_of_its_own_still_names_it():
    """Bowser's pre-fight dialogue retargets the pointer, and 7 of his 27
    real textbox rows are that shape — the gate must not cost them."""
    events = run([
        snap(ACT_WALKING, 100, level=6,
             landmark_behaviour=WOODEN_DOOR, landmark_home=(-997.0, 1203.0, 1178.0)),
        snap(ACT_TEXTBOX_DIALOG, 101, level=6, mario_action_state=TEXTBOX_OPEN_STATE,
             landmark_behaviour=STAR_DOOR, landmark_home=(-127.0, 3174.0, 3772.0)),
    ])
    assert events[0].payload["landmark"]["behaviour"] == STAR_DOOR


def test_a_stale_edge_reading_is_refused_even_when_the_settle_cannot_help():
    """The settle poll is somewhere else, so only the EDGE's own reading is
    available — and it is a leftover, so the row names nothing."""
    events = run([
        snap(ACT_WALKING, 100, level=6,
             landmark_behaviour=WOODEN_DOOR, landmark_home=(-997.0, 1203.0, 1178.0)),
        snap(ACT_WALKING, 553, level=6,
             landmark_behaviour=WOODEN_DOOR, landmark_home=(-997.0, 1203.0, 1178.0)),
        snap(ACT_TEXTBOX_DIALOG, 554, level=6, mario_action_state=TEXTBOX_OPEN_STATE,
             landmark_behaviour=WOODEN_DOOR, landmark_home=(-997.0, 1203.0, 1178.0)),
        snap(ACT_TEXTBOX_DIALOG, 555, level=24, curr_area=1,
             mario_action_state=TEXTBOX_OPEN_STATE, landmark_behaviour=TREE),
    ])
    assert events[0].payload["landmark"] is None


def test_automatic_dialogue_publishes_the_RAW_counter_where_a_door_publishes_it_plus_two():
    """His frame-stepped replay, 2026-08-10: Usamune 0'11"53 against our
    0'11"60 on journal id 28790 (the star door, automatic dialogue), raw
    counter 346. A door's own screenshot (2026-08-06) says +2 and is
    untouched, so this is per-ACTION rather than a fourth flip of one
    constant -- and it stays automatic dialogue's OWN number even after NPC
    dialogue got its own gated lag in round 3 (see
    test_a_textbox_moment_lags_one_frame_beyond_a_door below)."""
    textbox = run([snap(ACT_WALKING, 100, igt_overall=346),
                   snap(ACT_READING_AUTOMATIC_DIALOG, 101, igt_overall=346)])
    assert textbox[0].payload["igt_frames"] == 346
    assert textbox[0].payload["igt"] == "0'11\"53"

    door = run([snap(ACT_WALKING, 100, igt_overall=2003),
                snap(ACT_PULLING_DOOR, 101, igt_overall=2003)])
    assert door[0].payload["igt_frames"] == 2005


# -- the guard that would have caught the blanket gate ------------------------
# A rule measured on ONE kind and applied to EVERY kind is what shipped on
# 2026-08-10 and cost 21 of 51 door rows their name. The hand-written door case
# above is the specific bug; these two are the class. Both mutation-proved:
# flipping `Moment.needs_fresh_engagement`'s default to True reddens FOUR kinds
# here at once, where a single hand-written test would have caught one.

def _repeatable_edge(moment):
    """One action for `moment` that fires on a SINGLE entry snap, for tests
    that don't care about the open-state gate. `textbox` is the one kind
    with a gated action (NPC dialogue) sharing its `actions` with an
    ungated one (automatic dialogue, module docstring) -- prefer the
    ungated one so the shape below stays a plain two-touch edge for every
    kind, including ones added after this was written. Falls back to a
    gated action's own key for a kind with no ungated one at all."""
    if not moment.open_states:
        return next(iter(moment.actions))
    ungated = [action for action in moment.actions
               if action not in moment.open_states]
    if ungated:
        return ungated[0]
    return next(action for action, threshold in moment.open_states.items()
                if threshold is not None)


@pytest.mark.parametrize(
    "moment", [m for m in MOMENTS if m.actions], ids=lambda m: m.kind)
def test_every_kind_still_names_an_object_it_had_already_engaged(moment):
    """Doing the same thing to the same object twice must keep naming it —
    for every kind in the registry, including ones added after this was
    written. The pointer does not retarget on a repeat, so any rule that
    demands a retarget silently costs a kind its identity."""
    edge = _repeatable_edge(moment)
    events = run([
        snap(ACT_WALKING, 100, level=6),
        snap(ACT_WALKING, 101, level=6, landmark_behaviour=WOODEN_DOOR,
             landmark_home=(-997.0, 1203.0, 1178.0)),
        snap(ACT_WALKING, 600, level=6, landmark_behaviour=WOODEN_DOOR,
             landmark_home=(-997.0, 1203.0, 1178.0)),
        snap(edge, 601, level=6, landmark_behaviour=WOODEN_DOOR,
             landmark_home=(-997.0, 1203.0, 1178.0)),
    ])
    assert [e.payload["kind"] for e in events] == [moment.kind]
    landmark = events[0].payload["landmark"]
    if moment.needs_fresh_engagement:
        assert landmark is None, (
            f"{moment.kind} demands a fresh engagement, so a repeat must name "
            f"nothing — see test_only_the_textbox_may_demand_a_fresh_engagement")
    else:
        assert landmark is not None and landmark["behaviour"] == WOODEN_DOOR, (
            f"{moment.kind} lost its identity on a REPEAT of the same "
            f"interaction — the regression of 2026-08-10")


def test_only_the_textbox_may_demand_a_fresh_engagement():
    """An allowlist with its reason, the shape this repo already uses for
    exemptions. A kind belongs here only when its action is NOT itself
    operating an object — reading text is the only one, which is why a
    textbox can be left holding a door from fifteen seconds ago while a door
    pull, a pole grab, a pickup and a cannon entry cannot."""
    demanding = {m.kind for m in MOMENTS if m.needs_fresh_engagement}
    assert demanding == {"textbox"}


def test_the_engagement_age_rides_along_so_the_window_can_be_re_measured():
    events = run([
        snap(ACT_WALKING, 100, level=6),
        snap(ACT_WALKING, 553, level=6,
             landmark_behaviour=WOODEN_DOOR, landmark_home=(-997.0, 1203.0, 1178.0)),
        snap(ACT_WALKING, 900, level=6,
             landmark_behaviour=WOODEN_DOOR, landmark_home=(-997.0, 1203.0, 1178.0)),
        snap(ACT_TEXTBOX_DIALOG, 1006, level=6, mario_action_state=TEXTBOX_OPEN_STATE,
             landmark_behaviour=WOODEN_DOOR, landmark_home=(-997.0, 1203.0, 1178.0)),
    ])
    assert events[0].payload["engaged_age_frames"] == 1006 - 553


# -- the box actually opens, not the turn (round 2, 2026-08-10) ---------------
# His report: two King Whomp screenshots, both several frames ahead of
# Usamune's own screen. Mario TURNS to face the NPC for 6-8 frames before the
# box exists (decomp act_reading_npc_dialog: MARIO_ACTION_STATE 0..7 is the
# turn, 8 is the box), so the old entry-edge trigger fired on the turn's first
# frame. `Moment.open_states` gates on the real state instead of subtracting a
# constant — his own run measured a 6-8 frame spread, never one fixed number.

def test_the_npc_textbox_moment_fires_when_the_box_opens_not_when_the_turn_begins():
    events = run([
        snap(ACT_WALKING, 100),
        *[snap(ACT_READING_NPC_DIALOG, 101 + state, mario_action_state=state)
          for state in range(9)],   # 0..8 inclusive — the box opens at 8
    ])
    assert [e.payload["kind"] for e in events] == ["textbox"]
    assert events[0].frame == 109, (
        "the box-open frame (state 8), not the turn's first frame (101)")


def test_automatic_dialog_still_fires_on_the_plain_entry_edge():
    """Round 4 (2026-08-11) reverted the gate this test used to check: a
    threshold of 9 for automatic dialogue was reasoned from decomp alone and
    never checked against a screenshot, while automatic dialogue already
    carried its OWN, independently live-measured number at the plain entry
    edge (module docstring, "AUTOMATIC DIALOGUE KEEPS ITS OWN, OLDER,
    INDEPENDENT MEASUREMENT"). Gating it would have fired the moment on a
    different frame than the one that measurement was taken at."""
    events = run([
        snap(ACT_WALKING, 100),
        snap(ACT_READING_AUTOMATIC_DIALOG, 101, mario_action_state=0),
    ])
    assert [e.payload["kind"] for e in events] == ["textbox"]
    assert events[0].frame == 101, "the entry edge, not a box-open state"


def test_automatic_dialog_has_no_gate_in_the_registry():
    """The regression guard for the above: an unmeasured threshold must not
    quietly come back for this action."""
    assert ACT_READING_AUTOMATIC_DIALOG not in BOX_OPENS_AT_STATE


def test_a_variable_length_turn_still_lands_on_the_real_box_open_frame():
    """The gap is NOT a constant — his ten King Whomp captures ranged 6-8
    frames. The detector must watch the real state every poll, never subtract
    a fixed offset, or a run with a different turn length records wrong."""
    fast_turn = run([
        snap(ACT_WALKING, 200),
        snap(ACT_READING_NPC_DIALOG, 201, mario_action_state=0),
        snap(ACT_READING_NPC_DIALOG, 202, mario_action_state=8),
    ])
    slow_turn = run([
        snap(ACT_WALKING, 300),
        snap(ACT_READING_NPC_DIALOG, 301, mario_action_state=0),
        snap(ACT_READING_NPC_DIALOG, 302, mario_action_state=3),
        snap(ACT_READING_NPC_DIALOG, 303, mario_action_state=6),
        snap(ACT_READING_NPC_DIALOG, 304, mario_action_state=8),
    ])
    assert fast_turn[0].frame == 202
    assert slow_turn[0].frame == 304


def test_waiting_for_dialog_alone_never_opens_the_box():
    """ACT_WAITING_FOR_DIALOG carries no entry in BOX_OPENS_AT_STATE — the
    decomp gives it no state machine of its own, so it can sit in
    DIALOG_ACTIONS without ever firing the moment by itself."""
    events = run([
        snap(ACT_WALKING, 100),
        snap(ACT_WAITING_FOR_DIALOG, 101),
        snap(ACT_WAITING_FOR_DIALOG, 102),
        snap(ACT_WAITING_FOR_DIALOG, 103),
    ])
    assert events == []


def test_waiting_then_reading_still_gates_on_the_box_state():
    """His one live WAITING sighting (a castle NPC) moved into a reading
    action within a frame — the WAITING prefix must not shortcut the gate."""
    events = run([
        snap(ACT_WALKING, 100),
        snap(ACT_WAITING_FOR_DIALOG, 101),
        snap(ACT_READING_NPC_DIALOG, 102, mario_action_state=0),
        snap(ACT_READING_NPC_DIALOG, 103, mario_action_state=8),
    ])
    assert [e.payload["kind"] for e in events] == ["textbox"]
    assert events[0].frame == 103


def test_a_dialog_that_never_reaches_the_open_state_fires_no_moment():
    """He leaves the dialog action before the box ever opens — no box ever
    existed, so no moment should be recorded."""
    events = run([
        snap(ACT_WALKING, 100),
        snap(ACT_READING_NPC_DIALOG, 101, mario_action_state=0),
        snap(ACT_READING_NPC_DIALOG, 102, mario_action_state=3),
        snap(ACT_WALKING, 103),
    ])
    assert events == []


def test_the_moment_carries_usamunes_counter_from_the_box_open_frame():
    """The number the recorder shows must be Usamune's counter AT THE BOX,
    not at the turn's first frame — the actual live bug."""
    events = run([
        snap(ACT_WALKING, 100, igt_overall=2000),
        snap(ACT_READING_NPC_DIALOG, 101, mario_action_state=0, igt_overall=2001),
        snap(ACT_READING_NPC_DIALOG, 102, mario_action_state=4, igt_overall=2002),
        snap(ACT_READING_NPC_DIALOG, 103, mario_action_state=8, igt_overall=2003),
    ])
    assert events[0].payload["counter"] == 2003


# -- what this module deliberately does NOT emit ------------------------------

def test_regaining_control_is_NOT_a_moment_because_spawned_already_is_one():
    """`spawned` already fires on the edge OUT of the spawn actions (kind
    "spawn") and out of ACT_INTRO_CUTSCENE (kind "intro" -- addresses.py
    calls that "the canonical Lakitu-skip timing start", live-verified
    2026-06-12). A `first_controllable` moment would be a SECOND door onto
    the same frame, which is the divergent-duplication class this project
    holds a test suite against. Idle/sleeping are the only frames it would
    add, and stopping being idle is not a practice boundary."""
    idle_to_walk = run([snap(ACT_IDLE, 100), snap(ACT_WALKING, 101)])
    spawn_to_walk = run([snap(ACT_SPAWN_SPIN_AIRBORNE, 200),
                         snap(ACT_WALKING, 201)])
    assert idle_to_walk == []
    assert spawn_to_walk == []


# -- self-healing and gating --------------------------------------------------

def test_a_backward_global_timer_resets_the_ordinals():
    """Savestate load / console reset. The detector contract requires every
    detector to heal itself when the game clock jumps backward."""
    det = MomentDetector()
    run([snap(ACT_WALKING, 100), snap(ACT_PULLING_DOOR, 101)], det)
    det.process(snap(ACT_PULLING_DOOR, 101), snap(ACT_IDLE, 5))
    after = run([snap(ACT_WALKING, 6), snap(ACT_PULLING_DOOR, 7)], det)
    assert after[0].payload["ordinal"] == 1


def test_no_target_means_no_events_at_all():
    """The SEAM still works, and nothing in the shipped app injects it.

    This was task 0087's live rule ("these should ONLY be tracked when we
    explicitly select / autoselect a star or segment") until 2026-08-06, when
    it turned out to blind the recorder in the one situation the recorder is
    for. `tests/test_composition.py` is where that reversal is written down and
    guarded; this pair only proves the predicate is honoured if anyone ever
    passes one again."""
    det = MomentDetector(target_active=lambda: False)
    assert run([snap(ACT_WALKING, 100), snap(ACT_PULLING_DOOR, 101),
                snap(ACT_IDLE, 102), snap(ACT_READING_NPC_DIALOG, 103)],
               det) == []


def test_the_target_gate_is_read_every_tick_not_once_at_construction():
    """A target set mid-session must start recording without a restart."""
    live = {"on": False}
    det = MomentDetector(target_active=lambda: live["on"])
    assert run([snap(ACT_WALKING, 100), snap(ACT_PULLING_DOOR, 101)], det) == []
    live["on"] = True
    later = run([snap(ACT_WALKING, 102), snap(ACT_PULLING_DOOR, 103)], det)
    assert [e.payload["kind"] for e in later] == ["door_open"]


# -- the number on his screen -------------------------------------------------
# THE EVIDENCE IS A SCREENSHOT PAIR, and this is the third round on one
# constant, so it is quoted here rather than summarised. His screenshot of
# 2026-08-06 holds BOTH numbers in one frame: the emulator reads 1'06"83, the
# recorder's top row reads 1'06"80. Journal id 2279 is that door -- raw
# `counter` 2003, so Usamune showed frame 2005 and we published 2004.
# Usamune = counter + 2, and `tools/score_moment_clock.py` is what scored it
# (and what scores the next disagreement, from ONE screenshot, without
# anybody recalling a comparison).

def test_a_moment_carries_the_number_usamune_shows_not_the_raw_counter():
    events = run([snap(ACT_WALKING, 100, igt_overall=2003),
                  snap(ACT_PULLING_DOOR, 101, igt_overall=2003)])
    assert events[0].payload["counter"] == 2003
    assert events[0].payload["igt_frames"] == 2005
    assert events[0].payload["igt"] == "1'06\"83"


# -- an NPC dialogue lags one frame beyond a door (round 3, 2026-08-11) -------
# His re-run of round 2's own fix: *"it clearly says 11"30 here, but we show
# 11"26."* Scored with `tools/score_moment_clock.py --usamune "0'11\"30"`
# against the live journal: row 355, raw counter 336, Usamune reads
# counter + 3 -- one frame beyond a door's own counter + 2. One screenshot;
# see the module docstring for the caveat. (Automatic dialogue keeps its OWN,
# older number -- see test_automatic_dialogue_publishes_the_RAW_counter above.)

def test_a_textbox_moment_lags_one_frame_beyond_a_door():
    events = run([
        snap(ACT_WALKING, 100, igt_overall=336),
        *[snap(ACT_READING_NPC_DIALOG, 101 + state, mario_action_state=state,
               igt_overall=336)
          for state in range(9)],   # box opens at state 8, frame 109
    ])
    assert events[0].payload["counter"] == 336
    assert events[0].payload["igt_frames"] == 336 + 3, (
        "counter + 1 (Usamune's DISPLAY_TICK) + 2 (NPC dialogue's own gated "
        "display lag, Moment.gated_display_lag)")
    assert events[0].payload["igt"] == "0'11\"30"


# -- cannon entry (round 9 item 6) --------------------------------------------
# "We also need to detect when the user enters a cannon" / "(cannon entry
# xcam)". ACT_IN_CANNON is the frame the game commits Mario to the in-cannon
# view, which IS that camera cut; firing is a different action and is
# deliberately not a moment.

ACT_SHOT_FROM_CANNON = 0x00880898   # decomp include/sm64.h; NOT a moment


def test_climbing_into_a_cannon_is_a_moment():
    events = run([snap(ACT_WALKING, 100), snap(ACT_IN_CANNON, 101)])
    assert [e.payload["kind"] for e in events] == ["cannon_enter"]
    assert events[0].frame == 101


def test_firing_the_cannon_is_not_a_second_moment():
    """One kind, one boundary: entering the cannon is the practiced moment and
    the launch would double-count it."""
    events = run([snap(ACT_WALKING, 100), snap(ACT_IN_CANNON, 101),
                  snap(ACT_SHOT_FROM_CANNON, 102)])
    assert [e.payload["kind"] for e in events] == ["cannon_enter"]


# -- the registry -------------------------------------------------------------

def test_every_moment_kind_is_unique():
    kinds = [m.kind for m in MOMENTS]
    assert len(kinds) == len(set(kinds))


def test_every_moment_carries_a_label_for_the_picker():
    assert all(m.label and m.label.strip() for m in MOMENTS)


def test_no_two_moments_claim_the_same_action():
    """Overlapping action sets would emit two moments for one frame and give
    a subsection two names for the same boundary."""
    seen = set()
    for moment in MOMENTS:
        assert not (seen & moment.actions), f"{moment.kind} overlaps"
        seen |= moment.actions
