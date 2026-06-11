# tests/test_dust.py
from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.dust import DustTrickDetector
from sm64_events.memory.addresses import (ACT_BACKWARD_ROLLOUT, ACT_DIVE,
                                          ACT_DIVE_SLIDE, ACT_DOUBLE_JUMP,
                                          ACT_DOUBLE_JUMP_LAND,
                                          ACT_FORWARD_ROLLOUT, ACT_JUMP,
                                          ACT_JUMP_LAND, ACT_TRIPLE_JUMP)

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
    det = DustTrickDetector()
    events = []
    for prev, curr in zip(snaps, snaps[1:]):
        events.extend(det.process(prev, curr))
    return events


# -- rollouts -----------------------------------------------------------------
# Decomp-verified model (see detectors/dust.py): the dive's landing frame
# already shows ACT_DIVE_SLIDE in memory, but act_dive_slide first RUNS the
# next frame — so ONE visible slide frame is the frame-perfect input.

def test_one_slide_frame_is_frame_perfect_dustless():
    events = run([snap(ACT_DIVE, 100), snap(ACT_DIVE_SLIDE, 101),
                  snap(ACT_FORWARD_ROLLOUT, 102)])
    assert len(events) == 1
    ev = events[0]
    assert ev.type == "rollout" and ev.frame == 102
    assert ev.payload == {"dustless": True, "frames_late": 0,
                          "landing_frames": 1, "level": 24}


def test_two_slide_frames_is_one_late():
    events = run([snap(ACT_DIVE, 100), snap(ACT_DIVE_SLIDE, 101),
                  snap(ACT_DIVE_SLIDE, 102), snap(ACT_FORWARD_ROLLOUT, 103)])
    assert events[0].payload == {"dustless": False, "frames_late": 1,
                                 "landing_frames": 2, "level": 24}


def test_four_slide_frames_are_three_late():
    events = run([snap(ACT_DIVE, 100), snap(ACT_DIVE_SLIDE, 101),
                  snap(ACT_DIVE_SLIDE, 102), snap(ACT_DIVE_SLIDE, 103),
                  snap(ACT_DIVE_SLIDE, 104), snap(ACT_FORWARD_ROLLOUT, 105)])
    assert events[0].payload["frames_late"] == 3


def test_backward_rollout_also_fires():
    events = run([snap(ACT_DIVE, 100), snap(ACT_DIVE_SLIDE, 101),
                  snap(ACT_BACKWARD_ROLLOUT, 102)])
    assert len(events) == 1 and events[0].payload["dustless"] is True


def test_double_polled_slide_frames_count_once():
    # 60 Hz polling observes each 30 fps game frame ~twice (same global_timer)
    events = run([snap(ACT_DIVE, 100), snap(ACT_DIVE_SLIDE, 101),
                  snap(ACT_DIVE_SLIDE, 101), snap(ACT_DIVE_SLIDE, 102),
                  snap(ACT_DIVE_SLIDE, 102), snap(ACT_FORWARD_ROLLOUT, 103)])
    assert events[0].payload["frames_late"] == 1
    assert events[0].payload["landing_frames"] == 2


def test_direct_dive_to_rollout_edge_is_suppressed():
    # impossible per the decomp landing model; if a poll gap fabricates it,
    # we cannot judge the timing — refuse rather than guess
    assert run([snap(ACT_DIVE, 100), snap(ACT_FORWARD_ROLLOUT, 101)]) == []


def test_unobserved_slide_entry_is_suppressed():
    # attach/reconnect landed mid-slide: entry edge never seen -> no claim
    assert run([snap(ACT_DIVE_SLIDE, 101), snap(ACT_FORWARD_ROLLOUT, 102)]) == []
    assert run([snap(ACT_DIVE_SLIDE, 101), snap(ACT_DIVE_SLIDE, 102),
                snap(ACT_FORWARD_ROLLOUT, 103)]) == []


def test_rollout_from_non_dive_action_is_silent():
    assert run([snap(ACT_IDLE, 100), snap(ACT_FORWARD_ROLLOUT, 101)]) == []
    assert run([snap(ACT_WALKING, 100), snap(ACT_FORWARD_ROLLOUT, 101)]) == []


