"""Shipping account UI and local routes against a token-free account seam.

These are browser/component contracts, not live Supabase or cloud-exchange proof.
The fixture replaces only the remote account boundary. The actual app, assets,
local account router, request capability and practice database remain in use.
No browser fetch is intercepted, and every test reads its own checkout's UI.
"""
import contextlib
import json
import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab.driver import default_wait_ms, get_driver  # noqa: E402
import ui_fixture  # noqa: E402
from tests.player_pages_fixture import Account as ProgressAccount  # noqa: E402

WIDTHS = (1500, 850, 390)
MODAL = ".modal-body"
UID = "ec2f03ac-42db-4a29-8a68-dbc018aba020"
PROGRESS_READS = {("remote", "GET", "/v2/account/progress"),
                  ("remote", "GET", "/v2/account/player-profile")}


class FixtureAccount:
    """Controllable public state; no passwords, OAuth codes or credentials."""

    def __init__(self, status="signed_out", *, slow_profile=False):
        self._lock = threading.RLock()
        self.calls = []
        self.reads = 0
        self.slow_profile = slow_profile
        self.uid = UID
        self.taken = {"taken"}
        self.progress = ProgressAccount(virgin=status == "new")
        self.progress.publication.update(uid=UID)
        self.progress.profile.update(uid=UID)
        if status != "new":
            # Established accounts have already chosen to pause PB updates.
            self.progress.publication.update(revision=1,consent_revision=1)
        self.state = {
            "enabled": True, "authenticated": status != "signed_out", "stored": status != "signed_out",
            "provider": "twitch" if status != "signed_out" else None,
            "uid": UID, "status": status, "name": "Fixture runner",
            "sharing": False, "sheet_runner": None, "sheet_verified": False,
            "terms_version": "t1" if status == "active" else None,
            "privacy_version": "p1" if status == "active" else None,
            "terms_version_current": "t1", "privacy_version_current": "p1",
            "terms_url": "https://policies.example/terms",
            "privacy_url": "https://policies.example/privacy",
        }

    def snapshot(self):
        with self._lock:
            self.reads += 1
            return dict(self.state)

    def update(self, **fields):
        with self._lock:
            self.state.update(fields)

    async def restore(self):
        return self.snapshot()

    async def refresh(self):
        self.calls.append(("refresh",))
        return self.snapshot()

    async def begin_sign_in(self, provider):
        self.calls.append(("begin", provider))
        if self.slow_profile:
            # Native token installation precedes the remote profile read.
            self.update(authenticated=True, status="signed_out")
        return {"attempt_id": "fixture-attempt", "url": "about:blank"}

    def cancel_sign_in(self, attempt_id):
        self.calls.append(("cancel", attempt_id))
        self.update(authenticated=False, status="signed_out")

    async def complete_profile(self, name, **body):
        self.calls.append(("profile", name, dict(body)))
        self.update(name=name, status="active", **body)
        return self.snapshot()

    async def rename(self, name):
        self.calls.append(("rename", name))
        self.update(name=name, status="active")
        return self.snapshot()

    async def check_name(self, name):
        self.calls.append(("check", name))
        return {"name": name, "available": name.lower() not in self.taken}

    async def set_sharing(self, enabled):
        self.calls.append(("sharing", enabled))
        self.update(sharing=enabled)
        return self.snapshot()

    async def sign_out(self):
        self.calls.append(("sign_out",))
        self.update(authenticated=False, stored=False, provider=None, status="signed_out", name=None, sharing=False)
        return self.snapshot()

    async def request_json(self, method, path, **kwargs):
        self.calls.append(("remote", method, path, kwargs.get("json")))
        if method == "GET" and path == "/v2/account/player-export":
            return {"v":2,"uid":self.uid,"profile":self.progress.profile,
                    "publication":self.progress.publication,"snapshot":None}
        if path.startswith("/v2/account/"):
            return self.progress._route(method,path,kwargs.get("json"),kwargs.get("params",{}))
        if path == "/v1/account/export":
            return {"v": 1, "account": {"uid": self.uid}, "recordings": [], "complete": True}
        if path == "/v1/account/withdraw":
            self.update(sharing=False)
            return {"v": 1, "withdrawn": 0}
        if path == "/v1/account/delete":
            return {"v": 1, "deleted": True}
        raise AssertionError(f"Unmatched fixture account route: {method} {path}")


