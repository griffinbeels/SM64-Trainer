"""Recorded OR starts share one ordered path, independent of sample history."""
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sm64_events.core.events import Event
from sm64_events.server.api import create_api_router
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.storage.db import Database
from sm64_events.tracking.segments import MatchContext, SegmentDef, SegmentEngine
from sm64_events.tracking.service import TrackerService

T0 = datetime(2026, 9, 30, tzinfo=timezone.utc)


@pytest.fixture
def recorder_api(tmp_path):
    # Only the HTTP router runs; no poller, emulator, recorder or listener.
    db = Database(tmp_path / "recorder.db")
    session_id = db.insert_session(T0.isoformat())
    service = TrackerService(db, Broadcaster())
    app = FastAPI()
    app.include_router(create_api_router(service))
    with TestClient(app) as client:
        yield client, db, session_id
    db.close()


def append(db, session_id, kind, frame, payload):
    return db.append_event(session_id, len(db.events()) + 1,
        Event(type=kind, frame=frame, timestamp_utc=T0, payload=payload))


def draft(client, ids, start_ids=()):
    return client.get("/api/segments/synthesize", params={
        "ids": ",".join(map(str, ids)),
        "start_ids": ",".join(map(str, start_ids))})


def moment(kind, key):
    return {"kind": kind, "level": 9, "area": 1,
            "landmark": {"key": key, "nameable": True, "placed": True}}


def test_samples_before_and_after_the_path_do_not_expand_its_walk(recorder_api):
    client, db, sid = recorder_api
    before = append(db, sid, "level_changed", 100, {"from": 24, "to": 6})
    primary = append(db, sid, "level_changed", 1000, {"from": 23, "to": 6})
    append(db, sid, "area_changed", 1000, {"level": 6, "from": 1, "to": 1})
    middle = append(db, sid, "area_changed", 1300, {"level": 6, "from": 1, "to": 3})
    end = append(db, sid, "level_changed", 1500, {"from": 6, "to": 19})
    append(db, sid, "area_changed", 1700, {"level": 6, "from": 3, "to": 2})
    after = append(db, sid, "level_changed", 2000, {"from": 22, "to": 6})
    path = [primary, middle, end]
    legacy = client.get("/api/segments/synthesize", params={
        "ids": ",".join(map(str, path))}).json()
    response = draft(client, reversed(path), [after, before])
    assert response.status_code == 200
    body = response.json()
    assert body["start_clauses"] == [
        {"type": "level_exit", "from": 23},
        {"type": "level_exit", "from": 24},
        {"type": "level_exit", "from": 22}]
    assert body["start_sentences"] == [
        "Exit Dire, Dire Docks", "Exit Whomp's Fortress", "Exit Lethal Lava Land"]
    for field in ("start_clause", "start_sentence", "end_clause", "end_sentence",
                  "picked", "steps", "name"):
        assert body[field] == legacy[field], field
    assert [step["node"] for step in body["steps"]] == ["6:3"]
    assert body["picked_explicit"] is True


def test_promoting_a_middle_preserves_the_remaining_waypoint_order(recorder_api):
    client, db, sid = recorder_api
    primary = append(db, sid, "level_changed", 100, {"from": 23, "to": 6})
    first = append(db, sid, "level_changed", 200, {"from": 6, "to": 24})
    promoted = append(db, sid, "level_changed", 300, {"from": 24, "to": 6})
    last = append(db, sid, "area_changed", 400, {"level": 6, "from": 1, "to": 3})
    end = append(db, sid, "level_changed", 500, {"from": 6, "to": 19})
    body = draft(client, [end, last, promoted, first, primary], [promoted]).json()
    assert body["start_clauses"] == [
        {"type": "level_exit", "from": 23}, {"type": "level_exit", "from": 24}]
    assert [step["id"] for step in body["picked"]] == [first, last]
    assert [step["clause"] for step in body["picked"]] == [
        {"type": "level_enter", "to": 24},
        {"type": "area_enter", "level": 6, "area": 3, "from": 1}]
    assert body["picked_explicit"] is True


