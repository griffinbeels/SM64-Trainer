# tests/test_app.py
from fastapi.testclient import TestClient

from sm64_events.detectors.star_grab import StarGrabDetector
from sm64_events.server.app import create_app
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.poller import Poller


class OfflineMemory:
    """Never attaches — keeps the poll loop idling during endpoint tests."""
    attached = False

    def attach(self):
        return False

    def detach(self):
        pass


def make_client() -> TestClient:
    broadcaster = Broadcaster()
    poller = Poller(OfflineMemory(), [StarGrabDetector()], broadcaster)
    app = create_app(poller, broadcaster, debug_hooks=True)
    return TestClient(app)


def test_health_reports_unattached():
    with make_client() as client:
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["emulator_attached"] is False
        assert body["clients"] == 0
        assert body["last_frame"] is None


def test_state_is_null_before_first_snapshot():
    with make_client() as client:
        assert client.get("/state").json() == {"snapshot": None}


def test_websocket_receives_published_events():
    with make_client() as client:
        with client.websocket_connect("/ws/events") as ws:
            client.post("/debug/emit")
            msg = ws.receive_json()
            assert msg["v"] == 1
            assert msg["seq"] == 1
            assert msg["type"] == "debug"
