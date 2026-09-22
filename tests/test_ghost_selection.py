"""Choose a recorded ghost without changing the input comparison template."""
import asyncio
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sm64_events.ghost.native_session import NativeGhost
from sm64_events.server.ghost_api import create_ghost_router
from sm64_events.storage.db import Database
from tests.ghost_fixture import FakeReplay, a_document, a_view
from tests.test_ghost_native_session import Capture
from tests.test_ghost_session import a_session, drive


def save(store, attempt=7, *, kind="star", key="24-1", strat="10 coin", active=True):
    return store.save(kind=kind, entity_key=key, strat_tag=strat,
                      name=f"Run {attempt}", origin=f"attempt:{attempt}",
                      document=a_document(), active=active)


def ghosts(number, pb=True):
    return {"pb": pb, "templates": [] if number is None else [number]}


def select(session, number, **changes):
    return asyncio.run(session.update(ghosts=ghosts(number), target=session._read_target(), **changes))


def chosen_ids(body):
    return body["selection"]["ghosts"]["templates"]


def test_choose_inactive_other_strategy_without_changing_input_template(tmp_path):
    replay = FakeReplay(saved=(7, 8), views={7: a_view(7), 8: a_view(8)})
    session, windows, _, store = a_session(tmp_path, replay=replay)
    comparison = save(store)
    chosen = save(store, 8, strat="Other route", active=False)
    body = select(session, chosen.id, enabled=True)
    assert chosen_ids(body) == [chosen.id]
    assert body["source"]["attempt_id"] == 8
    assert store.active_for("star", "24-1", "10 coin").id == comparison.id
    assert parse_qs(urlsplit(windows[0].url).query)["template_id"] == [str(chosen.id)]
    assert {row["template_id"] for row in body["selection"]["candidates"]} == {comparison.id, chosen.id}
    assert next(row for row in body["selection"]["candidates"] if row["template_id"] == chosen.id)["strat"] == "Other route"
    assert select(session, None)["source"]["attempt_id"] == 7
    assert "template_id" not in parse_qs(urlsplit(windows[0].url).query)


def test_choice_is_restored_per_target_and_strategy_after_restart(tmp_path):
    db = Database(tmp_path / "preferences.db")
    session, _, tracker, store = a_session(tmp_path, db=db)
    first = save(store)
    second = save(store, 8, active=False)
    segment = save(store, 9, kind="segment", key="31", strat=None)
    select(session, second.id)
    tracker.strat_tag = "Other route"
    assert chosen_ids(drive(session)) == []
    select(session, first.id)
    tracker.target, tracker.strat_tag = ("segment", 31), None
    select(session, segment.id)
    tracker.target, tracker.strat_tag = ("star", 24, 1), "10 coin"
    assert chosen_ids(drive(session)) == [second.id]
    restored, _, restored_tracker, _ = a_session(tmp_path, db=db, store=store)
    assert chosen_ids(drive(restored)) == [second.id]
    restored_tracker.target, restored_tracker.strat_tag = ("segment", 31), None
    assert chosen_ids(drive(restored)) == [segment.id]
    select(restored, None)
    assert chosen_ids(drive(restored)) == []


def test_deleted_choice_is_unavailable_instead_of_falling_back(tmp_path):
    session, windows, _, store = a_session(tmp_path)
    save(store)
    chosen = save(store, active=False)
    select(session, chosen.id, enabled=True)
    store.delete(chosen.id)
    body = drive(session)
    assert chosen_ids(body) == [chosen.id]
    assert body["source"]["template_id"] == chosen.id
    assert body["source"]["reason"] == "selected_template_missing"
    assert not body["source"]["available"] and not windows[0].visible
    assert chosen.id not in [row["template_id"] for row in body["selection"]["candidates"]]


@pytest.mark.parametrize("number", [True, False, 0, -1, 1.0, "1", [], {}])
def test_bad_selection_is_rejected_atomically(tmp_path, number):
    session, _, _, store = a_session(tmp_path)
    save(store)
    with pytest.raises(ValueError):
        select(session, number, enabled=True, opacity=.2)
    assert not drive(session)["enabled"]


def test_stale_or_foreign_selection_cannot_change_preferences(tmp_path):
    session, _, tracker, store = a_session(tmp_path)
    same = save(store)
    foreign = save(store, key="24-2")
    with pytest.raises(ValueError, match="this star or segment"):
        select(session, foreign.id, enabled=True)
    with pytest.raises(ValueError, match="no longer exists"):
        select(session, 999)
    old_target = session._read_target()
    tracker.strat_tag = "Other route"
    with pytest.raises(ValueError, match="target changed"):
        asyncio.run(session.update(ghosts=ghosts(same.id), target=old_target, enabled=True))
    assert not drive(session)["enabled"]
    with pytest.raises(ValueError, match="current practice target"):
        asyncio.run(session.update(ghosts=ghosts(same.id)))


