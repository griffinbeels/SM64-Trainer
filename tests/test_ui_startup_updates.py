"""Update-before-sign-in through the real app and token-free remote seams."""
import threading
from pathlib import Path

import pytest

from frontend_runner import run_frontend
from test_ui_beta_updates import ReleaseBoundary, ui_fixture, get_driver
from test_ui_community import FixtureAccount

ROOT = Path(__file__).resolve().parents[1]


def test_actual_update_hook_startup_and_dismissal_contracts():
    run_frontend("startup-updates.test.js")


class StartupRelease(ReleaseBoundary):
    def __init__(self):
        super().__init__()
        self.beta = True
        self.release = threading.Event()

    def status(self, force=False, repair=False):
        if not self.release.wait(15):
            raise RuntimeError("Fixture check was never released")
        return super().status(force=force, repair=repair)


@pytest.mark.parametrize("width", (1500, 850))
@pytest.mark.parametrize("account_status", ("signed_out", "new"))
def test_update_precedes_login_and_signup_then_later_resumes_them(tmp_path, monkeypatch, width, account_status):
    updater = StartupRelease()
    account = FixtureAccount(account_status)
    create = ui_fixture.create_app
    monkeypatch.setattr(ui_fixture, "create_app", lambda *args, **kwargs:
                        create(*args, **kwargs, updater=updater, community_account=account))
    artifacts = ROOT / ".iteration/adaptive-ghosts/round53-ui"
    artifacts.mkdir(parents=True, exist_ok=True)
    try:
        with ui_fixture.serve_ui(tmp_path / "startup.db") as base:
            with get_driver().launch(headless=True, viewport=(width, 1100)) as page:
                page.goto(base)
                page.wait_for(".modal h2")
                assert "Checking for updates" in page.evaluate("document.querySelector('.modal').textContent")
                assert page.count(".community-providers, .community-form") == 0
                updater.release.set()
                page.wait_for(".update-summary")
                assert page.count(".modal") == 1
                assert page.count(".community-providers, .community-form") == 0
                assert not account.calls, account.calls
                (artifacts / f"{account_status}-{width}-update-first.png").write_bytes(page.screenshot())
                page.click(".modal-actions button:text-is('Later')")
                page.wait_for(".community-providers" if account_status == "signed_out" else ".community-form")
                assert page.count(".update-summary") == 0
                assert page.count(".modal") == 1
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert page.evaluate("document.querySelector('.modal').contains(document.activeElement)")
                (artifacts / f"{account_status}-{width}-onboarding.png").write_bytes(page.screenshot())
                assert page.problems() == []
    finally:
        updater.release.set()


def test_late_update_preserves_signup_draft_and_keyboard_focus(tmp_path, monkeypatch):
    updater = StartupRelease()
    account = FixtureAccount("new")
    create = ui_fixture.create_app
    monkeypatch.setattr(ui_fixture, "create_app", lambda *args, **kwargs:
                        create(*args, **kwargs, updater=updater, community_account=account))
    try:
        with ui_fixture.serve_ui(tmp_path / "late.db") as base:
            with get_driver().launch(headless=True, viewport=(850, 1100)) as page:
                page.goto(base)
                page.wait_for(".community-form", timeout_ms=10000)
                page.evaluate("""(() => { const field = document.querySelector('.community-form input[type=text]');
                  field.value = 'KeepMyDraft'; field.dispatchEvent(new Event('input', {bubbles:true}));
                  field.focus(); })()""")
                page.click(".community-form input[type=checkbox]")
                updater.release.set()
                page.wait_for(".update-summary")
                assert page.count(".modal:visible") == 1
                assert page.count(".community-form") == 1
                page.click(".modal-actions button:text-is('Later')")
                page.wait_for(".community-form")
                assert page.evaluate("document.querySelector('.community-form input[type=text]').value") == "KeepMyDraft"
                assert page.evaluate("document.querySelector('.community-form input[type=checkbox]').checked")
                assert page.evaluate("document.querySelector('.community-form').contains(document.activeElement)")
                assert page.problems() == []
    finally:
        updater.release.set()
