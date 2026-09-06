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

# The row tagged with a given strategy -- re-found every frame, because the
# switch re-renders the card and a handle taken once would go stale.
FIND_ROW = """
  ((strat) => {
    const card = Array.from(document.querySelectorAll('.log-card'))
      .find((c) => c.querySelector('.attempt-actions'));
    if (!card) return null;
    return Array.from(card.querySelectorAll('tr')).find((tr) => {
      const sel = tr.querySelector('.attempt-strategy select');
      return sel && sel.value === strat; }) || null;
  })
"""

# The opacity of that row, read off its computed style.
OPACITY_OF = f"""
  ((strat) => {{
    const tr = {FIND_ROW}(strat);
    return tr ? Number(getComputedStyle(tr).opacity) : null;
  }})
"""

# Sampling happens INSIDE the page, one reading per animation frame into an
# array the test collects afterwards in a single call.
#
# It used to poll from the driver -- one round trip per sample, 40 ms apart.
# That reads the same on an idle machine and is worthless on a busy one: a
# round trip can outlast the whole fade, so the trace goes 1.0 -> 0.5 with no
# middle and the test reports a SNAP that never happened. It failed exactly
# that way under the 16-worker gate (2026-09-02) and no rerun could save it,
# because the trace lives in a module-scoped fixture and every retry re-read
# the same bad measurement. requestAnimationFrame samples on the same clock
# the transition is painted on, so the reading no longer depends on how busy
# the machine outside the browser is.
RECORD_OPACITY = f"""
  ((strat, other, expectedOpacity) => {{
    const trace = [];
    const started = performance.now();
    window.__dimTraceDone = new Promise((resolve) => {{
      const step = () => {{
        const tr = {FIND_ROW}(strat);
        const opacity = tr ? Number(getComputedStyle(tr).opacity) : null;
        trace.push(opacity);
        const settled = tr && tr.classList.contains('other-strat') === other
          && opacity === expectedOpacity;
        if (settled || performance.now() - started >= 10000) {{
          resolve({{trace, settled: !!settled}});
        }} else requestAnimationFrame(step);
      }};
      requestAnimationFrame(step);
    }});
    return true;
  }})
"""


def _post(base, path, payload):
    request = urllib.request.Request(
        f"{base}{path}", data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(request, timeout=10).read())


def _set_active(base, strat):
    _post(base, "/api/strat", {"course_id": FIXTURE_COURSE, "star_id": FIXTURE_STAR,
                               "strat_tag": strat})


def _trace_through(page, base, watched, switch_to, expected_opacity):
    """Arm the in-page recorder, make the switch, then collect the frames it
    caught. The recorder is armed BEFORE the switch because the first frames
    of the fade are the ones that prove it is a fade. Finish only when the
    new row state reaches its endpoint: a fixed window starting before the
    POST can end before the socket/refetch has even started the transition."""
    other = json.dumps(watched != switch_to)
    page.evaluate(f"{RECORD_OPACITY}({watched!r}, {other}, {expected_opacity})")
    _set_active(base, switch_to)
    result = page.evaluate("window.__dimTraceDone")
    assert result["settled"], ("the switched row never reached its opacity endpoint", result)
    trace = result["trace"]
    assert trace, "the in-page recorder caught no frames at all"
    return trace


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
            # The initial sample is a settled reference, not an arbitrary
            # point in a mount transition. Keep the level owned by tuning.
            ready = page.evaluate(f"""
              new Promise((resolve) => {{
                const started = performance.now();
                const step = () => {{
                  const own = {FIND_ROW}({FIXTURE_STRAT!r});
                  const other = {FIND_ROW}({FIXTURE_FOREIGN_STRAT!r});
                  const rows = [own, other];
                  const settled = rows.every((tr) => tr && !tr.getAnimations()
                    .some((a) => a.transitionProperty === 'opacity'));
                  if (settled || performance.now() - started >= 10000)
                    resolve(settled);
                  else requestAnimationFrame(step);
                }};
                requestAnimationFrame(step);
              }})
            """)
            assert ready, "the initial rows did not finish their opacity transitions"
            out["at_rest"] = {
                "own": page.evaluate(f"{OPACITY_OF}({FIXTURE_STRAT!r})"),
                "other": page.evaluate(f"{OPACITY_OF}({FIXTURE_FOREIGN_STRAT!r})"),
            }
            out["down"] = _trace_through(page, base, FIXTURE_STRAT,
                                         FIXTURE_FOREIGN_STRAT, out["at_rest"]["other"])
            out["up"] = _trace_through(page, base, FIXTURE_STRAT, FIXTURE_STRAT,
                                       out["at_rest"]["own"])
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
