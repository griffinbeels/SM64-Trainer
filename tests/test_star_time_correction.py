"""A star's row appears at the x-cam; Usamune may revise its time afterwards.

Live report 2026-08-01: "I am standing under the star… doing a backflip
through it… when I land, there's STILL a ton of delay after that… now the
tool feels like it's broken and laggy… we HAVE THE ANSWER RIGHT WHEN THE STAR
DANCE HAPPENS."

He is right for every star but one shape. `USAMUNE_OVERALL` is subarea-local,
so on a multi-area star only Usamune's own late whole-star write knows the
real time — which is what the 45-frame wait was buying, on every grab, to
protect two grabs in eleven. The emit is an optimistic update now: publish as
soon as Usamune answers (0-12 frames), keep watching, and journal
`star_time_corrected` if the answer changes. This file is the whole path —
detector, pairing, projection, and the live re-projection that makes a
corrected row real.
"""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone

from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.memory import addresses as A
from sm64_events.detectors.star_grab import StarGrabDetector
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.storage.db import Database, EventRow
from sm64_events.tracking.projection import project, time_corrections
from sm64_events.tracking.segments import SegmentDef
from sm64_events.tracking.service import TrackerService

ACT_IDLE = 0x0C400201     # any non-star-dance action works here
W = "2026-08-01T12:00:00Z"
T0 = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
GRAB_FRAME, XCAM_FRAME = 1000, 1003
INSIDE_THE_PYRAMID, THE_WHOLE_STAR = 72, 574   # his own SSL numbers, in frames


def jev(id, type, frame, payload=None):
    return EventRow(id=id, session_id=1, seq=id, type=type, frame=frame,
                    wall_time_utc=W, payload=payload or {})


def grab(id, frame=XCAM_FRAME, course=8, star_id=2, igt=INSIDE_THE_PYRAMID):
    return jev(id, "star_collected", frame,
               {"course_id": course, "star_id": star_id, "igt_frames": igt,
                "igt_source": "counter", "grab_frame": GRAB_FRAME,
                "igt_timed_at": "xcam"})


def correction(id, frame=XCAM_FRAME, course=8, star_id=2,
               igt=THE_WHOLE_STAR, grab_frame=GRAB_FRAME):
    return jev(id, "star_time_corrected", frame,
               {"course_id": course, "star_id": star_id,
                "grab_frame": grab_frame, "igt_frames": igt,
                "igt": "0'19\"13", "igt_source": "result",
                "igt_reconstructed": False})


# --- the detector: two events, one grab -------------------------------------

def snap(frame, counter, action=A.ACT_STAR_DANCE_EXIT, timer=0, result=452):
    return GameSnapshot(
        wall_time_utc=T0, global_timer=frame, mario_action=action,
        mario_action_timer=timer, num_stars=6, last_completed_course=8,
        last_completed_star=3, igt_overall=counter, igt_result=result,
        curr_level=8, curr_area=2)


def a_subarea_grab(write_at=30, written=THE_WHOLE_STAR):
    """A multi-area star: our counter measures the pyramid, Usamune's late
    write measures the whole star. `write_at` is frames past the touch."""
    counter = INSIDE_THE_PYRAMID - 4        # +3 fall, +1 display tick
    snaps = [snap(GRAB_FRAME - 1, counter - 1, action=ACT_IDLE)]
    for offset in range(3):
        snaps.append(snap(GRAB_FRAME + offset, counter + offset,
                          action=A.ACT_FALL_AFTER_STAR_GRAB, timer=offset))
    for offset in range(3, StarGrabDetector.RESULT_SETTLE_FRAMES + 5):
        snaps.append(snap(GRAB_FRAME + offset, counter + offset, timer=offset - 3,
                          result=written if offset >= write_at else 452))
    return snaps


def detected(snaps):
    detector = StarGrabDetector()
    return [ev for prev, curr in zip(snaps, snaps[1:])
            for ev in detector.process(prev, curr)]


