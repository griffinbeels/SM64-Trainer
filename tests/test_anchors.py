# tests/test_anchors.py
from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.anchors import BOOT_TIMER_MAX, AnchorDetector

# ACT_IDLE is a PASSIVE_ACTIONS member — using it as the snap default means
# a snap-pair that never leaves idle produces mario_acted=False in payloads.
ACT_IDLE = 0x0C400201
ACT_WALKING = 0x04000440  # not in PASSIVE_ACTIONS -> counts as "acted"


def snap(timer: int, igt: int = 0, action: int = ACT_IDLE) -> GameSnapshot:
    return GameSnapshot(
        wall_time_utc=datetime(2026, 6, 10, tzinfo=timezone.utc),
        global_timer=timer, mario_action=action, mario_action_timer=0,
        num_stars=5, last_completed_course=1, last_completed_star=3,
        igt_overall=igt)


def test_igt_drop_to_zero_emits_practice_reset():
    events = AnchorDetector().process(snap(1000, igt=500), snap(1002, igt=0))
    assert len(events) == 1
    ev = events[0]
    assert ev.type == "practice_reset" and ev.frame == 1002
    assert ev.payload == {"igt_frames_before": 500, "mario_acted": False}


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
    assert ev.payload == {"igt_frames_restored": 120, "mario_acted": False}


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
