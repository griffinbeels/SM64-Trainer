"""A segment's recorded time is Usamune's IGT, not a global_timer delta.

Live report 2026-07-31: BitDW "No Reds" displayed 0'35"90 where Usamune
showed 0'35"96 (journal ids 23044-23061, attempt 50000023044, rta 1077).
The two frames are NOT a constant to subtract -- see the WHY THE DELTA IS NOT
THE IGT paragraph in tracking/segments.py's module docstring for the
measurement over 626 real star attempts.

Every case here plays real GameSnapshots through the REAL detector chain
(main.build_detectors -- the list the shipped app runs, so a detector that
stopped being wired fails here), journals what comes out exactly as
tracking/service.py does, and projects it with the SHIPPED pipe definitions
read out of data/defaults.seed.json. Nothing reaches the matcher by a route
the product does not use.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from sm64_events.core.paths import bundled_defaults_seed
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.core.timefmt import format_igt
from sm64_events.main import build_detectors
from sm64_events.memory.addresses import (ACT_DISAPPEARED, ACT_PUSHING_DOOR,
                                          ACT_STAR_DANCE_EXIT)
from sm64_events.storage.db import EventRow
from sm64_events.tracking.projection import project
from sm64_events.tracking.segments import SegmentDef

ACT_WALKING = 0x04000440
T0 = datetime(2026, 7, 31, 22, 28, tzinfo=timezone.utc)

# The live report's own numbers. Usamune's counter zeroed on frame ZERO_FRAME;
# the first 60 Hz poll to see the drop landed one game frame later, which is
# the frame the practice_reset carries and therefore the frame the segment
# arms on. The pipe is touched with Usamune's counter reading COUNTER_AT_PIPE.
ZERO_FRAME = 1000
ARM_FRAME = ZERO_FRAME + 1          # the earliest frame a detector can observe
COUNTER_AT_PIPE = 1078
PIPE_FRAME = ZERO_FRAME + COUNTER_AT_PIPE
USAMUNE_DISPLAY = 1079              # counter + IgtClock.DISPLAY_TICK
FRAME_DELTA = PIPE_FRAME - ARM_FRAME    # 1077, the number the report called wrong

PIPE_LEVELS = {"seg:bitdw-pipe": 17, "seg:bitfs-pipe": 19, "seg:bits-pipe": 21}


def seeded_pipe_def(seed_key="seg:bitdw-pipe", **overrides) -> SegmentDef:
    """The SHIPPED definition, so this file cannot pass against a corpus row
    that no longer has this shape."""
    seed = json.loads(bundled_defaults_seed().read_text(encoding="utf-8"))
    row = next(s for s in seed["segments"] if s["seed_key"] == seed_key)
    fields = dict(id=5, name=row["name"], enabled=True,
                  start_triggers=row["start_triggers"],
                  end_triggers=row["end_triggers"],
                  guards=row["guards"], waypoints=row["waypoints"],
                  match_mode=row.get("match_mode", "strict"))
    fields.update(overrides)
    return SegmentDef(**fields)


def snap(frame, counter, level=17, action=ACT_WALKING, area=1, result=0,
         course=1, star=1):
    return GameSnapshot(
        wall_time_utc=T0 + timedelta(seconds=frame / 30),
        global_timer=frame, mario_action=action, mario_action_timer=0,
        num_stars=8, last_completed_course=course, last_completed_star=star,
        igt_overall=counter, igt_result=result, curr_level=level, curr_area=1)


def running_counter(frame):
    """Usamune's overall counter for an uneventful practice reset at
    ZERO_FRAME: a previous attempt's time before, then counting up."""
    return 600 + (frame - 900) if frame <= ZERO_FRAME else frame - ZERO_FRAME


def a_run(counter=running_counter, pipe_frame=PIPE_FRAME, level=17,
          per_frame=None):
    """Snapshots from frame 900 up to and including the pipe touch.
    `per_frame` maps a frame to extra snap() kwargs for mid-run detours."""
    per_frame = per_frame or {}
    frames = [snap(f, counter(f), level=level, **per_frame.get(f, {}))
              for f in range(900, pipe_frame)]
    frames.append(snap(pipe_frame, counter(pipe_frame), level=level,
                       action=ACT_DISAPPEARED))
    return frames


def journal(snapshots):
    """Snapshots -> journal rows, through the real detector chain and the same
    broadcast-only rule tracking/service.py applies (stage_changed is never
    journaled)."""
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


def one_success(rows, definition):
    [attempt] = [a for a in project(rows, segments=[definition])
                 if a.segment_id is not None and a.outcome == "success"]
    return attempt


