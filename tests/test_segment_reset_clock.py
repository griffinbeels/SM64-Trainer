"""A retry owns a new clock; its counter measures only the previous run.

The LBLJ sample is the 2026-10-01 session-5 journal: the row starting at
4224 and closing at 4319 recorded 711 frames although that reset carried
53 counter frames. The 95 global frames likely include a frozen door-spawn
interval; raw snapshots were not captured, so that mechanism is not proven.
Definitions below are reference fixtures, independent of shipping defaults.
"""
from dataclasses import replace

import pytest

from sm64_events.storage.db import EventRow
from sm64_events.tracking.projection import Projector, replay
from sm64_events.tracking.segments import MatchContext, SegmentDef, SegmentEngine

W = "2026-10-01T01:40:21Z"
LOBBY_DOOR = "6:1:bhvDoorWarp:-1100,0,2202"
BASEMENT_DOOR = "6:1:bhvDoor:-1775,0,-824"
CTX = MatchContext(level=6, prev_level=6, num_stars=0, area=1)


def event(jid, kind, frame, payload=None):
    return EventRow(id=jid, session_id=5, seq=jid, type=kind,
                    frame=frame, wall_time_utc=W, payload=payload or {})


def definition(mode="strict", waypoints=True, clock="move"):
    return SegmentDef(
        id=1, name="LBLJ reference", enabled=True,
        start_triggers=[{"type": "moment_reached", "kind": "door_arrived",
                         "level": 6, "landmark": LOBBY_DOOR}],
        waypoints=[[{"type": "moment_reached", "kind": "door_open",
                     "level": 6, "landmark": BASEMENT_DOOR}]] if waypoints else [],
        end_triggers=[{"type": "entrance_touched", "to": 17}],
        guards=[], match_mode=mode, clock_start=clock)


def arrived(jid, frame):
    return event(jid, "moment_reached", frame,
                 {"kind": "door_arrived", "level": 6, "area": 1,
                  "landmark": {"key": LOBBY_DOOR}, "action": 4898,
                  "counter": 0, "igt_frames": 2})


def retry(jid, frame, counter=None, **payload):
    values = {"action": 205521409, "prev_action": 205521409,
              "mario_acted": True, "acted_tracking": True,
              "paused_frames_before": 0, **payload}
    if counter is not None:
        values["igt_frames_before"] = counter
    return event(jid, "practice_reset", frame, values)


def captured_lblj_events():
    # Counter values, frames, ids, and echo-discriminating door recencies
    # are copied from the reported live journal, not guessed from screenshots.
    rows = [event(250, "session_started", 0),
            event(251, "level_changed", 3390, {"from": 16, "to": 6}),
            event(252, "area_changed", 3390, {"level": 6, "from": 0, "to": 1}),
            arrived(259, 3407), event(260, "target_set", 0,
                                     {"kind": "segment", "segment_id": 1})]
    samples = [(261, 3528, 80, 83, False, 3529),
               (264, 3608, 38, 41, True, 3610),
               (267, 3709, 59, 62, True, 3711),
               (270, 3803, 52, 55, True, 3805),
               (273, 3996, 151, 154, True, 3998),
               (276, 4224, 186, 189, True, 4225),
               (279, 4319, 53, 56, True, 4320),
               (282, 5028, 667, 670, True, 5030),
               (285, 5585, 515, 518, True, 5587)]
    for jid, frame, counter, since_door, acted, arrival in samples:
        rows += [retry(jid, frame, counter, frames_since_door=since_door,
                       mario_acted=acted),
                 event(jid + 1, "mario_acted", arrival, {"action": 4898}),
                 arrived(jid + 2, arrival)]
    return rows


def test_reported_lblj_711_frames_are_the_current_runs_53_counter_frames():
    events = captured_lblj_events()
    live = Projector(segments=[definition()])
    live_rows = [attempt for row in events for attempt in live.feed(row)
                 if attempt.segment_id == 1]
    replay_rows, _ = replay(events, segments=[definition()])
    assert replay_rows == live_rows
    assert [row.rta_frames for row in live_rows] == [38, 59, 52, 151, 186, 53, 667, 515]
    reported = next(row for row in live_rows if row.anchor_frame == 4224)
    assert reported.id == 10000000276
    assert reported.outcome == "reset"
    assert reported.timed_by == "igt"
    assert reported.rta_frames == 53  # 4319 - 4224 is 95, the stale origin gave 711.


def test_current_door_spawn_run_uses_53_counter_frames_instead_of_95_global_frames():
    reference = replace(definition(),
                        start_triggers=[{"type": "attempt_anchor", "level": 6}])
    engine = SegmentEngine([reference])
    engine.feed(retry(276, 4224, 186), CTX)
    [row] = engine.feed(retry(279, 4319, 53), CTX)[0]
    assert row.anchor_frame == 4224
    assert row.rta_frames == 53
    assert row.timed_by == "igt"


