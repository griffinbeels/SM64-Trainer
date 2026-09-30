"""A shared save must become release input, not merely a dirty SQLite row."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from sm64_events.core import paths
from sm64_events.server import api
from sm64_events.server.app import create_app
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.poller import Poller
from sm64_events.storage.db import Database
from sm64_events.tracking import service as service_module
from sm64_events.tracking.defaults import reconcile_defaults, remember_deletion
from sm64_events.tracking.service import TrackerService
from sm64_events.tracking.shared_defaults import SharedDefaultsStore


BASE_ROW = {
    "seed_key": "seg:lblj", "name": "LBLJ", "enabled": True,
    "start_triggers": [{"type": "spawned", "level": 6}],
    "end_triggers": [{"type": "warp_entered", "level": 6}],
    "waypoints": [], "guards": [], "category": "Tricks",
    "default_strat": "LBLJ", "match_mode": "strict",
    "parents": ["area:6:1"], "clock_start": "trigger",
}
BASE = {"seed_version": 1, "segments": [BASE_ROW], "routes": []}


def lblj(db):
    return next(row for row in db.segment_defs() if row["seed_key"] == "seg:lblj")


class OfflineMemory:
    attached = False

    def attach(self):
        return False

    def detach(self):
        pass


@pytest.fixture
def shared_client(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    (root / "src/sm64_events/data").mkdir(parents=True)
    seed_path = root / "src/sm64_events/data/defaults.seed.json"
    seed_path.write_text(json.dumps(BASE), encoding="utf-8")
    monkeypatch.setattr(api, "shared_defaults_root", lambda: root)
    monkeypatch.setattr(service_module, "shared_defaults_root", lambda: root)
    monkeypatch.setattr(service_module, "SharedDefaultsStore",
                        lambda checkout: SharedDefaultsStore(
                            checkout, base_builder=lambda: copy.deepcopy(BASE)))
    db = Database(tmp_path / "local.db")
    reconcile_defaults(db, BASE)
    broadcaster = Broadcaster()
    tracker = TrackerService(db, broadcaster)
    monkeypatch.setattr(tracker, "_defaults_seed",
                        lambda: json.loads(seed_path.read_text(encoding="utf-8")))
    app = create_app(Poller(OfflineMemory(), [], tracker), broadcaster,
                     service=tracker)
    with TestClient(app) as client:
        yield client, tracker, db, root, seed_path
    db.close()


def test_shared_fix_survives_rebuild_restart_and_release_reconcile(shared_client, tmp_path):
    client, tracker, db, root, seed_path = shared_client
    sid = lblj(db)["id"]
    response = client.put(f"/api/segments/{sid}?scope=shared", json={
        "start_triggers": [
            {"type": "moment_reached", "kind": "door_open",
             "landmark": "6:1:bhvDoor:-1775,0,-824"},
            {"type": "spawned", "level": 6}],
        "clock_start": "move", "match_mode": "loose",
        "category": None, "parents": ["star:16:1"],
    })
    assert response.status_code == 200, response.text
    assert response.json()["seed_key"] == "seg:lblj"
    assert response.json()["message"] == "Saved for the next release"
    corrected = lblj(db)
    assert corrected["id"] == sid and corrected["seed_dirty"] == 0
    assert corrected["default_strat"] == "LBLJ"  # not an editable request field
    shipped = json.loads(seed_path.read_text(encoding="utf-8"))
    # A new store process reading authored input must reproduce the written seed.
    store = SharedDefaultsStore(root, base_builder=lambda: copy.deepcopy(BASE))
    with store.publish_segment({k: v for k, v in corrected.items()
                                if k not in {"id", "seed_key", "seed_dirty", "created_utc"}},
                               "seg:lblj"):
        pass
    assert json.loads(seed_path.read_text(encoding="utf-8")) == shipped
    for user in ("fresh", "clean", "personal", "deleted"):
        released = Database(tmp_path / f"{user}.db")
        try:
            if user != "fresh":
                reconcile_defaults(released, BASE)
            if user == "personal":
                local_id = lblj(released)["id"]
                released.update_segment_def(local_id, name="My LBLJ", seed_dirty=1)
            if user == "deleted":
                local_id = lblj(released)["id"]
                remember_deletion(released, "segments", "seg:lblj")
                released.delete_segment_def(local_id)
            assert reconcile_defaults(released, shipped) == []
            if user == "deleted":
                assert not any(r["seed_key"] == "seg:lblj" for r in released.segment_defs())
            elif user == "personal":
                assert lblj(released)["name"] == "My LBLJ"
            else:
                row = lblj(released)
                for field in ("start_triggers", "clock_start", "match_mode", "category",
                              "parents", "default_strat"):
                    assert row[field] == corrected[field], (user, field)
        finally:
            released.close()
    # The maintainer's own restart keeps the same local id and clean shared row.
    reconcile_defaults(db, shipped)
    assert lblj(db) == corrected


def test_legacy_and_explicit_personal_saves_never_change_release_input(shared_client):
    client, tracker, db, root, seed_path = shared_client
    before = seed_path.read_bytes()
    sid = lblj(db)["id"]
    for suffix in ("", "?scope=personal"):
        response = client.put(f"/api/segments/{sid}{suffix}", json={"name": "Mine"})
        assert response.status_code == 200
        assert lblj(db)["seed_dirty"] == 1
        assert seed_path.read_bytes() == before
    assert not (root / "src/sm64_events/data/segments.authored.json").exists()


def test_custom_row_promotion_keeps_identity_and_becomes_shippable(shared_client):
    client, tracker, db, root, seed_path = shared_client
    body = {k: v for k, v in BASE_ROW.items() if k not in {"seed_key", "default_strat"}}
    body["name"] = "A newly discovered segment"
    sid = client.post("/api/segments", json=body).json()["id"]
    result = client.put(f"/api/segments/{sid}?scope=shared", json={}).json()
    assert result["id"] == sid and result["seed_key"] != "seg:lblj"
    row = next(r for r in db.segment_defs() if r["id"] == sid)
    assert row["seed_key"] == result["seed_key"] and row["seed_dirty"] == 0
    assert any(r["seed_key"] == row["seed_key"] for r in
               json.loads(seed_path.read_text(encoding="utf-8"))["segments"])


def test_unavailable_shared_scope_is_forbidden_without_any_write(shared_client, monkeypatch):
    client, tracker, db, root, seed_path = shared_client
    monkeypatch.setattr(api, "shared_defaults_root", lambda: None)
    assert client.get("/api/segments/authoring").json() == {
        "can_share": False, "default_scope": "personal"}
    before = seed_path.read_bytes()
    sid = lblj(db)["id"]
    assert client.put(f"/api/segments/{sid}?scope=shared", json={"name": "No"}).status_code == 403
    assert client.post("/api/segments?scope=shared", json={
        k: v for k, v in BASE_ROW.items() if k not in {"seed_key", "default_strat"}
    }).status_code == 403
    assert lblj(db)["name"] == "LBLJ"
    assert seed_path.read_bytes() == before
    assert client.put(f"/api/segments/{sid}?scope=everyone", json={}).status_code == 422


def test_shared_db_failure_rolls_back_authored_and_generated_files(shared_client, monkeypatch):
    client, tracker, db, root, seed_path = shared_client
    before = seed_path.read_bytes()
    def refuse(*args, **kwargs):
        raise OSError("database unavailable")
    monkeypatch.setattr(db, "update_segment_def", refuse)
    response = client.put(f"/api/segments/{lblj(db)['id']}?scope=shared",
                          json={"name": "Cannot commit"})
    assert response.status_code == 503
    assert seed_path.read_bytes() == before
    assert not (root / "src/sm64_events/data/segments.authored.json").exists()


def test_reset_restores_the_complete_latest_shared_definition(shared_client):
    client, tracker, db, root, seed_path = shared_client
    sid = lblj(db)["id"]
    assert client.put(f"/api/segments/{sid}", json={
        "parents": [], "clock_start": "move", "match_mode": "loose",
        "category": None, "enabled": False}).status_code == 200
    assert client.post(f"/api/segments/{sid}/reset").status_code == 200
    row = lblj(db)
    for field, value in BASE_ROW.items():
        assert row[field] == value, field
    assert row["seed_dirty"] == 0


def test_shared_create_returns_committed_id_when_runtime_refresh_fails(shared_client, monkeypatch):
    client, tracker, db, root, seed_path = shared_client
    async def refuse_refresh():
        raise RuntimeError("offline projection failed")
    monkeypatch.setattr(tracker, "_segments_changed", refuse_refresh)
    response = client.post("/api/segments?scope=shared", json={
        k: v for k, v in BASE_ROW.items() if k not in {"seed_key", "default_strat"}
    })
    assert response.status_code == 200
    result = response.json()
    assert "Refresh failed" in result["message"]
    assert any(r["id"] == result["id"] and r["seed_key"] == result["seed_key"]
               for r in db.segment_defs())
    assert any(r["seed_key"] == result["seed_key"] for r in
               json.loads(seed_path.read_text(encoding="utf-8"))["segments"])


def test_only_explicit_source_launcher_can_author_defaults(monkeypatch):
    monkeypatch.delenv("SM64_MAINTAINER", raising=False)
    monkeypatch.setattr(paths, "is_frozen", lambda: False)
    assert paths.shared_defaults_root() is None
    monkeypatch.setenv("SM64_MAINTAINER", "1")
    assert paths.shared_defaults_root() is not None
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    assert paths.shared_defaults_root() is None


@pytest.mark.parametrize("field", ["guards", "waypoints", "parents"])
def test_shared_null_collections_cannot_enter_release_source(shared_client, field):
    client, tracker, db, root, seed_path = shared_client
    before = seed_path.read_bytes()
    response = client.put(f"/api/segments/{lblj(db)['id']}?scope=shared", json={field: None})
    assert response.status_code == 409, response.text
    assert seed_path.read_bytes() == before
