"""The Rank tab draws whole, or not at all (round 13, 2026-08-31).

His report: "the scorecard briefly appears for a couple seconds when I
access the rank page at the top... Once the rest of the page loads, then it
glitches and is moved to its actual position at the bottom of the screen.
If we are still waiting for elements to load, we shouldn't accidentally
render elements and then reorder them, as that reads like a glitch."

That is *layout shift*: each section drew as its own fetch landed, so the
scorecard -- last in the DOM but first to arrive -- occupied the top of a
page of placeholders and was displaced when the rest came in. RankPage now
holds its body behind the shared loading spinner until everything its
layout depends on is in hand.

Driven deterministically by DELAYING one of the page's fetches in the
browser, so "still loading" is a state the test can stand in rather than a
race it hopes to catch.
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

_OPEN_RANK_TAB = (
    "document.querySelector('button.nav-item[title=\"Rank\"]').click()")

# Hold ONE of the page's own fetches for 2.5s, before the tab is opened.
_DELAY_HISTORY = """
(() => {
  const realFetch = window.fetch;
  window.fetch = (input, init) => {
    const url = typeof input === 'string' ? input : input.url;
    if (url.includes('/api/marelo/history')) {
      return new Promise((resolve) => setTimeout(
        () => resolve(realFetch(input, init)), 2500));
    }
    return realFetch(input, init);
  };
  return true;
})()
"""


def test_the_rank_page_waits_behind_a_spinner_instead_of_reordering():
    with serve_ui() as base:
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_DELAY_HISTORY)
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_ms(900)          # well inside the held fetch

            # `.page-state` is counted app-wide on purpose: the spinner
            # REPLACES `.rank-page`, so there is no rank-scoped container to
            # look inside while it is showing. Other tabs stay mounted and
            # can carry their own loading state, hence >= rather than ==.
            during = page.evaluate(
                "({ spinner: document.querySelectorAll('.page-state.is-loading').length,"
                "   rankPages: document.querySelectorAll('.rank-page').length,"
                "   scorecards: document.querySelectorAll('.scorecard-card').length,"
                "   chips: document.querySelectorAll('.scope-chip').length,"
                "   rankCards: document.querySelectorAll('.rank-card-main').length })")

            page.wait_for(".rank-page .scorecard-card .score-card", timeout_ms=15000)
            page.wait_ms(300)
            after = page.evaluate(
                "(() => {"
                "  const card = document.querySelector('.rank-page .scorecard-card');"
                "  const rank = document.querySelector('.rank-page .rank-card');"
                "  return { spinner: document.querySelectorAll('.page-state.is-loading').length,"
                "           chips: document.querySelectorAll('.scope-chip').length,"
                "           scorecardTop: card.getBoundingClientRect().top,"
                "           rankCardBottom: rank.getBoundingClientRect().bottom };"
                "})()")

    assert during["spinner"] >= 1, (
        f"no loading spinner while the page was waiting: {during}")
    assert during["rankPages"] == 0, (
        f"the Rank page drew while its own data was still in flight: {during}")
    assert during["scorecards"] == 0, (
        "the scorecard drew before the page could be laid out in its final "
        f"order — the layout-shift glitch: {during}")
    assert during["rankCards"] == 0, during
    assert after["chips"] >= 1, (
        "the scope chips must arrive WITH the page, not after it")
    assert after["scorecardTop"] > after["rankCardBottom"], (
        "the scorecard belongs below the rank card once the page is drawn: "
        f"{after}")
