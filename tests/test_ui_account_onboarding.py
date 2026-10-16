"""New/returning account flows through real local routes and the shipping UI.

Only provider/account and emulator observations are fixtures. No live account,
practice profile, Project64 process or capture installation is changed.
"""
import asyncio
import contextlib
import json
import threading
from pathlib import Path

import pytest

from test_ui_community import (FixtureAccount, community_app, get_driver,
                               open_account, wait_until)
from test_ui_setup_modal import FRESH

ARTIFACTS = Path(__file__).resolve().parents[1] / ".iteration/adaptive-ghosts/round54-ui"


def trace_account_forms(page):
    page.evaluate("""(() => {
      window.accountFormsSeen = [];
      window.accountFormObserver = new MutationObserver(() => {
        const form = document.querySelector('.community-form');
        if (form) window.accountFormsSeen.push(form.innerText);
      });
      window.accountFormObserver.observe(document.body, {subtree:true, childList:true});
    })()""")


def finish_trace(page, name):
    value = page.evaluate("""(() => {
      window.accountFormObserver.disconnect();
      return {forms: window.accountFormsSeen, width: innerWidth,
        setup: !!document.querySelector('.setup-modal'),
        settings: !!document.querySelector('.community-privacy'),
        horizontalOverflow: document.documentElement.scrollWidth > innerWidth};
    })()""")
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / f"{name}.json").write_text(json.dumps(value, indent=2), encoding="utf-8")
    (ARTIFACTS / f"{name}.png").write_bytes(page.screenshot())
    assert not value["horizontalOverflow"], value
    return value


@pytest.mark.parametrize("provider", ("twitch", "discord"))
@pytest.mark.parametrize("setup_ready,width", ((True, 1500), (False, 850)))
def test_returning_login_skips_account_editing_and_resumes_only_needed_setup(
        monkeypatch, tmp_path, provider, setup_ready, width):
    account = FixtureAccount()
    fixture = {} if setup_ready else {"capture_layer_status": dict(FRESH)}
    with community_app(monkeypatch, tmp_path, account, **fixture) as (base, _):
        with get_driver().launch(headless=True, viewport=(width, 1100)) as page:
            page.goto(base)
            page.wait_for(f".community-provider.community-{provider}:not([disabled])")
            assert page.count(".setup-modal") == 0
            trace_account_forms(page)
            page.click(f".community-provider.community-{provider}")
            wait_until(page, "document.querySelector('.modal-body')?.innerText.includes('Finish signing in')",
                       "pending provider login")
            account.update(authenticated=True, stored=True, status="active", provider=provider,
                           name="griffman1212", terms_version="t1", privacy_version="p1", sharing=False)
            if setup_ready:
                wait_until(page, "!document.querySelector('.modal')", "onboarding completion")
            else:
                page.wait_for('.setup-modal[data-page="platform"]')
            trace = finish_trace(page, f"returning-{provider}-{setup_ready}-{width}")
            assert trace["forms"] == [] and not trace["settings"], trace
            assert trace["setup"] is not setup_ready, trace
            assert account.state["sharing"] is False
            assert not any(call[0] in {"profile", "rename", "sharing"} for call in account.calls)
            if not setup_ready:
                page.click(".setup-not-now")
                wait_until(page, "!document.querySelector('.modal')", "setup dismissal without an account form")
            if setup_ready:
                open_account(page)
                page.wait_for(".community-privacy")
                assert not page.evaluate("document.querySelector('.community-check input').checked")
            assert page.problems() == []


def test_new_account_chooses_name_then_setup_with_sharing_default_on(monkeypatch, tmp_path):
    account = FixtureAccount("new")
    with community_app(monkeypatch, tmp_path, account,
                       capture_layer_status=dict(FRESH)) as (base, _):
        with get_driver().launch(headless=True, viewport=(850, 1100)) as page:
            page.goto(base)
            page.wait_for(".community-form input[autofocus]")
            assert page.count(".setup-modal") == 0
            assert not page.evaluate("[...document.querySelectorAll('.community-check')].some(n => n.textContent.includes('Automatically share'))")
            page.click(".community-form > .community-check input")
            page.wait_for(".community-form button[type=submit]:not([disabled])")
            assert page.evaluate("document.querySelector('.community-form button[type=submit]').textContent.trim()") == "Continue"
            page.click(".community-form button[type=submit]")
            page.wait_for('.setup-modal[data-page="platform"]')
            assert account.state["sharing"] is True
            assert len([call for call in account.calls if call[0] == "profile"]) == 1
            assert page.count(".community-form, .community-privacy") == 0
            ARTIFACTS.mkdir(parents=True, exist_ok=True)
            (ARTIFACTS / "new-account-to-setup-850.png").write_bytes(page.screenshot())
            assert page.problems() == []


