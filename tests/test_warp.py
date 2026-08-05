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


def land(detector, during, to=23, after=77):
    """Drive the RELEASE half of a touch: the level edge that finally names
    where the entrance led. The touch itself carries no destination (decomp:
    sWarpDest is written 77 frames later), so every emit in this file needs
    one of these."""
    return detector.process(
        during,
        snap(global_timer=during.global_timer + after,
             mario_action=during.mario_action, curr_level=to))


def test_edge_into_warp_action_emits_warp_entered():
    detector = WarpDetector()
    during = snap(mario_action=ACT_DISAPPEARED)
    assert detector.process(snap(), during) == [], "held until it knows where"
    events = land(detector, during)
    assert len(events) == 1
    assert events[0].type == "warp_entered"
    # the scoping context, pinned by name -- the igt trio this payload also
    # carries has its own cases below and in tests/test_segment_igt.py
    assert events[0].payload["level"] == 17
    assert events[0].payload["area"] == 1
    assert events[0].payload["action"] == ACT_DISAPPEARED


def test_the_touch_is_held_until_the_destination_is_known():
    """The touch cannot name where it leads: decomp `level_trigger_warp()`
    writes nothing to sWarpDest, which `initiate_delayed_warp()` fills 77
    frames later, immediately before the level unloads. So the event is HELD
    and published back-dated once the level edge answers the question."""
    detector = WarpDetector()
    assert detector.process(snap(global_timer=2000),
                            snap(global_timer=2001,
                                 mario_action=ACT_DISAPPEARED)) == []
    assert detector.process(snap(global_timer=2001, mario_action=ACT_DISAPPEARED),
                            snap(global_timer=2002,
                                 mario_action=ACT_DISAPPEARED)) == []
    events = detector.process(
        snap(global_timer=2077, mario_action=ACT_DISAPPEARED),
        snap(global_timer=2078, mario_action=ACT_DISAPPEARED, curr_level=23))
    assert len(events) == 1
    assert events[0].payload["to"] == 23
    assert events[0].frame == 2001, "back-dated to the touch, not the release"


def test_the_pending_warp_flag_clearing_does_NOT_end_the_wait():
    """THE live bug, 2026-08-05. A grace window on pending_warp_op looked like
    the precise way to resolve an in-level teleporter promptly, and it
    published `to: None` on every real painting entry instead: the game clears
    sDelayedWarpOp when the delayed warp INITIATES (sDelayedWarpTimer = 20),
    and ~57 more frames of fade follow before the level byte moves. His
    journal, ids 25415/25371: touch at 2519145, level edge at 2519222, exactly
    77 frames -- and MIPS Clip kept timing to the DDD load."""
    detector = WarpDetector()
    during = snap(global_timer=2000, curr_level=6, pending_warp_op=0x06,
                  mario_action=ACT_DISAPPEARED)
    detector.process(snap(global_timer=1999, curr_level=6), during)
    # the flag goes quiet 20 frames in, as the real game does
    for offset in range(1, 77):
        events = detector.process(
            snap(global_timer=2000 + offset - 1, curr_level=6,
                 mario_action=ACT_DISAPPEARED,
                 pending_warp_op=0x06 if offset <= 20 else 0),
            snap(global_timer=2000 + offset, curr_level=6,
                 mario_action=ACT_DISAPPEARED,
                 pending_warp_op=0x06 if offset <= 20 else 0))
        assert events == [], f"published early at +{offset}"
    [event] = detector.process(
        snap(global_timer=2076, curr_level=6, mario_action=ACT_DISAPPEARED),
        snap(global_timer=2077, curr_level=23, mario_action=ACT_DISAPPEARED))
    assert event.payload["to"] == 23
    assert event.frame == 2000


def test_a_destination_free_touch_is_still_journaled():
    """Nothing that fired before the hold may stop firing. An in-level
    teleporter (CCM broken bridge, WDW corners) relocates Mario inside his own
    area, so no level or area edge ever comes -- the hold cap is what releases
    it, and the honest destination is None."""
    detector = WarpDetector()
    detector.process(snap(global_timer=2000, curr_level=5),
                     snap(global_timer=2001, curr_level=5,
                          mario_action=ACT_TELEPORT_FADE_OUT))
    events = []
    for offset in range(2, 400):
        events = detector.process(
            snap(global_timer=2000 + offset - 1, curr_level=5,
                 mario_action=ACT_TELEPORT_FADE_OUT),
            snap(global_timer=2000 + offset, curr_level=5,
                 mario_action=ACT_TELEPORT_FADE_OUT))
        if events:
            break
    assert len(events) == 1
    assert events[0].payload["to"] is None
    assert events[0].frame == 2001
    assert offset <= WarpDetector.HOLD_CAP_FRAMES + 1