@contextlib.contextmanager
def community_app(monkeypatch, tmp_path, account=None, **fixture):
    """Inject only the account seam into the established offline app fixture."""
    listener = ui_fixture._bound_socket()
    port = listener.getsockname()[1]
    create_app = ui_fixture.create_app

    def configured_app(*args, **kwargs):
        return create_app(*args, **kwargs, community_account=account,
                          protect_local_api=True, local_port=port)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(ui_fixture, "create_app", configured_app)
            patch.setattr(ui_fixture, "_bound_socket", lambda: listener)
            with ui_fixture.serve_ui_live(tmp_path / "practice.db", seed=False, **fixture) as result:
                # The normal fixture seeds some controls through HTTP before it
                # knows this protected server's capability. Seed public practice
                # events through its existing direct service helper instead.
                ui_fixture.seed_practice(result[1])
                yield result
    finally:
        listener.close()


def wait_until(page, expression, description):
    deadline = time.monotonic() + default_wait_ms() / 1000
    while not page.evaluate(expression):
        assert time.monotonic() < deadline, f"Timed out waiting for {description}"
        page.wait_ms(50)


def click_text(page, label, scope=MODAL):
    """Click a rendered control, scoped away from hidden app panes."""
    expression = (
        "Array.from(document.querySelectorAll(" + json.dumps(scope + " button") + "))"
        ".find(b => b.getClientRects().length && !b.disabled"
        " && b.textContent.trim() === " + json.dumps(label) + ")"
    )
    wait_until(page, f"!!({expression})", f"the {label!r} control")
    assert page.evaluate(f"(() => {{ const b = {expression}; b.click(); return true; }})()")


def open_account(page):
    if page.count(MODAL):
        return
    # A known-good app control precedes every manually opened account screen.
    label = page.evaluate("""(() => {
        const button = Array.from(document.querySelectorAll('button')).find(b =>
            ['Settings', 'Open settings'].includes(b.getAttribute('aria-label'))
            && b.getClientRects().length);
        return button?.getAttribute('aria-label');
    })()""")
    assert label, "The actual app's settings control did not render"
    page.click("button[aria-label=" + json.dumps(label) + "]")
    page.wait_for(".settings-drawer")
    assert page.evaluate("""(() => {
        const section = Array.from(document.querySelectorAll('.settings-section'))
            .find(s => s.querySelector('h3')?.textContent === 'Community');
        const button = section?.querySelector('button');
        if (button) button.click();
        return !!button;
    })()"""), "Settings has no reachable community account control"
    page.wait_for(MODAL)


def set_name(page, name):
    assert page.evaluate("""(() => {
        const input = document.querySelector('.modal-body input[autocomplete="nickname"]');
        if (!input) return false;
        input.value = VALUE;
        input.dispatchEvent(new Event('input', {bubbles:true}));
        return true;
    })()""".replace("VALUE", json.dumps(name)))
    page.wait_ms(50)


def modal_text(page):
    return page.evaluate("document.querySelector('.modal-body')?.innerText || ''")


def capture_widths(page, name):
    """Private screenshots are fixture evidence, never a live auth receipt."""
    folder = REPO / ".iteration" / "adaptive-ghosts" / "round19-community-ui"
    folder.mkdir(parents=True, exist_ok=True)
    for width in WIDTHS:
        page.set_viewport(width, 1000)
        wait_until(page, f"innerWidth === {width}", "the requested viewport")
        page.wait_ms(100)
        bounds = page.evaluate("""(() => {
            const box = document.querySelector('.modal')?.getBoundingClientRect();
            return {width: innerWidth, scroll: document.documentElement.scrollWidth,
                    left: box?.left, right: box?.right};
        })()""")
        assert bounds["scroll"] <= width + 1, bounds
        if bounds["left"] is not None:
            assert bounds["left"] >= -1 and bounds["right"] <= width + 1, bounds
        (folder / f"{name}-{width}.png").write_bytes(page.screenshot())


