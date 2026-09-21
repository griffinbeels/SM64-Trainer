"""Native playback reads observed matcher endpoints, never duration arithmetic."""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import sqlite3

import pytest

from sm64_events.core.events import Event
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.storage.db import Database, EventRow, MIGRATIONS
from sm64_events.tracking.projection import Projector, project
from sm64_events.tracking.segments import SEGMENT_ATTEMPT_OFFSET, SegmentEngine
from sm64_events.tracking.service import TrackerService
from tests.test_clock_start import DOOR_ECHO, ccm_piece, ctx
from tests.test_segments import _hundred_coin_def

UTC = "2026-09-21T10:00:00Z"
LATER = "2026-09-21T10:00:02Z"
LAST = "2026-09-21T10:00:03Z"


def row(jid, kind, frame, payload=None, *, utc=UTC, session=1):
    return EventRow(jid, session, jid, kind, frame, utc, payload or {})


def arm_row():
    return row(10, "moment_reached", 1000,
               {"kind": "door_open", "level": 6, "area": 1, "igt_frames": 42})


def close_row():
    return row(13, "warp_entered", 1127,
               {"level": 6, "area": 1, "to": 5, "igt_frames": 77}, utc=LAST)


def selected_segment(mode="move"):
    projector = Projector(segments=[ccm_piece(mode)])
    projector.feed(row(1, "level_changed", 990, {"from": 16, "to": 6}))
    projector.feed(row(2, "target_set", 991, {"kind": "segment", "segment_id": 1}))
    projector.feed(arm_row())
    return projector


@pytest.mark.parametrize("mode,origin,time,basis", [
    ("move", 1051, 77, "igt"), ("trigger", 1000, 127, "delta")])
def test_saved_segment_preserves_actual_origin_and_close_without_retiming(mode, origin, time, basis):
    projector = selected_segment(mode)
    projector.feed(row(11, "practice_reset", 1051, dict(DOOR_ECHO), utc=LATER))
    live = projector.native_ghost_origin("segment", "1")
    [attempt] = projector.feed(close_row())
    assert (attempt.native_clock_frame, attempt.closed_frame) == (origin, 1127)
    assert attempt.native_clock_utc == (LATER if mode == "move" else UTC)
    assert attempt.native_clock_kind == "segment"
    assert live["frame"] == attempt.native_clock_frame
    assert live["utc"] == attempt.native_clock_utc
    assert live["arm_id"] == attempt.id == 10 + SEGMENT_ATTEMPT_OFFSET
    assert (attempt.rta_frames, attempt.timed_by) == (time, basis)
    assert projector.native_ghost_origin("segment", "1") is None


def test_latest_move_zero_changes_origin_without_changing_the_arm():
    projector = selected_segment()
    before = projector.native_ghost_origin("segment", "1")
    assert before == {"kind": "segment", "session_id": 1,
                      "arm_id": 10 + SEGMENT_ATTEMPT_OFFSET,
                      "start_frame": 1000, "started_utc": UTC,
                      "frame": 1000, "utc": UTC}
    projector.feed(row(11, "practice_reset", 1051, dict(DOOR_ECHO), utc=LATER))
    projector.feed(row(12, "practice_reset", 1052, dict(DOOR_ECHO), utc=LAST))
    rebased = projector.native_ghost_origin("segment", "1")
    assert rebased == {**before, "frame": 1052, "utc": LAST}
    # A later section reset is another leg, not this arm's origin.
    projector.feed(row(14, "practice_reset", 1352, dict(DOOR_ECHO), utc=LAST))
    assert projector.native_ghost_origin("segment", "1") == rebased
    rebased["frame"] = 999
    assert projector.native_ghost_origin("segment", "1")["frame"] == 1052


