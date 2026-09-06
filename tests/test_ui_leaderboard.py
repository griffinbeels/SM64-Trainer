"""The leaderboard section on the Rank tab (Task 4, spec
2026-08-20-ranked-leaderboard) — draws GET /api/leaderboard's rows.

The default fixture already reaches a populated board with no extra seeding:
`serve_ui()`'s app builds its `LibraryStore` unconditionally off the bundled
Ultimate Sheet snapshot (`core/paths.py::bundled_sheet_library`), so the
default "overall" scope carries real community rows and a real "you" row —
measured directly before writing this file: 443 rows, longest runner name
20 characters ("SullyLikesBigChungus"), the user's own row present and
un-omitted. Seeding a fresh scenario for this test would only recreate what
already renders.
"""
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from ui_fixture import serve_ui           # noqa: E402
from uilab.driver import get_driver        # noqa: E402

CLICK_RANK_TAB = 'document.querySelector(\'.nav-item[title="Rank"]\').click()'

# The board is a CLOSED card by default (fourth read, 2026-08-23): every page
# that needs its rows opens it first, through the real head button.
OPEN_LEADERBOARD = """
  (() => {
    const head = document.querySelector('.leaderboard-card-head');
    if (head && head.getAttribute('aria-expanded') !== 'true') head.click();
    return !!head;
  })()
"""

ROWS = """
JSON.stringify(Array.from(document.querySelectorAll('.leaderboard-row')).map((row) => ({
  isYou: row.classList.contains('is-you'),
  position: row.querySelector('.leaderboard-pos').textContent,
  name: row.querySelector('.leaderboard-name').textContent,
})))
"""


def set_native_value(page, selector, value):
    """The controlled-input trick this repo's own librarynav test uses
    (test_fixture_reaches_the_real_page.py) — a plain `.value = x` bypasses
    Preact's own value tracking, so the framework never sees the change and
    the filter never runs."""
    page.evaluate(f"""
      (() => {{
        const box = document.querySelector({json.dumps(selector)});
        const setter = Object.getOwnPropertyDescriptor(
          window.HTMLInputElement.prototype, 'value').set;
        setter.call(box, {json.dumps(value)});
        box.dispatchEvent(new Event('input', {{bubbles: true}}));
      }})()
    """)


@pytest.fixture(scope="module")
def page():
    with serve_ui() as base, get_driver().launch() as opened:
        opened.goto(f"{base}/ui/index.html")
        opened.wait_for(".log-list-card")
        opened.evaluate(CLICK_RANK_TAB)
        opened.wait_for(".leaderboard-card-head", timeout_ms=15000)
        assert opened.evaluate(OPEN_LEADERBOARD)
        opened.wait_for(".leaderboard-row", timeout_ms=15000)
        opened.wait_ms(500)
        yield opened


def rows(page):
    return json.loads(page.evaluate(ROWS))


def test_the_board_draws_rows_in_order_with_positions(page):
    drawn = rows(page)
    assert len(drawn) > 1, f"expected many rows, drew {len(drawn)}"
    positions = [int(row["position"]) for row in drawn]
    assert positions == sorted(positions), (
        f"rows are not in position order: {positions[:10]}…")
    # Competition ranking (1-2-2-4): the first row is always 1.
    assert positions[0] == 1, f"the first row's position is {positions[0]}, not 1"


def test_exactly_one_row_carries_the_you_marker(page):
    drawn = rows(page)
    you_rows = [row for row in drawn if row["isYou"]]
    assert len(you_rows) == 1, (
        f"expected exactly one .is-you row, found {len(you_rows)}: {you_rows}")
    assert you_rows[0]["name"] == "You", you_rows[0]


def test_typing_in_the_filter_narrows_the_list_and_keeps_your_row(page):
    before = rows(page)
    assert len(before) > 20, "need a real board to prove narrowing at all"
    # A runner name plucked from the real fetched board, not invented — the
    # same "derive the example from the corpus" rule test_ui_core's own
    # search-example lesson names (a hand-picked string can pass through the
    # wrong path). Any named runner more than a few characters in does.
    target = next(row["name"] for row in before
                  if not row["isYou"] and len(row["name"]) >= 6)
    needle = target[:4]
    set_native_value(page, ".leaderboard-find-input", needle)
    page.wait_ms(200)
    try:
        after = rows(page)
        assert len(after) < len(before), (
            f"typing {needle!r} did not narrow the list ({len(before)} -> "
            f"{len(after)})")
        assert any(row["isYou"] for row in after), (
            "the user's own row disappeared once the filter matched fewer "
            "names -- it must never be filtered out")
        assert all(row["isYou"] or needle.lower() in row["name"].lower()
                   for row in after), (
            f"a row that does not match {needle!r} survived the filter: {after}")
    finally:
        set_native_value(page, ".leaderboard-find-input", "")
        page.wait_ms(200)


