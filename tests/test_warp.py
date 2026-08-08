from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.core.timefmt import format_igt
from sm64_events.detectors.igt_clock import IgtClock
from sm64_events.detectors.warp import WarpDetector
from sm64_events.memory.addresses import (ACT_BBH_ENTER_JUMP,
                                          ACT_BBH_ENTER_SPIN,
                                          ACT_DISAPPEARED,
                                          ACT_TELEPORT_FADE_OUT)

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
    where the entrance led. Snapshots here leave sWarpDest at its zero default,
    which reads as "no destination written", so every case built on this helper
    exercises the BACKSTOP path rather than the touch-frame publish."""
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


def dest(type_=1, level=23, area=1, node=10):
    """sWarpDest's four bytes, as a snapshot kwargs fragment."""
    return dict(warp_dest_type=type_, warp_dest_level=level,
                warp_dest_area=area, warp_dest_node=node)


def test_a_painting_touch_publishes_on_the_touch_frame_itself():
    """No wait at all for the case he reported. Live, 2026-08-05: a painting
    or portal writes sWarpDest at or before the frame Mario's action becomes
    ACT_DISAPPEARED, so the destination is already there to be read -- 13 of 13
    castle entries, 76-78 frames before the level byte moved."""
    detector = WarpDetector()
    before = snap(global_timer=2000, curr_level=6, **dest(level=6, node=31))
    detector.process(snap(global_timer=1999, curr_level=6,
                          **dest(level=6, node=31)), before)
    [event] = detector.process(
        before,
        snap(global_timer=2001, curr_level=6, mario_action=ACT_DISAPPEARED,
             **dest(level=23)))
    assert event.payload["to"] == 23, "the portal named DDD at the touch"
    assert event.frame == 2001


def test_a_pipe_touch_waits_out_its_stale_destination():
    """The negative case that proves the freshness test, and the reason
    `type != 0` cannot be the test on its own -- it survives a COMPLETED
    painting warp. A pipe is the one genuinely delayed warp: sWarpDest still
    holds the previous destination at the touch and is written 20 frames
    later, when sDelayedWarpOp's timer runs out."""
    detector = WarpDetector()
    stale = dict(curr_level=6, **dest(level=6, node=31))
    detector.process(snap(global_timer=1999, **stale),
                     snap(global_timer=2000, **stale))
    assert detector.process(
        snap(global_timer=2000, **stale),
        snap(global_timer=2001, mario_action=ACT_DISAPPEARED, **stale)) == [], \
        "a destination written before the touch is somebody else's"
    for offset in range(2, 21):
        assert detector.process(
            snap(global_timer=2000 + offset - 1, mario_action=ACT_DISAPPEARED,
                 **stale),
            snap(global_timer=2000 + offset, mario_action=ACT_DISAPPEARED,
                 pending_warp_op=0x04, **stale)) == [], f"published at +{offset}"
    [event] = detector.process(
        snap(global_timer=2020, mario_action=ACT_DISAPPEARED,
             pending_warp_op=0x04, **stale),
        snap(global_timer=2021, curr_level=6, mario_action=ACT_DISAPPEARED,
             **dest(level=17)))
    assert event.payload["to"] == 17
    assert event.frame == 2001, "back-dated to the touch, not the write"


def test_the_first_destination_seen_is_never_treated_as_fresh():
    """A detector built mid-session inherits whatever warp preceded it. That
    value is unstamped on purpose: unstamped means hold, and holding is the
    direction that cannot publish a wrong destination."""
    detector = WarpDetector()
    live = dict(curr_level=6, **dest(level=23))
    assert detector.process(
        snap(global_timer=2000, **live),
        snap(global_timer=2001, mario_action=ACT_DISAPPEARED, **live)) == []


def test_the_level_edge_still_answers_when_the_struct_never_does():
    """The backstop, unchanged. Whatever the destination struct does or does
    not say, a level edge names where the touch led and publishes it
    back-dated. Nothing that fired before the freshness test may stop
    firing."""
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


def test_the_bbh_cage_is_an_entrance_like_any_painting():
    """The one course entrance that fires NO generic warp action (his probe
    run, 2026-08-07: five entrances produced clean touches, BBH produced
    nothing). The cage plays its own pair — ENTER_JUMP then ENTER_SPIN 14
    frames later, level byte at +74 — and the JUMP is the commit moment, so
    the touch is its edge and `to` comes from the level edge exactly like a
    struct-silent painting. Repairs `seg:ccm->bbh`, whose entrance_touched
    end could never fire before this."""
    detector = WarpDetector()
    prev = snap(global_timer=2000, curr_level=26)          # the courtyard
    jump = snap(global_timer=2001, curr_level=26,
                mario_action=ACT_BBH_ENTER_JUMP)
    assert detector.process(prev, jump) == [], "held until it knows where"
    spin = snap(global_timer=2015, curr_level=26,
                mario_action=ACT_BBH_ENTER_SPIN)
    assert detector.process(jump, spin) == [], "the pair is ONE touch"
    arrived = snap(global_timer=2075, curr_level=4,
                   mario_action=ACT_BBH_ENTER_SPIN)
    events = detector.process(spin, arrived)
    assert len(events) == 1
    assert events[0].type == "warp_entered"
    assert events[0].frame == 2001, "the moment is the jump, not the load"
    assert events[0].payload["to"] == 4
    assert events[0].payload["level"] == 26


def test_the_spin_alone_is_not_a_touch():
    """ACT_BBH_ENTER_SPIN is deliberately outside the entry set — it is the
    second action of the pair, and the one a leaving-BBH probe window also
    caught (addresses.py has the arithmetic). A spin with no jump before it
    must not invent an entrance."""
    detector = WarpDetector()
    prev = snap(global_timer=2000, curr_level=26)
    spin = snap(global_timer=2001, curr_level=26,
                mario_action=ACT_BBH_ENTER_SPIN)
    assert detector.process(prev, spin) == []
    assert detector.process(
        spin, snap(global_timer=2075, curr_level=4,
                   mario_action=ACT_BBH_ENTER_SPIN)) == []


def test_the_touch_carries_WHICH_warp_it_was():
    """Read at the TOUCH, not at publish. A painting publishes on its own frame
    but a pipe waits ~20 for the destination, and by then Mario is engaged with
    nothing -- so the landmark belongs to the touch exactly like the frame and
    the time already held beside it.

    Until 2026-08-06 every `warp_entered` in the journal carried `landmark:
    null`, so his two BoB warps (ids 2327 and 2330) were the same sentence
    twice and neither could be renamed.
    """
    detector = WarpDetector()
    touching = snap(mario_action=ACT_DISAPPEARED, curr_level=9,
                    landmark_behaviour=0xAABBCCDD,
                    landmark_home=(120.0, 0.0, -30.0))
    assert detector.process(snap(curr_level=9), touching) == []
    [event] = land(detector, touching)
    assert event.payload["landmark"]["key"] == "9:1:aabbccdd:120,0,-30"
    assert event.payload["landmark"]["placed"] is True


def test_a_touch_that_engages_nothing_names_nothing():
    """A warp the object pool cannot name still records; `None` is what makes
    the label fall back to "Entered a warp in ..." rather than inventing one."""
    detector = WarpDetector()
    touching = snap(mario_action=ACT_DISAPPEARED)
    assert detector.process(snap(), touching) == []
    [event] = land(detector, touching)
    assert event.payload["landmark"] is None
