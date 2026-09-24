"""The Daily Star as he approved it (task 0111, rounds 1-2), driven in the real app.

Pins the approved outcomes, not the prototype's shortcuts:
- the announcement appears once per local date: Day N, the star, Your PB and
  Next Goal each with a rank line, and a TIME LEFT that ticks; "Got it" ends
  it for that date, and a reload does not bring it back;
- the day's cell on the star select row wears the foil and says so on hover;
- the star's name opens its Library page;
- open across midnight, it says TIMES UP! and turns to the next day's star.

The page clock is pinned so the fixture's own stage (Whomp's Fortress) holds
the day's star: 2026-09-23 is Day 1, "Shoot into the Wild Blue".
"""
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from ui_fixture import serve_ui  # noqa: E402



def local(*wall):
    """The instant this machine's clock reads `wall` (y, m, d, h, min, s), in
    whatever timezone the test runs -- the Daily Star turns at LOCAL midnight."""
    return datetime.fromtimestamp(time.mktime((*wall, 0, 0, -1)), tz=timezone.utc)


DAY_ONE = local(2026, 9, 23, 12, 0, 0)
MODAL = ".modal-daily"


@pytest.fixture(scope="module")
def base():
    with serve_ui() as url:
        yield url


@pytest.fixture
def browser():
    with sync_playwright() as play:
        launched = play.chromium.launch(headless=True)
        try:
            yield launched
        finally:
            launched.close()


def open_app(browser, base, at):
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.request.post(base + "/api/daily-star/seen", data={"date": "2000-01-01"})
    page.clock.install(time=at)
    page.goto(base + "/")
    return page, errors


def test_the_announcement_shows_once_per_date(browser, base):
    page, errors = open_app(browser, base, DAY_ONE)
    page.wait_for_selector(f"{MODAL} .daily-card", timeout=15000)
    assert page.inner_text(f"{MODAL} .daily-where").upper() == "DAY 1 · WHOMP'S FORTRESS"
    assert page.inner_text(f"{MODAL} .daily-name").strip() == "Shoot into the Wild Blue"
    assert page.locator(f"{MODAL} .modal-heading p").count() == 0      # no header blurb
    times = page.locator(f"{MODAL} .daily-times > div")
    assert [times.nth(i).locator("dt").inner_text().upper() for i in range(2)] \
        == ["YOUR PB", "NEXT GOAL"]
    assert page.locator(f"{MODAL} .daily-times .daily-rank").count() >= 1
    first = page.inner_text(f"{MODAL} .daily-timer b")
    page.wait_for_function(
        f"document.querySelector('{MODAL} .daily-timer b').textContent !== {first!r}",
        timeout=5000)
    page.click(f"{MODAL} .primary-button")
    page.wait_for_selector(MODAL, state="detached", timeout=5000)
    page.reload()
    page.wait_for_selector(".stagebanner .starcell", timeout=15000)
    time.sleep(2.5)                     # the announcement waits ~2 s for a quiet screen
    assert page.locator(MODAL).count() == 0
    assert errors == []
    page.close()


def test_the_days_cell_wears_the_foil_and_says_so(browser, base):
    page, errors = open_app(browser, base, DAY_ONE)
    page.wait_for_selector(f"{MODAL} .daily-card", timeout=15000)
    page.click(f"{MODAL} .primary-button")
    cells = page.locator(".stagebanner .starcell.daily-star-foil")
    cells.first.wait_for(timeout=15000)
    assert cells.count() == 1
    assert "Today's Daily Star" in cells.first.get_attribute("title")
    assert "Shoot into the Wild Blue" in cells.first.inner_text()
    assert errors == []
    page.close()


def test_the_name_opens_its_library_page(browser, base):
    page, errors = open_app(browser, base, DAY_ONE)
    page.wait_for_selector(f"{MODAL} .daily-name-link .daily-name-book", timeout=15000)
    page.click(f"{MODAL} .daily-name-link")
    page.wait_for_selector(MODAL, state="detached", timeout=5000)
    target = page.locator(".library-target").first
    target.wait_for(timeout=15000)
    assert target.inner_text().splitlines()[0].strip() == "Shoot into the Wild Blue"
    assert errors == []
    page.close()


def test_midnight_says_times_up_and_turns_to_the_next_star(browser, base):
    page, errors = open_app(browser, base, local(2026, 9, 23, 23, 59, 55))
    page.wait_for_selector(f"{MODAL} .daily-card", timeout=15000)
    page.wait_for_selector(f"{MODAL} .daily-timer.times-up", timeout=15000)
    assert page.inner_text(f"{MODAL} .daily-timer").strip() == "TIMES UP!"
    page.wait_for_function(
        f"document.querySelector('{MODAL} .daily-page:not(.turn-out) .daily-where')"
        ".textContent.toUpperCase().startsWith('DAY 2')", timeout=15000)
    page.wait_for_selector(f"{MODAL} .daily-page.turn-out", state="detached", timeout=5000)
    assert page.inner_text(f"{MODAL} .daily-name").strip() == "The Tip Top of the Huge Island"
    assert page.inner_text(f"{MODAL} .daily-timer b").startswith("23:59:")
    assert errors == []
    page.close()
