"""Resetting out of a star dance records ONE attempt: the grab.

Live report 2026-08-01: a single Whomp's Fortress grab produced two rows in
the practice log — `#2 ✗ reset 0'14"06` sitting under `#3 ✓ 0'13"53`. His
ruling: "we should NOT ever count a reset once we've triggered the star
grab… it should always be a single entry."

The cause is ORDERING, not attribution. Since the x-cam fix `star_collected`
is HELD (detectors/star_grab.py) and published 45+ frames after the grab, or
the moment a reset breaks that wait — so the reset tick carries two events
about two different moments, and journaling them in the order we LEARNED them
handed the open attempt to the reset and left the grab to open a second one.
main.build_detectors() publishes the held grab first; what is pinned here is
that the whole chain, replayed, yields one success and no reset row.

Every case plays real GameSnapshots through the REAL chain (a detector that
stopped being wired fails here), journals them the way tracking/service.py
does, and projects the result. Mutation proof: move StarGrabDetector back to
the end of build_detectors() and every case below goes red.
"""
from datetime import datetime, timedelta, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.main import build_detectors
from sm64_events.memory.addresses import (ACT_FALL_AFTER_STAR_GRAB,
                                          ACT_SPAWN_SPIN_LANDING,
                                          ACT_STAR_DANCE_EXIT)
from sm64_events.storage.db import EventRow
from sm64_events.tracking.projection import project

ACT_WALKING = 0x04000440
WF_LEVEL, WF_COURSE, STAR = 24, 2, 4  # course 2 star 4 = "Blast Away the Wall"
T0 = datetime(2026, 8, 1, 23, 53, tzinfo=timezone.utc)

RESET_FRAME = 1000     # the anchor that opens the attempt under test
GRAB_FRAME = 1400      # Mario touches the star, midair
LAND_FRAME = 1412      # ...and lands: the x-cam, where Usamune stops


def snap(frame, igt, action=ACT_WALKING, stars=5, star=STAR - 1,
         action_timer=0, timer=None):
    return GameSnapshot(
        wall_time_utc=T0 + timedelta(seconds=frame / 30),
        global_timer=frame if timer is None else timer,
        mario_action=action, mario_action_timer=action_timer,
        num_stars=stars, last_completed_course=WF_COURSE,
        last_completed_star=star, igt_overall=igt, igt_result=0,
        curr_level=WF_LEVEL, curr_area=1)


def a_grab_ending_at(last_dance_frame):
    """Snapshots for one ordinary attempt: reset, play, midair grab, land,
    then dance up to and including `last_dance_frame`."""
    frames = [snap(f, 500 + (f - 900)) for f in range(900, RESET_FRAME)]
    frames.append(snap(RESET_FRAME, 0, action=ACT_SPAWN_SPIN_LANDING))
    frames += [snap(f, f - RESET_FRAME)
               for f in range(RESET_FRAME + 1, GRAB_FRAME)]
    frames += [snap(f, f - RESET_FRAME, action=ACT_FALL_AFTER_STAR_GRAB,
                    stars=6, star=STAR, action_timer=f - GRAB_FRAME)
               for f in range(GRAB_FRAME, LAND_FRAME)]
    frames += [snap(f, f - RESET_FRAME, action=ACT_STAR_DANCE_EXIT, stars=6,
                    star=STAR, action_timer=f - LAND_FRAME)
               for f in range(LAND_FRAME, last_dance_frame + 1)]
    return frames


def practice_reset_at(frame):
    """Usamune's level reset: the overall IGT zeroes, gGlobalTimer runs on."""
    return snap(frame, 0, action=ACT_SPAWN_SPIN_LANDING, stars=6, star=STAR)


def console_reset_at(frame):
    """F1: gGlobalTimer restarts from boot, so the held grab's frame is LARGER
    than the reset's — a different epoch, not a later moment."""
    return snap(frame, 0, action=ACT_SPAWN_SPIN_LANDING, stars=6, star=STAR,
                timer=30)


def journal(snapshots):
    """Snapshots -> journal rows, through the real detector chain and the same
    broadcast-only rule tracking/service.py applies."""
    detectors, rows = build_detectors(), []
    for prev, curr in zip(snapshots, snapshots[1:]):
        for detector in detectors:
            for event in detector.process(prev, curr):
                if event.type == "stage_changed":
                    continue
                rows.append(EventRow(
                    id=len(rows) + 1, session_id=1, seq=len(rows) + 1,
                    type=event.type, frame=event.frame,
                    wall_time_utc=event.timestamp_utc.isoformat(),
                    payload=event.payload))
    return rows


def attempts_when_broken_by(breaker, at):
    return project(journal(a_grab_ending_at(at - 1) + [breaker(at)]))


def test_resetting_out_of_the_dance_records_the_grab_and_nothing_else():
    # Mid-dance, well inside the settle wait: the exact live report.
    [attempt] = attempts_when_broken_by(practice_reset_at, LAND_FRAME + 20)
    assert attempt.outcome == "success"
    assert (attempt.course_id, attempt.star_id) == (WF_COURSE, STAR - 1)


def test_resetting_before_mario_lands_records_the_grab_and_nothing_else():
    # Reset during the FALL: no x-cam ever happened, so the row is grab-timed
    # (tracking/caveats.py marks it) — but it is still one row, and a success.
    [attempt] = attempts_when_broken_by(practice_reset_at, LAND_FRAME - 4)
    assert attempt.outcome == "success"
    assert attempt.timed_at == "grab"


def test_a_console_reset_out_of_the_dance_records_the_grab_too():
    [attempt] = attempts_when_broken_by(console_reset_at, LAND_FRAME + 20)
    assert attempt.outcome == "success"


def test_the_held_grab_is_journaled_before_the_reset_that_broke_it():
    # The mechanism itself, so a failure names the cause and not the symptom.
    rows = journal(a_grab_ending_at(LAND_FRAME + 19)
                   + [practice_reset_at(LAND_FRAME + 20)])
    raced = [r.type for r in rows
             if r.type in ("star_collected", "practice_reset")]
    assert raced[-2:] == ["star_collected", "practice_reset"]


def test_an_undisturbed_grab_still_settles_and_records_one_success():
    # The control: nothing breaks the wait, so the emit lands on its own
    # deadline. One success either way — which is what makes the cases above
    # about the RESET rather than about the grab path being broken.
    [attempt] = project(journal(a_grab_ending_at(LAND_FRAME + 60)))
    assert attempt.outcome == "success"
    assert attempt.timed_at == "xcam"