def test_disarmed_but_still_selected_segment_has_no_live_origin():
    projector = selected_segment()
    assert projector.native_ghost_origin("segment", "2") is None
    assert projector.native_ghost_origin("star", "2-2") is None
    projector.feed(row(11, "death", 1200, {"cause": "fall"}))
    assert projector.target == ("segment", 1)
    assert projector.native_ghost_origin("segment", "1") is None


def test_gap_and_session_boundary_revoke_an_existing_origin():
    for event in ("tracking_gap", "session_started"):
        projector = selected_segment()
        assert projector.native_ghost_origin("segment", "1") is not None
        projector.feed(row(11, event, 1200, session=2))
        assert projector.native_ghost_origin("segment", "1") is None


def test_pending_destination_arm_is_not_certified_before_confirmation():
    definition = replace(ccm_piece("trigger"),
                         start_triggers=[{"type": "level_enter", "from": 16,
                                          "to": 6, "to_subarea": 2}])
    engine = SegmentEngine([definition])
    engine.feed(row(10, "level_changed", 1000, {"from": 16, "to": 6}), ctx())
    assert engine.native_ghost_origin(1) is None
    engine.feed(row(11, "area_changed", 1000, {"level": 6, "from": 1, "to": 2},
                    utc=LATER), ctx(area=2))
    origin = engine.native_ghost_origin(1)
    assert (origin["frame"], origin["utc"]) == (1000, UTC)
    engine.feed(row(12, "area_changed", 1000, {"level": 6, "from": 2, "to": 1},
                    utc=LAST), ctx())
    assert engine.native_ghost_origin(1) is None


def test_star_origin_survives_a_banked_area_leg_and_closes_at_observed_frame():
    projector = Projector()
    projector.feed(row(1, "target_set", 990, {"course_id": 2, "star_id": 2}))
    projector.feed(row(2, "practice_reset", 1000))
    origin = projector.native_ghost_origin("star", "2-2")
    assert origin["kind"] == "star" and origin["arm_id"] == 2
    projector.feed(row(3, "practice_reset", 1200,
                       {"area_load": True, "igt_frames_before": 101}, utc=LATER))
    assert projector.native_ghost_origin("star", "2-2") == origin
    [attempt] = projector.feed(row(4, "star_collected", 1800,
                                  {"course_id": 2, "star_id": 2, "igt_frames": 251}, utc=LAST))
    assert (attempt.native_clock_frame, attempt.native_clock_utc,
            attempt.native_clock_kind, attempt.closed_frame) == (1000, UTC, "star", 1800)
    assert (attempt.igt_frames, attempt.rta_frames) == (251, 800)
    assert projector.native_ghost_origin("star", "2-2") is None


def test_unobserved_star_beginning_does_not_invent_a_native_origin():
    [attempt] = project([row(1, "star_collected", 1800,
                            {"course_id": 2, "star_id": 2, "igt_frames": 251})])
    assert attempt.closed_frame == 1800
    assert (attempt.native_clock_frame, attempt.native_clock_utc,
            attempt.native_clock_kind) == (None, None, None)


@pytest.mark.parametrize("involuntary", ["area_load", "teleport"])
def test_first_seen_involuntary_reset_keeps_scoring_but_has_no_whole_star_origin(involuntary):
    projector = Projector()
    projector.feed(row(1, "target_set", 990, {"course_id": 2, "star_id": 2}))
    projector.feed(row(2, "practice_reset", 1000,
                       {involuntary: True, "igt_frames_before": 100}))
    assert projector.native_ghost_origin("star", "2-2") is None
    [attempt] = projector.feed(row(3, "star_collected", 1500,
                                  {"course_id": 2, "star_id": 2, "igt_frames": 251}))
    assert (attempt.anchor_frame, attempt.rta_frames, attempt.igt_frames) == (1000, 500, 251)
    assert (attempt.native_clock_frame, attempt.native_clock_utc,
            attempt.native_clock_kind, attempt.closed_frame) == (None, None, None, 1500)


