"""A selector cell's name is never shaved by its own box.

Live report 2026-08-02: *"it looks like the strategy name at the bottom cuts off
the text of the star / segment name a little bit ('Floating Isle' has the bottom
shaved off). I would expect the name of the star / segment to be displayed
without getting cut off; it presently reads like a bug."*

`.starname` clamps to two lines and hides its overflow, and its `min-height` was
2.1em against two line boxes of 1.12em — so the box was SHORTER than the two
lines it promises to show, and every wrapped name lost 0.5–2.5px of ink at every
measured width. The cell is a fixed-height OBS slot whose children had no free
space left, so the name (a flex item) was being shrunk to exactly that
min-height.

No defect probe can see this: nothing overflows a parent, nothing overlaps a
sibling, and both the one-line and two-line cells measure as perfectly ordinary
boxes. What is wrong is the relationship between a box's height and the ink
inside it, which is why this is a contract test of its own — the same reason
`test_objective_name_fits.py` exists one card further down.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab.driver import get_driver   # noqa: E402
from uilab_project import PROJECT     # noqa: E402

# Measures INK, not the box: a Range over the text node reports where the glyphs
# actually end, descenders included. `scrollHeight` alone misses a sub-pixel
# shave, and a screenshot cannot be asserted on.
MEASURE = """
(() => {
  const out = [];
  for (const cell of document.querySelectorAll(".selector-card .starcell")) {
    const name = cell.querySelector(".starname");
    if (!name) continue;
    const range = document.createRange();
    range.selectNodeContents(name);
    const rects = [...range.getClientRects()];
    if (!rects.length) continue;
    const box = name.getBoundingClientRect();
    out.push({
      text: name.textContent, lines: rects.length,
      inkOverflow: +(Math.max(...rects.map((r) => r.bottom)) - box.bottom).toFixed(2),
      hidden: +(name.scrollHeight - name.clientHeight).toFixed(2),
    });
  }
  return JSON.stringify(out);
})()
"""

WIDTHS = (1500, 1200, 900, 850)


@pytest.fixture(scope="module")
def measured():
    out = {}
    with PROJECT.open() as url, get_driver().launch() as page:
        page.goto(url)
        page.wait_for(PROJECT.ready_selector)
        for width in WIDTHS:
            page.set_viewport(width, 1000)
            page.wait_ms(420)
            cells = json.loads(page.evaluate(MEASURE))
            assert cells, f"no selector cells rendered at {width}px"
            out[width] = cells
    return out


@pytest.mark.parametrize("width", WIDTHS)
def test_no_cell_name_is_clipped_by_its_own_box(measured, width):
    shaved = [cell for cell in measured[width]
              if cell["inkOverflow"] > 0 or cell["hidden"] > 0.5]
    assert not shaved, (
        f"at {width}px these names are cut off by .starname's own box: {shaved}. "
        f"`min-height` has to cover the line-clamp AND the font's descenders; "
        f"the room comes from the selector cell's bottom padding, never from "
        f"the row's height (that is a fixed OBS slot).")


def test_the_guard_can_fail(measured):
    """Every assertion above is satisfied by a page with no wrapped names at
    all, which is exactly what a broken fixture would produce. At least one
    name must actually be on two lines for any of this to mean anything."""
    wrapped = {width: sum(1 for cell in cells if cell["lines"] > 1)
               for width, cells in measured.items()}
    assert any(count for count in wrapped.values()), (
        f"no name wrapped to two lines at any width ({wrapped}) — this "
        f"measured nothing, so the fixture is not reaching the real row")
