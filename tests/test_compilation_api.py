from fastapi import FastAPI
from fastapi.testclient import TestClient

from sm64_events.server.compilation_api import create_compilation_router
from sm64_events.tracking.compilation import EntityRef


class FakeService:
    def __init__(self):
        self.started = None

    def start(self, identity, x_before, y_after):
        if x_before < 0 or y_after < 0:
            raise ValueError("x_before and y_after must be >= 0")
        self.started = (identity, x_before, y_after)
        return "job123"

    def status(self, job_id):
        if job_id != "job123":
            raise LookupError("no such compilation job")
        return {"state": "done", "result": {"path": "x.mp4"}}


def _client(svc):
    app = FastAPI()
    app.include_router(create_compilation_router(svc))
    return TestClient(app)


def test_star_body_dispatches_to_star_identity():
    svc = FakeService()
    r = _client(svc).post("/api/compilation",
                          json={"star": {"course_id": 1, "star_id": 0},
                                "x_before": 5, "y_after": 3})
    assert r.status_code == 200
    assert r.json()["job_id"] == "job123"
    assert svc.started[0] == EntityRef(course_id=1, star_id=0)


def test_segment_body_dispatches_to_segment_identity():
    svc = FakeService()
    r = _client(svc).post("/api/compilation",
                          json={"segment_id": 9, "x_before": 4, "y_after": 2})
    assert r.status_code == 200
    assert svc.started[0] == EntityRef(segment_id=9)


def test_both_kinds_is_409():
    r = _client(FakeService()).post(
        "/api/compilation",
        json={"star": {"course_id": 1, "star_id": 0}, "segment_id": 9})
    assert r.status_code == 409


def test_neither_kind_is_409():
    r = _client(FakeService()).post("/api/compilation", json={})
    assert r.status_code == 409


def test_negative_window_is_409():
    r = _client(FakeService()).post(
        "/api/compilation",
        json={"segment_id": 9, "x_before": -1, "y_after": 2})
    assert r.status_code == 409


def test_status_known_and_unknown():
    c = _client(FakeService())
    assert c.get("/api/compilation/job123").json()["state"] == "done"
    assert c.get("/api/compilation/nope").status_code == 404
