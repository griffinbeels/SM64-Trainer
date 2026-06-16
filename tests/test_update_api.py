import threading
import time

from fastapi.testclient import TestClient

from sm64_events.detectors.star_grab import StarGrabDetector
from sm64_events.server.app import create_app
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.poller import Poller


class OfflineMemory:
    attached = False

    def attach(self):
        return False

    def detach(self):
        pass


class FakeUpdater:
    def __init__(self):
        self.skipped = None
        self.applied_with = None

    def status(self, force=False):
        return {"current": "1.0.0", "frozen": True, "update_available": True,
                "latest": "2.0.0", "notes": "n", "html_url": "h",
                "skipped": self.skipped, "writable": True,
                "state": "idle", "progress": 0.0, "force": force}

    def begin_apply(self, on_success):
        self.applied_with = on_success
        on_success()                 # simulate immediate success
        return {"state": "downloading"}

    def skip(self, version):
        self.skipped = version


def _client(updater):
    poller = Poller(OfflineMemory(), [StarGrabDetector()], Broadcaster())
    app = create_app(poller, Broadcaster(), updater=updater)
    return TestClient(app)


def _wait(flag, timeout=2.0):
    end = time.monotonic() + timeout
    while not flag and time.monotonic() < end:
        time.sleep(0.01)


def test_status_returns_service_payload():
    with _client(FakeUpdater()) as c:
        body = c.get("/api/update/status").json()
        assert body["update_available"] is True
        assert body["latest"] == "2.0.0"


def test_status_passes_force():
    with _client(FakeUpdater()) as c:
        assert c.get("/api/update/status?force=1").json()["force"] is True


def test_skip_records_version():
    up = FakeUpdater()
    with _client(up) as c:
        resp = c.post("/api/update/skip", json={"version": "2.0.0"})
        assert resp.status_code == 200
        assert up.skipped == "2.0.0"


def test_apply_triggers_restart_callback():
    up = FakeUpdater()
    with _client(up) as c:
        called: list[bool] = []
        c.app.state.request_restart = lambda: called.append(True)
        resp = c.post("/api/update/apply")
        assert resp.status_code == 200
        assert resp.json()["state"] == "downloading"
        _wait(called)
        assert called == [True]      # on_success -> app.state.request_restart
