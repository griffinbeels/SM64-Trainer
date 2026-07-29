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

from uilab.driver import get_driver          # noqa: E402
from uilab_project import PROJECT, STORIES   # noqa: E402

PRIMARY = ".practice-detail-grid.is-primary "

# By NAME, not index (tests/test_ui_collapse_story.py's own rule: a reordered
# list shifted these two apart once and the probe started asserting the wrong
# row). The four Segments-tab stories are the mechanism this file's own lesson
# demands for a FOURTH instance of it: this branch's authoring surfaces
# (recorder modal, segments editor, lint/backtest/split/merge) had never been
# rendered by any gate. Reused here rather than restated, so there is exactly
# one script that reaches each state -- the sweep and these assertions agree
# on it by construction, not by two authors keeping two copies in step.
_BY_NAME = {story.name: story for story in STORIES}


# Two viewports, not one: 1500x1000 (comfortably wide, side-by-side rank
# banners) and 850x1180 (the supported floor, min_viewport_width -- stacked
# banners, the narrow objective-card band). Reach had only ever been proven
# at the first. Every test in this module runs once per viewport via this
# one parametrized fixture, so a surface that only reaches its populated
# state at one width (a real, previously-hit failure class -- the star
# fixture's own history above) cannot pass here by accident of which size
# happened to be checked.
@pytest.fixture(scope="module", params=[(1500, 1000), (850, 1180)],
                ids=["1500x1000", "850x1180"])
def page(request):
    width, height = request.param
    with PROJECT.open() as url, get_driver().launch() as opened:
        opened.goto(url)
        opened.wait_for(PROJECT.ready_selector)
        opened.set_viewport(width, height)
        opened.wait_ms(500)
        yield opened


def reach(page, story_name: str):
    """Run one named Story's own setup on the shared `page` -- ONE browser
    for the whole file (tests/test_ui_collapse_story.py's own pattern), not
    a fresh server+browser per surface. Two of those in the same process hit
    a real, reproducible `asyncio.run() cannot be called from a running
    event loop` (Playwright's sync API leaves this thread's loop state behind
    its own `with get_driver().launch():` block, measured 2026-07-29) --
    each Story's setup is already idempotent and order-independent by
    design (uilab's own Story contract), so reusing the one `page` the file
    already opens is not a workaround, it is the correct shape."""
    page.evaluate(_BY_NAME[story_name].setup)
    page.wait_ms(200)


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


def test_the_armed_segment_card_draws_two_rank_banners(page):
    """This branch's OWN instance of the exact bug the star's test above
    exists to catch. LBLJ (segment id 1) -- the segment `_arm_segment` used
    until 2026-07-29 -- has exactly one bundled strategy, so its ladder IS
    its best ladder (views.py::ranks_share_ladder) and the armed-segment
    card drew ONE combined banner. The two-banner-plus-`.seg-waiting`
    layout, and the CSS fix scoped to the non-last banner (index.html), had
    never been rendered by any instrument until the armed segment moved to
    ui_fixture.FIXTURE_SEGMENT (four bundled strategies)."""
    card = ".objective-card:has(.seg-waiting)"
    banners = count(page, f"{card} .rank-banner")
    assert banners == 2, (
        f"{banners} rank banner(s) on the armed-segment card, expected 2. "
        "If this is 1, the armed segment has one strategy active and its "
        "ladder merged with its best -- pick a segment with several, see "
        "ui_fixture.py::FIXTURE_SEGMENT. If 0, there is no strategy or no "
        "PB on it yet.")


def test_the_armed_segment_card_has_the_seg_waiting_row(page):
    """`.seg-waiting` is this branch's own new grid row (Task 6) -- the whole
    reason `--objective-card-narrow` needed re-measuring and the reason the
    `.rank-banner::before` bleed fix above exists at all. Absent from every
    star card by construction (rule 11: a star has no waypoint sequence or
    staleness deadline to describe)."""
    assert count(page, ".seg-waiting") == 1


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


# --- this branch's own surfaces (spec 2026-07-28-multi-step-segments) ------
# Added 2026-07-29: a FOURTH instance of this file's own lesson. Every story
# above is Practice-page state inherited from main; nothing here had ever put
# the gate on the Segments tab at all, so the recorder modal (the feature this
# whole branch exists for), the segments editor, and the lint/backtest/split/
# merge panels had been rendered by this gate exactly zero times.