def test_a_held_touch_is_never_lost_to_a_stalled_warp():
    """Absolute cap -- a hold with no clock is how an event disappears. An
    aborted fade (savestate mid-warp) must still journal the touch."""
    detector = WarpDetector()
    detector.process(snap(global_timer=2000),
                     snap(global_timer=2001, mario_action=ACT_DISAPPEARED,
                          pending_warp_op=0x08))
    events = []
    for offset in range(2, 600):
        events = detector.process(
            snap(global_timer=2000 + offset - 1, mario_action=ACT_DISAPPEARED,
                 pending_warp_op=0x08),
            snap(global_timer=2000 + offset, mario_action=ACT_DISAPPEARED,
                 pending_warp_op=0x08))
        if events:
            break
    assert len(events) == 1
    assert events[0].payload["to"] is None
    assert events[0].frame == 2001
    assert offset <= WarpDetector.HOLD_CAP_FRAMES + 1


def test_a_console_reset_releases_the_held_touch():
    """game_reset restarts gGlobalTimer. The held touch belongs to the epoch
    before it and must be journaled, not stranded in a detector that will
    never see its frame again."""
    detector = WarpDetector()
    detector.process(snap(global_timer=9000),
                     snap(global_timer=9001, mario_action=ACT_DISAPPEARED,
                          pending_warp_op=0x08))
    [event] = detector.process(
        snap(global_timer=9001, mario_action=ACT_DISAPPEARED,
             pending_warp_op=0x08),
        snap(global_timer=40, pending_warp_op=0))
    assert event.payload["to"] is None
    assert event.frame == 9001


def test_the_pipe_touch_carries_usamunes_running_counter():
    """A segment ending here records THIS number, not a global_timer delta
    (live report 2026-07-31; tracking/segments.py's rta_frames clause). No
    Usamune RESULT is written at a pipe, so the source is always `counter`:
    the running counter plus the one-frame display tick."""
    detector = WarpDetector()
    detector.process(snap(global_timer=1998, igt_overall=1076),
                     snap(global_timer=1999, igt_overall=1077))
    during = snap(global_timer=2000, igt_overall=1078,
                  mario_action=ACT_DISAPPEARED)
    assert detector.process(snap(global_timer=1999, igt_overall=1077),
                            during) == []
    [event] = land(detector, during)
    assert event.payload["igt_source"] == "counter"
    assert event.payload["igt_frames"] == 1078 + IgtClock.DISPLAY_TICK
    assert event.payload["igt"] == format_igt(event.payload["igt_frames"])


def test_the_held_touch_carries_the_TOUCH_frames_igt_not_the_releases():
    """Reading the clock at release would measure the fade -- the exact class
    of bug the 2026-07-31 pipe fix removed. The release snapshot below has
    already been zeroed by the level load; the recorded number must ignore
    it."""
    detector = WarpDetector()
    detector.process(snap(global_timer=1998, igt_overall=1076),
                     snap(global_timer=1999, igt_overall=1077))
    detector.process(snap(global_timer=1999, igt_overall=1077),
                     snap(global_timer=2000, igt_overall=1078,
                          mario_action=ACT_DISAPPEARED))
    [event] = detector.process(
        snap(global_timer=2076, igt_overall=1078, mario_action=ACT_DISAPPEARED),
        snap(global_timer=2077, igt_overall=0, mario_action=ACT_DISAPPEARED,
             curr_level=23))
    assert event.payload["igt_frames"] == 1078 + IgtClock.DISPLAY_TICK
    assert event.payload["igt"] == format_igt(event.payload["igt_frames"])
    assert event.frame == 2000


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
    during = snap(global_timer=2000, igt_overall=100, igt_result=812,
                  mario_action=ACT_DISAPPEARED)
    detector.process(snap(global_timer=1999, igt_overall=99, igt_result=812),
                     during)
    [event] = land(detector, during)
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
    during = snap(global_timer=501, igt_overall=41,
                  mario_action=ACT_DISAPPEARED)
    detector.process(snap(global_timer=500, igt_overall=40), during)
    [event] = land(detector, during)
    assert event.payload["igt_frames"] == 41 + IgtClock.DISPLAY_TICK


def test_no_second_touch_while_one_is_already_held():
    """A fade in progress is one warp. A second action edge inside it must not
    stack a second held touch, or one entrance journals twice."""
    detector = WarpDetector()
    during = snap(global_timer=2001, mario_action=ACT_DISAPPEARED)
    detector.process(snap(global_timer=2000), during)
    detector.process(during, snap(global_timer=2002))
    assert detector.process(snap(global_timer=2002),
                            snap(global_timer=2003,
                                 mario_action=ACT_DISAPPEARED)) == []
    events = detector.process(
        snap(global_timer=2003, mario_action=ACT_DISAPPEARED),
        snap(global_timer=2078, mario_action=ACT_DISAPPEARED, curr_level=23))
    assert len(events) == 1
    assert events[0].frame == 2001, "the FIRST touch, not the second"


def test_teleport_fade_out_also_emits_warp_entered():
    detector = WarpDetector()
    during = snap(mario_action=ACT_TELEPORT_FADE_OUT)
    detector.process(snap(), during)
    events = land(detector, during)
    assert len(events) == 1
    assert events[0].payload["action"] == ACT_TELEPORT_FADE_OUT


def test_exit_from_warp_action_is_silent():
    # savestate-load mid-warp: prev=warp, curr=idle -> no edge-in, no event
    assert WarpDetector().process(snap(mario_action=ACT_DISAPPEARED), snap()) == []
