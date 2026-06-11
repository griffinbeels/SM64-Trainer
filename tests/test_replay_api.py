from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sm64_events.server.replay_api import create_replay_router


class FakeReplayService:
    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
    def status(self):
        return {"enabled": True, "recording": True, "window_found": True,
                "audio_mode": "process", "encoder": "libx264",
                "buffer_start_utc": None, "buffer_end_utc": None,
                "disk_bytes": 0}
    def view(self, attempt_id: int):
        if attempt_id == 404:
            raise LookupError("no attempt")
        if attempt_id == 409:
            raise ValueError("no footage")
        if attempt_id == 503:
            raise RuntimeError("database unavailable")
        return {"clip_url": "/api/replay/clips/clip_attempt_42.mp4",
                "duration_s": 4.0, "truncated": False}
    def save(self, attempt_id: int):
        if attempt_id == 404:
            raise LookupError("no attempt")
        return {"path": "replays/2026-06-11/session_3/x.mp4", "truncated": False}
    def reveal(self, path: str) -> None:
        if path == "BAD":
            raise LookupError("no such saved replay")

    def clip_path(self, name: str) -> Path:
        if name != "clip_attempt_42.mp4":
            raise LookupError("no such clip")
        p = self.tmp / name
        if not p.exists():
            p.write_bytes(b"\x00" * 2048)
        return p


def make_client(tmp_path):
    app = FastAPI()
    app.include_router(create_replay_router(FakeReplayService(tmp_path)))
    return TestClient(app)


def test_status(tmp_path):
    r = make_client(tmp_path).get("/api/replay/status")
    assert r.status_code == 200 and r.json()["recording"] is True


def test_view_maps_error_taxonomy(tmp_path):
    c = make_client(tmp_path)
    assert c.post("/api/attempts/1/replay").status_code == 200
    assert c.post("/api/attempts/404/replay").status_code == 404
    assert c.post("/api/attempts/409/replay").status_code == 409
    assert c.post("/api/attempts/503/replay").status_code == 503


def test_clip_serving_supports_range(tmp_path):
    c = make_client(tmp_path)
    r = c.get("/api/replay/clips/clip_attempt_42.mp4",
              headers={"Range": "bytes=0-99"})
    assert r.status_code == 206                      # partial content = scrubbable
    assert r.headers["content-type"] == "video/mp4"
    assert "accept-ranges" in {k.lower() for k in r.headers}
    assert c.get("/api/replay/clips/evil.txt").status_code == 404


def test_save_passes_truncated_through(tmp_path):
    r = make_client(tmp_path).post("/api/attempts/1/replay/save")
    assert r.status_code == 200
    body = r.json()
    assert body["path"].endswith(".mp4") and body["truncated"] is False


def test_reveal_endpoint(tmp_path):
    c = make_client(tmp_path)
    r = c.post("/api/replay/reveal", json={"path": "replays/x.mp4"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    r = c.post("/api/replay/reveal", json={"path": "BAD"})
    assert r.status_code == 404