def test_the_basis_line_names_pb(page):
    text = page.evaluate(
        "document.querySelector('.leaderboard-basis').textContent")
    assert "PB" in text, f"the basis line never says PB: {text!r}"


YOU_COVERAGE = """
  (() => {
    const you = document.querySelector('.leaderboard-row.is-you .leaderboard-coverage');
    return you ? you.textContent.trim() : null;
  })()
"""


def wait_for_coverage(page, denominator):
    """Wait for the changed value, not the row that was already visible."""
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        value = page.evaluate(YOU_COVERAGE)
        if value and value.split("/")[-1] == str(denominator):
            return value
        page.wait_ms(25)
    raise AssertionError(f"coverage never reached denominator {denominator}; last value {value!r}")


def test_excluding_an_entity_narrows_the_board_like_your_own_tab(page):
    """Round 1, third read (2026-08-23), reversing the design-session rule
    and fix wave M2's note: "if I have personally excluded certain segments
    ... it should also be excluded for all of the fake leaderboards & their
    pages as well." So excluding an entity shrinks the board's denominator
    -- your own row's `practiced/n` drops by one on the next fetch -- and
    the basis line no longer has a discrepancy to explain. Drives the REAL
    control (the Breakdown table's own Ignore button), not the API."""
    before = page.evaluate(YOU_COVERAGE)
    assert before and "/" in before, before
    n_before = int(before.split("/")[1])
    assert "excluded" not in page.evaluate(
        "document.querySelector('.leaderboard-basis').textContent").lower()
    clicked = page.evaluate("""
      (() => {
        const btn = Array.from(document.querySelectorAll('.rank-breakdown button.chip'))
          .find((candidate) => candidate.textContent.trim() === 'Ignore');
        if (!btn) return false;
        btn.click();
        return true;
      })()
    """)
    assert clicked, "no practiced entity with an Ignore button on the Rank tab"
    try:
        during = wait_for_coverage(page, n_before - 1)
        assert int(during.split("/")[1]) == n_before - 1, (
            f"excluding an entity did not narrow the board: {before!r} -> {during!r}")
    finally:
        # Restore -- a driven test that edits the real exclusion set must
        # not leave it edited for the next test in this module-scoped page
        # (ui-core.md's own rule for anything that writes to the real store).
        restored = page.evaluate("""
          (() => {
            const btn = Array.from(document.querySelectorAll('.rank-breakdown button.chip'))
              .find((candidate) => candidate.textContent.trim() === 'Include');
            if (!btn) return false;
            btn.click();
            return true;
          })()
        """)
        assert restored, "could not find the Include button to undo the exclusion"
        after = wait_for_coverage(page, n_before)
        assert int(after.split("/")[1]) == n_before, f"the exclusion survived undo: {after!r}"


def test_the_omitted_count_is_stated_not_a_footnote(page):
    text = page.evaluate(
        "document.querySelector('.leaderboard-omitted').textContent")
    assert text.strip(), "the omitted line rendered empty"


def test_jump_to_you_scrolls_the_board_toward_your_row(page):
    """The user's row sits deep in a 400+ row board — reach without hunting
    is the contract, so the jump control must actually move the scroll
    position, not merely exist."""
    page.evaluate("""
      (() => {
        document.querySelector('.leaderboard-body').scrollTop = 0;
      })()
    """)
    page.wait_ms(100)
    before = page.evaluate(
        "document.querySelector('.leaderboard-body').scrollTop")
    page.evaluate(
        "Array.from(document.querySelectorAll('.leaderboard-jump'))[0].click()")
    page.wait_ms(200)
    after = page.evaluate(
        "document.querySelector('.leaderboard-body').scrollTop")
    assert after > before, (
        f"the jump-to-you button did not move the board's scroll position "
        f"({before} -> {after})")


def test_your_own_row_click_is_still_a_no_op(page):
    """Task 5 wired every OTHER row to the runner's page
    (tests/test_ui_runner_page.py owns that door); your own row has no
    `runner` name to open and stays inert -- clicking it must not throw or
    navigate away from the Rank tab."""
    page.evaluate("document.querySelector('.leaderboard-row.is-you').click()")
    page.wait_ms(100)
    assert page.count(".leaderboard-row") > 0, (
        "the Rank tab navigated away or crashed after clicking your own row")
    assert page.count(".runner-page") == 0, (
        "clicking your own row opened a runner page")
