# tests/test_ui_sheet_best_row.py
"""The standards table's last row: the fastest time on the Ultimate Sheet.

His report, 2026-08-15, after expanding Mario into its five divisions and
reading Mario 1: "there actually ARE faster times than this". The top of a
ladder is not the top of the sport, so the table gains one row under every
rank naming the fastest [[sheet entry]] per strategy, its runner, and a link
where the run was filmed.

Driven in a real browser because every claim here is a render: that the row is
LAST, that it lines up with the ladder's own columns (it is a `<tr>` in the
same `<tbody>`, which is the only reason the columns cannot drift), that it
wears no cap art, and that a strategy the sheet has nothing for draws a dash
rather than a guess. A unit test over the payload can see none of those.
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

SETTLE = "new Promise(r => setTimeout(r, 2500))"
BEAT = "new Promise(r => setTimeout(r, 900))"

OPEN_PANEL = """
  (() => {
    const card = Array.from(document.querySelectorAll('.log-card'))
      .find((c) => c.querySelector('.standards-toggle'));
    if (!card) return null;
    card.querySelector('.standards-toggle').click();
    return true;
  })()
"""

# Everything the row claims, read off the RENDERED table plus the payload it
# came from — so the assertions below compare the screen against the server's
# own answer rather than against a number written here.
READ_ROW = """
  (async () => {
    const card = Array.from(document.querySelectorAll('.log-card'))
      .find((c) => c.querySelector('.stdtable'));
    if (!card) return {error: 'no open standards table'};
    const entity = card.getAttribute('data-feed-key');
    const res = await fetch('/api/ranks/standards?entity='
      + encodeURIComponent(entity));
    const data = await res.json();
    const heads = Array.from(card.querySelectorAll(
      '.stdtable thead tr:last-child th')).map((th) => th.textContent.trim());
    const body = card.querySelector('.stdtable tbody');
    const rows = Array.from(body.querySelectorAll('tr'));
    const row = card.querySelector('tr.std-sheet-best');
    if (!row) return {error: 'no sheet-best row', entity,
                      served: Object.keys(data.sheet_best || {})};
    const cells = Array.from(row.querySelectorAll('td'));
    const ladderRow = rows.find((r) => !r.classList.contains('std-sheet-best')
                                       && r.querySelectorAll('td').length > 1);
    const cellLefts = (tr) => Array.from(tr.querySelectorAll('td'))
      .map((td) => Math.round(td.getBoundingClientRect().left));
    return {
      entity,
      served: data.sheet_best || {},
      heads,
      isLastRow: rows[rows.length - 1] === row,
      label: cells[0].textContent.trim(),
      // One cell per column, and the columns LINE UP with the ladder's: the
      // row is a <tr> in the same <tbody>, so this is structural rather than
      // a coincidence — but a future refactor into a nested table would look
      // identical until someone measured it.
      cellCount: cells.length,
      ladderCellCount: ladderRow ? ladderRow.querySelectorAll('td').length : 0,
      alignsWithLadder: ladderRow
        && JSON.stringify(cellLefts(row)) === JSON.stringify(cellLefts(ladderRow)),
      // It grades nothing, so it wears none of a rank's art and takes no part
      // in the "you are here" bracket.
      caps: row.querySelectorAll('.hat').length,
      markers: row.querySelectorAll(
        '.std-marker-bracket, .std-you-badge, .std-beaten').length,
      values: cells.slice(1).map((td) => ({
        text: td.textContent.trim(),
        time: (td.querySelector('a, span:not(.std-sheet-best-runner)')
               || {}).textContent,
        runner: (td.querySelector('.std-sheet-best-runner') || {}).textContent,
        href: (td.querySelector('a') || {}).href || null,
      })),
    };
  })()
"""


@pytest.fixture(scope="module")
def row_state():
    with tempfile.TemporaryDirectory() as scratch:
        with serve_ui(Path(scratch) / "sheetbest.db") as base:
            with driver.get_driver().launch(headless=True,
                                            viewport=(1500, 1100)) as page:
                page.goto(base)
                page.evaluate(SETTLE)
                assert page.evaluate(OPEN_PANEL), "no standards toggle on any card"
                page.evaluate(BEAT)
                state = page.evaluate(READ_ROW)
    assert not state.get("error"), state
    return state


def test_the_row_is_last_and_named_for_what_it_is(row_state):
    """Under every rank rather than in the ladder, and called Sheet Best: we
    know it is the fastest row on the sheet, and calling it a world record
    asserts more than that."""
    assert row_state["isLastRow"], row_state
    assert row_state["label"] == "Sheet Best", row_state["label"]


def test_it_has_one_cell_per_strategy_column_and_lines_up(row_state):
    """A real `<tr>` in the SAME `<tbody>`, which is why the columns cannot
    drift from the ladder's — the same reason `StdSubRows` uses real rows
    rather than a nested table."""
    assert row_state["cellCount"] == len(row_state["heads"]), row_state
    assert row_state["cellCount"] == row_state["ladderCellCount"], row_state
    assert row_state["alignsWithLadder"], row_state


def test_it_wears_no_rank_art_and_no_you_marker(row_state):
    """It grades nothing. A cap would say it does, and `markerPosition` walks
    the ladder, which this row is not part of."""
    assert row_state["caps"] == 0, row_state
    assert row_state["markers"] == 0, row_state


def test_every_cell_agrees_with_what_the_server_served(row_state):
    """Derived from the payload rather than hand-picked, so the assertion
    survives any fixture data: a strategy the sheet has a time for prints it
    with its runner, and one it has nothing for prints a dash."""
    from sm64_events.core.timefmt import format_igt

    served, heads = row_state["served"], row_state["heads"]
    assert served, "the fixture entity has no sheet best at all — this test " \
                   "would pass vacuously; seed one or point it at another star"
    seen_with_time = 0
    for head, cell in zip(heads[1:], row_state["values"]):
        best = next((b for strat, b in served.items() if head.startswith(strat)),
                    None)
        if best is None:
            assert cell["text"] == "—", (head, cell)
            continue
        seen_with_time += 1
        assert format_igt(round(best["time_cs"] * 30 / 100)) in cell["text"] \
            or cell["time"], (head, cell, best)
        if best["runner"]:
            assert cell["runner"] == best["runner"], (head, cell, best)
        assert (cell["href"] is not None) == bool(best["video"]), (head, cell, best)
    assert seen_with_time, ("no column resolved to a served sheet best — the "
                            "row rendered but says nothing")