def test_the_pipe_touch_carries_usamunes_igt():
    warp = next(r for r in journal(a_run()) if r.type == "warp_entered")
    assert warp.payload["igt_frames"] == USAMUNE_DISPLAY
    assert warp.payload["igt"] == '0\'35"96'
    # counter, never "result": Usamune writes its result store on a STAR grab
    # and a pipe touch is not one.
    assert warp.payload["igt_source"] == "counter"


def test_the_live_reported_run_now_records_what_usamune_showed():
    attempt = one_success(journal(a_run()), seeded_pipe_def())
    assert attempt.anchor_frame == ARM_FRAME       # armed where the report did
    assert attempt.rta_frames == USAMUNE_DISPLAY
    assert format_igt(attempt.rta_frames) == '0\'35"96'
    assert format_igt(FRAME_DELTA) == '0\'35"90'   # what it used to record


def test_a_pause_mid_run_is_not_counted():
    """Usamune's counter stops while the game is paused; a frame delta cannot.
    120 held frames must not move the recorded time at all."""
    held = 120

    def paused(frame):
        """Frozen for `held` frames once the counter reaches 500 -- the pause
        menu, mid-run, well after the reset the segment arms on."""
        if frame <= ZERO_FRAME:
            return running_counter(frame)
        counter = frame - ZERO_FRAME
        return counter if counter <= 500 else max(500, counter - held)

    pipe_frame = PIPE_FRAME + held
    assert paused(pipe_frame) == COUNTER_AT_PIPE
    attempt = one_success(journal(a_run(paused, pipe_frame)), seeded_pipe_def())
    assert attempt.rta_frames == USAMUNE_DISPLAY            # unmoved
    assert pipe_frame - ARM_FRAME == FRAME_DELTA + held     # the delta moved


def test_a_door_crossed_mid_run_falls_back_to_the_frame_delta():
    """Usamune re-zeroes on a door, and the matcher deliberately ignores that
    anchor as an echo -- so the counter at the pipe measures from the DOOR,
    not from the segment start, and must not be believed. The fallback spans
    the right two moments even though it counts paused frames."""
    door_frame = 1500

    def with_door(frame):
        if frame <= ZERO_FRAME:
            return running_counter(frame)
        return frame - ZERO_FRAME if frame < door_frame else frame - door_frame

    rows = journal(a_run(with_door, per_frame={
        door_frame - 1: {"action": ACT_PUSHING_DOOR},
        door_frame: {"action": ACT_PUSHING_DOOR}}))
    assert any(r.type == "practice_reset" and r.frame == door_frame
               for r in rows), "the door's IGT reset must be journaled"
    attempt = one_success(rows, seeded_pipe_def())
    assert attempt.anchor_frame == ARM_FRAME       # the door echo did not re-arm
    assert attempt.rta_frames == FRAME_DELTA       # NOT the since-the-door counter
    assert attempt.rta_frames != PIPE_FRAME - door_frame + 1


def test_a_segment_armed_mid_level_falls_back_to_the_frame_delta():
    """Usamune's counter measures from the last load. A segment that starts on
    something else -- here a star grab, an idiom the corpus really uses -- would
    otherwise bank the whole since-load time as its own."""
    grab_frame = 1400
    dance = {f: {"action": ACT_STAR_DANCE_EXIT, "course": 16, "star": 1}
             for f in range(grab_frame, grab_frame + 40)}
    dance.update({f: {"course": 16, "star": 1, "result": grab_frame - ZERO_FRAME}
                  for f in range(grab_frame + 40, PIPE_FRAME + 1)})
    rows = journal(a_run(per_frame=dance))
    grab = next(r for r in rows if r.type == "star_collected")
    assert (grab.payload["course_id"], grab.payload["star_id"]) == (16, 0)
    attempt = one_success(rows, seeded_pipe_def(
        start_triggers=[{"type": "star_grabbed", "course": 16, "star": 0}],
        match_mode="strict"))
    assert attempt.anchor_frame == grab.frame
    assert attempt.rta_frames == PIPE_FRAME - grab.frame
    warp = next(r for r in rows if r.type == "warp_entered")
    assert warp.payload["igt_frames"] > attempt.rta_frames, (
        "the payload igt counts from the level load, which is the whole point")


def test_a_stale_star_result_does_not_hijack_the_pipes_time():
    """A reds->pipe run grabs a star and THEN walks to the pipe. Usamune's
    result store still holds that star's time; IgtClock's freshness rule is
    what keeps the pipe on the running counter."""
    rows = journal(a_run(per_frame={f: {"result": 812}
                                    for f in range(ARM_FRAME + 1, PIPE_FRAME + 1)}))
    warp = next(r for r in rows if r.type == "warp_entered")
    assert warp.payload["igt_source"] == "counter"
    assert warp.payload["igt_frames"] == USAMUNE_DISPLAY