def test_stable_initial_load_and_settings_have_no_browser_faults(monkeypatch, tmp_path):
    with community_app(monkeypatch, tmp_path) as (base, _):
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(base)
            page.wait_for(".practice-page")
            page.wait_for(".context-shell")
            open_account(page)
            page.wait_for(".community-providers")
            assert page.evaluate("""Array.from(document.querySelectorAll(
                '.community-providers button')).every(button => button.disabled)""")
            assert "not configured" in modal_text(page)
            capture_widths(page, "stable-unconfigured")
            click_text(page, "Practice offline")
            wait_until(page, "!document.querySelector('.modal-body')", "offline dismissal")
            assert page.problems() == []


def test_sign_in_is_the_first_step_with_discord_and_twitch_only(monkeypatch, tmp_path):
    """His decisions (round 41): Twitch or Discord only, and a configured build
    with no account on this profile keeps the sign-in screen open: no Google,
    no email, no "Practice offline", no close."""
    account = FixtureAccount()
    with community_app(monkeypatch, tmp_path, account) as (base, _):
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(base)
            page.wait_for(".practice-page")
            # Nothing opened it: the account screen is the first step on its own.
            page.wait_for(".community-provider.community-discord:not([disabled])")
            page.wait_for(".community-provider.community-twitch:not([disabled])")
            wait_until(page, """Array.from(document.querySelectorAll(
                '.community-providers img')).every(img => img.complete && img.naturalWidth > 0)""",
                       "the provider artwork to load")
            assets = page.evaluate("""Array.from(document.querySelectorAll(
                '.community-providers img')).map(img => new URL(img.src).pathname)""")
            assert assets == ["/ui/assets/community/discord-clyde-white.svg",
                              "/ui/assets/community/twitch-glitch-white.svg"]
            # His nitpick (round 43): every provider's icon sits on one x, and
            # each label is centered on its own button, whatever its length.
            geometry = page.evaluate("""Array.from(document.querySelectorAll('.community-provider')).map(button => {
                const icon = button.querySelector('img').getBoundingClientRect(), box = button.getBoundingClientRect();
                const range = document.createRange(); range.selectNodeContents(button);
                const text = Array.from(range.getClientRects()).filter(r => r.width > icon.width + 1).pop();
                return {icon: icon.left, centre: box.left + box.width / 2, text: text ? text.left + text.width / 2 : null};
            })""")
            assert len({round(g["icon"]) for g in geometry}) == 1, geometry
            assert all(g["text"] is not None and abs(g["text"] - g["centre"]) <= 1 for g in geometry), geometry
            text = modal_text(page)
            assert "Continue with email" not in text and "Google" not in text and "Practice offline" not in text
            assert "Sign in" in page.evaluate("document.querySelector('.modal-heading h2')?.innerText || ''")
            assert page.count(".modal [aria-label='Close']") == 0
            capture_widths(page, "providers")
            # Escape stays unreachable; the wizard waits behind the account step.
            page.evaluate("(() => { document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'})); return true; })()")
            page.wait_ms(100)
            assert page.count(".community-providers") == 1 and page.count(".setup-progress") == 0
            assert all(call[0] == "refresh" or call[:3] in PROGRESS_READS for call in account.calls)
            assert page.problems() == []


