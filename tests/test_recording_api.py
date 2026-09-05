from fastapi import FastAPI
from fastapi.testclient import TestClient

from sm64_events.compare.media import RecordingMedia
from sm64_events.server.recording_api import create_recording_router
from sm64_events.server.media_api import create_media_router


class Tracker:
    def __init__(self):
        self.value = {"url": None, "revision": 0}

    def recording_link(self, attempt_id):
        if attempt_id != 1:
            raise LookupError("No such attempt")
        return self.value

    async def set_recording_link(self, attempt_id, url, expected_revision=None):
        self.recording_link(attempt_id)
        if expected_revision != self.value["revision"]:
            raise ValueError("Recording changed. Reload it before editing.")
        self.value = {"url": url, "revision": self.value["revision"] + 1}
        return self.value


def test_recording_edit_and_conditional_undo_without_capture():
    app = FastAPI()
    app.include_router(create_recording_router(Tracker()))
    client = TestClient(app)
    route = "/api/attempts/1/recording"
    assert client.get(route).json() == {"url": None, "revision": 0}
    saved = client.put(route, json={"url": "https://youtu.be/abcdefghijk", "expected_revision": 0})
    assert saved.status_code == 200 and saved.json()["revision"] == 1
    assert client.put(route, json={"url": None, "expected_revision": 0}).status_code == 409
    assert client.put(route, json={"url": None, "expected_revision": 1}).json()["url"] is None
    assert client.get("/api/attempts/2/recording").status_code == 404


def test_media_routes_keep_embed_fallback_without_ffmpeg():
    app = FastAPI()
    app.include_router(create_media_router(RecordingMedia(None, preview_probe=lambda url: {})))
    client = TestClient(app)
    url = "https://youtu.be/abcdefghijk?t=12"
    assert client.get("/api/media", params={"url": url}).json()["state"] == "missing"
    assert client.get("/api/media/preview", params={"url": url}).json()["url"] == url
    result = client.post("/api/media", json={"url": url})
    assert result.status_code == 200 and result.json()["state"] == "error"
    assert client.get("/api/media", params={"url": "file:///secret"}).status_code == 422
    assert client.get("/api/media/cache/missing.mp4").status_code == 404
