"""One upload imports a collection; one export job yields a downloadable file."""
import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sm64_events.ghost.sharing import GhostSharing
from sm64_events.server.ghost_api import create_ghost_router
from tests.test_ghost_sharing import Store, package


def client_for(tmp_path):
    replay = SimpleNamespace(cfg=SimpleNamespace(save_root=tmp_path / "replays"))
    sharing = GhostSharing(replay, Store())
    session = SimpleNamespace(_sharing=lambda: sharing, _read_target=lambda: {"kind": "star", "entity_key": "2-1", "strat": "Standard"})
    app = FastAPI()
    app.include_router(create_ghost_router(None, replay, session=session))
    return TestClient(app), sharing


def finished(client, answer):
    assert answer.status_code == 200
    url = f"/api/ghost/library/jobs/{answer.json()['job_id']}"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = client.get(url)
        assert result.status_code == 200
        if result.json()["state"] != "running":
            return result.json()
        time.sleep(.01)
    raise AssertionError("Collection job never finished")


def test_one_file_imports_then_exports_a_collection_and_download_has_no_internal_path(tmp_path):
    source, _path, row = package(tmp_path)
    client, sharing = client_for(tmp_path)
    result = finished(client, client.post("/api/ghost/library/import", content=source.read_bytes(),
                                         headers={"Content-Type": "application/octet-stream"}))
    assert result["state"] == "done" and result["result"]["imported"] == 1
    assert not list((sharing.root / "uploads").glob("*.upload"))
    catalog = client.get("/api/ghost/library")
    assert catalog.status_code == 200
    assert catalog.json()["ghosts"][0]["author"] == "Runner One"
    exported = finished(client, client.post("/api/ghost/library/export", json={
        "selected": {"imports": [row["id"]]}, "author": "My Name"}))
    assert exported["state"] == "done"
    assert "path" not in exported["result"] and exported["result"]["exported"] == 1
    download = client.get(exported["result"]["download_url"])
    assert download.status_code == 200 and download.content.startswith(b"PK")
    assert ".sm64ghosts" in download.headers["content-disposition"]


def test_bad_import_is_a_readable_job_failure_and_does_not_publish_any_ghost(tmp_path):
    client, sharing = client_for(tmp_path)
    result = finished(client, client.post("/api/ghost/library/import", content=b"not a package"))
    assert result["state"] == "error" and "collection" in result["message"]
    assert sharing.imported_rows() == []
    assert not list((sharing.root / "uploads").glob("*.upload"))


def test_sharing_doors_refuse_unknown_jobs_empty_upload_and_invalid_export_selection(tmp_path):
    client, _sharing = client_for(tmp_path)
    assert client.get("/api/ghost/library/jobs/missing").status_code == 404
    assert client.get("/api/ghost/library/download/missing").status_code == 404
    assert client.post("/api/ghost/library/import", content=b"").status_code == 422
    assert client.post("/api/ghost/library/export", json={"selected": {"attempts": [0]}, "author": "Name"}).status_code == 422
    assert client.post("/api/ghost/library/export", json={"selected": {"attempts": [1]}, "author": ""}).status_code == 422


def test_race_preparation_checks_current_target_and_import_bytes(tmp_path):
    source, _, row = package(tmp_path)
    client, sharing = client_for(tmp_path)
    sharing.import_file(source)
    body = {"ghosts": {"pb": False, "attempts": [], "imports": [row["id"]]},
            "target": {"kind": "star", "entity_key": "2-1", "strat": "Standard"}}
    assert client.post("/api/ghost/library/prepare-race", json=body).status_code == 200
    assert client.post("/api/ghost/library/prepare-race", json={**body, "target": None}).status_code == 422
    (sharing.root / f"{row['sha256']}.ghost").write_bytes(b"other")
    assert client.post("/api/ghost/library/prepare-race", json=body).status_code == 422
    assert sharing.database.writes == []


def test_unready_collections_and_absent_practice_target_have_readable_refusals(tmp_path):
    app = FastAPI()
    unready = SimpleNamespace(_sharing=lambda: None, _read_target=lambda: None)
    app.include_router(create_ghost_router(None, None, session=unready))
    client = TestClient(app)
    assert client.get("/api/ghost/library").status_code == 503
    assert client.post("/api/ghost/library/export", json={
        "selected": {"attempts": [1]}, "author": "Name"}).status_code == 503
    assert client.post("/api/ghost/library/prepare-race", json={
        "ghosts": {"pb": False, "attempts": []}, "target": None}).status_code == 422
