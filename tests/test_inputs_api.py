"""The captured-input routes are a skin over `InputsService`: every route
asks the service and maps its exceptions (404 / 409 / 503), nothing more."""
from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sm64_events.inputs.frame import InputFrame
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


@pytest.fixture
def client(tmp_path):
    db = Database(tmp_path / "t.db")
    session = db.insert_session(AT)
    db.inputs.append(session, [(100 + step, InputFrame(0x8000, 0, 40, 0))
                               for step in range(5)], AT, LATER)
    captured = FakeAttempt(id=7)
    silent = FakeAttempt(id=8, started_utc="2026-08-21T09:00:00+00:00",
                         ended_utc="2026-08-21T09:00:30+00:00")
    service = InputsService(db.inputs, db.input_templates,
                            lambda: [captured, silent], version="jp")
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
    assert "0-4       A        +40,+0" in text


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
