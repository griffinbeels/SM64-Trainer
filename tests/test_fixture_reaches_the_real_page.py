"""The layout gate's fixture must render the page a human actually looks at.

This is the canary for the failure that has cost more than any other in this
project. Every layout gate here is only as good as the state `ui_fixture.py`
reaches, and when that state is wrong the gate does not go red — it reports a
clean page nobody is looking at. Three times, each hidden by the one before:

  2026-07-28  no stage       -> the Active Target card rendered "Nothing to
                                practice here". 26 real defects invisible, and
                                a whole feature (per-card collapse) served
                                correctly and rendered zero times, no error.
  2026-07-28  no strat/PB    -> the card rendered, the two RANK BANNERS did
                                not. The banners are the crowded part.
  2026-07-29  a one-strategy star -> the strategy ladder was also the star's
                                best ladder, so the card drew ONE combined
                                banner instead of two, and the entire class of
                                "the two banners crowd each other" defects was
                                unreachable. The user reported that overlap
                                three times over two days while every sweep
                                stayed green.

Each was found by a human looking at a screenshot, never by a test. So each
becomes an assertion here, and every future one should be added the same day.

None of these check LAYOUT — that is the sweep's job. They check that the thing
whose layout is being swept is on the page at all.
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
from uilab_project import PROJECT    # noqa: E402

PRIMARY = ".practice-detail-grid.is-primary "


@pytest.fixture(scope="module")
def page():
    with PROJECT.open() as url, get_driver().launch() as opened:
        opened.goto(url)
        opened.wait_for(PROJECT.ready_selector)
        opened.set_viewport(1500, 1000)
        opened.wait_ms(500)
        yield opened


def count(page, selector) -> int:
    # The driver's own verb, not a hand-rolled `evaluate`. A bare expression
    # passed to evaluate() is wrapped as a function BODY and returns None, and
    # `None == 1` fails in a way that looks like a missing element rather than a
    # broken probe — the same shape of instrument fault this whole file exists
    # to catch, one layer down.
    return page.count(selector)


def test_the_active_target_card_is_populated_not_the_empty_state(page):
    """`.objective-empty` is what renders with no stage — "Nothing to practice
    here". A sweep of that card measures an empty box and calls the page
    clean."""
    assert count(page, PRIMARY + ".objective-card") == 1
    assert count(page, PRIMARY + ".objective-empty") == 0, (
        "the fixture is standing nowhere — the Active Target card is its EMPTY "
        "variant, and every layout number taken from it is about a card the "
        "user never sees. serve_ui() must publish a stage_changed first.")


def test_both_rank_banners_render(page):
    """The crowded row, and the one three separate bugs lived in. TWO, not one:
    a star with a single strategy collapses them into one combined banner and
    the whole two-banner layout stops existing."""
    banners = count(page, PRIMARY + ".rank-banner")
    assert banners == 2, (
        f"{banners} rank banner(s), expected 2. If this is 1, the seeded star "
        "has one strategy, so its ladder IS the star's best ladder and the two "
        "measures merged (views.py::ranks_share_ladder) — pick a star with "
        "several strategies, see ui_fixture.py::FIXTURE_STAR. If 0, there is "
        "no strategy or no PB and the card says 'pick a strat to see your "
        "rank'.")


def test_neither_banner_is_the_sentinel_variant(page):
    """`.rank-banner-empty` has no medal, no progress bar and no wash — it is a
    different, shorter box, and swept in place of the graded one it under-
    reports every height on the card."""
    assert count(page, PRIMARY + ".rank-banner-empty") == 0


def test_the_pb_tag_and_strategy_picker_are_present(page):
    """Both are columns of the metrics grid. Missing either changes the whole
    row's geometry, which is what the rank-wash bugs were measured against."""
    assert count(page, PRIMARY + ".pbtag") == 1
    assert count(page, PRIMARY + ".objective-strategy select") == 1


def test_the_star_row_has_stars_in_it(page):
    """Most of the currently-owed defects live here, and none of them existed
    on any sweep before a course was loaded."""
    assert count(page, ".starcell") >= 2


def test_the_collapse_toggles_exist(page):
    """The collapsed page is a declared story. With no toggles that story
    silently degrades into a second copy of the expanded one — a whole layout
    the user asked to be held to, measured zero times."""
    assert count(page, ".card-collapse") >= 3


def test_the_practice_log_and_analysis_cards_are_on_the_page(page):
    """The user's stated hierarchy for this page: selector -> target ->
    practice log -> analysis. A fixture missing the bottom half measures the
    top half and reports the page clean."""
    assert count(page, ".attempts-card") >= 1
    assert count(page, ".analysis-card") >= 1