@pytest.mark.parametrize("status,share_pbs", [("new",True),("new",False),("consent_required",None)])
def test_profile_discloses_new_sharing_and_keeps_import_opt_in(monkeypatch, tmp_path, status, share_pbs):
    account = FixtureAccount(status)
    with community_app(monkeypatch, tmp_path, account) as (base, _):
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(base)
            page.wait_for(".practice-page")
            open_account(page)
            page.wait_for(".community-sheet .search-select-trigger")
            pb_choice = ".community-form .community-pb-sharing input"
            assert page.count(pb_choice) == int(status == "new")
            if status == "new":
                assert page.evaluate("document.querySelector("+json.dumps(pb_choice)+").checked")
                if not share_pbs:
                    page.click(pb_choice)
            assert page.evaluate("document.querySelector('.community-form button[type=submit]').disabled")
            assert ("shared automatically" if status == "new" else "sharing stays off") in modal_text(page)
            page.click(".community-sheet .search-select-trigger")
            page.wait_for(".community-sheet .search-menu-option")
            chosen = page.evaluate("document.querySelector('.community-sheet .search-menu-option').dataset.value")
            page.click(".community-sheet .search-menu-option:first-child")
            page.wait_for(".community-sheet input[type=checkbox]")
            assert page.evaluate("!document.querySelector('.community-sheet input[type=checkbox]').checked")
            assert "does not create ghosts or verify name ownership" in modal_text(page)
            assert not page.evaluate("document.querySelector('.community-sheet input[type=checkbox]').checked")
            capture_widths(page, f"profile-defaults-{status}")
            # Consent enables the disclosed default; import stays unselected.
            consent = ".community-form > .community-check:last-of-type input"
            page.click(consent)
            page.wait_for(".community-form button[type=submit]:not([disabled])")
            page.click(".community-form button[type=submit]")
            wait_until(page, "!document.querySelector('.modal-body')", "profile completion")
            assert ("profile", chosen, {"terms_version": "t1", "privacy_version": "p1",
                                       "sharing": status == "new", "sheet_runner": chosen}) in account.calls
            if status == "new":
                control = [call for call in account.calls if call[:3] == ("remote","PUT","/v2/account/progress/control")]
                assert control and control[0][3]["action"] == ("enable" if share_pbs else "disable")
                assert account.progress.publication["enabled"] == share_pbs
            assert not page.count(".modal-body"), "Unchecked Sheet import unexpectedly kept its flow open"
            assert page.problems() == []


def test_forced_rename_can_sign_out_without_changing_owned_data(monkeypatch, tmp_path):
    """Signing out of a forced rename returns to the sign-in step (round 41:
    no practice without an account), never to an unsigned practice page."""
    account = FixtureAccount("rename_required")
    with community_app(monkeypatch, tmp_path, account) as (base, service):
        before = len(service.db.attempts())
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(base)
            page.wait_for(".community-form input[autocomplete=nickname]")
            assert "Your account and ghosts are still yours" in modal_text(page)
            capture_widths(page, "forced-rename")
            click_text(page, "Sign out")
            page.wait_for(".community-provider.community-discord")
            assert account.uid == UID
            assert ("sign_out",) in account.calls
            assert not any(call[0] in {"rename", "profile"}
                           or (call[0] == "remote" and call[:3] not in PROGRESS_READS)
                           for call in account.calls)
            assert len(service.db.attempts()) == before
            assert page.count(".practice-page") == 1 and "Practice offline" not in modal_text(page)
            assert page.problems() == []


def test_renewing_consent_preserves_sharing_and_sheet_identity(monkeypatch, tmp_path):
    account = FixtureAccount('consent_required')
    account.update(sharing=True, sheet_runner='Existing runner')
    with community_app(monkeypatch, tmp_path, account) as (base, _):
        with get_driver().launch(headless=True, viewport=(850, 1000)) as page:
            page.goto(base)
            page.wait_for('.practice-page')
            open_account(page)
            page.wait_for('.community-form')
            assert 'shared automatically' in modal_text(page)
            assert not page.evaluate("document.querySelector('.community-sheet input[type=checkbox]').checked")
            page.click('.community-form > .community-check:last-of-type input')
            page.wait_for('.community-form button[type=submit]:not([disabled])')
            page.click('.community-form button[type=submit]')
            wait_until(page, "!document.querySelector('.modal-body')", 'renewed consent')
            assert ('profile', 'Fixture runner', {'terms_version': 't1', 'privacy_version': 'p1',
                    'sharing': True, 'sheet_runner': 'Existing runner'}) in account.calls
            assert page.problems() == []


