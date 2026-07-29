"""Every star name fits the Active Target card, on one line, at every width.

The name is the one thing on that card that says WHAT you are practising, and
it ellipsised mid-word — "Fall onto the Cag…" — at 850 and 900px, because the
name column's floor was 180px and most of the 97 names need more (live report,
2026-07-29).

A gate that only checks the SEEDED star cannot hold this: the fixture practises
"Fall onto the Caged Island" at 234px, so a 240px floor would pass while the
widest name at 293px still broke. So the corpus is measured against the real
column instead — every name, at the card's actual font, in the real layout.

That also makes the constants in index.html un-rottable. They are measured
numbers with the measurement written beside them; this is the thing that fails
when a name, a font, or a column changes underneath them.
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


# Measures against the LIVE h2 — its font, its column, its letter-spacing —
# rather than against a number copied out of the stylesheet. A test that
# restates the CSS is a second copy of the CSS.
WIDEST = """
((names) => {
  const h2 = document.querySelector(
    ".practice-detail-grid.is-primary .objective-name h2");
  if (!h2) return null;
  const style = getComputedStyle(h2);
  const probe = document.createElement("span");
  probe.style.cssText = "position:absolute;visibility:hidden;white-space:nowrap;"
    + `font:${style.font};letter-spacing:${style.letterSpacing}`;
  document.body.appendChild(probe);
  let worst = {name: "", needs: 0};
  for (const name of names) {
    probe.textContent = name;
    const needs = probe.getBoundingClientRect().width;
    if (needs > worst.needs) worst = {name, needs: Math.ceil(needs)};
  }
  probe.remove();
  return JSON.stringify({...worst, gets: h2.clientWidth,
    clamped: style.webkitLineClamp !== "none",
    font: style.fontSize});
})(%s)
"""

# Every width the card keeps its one-line heading at. 913 and 912 are the two
# sides of the tight band's threshold — the place a fix like this is most likely
# to be off by one. They are WINDOW widths for a CONTAINER threshold of 793px:
# the pane runs 119px narrower than the window in this range, so the number in
# the stylesheet and the number here are not the same number and must not be
# assumed to be.
WIDTHS = (1500, 1200, 1000, 913, 912, 900, 851, 850)


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
            page.wait_ms(420)
            raw = page.evaluate(WIDEST % json.dumps(names))
            assert raw, f"no active-target heading at {width}px"
            out[width] = json.loads(raw)
    return out


@pytest.mark.parametrize("width", WIDTHS)
def test_the_widest_star_name_fits_on_one_line(measured, width):
    result = measured[width]
    if result["clamped"]:
        pytest.skip("this width wraps the name to two lines by design")
    assert result["needs"] <= result["gets"], (
        f"at {width}px the widest star name ({result['name']!r}) needs "
        f"{result['needs']}px and the column gives {result['gets']}px, so it "
        f"ellipsises mid-word. Either widen the name column's floor in "
        f"`.objective-heading` or take another step off the font — the space "
        f"comes from the strategy column, never from the name.")


def test_the_supported_floor_is_the_tightest_case_measured(measured):
    """850px is where this breaks first, so it must be in the list — and the
    list must not quietly start above the floor the app now enforces."""
    assert min(WIDTHS) == PROJECT.min_viewport_width


def test_the_guard_can_fail(measured):
    """Every assertion above is `needs <= gets`, which a probe returning zeros
    would also satisfy. This is the one that proves real text was measured."""
    result = measured[max(WIDTHS)]
    assert result["needs"] > 100, f"suspiciously narrow measurement: {result}"
    assert result["name"], "no widest name identified"
