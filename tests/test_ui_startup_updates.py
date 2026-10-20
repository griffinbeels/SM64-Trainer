"""Silent startup checks, actionable offers and retained onboarding through the real app."""
import contextlib
import json
import os
import threading
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from frontend_runner import run_frontend
from test_ui_beta_updates import ReleaseBoundary, ui_fixture, get_driver
from test_ui_community import FixtureAccount
from tests.browser_faults import no_faults, watch

ROOT = Path(__file__).resolve().parents[1]


def trace_onboarding_focus(page):
    page.evaluate("""(() => {
      const rows = []; let frame;
      const sample = why => {
        const dialog = [...document.querySelectorAll('.modal')].find(node =>
          node.getClientRects().length && !node.closest('[inert]')
          && node.querySelector('.community-providers,.community-form'));
        if (dialog) rows.push({why, at: performance.now(),
          inside: dialog.contains(document.activeElement),
          active: document.activeElement?.tagName + '.' + document.activeElement?.className});
      };
      const observer = new MutationObserver(() => sample('mutation'));
      observer.observe(document.body, {subtree:true,childList:true,attributes:true});
      const focus = () => sample('focus');
      document.addEventListener('focusin', focus);
      const tick = () => { sample('frame'); frame = requestAnimationFrame(tick); };
      frame = requestAnimationFrame(tick);
      window.finishStartupFocusTrace = async () => {
        const end = performance.now() + 3000;
        while (performance.now() < end && rows.filter(row => row.why === 'frame').length < 2)
          await new Promise(resolve => setTimeout(resolve, 10));
        observer.disconnect(); cancelAnimationFrame(frame);
        document.removeEventListener('focusin', focus);
        return rows;
      };
    })()""")


def assert_painted_onboarding_owns_focus(page, name):
    rows = page.evaluate("window.finishStartupFocusTrace()")
    folder = ROOT / ".iteration/adaptive-ghosts/round53-ui"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"focus-{name}.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    painted = [row for row in rows if row["why"] == "frame"]
    assert len(painted) >= 2, rows
    assert all(row["inside"] for row in painted), rows


def test_actual_update_hook_startup_and_dismissal_contracts():
    run_frontend("startup-updates.test.js", "modal-focus.test.js")


class StartupRelease(ReleaseBoundary):
    def __init__(self):
        super().__init__()
        self.beta = True
        self.release = threading.Event()
        self.started = threading.Event()

    def status(self, force=False, repair=False):
        self.started.set()
        if not self.release.wait(15):
            raise RuntimeError("Fixture check was never released")
        return super().status(force=force, repair=repair)


def hold_refresh_probes(page):
    """Guarantee the refresh retires a read and telemetry, not just status calls."""
    held = []
    page.route("**/*probe=startup-retire", lambda route: held.append(route.request))
    for endpoint, method in (("/api/scorecard/entry_goals", "GET"), ("/api/uilog", "POST")):
        probe = endpoint + "?probe=startup-retire"
        options = {"method": method, **({"body": "{}"} if method == "POST" else {})}
        with page.expect_request(lambda request, *, probe=probe: request.url.endswith(probe)):
            page.evaluate(f"void fetch({json.dumps(probe)}, {json.dumps(options)}).catch(() => {{}})")
    return held


@pytest.mark.parametrize("width", (1500, 850))
@pytest.mark.parametrize("account_status", ("signed_out", "new"))
def test_checking_does_not_block_login_and_signup_but_a_real_offer_gets_priority(tmp_path, monkeypatch, width, account_status):
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
                page.wait_for(".practice-page")
                assert page.evaluate("![...document.querySelectorAll('.modal')].some(e=>e.textContent.includes('Checking for updates'))")
                page.wait_for(".community-providers" if account_status == "signed_out" else ".community-form")
                assert not updater.release.is_set()
                updater.release.set()
                page.wait_for(".update-summary")
                assert page.count(".modal:visible") == 1
                assert page.count(".community-providers, .community-form") == 1  # retained beneath the offer
                assert all(call[0] == "check" for call in account.calls), account.calls
                (artifacts / f"{account_status}-{width}-update-first.png").write_bytes(page.screenshot())
                trace_onboarding_focus(page)
                page.click(".modal-actions button:text-is('Later')")
                page.wait_for(".community-providers" if account_status == "signed_out" else ".community-form")
                assert_painted_onboarding_owns_focus(page, f"initial-{account_status}-{width}")
                assert page.count(".update-summary") == 0
                assert page.count(".modal") == 1
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert page.evaluate("document.querySelector('.modal').contains(document.activeElement)")
                (artifacts / f"{account_status}-{width}-onboarding.png").write_bytes(page.screenshot())
                assert page.problems() == []
    finally:
        updater.release.set()


