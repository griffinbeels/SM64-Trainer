# tests/test_card_footer_takes_no_room_when_empty.py
"""An open card's footer takes no room when it holds no controls.

Griffin, 2026-08-05: "we should move the rank standards up a little bit to
tighten up the space vertically... basically just tightening up the gap."

Both of `.attempt-footer`'s children are conditional -- the pagination hides
itself at one page, and `HideToggle` renders nothing when no attempt is hidden
-- so an ordinary short card drew an EMPTY 40px box with a separator line
above it, sitting between the last attempt row and the in-card Rank standards
panel. Measured before the fix: 48px of gap, 40 of it that box and 8 the
panel's own margin. After: 8px.

Only a render can see this. The markup was already correct in both states --
the boxes are the same boxes -- and a purely vertical gap between two siblings
overflows nothing, clips nothing and truncates nothing, so every probe in the
responsive sweep reads the card as perfectly healthy either way.

COVERAGE OWED, stated rather than left silent: the OTHER half -- a footer that
does hold a control keeps its height and its separator -- is not reachable
through `tools/ui_fixture.py`. Its one open card has a single page of attempts
and nothing hidden, so no card on the page renders a footer button at all
(measured, not assumed: `buttons: 0` on the only footer that exists). Reaching
it needs a fixture that seeds either eleven attempts on one entity or a
cleared one, which changes what the whole responsive matrix measures. The
`:has(button)` selector is what keeps that half honest in the meantime -- the
rule cannot fire on a footer that has one.
"""
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from ui_fixture import serve_ui  # noqa: E402

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab import driver  # noqa: E402

MEASURE = """
  (() => {
    const card = Array.from(document.querySelectorAll('.log-card'))
      .find((c) => c.querySelector('.attempt-table') && c.querySelector('.stdpanel'));
    if (!card) return null;
    const table = card.querySelector('.attempt-table');
    const panel = card.querySelector('.stdpanel');
    const footer = card.querySelector('.attempt-footer');
    const cs = footer ? getComputedStyle(footer) : null;
    return {
      gap: Math.round(panel.getBoundingClientRect().top
                      - table.getBoundingClientRect().bottom),
      footerHeight: footer ? Math.round(footer.getBoundingClientRect().height) : null,
      footerButtons: footer ? footer.querySelectorAll('button').length : null,
      footerBorder: cs ? cs.borderTopWidth : null,
    };
  })()
"""


@pytest.fixture(scope="module")
def card():
    with tempfile.TemporaryDirectory() as scratch:
        with serve_ui(Path(scratch) / "footer.db") as base:
            with driver.get_driver().launch(headless=True,
                                            viewport=(1500, 1000)) as page:
                page.goto(base)
                page.evaluate("new Promise(r => setTimeout(r, 2500))")
                return page.evaluate(MEASURE)


def test_the_fixture_reaches_an_open_card_with_a_standards_panel(card):
    """Precondition. Without an open card carrying both the attempt table and
    the panel there is no gap to measure, and every assertion below would be
    about a page nobody drew."""
    assert card is not None, "no open card renders both a table and the panel"
    assert card["footerButtons"] == 0, (
        "this fixture's footer now HOLDS a control, so it is measuring the "
        f"other case and this file's owed-coverage note is stale: {card}")


def test_an_empty_footer_draws_neither_height_nor_a_separator(card):
    assert card["footerHeight"] == 0, (
        f"the empty footer still reserves space: {card}")
    assert card["footerBorder"] == "0px", (
        f"the empty footer still draws its separator line: {card}")


def test_the_standards_panel_sits_close_under_the_last_attempt(card):
    """48px before the fix, 8px after. The bound is generous on purpose --
    this pins "the empty box is gone", not a specific margin somebody may
    still want to tune."""
    assert card["gap"] <= 20, (
        f"the gap above the Rank standards panel is back: {card}")
