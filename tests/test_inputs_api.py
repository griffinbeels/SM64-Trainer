"""The captured-input routes are a skin over `InputsService`: every route
asks the service and maps its exceptions (404 / 409 / 503), nothing more."""
from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sm64_events.inputs.frame import InputFrame
from sm64_events.inputs.observation import InputObservation
from sm64_events.inputs.service import InputsService
from sm64_events.server.inputs_api import create_inputs_router
from sm64_events.storage.db import Database

AT = "2026-08-20T21:00:00+00:00"
LATER = "2026-08-20T21:00:30+00:00"


@dataclass
class FakeAttempt:
    id: int
    started_utc: str = AT
    ended_utc: str = LATER
    anchor_frame: int | None = None
    rta_frames: int | None = None
    segment_id: int | None = None
    course_id: int | None = 24
    star_id: int | None = 1
    strat_tag: str | None = "10 coin"


@pytest.fixture(params=[False, True], ids=["legacy", "observed"])
def client(tmp_path, request):
    db = Database(tmp_path / "t.db")
    session = db.insert_session(AT)
    db.inputs.append(session, [(100 + step, InputFrame(0x8000, 0, 40, 0))
                               for step in range(5)], AT, LATER,
                     observations=([InputObservation("poll:api", n, AT) for n in range(5)]
                                   if request.param else None))
    captured = FakeAttempt(id=7)
    silent = FakeAttempt(id=8, started_utc="2026-08-21T09:00:00+00:00",
                         ended_utc="2026-08-21T09:00:30+00:00")
    segment = FakeAttempt(id=9, segment_id=12)
    service = InputsService(db.inputs, db.input_templates,
                            lambda: [captured, silent, segment], version="jp")
    app = FastAPI()
    app.include_router(create_inputs_router(service))
    return TestClient(app)


def test_the_timeline_answers_with_named_runs(client):
    payload = client.get("/api/attempts/7/inputs").json()
    assert payload["runs"] == [{"start": 0, "length": 5, "buttons": 0x8000,
                                "stick_x": 40, "stick_y": 0, "yaw": 0,
                                "speed": 0.0}]


def test_an_unknown_attempt_is_a_404_on_every_attempt_route(client):
    assert client.get("/api/attempts/99/inputs").status_code == 404
    assert client.get("/api/attempts/99/inputs/document").status_code == 404
    assert client.post("/api/attempts/99/inputs/template",
                       json={}).status_code == 404


def test_the_document_records_the_version_the_server_is_reading(client):
    text = client.get("/api/attempts/7/inputs/document").text
    assert "version:  jp" in text
    assert "0-4 A +40,+0" in text


def test_an_attempt_with_no_capture_has_no_document_and_cannot_be_marked(client):
    assert client.get("/api/attempts/8/inputs/document").status_code == 404
    marked = client.post("/api/attempts/8/inputs/template", json={})
    assert marked.status_code == 409
    assert "no captured input" in marked.text


def test_marking_an_attempt_makes_it_the_active_template(client):
    marked = client.post("/api/attempts/7/inputs/template",
                         json={"name": "the good one"}).json()
    assert marked["origin"] == "attempt:7"
    payload = client.get("/api/attempts/7/inputs").json()
    assert payload["template"]["name"] == "the good one"
    listed = client.get("/api/inputs/templates",
                        params={"kind": "star", "entity_key": "24-1"}).json()
    assert [row["active"] for row in listed["templates"]] == [True]


def test_local_export_download_and_import_lifecycle(client):
    exported = client.get("/api/attempts/7/inputs/document")
    assert exported.headers["content-disposition"] == 'attachment; filename="attempt-7.inputs.txt"'
    assert "author:   griffman1212" in exported.text
    original = client.post("/api/attempts/7/inputs/template", json={"name": "mine"}).json()
    text = exported.text.replace("griffman1212", "another player")
    text = text.replace("--", "video: https://example.test/watch\n--")
    preview = client.post("/api/attempts/7/inputs/template/preview", json={"document": text})
    assert preview.status_code == 200
    assert preview.json()["document"] == {
        "target": "star 24 1", "strategy": "10 coin", "version": "jp",
        "author": "another player", "frames": 5, "name": None,
    }
    assert len(client.get("/api/inputs/templates").json()["templates"]) == 1
    response = client.post("/api/attempts/7/inputs/template/import",
                           json={"name": "friend's example", "document": text})
    assert response.status_code == 201
    imported = response.json()
    assert imported["author"] == "another player" and imported["frames"] == 5
    assert imported["active"] is True and imported["entity_key"] == "24-1"
    downloaded = client.get(f"/api/inputs/templates/{imported['id']}/document")
    assert downloaded.text == text.replace("--\n", "name: friend's example\n--\n", 1)
    copied_preview = client.post("/api/attempts/7/inputs/template/preview",
                                 json={"document": downloaded.text}).json()
    assert copied_preview["document"]["name"] == "friend's example"
    assert downloaded.headers["content-disposition"] == f'attachment; filename="template-{imported["id"]}.inputs.txt"'
    assert client.get("/api/attempts/7/inputs").json()["template"]["id"] == imported["id"]
    assert client.post(f"/api/inputs/templates/{original['id']}/activate").status_code == 200
    assert client.get("/api/attempts/7/inputs").json()["template"]["id"] == original["id"]
    assert client.delete(f"/api/inputs/templates/{original['id']}").status_code == 200
    assert client.get("/api/attempts/7/inputs").json()["template"] is None
    assert client.get(f"/api/inputs/templates/{original['id']}/document").status_code == 404
    assert client.delete(f"/api/inputs/templates/{original['id']}").status_code == 404


