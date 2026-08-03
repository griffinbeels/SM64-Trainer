"""Every star name fits `.log-card-name b`, on one line, at every width.

Modeled on tests/test_objective_name_fits.py -- same corpus, same reasoning,
same reason this exists: the fixture only ever seeds ONE star ("Fall onto the
Caged Island"), and 0 of 97 corpus names ellipsising past a fixed pane at
850/900px is a completely different claim than one name not doing it. This is
what would have caught the final-review's item 1 -- `.log-card-head`'s own
comment claimed no truncation was reachable here, measured against a technique
that could not have detected it (see index.html's own corrected comment).

ONE real difference from the objective card's version, and it changes the
measuring technique: `.objective-name h2` is a plain block element filling its
own single-purpose grid column, so its `clientWidth` IS the column's budget
regardless of what text is inside it -- a synthetic probe span can be compared
against it directly. `.log-card-name` instead sits in a FLEX row
(`.log-card-select`, alongside the entity art) with no `flex-grow`, so a flex
item with room to spare just sits at its own CONTENT width and never reports
the column's true budget. Comparing a synthetic probe against `b.clientWidth`
measured a constant 195px at every width from 1920 down to 850 in early
testing here -- the box was simply never being asked to shrink by whatever
text happened to be rendered at the time.

So this test renders each candidate name into the REAL `b` element (a genuine
DOM mutation, not a synthetic sibling) and reads its own scrollWidth vs
clientWidth immediately after -- flex-shrink then engages exactly as it would
for a real practiced star, because the box actually IS holding that text.
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

from sm64_events.memory.addresses import STAR_NAMES  # noqa: E402
from uilab.driver import get_driver                  # noqa: E402
from uilab_project import PROJECT                    # noqa: E402


def star_names() -> list[str]:
    names: list[str] = []
    for value in STAR_NAMES.values():
        names += list(value.values()) if isinstance(value, dict) else list(value)
    return sorted({name for name in names if isinstance(name, str)})


# Real-render probe: sets each candidate name into the REAL `.log-card-name b`
# (any card -- the column geometry does not depend on which entity it names),
# forces a layout with `offsetWidth`, and reads scrollWidth/clientWidth off
# THAT element. Restores the original text before returning, so this cannot
# leave the page in a state a later assertion in the same test would read.
OVERFLOW_SWEEP = """
((names) => {
  const b = document.querySelector(".log-card-name b");
  if (!b) return null;
  const original = b.textContent;
  let overflowCount = 0;
  let worst = {name: "", over: 0};
  for (const name of names) {
    b.textContent = name;
    void b.offsetWidth;
    const over = b.scrollWidth - b.clientWidth;
    if (over > 1.5) {           // uilab's own EPS -- sub-pixel layout noise
      overflowCount++;
      if (over > worst.over) worst = {name, over};
    }
  }
  b.textContent = original;
  void b.offsetWidth;
  return JSON.stringify({overflowCount, total: names.length, worst});
})(%s)
"""

# Same width list as test_objective_name_fits.py, for the same reason: 913/912
# straddle the objective card's own tight-band container threshold, and the
# supported floor (850) plus one pixel above it (851) are the tightest cases.
# This card's own threshold (900px container / 1019-1020px window) sits
# inside this range too, so nothing here needs a wider sweep to reach it.
WIDTHS = (1920, 1500, 1200, 1000, 913, 912, 900, 851, 850)


@pytest.fixture(scope="module")
def measured():
    names = star_names()
    assert len(names) > 50, f"only {len(names)} star names — corpus not loaded"
    out = {}
    with PROJECT.open() as url, get_driver().launch() as page:
        page.goto(url)
        page.wait_for(PROJECT.ready_selector)
        for width in WIDTHS:
            page.set_viewport(width, 1000)
            page.wait_ms(320)
            raw = page.evaluate(OVERFLOW_SWEEP % json.dumps(names))
            assert raw, f"no `.log-card-name b` on the page at {width}px"
            out[width] = json.loads(raw)
    return out


@pytest.mark.parametrize("width", WIDTHS)
def test_no_star_name_overflows_the_log_card_name(measured, width):
    result = measured[width]
    assert result["overflowCount"] == 0, (
        f"at {width}px, {result['overflowCount']} of {result['total']} corpus "
        f"star names overflow `.log-card-name b` (worst: "
        f"{result['worst']['name']!r}, {result['worst']['over']}px over) -- "
        "either the narrow-pane reflow (`@container (max-width: 900px)` in "
        "index.html) regressed, or its threshold no longer covers this width")


def test_the_supported_floor_is_the_tightest_case_measured(measured):
    """850px is where this broke first (41 of 97 names, final review item 1),
    so it must be in the list -- and the list must not quietly start above
    the floor the app now enforces."""
    assert min(WIDTHS) == PROJECT.min_viewport_width


def test_the_guard_can_fail(measured):
    """Every assertion above is `overflowCount == 0`, which a probe that never
    actually measured anything would also satisfy. Confirms the mechanism
    itself can detect an overflow: a string built wider than any real column
    at any tested width must be reported as overflowing everywhere."""
    absurdly_wide_name = "X" * 400
    with PROJECT.open() as url, get_driver().launch() as page:
        page.goto(url)
        page.wait_for(PROJECT.ready_selector)
        page.set_viewport(max(WIDTHS), 1000)
        page.wait_ms(320)
        raw = page.evaluate(OVERFLOW_SWEEP % json.dumps([absurdly_wide_name]))
    result = json.loads(raw)
    assert result["overflowCount"] == 1, (
        f"a deliberately absurd {len(absurdly_wide_name)}-character name did "
        "not register as an overflow at the widest tested width -- the probe "
        "is not actually measuring anything")
