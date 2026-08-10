"""A selector cell's rank slot is the same height ranked or not.

Live report, 2026-08-10, his third on the selector's clipped text and the one
that finally named the discriminator himself: *"The text also wasn't fixed...
(somehow the LBLJ text is still broken, but the others are fine???)"* — his
Castle Lobby row is LBLJ, the one movement there he has practised, beside six
`Bowser 1 -> X` movements he has not.

WHY A CONTRACT TEST AND NOT A PROBE. The responsive sweep asks "is something
broken"; it cannot ask "does this component draw itself the same way in both
of its states". Nothing overflows here and no box clips — the cells simply
disagree with each other by 1.59px, which is invisible to every defect class
and plainly visible to him at the bottom of a fixed-height cell.

WHAT WAS ACTUALLY WRONG, measured rather than reasoned (the first diagnosis
from the screenshot was wrong, and the slot's declared `height: 18px` is why
it looked fine): `.starcell` is a fixed-height column whose contents already
overflow, so every child is a shrink candidate and a declared height is only
a starting point. The slot collapsed to **16px holding a rank MEDAL** (a 16px
image resists) and to **14.41px holding the "–"** (text compresses freely).
`flex: 0 0 18px` is what makes one number mean one number in both states.

Sibling of `tests/test_selector_strat_line_fits.py`, which fixed the line
BELOW this one for the whole row; the two causes hid each other, which is why
this survived that round.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from ui_fixture import serve_ui     # noqa: E402
from uilab.driver import get_driver  # noqa: E402

# His own row, rebuilt in the REAL one so flex-shrink engages exactly as it
# does live -- the same "mutate the real element" technique
# tests/test_log_card_name_fits.py uses, because a synthetic sibling never
# reports the column's true budget. The fixture's Castle Lobby renders LBLJ
# alone, and one cell in a wide row is never asked to shrink at all.
CLONE_HIS_ROW = """
(() => {
  const row = document.querySelector('.stagebanner .starrow');
  const cell = row.querySelector('.starcell');
  if (!cell) return 0;
  const names = ["Bowser 1 \\u2192 BoB", "Bowser 1 \\u2192 WF",
                 "Bowser 1 \\u2192 CCM", "Bowser 1 \\u2192 SSL",
                 "Bowser 1 \\u2192 DDD (Crackslide)",
                 "Bowser 1 \\u2192 BitFS (SBLJ / DDD Skip)"];
  for (const label of names) {
    const clone = cell.cloneNode(true);
    clone.querySelector('.starname').textContent = label;
    clone.querySelector('.starrank').textContent = "\\u2013";   // unranked
    row.appendChild(clone);
  }
  return row.children.length;
})()
"""

MEASURE = """
Array.from(document.querySelectorAll('.stagebanner .starcell')).map(c => {
  const nm = c.querySelector('.starname');
  const rk = c.querySelector('.starrank');
  return {name: nm ? nm.textContent.trim() : null,
          ranked: rk ? rk.children.length > 0 : false,
          slot: rk ? +rk.getBoundingClientRect().height.toFixed(2) : null};
})
"""


def test_a_ranked_cell_and_an_unranked_one_reserve_the_same_slot():
    with serve_ui(castle_stage=1) as url, get_driver().launch() as page:
        page.goto(url + "/ui/index.html")
        page.wait_ms(2500)
        assert page.evaluate(CLONE_HIS_ROW) == 7, (
            "the Castle Lobby row did not render a cell to clone -- the "
            "fixture reached a page nobody is looking at")
        page.wait_ms(400)
        cells = page.evaluate(MEASURE)

    ranked = [c for c in cells if c["ranked"]]
    unranked = [c for c in cells if not c["ranked"]]
    assert ranked and unranked, (
        "this row must hold BOTH states or it proves nothing: "
        f"{[(c['name'], c['ranked']) for c in cells]}")
    heights = {c["slot"] for c in cells}
    assert len(heights) == 1, (
        "the rank slot is a different height depending on what it holds, so "
        "one cell's contents sit lower than its neighbours' and shave "
        "themselves at the bottom of the clip: "
        + ", ".join(f"{c['name']}={c['slot']}"
                    + ("(ranked)" if c["ranked"] else "") for c in cells))
