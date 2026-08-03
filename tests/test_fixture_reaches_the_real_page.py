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
import re
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


def test_exactly_the_grab_timed_row_wears_a_caveat_mark(page):
    """The practice log's own mark, and the fourth instance of this file's
    lesson: it draws only on a row whose x-cam PROVABLY never happened, so a
    fixture of clean rows renders a log the badge can never appear in and
    every sweep over it says nothing (2026-08-02, "if you've been practicing
    all wrong, you should know").

    Counted, not merely found: ONE of the four seeded successes is grab-timed,
    and a badge on all four would be the alarm-fatigue failure the server-side
    predicate is measured to avoid — 3 of his 837 star successes carry proof,
    670 carry only silence.

    2026-08-03 (practice-log-entity-cards, task 6): the attempt table this
    row lives in is no longer inside the PRIMARY objective card -- it is the
    page-level practice log (practicelog.js's `LogCard`), so the scope this
    assertion searches moved with it."""
    marks = count(page, ".log-card .attempt-result .caveat-chip")
    assert marks == 1, (
        f"{marks} caveat marks in the practice log, expected 1. If 0, either "
        "the badge is not drawn (practice.js) or the fixture seeded no "
        "grab-timed attempt (ui_fixture.py::seed_practice). If 4, the server "
        "is marking UNKNOWN rows as well as proven ones "
        "(tracking/caveats.py::attempt_caveat).")


def test_neither_banner_is_the_sentinel_variant(page):
    """`.rank-banner-empty` has no medal, no progress bar and no wash — it is a
    different, shorter box, and swept in place of the graded one it under-
    reports every height on the card."""
    assert count(page, PRIMARY + ".rank-banner-empty") == 0


def test_a_log_card_still_draws_two_rank_banners(page):
    """This branch's OWN instance of the exact bug the star's test above
    exists to catch. LBLJ (segment id 1) -- the segment `_arm_segment` used
    until 2026-07-29 -- has exactly one bundled strategy, so its ladder IS
    its best ladder (views.py::ranks_share_ladder) and its card drew ONE
    combined banner. ui_fixture.FIXTURE_SEGMENT (four bundled strategies)
    fixed that for the ARMED-SEGMENT scenario this file used to measure.

    2026-08-03 (practice-log-entity-cards, task 6): that scenario's own card
    is gone by design, not by regression. `_arm_segment` deliberately does
    NOT touch the active star target ("this composes with whatever star
    target `serve_ui` already seeded" -- its own docstring), so this fixture
    always leaves the STAR active and the segment merely armed alongside it.
    Before this task an armed-but-not-active segment still got a full
    `.objective-card` of its own, inside the practice INDEX (a closed
    `<details>` -- still queryable, just not visible). The index is deleted
    now: "the objective card is the active target, always" (spec section 1),
    and everything else -- armed or not -- is a `LogCard` in the practice log.
    So the two-banner assertion moved to whichever `LogCard` this fixture's
    multi-strategy entities render through -- LogCard is now the ONE
    component either kind renders through (rule 11 made structural), so
    proving it does not collapse a multi-strategy entity's banners on ANY
    card is the same proof the old per-kind check gave, without needing to
    single out this fixture's specific segment."""
    per_card = page.evaluate(
        "return Array.from(document.querySelectorAll('.log-card'))"
        ".map(c => c.querySelectorAll('.rank-banner').length)")
    assert 2 in per_card, (
        f"no `.log-card` drew 2 rank banners (found {per_card}) -- either "
        "no seeded entity has multiple strategies any more (see "
        "ui_fixture.py::FIXTURE_STAR/FIXTURE_SEGMENT) or LogCard collapsed "
        "them (ranks.py::ranks_share_ladder / attemptlog.js::"
        "showsEntityBanner)")


