"""A finish Mario reaches already dead is a death, never a success.

His report, 2026-09-28: "it's possible for Mario to die AND THEN grab a star /
exit a stage successfully. BUT MARIO IS DEAD, so technically we didn't
actually succeed ... when we grab the star / complete a segment, we need to
confirm that mario is alive." The row was BitFS "No Reds" #2, 0'48"26: a lava
boost ran out of health at frame 76972 (the game pended its death warp, no
death action ran), Mario flew on into the pipe at 77012, and the death warp
carried him to the castle at 77022 — read off the ghost pose archive.

`detectors/death.py::mario_alive` is the one definition; the success
detectors stamp it at the success frame and the tracking layer turns a dead
finish into a death. Journal replays below exercise the gate on its own; the
real-chain runs play GameSnapshots through `main.build_detectors` and the
SHIPPED BitFS pipe definition, so an unwired detector fails here too.
"""
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from sm64_events.core.paths import bundled_defaults_seed
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.death import ALIVE_KEY, FINISHED_DEAD, mario_alive
from sm64_events.detectors.key import KeyGrabDetector
from sm64_events.detectors.star_grab import StarGrabDetector
from sm64_events.detectors.warp import WarpDetector
from sm64_events.main import build_detectors
from sm64_events.memory.addresses import (ACT_DISAPPEARED, ACT_LAVA_BOOST,
                                          ACT_STAR_DANCE_EXIT, WARP_OP_DEATH,
                                          WARP_OP_WARP_FLOOR,
                                          WARP_OP_WARP_OBJECT)
from sm64_events.storage.db import EventRow
from sm64_events.tracking.projection import project
from sm64_events.tracking.segments import SegmentDef, SegmentEngine, MatchContext

T0 = datetime(2026, 9, 28, 23, 0, tzinfo=timezone.utc)
ACT_IDLE = 0x0C400201
ACT_WALKING = 0x04000440
ACT_BURNING_JUMP = 0x010208B4
ACT_QUICKSAND_DEATH = 0x00021312
ACT_SPECIAL_DEATH_EXIT = 0x0000192C
DEAD = 0xFF            # health the game clamps a dead Mario to


def snap(**overrides) -> GameSnapshot:
    defaults = dict(wall_time_utc=T0, global_timer=1000,
                    mario_action=ACT_IDLE, mario_action_timer=0, num_stars=8,
                    last_completed_course=1, last_completed_star=1,
                    curr_level=19, curr_area=1)
    defaults.update(overrides)
    return GameSnapshot(**defaults)


def jev(id, type, frame, payload=None):
    return EventRow(id=id, session_id=1, seq=id, type=type, frame=frame,
                    wall_time_utc=T0.isoformat(), payload=payload or {})


# -- the definition --------------------------------------------------------

@pytest.mark.parametrize("fields,alive", [
    ({}, True),                                           # full health
    ({"mario_health": 0x100}, True),                      # one wedge left
    ({"mario_health": DEAD}, False),                      # the game's own test
    ({"mario_health": 0xF6}, False),                      # burning ground's floor
    ({"mario_health": 0}, True),                          # no file loaded yet
    ({"mario_action": ACT_QUICKSAND_DEATH}, False),       # full health, dying
    ({"pending_warp_op": WARP_OP_WARP_FLOOR}, False),     # void-out
    ({"pending_warp_op": WARP_OP_DEATH}, False),          # lava boost, OOB
    ({"pending_warp_op": WARP_OP_WARP_OBJECT}, True),     # a live pipe ride
])
def test_mario_alive_reads_every_mark_the_game_uses_for_dead(fields, alive):
    assert mario_alive(snap(**fields)) is alive


# -- the success detectors stamp it at the success frame -------------------

def test_the_pipe_touch_carries_whether_mario_was_alive_when_he_touched_it():
    detector = WarpDetector()
    riding_a_death = snap(mario_action=ACT_LAVA_BOOST, mario_health=DEAD,
                          pending_warp_op=WARP_OP_DEATH)
    touch = replace(riding_a_death, mario_action=ACT_DISAPPEARED,
                    global_timer=1040)
    assert detector.process(riding_a_death, touch) == []   # held
    [ev] = detector.process(touch, replace(touch, global_timer=1050,
                                           curr_level=6, curr_area=3,
                                           pending_warp_op=0))
    assert ev.type == "warp_entered" and ev.payload[ALIVE_KEY] is False


