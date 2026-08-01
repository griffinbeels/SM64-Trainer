from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.core.timefmt import format_igt
from sm64_events.detectors.igt_clock import IgtClock
from sm64_events.detectors.warp import WarpDetector
from sm64_events.memory.addresses import ACT_DISAPPEARED, ACT_TELEPORT_FADE_OUT

ACT_IDLE = 0x0C400201


def snap(**overrides) -> GameSnapshot:
    defaults = dict(
        wall_time_utc=datetime(2026, 6, 11, tzinfo=timezone.utc),
        global_timer=2000, mario_action=ACT_IDLE, mario_action_timer=0,
        num_stars=8, last_completed_course=1, last_completed_star=1,
        curr_level=17, curr_area=1)
    defaults.update(overrides)
    return GameSnapshot(**defaults)


def test_edge_into_warp_action_emits_warp_entered():
    events = WarpDetector().process(snap(), snap(mario_action=ACT_DISAPPEARED))
    assert len(events) == 1
    assert events[0].type == "warp_entered"
    # the scoping context, pinned by name -- the igt trio this payload also
    # carries has its own cases below and in tests/test_segment_igt.py
    assert events[0].payload["level"] == 17
    assert events[0].payload["area"] == 1
    assert events[0].payload["action"] == ACT_DISAPPEARED


def test_the_pipe_touch_carries_usamunes_running_counter():
    """A segment ending here records THIS number, not a global_timer delta
    (live report 2026-07-31; tracking/segments.py's rta_frames clause). No
    Usamune RESULT is written at a pipe, so the source is always `counter`:
    the running counter plus the one-frame display tick."""
    detector = WarpDetector()
    detector.process(snap(global_timer=1998, igt_overall=1076),
                     snap(global_timer=1999, igt_overall=1077))
    [event] = detector.process(snap(global_timer=1999, igt_overall=1077),
                               snap(global_timer=2000, igt_overall=1078,
                                    mario_action=ACT_DISAPPEARED))
    assert event.payload["igt_source"] == "counter"
    assert event.payload["igt_frames"] == 1078 + IgtClock.DISPLAY_TICK
    assert event.payload["igt"] == format_igt(event.payload["igt_frames"])


def test_a_star_grabbed_earlier_in_the_run_does_not_hijack_the_time():
    """A reds->pipe run leaves Usamune's result store holding that star's
    time. IgtClock's freshness rule is what keeps the pipe on the counter --
    this pins that the shared clock is doing that job, so no second
    warp-specific clock is needed."""
    detector = WarpDetector()
    for frame in range(1900, 2000):
        detector.process(snap(global_timer=frame - 1, igt_overall=frame - 1901,
                              igt_result=812),
                         snap(global_timer=frame, igt_overall=frame - 1900,
                              igt_result=812))
    [event] = detector.process(
        snap(global_timer=1999, igt_overall=99, igt_result=812),
        snap(global_timer=2000, igt_overall=100, igt_result=812,
             mario_action=ACT_DISAPPEARED))
    assert event.payload["igt_source"] == "counter"
    assert event.payload["igt_frames"] == 100 + IgtClock.DISPLAY_TICK


def test_the_clock_self_heals_on_a_backward_timer_jump():
    """Domain rule 4: a savestate load rewinds global_timer, and the samples
    from before it must not be extrapolated across the gap."""
    detector = WarpDetector()
    for frame in range(9000, 9040):
        detector.process(snap(global_timer=frame - 1, igt_overall=frame - 8000),
                         snap(global_timer=frame, igt_overall=frame - 7999))
    detector.process(snap(global_timer=9039, igt_overall=1040),
                     snap(global_timer=500, igt_overall=40))
    [event] = detector.process(
        snap(global_timer=500, igt_overall=40),
        snap(global_timer=501, igt_overall=41, mario_action=ACT_DISAPPEARED))
    assert event.payload["igt_frames"] == 41 + IgtClock.DISPLAY_TICK


def test_no_event_while_warp_action_persists():
    d = WarpDetector()
    d.process(snap(), snap(mario_action=ACT_DISAPPEARED))
    assert d.process(snap(mario_action=ACT_DISAPPEARED),
                     snap(mario_action=ACT_DISAPPEARED)) == []


def test_teleport_fade_out_also_emits_warp_entered():
    events = WarpDetector().process(
        snap(), snap(mario_action=ACT_TELEPORT_FADE_OUT))
    assert len(events) == 1
    assert events[0].payload["action"] == ACT_TELEPORT_FADE_OUT


def test_exit_from_warp_action_is_silent():
    # savestate-load mid-warp: prev=warp, curr=idle -> no edge-in, no event
    assert WarpDetector().process(snap(mario_action=ACT_DISAPPEARED), snap()) == []