def test_sign_in_stays_pending_until_slow_remote_profile_is_ready(monkeypatch, tmp_path):
    account = FixtureAccount(slow_profile=True)
    with community_app(monkeypatch, tmp_path, account) as (base, _):
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(base)
            page.wait_for(".practice-page")
            open_account(page)
            page.wait_for(".community-provider.community-discord:not([disabled])")
            page.click(".community-provider.community-discord")
            wait_until(page, "document.querySelector('.modal-body')?.innerText.includes('Finish signing in')",
                       "the pending sign-in screen")
            baseline = account.reads
            deadline = time.monotonic() + default_wait_ms() / 1000
            while account.reads < baseline + 2:
                assert time.monotonic() < deadline, "Pending sign-in stopped polling before profile readiness"
                page.wait_ms(50)
            assert "Finish signing in" in modal_text(page)
            assert "invitation" not in modal_text(page)
            assert not page.count(".community-form input[autocomplete=nickname]")
            account.update(authenticated=True, status="new")
            page.wait_for(".community-form input[autocomplete=nickname]")
            assert ("begin", "discord") in account.calls
            assert page.problems() == []


def test_settings_use_registered_name_sharing_and_sign_out_routes(monkeypatch, tmp_path):
    account = FixtureAccount("active")
    with community_app(monkeypatch, tmp_path, account) as (base, _):
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(base)
            page.wait_for(".practice-page")
            open_account(page)
            page.wait_for(".community-privacy")
            set_name(page, "Changed fixture name")
            page.click(".community-form > .community-check input[type=checkbox]")
            click_text(page, "Done")
            wait_until(page, "!document.querySelector('.modal-body')", "settings save")
            assert ("rename", "Changed fixture name") in account.calls
            assert ("sharing", True) in account.calls
            open_account(page)
            page.wait_for(".community-privacy")
            click_text(page, "Sign out")
            page.wait_for(".community-provider.community-discord")  # signed out: the sign-in step returns (round 41)
            assert ("sign_out",) in account.calls
            assert page.problems() == []


def test_sharing_opt_out_explains_effect_and_cancel_preserves_choice(monkeypatch, tmp_path):
    account = FixtureAccount("active")
    account.update(sharing=True)
    with community_app(monkeypatch, tmp_path, account) as (base, _):
        with get_driver().launch(headless=True, viewport=(850, 1000)) as page:
            page.goto(base)
            page.wait_for(".practice-page")
            open_account(page)
            page.wait_for(".community-privacy")
            page.click(".community-form > .community-check input[type=checkbox]")
            assert "won't receive your new ghosts" in modal_text(page)
            assert "Existing shared ghosts stay available" in modal_text(page)
            capture_widths(page, "sharing-off-pending")
            assert account.state["sharing"] is True
            page.click(".modal-close")
            wait_until(page, "!document.querySelector('.modal-body')", "cancelled settings")
            assert not any(call[0] == "sharing" for call in account.calls)
            open_account(page)
            page.wait_for(".community-privacy")
            assert page.evaluate("document.querySelector('.community-form > .community-check input').checked")
            page.click(".community-form > .community-check input[type=checkbox]")
            click_text(page, "Done")
            wait_until(page, "!document.querySelector('.modal-body')", "saved opt-out")
            assert ("sharing", False) in account.calls
            assert account.state["sharing"] is False
            assert page.problems() == []


