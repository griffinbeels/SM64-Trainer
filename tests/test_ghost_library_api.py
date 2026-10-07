"""Public task flows expose review before commit and remember reusable searches."""
import json
import zipfile

import pytest

from sm64_events.ghost.library_preferences import SHORTCUTS_KEY
from tests.test_ghost_sharing import package
from tests.test_ghost_sharing_api import client_for, finished


CRITERIA = {"query": "Pillarless", "source": "local", "target": {"kind": "star", "entity_key": "2-1"},
            "practice": False, "sort": "fastest", "bests": True}


def preview(client, source):
    result = finished(client, client.post("/api/ghost/library/import/preview", content=source.read_bytes()))
    assert result["state"] == "done"
    return result["result"]


def test_preview_cancel_and_selective_commit_use_existing_job_contract(tmp_path):
    source, _, row = package(tmp_path)
    client, sharing = client_for(tmp_path)
    staged = preview(client, source)
    assert {"preview_id", "ghosts", "expires_utc"} == set(staged)
    assert sharing.imported_rows() == [] and sharing.database.writes == []
    assert client.delete(f"/api/ghost/library/import/preview/{staged['preview_id']}").json() == {"discarded": True}
    assert client.post("/api/ghost/library/import/commit", json={"preview_id": staged["preview_id"], "selected": [row["id"]]}).status_code == 422
    staged = preview(client, source)
    done = finished(client, client.post("/api/ghost/library/import/commit", json={
        "preview_id": staged["preview_id"], "selected": [row["id"]]}))
    assert done["state"] == "done" and done["result"]["imported"] == 1
    assert done["result"]["import_ids"] == [row["id"]]
    assert not list((sharing.root / "uploads").glob("*.upload"))
    assert not list(sharing.previews.root.glob("preview-*"))


def test_author_is_remembered_before_export_and_multilingual_limit_uses_bytes(tmp_path):
    client, sharing = client_for(tmp_path)
    assert client.put("/api/ghost/library/author", json={"author": "  Griffin  "}).json() == {"author": "Griffin"}
    assert client.get("/api/ghost/library").json()["author"] == "Griffin"
    assert client.put("/api/ghost/library/author", json={"author": "\u3042" * 42}).status_code == 200
    assert client.put("/api/ghost/library/author", json={"author": "\u3042" * 43}).status_code == 422
    for value in ("", [], {}, 4, True, None, "a\n"):
        assert client.put("/api/ghost/library/author", json={"author": value}).status_code == 422


def test_shortcuts_persist_criteria_not_identity_lists_and_delete_cleanly(tmp_path):
    client, sharing = client_for(tmp_path)
    answer = client.post("/api/ghost/library/shortcuts", json={"name": "My SSL bests", "criteria": CRITERIA})
    assert answer.status_code == 200
    row, = answer.json()["shortcuts"]
    assert row["version"] == 1 and row["criteria"] == CRITERIA
    assert not any(key in json.dumps(sharing.database.state[SHORTCUTS_KEY]) for key in ("attempt_id", "import_id"))
    assert client.get("/api/ghost/library").json()["shortcuts"] == [row]
    assert client.delete(f"/api/ghost/library/shortcuts/{row['id']}").json() == {"shortcuts": []}
    assert client.delete(f"/api/ghost/library/shortcuts/{row['id']}").status_code == 404


@pytest.mark.parametrize("changes", [
    {"source": []}, {"sort": {}}, {"practice": 1}, {"bests": "yes"}, {"query": None},
    {"target": {"kind": "star", "entity_key": []}}, {"target": {"kind": "segment", "entity_key": "01"}},
    {"target": {"kind": "segment", "entity_key": "1", "follow_active": True}}, {"attempts": [1]},
])
def test_external_shortcut_types_and_extra_fields_fail_with_actionable_validation(tmp_path, changes):
    client, _ = client_for(tmp_path)
    answer = client.post("/api/ghost/library/shortcuts", json={"name": "Bad", "criteria": {**CRITERIA, **changes}})
    assert answer.status_code == 422 and isinstance(answer.json()["detail"], str)


def test_invalid_preview_selections_are_api_errors_not_type_errors(tmp_path):
    source, _, row = package(tmp_path)
    client, _ = client_for(tmp_path)
    staged = preview(client, source)
    for selected in (None, {}, [], [None], [[]], [{}], [True], [row["id"], row["id"]]):
        answer = client.post("/api/ghost/library/import/commit", json={"preview_id": staged["preview_id"], "selected": selected})
        assert answer.status_code == 422
    for identity in (None, {}, [], 1):
        assert client.post("/api/ghost/library/import/commit", json={"preview_id": identity, "selected": [row["id"]]}).status_code == 422


@pytest.mark.parametrize("version", [[], {}, 1, True, "eu"])
def test_invalid_version_metadata_gives_readable_job_failure(tmp_path, version):
    source, _, _ = package(tmp_path)
    bad = tmp_path / "bad.sm64ghosts"
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(bad, "w") as changed:
        document = json.loads(original.read("collection.json"))
        document["ghosts"][0]["game_version"] = version
        changed.writestr("collection.json", json.dumps(document))
        for name in original.namelist():
            if name != "collection.json":
                changed.writestr(name, original.read(name))
    client, sharing = client_for(tmp_path)
    job = finished(client, client.post("/api/ghost/library/import/preview", content=bad.read_bytes()))
    assert job["state"] == "error" and "version" in job["message"]
    assert sharing.imported_rows() == []


@pytest.mark.parametrize("version", [True, [], {}, "2"])
def test_manifest_version_requires_an_integer_schema_version(tmp_path, version):
    source, _, _ = package(tmp_path)
    bad = tmp_path / "bad-manifest.sm64ghosts"
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(bad, "w") as changed:
        document = json.loads(original.read("collection.json"))
        document["version"] = version
        changed.writestr("collection.json", json.dumps(document))
        for name in original.namelist():
            if name != "collection.json":
                changed.writestr(name, original.read(name))
    client, sharing = client_for(tmp_path)
    job = finished(client, client.post("/api/ghost/library/import/preview", content=bad.read_bytes()))
    assert job["state"] == "error" and "collection" in job["message"]
    assert sharing.imported_rows() == []


def test_stored_shortcut_bool_version_and_unbounded_rows_are_not_exposed(tmp_path, monkeypatch):
    client, sharing = client_for(tmp_path)
    answer = client.post("/api/ghost/library/shortcuts", json={"name": "Saved", "criteria": CRITERIA}).json()
    row, = answer["shortcuts"]
    sharing.database.state[SHORTCUTS_KEY] = [{**row, "version": True}]
    assert client.get("/api/ghost/library").json()["shortcuts"] == []
    sharing.database.state[SHORTCUTS_KEY] = [row]
    monkeypatch.setattr("sm64_events.ghost.library_preferences.MAX_SHORTCUTS", 1)
    assert client.post("/api/ghost/library/shortcuts", json={"name": "Another", "criteria": CRITERIA}).status_code == 422
    sharing.database.state[SHORTCUTS_KEY] = [row, row]
    assert client.get("/api/ghost/library").json()["shortcuts"] == []