def test_a_live_pipe_touch_says_so():
    detector = WarpDetector()
    touch = snap(mario_action=ACT_DISAPPEARED, global_timer=1001)
    detector.process(snap(), touch)
    [ev] = detector.process(touch, replace(touch, global_timer=1024,
                                           curr_level=33))
    assert ev.payload[ALIVE_KEY] is True


@pytest.mark.parametrize("fields,alive", [
    ({}, True), ({"pending_warp_op": WARP_OP_WARP_FLOOR}, False)])
def test_a_star_grab_carries_whether_mario_was_alive_at_the_touch(fields,
                                                                   alive):
    # A ground grab, then the dance running past Usamune's settle window so
    # the held grab publishes. The void-out is pending AT the touch only.
    detector, events = StarGrabDetector(), []
    touch = snap(curr_level=24, mario_action=ACT_STAR_DANCE_EXIT,
                 global_timer=1001, igt_overall=231, igt_result=231, **fields)
    stream = [snap(curr_level=24, global_timer=1000, igt_overall=230), touch,
              *(replace(touch, global_timer=f, igt_overall=231 + f - 1001,
                        pending_warp_op=0) for f in range(1002, 1060))]
    for prev, curr in zip(stream, stream[1:], strict=False):
        events.extend(detector.process(prev, curr))
    [ev] = [e for e in events if e.type == "star_collected"]
    assert ev.payload[ALIVE_KEY] is alive


@pytest.mark.parametrize("fields,alive", [
    ({}, True), ({"pending_warp_op": WARP_OP_WARP_FLOOR}, False)])
def test_a_key_grab_carries_whether_mario_was_alive(fields, alive):
    [ev] = KeyGrabDetector().process(
        snap(curr_level=30),
        snap(curr_level=30, mario_action=ACT_STAR_DANCE_EXIT, **fields))
    assert ev.payload[ALIVE_KEY] is alive


# -- the tracking gate, replayed from journal rows -------------------------

PIPE = SegmentDef(id=6, name="BitFS Pipe Entry", enabled=True,
                  start_triggers=[{"type": "level_enter", "to": 19},
                                  {"type": "attempt_anchor", "level": 19}],
                  end_triggers=[{"type": "warp_entered", "level": 19}],
                  waypoints=[], guards=[], match_mode="exclusive")
B2 = SegmentDef(id=9, name="Bowser 2", enabled=True,
                start_triggers=[{"type": "level_enter", "to": 33}],
                end_triggers=[{"type": "key_grabbed", "level": 33}],
                waypoints=[], guards=[])


def ctx(level, prev_level=None):
    return MatchContext(level=level, prev_level=prev_level, num_stars=None,
                        area=None)


def pipe_close(alive):
    e = SegmentEngine([PIPE])
    e.feed(jev(1, "level_changed", 1000, {"from": 6, "to": 19}),
           ctx(19, prev_level=6))
    touch = {"level": 19, "area": 1, "action": ACT_DISAPPEARED}
    if alive is not None:
        touch[ALIVE_KEY] = alive
    closed, _ = e.feed(jev(2, "warp_entered", 2448, touch), ctx(19))
    return [(a.outcome, a.outcome_detail) for a in closed]


def test_a_dead_pipe_touch_records_a_death_not_a_success():
    assert pipe_close(alive=False) == [("death", FINISHED_DEAD)]


@pytest.mark.parametrize("alive", [True, None])   # None: a pre-gate journal
def test_a_live_or_legacy_pipe_touch_is_still_a_success(alive):
    assert pipe_close(alive) == [("success", None)]


def test_a_dead_key_grab_ends_the_fight_as_a_death():
    e = SegmentEngine([B2])
    e.feed(jev(1, "level_changed", 1000, {"from": 19, "to": 33}),
           ctx(33, prev_level=19))
    closed, _ = e.feed(jev(2, "key_grabbed", 1900,
                           {"level": 33, "which": "bitfs", ALIVE_KEY: False}),
                       ctx(33))
    assert [(a.outcome, a.outcome_detail) for a in closed] == \
        [("death", FINISHED_DEAD)]


def star(id, frame, alive):
    return jev(id, "star_collected", frame,
               {"course_id": 2, "star_id": 2, "igt_frames": 343,
                ALIVE_KEY: alive})


def test_a_dead_star_grab_records_a_death_not_a_success():
    [a] = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0,
                                        "mario_acted": True}),
        star(2, 1350, alive=False),
    ])
    assert (a.outcome, a.outcome_detail) == ("death", FINISHED_DEAD)
    assert a.igt_frames == 343      # the star's own whole-course time