def hundred_projector(*, observed, duplicate=False):
    definitions = [_hundred_coin_def()]
    if duplicate:
        definitions.append(_hundred_coin_def(id=8))
    projector = Projector(segments=definitions)
    if observed:
        projector.feed(row(1, "level_changed", 900, {"from": 16, "to": 24}))
    projector.feed(row(2, "target_set", 910, {"course_id": 2, "star_id": 6}))
    projector.feed(row(3, "star_collected", 1000,
                       {"course_id": 2, "star_id": 6, "igt_frames": 101}))
    return projector


def test_reattributed_star_uses_its_engine_clock_and_persists_that_policy():
    projector = hundred_projector(observed=True)
    origin = projector.native_ghost_origin("star", "2-6")
    assert origin["kind"] == "segment" and origin["frame"] == 900
    attempts = projector.feed(row(4, "star_collected", 1300,
                                   {"course_id": 2, "star_id": 3, "igt_frames": 301}, utc=LAST))
    [attempt] = [a for a in attempts if a.star_id == 6]
    assert attempt.segment_id is None
    assert (attempt.native_clock_kind, attempt.native_clock_frame,
            attempt.closed_frame) == ("segment", 900, 1300)
    assert origin["arm_id"] == attempt.id


def test_proof_arm_with_unobserved_beginning_and_ambiguous_engines_refuse():
    projector = hundred_projector(observed=False)
    assert projector.native_ghost_origin("star", "2-6") is None
    attempts = projector.feed(row(4, "star_collected", 1300,
                                   {"course_id": 2, "star_id": 3, "igt_frames": 301}))
    [attempt] = [a for a in attempts if a.star_id == 6]
    assert attempt.native_clock_frame is None and attempt.native_clock_kind is None
    assert attempt.closed_frame == 1300
    assert hundred_projector(observed=True, duplicate=True).native_ghost_origin("star", "2-6") is None


def test_service_refuses_unrecovered_or_previous_session_origins(tmp_path):
    db = Database(tmp_path / "tracker.db")
    service = TrackerService(db, Broadcaster())
    asyncio.run(service.start())
    stamp = datetime(2026, 9, 21, 10, tzinfo=timezone.utc)
    for kind, frame, payload in (("target_set", 990, {"course_id": 2, "star_id": 2}),
                                 ("practice_reset", 1000, {})):
        asyncio.run(service.publish(Event(type=kind, frame=frame,
                                          timestamp_utc=stamp, payload=payload)))
    assert service.native_ghost_origin("star", "2-2")["session_id"] == service.session_id
    service._tracking_dirty = True
    assert service.native_ghost_origin("star", "2-2") is None
    service._tracking_dirty = False
    service.session_id += 1
    assert service.native_ghost_origin("star", "2-2") is None
    db.close()


def test_native_endpoints_round_trip_cache_and_old_rows_remain_unknown(tmp_path):
    path = tmp_path / "prior.db"
    with sqlite3.connect(path) as old:
        for migration in MIGRATIONS[:-1]:
            old.executescript(migration)
        old.execute(f"PRAGMA user_version = {len(MIGRATIONS) - 1}")
        old.execute("""INSERT INTO attempts
            (id,session_id,anchor_type,anchor_frame,outcome,igt_frames,rta_frames,
             started_utc,ended_utc) VALUES (7,1,'practice_reset',1000,'success',251,800,?,?)""",
                    (UTC, LAST))
    db = Database(path)
    previous = db.attempt(7)
    assert (previous.native_clock_frame, previous.native_clock_utc,
            previous.native_clock_kind, previous.closed_frame) == (None, None, None, None)
    projector = selected_segment()
    projector.feed(row(11, "practice_reset", 1051, dict(DOOR_ECHO), utc=LATER))
    [attempt] = projector.feed(close_row())
    db.upsert_attempt(attempt)
    assert db.attempt(attempt.id) == attempt
    db.replace_attempts([attempt])
    db.close()
    reopened = Database(path)
    assert reopened.attempts() == [attempt]
    reopened.close()
