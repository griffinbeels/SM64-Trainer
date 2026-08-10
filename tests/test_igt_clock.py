# tests/test_igt_clock.py
"""The shared Usamune IGT clock. Its source-precedence behaviour is exercised
end-to-end through test_star_grab.py and test_key.py; these lock the public
interface (empty / observe / igt_at) so a consumer refactor can't silently
break the contract."""
from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.igt_clock import IgtClock


def snap(global_timer, igt_overall=0, igt_result=0,
         curr_level=24, curr_area=1) -> GameSnapshot:
    return GameSnapshot(
        wall_time_utc=datetime(2026, 6, 12, tzinfo=timezone.utc),
        global_timer=global_timer, mario_action=0, mario_action_timer=0,
        num_stars=0, last_completed_course=0, last_completed_star=0,
        igt_overall=igt_overall, igt_result=igt_result,
        curr_level=curr_level, curr_area=curr_area)


def walk(clock, frames, **kw):
    """Feed consecutive frames, since the basis tracking is edge-driven."""
    for frame in frames:
        clock.observe(snap(frame, **kw))


def test_empty_until_first_observe():
    c = IgtClock()
    assert c.empty()
    c.observe(snap(100))
    assert not c.empty()


def test_fresh_result_is_authoritative():
    c = IgtClock()
    c.observe(snap(1386, igt_overall=1386, igt_result=0))
    curr = snap(1389, igt_overall=1388, igt_result=1388)
    c.observe(curr)
    assert c.igt_at(1387, curr) == (1388, "result")


def test_counter_path_adds_the_display_tick():
    # result store untouched (0) -> overall counter back-computed + 1 tick
    c = IgtClock()
    c.observe(snap(1386, igt_overall=1386, igt_result=0))
    curr = snap(1387, igt_overall=1387, igt_result=0)
    c.observe(curr)
    assert c.igt_at(1387, curr) == (1388, "counter")


# `counter_may_be_subarea_local` gates the FAST publish in star_grab.py: True
# costs the full settle wait. Live report 2026-08-02 -- "the FIRST star that I
# grab after entering a course has an exceptionally high amount of delay...
# BUT THEN ALL THE OTHER STARS ARE ACTUALLY PERFECTLY TIMED" -- and his
# journal agreed exactly: `published_after` was 45 frames on the first grab of
# each course entry and 1 frame on every other grab.


def enter_a_course(clock, start=1000):
    """Arriving in SSL from the castle, as the bytes actually move.

    The level byte changes on one frame and the AREA byte settles afterwards
    (3->2->1 over ~47 frames, measured 2026-08-01), so the load's own area
    edges land well after the level edge that explains them -- which is the
    whole reason the arrival used to read as a warp deeper into the level.
    """
    walk(clock, range(start, start + 3), igt_overall=900, curr_level=6)
    walk(clock, [start + 3], curr_level=8, curr_area=3, igt_overall=900)
    walk(clock, [start + 20], curr_level=8, curr_area=2, igt_overall=900)
    walk(clock, [start + 47], curr_level=8, curr_area=1, igt_overall=900)
    walk(clock, [start + 50], curr_level=8, curr_area=1, igt_overall=0)


def test_arriving_in_a_course_is_not_a_subarea_load():
    c = IgtClock()
    enter_a_course(c)
    assert c.counter_may_be_subarea_local() is False


def test_warping_deeper_mid_run_is_a_subarea_load():
    # The pyramid door, ten seconds into the run: an area edge with no level
    # edge to explain it, and the counter zeroing beside it.
    c = IgtClock()
    enter_a_course(c)
    walk(c, range(1400, 1403), curr_level=8, curr_area=1, igt_overall=350)
    walk(c, [1403], curr_level=8, curr_area=2, igt_overall=350)
    walk(c, [1405], curr_level=8, curr_area=2, igt_overall=0)
    assert c.counter_may_be_subarea_local() is True


def test_the_time_before_the_pyramid_door_is_carried():
    # The half of a subarea star the counter throws away. 480 frames to reach
    # the door, and the counter restarts there -- so the whole star is 480
    # plus whatever the subarea takes, which is the difference Usamune's own
    # write burst shows (SSL Pyramid [[0, 69], [1, 71], [27, 551]] -> 480).
    c = IgtClock()
    enter_a_course(c)
    walk(c, range(1500, 1503), curr_level=8, curr_area=1, igt_overall=480)
    walk(c, [1503], curr_level=8, curr_area=2, igt_overall=480)
    walk(c, [1505], curr_level=8, curr_area=2, igt_overall=0)
    assert c.banked_frames() == 480
    grabbed = snap(1577, igt_overall=71, curr_level=8, curr_area=2)
    c.observe(grabbed)
    assert c.whole_star_igt_at_xcam(1577, grabbed) == (552, "counter")