@pytest.mark.parametrize("mode", ["strict", "loose", "exclusive"])
@pytest.mark.parametrize("waypoints", [False, True])
@pytest.mark.parametrize("clock", ["move", "trigger"])
def test_retries_inside_load_window_close_old_clock_then_start_new_clock(mode, waypoints, clock):
    engine = SegmentEngine([definition(mode, waypoints, clock)])
    engine.feed(arrived(1, 1000), CTX)
    # A genuine door echo zero starts the move clock after its animation.
    engine.feed(retry(2, 1051, frames_since_door=2), CTX)
    closed = []
    for jid, frame in [(3, 1200), (4, 1230), (5, 1400)]:
        closed += engine.feed(retry(jid, frame), CTX)[0]
    assert [row.rta_frames for row in closed] == ([149, 30, 170] if clock == "move"
                                                 else [200, 30, 170])
    assert all(row.timed_by == "delta" for row in closed)
    # Success after a retry uses that retry's counter, even for a continuing
    # waypoint arm whose start trigger never fires again.
    if waypoints:
        engine.feed(event(6, "moment_reached", 1410,
                          {"kind": "door_open", "level": 6,
                           "landmark": {"key": BASEMENT_DOOR}}), CTX)
    [success] = engine.feed(event(7, "warp_entered", 1450,
                                 {"level": 6, "to": 17, "igt_frames": 48}), CTX)[0]
    assert success.timed_by == "igt"
    assert success.rta_frames == 48


@pytest.mark.parametrize("discard", [{"mario_acted": False},
                                     {"paused_frames_before": 200}])
def test_discarded_retry_also_forgets_the_previous_move_clock(discard):
    engine = SegmentEngine([definition()])
    engine.feed(arrived(1, 1000), CTX)
    engine.feed(retry(2, 1051, frames_since_door=2), CTX)
    assert engine.feed(retry(3, 1200, **discard), CTX)[0] == []
    [row] = engine.feed(retry(4, 1240, 25), CTX)[0]
    assert row.anchor_frame == 1200
    assert row.rta_frames == 25
    assert row.timed_by == "igt"


def test_counter_at_reset_is_only_used_for_the_current_move_clock_epoch():
    engine = SegmentEngine([definition()])
    engine.feed(arrived(1, 1000), CTX)
    engine.feed(retry(2, 1051, frames_since_door=2), CTX)
    # A mid-segment door resets Usamune but does not end this multi-leg piece.
    engine.feed(retry(3, 1300, frames_since_door=2), CTX)
    [row] = engine.feed(retry(4, 1400, 90), CTX)[0]
    assert row.rta_frames == 349
    assert row.timed_by == "delta"


def test_trigger_clock_keeps_global_span_when_reset_has_a_counter():
    engine = SegmentEngine([definition(clock="trigger")])
    engine.feed(arrived(1, 1000), CTX)
    [row] = engine.feed(retry(2, 1060, 18), CTX)[0]
    assert row.rta_frames == 60
    assert row.timed_by == "delta"


def test_a_reset_counter_longer_than_its_run_is_not_borrowed():
    engine = SegmentEngine([definition()])
    engine.feed(retry(1, 1000), CTX)
    engine.feed(arrived(2, 1001), CTX)
    [row] = engine.feed(retry(3, 1100, 1000), CTX)[0]
    assert row.rta_frames == 99
    assert row.timed_by == "delta"


def test_frame_rewind_does_not_borrow_the_restored_counter():
    engine = SegmentEngine([definition()])
    engine.feed(arrived(1, 1000), CTX)
    engine.feed(retry(2, 1051, frames_since_door=2), CTX)
    [row] = engine.feed(event(3, "state_loaded", 500,
                             {"igt_frames_restored": 19}), CTX)[0]
    assert row.rta_frames is None
    [next_row] = engine.feed(retry(4, 650, 120), CTX)[0]
    assert next_row.rta_frames == 120
    assert next_row.timed_by == "igt"


def test_hard_reset_inside_load_window_preserves_the_elapsed_span():
    engine = SegmentEngine([definition()])
    engine.feed(arrived(1, 1000), CTX)
    [row] = engine.feed(event(2, "game_reset", 1060), CTX)[0]
    assert row.outcome == "hard_reset"
    assert row.rta_frames == 60


def test_completed_igt_and_late_load_rebases_keep_existing_behavior():
    engine = SegmentEngine([replace(definition(), waypoints=[])])
    engine.feed(arrived(1, 1000), CTX)
    engine.feed(retry(2, 1051, frames_since_door=2), CTX)
    engine.feed(retry(3, 1075, frames_since_door=2), CTX)
    [row] = engine.feed(event(4, "warp_entered", 1200,
                             {"level": 6, "to": 17, "igt_frames": 119}), CTX)[0]
    assert row.rta_frames == 119
    assert row.timed_by == "igt"