def test_profile_bounds_normalized_name_before_submission(monkeypatch, tmp_path):
    account = FixtureAccount("new")
    with community_app(monkeypatch, tmp_path, account) as (base, _):
        with get_driver().launch(headless=True, viewport=(850, 1000)) as page:
            page.goto(base)
            page.wait_for(".practice-page")
            open_account(page)
            page.wait_for(".community-form")
            page.click(".community-form > .community-check:last-of-type input")
            set_name(page, "a" * 21)
            assert "20 characters or fewer" in modal_text(page)
            assert page.evaluate("document.querySelector('.community-form button[type=submit]').disabled")
            set_name(page, "ﬃ" * 7)
            assert page.evaluate("document.querySelector('.community-form button[type=submit]').disabled")
            set_name(page, "SullyLikesBigChungus")
            page.wait_for(".community-form button[type=submit]:not([disabled])")
            capture_widths(page, "longest-library-name")
            page.click(".community-form button[type=submit]")
            wait_until(page, "!document.querySelector('.modal-body')", "valid 20-character name")
            assert any(call[:2] == ("profile", "SullyLikesBigChungus") for call in account.calls)
            assert page.problems() == []


def test_privacy_actions_use_owner_routes_and_explicit_confirmation(monkeypatch, tmp_path):
    account = FixtureAccount("active")
    with community_app(monkeypatch, tmp_path, account) as (base, _):
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(base)
            page.wait_for(".practice-page")
            open_account(page)
            page.click(".community-privacy summary")
            click_text(page, "Export account data")
            deadline = time.monotonic() + default_wait_ms() / 1000
            while ("remote", "GET", "/v1/account/export", None) not in account.calls:
                assert time.monotonic() < deadline, "Export never reached the registered account route"
                page.wait_ms(50)
            wait_until(page, """!Array.from(document.querySelectorAll('.modal-body button'))
                .some(button => button.disabled)""", "account export to finish")
            assert ("remote", "GET", "/v1/account/export", None) in account.calls
            assert ("remote", "GET", "/v2/account/player-export", None) in account.calls
            click_text(page, "Remove shared ghosts")
            page.wait_for(".community-form h3")
            assert "Previously downloaded copies may remain" in modal_text(page)
            assert not any(call[:3] == ("remote", "POST", "/v1/account/withdraw") for call in account.calls)
            click_text(page, "Remove shared ghosts")
            page.wait_for(".community-privacy")
            assert ("remote", "POST", "/v1/account/withdraw", {"confirmation": "WITHDRAW MY GHOSTS"}) in account.calls
            page.click(".community-privacy summary")
            click_text(page, "Delete account")
            page.wait_for(".community-form h3")
            assert not any(call[:3] == ("remote", "POST", "/v1/account/delete") for call in account.calls)
            click_text(page, "Delete account")
            page.wait_for(".community-providers")
            assert ("remote", "POST", "/v1/account/delete", {"confirmation": "DELETE MY ACCOUNT"}) in account.calls
            assert ("sign_out",) in account.calls
            assert page.problems() == []


@pytest.mark.parametrize("provider", ["discord", "twitch"])
def test_cancelling_sign_in_posts_the_exact_pending_attempt(monkeypatch, tmp_path, provider):
    account = FixtureAccount()
    with community_app(monkeypatch, tmp_path, account) as (base, _):
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(base)
            page.wait_for(".practice-page")
            open_account(page)
            page.wait_for(f".community-provider.community-{provider}:not([disabled])")
            page.click(f".community-provider.community-{provider}")
            wait_until(page, "document.querySelector('.modal-body')?.innerText.includes('Finish signing in')",
                       "the cancellable sign-in screen")
            click_text(page, "Cancel")
            page.wait_for(".community-providers")
            deadline = time.monotonic() + default_wait_ms() / 1000
            while ("cancel", "fixture-attempt") not in account.calls:
                assert time.monotonic() < deadline, "Cancel did not invalidate the pending attempt"
                page.wait_ms(50)
            assert ("begin", provider) in account.calls
            assert not account.snapshot()["authenticated"]
            assert page.problems() == []