def test_candidates_validate_files_without_changing_the_native_selection(tmp_path):
    session, _, _, store = a_session(tmp_path)
    first, chosen, absent = save(store), save(store, 8, active=False), save(store, 9, active=False)
    capture, calls, assets = Capture(), [], {}
    for number in (7, 8):
        path = tmp_path / f"{number}.ghost"
        path.write_bytes(b"valid asset")
        path.with_suffix(".json").write_text("{}")
        assets[number] = path, {"clip_id": f"{number:02x}" * 16, "sha256": "cd" * 32}
    replay = SimpleNamespace(recorder=SimpleNamespace(ghost_capture=capture),
                             native_identity=lambda _: {"native_clock_frame": 100},
                             find_native=lambda number: calls.append(number) or assets.get(number))
    session._native = NativeGhost(replay, origin_of=lambda *_: {
        "kind": "star", "session_id": 1, "arm_id": 9,
        "frame": 100, "utc": "2026-09-21T08:00:00+00:00"})
    body = select(session, chosen.id, enabled=True, view="mario")
    session._native.note()
    assert [clip["clip_path"] for clip in capture.commands[-1]["clips"]] == [str(assets[8][0])]
    rows = {row["template_id"]: row for row in body["selection"]["candidates"]}
    assert rows[first.id]["native_available"] and rows[chosen.id]["native_available"]
    assert not rows[absent.id]["native_available"] and rows[absent.id]["native_reason_text"]
    assert rows[first.id]["whole_available"] and not rows[chosen.id]["whole_available"]
    for _ in range(10):
        drive(session)
    assert calls == [8, 9, 7]
    # A renderer/model failure cannot relabel a validated saved file as invalid.
    capture.snapshot_status = lambda **_: {
        "available": True, "state": 5, "reason": 11,
        "producer": SimpleNamespace(configuration=2, epoch=3)}
    body = drive(session)
    assert next(row for row in body["selection"]["candidates"] if row["template_id"] == chosen.id)["native_available"]
    assert "game ROM" in body["window"]["reason"]


def test_source_api_and_switch_follow_same_choice_for_both_star_and_segment(tmp_path):
    replay = FakeReplay(saved=(7, 8), views={7: a_view(7), 8: a_view(8)})
    session, _, tracker, store = a_session(tmp_path, replay=replay)
    save(store)
    chosen = save(store, 8, active=False)
    app = FastAPI()
    app.include_router(create_ghost_router(session._inputs, replay, session))
    with TestClient(app) as client:
        response = client.put("/api/ghost", json={"ghosts": ghosts(chosen.id), "target": session._read_target()})
        assert response.status_code == 200
        params = {"kind": "star", "entity_key": "24-1", "strat": "10 coin"}
        assert client.get("/api/ghost/source", params=params).json()["attempt_id"] == 8
        store.delete(chosen.id)
        assert client.get("/api/ghost/source", params=params).json()["reason"] == "selected_template_missing"
        assert client.get("/api/ghost/source", params={**params, "template_id": -1}).status_code == 422
        tracker.target, tracker.strat_tag = ("segment", 31), None
        segment = save(store, 8, kind="segment", key="31", strat=None)
        response = client.put("/api/ghost", json={"ghosts": ghosts(segment.id), "target": session._read_target()})
        assert response.status_code == 200
        answer = client.get("/api/ghost/source", params={"kind": "segment", "entity_key": "31"}).json()
        assert answer["available"] and answer["attempt_id"] == 8


def test_real_saved_pose_corruption_revokes_only_that_candidate(tmp_path):
    from dataclasses import replace

    from tests.test_ghost_recording import native_service

    replay, row, archive = native_service(tmp_path)
    try:
        other = replace(row, id=row.id + 1)
        replay.tracker.db._attempts.append(other)
        replay.preserve_native(row.id)
        replay.preserve_native(other.id)
        session, _, _, store = a_session(tmp_path, target=("star", 2, 2),
                                         strat=None, replay=replay)
        kept = save(store, row.id, key="2-2", strat=None)
        chosen = save(store, other.id, key="2-2", strat=None, active=False)
        session._native = NativeGhost(replay)
        body = select(session, chosen.id, view="mario")
        assert all(candidate["native_available"] for candidate in body["selection"]["candidates"])
        path, _ = replay.find_native(other.id)
        data = bytearray(path.read_bytes())
        data[-1] ^= 1
        path.write_bytes(data)
        body = drive(session)
        candidates = {candidate["template_id"]: candidate for candidate in body["selection"]["candidates"]}
        assert not body["source"]["available"]
        assert not candidates[chosen.id]["native_available"]
        assert candidates[chosen.id]["native_reason_text"]
        assert candidates[kept.id]["native_available"]
        assert chosen_ids(body) == [chosen.id]
    finally:
        archive.close()
