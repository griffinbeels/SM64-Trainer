"""Opt-in vendor smoke through normal startup and the real installer API.

This sends one deliberate technical failure to the configured Sentry project.
It never attaches real emulator memory, recordings, user history or raw logs.
Run only with SM64_SENTRY_LIVE_VERIFY=1; normal test runs skip this module.
"""
import errno
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace as NS
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("SM64_SENTRY_LIVE_VERIFY") != "1",
                                reason="explicit live Sentry verification only")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from ui_fixture import _FixtureCaptureLayer, serve_ui


def _synthetic_snapshot(app):
    snapshot = app.state.diagnostic_snapshot
    snapshot._sources.update(
        capture=lambda: {"recorder": {"recording": False, "pictures_fed": 0},
                         "gpu": {"latest": {"stage": "channel", "native_reason": 0}}},
        ghost=lambda: {"enabled": True, "has_target": True, "label": "NEVER_SEND_PLAYER",
                       "native": {"state": 5, "reason": 8, "displayed": False}},
        setup=lambda: {"state": "needs_restart", "runtime": {"rom": {"region": "jp"},
                        "checks": {"pictures": False, "game": True}}},
        pipeline=lambda: {"inputs": {"frames": 123, "sink_failures": 0}},
        resources=lambda: {"rss_mib": 100})
    snapshot._native = NS(snapshot=lambda layer: {"available": False}, close=lambda: None)
    return app


def test_real_startup_and_failed_install_reach_sentry(tmp_path, monkeypatch):
    from sm64_events.core import error_reporting, reporting_setup
    from sm64_events.core.logging_setup import configure_logging
    config = Path(__file__).resolve().parents[1] / "src/sm64_events/data/error-reporting.json"
    dsn = reporting_setup.read_config_dsn(config)
    assert dsn, "Configure a public Sentry project before explicitly running this test."
    marker = "SM64 Trainer Sentry verification " + time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    monkeypatch.setenv("SM64_SENTRY_DSN", dsn)
    monkeypatch.setattr(reporting_setup, "_started", False)
    monkeypatch.setattr("sm64_events.core.paths.data_root", lambda: tmp_path)
    configure_logging(tmp_path / "logs")
    assert error_reporting.status()["configured"] is True
    captured = []
    original_capture = error_reporting.capture_exception

    def observe_capture(error, **kwargs):
        event_id = original_capture(error, **kwargs)
        captured.append(event_id)
        return event_id

    def deliberate_install_failure(self, consent):
        raise OSError(errno.EIO, marker)

    monkeypatch.setattr(error_reporting, "capture_exception", observe_capture)
    monkeypatch.setattr(_FixtureCaptureLayer, "install", deliberate_install_failure)
    import ui_fixture
    create = ui_fixture.create_app
    monkeypatch.setattr(ui_fixture, "create_app", lambda *args, **kwargs: _synthetic_snapshot(create(*args, **kwargs)))
    try:
        with serve_ui(db_path=tmp_path / "isolated.db", seed=False) as url:
            request = Request(url + "/api/setup/capture-layer", data=b'{"consent":true}',
                              headers={"Content-Type": "application/json"}, method="POST")
            with pytest.raises(HTTPError) as result:
                urlopen(request, timeout=10)
            assert result.value.code == 409
            assert marker in json.loads(result.value.read())["detail"]
            assert len(captured) == 1 and captured[0]
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and error_reporting.status().get("sent", 0) < 1:
                time.sleep(0.1)
            status = error_reporting.status()
            assert status["sent"] >= 1, status
            receipt = {"marker": marker, "event_id": captured[0],
                       "entry": "configure_logging -> initialize_reporting -> setup capture-layer API",
                       "http_status": 409, "reporter_status": status}
            (tmp_path / "sentry-verification-receipt.json").write_text(json.dumps(receipt, indent=2))
            print(json.dumps(receipt))
    finally:
        error_reporting.shutdown()


