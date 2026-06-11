# tests/test_rollout.py
from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.rollout import RolloutDetector
from sm64_events.memory.addresses import (ACT_BACKWARD_ROLLOUT, ACT_DIVE,
                                          ACT_DIVE_SLIDE, ACT_FORWARD_ROLLOUT)

ACT_IDLE = 0x0C400201
ACT_WALKING = 0x04000440


def snap(action, timer, level=24, **overrides) -> GameSnapshot:
    defaults = dict(
        wall_time_utc=datetime(2026, 6, 10, tzinfo=timezone.utc),
        global_timer=timer, mario_action=action, mario_action_timer=0,
        num_stars=5, last_completed_course=1, last_completed_star=3,
        igt_overall=300, curr_level=level)
    defaults.update(overrides)
    return GameSnapshot(**defaults)


def run(snaps):
    """Feed consecutive (prev, curr) pairs like the poller does."""
    det = RolloutDetector()
    events = []
    for prev, curr in zip(snaps, snaps[1:]):
        events.extend(det.process(prev, curr))
    return events


def test_direct_dive_to_rollout_edge_is_dustless():
    events = run([snap(ACT_DIVE, 100), snap(ACT_FORWARD_ROLLOUT, 101)])
    assert len(events) == 1
    ev = events[0]
    assert ev.type == "rollout" and ev.frame == 101
    assert ev.payload == {"dustless": True, "frames_late": 0, "level": 24}


def test_backward_rollout_also_fires():
    events = run([snap(ACT_DIVE, 100), snap(ACT_BACKWARD_ROLLOUT, 101)])
    assert len(events) == 1 and events[0].payload["dustless"] is True


def test_payload_carries_current_level():
    events = run([snap(ACT_DIVE, 100, level=8),
                  snap(ACT_FORWARD_ROLLOUT, 101, level=8)])
    assert events[0].payload["level"] == 8


def test_one_slide_frame_is_one_frame_late():
    events = run([snap(ACT_DIVE, 100), snap(ACT_DIVE_SLIDE, 101),
                  snap(ACT_FORWARD_ROLLOUT, 102)])
    assert len(events) == 1
    assert events[0].payload == {"dustless": False, "frames_late": 1, "level": 24}


def test_three_slide_frames_are_three_late():
    events = run([snap(ACT_DIVE, 100), snap(ACT_DIVE_SLIDE, 101),
                  snap(ACT_DIVE_SLIDE, 102), snap(ACT_DIVE_SLIDE, 103),
                  snap(ACT_FORWARD_ROLLOUT, 104)])
    assert events[0].payload["frames_late"] == 3


def test_double_polled_slide_frames_count_once():
    # 60 Hz polling observes each 30 fps game frame ~twice (same global_timer)
    events = run([snap(ACT_DIVE, 100), snap(ACT_DIVE_SLIDE, 101),
                  snap(ACT_DIVE_SLIDE, 101), snap(ACT_DIVE_SLIDE, 102),
                  snap(ACT_DIVE_SLIDE, 102), snap(ACT_FORWARD_ROLLOUT, 103)])
    assert events[0].payload["frames_late"] == 2


def test_slide_to_rollout_without_observed_entry_is_at_least_one_late():
    # first pair after attach lands mid-slide: prev IS the slide
    events = run([snap(ACT_DIVE_SLIDE, 101), snap(ACT_FORWARD_ROLLOUT, 102)])
    assert events[0].payload == {"dustless": False, "frames_late": 1, "level": 24}


def test_rollout_from_non_dive_action_is_silent():
    # reconnect mid-rollout / slide-kick rollouts are not the dive trick
    assert run([snap(ACT_IDLE, 100), snap(ACT_FORWARD_ROLLOUT, 101)]) == []
    assert run([snap(ACT_WALKING, 100), snap(ACT_FORWARD_ROLLOUT, 101)]) == []


def test_continuing_rollout_does_not_refire():
    events = run([snap(ACT_DIVE, 100), snap(ACT_FORWARD_ROLLOUT, 101),
                  snap(ACT_FORWARD_ROLLOUT, 102)])
    assert len(events) == 1


def test_abandoned_slide_does_not_leak_frames_into_next_rollout():
    events = run([
        snap(ACT_DIVE, 100), snap(ACT_DIVE_SLIDE, 101),
        snap(ACT_DIVE_SLIDE, 102), snap(ACT_DIVE_SLIDE, 103),
        snap(ACT_IDLE, 110),                      # slide fizzled, no rollout
        snap(ACT_DIVE, 200), snap(ACT_DIVE_SLIDE, 201),
        snap(ACT_FORWARD_ROLLOUT, 202),
    ])
    assert len(events) == 1
    assert events[0].payload["frames_late"] == 1


def test_self_heals_when_global_timer_jumps_backward():
    events = run([
        snap(ACT_DIVE, 5000), snap(ACT_DIVE_SLIDE, 5001),
        snap(ACT_DIVE_SLIDE, 5002),
        snap(ACT_DIVE_SLIDE, 300),      # savestate load mid-slide
        snap(ACT_FORWARD_ROLLOUT, 301),
    ])
    # the pre-jump slide frames must not inflate the count
    assert len(events) == 1
    assert events[0].payload["frames_late"] == 1
