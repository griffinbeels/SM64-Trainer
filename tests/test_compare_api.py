from fastapi import FastAPI
from fastapi.testclient import TestClient

from sm64_events.server.compare_api import create_compare_router


class _FakeService:
    def __init__(self): self.deleted = []; self.uploads = []
    def view(self, entity, strat):
        return {"entity": entity, "strat": strat, "saved": [], "auto": None,
                "suggestion": None}
    def start_import(self, **kw): return "job123"
    def start_upload(self, **kw):
        self.uploads.append(kw)
        return "job456"
    def import_status(self, job_id):
        if job_id != "job123":
            raise LookupError("no such import job")
        return {"state": "done", "progress": 1.0, "message": "done",
                "comparison": {"id": 1}}
    async def update(self, comp_id, **fields):
        return {"id": comp_id, **fields}
    async def delete(self, comp_id):
        self.deleted.append(comp_id)


def _client(svc):
    app = FastAPI()
    app.include_router(create_compare_router(svc))
    return TestClient(app)


def test_view_endpoint():
    c = _client(_FakeService())
    r = c.get("/api/compare/view", params={"entity": "star:7:0",
                                           "strat": "Ledgegrab"})
    assert r.status_code == 200 and r.json()["strat"] == "Ledgegrab"


def test_import_returns_job_then_status():
    c = _client(_FakeService())
    r = c.post("/api/compare/import", json={"entity_key": "star:7:0",
        "strat": "L", "name": "n", "source_kind": "file", "source_ref": "/v"})
    assert r.json()["job_id"] == "job123"
    s = c.get("/api/compare/import/job123")
    assert s.json()["state"] == "done"
    assert c.get("/api/compare/import/nope").status_code == 404


def test_put_and_delete():
    svc = _FakeService()
    c = _client(svc)
    r = c.put("/api/compare/videos/5", json={"in_frame": 90, "touch": True})
    assert r.json()["in_frame"] == 90
    assert c.delete("/api/compare/videos/5").json()["ok"] is True
    assert svc.deleted == [5]


def test_upload_route():
    svc = _FakeService()
    c = _client(svc)
    r = c.post("/api/compare/upload", params={"entity_key": "star:7:0",
        "strat": "L", "name": "n", "filename": "clip.mp4"}, content=b"rawbytes")
    assert r.status_code == 200
    assert r.json()["job_id"] == "job456"
    assert len(svc.uploads) == 1
    assert svc.uploads[0]["data"] == b"rawbytes"
    assert svc.uploads[0]["filename"] == "clip.mp4"
