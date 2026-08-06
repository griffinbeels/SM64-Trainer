# tests/test_step_track_on_the_identity_line.py
"""An armed card's step track shares the identity line, not a row below it.

Griffin, 2026-08-05: "visually, when there's a step X of X display, we should
display it right after the identity display to the right (in this case, where
it says Bowser 3: to the right of that). That way, we can maintain the single
line height design with these cards."

Only a render can answer this. The markup change is one element moving inside
another, which every unit test and `node --check` passes straight through, and
the failure it replaced -- an armed card twice the height of every neighbour --
is a pure layout fact with no DOM signature at all.

The wrap is deliberate and is NOT what this measures: a track too long to fit
takes its own line rather than ellipsising a place name, because the chips
carry their own per-name ellipsis and would otherwise clip one silently
(`tests/test_step_track_fits.py` holds that guarantee, measured at four
widths). What this pins is the ordinary case -- a short track, on the line.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from ui_fixture import FIXTURE_SEGMENT, serve_ui  # noqa: E402

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab import driver  # noqa: E402

SETTLE = "new Promise(r => setTimeout(r, 2500))"

MEASURE = """
  (() => {
    const card = Array.from(document.querySelectorAll('.log-card'))
      .find((c) => c.querySelector('.step-row'));
    if (!card) return null;
    const box = (el) => {
      const r = el.getBoundingClientRect();
      return {top: Math.round(r.top), bottom: Math.round(r.bottom),
              left: Math.round(r.left), width: Math.round(r.width)};
    };
    return {
      identity: box(card.querySelector('.log-card-identity')),
      name: box(card.querySelector('.log-card-name')),
      steps: box(card.querySelector('.step-row')),
      insideIdentity: card.querySelector('.log-card-identity .step-row') !== null,
      chips: card.querySelectorAll('.step-row .step-chip').length,
    };
  })()
"""


@pytest.fixture(scope="module")
def armed():
    import tempfile
    with tempfile.TemporaryDirectory() as scratch:
        db = Path(scratch) / "steps.db"
        with serve_ui(db, arm_segment=FIXTURE_SEGMENT) as base:
            with driver.get_driver().launch(headless=True,
                                            viewport=(1500, 1000)) as page:
                page.goto(base)
                page.evaluate(SETTLE)
                return page.evaluate(MEASURE)


def test_the_fixture_actually_renders_an_armed_card(armed):
    """Without this the assertions below are about a card nobody drew --
    the failure mode that has been the root cause on this surface three
    times (`.claude/rules/ui-core.md`)."""
    assert armed is not None, "no card on the page carries a step track"
    assert armed["chips"] >= 1, f"the step track drew no chips: {armed}"


def test_the_step_track_lives_inside_the_identity_row(armed):
    assert armed["insideIdentity"], (
        "the step track is a sibling of the identity row again, which is what "
        "made an armed card two rows tall")


def test_a_short_track_sits_on_the_same_line_as_the_name(armed):
    """Vertical overlap, not equal tops: the chips and the name are different
    heights, so sharing a LINE means their boxes intersect vertically."""
    name, steps = armed["name"], armed["steps"]
    assert steps["top"] < name["bottom"] and name["top"] < steps["bottom"], (
        f"the step track dropped below the name instead of sitting beside it: "
        f"name={name} steps={steps}")


def test_the_track_is_to_the_RIGHT_of_the_name(armed):
    """"right after the identity display to the right" -- his words, and the
    half a vertical-overlap check alone would not catch."""
    assert armed["steps"]["left"] > armed["name"]["left"], (
        f"the step track is not right of the name: {armed}")
