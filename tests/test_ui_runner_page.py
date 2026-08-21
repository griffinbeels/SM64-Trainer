"""The runner page (Task 5, spec 2026-08-20-ranked-leaderboard) and its two
doors: a leaderboard row (leaderboard.js) and a runner's name inside a
Library target page (librarytarget.js).

`serve_ui(arm_segment=FIXTURE_SEGMENT, seed_editor_fixtures=True)` is the
exact fixture test_ui_library_target.py already uses to reach a real,
populated target page (star:2:4, four real bundled-sheet approaches) — and
per test_ui_leaderboard.py's own docstring the SAME server also carries a
real 443-runner board with no extra seeding, since `LibraryStore` loads the
bundled Ultimate Sheet snapshot unconditionally. One server serves both
doors; no invented data.
"""
import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH")

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from ui_fixture import FIXTURE_SEGMENT, serve_ui  # noqa: E402
from uilab import driver  # noqa: E402

CLICK_RANK_TAB = 'document.querySelector(\'.nav-item[title="Rank"]\').click()'
CLICK_LIBRARY_TAB = 'document.querySelector(\'.nav-item[title="Library"]\').click()'
# Subdivision groups ship collapsed by default -- same helper
# test_ui_library_target.py uses to reveal the ExampleCard/PlainEntry rows a
# runner's name lives in.
EXPAND_DIVISIONS = (
    "Array.from(document.querySelectorAll("
    "'.library-section.open .library-division-head')).forEach((head) => "
    "head.getAttribute('aria-expanded') === 'true' || head.click())")

HEADERS = "Array.from(document.querySelectorAll('.rank-breakdown th')).map(e => e.textContent.trim())"


@pytest.fixture(scope="module")
def server():
    with serve_ui(arm_segment=FIXTURE_SEGMENT, seed_editor_fixtures=True) as base:
        yield base


@pytest.fixture
def rank_page(server):
    """A fresh page on the Rank tab -- the user's own board, nobody's runner
    page open yet."""
    with driver.get_driver().launch(headless=True) as page:
        page.goto(f"{server}/ui/index.html")
        page.wait_for(".log-list-card", timeout_ms=20000)
        page.evaluate(CLICK_RANK_TAB)
        page.wait_for(".leaderboard-row", timeout_ms=15000)
        page.wait_ms(200)
        yield page


@pytest.fixture
def library_page(server):
    """A fresh page auto-opened onto a real target page with real community
    entries, divisions expanded so a runner's name is on screen."""
    with driver.get_driver().launch(headless=True) as page:
        page.goto(f"{server}/ui/index.html")
        page.wait_for(".log-list-card", timeout_ms=20000)
        page.evaluate(CLICK_LIBRARY_TAB)
        page.wait_for(".library-target .library-section", timeout_ms=15000)
        page.evaluate(EXPAND_DIVISIONS)
        page.wait_ms(200)
        yield page


def click_a_runner_row(page):
    """The leaderboard's own door: click the first row that is not the
    user's, return the runner name it named."""
    name = page.evaluate("""
      (() => {
        const row = Array.from(document.querySelectorAll('.leaderboard-row'))
          .find((candidate) => !candidate.classList.contains('is-you'));
        if (!row) return null;
        const name = row.querySelector('.leaderboard-name').textContent;
        row.click();
        return name;
      })()
    """)
    assert name, "no non-you leaderboard row to click"
    return name


def assert_reads_as_the_runner_page(page, expected_name):
    page.wait_for(".runner-page", timeout_ms=8000)
    page.wait_ms(200)
    heading = page.evaluate("document.querySelector('.runner-page h2').textContent")
    assert heading == expected_name, (
        f"the runner page opened for {heading!r}, expected {expected_name!r}")
    assert page.count(".runner-page .scope-chip") > 0, (
        "the runner page drew no scope chips")


def assert_no_editing_controls_on_the_runner_page(page):
    """Read-only, contract-mandated: no Ignore/Include control, no ✎ icon-
    repoint affordance, anywhere on the runner page."""
    ignore_buttons = page.evaluate(
        "document.querySelectorAll('.runner-page .rank-breakdown tbody button').length")
    assert ignore_buttons == 0, (
        f"found {ignore_buttons} button(s) in the runner page's breakdown rows "
        "-- the runner variant must carry no Ignore/Include control")
    edit_icons = page.evaluate("document.querySelectorAll('.runner-page .editicon').length")
    assert edit_icons == 0, f"found {edit_icons} ✎ icon(s) on the runner page"


# ---- Step 1's own three assertions, in one test ---------------------------

def test_the_runner_page_draws_name_chips_and_breakdown_with_no_editing_controls(rank_page):
    name = click_a_runner_row(rank_page)
    assert_reads_as_the_runner_page(rank_page, name)
    headers = rank_page.evaluate(HEADERS)
    assert "Their time" in headers and "Your time" in headers and "Gap" in headers, headers
    assert "Score (pts)" not in headers and "Gain (pts)" not in headers, headers
    assert_no_editing_controls_on_the_runner_page(rank_page)