def test_a_dead_grab_after_its_death_event_is_one_death_not_two():
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0,
                                        "mario_acted": True}),
        jev(2, "death", 1300, {"cause": "fall", "igt_frames": 290,
                               "level": 9}),
        star(3, 1310, alive=False),
    ])
    assert [(a.outcome, a.outcome_detail) for a in attempts] == \
        [("death", "fall")]


# -- the real chain: his BitFS run, and the one only health can see --------

def shipped_bitfs_pipe() -> SegmentDef:
    seed = json.loads(bundled_defaults_seed().read_text(encoding="utf-8"))
    row = next(s for s in seed["segments"] if s["seed_key"] == "seg:bitfs-pipe")
    return SegmentDef(id=6, name=row["name"], enabled=True,
                      start_triggers=row["start_triggers"],
                      end_triggers=row["end_triggers"], guards=row["guards"],
                      waypoints=row["waypoints"],
                      match_mode=row.get("match_mode", "strict"))


ZERO = 1000          # Usamune's counter zeroes: a practice reset in BitFS


def run(per_frame, pipe_frame, landing_level, landing_area=1, after=None):
    """BitFS from a practice reset through a pipe touch and the level that
    loads 10 frames later. `per_frame(f)` returns the snapshot fields that
    frame differs by; `after` the fields once the new level has loaded."""
    frames = []
    for f in range(900, pipe_frame + 11):
        fields = dict(global_timer=f,
                      wall_time_utc=T0 + timedelta(seconds=f / 30),
                      igt_overall=(600 + f - 900) if f < ZERO else f - ZERO,
                      mario_action=ACT_WALKING if f < ZERO + 5 else ACT_IDLE)
        fields.update(per_frame(f))
        if f >= pipe_frame:
            fields["mario_action"] = ACT_DISAPPEARED
        if f >= pipe_frame + 10:
            fields.update(curr_level=landing_level, curr_area=landing_area,
                          **(after or {}))
        frames.append(snap(**fields))
    # He had the segment selected: a failure records only against the
    # target (tracking-storage rule), a success always does.
    rows = [EventRow(id=1, session_id=1, seq=1, type="target_set", frame=0,
                     wall_time_utc=T0.isoformat(),
                     payload={"kind": "segment", "segment_id": 6})]
    detectors = build_detectors()
    for prev, curr in zip(frames, frames[1:], strict=False):
        for detector in detectors:
            for event in detector.process(prev, curr):
                if event.type != "stage_changed":
                    rows.append(EventRow(
                        id=len(rows) + 1, session_id=1, seq=len(rows) + 1,
                        type=event.type, frame=event.frame,
                        wall_time_utc=event.timestamp_utc.isoformat(),
                        payload=event.payload))
    return [(a.outcome, a.outcome_detail)
            for a in project(rows, segments=[shipped_bitfs_pipe()])
            if a.segment_id is not None]


def test_his_bitfs_run_a_lava_death_then_the_pipe_is_a_lava_death():
    lava, died, pipe = ZERO + 900, ZERO + 966, ZERO + 1006

    def frame(f):
        if f < lava:
            return {}
        return dict(mario_action=ACT_LAVA_BOOST,
                    mario_health=DEAD if f >= died else 0x300,
                    pending_warp_op=WARP_OP_DEATH if f >= died else 0)

    assert run(frame, pipe, landing_level=6, landing_area=3,
               after=dict(mario_action=ACT_SPECIAL_DEATH_EXIT,
                          mario_health=0x100, pending_warp_op=0)) == \
        [("death", "lava")]


def test_a_burning_jump_into_the_pipe_is_a_death_only_health_can_see():
    # Hit to zero in the air: no death action runs until he lands, no death
    # warp is pending, and interact_warp has no health check — the game
    # really does take him to Bowser 2, where he dies on arrival.
    burned, pipe = ZERO + 900, ZERO + 930

    def frame(f):
        if f < burned:
            return {}
        return dict(mario_action=ACT_BURNING_JUMP, mario_health=DEAD,
                    pending_warp_op=WARP_OP_WARP_OBJECT if f > pipe + 1 else 0)

    assert run(frame, pipe, landing_level=33) == [("death", FINISHED_DEAD)]


def test_a_live_bitfs_pipe_is_still_a_success():
    assert run(lambda f: {}, ZERO + 1000, landing_level=33) == \
        [("success", None)]