def _initialize_progress_probe(tmp_path, monkeypatch, marker):
    from sm64_events.core import build_channel, error_reporting, reporting_setup, version
    from sm64_events.core.logging_setup import configure_logging
    config = Path(__file__).resolve().parents[1] / "src/sm64_events/data/error-reporting.json"
    dsn = reporting_setup.read_config_dsn(config)
    assert dsn
    monkeypatch.setenv("SM64_SENTRY_DSN", dsn)
    monkeypatch.setattr(reporting_setup, "_started", False)
    monkeypatch.setattr("sm64_events.core.paths.data_root", lambda: tmp_path)
    monkeypatch.setattr("sm64_events.core.paths.migrate_legacy_data_dir", lambda: None)
    monkeypatch.setattr(build_channel, "current_build", lambda: build_channel.BuildChannel(
        installation="normal", channel="beta", build_id=marker))
    monkeypatch.setattr(version, "__version__", "1.10.0-beta.26")
    configure_logging(tmp_path / "logs")
    assert error_reporting.status()["configured"] and error_reporting.status()["enabled"]


def test_real_active_progress_watch_reaches_sentry_and_optout_stops_it(tmp_path, monkeypatch):
    """Normal reporting init and real app lifespan; only device facts are inert."""
    from sm64_events.core import error_reporting
    from sm64_events.core.silent_failures import SilentFailureMonitor
    import sm64_events.server.diagnostics_runtime as runtime
    import ui_fixture

    marker = "beta26-progress-verification-" + time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    _initialize_progress_probe(tmp_path, monkeypatch, marker)
    captured = []
    original_capture, create = error_reporting.capture_problem, ui_fixture.create_app
    state = {"frame": 0, "pictures": 0, "scope": 1}

    def observe(message, **kwargs):
        event_id = original_capture(message, **kwargs)
        captured.append(event_id)
        return event_id

    def progress():
        state["frame"] += 30
        return {"game": {"attached": True, "paused": False, "held": False,
                         "frame": state["frame"], "scope": state["scope"]},
                "capture": {"recording_enabled": True, "paused": False, "gated": False,
                            "storage_pressure": False, "player_active": True,
                            "pictures": state["pictures"], "scope": state["scope"]}}

    def fixture_app(*args, **kwargs):
        app = create(*args, **kwargs)
        snapshot = app.state.diagnostic_snapshot
        snapshot._progress = progress
        snapshot._sources.update(capture=lambda: {"recorder": {"recording_enabled": True,
            "recording": False, "pictures_fed": 0, "window_found": True, "frame_source": "plugin"}},
            ghost=lambda: {"enabled": False}, setup=lambda: {"state": "ready"},
            pipeline=lambda: {"emulator_attached": True, "paused": False})
        snapshot._native = NS(snapshot=lambda layer: {"available": False}, close=lambda: None)
        return app

    monkeypatch.setattr(error_reporting, "capture_problem", observe)
    # Test-only timing; the actual app lifespan starts/stops the real observer.
    monkeypatch.setattr(runtime, "SilentFailureMonitor", lambda probe: SilentFailureMonitor(
        probe, reporter=observe, interval_s=0.1, grace_s=0.3))
    monkeypatch.setattr(ui_fixture, "create_app", fixture_app)
    try:
        with serve_ui(db_path=tmp_path / "isolated.db", from_dev_db=False, seed=False) as url:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and error_reporting.status().get("sent", 0) < 1:
                time.sleep(0.1)
            assert len(captured) == 1 and captured[0]
            assert error_reporting.status()["sent"] >= 1
            request = Request(url + "/api/diagnostics/reporting", data=b'{"enabled":false}',
                              headers={"Content-Type": "application/json"}, method="PUT")
            with urlopen(request, timeout=10) as response:
                assert json.loads(response.read())["enabled"] is False
            state["scope"] += 1
            time.sleep(0.7)
            assert captured == [captured[0]] and error_reporting.status()["enabled"] is False
            receipt = {"marker": marker, "event_id": captured[0],
                "entry": "configure_logging -> initialize_reporting -> actual create_app lifespan -> cached progress watchdog -> PersistentTransport",
                "optout_stops_reports": True, "new_profile_default_enabled": True,
                "reporter_sent": error_reporting.status()["sent"]}
            (tmp_path / "sentry-progress-verification-receipt.json").write_text(json.dumps(receipt, indent=2))
            print(json.dumps(receipt))
    finally:
        error_reporting.shutdown()