def test_the_armed_segments_log_card_shows_its_own_step_progress(page):
    """Griffin asked for this by name: "i like the idea of knowing for sure
    the system is aware of me grabbing that first star, proven by it
    progressing to the next step." `armed_detail` is NOT segment-only (the
    100-coin star carries it too, which is why StarSection and
    SegmentSection already draw this identically) -- this fixture only
    exercises the segment side (`_arm_segment`, coexisting with the active
    star target rather than replacing it), so this proves the row survived
    for at least one kind, at its new address.

    Before this task an armed-but-not-active segment still got a full
    `.objective-card` -- including this row -- inside the now-deleted
    practice index; a `LogCard` is the only surface such a segment gets now
    (test_a_log_card_still_draws_two_rank_banners, above), so `LogCard` must
    carry the identical `armed_detail` row (practicelog.js) or the
    capability is lost outright, not merely relocated.

    Asserts the rendered TEXT, not just that some markup exists: a
    kind-gated conditional wrapped around the same literal `sec.armed_detail
    && html` still leaves that string findable by a source scan (the
    reviewer's own mutation proof on this branch: wrapping a shared call
    site in a `!seg &&` guard left every count-based check green), so a
    behavioural check on the DRAWN content is what actually proves neither
    kind lost it."""
    log_card_rows = count(page, ".log-card .seg-waiting")
    assert log_card_rows == 1, (
        f"{log_card_rows} `.seg-waiting` row(s) inside a `.log-card`, "
        "expected 1 (the armed segment, ui_fixture.py::FIXTURE_SEGMENT) -- "
        "either the segment stopped carrying armed_detail or LogCard "
        "stopped drawing the row for it")
    step_text = page.evaluate(
        "const el = document.querySelector("
        "'.log-card .seg-waiting .seg-waiting-step');"
        "return el ? el.textContent : null")
    waiting_text = page.evaluate(
        "const el = document.querySelector("
        "'.log-card .seg-waiting .seg-waiting-for');"
        "return el ? el.textContent : null")
    assert step_text and re.search(r"Step\s*\d+\s*of\s*\d+", step_text), (
        f"seg-waiting-step reads {step_text!r} -- not the expected "
        '"Step N of M" shape')
    assert waiting_text and waiting_text.strip().startswith("Waiting for"), (
        f"seg-waiting-for reads {waiting_text!r}")
    # The ACTIVE star in this fixture is an ordinary one (no armed_detail),
    # so its own objective card must show none of this -- a positive count
    # above paired with this zero is what makes both readable as real
    # signal rather than one vacuous side of a tautology.
    assert count(page, ".objective-card .seg-waiting") == 0


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
    top half and reports the page clean.

    2026-08-03 (practice-log-entity-cards, task 6): the practice log is no
    longer `.attempts-card` (that class died with StarSection/SegmentSection's
    own attempts table) -- it is the page-level `.log-list-card`
    (practicelog.js's `PracticeLog`), holding one `.log-card` per entity."""
    assert count(page, ".log-list-card") >= 1
    assert count(page, ".log-card") >= 1
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


def test_the_matching_control_shows_the_definition_s_STORED_mode(page):
    """Presence is not correctness, and this is the link no other test covers.

    Every other guard on this control is a source scan; the one above counts
    the element. The mutation that passes all of them: drop `match_mode` from
    `db.segment_defs()`'s row dict. `d.match_mode` becomes undefined, the
    `!matchModeInfo && d.match_mode` fallback is falsy, and EVERY strict
    definition silently displays "Loose" — the exact class of bug this
    control was added to end. The editor fixture's definitions are seeded
    `"match_mode": "strict"`, so reading the value closes it.
    (Delta review, finding 7.)
    """
    reach(page, "segments-editor")
    # `return`, not a bare expression: evaluate() wraps its argument as a
    # function BODY, so a bare expression yields None — which reads as "the
    # control is missing" rather than "the probe is broken". Exactly the trap
    # `count()` below documents, walked into anyway on the first attempt.
    value = page.evaluate(
        "return document.querySelector('.builder-matchmode select').value")
    assert value == "strict", (
        f"the Matching control reads {value!r} for a definition stored as "
        "'strict'. If this is 'loose', the stored mode is not reaching the "
        "editor — check that db.segment_defs() still carries match_mode and "
        "that segments.js still seeds the select from `initial`.")


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
    # Absence of a placeholder is not presence of content. `segmenttimeline.js`
    # renders "Working it out…" only while `!synth && !synthErr` — a FAILED
    # synthesize clears the placeholder and renders `.badx` instead, so both
    # this test and the dense-review-step one above pass green on an error
    # state, measuring the layout of an error box. (Delta review, finding 6.)
    assert count(page, ".record-review .badx") == 0, (
        "the review step rendered an error, not a synthesized definition — "
        "the placeholder assertions above cannot tell those apart")


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
