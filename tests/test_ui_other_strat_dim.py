# tests/test_ui_other_strat_dim.py
"""A practice-log row on a strategy other than the selected one is DIMMED,
and the dim ANIMATES in both directions when the header's strategy changes.

His call, 2026-08-23: "it should slightly dim the row, so that it's very
clear that it's for a different strategy. When we swap between strategies,
then the dimming should naturally animate to the correct states."

Two things no end-state render can prove, so both are sampled MID-transition
through the real endpoint: that the opacity passes through intermediate
values on the way down AND on the way back up. A snap in either direction
looks identical to a fade at both ends (his 2026-07-26 ruling on a ladder
band that expanded instantly and collapsed smoothly under one stylesheet),
and the way a fade turns into a snap in one direction is a second
`transition` declaration on the state class -- which is exactly what this
catches. The dim LEVEL and the fade DURATION are logtuning.js rows, so
nothing here pins either number; it pins that the level is below 1 and that
the path between the two levels has a middle.
"""
import json
import sys
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
from ui_fixture import (FIXTURE_COURSE, FIXTURE_FOREIGN_STRAT, FIXTURE_STAR,  # noqa: E402
                        FIXTURE_STRAT, serve_ui)
from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab.driver import get_driver  # noqa: E402

# The opacity of the first row tagged with a given strategy, read off the
# computed style of the <tr> itself (the element that carries the class).
OPACITY_OF = """
  ((strat) => {
    const card = Array.from(document.querySelectorAll('.log-card'))
      .find((c) => c.querySelector('.attempt-actions'));
    const tr = Array.from(card.querySelectorAll('tr')).find((tr) => {
      const sel = tr.querySelector('.attempt-strategy select');
      return sel && sel.value === strat; });
    return tr ? Number(getComputedStyle(tr).opacity) : null;
  })
"""


def _post(base, path, payload):
    request = urllib.request.Request(
        f"{base}{path}", data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(request, timeout=10).read())


def _set_active(base, strat):
    _post(base, "/api/strat", {"course_id": FIXTURE_COURSE, "star_id": FIXTURE_STAR,
                               "strat_tag": strat})


def _sample(page, strat, count=16, every_ms=40):
    out = []
    for _ in range(count):
        out.append(page.evaluate(f"{OPACITY_OF}({strat!r})"))
        page.wait_ms(every_ms)
    return out


@pytest.fixture(scope="module")
def samples():
    """Start on the fixture's own strategy, switch to the foreign one and
    sample the fixture-strategy row on the way DOWN, switch back and sample
    it on the way UP."""
    out = {}
    with serve_ui() as base:
        with get_driver().launch(headless=True, viewport=(1500, 1200)) as page:
            page.goto(base)
            page.wait_for(".log-card .attempt-actions")
            page.wait_ms(400)
            out["at_rest"] = {
                "own": page.evaluate(f"{OPACITY_OF}({FIXTURE_STRAT!r})"),
                "other": page.evaluate(f"{OPACITY_OF}({FIXTURE_FOREIGN_STRAT!r})"),
            }
            _set_active(base, FIXTURE_FOREIGN_STRAT)
            out["down"] = _sample(page, FIXTURE_STRAT)
            _set_active(base, FIXTURE_STRAT)
            out["up"] = _sample(page, FIXTURE_STRAT)
    return out


def test_a_row_on_another_strategy_is_dimmed_and_the_active_ones_are_not(samples):
    rest = samples["at_rest"]
    assert rest["own"] is not None and rest["other"] is not None, rest
    assert rest["own"] == 1, rest
    assert 0 < rest["other"] < 1, rest


def _has_a_middle(trace, start, end):
    lo, hi = sorted((start, end))
    return any(lo + 0.02 < v < hi - 0.02 for v in trace if v is not None)


def test_the_dim_fades_in_both_directions(samples):
    down, up = samples["down"], samples["up"]
    assert down[-1] < 1 and up[-1] == 1, (down, up)
    assert _has_a_middle(down, 1, down[-1]), (
        "the row SNAPPED to dim -- no intermediate opacity was ever painted", down)
    assert _has_a_middle(up, down[-1], 1), (
        "the row SNAPPED back to full -- the transition is lost in one "
        "direction, which is what a second `transition` declaration on the "
        "state class does", up)
