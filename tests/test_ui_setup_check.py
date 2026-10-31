"""The setup check on screen: the practice card's ghost notice, Settings →
Check my setup, and the setup wizard's completion notes.

The comparison drawn is the real `SetupProfile`'s answer for the AMD tester's
shape (stock Project64 1.6 under Program Files, Jabo plugins, no Usamune rdb
entry), served through the real `/api/setup/check` and `/api/setup` routes.
Screenshots land in pytest's tmp folder for review at 850 px and wide.
"""
import contextlib
from copy import deepcopy
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from playwright.sync_api import sync_playwright  # noqa: E402

import ui_fixture  # noqa: E402
from ghost_fixture import ghost_session, lineup_rivals, serve_ui_with_ghost  # noqa: E402
from tests.browser_faults import no_faults, watch  # noqa: E402
from tests.test_setup_profile import stock_check  # noqa: E402

CARD = ".log-card.log-card-active"
NOTICE = f"{CARD} [data-testid=ghost-setup-notice]"
CHECK = "[data-testid=setup-check]"
SETTINGS_CONTROL = '.nav-item.settings-link, button[aria-label="Open settings"]'
WIDTHS = (850, 1500)
WORDS = "Ghosts aren't showing. Your Project64 setup is different from the tested setup."


class ServedCheck:
    """The setup check the app's routes serve: whatever the test holds now."""

    def __init__(self):
        self.value = None

    def compare(self, layer, rom):
        return deepcopy(self.value)

    def diagnostic_status(self, *_args):
        return deepcopy(self.value)


@contextlib.contextmanager
def with_setup_check(served: ServedCheck):
    real = ui_fixture.create_app

    def create_app(*args, **kwargs):
        return real(*args, **{**kwargs, "setup_profile": served})

    ui_fixture.create_app = create_app
    try:
        yield
    finally:
        ui_fixture.create_app = real


def matching(check):
    return {**check, "flagged": [], "required_differs": 0, "unknown": 0,
            "items": [{**item, "status": "match", "fix": None} for item in check["items"]]}


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    check = ServedCheck()
    check.value = stock_check(tmp_path_factory.mktemp("stock"))
    session = {**ghost_session(enabled=True), "rivals": lineup_rivals(), "display_stalled": True}
    with with_setup_check(check), serve_ui_with_ghost(deepcopy(session)) as (base, state):
        with sync_playwright() as play:
            browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
            try:
                yield base, state, check, session, browser
            finally:
                browser.close()


@contextlib.contextmanager
def opened(served, width, *, stalled=True, check=None):
    base, state, served_check, session, browser = served
    state["session"] = {**deepcopy(session), "display_stalled": stalled}
    original = served_check.value
    if check is not None:
        served_check.value = check
    context = browser.new_context(viewport={"width": width, "height": 1000})
    page = context.new_page()
    page.set_default_timeout(15000)
    seen = watch(page)
    page.route("**/api/replay/available", lambda route: route.fulfill(json={"available": []}))
    try:
        page.goto(base)
        page.locator(f"{CARD} .ghost-row").first.wait_for()
        yield page, seen
    finally:
        served_check.value = original
        page._retire_observed_requests()
        context.close()


ITEMS = """(root) => [...document.querySelectorAll(root + ' .setup-check-item')].map((li) => {
  const box = li.getBoundingClientRect();
  const parent = li.closest('.modal, .setup-check-notes').getBoundingClientRect();
  return { id: li.dataset.item, status: li.dataset.status, highlighted: li.classList.contains('is-highlighted'),
    text: li.innerText, inside: box.left >= parent.left - 0.5 && box.right <= parent.right + 0.5,
    overflow: [...li.querySelectorAll('dd, p, strong')].some((n) => n.scrollWidth > n.clientWidth + 1),
    link: (li.querySelector('a') || {}).href || null };
})"""


@pytest.mark.parametrize("width", WIDTHS)
def test_unseen_ghosts_with_a_different_setup_show_the_notice_and_open_the_check(served, width, tmp_path):
    with opened(served, width) as (page, seen):
        notice = page.locator(NOTICE)
        notice.wait_for()
        assert notice.locator("p").inner_text() == WORDS
        card = page.locator(CARD).bounding_box()
        box = notice.bounding_box()
        assert card["x"] - 0.5 <= box["x"] and box["x"] + box["width"] <= card["x"] + card["width"] + 0.5
        (tmp_path / f"setupbase-notice-{width}.png").write_bytes(page.locator(CARD).screenshot())
        notice.get_by_role("button", name="Check my setup").click()
        page.locator(f"{CHECK} .setup-check-item").first.wait_for()
        items = page.evaluate(ITEMS, CHECK)
        flagged = served[2].value["flagged"]
        assert [row["id"] for row in items if row["highlighted"]] == flagged
        assert [row["id"] for row in items][:len(flagged)] == flagged   # differences first
        assert items[-1]["id"] == "controller_plugin" and "Noted" in items[-1]["text"]
        assert "That is fine" in items[-1]["text"] and items[-1]["link"] is None
        assert all(row["inside"] and not row["overflow"] for row in items), items
        graphics = next(row for row in items if row["id"] == "graphics_plugin")
        assert "Jabo_Direct3D8.dll" in graphics["text"] and graphics["link"].startswith("https://wermi")
        assert "7 settings are different from the tested setup." in page.locator(CHECK).inner_text()
        page.wait_for_timeout(300)
        (tmp_path / f"setupbase-check-{width}.png").write_bytes(page.locator(".modal").screenshot())
        no_faults(seen)


@pytest.mark.parametrize("stalled,matches", [(True, True), (False, False)])
def test_a_matching_setup_or_shown_ghosts_add_nothing_new(served, stalled, matches):
    """A matching setup that still fails is our bug: the report carries it,
    and the player sees nothing new. Shown ghosts never ask for the check."""
    check = matching(served[2].value) if matches else None
    with opened(served, 850, stalled=stalled, check=check) as (page, seen):
        requests = []
        page.on("request", lambda request: requests.append(request.url) if "/api/setup/check" in request.url else None)
        page.wait_for_timeout(1200)
        assert page.locator(NOTICE).count() == 0
        if not stalled:
            assert requests == []
        no_faults(seen)


def test_the_notice_guard_can_fail(served):
    """Control: the same page with a flagged check does show the notice, so
    the empty results above measured the rule, not a page that never drew."""
    with opened(served, 850) as (page, _seen):
        page.locator(NOTICE).wait_for()
        assert page.locator(NOTICE).count() == 1


def test_settings_lists_every_item(served, tmp_path):
    with opened(served, 1500, stalled=False) as (page, seen):
        page.locator(SETTINGS_CONTROL).first.click()
        page.get_by_role("button", name="Check my setup").click()
        page.locator(f"{CHECK} .setup-check-item").first.wait_for()
        items = page.evaluate(ITEMS, CHECK)
        assert len(items) == 10 and not any(row["highlighted"] for row in items)
        assert {row["status"] for row in items[:7]} == {"differs"}
        assert {row["id"] for row in items if row["status"] == "unknown"} == {"counter_factor", "cpu_core"}
        (tmp_path / "setupbase-settings-check.png").write_bytes(page.locator(".modal").screenshot())
        page.evaluate("document.querySelector('.modal .modal-body').scrollTo({top: 1e6, behavior: 'instant'})")
        page.wait_for_timeout(100)
        (tmp_path / "setupbase-settings-check-end.png").write_bytes(page.locator(".modal").screenshot())
        no_faults(seen)