def test_all_promoted_middles_do_not_revive_automatic_steps(recorder_api):
    client, db, sid = recorder_api
    primary = append(db, sid, "level_changed", 100, {"from": 24, "to": 6})
    append(db, sid, "area_changed", 100, {"level": 6, "from": 1, "to": 1})
    promoted = append(db, sid, "moment_reached", 200,
                      {"kind": "door_open", "level": 6, "ordinal": 1})
    append(db, sid, "area_changed", 400, {"level": 6, "from": 1, "to": 3})
    end = append(db, sid, "level_changed", 500, {"from": 6, "to": 8})
    body = draft(client, [primary, promoted, end], [promoted]).json()
    assert body["picked"] == []
    assert body["picked_explicit"] is True
    assert [step["node"] for step in body["steps"]] == ["6:3"]
    automatic = draft(client, [primary, end], [promoted]).json()
    assert automatic["picked_explicit"] is False
    assert automatic["steps"] == body["steps"]


def test_equivalent_samples_deduplicate_conditions_but_preserve_distinct_spawns(recorder_api):
    client, db, sid = recorder_api
    spawn = {"level": 9, "area": 1, "spawn_node": 10}
    primary = append(db, sid, "spawned", 100, spawn)
    repeated = append(db, sid, "spawned", 100, spawn)
    other = append(db, sid, "spawned", 100, {**spawn, "spawn_node": 20})
    end = append(db, sid, "moment_reached", 300, moment("pole_grab", "pole"))
    body = draft(client, [primary, end], [other, repeated, primary, other]).json()
    assert body["start_clauses"] == [
        {"type": "spawned", **spawn}, {"type": "spawned", **spawn, "spawn_node": 20}]
    assert len(body["start_sentences"]) == 2
    assert body["start_clause"] == body["start_clauses"][0]
    assert body["start_sentence"] == body["start_sentences"][0]


@pytest.mark.parametrize(("sample", "status", "message"), [
    ("nope", 422, "start_ids"),
    ("999999", 404, "unknown timeline event"),
    ("unsupported", 409, "start"),
    ("finish", 422, "finish")])
def test_unusable_alternative_start_has_a_specific_error(recorder_api, sample, status, message):
    client, db, sid = recorder_api
    primary = append(db, sid, "spawned", 100, {"level": 9})
    unsupported = append(db, sid, "practice_reset", 200, {"igt_frames_before": 0})
    end = append(db, sid, "moment_reached", 300, moment("pole_grab", "pole"))
    extra = str({"unsupported": unsupported, "finish": end}.get(sample, sample))
    response = client.get("/api/segments/synthesize", params={
        "ids": f"{primary},{end}", "start_ids": extra})
    assert response.status_code == status
    assert message in response.json()["detail"]


def test_both_recorded_starts_require_the_same_waypoint_before_finishing(recorder_api):
    client, db, sid = recorder_api
    first_spawn = {"level": 9, "area": 1, "spawn_node": 10}
    second_spawn = {**first_spawn, "spawn_node": 20}
    door = moment("door_open", "9:1:bhvDoor:1,2,3")
    pole = moment("pole_grab", "9:1:pole:4,5,6")
    primary = append(db, sid, "spawned", 100, first_spawn)
    waypoint = append(db, sid, "moment_reached", 200, door)
    end = append(db, sid, "moment_reached", 300, pole)
    alternate = append(db, sid, "spawned", 500, second_spawn)
    append(db, sid, "moment_reached", 600, door)
    append(db, sid, "moment_reached", 700, pole)
    append(db, sid, "spawned", 900, second_spawn)
    append(db, sid, "moment_reached", 1100, pole)
    body = draft(client, [primary, waypoint, end], [alternate]).json()
    definition = {"name": body["name"], "enabled": True,
                  "start_triggers": body["start_clauses"],
                  "end_triggers": [body["end_clause"]], "guards": [],
                  "waypoints": [[step["clause"]] for step in body["picked"]],
                  "match_mode": "strict", "clock_start": "move"}
    engine = SegmentEngine([SegmentDef(id=1, **definition)])
    closed = []
    for row in db.events():
        attempts, _notices = engine.feed(
            row, MatchContext(level=9, prev_level=9, num_stars=0, area=1))
        closed.extend(attempts)
    assert [attempt.anchor_frame for attempt in closed] == [100, 500]
    assert [attempt.rta_frames for attempt in closed] == [200, 200]
    assert all(attempt.outcome == "success" for attempt in closed)
    armed = engine.armed_items()[1]
    assert armed.start_frame == 900
    assert armed.progress == 0
    response = client.post("/api/segments/backtest", json={"definition": definition})
    assert response.status_code == 200
    report = response.json()
    assert report["fires"] == 2
    assert [attempt["anchor_frame"] for attempt in report["attempts"]] == [100, 500]
    assert report["unclosed"][0]["progress"] == 0