def test_the_segment_editor_is_open_with_a_real_definition(page):
    """`.segbuilder` is the Builder's own root -- if this is 0, the setup
    navigated to the Segments tab and stopped, and everything below measures
    the empty "Choose a segment to edit" state instead."""
    reach(page, "segments-editor")
    assert count(page, ".segbuilder") == 1
    assert count(page, ".workshop-empty") == 0


def test_the_matching_control_is_on_the_open_definition(page):
    reach(page, "segments-editor")
    assert count(page, ".builder-matchmode select") == 1


def test_the_lint_panel_has_a_real_finding(page):
    """A definition with NO lint finding renders no `.lint-panel` at all
    (`${lintFindings.length > 0 && html...}` in segments.js) -- so opening a
    quiet definition would sweep a panel that is never actually there. The
    fixture's two "Editor Fixture" segments are byte-identical on purpose,
    to guarantee a real `duplicate` warning every time."""
    reach(page, "segments-editor")
    assert count(page, ".lint-panel .lint-finding") >= 1


def test_the_backtest_panel_rendered(page):
    """Only exists after clicking "Try it against my history" and getting a
    response back -- the segments-editor Story's own setup does that click
    and waits for it, rather than leaving this panel permanently unmeasured."""
    reach(page, "segments-editor")
    assert count(page, ".backtest-panel") >= 1


def test_the_split_panel_is_offered(page):
    """Only offered for a saved segment with EXACTLY one waypoint -- neither
    LBLJ nor any of the other nine legacy tricks carries one, so this needed
    its own purpose-built fixture segment rather than reusing LBLJ."""
    reach(page, "segments-editor")
    assert count(page, ".builder-split") == 1


def test_the_merge_panel_is_offered(page):
    reach(page, "segments-editor")
    assert count(page, ".builder-merge") == 1


def test_the_recorder_start_step_has_pickable_rows(page):
    """`.record-rows` renders nothing (a plain-text empty state) if the
    fixture's journal has no timeline rows -- both `_arm_segment`'s
    level_changed events and `seed_practice`'s star_collected events count,
    so this should never be empty in the default fixture."""
    reach(page, "recorder-start")
    assert count(page, ".record-steps") == 1
    assert count(page, ".record-row") >= 1


def test_the_recorder_end_step_still_has_later_rows_to_pick(page):
    """The `later` list is `rows.filter(row => row.id > startRow.id)` --
    picking the MOST RECENT event as "start" would leave this empty, which
    is exactly the bug this fixture's setup script had until the rows it
    clicked were changed from last-in-list to first-in-list (measured, not
    assumed: `later rows: 0` was the actual failure)."""
    reach(page, "recorder-end")
    assert count(page, ".record-picked") == 1
    assert count(page, ".record-row") >= 1


def test_the_recorder_reaches_the_dense_review_step(page):
    """The review step is the one the team lead named as dense: two
    `.record-picked` summaries, the synthesized start/end sentences, the
    backtest summary and (when findings exist) the lint panel, all at once.
    `.record-review` is the whole step's own root."""
    reach(page, "recorder-review")
    assert count(page, ".record-review") == 1
    assert count(page, ".record-picked") == 2


def test_the_recorder_review_step_has_run_its_backtest(page):
    """`synth`/`btReport` are both fetched asynchronously on entering this
    step -- if the Story's setup did not wait for them, this measures
    "Working it out…"/"Testing against your history…" placeholders instead
    of the real content whose layout the sweep is supposed to be checking."""
    reach(page, "recorder-review")
    assert count(page, ".record-review:has-text('Working it out')") == 0
    assert count(page, ".record-review:has-text('Testing against your history')") == 0


def test_the_page_story_returns_to_practice_after_the_segments_tab(page):
    """The sweep's own self-healing guard (uilab_project.py's `_EXPAND_ALL`):
    without it, whichever Segments-tab story ran last in a viewport's pass
    would leave the NEXT viewport's "page" story measuring the Segments tab
    under the Practice page's name -- silently, since the sweep never
    reloads between viewports or stories."""
    reach(page, "recorder-review")
    reach(page, "page")
    assert count(page, PRIMARY + ".objective-card") == 1
    # >=1, not ==1: the mobile bottom-bar nav ALSO renders a "Practice" item
    # (hidden by CSS at this viewport, still present in the DOM), so a wide
    # viewport genuinely has two.
    assert page.count('button.nav-item[title="Practice"][aria-current="page"]') >= 1
