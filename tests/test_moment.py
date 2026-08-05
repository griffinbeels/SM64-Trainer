# tests/test_moment.py
from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.moment import MOMENTS, MomentDetector
from sm64_events.memory.addresses import (ACT_PULLING_DOOR, ACT_PUSHING_DOOR,
                                          ACT_READING_NPC_DIALOG,
                                          ACT_SPAWN_SPIN_AIRBORNE)

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
    """Feed consecutive (prev, curr) pairs like the poller does."""
    det = detector or MomentDetector()
    events = []
    for prev, curr in zip(snaps, snaps[1:]):
        events.extend(det.process(prev, curr))
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
    """Task 0087: "these should ONLY be tracked when we explicitly select /
    autoselect a star or segment". Also what keeps a per-wall-kick vocabulary
    from multiplying journal volume."""
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
