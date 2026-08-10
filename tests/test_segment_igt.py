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
from sm64_events.memory.addresses import (ACT_DISAPPEARED,
                                          ACT_INTRO_CUTSCENE,
                                          ACT_PUSHING_DOOR,
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


# A pipe LEADS somewhere, and since 2026-08-04 the detector needs that to be
# in the fixture: the touch is HELD until a level or area edge names the
# destination, because it cannot name its own (decomp -- see detectors/
# warp.py). The run therefore keeps polling through the fade and lands. The
# recorded time must be unchanged by any of it: the IGT is the TOUCH's.
FADE_FRAMES = 23                    # measured pipe fade; painting/portal is 77
LANDING_LEVEL = 30                  # the Bowser 1 arena, past the BitDW pipe


def a_run(counter=running_counter, pipe_frame=PIPE_FRAME, level=17,
          per_frame=None, landing=LANDING_LEVEL):
    """Snapshots from frame 900 through the pipe touch and the fade that
    follows it, ending on the frame the destination loads. `per_frame` maps a
    frame to extra snap() kwargs for mid-run detours; `landing=None` stops at
    the touch, for the cases that are about a run which never arrives."""
    per_frame = per_frame or {}
    frames = [snap(f, counter(f), level=level, **per_frame.get(f, {}))
              for f in range(900, pipe_frame)]
    frames.append(snap(pipe_frame, counter(pipe_frame), level=level,
                       action=ACT_DISAPPEARED))
    if landing is not None:
        frames.extend(
            snap(pipe_frame + offset, counter(pipe_frame + offset),
                 level=level if offset < FADE_FRAMES else landing,
                 action=ACT_DISAPPEARED)
            for offset in range(1, FADE_FRAMES + 1))
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


# --- a moment carries Usamune's number too (live report 2026-08-05) ---------
#
# Lakitu Skip ends on `moment_reached door_open` since his ruling that day, and
# a moment was the one boundary type carrying no time: it was absent from
# IGT_BEARING_EVENT_TYPES, so `_close` fell through to the `global_timer`
# delta. That is the number he was shown, and it is not the number on screen:
#
#   "in Usamune, that's the timer it actually displays upon opening the door"
#   -- with the emulator reading 0'07"76 as he took hold of it, and
#   "I would expect the timer to stop on door entry and the practice log entry
#    to display for the DOOR timing"
#
# This matters far more than for the pipe family: EVERY subsection begins and
# ends on a moment (that is what the type is for), so a delta-timed moment
# would have been the clock for the entire feature.

LAKITU_LEVEL = 16                   # Castle Grounds
SPAWN_FRAME = 1000                  # the frame the intro cutscene ends
COUNTER_AT_DOOR = 233               # Usamune's counter as he takes the door
# +1 because the counter drops the frame AFTER the spawn (see `counter`
# below), so this is the frame whose counter reads COUNTER_AT_DOOR.
DOOR_FRAME = SPAWN_FRAME + 1 + COUNTER_AT_DOOR
# counter + IgtClock.DISPLAY_TICK + MomentDetector.DISPLAY_LAG_FRAMES. A PIPE
# reads one lower and was live-verified there; a DOOR reads this, measured off
# a screenshot holding both numbers in one frame (2026-08-06, journal id 2279:
# Usamune 1'06"83 against our 1'06"80 on raw counter 2003). Reasoning from the
# pipe to the door is what deleted the term for a day. `moment.py` carries the
# pair; `tools/score_moment_clock.py` scores the next one.
DOOR_DISPLAY = COUNTER_AT_DOOR + 2


def seeded_lakitu_def() -> SegmentDef:
    """The SHIPPED Lakitu Skip, so this cannot pass against a corpus row that
    has stopped ending at the door."""
    seed = json.loads(bundled_defaults_seed().read_text(encoding="utf-8"))
    row = next(s for s in seed["segments"] if s["seed_key"] == "seg:lakitu-skip")
    assert row["end_triggers"][0]["type"] == "moment_reached", (
        "Lakitu Skip no longer ends on a moment -- this file is measuring "
        "something else")
    return SegmentDef(id=7, name=row["name"], enabled=True,
                      start_triggers=row["start_triggers"],
                      end_triggers=row["end_triggers"],
                      guards=row["guards"], waypoints=row["waypoints"],
                      match_mode=row.get("match_mode", "strict"))


def a_lakitu_run(door_frame=DOOR_FRAME, per_frame=None):
    """Spawn onto the grounds out of the intro cutscene, walk, take the door.

    `spawned` fires on the edge OUT of ACT_INTRO_CUTSCENE -- addresses.py calls
    that "the canonical Lakitu-skip timing start", live-verified 2026-06-12 --
    and Usamune's counter zeroes on the same load, so the counter reads
    frame - SPAWN_FRAME throughout.
    """
    per_frame = per_frame or {}

    def counter(frame):
        # A PREVIOUS run's time before the reload, then counting from the
        # spawn. The DROP is what the anchor detector reads as the reload's
        # own `practice_reset`, and in real play that anchor lands at spawn+1
        # (31 of 31 of his runs) -- which is the whole point of seeding it:
        # without it `_last_igt_zero_frame` is never set, and the segment
        # could not take Usamune's number however correct that number was.
        # The drop is one frame AFTER the action leaves the cutscene, which
        # is the shape real play has: `spawned` at 2127, the reload's own
        # `practice_reset` at 2128, in 31 of 31 of his runs. Putting them on
        # the same frame is what made the earlier version of this fixture
        # green with `IGT_ARM_SKEW_FRAMES` mutated to 0 -- a guard nobody had
        # seen fail.
        if frame <= SPAWN_FRAME:
            return 600 + (frame - (SPAWN_FRAME - 30))
        return frame - SPAWN_FRAME - 1

    def one(frame, **kwargs):
        return snap(frame, counter(frame), level=LAKITU_LEVEL,
                    **{**kwargs, **per_frame.get(frame, {})})

    frames = [one(f, action=ACT_INTRO_CUTSCENE)
              for f in range(SPAWN_FRAME - 30, SPAWN_FRAME)]
    frames += [one(f) for f in range(SPAWN_FRAME, door_frame)]
    # Two frames INSIDE the door action: the moment is the entry EDGE, so a
    # single frame would leave nothing for the next assertion to prove is not
    # a second moment.
    frames += [one(door_frame, action=ACT_PUSHING_DOOR),
               one(door_frame + 1, action=ACT_PUSHING_DOOR)]
    return frames


def test_the_door_carries_the_number_usamune_shows():
    rows = journal(a_lakitu_run())
    [moment] = [r for r in rows if r.type == "moment_reached"]
    assert moment.frame == DOOR_FRAME, "the moment is the entry EDGE"
    assert moment.payload["kind"] == "door_open"
    assert moment.payload["igt_frames"] == DOOR_DISPLAY


def test_lakitu_banks_usamunes_number_not_the_frame_delta():
    """The report, end to end: "the practice log consistently shows about one
    frame faster than the time in Usamune, every time".

    ONE thing was wrong, and a second "fix" rode along unfalsified beside it.

    THE REAL ONE: the attempt was not taking the moment's number at all.
    Lakitu arms on `spawned` and the reload's own `practice_reset` lands the
    NEXT frame, so `_close`'s "the counter zeroed on the arm frame" test missed
    by one and the `global_timer` DELTA stood. `segments.IGT_ARM_SKEW_FRAMES`.

    THE ONE THAT LOOKED LIKE IT: `MomentDetector.DISPLAY_LAG_FRAMES = 1`
    shipped in the same commit, on 16 samples all read off that same delta --
    so nothing in that round could tell WHICH of the two numbers the offset
    belonged to, and round 6 removed it. His next screenshot held Usamune and
    the recorder in ONE frame and settled it the other way: a door really does
    read `counter + 2`. Restored 2026-08-06 with the pair written into
    `moment.py`, and `tools/score_moment_clock.py` exists so the next
    disagreement is scored rather than recalled.
    """
    attempt = one_success(journal(a_lakitu_run()), seeded_lakitu_def())
    assert attempt.closed_by == "moment_reached"
    assert attempt.timed_by == "igt"
    assert attempt.rta_frames == DOOR_DISPLAY
    # WHY ONE REPORT COULD BE SATISFIED BY EITHER FIX, kept as an assertion
    # because it is the trap: a clean Lakitu run's DELTA is `counter + 1`, one
    # frame under the door's own reading, so "the log reads a frame fast" was
    # true of the delta AND of the moment, and fixing either one alone looked
    # like it had worked. `timed_by` is the discriminator this test rests on --
    # mutating `IGT_ARM_SKEW_FRAMES` to 0 flips it to "delta" and fails above.
    assert DOOR_FRAME - SPAWN_FRAME == DOOR_DISPLAY - 1


def test_a_subsection_armed_ON_the_zeroing_event_does_take_usamunes_number():
    """The case a carried IGT exists for, and the shape most subsections have:
    armed by the very anchor that zeroed the counter, so the counter measures
    exactly this segment and the door banks what the screen shows."""
    from_anchor = SegmentDef(
        id=8, name="Grounds door", enabled=True,
        start_triggers=[{"type": "attempt_anchor", "level": LAKITU_LEVEL}],
        end_triggers=[{"type": "moment_reached", "kind": "door_open"}],
        guards=[], waypoints=[], match_mode="loose")
    # The counter drops to 0 at SPAWN_FRAME, which is what the anchor detector
    # reads as a practice reset -- so the arm and the zero are one frame.
    frames = [snap(f, 600 + (f - (SPAWN_FRAME - 30)), level=LAKITU_LEVEL)
              for f in range(SPAWN_FRAME - 30, SPAWN_FRAME)]
    frames += [snap(f, f - SPAWN_FRAME, level=LAKITU_LEVEL)
               for f in range(SPAWN_FRAME, DOOR_FRAME)]
    frames += [snap(DOOR_FRAME, COUNTER_AT_DOOR, level=LAKITU_LEVEL,
                    action=ACT_PUSHING_DOOR),
               snap(DOOR_FRAME + 1, COUNTER_AT_DOOR + 1, level=LAKITU_LEVEL,
                    action=ACT_PUSHING_DOOR)]
    rows = journal(frames)
    assert any(r.type == "practice_reset" for r in rows), (
        "the fixture produced no anchor, so the claim is unreachable")
    attempt = one_success(rows, from_anchor)
    assert attempt.timed_by == "igt"
    assert attempt.rta_frames == DOOR_DISPLAY


# --- a carried IGT is not this piece's time (round 24, 2026-08-09) ----------
#
# The star clock CARRIES a leg across an area warp and adds it back
# (`IgtClock.carried_igt_at_xcam`, 2026-08-02), so a grab inside a subarea
# reports the WHOLE STAR. A subsection that armed on the spawn into that
# subarea passes `_close`'s zero-frame precondition honestly -- Usamune really
# did zero on its arm frame -- and used to bank the star's number as its own.
#
# His report, off his own screen: "it's incorrectly counting THE ENTIRE STAR
# TIME as the segment time... I would expect the timer to begin when the
# segment actually starts -- that is, it should be about ~13 seconds long."
#
# His run, from the journal, is the fixture below: armed at frame 2409018 (the
# spawn into the volcano), closed on the grab at 2409397 -- 379 frames, 12.6 s
# -- while the grab reported igt 676, which is the 296 banked before the warp
# plus this piece's own 380.

def _close_directly(*, arm_frame, close_frame, close_igt, zero_frame):
    """`SegmentEngine._close` over a hand-built arm, which is the only way to
    drive this branch without synthesising a whole subarea run."""
    from sm64_events.storage.db import Attempt
    from sm64_events.tracking import segments as seg

    engine = seg.SegmentEngine([])
    engine._last_igt_zero_frame = zero_frame
    arm = seg._Arm(jid=1, session_id=1, start_frame=arm_frame,
                   anchor_type="spawned", started_utc="2026-08-09T00:00:00Z")
    definition = seg.SegmentDef(
        id=87, name="Inside the Volcano", enabled=True,
        start_triggers=[], end_triggers=[], guards=[])
    event = EventRow(id=9, session_id=1, seq=9, type="star_collected",
                     frame=close_frame, wall_time_utc="2026-08-09T00:00:00Z",
                     payload={"igt_frames": close_igt})
    return engine._close(Attempt, definition, arm, event, "success", None)


def test_an_igt_longer_than_the_span_it_covers_is_refused():
    attempt = _close_directly(arm_frame=2409018, close_frame=2409397,
                              close_igt=676, zero_frame=2409018)
    assert attempt.rta_frames == 379, (
        "a piece must time from its own arm, not adopt the star's carried "
        "whole-run number")
    assert attempt.timed_by == "delta"


def test_an_igt_a_frame_or_two_over_the_span_is_still_taken():
    """The legitimate case, and the reason this is a slack rather than a
    strict comparison: a close event's igt runs a frame or two over the delta
    through the display tick and the arm's own poll alignment. His "Volcano
    Entry", closed by its own warp touch, reported 276 against a span of 274
    -- where the carried leg above is 297 frames over."""
    attempt = _close_directly(arm_frame=2408721, close_frame=2408995,
                              close_igt=276, zero_frame=2408721)
    assert attempt.rta_frames == 276
    assert attempt.timed_by == "igt"


def test_a_carried_prefix_is_SUBTRACTED_rather_than_refused():
    """Round 25. Round 24 refused a carried igt and fell back to the delta,
    which is honest and is not what Usamune shows: a delta counts the star
    dance and every paused frame. His screenshot holds both numbers at once --
    the emulator reading 0'13"60 while the row read 0'16"60.

    His run: the grab reported 696 (the whole star), 289 was banked when he
    warped into the volcano, and 696 - 289 = 407 against the 408 on screen --
    one display tick, the standing relationship. The delta was 498.
    """
    from sm64_events.storage.db import Attempt
    from sm64_events.tracking import segments as seg

    engine = seg.SegmentEngine([])
    ctx = seg.MatchContext(level=22, prev_level=22, num_stars=None, area=2)
    # The warp deeper into the course banks the leg it interrupts...
    engine.feed(ctx=ctx, ev=EventRow(
        id=1, session_id=1, seq=1, type="area_changed", frame=2550851,
        wall_time_utc="2026-08-09T00:00:00Z",
        payload={"level": 22, "from": 1, "to": 2, "igt_frames": 289}))
    # ...and the load's own co-frame reset must NOT wipe it (the first version
    # of this did, and measured as zero rows changed on his whole journal).
    engine.feed(ctx=ctx, ev=EventRow(
        id=2, session_id=1, seq=2, type="practice_reset", frame=2550851,
        wall_time_utc="2026-08-09T00:00:00Z", payload={}))
    assert engine._banked_before_zero == 289

    arm = seg._Arm(jid=3, session_id=1, start_frame=2550851,
                   anchor_type="spawned", started_utc="2026-08-09T00:00:00Z")
    definition = seg.SegmentDef(
        id=87, name="Inside the Volcano", enabled=True,
        start_triggers=[], end_triggers=[], guards=[])
    grab = EventRow(id=4, session_id=1, seq=4, type="star_collected",
                    frame=2551349, wall_time_utc="2026-08-09T00:00:00Z",
                    payload={"igt_frames": 696})
    attempt = engine._close(Attempt, definition, arm, grab, "success", None)
    # 696 - 289 + DISPLAY_TICK = 408, exactly the number on his screen.
    #
    # THE TICK IS NOT A FUDGE. Both numbers already carry it, so a bare
    # subtraction removes it twice and lands one frame -- 0.03 s -- under
    # Usamune. He reported that separately, with two more samples: 12"93 on
    # the emulator against 12"90 in the log, and 24"76 against 24"73 --
    # "Looks like we're one frame too early?". Three independent readings,
    # every one exactly one frame, which is why this is a named constant.
    assert attempt.rta_frames == 408, "the piece's own portion of the star"
    assert attempt.timed_by == "igt"


def test_a_reset_INSIDE_the_subarea_banks_nothing():
    """The half he reported as already correct, and the one that must not
    move: a run begun by resetting inside the subarea has no prefix to
    subtract, so its close is timed exactly as before."""
    from sm64_events.tracking import segments as seg

    engine = seg.SegmentEngine([])
    ctx = seg.MatchContext(level=22, prev_level=22, num_stars=None, area=2)
    engine.feed(ctx=ctx, ev=EventRow(
        id=1, session_id=1, seq=1, type="area_changed", frame=2550851,
        wall_time_utc="2026-08-09T00:00:00Z",
        payload={"level": 22, "from": 1, "to": 2, "igt_frames": 289}))
    engine.feed(ctx=ctx, ev=EventRow(   # a LATER reset -- his, not the load's
        id=2, session_id=1, seq=2, type="practice_reset", frame=2551000,
        wall_time_utc="2026-08-09T00:00:00Z", payload={}))
    assert engine._banked_before_zero == 0