@pytest.mark.parametrize("width", (850, 1500))
def test_refresh_is_usable_while_checking_and_stays_clear_without_an_offer(tmp_path, monkeypatch, width):
    updater = StartupRelease()
    updater.beta = False
    account = FixtureAccount("active")
    create = ui_fixture.create_app
    monkeypatch.setattr(ui_fixture, "create_app", lambda *args, **kwargs:
                        create(*args, **kwargs, updater=updater, community_account=account))
    artifacts = Path(os.environ.get("SM64_STARTUP_EVIDENCE", tmp_path / "silent-update-shots"))
    artifacts.mkdir(parents=True, exist_ok=True)
    try:
        with ui_fixture.serve_ui(tmp_path / "current.db") as base, contextlib.ExitStack() as cleanup:
            cleanup.callback(updater.release.set)
            # Raw requests retain document identity; uilab's string-only fault
            # drain cannot distinguish this refresh from a same-page failure.
            with sync_playwright() as play:
                browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
                page = browser.new_page(viewport={"width": width, "height": 1100}, reduced_motion="no-preference")
                page.set_default_timeout(15000)
                seen, held = watch(page), []
                try:
                    for cycle in range(2):  # the initial load and an ordinary refresh
                        page._retire_observed_requests()
                        page.goto(base, wait_until="domcontentloaded")
                        page.locator(".practice-page").first.wait_for()
                        assert updater.started.wait(5)
                        assert not updater.release.is_set()
                        assert page.locator(".modal, .update-summary").count() == 0
                        page.locator("button[aria-label='Settings']:visible,button[aria-label='Open settings']:visible").first.wait_for()
                        label = page.evaluate("""[...document.querySelectorAll('button')].find(b=>
                          ['Settings','Open settings'].includes(b.getAttribute('aria-label'))
                          && b.getClientRects().length)?.getAttribute('aria-label')""")
                        assert label, "The Settings control must be present before testing its reachability"
                        page.click("button[aria-label=" + json.dumps(label) + "]")
                        page.locator(".settings-drawer").first.wait_for()
                        page.click("button[aria-label='Close settings']")
                        page.locator(".settings-drawer").wait_for(state="detached")
                        if cycle == 0:
                            # Make the former assumption fail deterministically:
                            # a refresh can retire reads and telemetry as well
                            # as the two deliberately held updater requests.
                            held = hold_refresh_probes(page)
                    assert len(held) == 2 and all(request.failure == "net::ERR_ABORTED" for request in held)
                    (artifacts / f"checking-silent-{width}.png").write_bytes(page.screenshot())
                    updater.release.set()
                    page.click("button[aria-label=" + json.dumps(label) + "]")
                    # This control becomes enabled only after the actual update
                    # hook receives status; observe the UI, not a guessed URL.
                    page.locator("#receive-beta-updates:not([disabled])").first.wait_for()
                    page.click("button[aria-label='Close settings']")
                    page.locator(".settings-drawer").wait_for(state="detached")
                    assert page.locator(".modal, .update-summary").count() == 0
                    (artifacts / f"no-offer-{width}.png").write_bytes(page.screenshot())
                    no_faults(seen)
                finally:
                    page._retire_observed_requests()
                    browser.close()
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
                consent = '.community-form label:has(a[href]) input[type=checkbox]'
                sharing = '.community-form .community-pb-sharing input[type=checkbox]'
                assert page.count(consent) == 1
                assert page.evaluate(f"document.querySelector({json.dumps(sharing)}).checked")
                page.click(sharing)  # Retain an explicit PB-sharing opt-out too.
                page.click(consent)
                updater.release.set()
                page.wait_for(".update-summary")
                assert page.count(".modal:visible") == 1
                assert page.count(".community-form") == 1
                trace_onboarding_focus(page)
                # Shift+Tab and Tab cycle within the active update dialog;
                # the retained inert signup dialog must not intercept them.
                page.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', {key:'Tab',shiftKey:true,bubbles:true,cancelable:true}))")
                assert page.evaluate("document.activeElement.textContent.trim()") == "Later"
                page.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', {key:'Tab',bubbles:true,cancelable:true}))")
                assert page.evaluate("document.activeElement.getAttribute('aria-label')") == "Close dialog"
                page.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape',bubbles:true,cancelable:true}))")
                page.wait_for(".community-form")
                assert_painted_onboarding_owns_focus(page, "late-signup")
                assert page.evaluate("document.querySelector('.community-form input[type=text]').value") == "KeepMyDraft"
                assert page.evaluate(f"document.querySelector({json.dumps(consent)}).checked")
                assert not page.evaluate(f"document.querySelector({json.dumps(sharing)}).checked")
                assert page.evaluate("document.querySelector('.community-form').contains(document.activeElement)")
                assert page.problems() == []
    finally:
        updater.release.set()
