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
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("SM64_SENTRY_LIVE_VERIFY") != "1",
                                reason="explicit live Sentry verification only")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from ui_fixture import _FixtureCaptureLayer, serve_ui


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
