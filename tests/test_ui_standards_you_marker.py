# tests/test_ui_standards_you_marker.py
"""The "◀ you" marker sits on a strategy you have actually run.

His report, 2026-08-15, under "my progress isn't being tracked correctly": the
standards table planted a "you are here" badge in the column of a strategy he
held no time on, because in `pb` rank mode the marker fell back to the
entity's STRATEGY-BLIND personal best and drew it against whichever column was
active. A 3x LJ run therefore marked the Standard ladder.

Driven in a browser because the claim is entirely about what is PAINTED: the
badge, the bracketed cutoffs around it and the already-beaten rows below it
are three CSS classes chosen inside the render, and the payload alone says
nothing about where they land.

Mutation to prove it has teeth: pass `sec.pb` instead of `sec.pb_by_strat` to
`StandardsPanel` in `practicelog.js` and the second test goes red.
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
from standards_panel import BEAT, OPEN_PANEL, SETTLE  # noqa: E402


# Switch the card's ACTIVE strategy through the real endpoint, so the whole
# chain (strat_set -> reprojection -> session view -> panel refetch) runs
# exactly as it does for a click on the picker.
SET_STRAT = """
  (async (strat) => {
    const card = Array.from(document.querySelectorAll('.log-card'))
      .find((c) => c.querySelector('.stdtable'));
    const key = card.getAttribute('data-feed-key');
    const [, course, star] = key.split(':');
    const res = await fetch('/api/strat', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({course_id: Number(course), star_id: Number(star),
                            strat_tag: strat}),
    });
    return res.status;
  })
"""

READ_MARKER = """
  (() => {
    const card = Array.from(document.querySelectorAll('.log-card'))
      .find((c) => c.querySelector('.stdtable'));
    if (!card) return {error: 'no open standards table'};
    const heads = Array.from(card.querySelectorAll(
      '.stdtable thead tr:last-child th')).map((th) => th.textContent.trim());
    const badge = card.querySelector('.std-you-badge');
    const columnOf = (el) => {
      const cell = el.closest('td, th');
      if (!cell) return null;
      return Array.from(cell.parentElement.children).indexOf(cell);
    };
    return {
      heads,
      badge: badge ? badge.textContent.trim() : null,
      badgeColumn: badge ? columnOf(badge) : null,
      brackets: Array.from(card.querySelectorAll('.std-marker-bracket'))
        .map(columnOf),
      beaten: Array.from(card.querySelectorAll('.std-beaten')).map(columnOf),
      pbTag: (card.querySelector('.pbtag') || {}).textContent,
      activeStrat: (card.querySelector('.log-card-strat-picker select')
                    || {}).value,
    };
  })()
"""


@pytest.fixture(scope="module")
def marker_states():
    """The panel read twice: on the fixture's own strategy (which holds a PB)
    and on one it does not."""
    from ui_fixture import FIXTURE_FOREIGN_STRAT, FIXTURE_STRAT

    out = {}
    with tempfile.TemporaryDirectory() as scratch:
        with serve_ui(Path(scratch) / "marker.db") as base:
            with driver.get_driver().launch(headless=True,
                                            viewport=(1500, 1200)) as page:
                page.goto(base)
                page.evaluate(SETTLE)
                assert page.evaluate(OPEN_PANEL), "no standards toggle on any card"
                page.evaluate(BEAT)
                out["with_pb"] = page.evaluate(READ_MARKER)
                assert page.evaluate(
                    f"({SET_STRAT})({FIXTURE_FOREIGN_STRAT!r})") == 200
                page.evaluate(BEAT)
                page.evaluate(BEAT)
                out["without_pb"] = page.evaluate(READ_MARKER)
                out["strats"] = (FIXTURE_STRAT, FIXTURE_FOREIGN_STRAT)
    assert not out["with_pb"].get("error"), out["with_pb"]
    assert not out["without_pb"].get("error"), out["without_pb"]
    return out


def test_the_marker_sits_on_the_strategy_that_holds_the_pb(marker_states):
    """The control, and it is what makes the next test mean something: with a
    PB on the active strategy the badge draws, in that strategy's own column,
    with its two bracketed cutoffs around it."""
    state = marker_states["with_pb"]
    active = marker_states["strats"][0]
    assert state["badge"], state
    column = state["heads"].index(
        next(h for h in state["heads"] if h.startswith(active)))
    assert state["badgeColumn"] == column, state
    assert state["brackets"] and set(state["brackets"]) == {column}, state


def test_a_strategy_you_have_never_run_carries_no_marker(marker_states):
    """The bug: the fallback used to read the entity's strategy-blind PB, so
    switching to a strategy with no saved time of its own kept drawing "you
    are here" — on a ladder he had never run. No time on this strategy now
    means no marker at all, which is the honest state."""
    state = marker_states["without_pb"]
    assert state["badge"] is None, state
    assert state["brackets"] == [], state
    assert state["beaten"] == [], state
    # ... and the card says so in words rather than leaving a blank: the tag
    # names WHICH strategy has nothing, because "no PB yet" would read as the
    # entity having none at all.
    assert "no PB" in (state["pbTag"] or ""), state
    assert marker_states["strats"][1] in (state["pbTag"] or ""), state
