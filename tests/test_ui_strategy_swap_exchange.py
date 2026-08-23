# tests/test_ui_strategy_swap_exchange.py
"""Swapping the card's active STRATEGY is an exchange, never a rank-up.

His ruling, 2026-08-23: "When we swap between strategies, it shouldn't
animate the full ranked bar filling up. It should use the MARELO type
transition animation, where the cap squishes down, and pops back up with the
new rank. The bar position should simply lerp to the new position rather
than restarting. The reason is because it incorrectly right now gives a
false sense of progression, whereas it should feel more like a transition."

Until then a strategy change REPLAYED the first-pick climb from Capless 5
(the 2026-07-27 "repeat the animation process" rule, a `replayKey` in
rankclimb.js). Driven through the real `/api/strat` endpoint on the fixture
card, which holds a ranked strategy (a saved PB) and a second one at the
floor, so the swap has somewhere to go in BOTH directions:

* the banner enters `.is-swapping` and never `.is-climbing`;
* the bar passes through intermediate widths on the way DOWN (ranked ->
  floor), which a climb would have snapped -- "never a regression" -- and
  lands within the swap's own clock on the way UP rather than a multi-second
  ladder walk.

The FIRST pick (no strategy -> a strategy) is deliberately not covered here:
that half of the 2026-07-27 ruling stands and still climbs from the floor.
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

BANNER = ".log-card.log-card-active .rank-banner"

READ = f"""
  (() => {{
    const banner = document.querySelector('{BANNER}');
    if (!banner) return null;
    const bar = banner.querySelector('.rank-progress-track i');
    return {{
      swapping: banner.classList.contains('is-swapping'),
      climbing: banner.classList.contains('is-climbing'),
      width: bar ? parseFloat(bar.style.width) : null,
      name: (banner.querySelector('.rank-banner-name') || {{}}).textContent,
    }};
  }})()
"""


def _set_active(base, strat):
    request = urllib.request.Request(
        f"{base}/api/strat",
        data=json.dumps({"course_id": FIXTURE_COURSE, "star_id": FIXTURE_STAR,
                         "strat_tag": strat}).encode(),
        method="POST", headers={"Content-Type": "application/json"})
    urllib.request.urlopen(request, timeout=10).read()


def _trace(page, count=40, every_ms=25):
    out = []
    for _ in range(count):
        out.append(page.evaluate(READ))
        page.wait_ms(every_ms)
    return out


@pytest.fixture(scope="module")
def traces():
    out = {}
    with serve_ui() as base:
        with get_driver().launch(headless=True, viewport=(1500, 1200)) as page:
            page.goto(base)
            page.wait_for(BANNER)
            page.wait_ms(1500)            # let any arrival climb settle
            out["ranked"] = page.evaluate(READ)
            _set_active(base, FIXTURE_FOREIGN_STRAT)
            out["down"] = _trace(page)
            page.wait_ms(800)
            out["floor"] = page.evaluate(READ)
            _set_active(base, FIXTURE_STRAT)
            out["up"] = _trace(page)
    return out


def test_the_fixture_has_somewhere_to_swap_to(traces):
    ranked, floor = traces["ranked"], traces["floor"]
    assert ranked and floor, (ranked, floor)
    assert ranked["width"] > floor["width"], (
        "the fixture's two strategies must sit at different bar positions "
        "for a lerp to be visible", ranked, floor)


def test_a_strategy_swap_exchanges_and_never_climbs(traces):
    for direction in ("down", "up"):
        trace = traces[direction]
        assert any(t and t["swapping"] for t in trace), (direction, trace[:8])
        assert not any(t and t["climbing"] for t in trace), (
            f"a strategy swap started the earned-rank climb ({direction})",
            [t for t in trace if t and t["climbing"]][:3])


def test_the_bar_lerps_down_instead_of_snapping(traces):
    start, end = traces["ranked"]["width"], traces["floor"]["width"]
    widths = [t["width"] for t in traces["down"] if t and t["width"] is not None]
    between = [w for w in widths if end + 0.5 < w < start - 0.5]
    assert between, (
        "ranked -> floor painted no intermediate bar width: the bar snapped "
        "(a climb never animates a regression) instead of lerping", widths)


def test_the_bar_lerps_up_within_the_swap_rather_than_refilling(traces):
    end = traces["ranked"]["width"]
    widths = [t["width"] for t in traces["up"] if t and t["width"] is not None]
    assert abs(widths[-1] - end) < 0.5, (
        "one second after the swap back the bar is still not at its target "
        "-- a ladder climb is running instead of a short exchange", widths)
    assert any(0.5 < w < end - 0.5 for w in widths), (
        "floor -> ranked painted no intermediate bar width", widths)
