"""The sidebar stays put while the page scrolls (round 12, 2026-08-29).

His report: "the sidebar totally disappears when I scroll all the way down
to the bottom. The sidebar should always stay there when we're scrolling
the main content of the screen, it should be sticky."

The sidebar HAD `position: sticky` all along — what defeated it was
`overflow-x: hidden` on `#app`, which turns #app into a scroll container,
and sticky sticks to its nearest SCROLLING ancestor; #app never scrolls
(the viewport does), so the sticky silently never fired. `overflow-x: clip`
clips the same pixels without creating a scroll container. This test pins
the OUTCOME (the sidebar's top stays at the viewport after a hard scroll),
not the declaration, so any future ancestor that recreates the trap goes
red — mutation-proved by restoring `hidden` on #app.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from ui_fixture import serve_ui  # noqa: E402
from uilab.driver import get_driver  # noqa: E402

_OPEN_RANK_TAB = ("document.querySelector('button.nav-item[title=\"Rank\"]')"
                  ".click()")


def test_the_sidebar_stays_at_the_top_after_scrolling_to_the_bottom():
    with serve_ui() as base:
        with get_driver().launch(headless=True, viewport=(1200, 600)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-card .score-card")
            page.wait_ms(200)

            state = page.evaluate(
                "(() => {"
                "  window.scrollTo(0, document.body.scrollHeight);"
                "  return null;"
                "})()")
            del state
            page.wait_ms(200)
            measured = page.evaluate(
                "(() => {"
                "  const sidebar = document.querySelector('.app-sidebar');"
                "  return { scrollY: window.scrollY,"
                "           top: sidebar.getBoundingClientRect().top };"
                "})()")

        assert measured["scrollY"] > 200, (
            f"the page never actually scrolled ({measured}) — the fixture "
            "page is too short for this test to mean anything")
        assert -1 <= measured["top"] <= 1, (
            f"the sidebar scrolled away: top={measured['top']}px after a "
            f"{measured['scrollY']}px scroll")