def test_the_detector_publishes_early_and_corrects_late():
    row, fix = detected(a_subarea_grab())
    assert (row.type, row.payload["igt_frames"]) == ("star_collected",
                                                     INSIDE_THE_PYRAMID)
    assert (fix.type, fix.payload["igt_frames"]) == ("star_time_corrected",
                                                     THE_WHOLE_STAR)


def test_a_write_that_says_what_we_published_produces_no_correction():
    [row] = detected(a_subarea_grab(write_at=4, written=INSIDE_THE_PYRAMID))
    assert row.type == "star_collected"


# --- the pairing: a correction belongs to ONE grab --------------------------

def test_a_correction_is_paired_with_the_grab_before_it():
    assert time_corrections([grab(1), correction(2)])[1]["igt_frames"] \
        == THE_WHOLE_STAR


def test_a_correction_naming_another_grab_is_dropped_not_applied():
    # Fail closed: a journal where the pairing is not what the detector meant
    # must lose the correction, never move a different star's time.
    assert time_corrections([grab(1), correction(2, grab_frame=999)]) == {}
    assert time_corrections([grab(1), correction(2, star_id=5)]) == {}


# --- the row: one number, wherever it is read -------------------------------

def test_the_attempt_records_the_corrected_time():
    [attempt] = project([
        jev(1, "practice_reset", 900, {"igt_frames_before": 400,
                                       "mario_acted": True}),
        grab(2),
        correction(3),
    ])
    assert attempt.outcome == "success"
    assert attempt.igt_frames == THE_WHOLE_STAR
    assert attempt.timed_at == "xcam"      # the correction moves the NUMBER only


def test_an_uncorrected_grab_is_untouched():
    [attempt] = project([
        jev(1, "practice_reset", 900, {"igt_frames_before": 400,
                                       "mario_acted": True}),
        grab(2),
    ])
    assert attempt.igt_frames == INSIDE_THE_PYRAMID


def test_the_hundred_coin_row_closed_by_the_same_grab_is_corrected_too():
    # The 100-coin star IS the segment, and its time is stamped from the
    # CLOSING grab's payload — so it is the second reader of the number a
    # correction changes, and the reason the fold-in happens at the event
    # rather than at the attempt (projection.py caveat 19).
    hundred_coin = SegmentDef(
        id=100, name="course 8 100 Coins -> Exit", enabled=True,
        start_triggers=[{"type": "level_enter", "to": 8},
                        {"type": "attempt_anchor", "level": 8}],
        end_triggers=[{"type": "star_grabbed", "course": 8, "star": s}
                      for s in range(6)],
        waypoints=[[{"type": "star_grabbed", "course": 8, "star": 6}]],
        guards=[], match_mode="strict")
    attempts = project([
        jev(1, "level_changed", 900, {"from": 16, "to": 8}),
        jev(2, "star_collected", 950, {"course_id": 8, "star_id": 6,
                                       "igt_frames": 40}),
        grab(3),
        correction(4),
    ], segments=[hundred_coin])
    hundred = next(a for a in attempts if (a.course_id, a.star_id) == (8, 6))
    exit_star = next(a for a in attempts if (a.course_id, a.star_id) == (8, 2))
    assert hundred.igt_frames == THE_WHOLE_STAR == exit_star.igt_frames


# --- live: a correction is what makes the recorded row change ---------------

def test_the_service_reprojects_so_the_stored_row_carries_the_new_time(tmp_path):
    db = Database(tmp_path / "t.db")
    svc = TrackerService(db, Broadcaster())
    asyncio.run(svc.start())

    def publish(row):
        asyncio.run(svc.publish(Event(type=row.type, frame=row.frame,
                                      timestamp_utc=T0, payload=row.payload)))

    publish(jev(0, "practice_reset", 900, {"igt_frames_before": 400,
                                           "mario_acted": True}))
    publish(grab(0))
    [before] = [a for a in db.attempts() if a.outcome == "success"]
    assert before.igt_frames == INSIDE_THE_PYRAMID   # what he saw immediately
    publish(correction(0))
    [after] = [a for a in db.attempts() if a.outcome == "success"]
    assert after.id == before.id                     # the SAME row, revised
    assert after.igt_frames == THE_WHOLE_STAR
