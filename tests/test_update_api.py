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

    def status(self, force=False, repair=False):
        return {"current": "1.0.0", "frozen": True, "update_available": True,
                "latest": "2.0.0", "notes": "n", "html_url": "h",
                "skipped": self.skipped, "writable": True,
                "state": "idle", "progress": 0.0, "force": force, "repair": repair}

    def begin_apply(self, on_success, repair=False, can_apply=None):
        self.applied_with = on_success
        on_success()                 # simulate immediate success
        return {"state": "downloading"}

    def skip(self, version):
        self.skipped = version

    def beta_preferences(self):
        return {"receive_beta_updates": getattr(self, "beta", False)}

    def set_beta_updates(self, enabled):
        self.beta = enabled
        return {**self.status(force=True), **self.beta_preferences()}


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


def test_preferences_default_off_and_toggle_forces_eligible_check():
    with _client(FakeUpdater()) as client:
        assert client.get("/api/update/preferences").json()["receive_beta_updates"] is False
        enabled = client.put("/api/update/preferences", json={"receive_beta_updates": True}).json()
        assert enabled["receive_beta_updates"] is True
        assert enabled["force"] is True
        assert client.put("/api/update/preferences", json={"receive_beta_updates": False}).json()["receive_beta_updates"] is False


def test_preferences_reject_coercion_and_unrecognized_fields():
    with _client(FakeUpdater()) as client:
        for body in ({"receive_beta_updates": "false"}, {"receive_beta_updates": 1},
                     {"receive_beta_updates": True, "installed_version": "0.0.0"}):
            assert client.put("/api/update/preferences", json=body).status_code == 422
        assert client.post("/api/update/skip", json={"version": "invalid"}).status_code == 422


def test_explicit_repair_reaches_service():
    with _client(FakeUpdater()) as client:
        assert client.get("/api/update/status?force=1&repair=1").json()["repair"] is True


def test_active_gameplay_refuses_update_before_worker_starts():
    from fastapi import FastAPI
    from sm64_events.server.update_api import create_update_router
    updater = FakeUpdater()
    app = FastAPI()
    app.include_router(create_update_router(updater, lambda: None,
        can_apply=lambda: "Pause the trainer before updating."))
    with TestClient(app) as client:
        response = client.post("/api/update/apply")
        assert response.status_code == 409
        assert response.json()["detail"] == "Pause the trainer before updating."
        assert updater.applied_with is None


def test_preferences_busy_refusal_is_a_readable_conflict():
    updater = FakeUpdater()
    def refuse(enabled):
        raise ValueError("Wait for the current update to finish.")
    updater.set_beta_updates = refuse
    with _client(updater) as client:
        response = client.put("/api/update/preferences", json={"receive_beta_updates": True})
        assert response.status_code == 409
        assert response.json()["detail"] == "Wait for the current update to finish."


def test_recovery_confirmation_waits_for_first_healthy_request_and_runs_once():
    from types import SimpleNamespace
    updater = FakeUpdater()
    calls = []
    updater.startup_maintenance = lambda **kwargs: calls.append(kwargs)
    with _client(updater) as client:
        assert calls == []  # Startup can still fail while binding the port.
        client.get("/")
        assert calls == []
        client.app.state.poll_supervisor = SimpleNamespace(health=lambda: {"state": "failed", "error": "failed"})
        assert client.get("/health").json()["status"] == "error"
        assert calls == []
        del client.app.state.poll_supervisor
        assert client.get("/health").json()["status"] == "ok"
        client.get("/health")
        assert calls == [{"bootstrap_path": None}]


def test_afk_is_not_permission_to_replace_app_files():
    from types import SimpleNamespace
    from fastapi import FastAPI
    from sm64_events.server.app import _mount_optional_routes
    updater = FakeUpdater()
    updater.begin_apply = lambda **kwargs: {"state": "downloading"}
    poller = SimpleNamespace(memory=SimpleNamespace(attached=True), paused=False)
    app = FastAPI()
    _mount_optional_routes(app, None, None, None, None, None, updater, None, None, poller)
    with TestClient(app) as client:
        assert client.post("/api/update/apply").status_code == 409
        poller.paused = True
        assert client.post("/api/update/apply").status_code == 200