def test_desktop_shell_sign_in_never_pre_opens_a_blank_window(monkeypatch, tmp_path):
    """Round 39: pywebview hands window.open to the OS, which asked for an app for 'about:blank'."""
    from sm64_events.server import community_api
    opened = []
    monkeypatch.setattr(community_api, "open_in_browser", lambda url: opened.append(url) or True)
    account = FixtureAccount()
    with community_app(monkeypatch, tmp_path, account) as (base, _):
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(base)
            page.wait_for(".practice-page")
            page.evaluate("(() => { window.pywebview = {platform: 'edgechromium'}; window.__windows = [];"
                          " window.open = (url) => { window.__windows.push(url); return null; }; return true; })()")
            open_account(page)
            page.wait_for(".community-provider.community-discord:not([disabled])")
            page.click(".community-provider.community-discord")
            wait_until(page, "document.querySelector('.modal-body')?.innerText.includes('Finish signing in')",
                       "the pending sign-in screen")
            assert page.evaluate("window.__windows") == []
            assert ("begin", "discord") in account.calls
            deadline = time.monotonic() + default_wait_ms() / 1000
            while opened != ["about:blank"]:
                assert time.monotonic() < deadline, "The server never opened the default browser"
                page.wait_ms(50)
            assert page.count(".modal-body a[href='about:blank']") == 1  # the fallback link stays
            assert page.problems() == []


def test_the_name_field_checks_validity_and_availability_as_you_type(monkeypatch, tmp_path):
    """His ask (round 41): a responsive unique username check with a check mark or a cross."""
    account = FixtureAccount("new")
    with community_app(monkeypatch, tmp_path, account) as (base, _):
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(base)
            page.wait_for(".community-form input[autocomplete=nickname]")
            page.evaluate("(() => { const i = document.querySelector('.community-form input[autocomplete=nickname]');"
                          " i.value = 'abc'; i.dispatchEvent(new Event('input', {bubbles: true})); return true; })()")
            wait_until(page, "document.querySelector('.community-name-state')?.dataset.state === 'invalid'", "the too-short cross")
            assert "at least 4" in page.evaluate("document.querySelector('.community-name-state').innerText")
            assert page.evaluate("document.querySelector('.community-form button[type=submit]').disabled")
            page.evaluate("(() => { const i = document.querySelector('.community-form input[autocomplete=nickname]');"
                          " i.value = 'taken'; i.dispatchEvent(new Event('input', {bubbles: true})); i.dispatchEvent(new Event('blur')); return true; })()")
            wait_until(page, "document.querySelector('.community-name-state')?.dataset.state === 'taken'", "the taken cross")
            assert ("check", "taken") in account.calls
            assert page.evaluate("document.querySelector('.community-form button[type=submit]').disabled")
            page.evaluate("(() => { const i = document.querySelector('.community-form input[autocomplete=nickname]');"
                          " i.value = 'Fresh'; i.dispatchEvent(new Event('input', {bubbles: true})); return true; })()")
            wait_until(page, "document.querySelector('.community-name-state')?.dataset.state === 'available'", "the available check mark")
            assert "Available" in page.evaluate("document.querySelector('.community-name-state').innerText")
            page.evaluate("(() => { const boxes = document.querySelectorAll('.community-form .community-check input[type=checkbox]');"
                          " const box = boxes[boxes.length - 1]; box.click(); return box.checked; })()")
            wait_until(page, "!document.querySelector('.community-form button[type=submit]').disabled", "submit enabled for a valid free name")
            assert page.problems() == []


def test_an_existing_account_signs_in_immediately_without_a_name_prompt(monkeypatch, tmp_path):
    """An existing identity finishes sign-in without account editing (round 54)."""
    account = FixtureAccount()
    with community_app(monkeypatch, tmp_path, account) as (base, _):
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(base)
            page.wait_for(".community-provider.community-twitch:not([disabled])")
            page.click(".community-provider.community-twitch")
            wait_until(page, "document.querySelector('.modal-body')?.innerText.includes('Finish signing in')", "the pending screen")
            account.update(authenticated=True, stored=True, status="active", provider="twitch", name="griffman1212",
                           terms_version="t1", privacy_version="p1")
            wait_until(page, "!document.querySelector('.modal')", "returning sign-in completion")
            assert not page.count(".community-form")
            assert ("begin", "twitch") in account.calls and not any(call[0] == "profile" for call in account.calls)
            assert page.problems() == []