@pytest.mark.parametrize("seed_key", sorted(PIPE_LEVELS))
def test_every_shipped_pipe_segment_gets_the_igt(seed_key):
    """The report named BitDW; the other two are the same definition, and a
    fix that reached only the one reported is the asymmetry rule 11 is about."""
    level = PIPE_LEVELS[seed_key]
    attempt = one_success(journal(a_run(level=level)),
                          seeded_pipe_def(seed_key))
    assert attempt.rta_frames == USAMUNE_DISPLAY


# -- how the time was measured, recorded on the row (ruling 6, round 3) -------
#
# A PB set before warp.py carried Usamune's IGT stands on a wall-frame delta
# and runs ~1-2 frames CHEAP, so an identical run cannot beat it. Those rows
# cannot be backfilled (the raw counter at those frames was never journaled),
# so the ruling is that they stand and are MARKED -- and the mark is DERIVED
# from the journal on every reproject rather than migrated as a list of ids.


def test_a_time_taken_from_usamune_says_so():
    attempt = one_success(journal(a_run()), seeded_pipe_def())
    assert attempt.rta_frames == USAMUNE_DISPLAY
    assert attempt.timed_by == "igt"
    assert attempt.closed_by == "warp_entered"


def test_a_time_that_fell_back_to_the_delta_says_so():
    """Same shape as the armed-mid-level fallback above, asserting the RECORD
    of which branch ran rather than the number it produced. Nothing downstream
    can reconstruct this: both branches write the same rta_frames field."""
    grab_frame = 1400
    dance = {f: {"action": ACT_STAR_DANCE_EXIT, "course": 16, "star": 1}
             for f in range(grab_frame, grab_frame + 40)}
    dance.update({f: {"course": 16, "star": 1, "result": grab_frame - ZERO_FRAME}
                  for f in range(grab_frame + 40, PIPE_FRAME + 1)})
    attempt = one_success(journal(a_run(per_frame=dance)), seeded_pipe_def(
        start_triggers=[{"type": "star_grabbed", "course": 16, "star": 0}],
        match_mode="strict"))
    assert attempt.rta_frames == PIPE_FRAME - grab_frame
    assert attempt.timed_by == "delta"
    assert attempt.closed_by == "warp_entered"


def test_an_old_journal_entry_is_delta_timed_and_a_new_one_is_not():
    """THE fact ruling 6 marks, end to end. The only difference between the
    two runs is whether the journaled `warp_entered` carries `igt_frames` --
    which is exactly what separates a pipe attempt recorded before 2026-07-31
    from one recorded after, since a stored payload never gains a key."""
    rows = journal(a_run())
    fresh = one_success(rows, seeded_pipe_def())

    old = []
    for row in rows:      # strip the key the way history simply lacks it
        payload = dict(row.payload)
        payload.pop("igt_frames", None)
        old.append(EventRow(id=row.id, session_id=row.session_id, seq=row.seq,
                            type=row.type, frame=row.frame,
                            wall_time_utc=row.wall_time_utc, payload=payload))
    stale = one_success(old, seeded_pipe_def())

    assert (fresh.timed_by, stale.timed_by) == ("igt", "delta")
    assert fresh.closed_by == stale.closed_by == "warp_entered"
    # and the reason it matters: the old row is CHEAPER, so an identical run
    # timed the new way does not beat it.
    assert stale.rta_frames < fresh.rta_frames


def test_only_a_closing_event_that_could_have_carried_igt_is_comparable():
    """The discriminator, and the reason `closed_by` is recorded beside
    `timed_by`. Measured over the 2026-07-31 journal, 570 of 626 segment
    attempts are delta-timed -- but most are delta FOREVER: a movement closing
    on a `level_changed` has no Usamune number to be given, so its delta is
    simply how that segment is measured and stays comparable to the next run
    of it. Marking every delta row would have marked 18 of 23 saved segment
    PBs, including every castle movement; this rule marks 10, which is the
    pipe family plus one older Bowser 3 row that no hand-written list named."""
    from sm64_events.core.events import IGT_BEARING_EVENT_TYPES

    def is_marked(attempt):
        return (attempt.timed_by == "delta"
                and attempt.closed_by in IGT_BEARING_EVENT_TYPES)

    rows = journal(a_run())
    assert not is_marked(one_success(rows, seeded_pipe_def()))

    old = [EventRow(id=r.id, session_id=r.session_id, seq=r.seq, type=r.type,
                    frame=r.frame, wall_time_utc=r.wall_time_utc,
                    payload={k: v for k, v in r.payload.items()
                             if k != "igt_frames"})
           for r in rows]
    assert is_marked(one_success(old, seeded_pipe_def()))

    # A `level_changed` closure is delta for a reason that will never change,
    # so it is NOT marked however old it is.
    assert "level_changed" not in IGT_BEARING_EVENT_TYPES
