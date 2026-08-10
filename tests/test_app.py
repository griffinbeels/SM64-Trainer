# tests/test_app.py
import json
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


def test_refresh_applies_the_humans_audit_corrections(tmp_path, monkeypatch):
    """A live POST /api/library/refresh must apply the SAME audit corrections
    tools/scrape_sheet.py bakes into the bundled snapshot (library/audit.py's
    load_overrides over core.paths.bundled_library_overrides()) -- without
    them a refresh un-corrects every row the human fixed by hand, and (a
    refreshed copy always carries a newer sheet_revision) the un-corrected
    copy then wins over the bundled, corrected one until the next release."""
    import sm64_events.core.paths as paths_mod
    from sm64_events.library.build import SCHEMA_VERSION

    overrides_path = tmp_path / "library_overrides.json"
    written = {"targets": {}, "rows": {"some-row-key": {"kind": "approach"}}}
    overrides_path.write_text(json.dumps(written), encoding="utf-8")
    monkeypatch.setattr(paths_mod, "bundled_library_overrides",
                        lambda: overrides_path)

    captured = {}

    def fake_build(data, fetched_at, overrides=None):
        captured["overrides"] = overrides
        # Deliberately OLDER than the real bundled snapshot, so refresh()
        # returns before ever writing to the real (cwd-relative) local path.
        return {"schema_version": SCHEMA_VERSION,
                "sheet_revision": "2000-01-01T00:00:00", "fetched_at": fetched_at,
                "runners": [], "ladder_model": {}, "targets": []}

    monkeypatch.setattr("sm64_events.library.build.build", fake_build)
    monkeypatch.setattr("sm64_events.library.ladders.fit_payload", lambda p: p)
    monkeypatch.setattr("sm64_events.server.library_api.fetch", lambda: b"stub")

    with make_client() as client:
        resp = client.post("/api/library/refresh")
    assert resp.status_code == 200
    assert resp.json()["applied"] is False       # never wrote to a real path
    assert captured["overrides"] == written


def test_websocket_receives_published_events():
    with make_client() as client:
        with client.websocket_connect("/ws/events") as ws:
            client.post("/debug/emit")
            msg = ws.receive_json()
            assert msg["v"] == 1
            assert msg["seq"] == 1
            assert msg["type"] == "debug"


# -- db reattach loop (post-update broadcast-only incident 2026-07-23) -------
# Contract: a server that started without the db (instance lock still held
# by the exiting old process) must upgrade itself to full tracking once the
# lock frees — /health flips db "error" -> "ok" and /api/session serves.


def test_db_reattach_upgrades_broadcast_only(tmp_path, monkeypatch):
    import sm64_events.server.app as app_mod
    from sm64_events.storage.db import Database
    from sm64_events.tracking.service import TrackerService

    monkeypatch.setattr(app_mod, "_DB_RETRY_INTERVAL_S", 0.01)
    broadcaster = Broadcaster()
    svc = TrackerService(None, broadcaster)
    poller = Poller(OfflineMemory(), [StarGrabDetector()], svc)
    attempts_before_free = 3
    calls: list[int] = []

    def db_retry():
        calls.append(1)
        if len(calls) < attempts_before_free:
            return None            # lock still held by the old process
        return Database(tmp_path / "t.db")

    app = create_app(poller, broadcaster, service=svc, db_retry=db_retry)
    with TestClient(app) as client:
        assert client.get("/health").json()["db"] == "error"
        deadline = time.monotonic() + 5.0
        while (time.monotonic() < deadline
               and client.get("/health").json()["db"] != "ok"):
            time.sleep(0.02)
        assert client.get("/health").json()["db"] == "ok"
        assert client.get("/api/session").status_code == 200
    assert len(calls) >= attempts_before_free


def test_db_retry_exception_ends_loop_and_stays_degraded(tmp_path, monkeypatch):
    """The retry exists ONLY for the lock race — a broken database must not
    be re-tried in a hot loop forever."""
    import sm64_events.server.app as app_mod
    from sm64_events.tracking.service import TrackerService

    monkeypatch.setattr(app_mod, "_DB_RETRY_INTERVAL_S", 0.01)
    broadcaster = Broadcaster()
    svc = TrackerService(None, broadcaster)
    poller = Poller(OfflineMemory(), [StarGrabDetector()], svc)
    calls: list[int] = []

    def db_retry():
        calls.append(1)
        raise RuntimeError("db is broken")

    app = create_app(poller, broadcaster, service=svc, db_retry=db_retry)
    with TestClient(app) as client:
        time.sleep(0.2)
        assert client.get("/health").json()["db"] == "error"
    assert len(calls) == 1


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


def test_admin_shutdown_invokes_state_callback():
    with make_client() as client:
        called: list[bool] = []
        client.app.state.request_shutdown = lambda: called.append(True)
        resp = client.post("/api/admin/shutdown")
        assert resp.status_code == 200
        assert resp.json() == {"shutting_down": True}
        _wait_for(called)
        assert called == [True]


def test_admin_shutdown_fallback_raises_sigint(monkeypatch):
    raised: list[int] = []
    monkeypatch.setattr("sm64_events.server.app.signal.raise_signal",
                        raised.append)
    with make_client() as client:
        if hasattr(client.app.state, "request_shutdown"):
            delattr(client.app.state, "request_shutdown")
        assert client.post("/api/admin/shutdown").status_code == 200
        _wait_for(raised)
        assert raised == [signal.SIGINT]


def test_admin_restart_invokes_state_callback():
    with make_client() as client:
        called: list[bool] = []
        client.app.state.request_restart = lambda: called.append(True)
        resp = client.post("/api/admin/restart")
        assert resp.status_code == 200
        assert resp.json() == {"restarting": True}
        _wait_for(called)
        assert called == [True]


def test_admin_restart_fallback_relaunches(monkeypatch):
    spawned: list[bool] = []
    monkeypatch.setattr("sm64_events.server.app.spawn_replacement",
                        lambda: spawned.append(True))
    monkeypatch.setattr("sm64_events.server.app.signal.raise_signal",
                        lambda s: None)
    with make_client() as client:
        if hasattr(client.app.state, "request_restart"):
            delattr(client.app.state, "request_restart")
        assert client.post("/api/admin/restart").status_code == 200
        _wait_for(spawned)
        assert spawned == [True]
