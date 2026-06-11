# tests/test_anchors.py
from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.anchors import AnchorDetector

ACT_IDLE = 0x0C400201


def snap(timer: int, igt: int = 0) -> GameSnapshot:
    return GameSnapshot(
        wall_time_utc=datetime(2026, 6, 10, tzinfo=timezone.utc),
        global_timer=timer, mario_action=ACT_IDLE, mario_action_timer=0,
        num_stars=5, last_completed_course=1, last_completed_star=3,
        igt_overall=igt)


def test_igt_drop_to_zero_emits_practice_reset():
    events = AnchorDetector().process(snap(1000, igt=500), snap(1002, igt=0))
    assert len(events) == 1
    ev = events[0]
    assert ev.type == "practice_reset" and ev.frame == 1002
    assert ev.payload == {"igt_frames_before": 500}


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
    assert ev.payload == {"igt_frames_restored": 120}


def test_backward_jump_into_boot_range_is_left_to_game_reset():
    assert AnchorDetector().process(snap(5000, igt=900), snap(50, igt=0)) == []


def test_state_loaded_takes_priority_over_practice_reset():
    # a load that also restores a near-zero IGT must classify as state_loaded
    events = AnchorDetector().process(snap(5000, igt=900), snap(3000, igt=3))
    assert [e.type for e in events] == ["state_loaded"]