@pytest.mark.parametrize("route", [
    "/api/attempts/7/inputs/template/preview", "/api/attempts/7/inputs/template/import",
    "/api/inputs/templates",
])
def test_import_routes_refuse_bad_and_oversized_documents(client, route):
    from sm64_events.inputs.document import MAX_DOCUMENT_BYTES
    body = {"name": "bad", "document": "not a document", "kind": "star", "entity_key": "24-1"}
    assert client.post(route, json=body).status_code == 409
    body["document"] = "a" * (MAX_DOCUMENT_BYTES + 1)
    assert client.post(route, json=body).status_code == 413
    assert client.post(route, content="not json").status_code == 422
    assert client.get("/api/inputs/templates").json()["templates"] == []


def test_generic_import_remains_available_and_validated(client):
    text = client.get("/api/attempts/7/inputs/document").text
    body = {"kind": "segment", "entity_key": "99", "name": "my segment", "document": text}
    imported = client.post("/api/inputs/templates", json=body)
    assert imported.status_code == 200
    assert imported.json()["kind"] == "segment"
    body["kind"] = "anything"
    assert client.post("/api/inputs/templates", json=body).status_code == 409


def test_segment_import_uses_the_selected_local_attempt(client):
    text = client.get("/api/attempts/9/inputs/document").text.replace("segment 12", "segment 999")
    preview = client.post("/api/attempts/9/inputs/template/preview", json={"document": text}).json()
    assert preview["document"]["target"] == "segment 999"
    assert preview["destination"]["entity_key"] == "12"
    saved = client.post("/api/attempts/9/inputs/template/import",
                        json={"document": text, "name": "Their segment"}).json()
    assert (saved["kind"], saved["entity_key"], saved["strat_tag"]) == ("segment", "12", "10 coin")
    assert client.get("/api/attempts/9/inputs").json()["template"]["id"] == saved["id"]
    assert client.get("/api/attempts/7/inputs").json()["template"] is None


def test_attempt_import_routes_require_a_real_attempt(client):
    text = client.get("/api/attempts/7/inputs/document").text
    body = {"document": text, "name": "Valid file"}
    for action in ("preview", "import"):
        assert client.post(f"/api/attempts/999/inputs/template/{action}", json=body).status_code == 404
    assert client.get("/api/inputs/templates").json()["templates"] == []


def test_request_stream_limit_precedes_json_decoding(client, monkeypatch):
    monkeypatch.setattr("sm64_events.server.inputs_api.MAX_REQUEST_BYTES", 100)
    response = client.post("/api/attempts/7/inputs/template/preview", content="x" * 101)
    assert response.status_code == 413


def test_select_template_applies_the_library_choice_to_the_current_strategy(client):
    current = client.post("/api/attempts/7/inputs/template", json={}).json()
    text = client.get("/api/attempts/7/inputs/document").text
    source = client.post("/api/inputs/templates", json={
        "kind": "star", "entity_key": "24-1", "strat_tag": "other strategy",
        "document": text, "name": "other example",
    }).json()
    assert client.get("/api/attempts/7/inputs").json()["template"]["id"] == current["id"]
    body = {"template_id": source["id"]}
    response = client.post("/api/attempts/7/inputs/template/select", json=body)
    assert response.status_code == 200
    selected = response.json()
    assert selected["strat_tag"] == "10 coin"
    assert selected["author"] == "griffman1212"
    assert client.get("/api/attempts/7/inputs").json()["template"]["id"] == selected["id"]
    assert client.post("/api/attempts/7/inputs/template/select", json=body).json()["id"] == selected["id"]
    listed = client.get("/api/inputs/templates").json()["templates"]
    assert len(listed) == 3
    assert next(row for row in listed if row["id"] == source["id"])["active"] is True
    assert client.post("/api/attempts/9/inputs/template/select", json=body).status_code == 409
    assert client.post("/api/attempts/99/inputs/template/select", json=body).status_code == 404
    assert client.post("/api/attempts/7/inputs/template/select", json={"template_id": 999}).status_code == 404