def test_returning_login_from_settings_also_skips_account_editing(monkeypatch, tmp_path):
    account = FixtureAccount()
    account.update(stored=True)  # Existing credentials still allow offline practice.
    with community_app(monkeypatch, tmp_path, account) as (base, _):
        with get_driver().launch(headless=True, viewport=(850, 1100)) as page:
            page.goto(base)
            page.wait_for(".practice-page")
            wait_until(page, "!document.querySelector('.modal')", "startup checks")
            open_account(page)
            page.wait_for(".community-provider.community-twitch:not([disabled])")
            trace_account_forms(page)
            page.click(".community-provider.community-twitch")
            wait_until(page, "document.querySelector('.modal-body')?.innerText.includes('Finish signing in')",
                       "manual provider sign-in")
            account.update(authenticated=True, status="active", provider="twitch",
                           terms_version="t1", privacy_version="p1")
            wait_until(page, "!document.querySelector('.modal')", "manual returning sign-in completion")
            assert finish_trace(page, "returning-from-settings-850")["forms"] == []
            assert not any(call[0] in {"profile", "rename", "sharing"} for call in account.calls)
            assert page.problems() == []


def test_setup_waits_for_initial_account_state(monkeypatch, tmp_path):
    account = FixtureAccount()
    release = threading.Event()
    armed = threading.Event()
    original = account.snapshot

    def delayed_account():
        if armed.is_set():
            assert release.wait(15), "The initial account request was never released"
        return original()

    monkeypatch.setattr(account, "snapshot", delayed_account)
    try:
        with community_app(monkeypatch, tmp_path, account,
                           capture_layer_status=dict(FRESH)) as (base, _):
            armed.set()
            with get_driver().launch(headless=True, viewport=(850, 1100)) as page:
                page.goto(base)
                page.wait_for(".practice-page")
                # The setup response has resolved while account is still pending.
                wait_until(page, "performance.getEntriesByType('resource').some(e => e.name.endsWith('/api/setup'))",
                           "setup discovery before account resolution")
                assert page.count(".setup-modal") == 0
                release.set()
                page.wait_for(".community-providers")
                assert page.count(".setup-modal") == 0
                assert page.problems() == []
    finally:
        release.set()


@pytest.mark.parametrize("recovery", ("cached", "retry"))
def test_restored_login_waits_for_profile_without_invitation_error(monkeypatch, tmp_path, recovery):
    account = FixtureAccount()
    account.update(authenticated=True, stored=True)
    with community_app(monkeypatch, tmp_path, account,
                       capture_layer_status=dict(FRESH)) as (base, _):
        with get_driver().launch(headless=True, viewport=(850, 1100)) as page:
            page.goto(base)
            page.wait_for(".modal-body p:text-is('Loading your account…')")
            assert "Loading your account" in page.evaluate("document.querySelector('.modal-body').innerText")
            assert "invitation" not in page.evaluate("document.querySelector('.modal-body').innerText")
            assert page.count(".community-form, .setup-modal, .community-providers") == 0
            def ready():
                account.update(status="active", provider="discord", terms_version="t1", privacy_version="p1")

            if recovery == "retry":
                # A public-config restore failure can leave no account.error;
                # local snapshot polling alone cannot recover remote work.
                async def remote_refresh():
                    account.calls.append(("remote_retry",))
                    ready()
                    return account.snapshot()

                monkeypatch.setattr(account, "refresh", remote_refresh)
                page.click(".modal-body button:text-is('Retry')")
            else:
                ready()
            page.wait_for('.setup-modal[data-page="platform"]')
            assert page.count(".community-form, .community-privacy") == 0
            assert not any(call[0] == "begin" for call in account.calls)
            if recovery == "retry":
                assert ("remote_retry",) in account.calls
            assert page.problems() == []


def test_opted_in_sheet_import_finishes_before_emulator_setup(monkeypatch, tmp_path):
    from sm64_events.library.store import LibraryStore

    # Keep the actual local import job, column matching and landing. Only the
    # remote refresh is offline; this test must never download the live sheet.
    monkeypatch.setattr(LibraryStore, "refresh", lambda *args, **kwargs: {"applied": False})
    account = FixtureAccount("new")
    saving, release = threading.Event(), threading.Event()
    complete = account.complete_profile

    async def slow_save(name, **body):
        saving.set()
        assert await asyncio.to_thread(release.wait, 15), "Profile save was never released"
        return await complete(name, **body)

    monkeypatch.setattr(account, "complete_profile", slow_save)
    with community_app(monkeypatch, tmp_path, account,
                       capture_layer_status=dict(FRESH)) as (base, _), contextlib.ExitStack() as cleanup:
        cleanup.callback(release.set)
        with get_driver().launch(headless=True, viewport=(850, 1100)) as page:
            page.goto(base)
            page.wait_for(".community-sheet .search-select-trigger")
            page.click(".community-sheet .search-select-trigger")
            page.wait_for(".community-sheet .search-menu-option")
            page.click(".community-sheet .search-menu-option:first-child")
            page.click(".community-sheet input[type=checkbox]")
            page.click(".community-form > .community-check input")
            page.wait_for(".community-form button[type=submit]:not([disabled])")
            page.click(".community-form button[type=submit]")
            page.wait_for(".community-actions button:text-is('Start practicing')")
            assert saving.wait(2)
            assert page.evaluate("document.querySelector('.community-actions button').disabled")
            assert page.count(".setup-modal") == 0
            release.set()
            page.wait_for(".importdoor-summary.is-ok")
            assert page.count(".setup-modal, .community-privacy") == 0
            assert account.state["status"] == "active" and account.state["sharing"] is True
            page.click(".community-actions button:text-is('Start practicing')")
            page.wait_for('.setup-modal[data-page="platform"]')
            assert page.problems() == []
