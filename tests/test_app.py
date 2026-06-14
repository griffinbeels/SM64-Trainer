# tests/test_app.py
import signal
import threading
import time

from fastapi.testclient import TestClient

from sm64_events.detectors.star_grab import StarGrabDetector
from sm64_events.server.app import (ForceExitWatchdog, create_app,
                                    install_force_exit_watchdog)
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


def test_index_serves_event_viewer():
    with make_client() as client:
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
        # New importmap shell: references app.js via importmap, not /ws/events inline
        assert "/ui/app.js" in resp.text
        assert "importmap" in resp.text


def test_health_reports_unattached():
    with make_client() as client:
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["emulator_attached"] is False
        assert body["clients"] == 0
        assert body["last_frame"] is None
        assert "memory" in body            # observability surface always present


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


# -- force-exit watchdog (CTRL+C stall incidents 2026-06-12 / 2026-06-13) ----
# Contract: the first stop signal arms a bounded force-exit so the process
# terminates even when uvicorn's connection drain wedges forever (browser
# holding a stalled connection). The graceful path must be unaffected: the
# chained handler still calls the previous one.


def _wait_for(calls: list, timeout_s: float = 2.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)


def test_watchdog_force_exits_after_deadline():
    calls: list[int] = []
    dog = ForceExitWatchdog(deadline_s=0.05, exit_fn=calls.append)
    dog.arm()
    _wait_for(calls)
    assert calls == [1]


def test_watchdog_arm_is_idempotent():
    calls: list[int] = []
    dog = ForceExitWatchdog(deadline_s=0.05, exit_fn=calls.append)
    dog.arm()
    dog.arm()  # second CTRL+C must not start a second timer
    _wait_for(calls)
    time.sleep(0.1)  # would catch a late second fire
    assert calls == [1]


def test_install_chains_previous_handler_and_arms():
    saved = {s: signal.getsignal(getattr(signal, s))
             for s in ("SIGINT", "SIGTERM", "SIGBREAK")
             if hasattr(signal, s)}
    try:
        prev_calls: list[tuple] = []
        signal.signal(signal.SIGINT, lambda s, f: prev_calls.append((s, f)))
        exits: list[int] = []
        dog = ForceExitWatchdog(deadline_s=0.05, exit_fn=exits.append)
        assert install_force_exit_watchdog(dog) is True
        chained = signal.getsignal(signal.SIGINT)
        chained(signal.SIGINT, None)  # what CTRL+C delivers
        assert prev_calls == [(signal.SIGINT, None)]  # graceful path intact
        _wait_for(exits)
        assert exits == [1]  # and the hard deadline was armed
    finally:
        for name, handler in saved.items():
            signal.signal(getattr(signal, name), handler)


def test_install_is_noop_off_main_thread():
    before = signal.getsignal(signal.SIGINT)
    result: list[bool] = []
    t = threading.Thread(
        target=lambda: result.append(install_force_exit_watchdog()))
    t.start()
    t.join()
    assert result == [False]
    assert signal.getsignal(signal.SIGINT) is before