def test_the_users_own_rank_page_still_shows_both(rank_page):
    """The regression guard for the `variant` change: before ever opening a
    runner, the user's OWN breakdown still carries its Ignore/Include
    control and the coverage strip still offers ✎."""
    headers = rank_page.evaluate(HEADERS)
    assert "Score (pts)" in headers and "Gain (pts)" in headers, headers
    assert "Their time" not in headers and "Gap" not in headers, headers
    ignore_or_include = rank_page.evaluate("""
      Array.from(document.querySelectorAll('.rank-page .rank-breakdown tbody button'))
        .some((btn) => btn.textContent.trim() === 'Ignore' || btn.textContent.trim() === 'Include')
    """)
    assert ignore_or_include, "no Ignore/Include button found on the user's own breakdown"
    edit_icons = rank_page.evaluate("document.querySelectorAll('.rank-page .editicon').length")
    assert edit_icons > 0, "no ✎ icon found in the user's own coverage strip"


def test_back_returns_to_the_leaderboard(rank_page):
    click_a_runner_row(rank_page)
    rank_page.wait_for(".runner-page", timeout_ms=8000)
    rank_page.evaluate("document.querySelector('.runner-page .entity-back').click()")
    rank_page.wait_for(".leaderboard-row", timeout_ms=8000)
    assert rank_page.count(".runner-page") == 0, "the runner page never closed"


# ---- Fix round 1: the coverage tile must not mix two people's numbers -----
# CoverageStrip's EntityDetail panel reads t.view -- the VIEWING user's own
# attempts/PB -- never the runner's. A lit tile on the runner page must not
# open it: EntityDetail would print the runner's MARELO beside the reader's
# own PB with nothing saying whose was whose (reviewer's live repro,
# darkdog47: "9575 pts · PB 0'11\"43", where 9575 is the runner's score and
# 11"43 is the reviewer's own PB).

def click_a_lit_tile(page, scope_selector):
    """Click the first PRACTICED (non-`.is-unpracticed`) coverage tile inside
    `scope_selector`, return whether one was found."""
    return page.evaluate(f"""
      (() => {{
        const tile = document.querySelector(
          {json.dumps(scope_selector)} + ' .entity-tile:not(.is-unpracticed)');
        if (!tile) return false;
        tile.click();
        return true;
      }})()
    """)


def test_clicking_a_lit_coverage_tile_opens_nothing_on_the_runner_page(rank_page):
    click_a_runner_row(rank_page)
    rank_page.wait_for(".runner-page", timeout_ms=8000)
    rank_page.wait_ms(200)
    found = click_a_lit_tile(rank_page, ".runner-page")
    assert found, "no practiced coverage tile on the runner page to click"
    rank_page.wait_ms(200)
    assert rank_page.count(".runner-page .entity-detail") == 0, (
        "clicking a coverage tile on the runner page opened EntityDetail -- "
        "that panel reads the VIEWING USER's own attempts/PB, not the "
        "runner's, and must never open here")
    # Dead-control contract: the tile must not look clickable either.
    static_tiles = rank_page.evaluate(
        "document.querySelectorAll('.runner-page .entity-tile.is-static').length")
    assert static_tiles > 0, "no coverage tile carries .is-static on the runner page"
    cursor = rank_page.evaluate("""
      getComputedStyle(document.querySelector('.runner-page .entity-tile')).cursor
    """)
    assert cursor == "default", f"a runner-page coverage tile still shows cursor: {cursor!r}"


def test_clicking_a_lit_coverage_tile_still_opens_the_panel_on_your_own_tab(rank_page):
    """The regression guard for this fix round: before ever opening a
    runner, the user's OWN coverage tile is still a real control."""
    found = click_a_lit_tile(rank_page, ".rank-page")
    assert found, "no practiced coverage tile on the user's own tab to click"
    rank_page.wait_ms(200)
    assert rank_page.count(".rank-page .entity-detail") == 1, (
        "clicking a practiced coverage tile on the user's own tab did not "
        "open EntityDetail")
    static_tiles = rank_page.evaluate(
        "document.querySelectorAll('.rank-page .entity-tile.is-static').length")
    assert static_tiles == 0, (
        f"found {static_tiles} .is-static tile(s) on the user's own tab -- "
        "every tile there must stay interactive")


# ---- the second door: a runner's name inside the Library ------------------

def test_a_library_entry_runners_name_opens_the_same_runner_page(library_page):
    name = library_page.evaluate("""
      (() => {
        const link = document.querySelector('.library-target .library-runner-link');
        if (!link) return null;
        const name = link.textContent;
        link.click();
        return name;
      })()
    """)
    assert name, "no clickable runner name found on the opened target page"
    assert_reads_as_the_runner_page(library_page, name)
    assert_no_editing_controls_on_the_runner_page(library_page)


def test_the_syntheticyou_row_never_offers_a_runner_link(library_page):
    """`entry._isYou` (the leaderboard-mode PB insert, librarymodel.js) has
    no runner behind it, and must never render as a clickable name."""
    switched = library_page.evaluate("""
      (() => {
        const seg = Array.from(document.querySelectorAll('.library-mode-seg'))
          .find((btn) => btn.textContent.trim() === 'Leaderboard');
        if (!seg) return false;
        seg.click();
        return true;
      })()
    """)
    if not switched:
        pytest.skip("no open section carries the Leaderboard mode switch")
    library_page.wait_ms(200)
    you_is_a_link = library_page.evaluate("""
      (() => {
        const row = document.querySelector('.library-leaderboard-row.is-you');
        return !!(row && row.querySelector('.library-runner-link'));
      })()
    """)
    assert not you_is_a_link, "the synthetic You row rendered as a clickable runner link"