def test_every_leg_is_banked_not_just_the_last_one():
    # The two rows the single cached half could never state: CCM 100 Coins,
    # which crosses TWO involuntary restarts and published 37 seconds short of
    # the truth (2026-08-04, journal ids 23370 and 23799). An accumulator
    # covers any number of legs by construction.
    c = IgtClock()
    enter_a_course(c)
    walk(c, range(1500, 1503), curr_level=8, curr_area=1, igt_overall=400)
    walk(c, [1503], curr_level=8, curr_area=2, igt_overall=400)   # deeper
    walk(c, [1505], curr_level=8, curr_area=2, igt_overall=0)     # leg 1 banked
    walk(c, range(1600, 1603), curr_level=8, curr_area=2, igt_overall=300)
    walk(c, [1603], curr_level=8, curr_area=3, igt_overall=300)   # deeper again
    walk(c, [1605], curr_level=8, curr_area=3, igt_overall=0)     # leg 2 banked
    assert c.banked_frames() == 700
    grabbed = snap(1700, igt_overall=95, curr_level=8, curr_area=3)
    c.observe(grabbed)
    assert c.whole_star_igt_at_xcam(1700, grabbed) == (796, "counter")


def test_nothing_is_carried_across_a_restart_into_the_main_area():
    # A retry's own reload lands back in area 1, and carrying a previous run's
    # time across it is the one failure here that would record a wrong number
    # silently. The KNOWN residual, stated rather than hidden: walking OUT of a
    # subarea on foot lands in area 1 too and is indistinguishable -- it occurs
    # 0 times in 875 measured grabs, and the correction watch still covers it.
    c = IgtClock()
    enter_a_course(c)
    walk(c, range(1500, 1503), curr_level=8, curr_area=2, igt_overall=480)
    walk(c, [1503], curr_level=8, curr_area=1, igt_overall=480)
    walk(c, [1505], curr_level=8, curr_area=1, igt_overall=0)
    assert c.banked_frames() == 0
    assert c.counter_may_be_subarea_local() is False
    grabbed = snap(1577, igt_overall=71, curr_level=8, curr_area=1)
    c.observe(grabbed)
    assert c.whole_star_igt_at_xcam(1577, grabbed) == (72, "counter")


def test_a_reset_clears_the_subarea_basis():
    # His own probe: enter, reset immediately, grab -- and the grab was fast.
    # An L-reset in the course's main area zeroes the counter with no area
    # edge beside it, so the zero point IS the start of the run.
    c = IgtClock()
    enter_a_course(c)
    walk(c, range(1400, 1403), curr_level=8, curr_area=1, igt_overall=350)
    walk(c, [1403], curr_level=8, curr_area=1, igt_overall=0)
    assert c.counter_may_be_subarea_local() is False


# -- a spawn is the ZERO, not a reading of the counter -------------------------
# His report, 2026-08-06: *"for 'starting' a level, the timer event should be
# at 0"00"*, against a recorder row reading "Started Bob-omb Battlefield
# 16"33". Measured from his own journal at capture time: every recent `spawned`
# carried `igt_source: "reconstructed"` and the PREVIOUS run's final time (ids
# 2345 1'11"10, 2348 0'33"60, 2354 0'07"13).
#
# The mechanism is `_reading`'s reset-race guard doing its job in the one place
# its premise is inverted. For a STAR grabbed a blink after the counter reset,
# the grab concluded the attempt that was being played, so reconstructing the
# pre-reset value is right. A SPAWN is the other side of the same edge: it
# opens the attempt the reset started, so the only honest number is zero.

def test_a_spawn_at_a_counter_reset_reads_zero_not_the_run_that_just_ended():
    c = IgtClock()
    walk(c, range(1000, 1030), curr_level=8, igt_overall=500)
    arrival = snap(1031, igt_overall=0, curr_level=8)
    c.observe(arrival)
    assert c.igt_at(1031, arrival)[1] == "reconstructed", (
        "the fixture no longer reproduces the reported shape")
    assert c.igt_at_spawn(1031, arrival) == (0, "spawn")


def test_a_spawn_whose_counter_has_already_zeroed_reads_a_literal_zero():
    """Not one DISPLAY_TICK. The tick compensates the counter path -- Usamune's
    display leads the counter by a frame -- and a spawn is not a reading of the
    counter at all, it is the origin the counter is about to measure from."""
    c = IgtClock()
    walk(c, range(1000, 1005), curr_level=8, igt_overall=0)
    arrival = snap(1006, igt_overall=0, curr_level=8)
    c.observe(arrival)
    assert c.igt_at_spawn(1006, arrival) == (0, "spawn")


def test_a_spawn_ACTION_in_the_middle_of_a_run_states_the_real_counter():
    """The guard that stops a stated zero from meaning "we could not read it".
    A cannon exit and a savestate loaded mid-run both enter a spawn action with
    the counter running, and `spawn.py` says those are harmless -- they are
    only harmless while they keep telling the truth about the clock."""
    c = IgtClock()
    walk(c, range(1000, 1030), curr_level=8, igt_overall=900)
    mid_run = snap(1031, igt_overall=901, curr_level=8)
    c.observe(mid_run)
    assert c.igt_at_spawn(1031, mid_run) == (902, "counter")