def test_continuing_rollout_does_not_refire():
    events = run([snap(ACT_DIVE, 100), snap(ACT_DIVE_SLIDE, 101),
                  snap(ACT_FORWARD_ROLLOUT, 102),
                  snap(ACT_FORWARD_ROLLOUT, 103)])
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
    assert events[0].payload == {"dustless": True, "frames_late": 0,
                                 "landing_frames": 1, "level": 24}


def test_backward_timer_jump_mid_slide_suppresses_then_recovers():
    events = run([
        snap(ACT_DIVE, 5000), snap(ACT_DIVE_SLIDE, 5001),
        snap(ACT_DIVE_SLIDE, 5002),
        snap(ACT_DIVE_SLIDE, 300),      # savestate load mid-slide
        snap(ACT_FORWARD_ROLLOUT, 301),  # timing unknowable -> no event
        snap(ACT_DIVE, 400), snap(ACT_DIVE_SLIDE, 401),
        snap(ACT_FORWARD_ROLLOUT, 402),  # clean trick fires again
    ])
    assert len(events) == 1
    assert events[0].frame == 402 and events[0].payload["dustless"] is True


def test_payload_carries_current_level():
    events = run([snap(ACT_DIVE, 100, level=8), snap(ACT_DIVE_SLIDE, 101, level=8),
                  snap(ACT_FORWARD_ROLLOUT, 102, level=8)])
    assert events[0].payload["level"] == 8


# -- chained jumps -------------------------------------------------------------

def test_frame_perfect_double_jump_is_dustless():
    events = run([snap(ACT_JUMP, 100), snap(ACT_JUMP_LAND, 101),
                  snap(ACT_DOUBLE_JUMP, 102)])
    assert len(events) == 1
    ev = events[0]
    assert ev.type == "jump" and ev.frame == 102
    assert ev.payload == {"dustless": True, "frames_late": 0,
                          "landing_frames": 1, "kind": "double", "level": 24}


def test_late_double_jump_counts_frames_late():
    events = run([snap(ACT_JUMP, 100), snap(ACT_JUMP_LAND, 101),
                  snap(ACT_JUMP_LAND, 102), snap(ACT_JUMP_LAND, 103),
                  snap(ACT_DOUBLE_JUMP, 104)])
    assert events[0].payload["frames_late"] == 2
    assert events[0].payload["dustless"] is False
    assert events[0].payload["kind"] == "double"


def test_frame_perfect_triple_jump_is_dustless():
    events = run([snap(ACT_DOUBLE_JUMP, 100), snap(ACT_DOUBLE_JUMP_LAND, 101),
                  snap(ACT_TRIPLE_JUMP, 102)])
    assert len(events) == 1
    assert events[0].payload["kind"] == "triple"
    assert events[0].payload["dustless"] is True


def test_single_rejump_from_jump_land_is_silent():
    # double-jump window expired: the game gives ACT_JUMP, not a chain jump
    assert run([snap(ACT_JUMP, 100), snap(ACT_JUMP_LAND, 101),
                snap(ACT_JUMP_LAND, 102), snap(ACT_JUMP, 103)]) == []


def test_landing_without_jump_is_silent():
    assert run([snap(ACT_JUMP, 100), snap(ACT_JUMP_LAND, 101),
                snap(ACT_WALKING, 102)]) == []


def test_full_triple_jump_chain_emits_both_events():
    events = run([
        snap(ACT_JUMP, 100),
        snap(ACT_JUMP_LAND, 101),
        snap(ACT_DOUBLE_JUMP, 102),            # frame perfect
        snap(ACT_DOUBLE_JUMP_LAND, 130),
        snap(ACT_DOUBLE_JUMP_LAND, 131),
        snap(ACT_TRIPLE_JUMP, 132),            # one late
    ])
    assert [e.payload["kind"] for e in events] == ["double", "triple"]
    assert events[0].payload["dustless"] is True
    assert events[1].payload == {"dustless": False, "frames_late": 1,
                                 "landing_frames": 2, "kind": "triple",
                                 "level": 24}
