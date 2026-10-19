"""Exercise the shipped Settings and continuous install state offline."""
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from find_uilab import find_uilab

_missing = find_uilab()
if _missing:
    pytest.skip(_missing, allow_module_level=True)

from uilab.driver import get_driver
import ui_fixture
from ui_fixture import serve_ui
from test_ui_setup_modal import (INSTALLED, click_text, connect, scenario,
                                 wait_page, wait_step, wait_until)


def test_debug_report_button_exports_the_recording_route_and_retained_failure(tmp_path, monkeypatch):
    """The tester's existing gesture reaches the new cached evidence."""
    import sm64_events.server.app as app_module
    from sm64_events.replay.recorder import ReplayRecorder

    recorder = ReplayRecorder.__new__(ReplayRecorder)
    recorder.status = lambda **kw: {"recording": True, "frame_source": "desktop"}
    recorder._capture_diagnostics = lambda: {
        "first_failure": {"stage": "encoder", "encoder_backend": "windows",
                          "gpu_vendor_id": 0x1002, "native_hresult": 0xC00D36B4},
        "latest": {"stage": "encoder"},
    }
    mount = app_module._mount_diagnostics

    def with_cached_evidence(app, poller, broadcaster, service, monitor, *args):
        mount(app, poller, broadcaster, service, monitor,
              replay=SimpleNamespace(recorder=recorder),
              capture_layer=SimpleNamespace(diagnostic_status=lambda: {"state": "needs_restart"}))

    monkeypatch.setattr(app_module, "_mount_diagnostics", with_cached_evidence)
    monkeypatch.setattr(app_module, "diagnostics_dir", lambda: tmp_path / "reports")
    create = ui_fixture.create_app
    revealed = []

    def fixture_app(*args, **kwargs):
        app = create(*args, **kwargs)

        @app.post("/api/replay/reveal")
        def reveal(body: dict):
            revealed.append(Path(body["path"]))  # Observe the gesture without opening Explorer.
            return {"ok": True}

        return app

    monkeypatch.setattr(ui_fixture, "create_app", fixture_app)
    with serve_ui(db_path=tmp_path / "report.db") as url, get_driver().launch(viewport=(1280, 900)) as page:
        page.goto(url + "/ui/index.html")
        page.wait_for('.settings-link[aria-label="Settings"]')
        page.click('.settings-link[aria-label="Settings"]')
        page.wait_for(".settings-drawer")
        page.click('.settings-drawer button[title^="Write a debug report"]')
        wait_until(page, "document.querySelector('.settings-drawer').textContent.includes('Report saved:')", "report is saved")
        assert len(revealed) == 1 and revealed[0].is_file()
        report = revealed[0].read_text(encoding="utf-8")
        assert "desktop_time_estimate" in report and "first_failure" in report
        assert "windows" in report and "3222091444" in report
        assert "needs_restart" in report
        assert page.problems() == []
        (tmp_path / "debug-report-saved.png").write_bytes(page.screenshot())


@pytest.mark.parametrize("width", [850, 1280])
def test_reporting_setting_and_automatic_browser_capture(tmp_path, monkeypatch, width):
    from sm64_events.core import error_reporting as reports
    current = {"configured": True, "enabled": True, "pending": 0}
    captured = []
    monkeypatch.setattr(reports, "status", lambda: dict(current))
    monkeypatch.setattr(reports, "set_enabled", lambda value: current.update(
        enabled=value, preference_error=not value))
    monkeypatch.setattr(reports, "capture_problem", lambda message, **kw: captured.append((message, kw)))
    with serve_ui(db_path=tmp_path / "ui.db") as url, get_driver().launch(viewport=(width, 900)) as page:
        page.goto(url + "/ui/index.html")
        page.wait_for('.settings-link[aria-label="Settings"]')
        page.click('.settings-link[aria-label="Settings"]')
        page.wait_for("#automatic-error-reports:not(:disabled)")
        assert page.evaluate("document.querySelector('#automatic-error-reports').checked") is True
        assert page.problems() == []
        (tmp_path / f"reporting-{width}.png").write_bytes(page.screenshot())
        page.click("#automatic-error-reports")
        wait_until(page, "!document.querySelector('#automatic-error-reports').checked", "reporting is disabled")
        assert current["enabled"] is False
        assert "could not be saved" in page.evaluate("document.querySelector('.settings-drawer').innerText")
        page.click('.settings-head button[aria-label="Close settings"]')
        page.click('.settings-link[aria-label="Settings"]')
        page.wait_for("#automatic-error-reports:not(:disabled)")
        assert "could not be saved" in page.evaluate("document.querySelector('.settings-drawer').innerText")
        page.click("#automatic-error-reports")
        wait_until(page, "document.querySelector('#automatic-error-reports').checked", "reporting is enabled")
        assert "could not be saved" not in page.evaluate("document.querySelector('.settings-drawer').innerText")
        page.evaluate("setTimeout(() => { throw new Error('Synthetic browser capture control'); }, 0)")
        for _ in range(100):
            if captured:
                break
            page.wait_ms(25)
        assert len(captured) == 1
        assert captured[0][0] == "Synthetic browser capture control"
        assert captured[0][1]["context"]["component"] == "browser"
        assert "Synthetic browser capture control" in "\n".join(page.problems())
        assert page.problems() == []


def test_install_stays_pending_until_the_permission_retry_finishes(tmp_path, monkeypatch):
    entered, finish = threading.Event(), threading.Event()
    original = ui_fixture._FixtureCaptureLayer.install

    def pending_install(self, consent):
        entered.set()
        if not finish.wait(15):
            raise TimeoutError("synthetic elevation timed out")
        return original(self, consent)

    monkeypatch.setattr(ui_fixture._FixtureCaptureLayer, "install", pending_install)
    layer, observed = scenario()
    connect(layer, observed)
    with serve_ui(db_path=tmp_path / "setup.db", capture_layer_status=layer,
                  capture_layer_install=INSTALLED, setup_observer=lambda _: observed) as url, \
            get_driver().launch(viewport=(850, 900)) as page:
        try:
            page.goto(url + "/ui/index.html")
            wait_page(page, "platform")
            page.click(".setup-platform-choice:first-child")
            wait_page(page, "install")
            wait_step(page, "close")
            layer["pj64_running"] = False
            wait_step(page, "install")
            click_text(page, "Install Practice Replay")
            assert entered.wait(5)
            page.wait_ms(500)
            assert "Installing" in page.evaluate("document.querySelector('.setup-modal').innerText")
            assert page.evaluate("document.querySelector('.setup-install-action button').disabled") is True
            assert "failed" not in page.evaluate("document.querySelector('.setup-modal').innerText").lower()
            (tmp_path / "install-pending-850.png").write_bytes(page.screenshot())
            finish.set()
            wait_step(page, "reopen")
            assert page.problems() == []
        finally:
            finish.set()
