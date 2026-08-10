"""The strategy sub-line keeps all of its ink inside the selector's cells.

Live report 2026-08-10: *"the text for the quick selector / segments gets cut
off at the bottom (where it says 'standard' the bottom is cut off)."*

TWO independent shaves produced it, and the responsive sweep could only ever
have seen one of them:

  1. `.selector-card .starrow`'s height subtracted the card's padding and its
     `.shead`, but not the card's own 1px top and bottom BORDER --
     `--selector-height` sets a border-box height, so the row ran 2px taller
     than the space it was given, at every width. The sweep HAD measured that
     one and was carrying it as 78 exempted "scrollHeight 222 > clientHeight
     220" rows rather than as a bug. Those rows are gone; that half now guards
     itself, since an unexempted overflow is a red sweep.

  2. This file's half, which no probe can express. `.starcell` is a
     fixed-height COLUMN flex container whose children overflow it, so every
     child is a shrink candidate -- and `.starsub` clips its own content
     (`overflow: hidden`). Measured at 1400px before the fix: a 16px `.strat`
     line box inside a `.starsub` squeezed to 15.08px, so "Standard" lost the
     bottom of its descenders. The sweep's clipping probe compares a box's
     scrollHeight against its clientHeight; a text node whose LINE BOX is
     taller than its clipping parent by a fraction of a pixel does not move
     either number, which is why three years of green sweeps sat over it.

Mutation-proved: drop `flex-shrink: 0` from `.starsub` and the 1400px case
goes red with the measured 15.08-vs-16 shortfall.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab.driver import get_driver  # noqa: E402
from uilab_project import SUBSECTION_PROJECT  # noqa: E402

# The widths the shave was measured at, plus the supported floor. 1400 is the
# one that actually bit -- the `.strat` font-size clamp tops out there, so the
# line box is at its tallest while the cell is not.
WIDTHS = (850, 1019, 1200, 1400, 1600)

# Every `.starsub` in the selector, with the tallest line box it contains.
# `scrollHeight` on the CHILD is the ink; `clientHeight` on the sub is the
# clip. A sub with no strat line (the "-" placeholder is still a line) is
# reported rather than skipped, so an empty row cannot pass vacuously.
MEASURE = """
(() => {
  const card = document.querySelector('.selector-card');
  if (!card) return {error: 'no selector card on the page'};
  const subs = Array.from(card.querySelectorAll('.starsub'));
  return {
    count: subs.length,
    rows: subs.map(sub => {
      const cell = sub.closest('.starcell');
      const name = cell && cell.querySelector('.starname');
      const ink = Math.max(0, ...Array.from(sub.children)
        .map(child => child.scrollHeight));
      return {
        cell: name ? name.innerText.trim().split('\\n')[0] : '(unnamed)',
        text: sub.innerText.trim(),
        clip: +sub.getBoundingClientRect().height.toFixed(2),
        ink: ink,
        shaved: +(ink - sub.getBoundingClientRect().height).toFixed(2),
      };
    }),
  };
})()
"""


def test_no_strategy_line_is_shaved_by_its_own_cell():
    with SUBSECTION_PROJECT.open() as url, get_driver().launch() as page:
        page.goto(url)
        page.wait_for(SUBSECTION_PROJECT.ready_selector)
        page.wait_ms(400)
        for width in WIDTHS:
            page.set_viewport(width, 1000)
            page.wait_ms(350)
            data = page.evaluate(MEASURE)
            assert not data.get("error"), data
            assert data["count"] >= 1, (
                f"{width}px: no .starsub rendered at all -- the fixture is "
                "measuring a selector with no strategy lines on it, which "
                "would pass this test forever")
            shaved = [row for row in data["rows"] if row["shaved"] > 0.01]
            assert not shaved, (
                f"{width}px: the strategy line is clipped by its own cell in "
                f"{len(shaved)} of {data['count']} cells -- e.g. "
                f"{shaved[0]['text']!r} in {shaved[0]['cell']!r} has "
                f"{shaved[0]['ink']}px of ink in a {shaved[0]['clip']}px box "
                f"({shaved[0]['shaved']}px of descender lost). `.starsub` is "
                "a shrinkable flex item that clips its own content; it needs "
                "flex-shrink: 0.")
