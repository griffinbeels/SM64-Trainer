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
import json
import re
import sys
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab.driver import get_driver          # noqa: E402
from uilab_project import (PROJECT, STORIES,  # noqa: E402
                           BOWSER_COURSE, BOWSER_LEVEL)
from ui_fixture import (serve_ui, FIXTURE_COURSE, FIXTURE_LEVEL,  # noqa: E402
                        _seed_target, _target_segment)

# Re-pointed 2026-08-04 (amendment A8, spec practice-log-entity-cards): the
# Active Target card (`.practice-detail-grid.is-primary`) is deleted -- the
# entity actually being practised is now the `.log-card` carrying
# `.log-card-active` (LogCard's own highlight, ui/components/practicelog.js).
# Every test below that used to scope to the primary objective card now scopes
# to that card instead -- same "which card is the crowded/interesting one"
# question, new address.
PRIMARY = ".log-card.log-card-active "

# By NAME, not index (tests/test_ui_collapse_story.py's own rule: a reordered
# list shifted these two apart once and the probe started asserting the wrong
# row). The four Segments-tab stories are the mechanism this file's own lesson
# demands for a FOURTH instance of it: this branch's authoring surfaces
# (recorder modal, segments editor, lint/backtest/split/merge) had never been
# rendered by any gate. Reused here rather than restated, so there is exactly
# one script that reaches each state -- the sweep and these assertions agree
# on it by construction, not by two authors keeping two copies in step.
_BY_NAME = {story.name: story for story in STORIES}


# --- render gaps closed by Task 7's review, each its own fixture instance --
# Neither of these reuses the shared `page` fixture below: both need server
# state (`reconcile_full_corpus`, a synthetic 100-coin engine) that would
# widen what PROJECT's own sweep measures and re-derive its whole
# `known_defects` table for no reason this task's job needs -- a genuinely
# separate scenario earns its own `serve_ui()` instance instead (same
# reasoning `test_ui_rank_line.py` uses for the tuning inspector).
#
# Placed ABOVE the `page` fixture's own first use, on purpose: a second
# `get_driver().launch()` opened while a PREVIOUS one (here, the module-
# scoped `page` fixture's own `with` block) is still live in the same thread
# hits a real, reproducible `asyncio.run() cannot be called from a running
# event loop` inside `serve_ui`'s own seeding (measured directly -- both
# tests pass in isolation and fail once collected after any test requesting
# `page`). Running before `page`'s first request means its `with` block has
# not opened yet, so there is nothing for either of these to collide with.

def test_the_bowser_reds_pipe_pairing_renders_its_family_naming():
    """`pipe_star_entity`/`pipe_segment_id` (views.py's `_reds_pipe_segments`)
    drive the "(Pipe)"/"(Star)" suffixed naming a Bowser Reds star and its
    paired `seg:reds->pipe:<abbrev>` segment borrow from each other
    (redsfamily.js::familyLabel) -- and until now no fixture ever loaded the
    corpus segment this needs (`_reds_pipe_segments` matches by `seed_key`,
    which only a real reconcile stamps) or armed it, so this naming path had
    only ever been verified by reading source, never by a render.

    `enter_level=17` arms whatever real definitions key off entering BitDW
    (both the legacy `seg:bitdw-pipe` and the corpus `seg:reds->pipe:bitdw`,
    once `reconcile_full_corpus` has loaded it) and leaves them armed.
    `pipe_star_entity`/`pipe_segment_id` (the naming payload) are structural
    -- derived from the segment ROW existing, not from any attempt or arm
    state -- so the Reds STAR's own card carries "(Star)" the instant it has
    a section at all, which `target=(BOWSER_COURSE, 0)` gives it.

    **The PIPE segment's own card is different (2026-08-05): arming alone no
    longer publishes one** (`.claude/rules/hundred-coin.md`, "one CARD, only
    when the entity is the target" -- an unchosen ambiently-arming def must
    not manufacture a card). So its "(Pipe)" naming is proven the same way a
    real player reaches it -- by making it the live target, via the exact
    `POST /api/target` `_target_segment` uses -- rather than by merely
    entering the level and leaving it armed. This is a SECOND phase in the
    same render, not a second fixture: the star's own card (and its "(Star)"
    naming) does not depend on the segment ever having a section, so
    re-targeting onto the segment afterward proves both paths without
    needing them visible simultaneously, which the new rule no longer
    permits for an entity nobody has chosen."""
    with serve_ui(reconcile_full_corpus=True,
                 stage=(BOWSER_COURSE, BOWSER_LEVEL),
                 target=(BOWSER_COURSE, 0),
                 enter_level=BOWSER_LEVEL) as base, \
            get_driver().launch() as opened:
        opened.goto(f"{base}/ui/index.html")
        opened.wait_for(".log-list-card")
        opened.wait_ms(300)

        def card_names():
            return opened.evaluate(
                "return Array.from(document.querySelectorAll('.log-card-name'))"
                ".map(el => el.textContent)")

        names = card_names()
        assert any("(Star)" in name for name in names), (
            f"no log card reads \"…(Star)\" -- names were {names!r}. The "
            "Reds star's own section needs pipe_segment_id set, which "
            "requires the paired seg:reds->pipe:<abbrev> row to exist "
            "(reconcile_full_corpus).")

        segments = json.loads(urllib.request.urlopen(
            f"{base}/api/segments", timeout=10).read())
        pipe = next(s for s in segments
                   if (s.get("seed_key") or "") == "seg:reds->pipe:bitdw")
        _target_segment(base, pipe["id"])
        opened.wait_ms(300)
        names = card_names()
        assert any("(Pipe)" in name for name in names), (
            f"no log card reads \"…(Pipe)\" after targeting the reds->pipe "
            f"segment -- names were {names!r}. Either it never armed (check "
            "ui_fixture.py's enter_level) or views.py's pipe_star_entity "
            "stopped resolving it (tracking-storage.md's _reds_pipe_segments)")


def test_the_star_kind_armed_detail_renders():
    """`armed_detail` (the "Step N of M" progress row `LogCard` shares
    between kinds) is a documented rule-11 ASYMMETRY: every star but the
    100-coin one carries none, because an ordinary star is one atomic grab
    with nothing to be part-way through (test_star_sections_carry_no_arm_
    detail). `_arm_segment` (ui_fixture.py) exercises the SEGMENT half of
    that asymmetry; nothing before this exercised the STAR half, so
    `segments.hundred_coin_entity`'s reattribution path -- and the star-kind
    `.seg-waiting` row -- had only ever been unit-tested against hand-built
    dicts (tests/test_ui_entity_section.py), never rendered.

    `arm_hundred_coin` posts a synthetic def matching the pattern
    `hundred_coin_entity` looks for (a `star_grabbed(star=6, course=…)`
    WAYPOINT) and arms it, coexisting with the ordinary star target
    `serve_ui` seeds by default on the same course.

    **The 100-coin star's own card is TARGETED here too (2026-08-05):
    arming its engine alone no longer publishes one** (`.claude/rules/
    hundred-coin.md`, "one CARD, only when the entity is the target" --
    walking into a 100-coin course with nothing chosen must not manufacture
    an empty card, and this synthetic engine arms the identical ambient
    way). `_seed_target(base, FIXTURE_COURSE, 6, with_pb=False)` is the same
    real `POST /api/target` a player's click sends -- star 6 has no rank
    standards in the bundled seed, so `with_pb=False` skips the strat/PB
    half `_seed_target` otherwise does for a graded star. This retargets
    away from the ordinary star `serve_ui` seeded by default, which keeps
    its own card regardless (it has real seeded ATTEMPTS, not merely a
    target) -- so the positive/negative pairing below is identified by
    NAME now rather than by `.log-card-active`, since that class moved
    with the target."""
    with serve_ui(arm_hundred_coin=(FIXTURE_COURSE, FIXTURE_LEVEL)) as base, \
            get_driver().launch() as opened:
        opened.goto(f"{base}/ui/index.html")
        opened.wait_for(".log-list-card")
        opened.wait_ms(300)
        _seed_target(base, FIXTURE_COURSE, 6, with_pb=False)
        opened.wait_ms(300)
        rows = opened.count(".log-card .seg-waiting")
        assert rows == 1, (
            f"{rows} `.seg-waiting` row(s) inside a `.log-card`, expected 1 "
            "(the synthetic 100-coin engine) -- either it never armed, was "
            "never targeted, or LogCard stopped drawing armed_detail for a "
            "star")
        step_text = opened.evaluate(
            "const el = document.querySelector("
            "'.log-card .seg-waiting .seg-waiting-step');"
            "return el ? el.textContent : null")
        assert step_text and re.search(r"Step\s*\d+\s*of\s*\d+", step_text), (
            f"seg-waiting-step reads {step_text!r} -- not the expected "
            '"Step N of M" shape')
        # The ORDINARY star sharing this course (no longer the active
        # target, but still on the page via its own seeded attempts) must
        # carry none of this -- a positive count above paired with this
        # zero is what makes both readable as real signal (same pairing
        # test_the_armed_segments_log_card_shows_its_own_step_progress uses
        # for the segment side). Identified by NAME, not `.log-card-active`
        # -- the 100-coin star's own card wears that class now.
        scoped = opened.evaluate(
            "const cards = Array.from(document.querySelectorAll('.log-card'));"
            "const hundredCoin = cards.find(c => "
            "  c.querySelector('.log-card-name')?.textContent.includes('100 Coins'));"
            "const other = cards.find(c => c !== hundredCoin "
            "  && c.querySelector('.seg-waiting'));"
            "return {hundredCoinHasIt: !!hundredCoin?.querySelector('.seg-waiting'),"
            "        anotherCardHasIt: !!other};")
        assert scoped["hundredCoinHasIt"] is True, (
            "the .seg-waiting row is not inside the '100 Coins' card")
        assert scoped["anotherCardHasIt"] is False, (
            "a NON-100-coin card also carries .seg-waiting -- armed_detail "
            "leaked onto the wrong star's card")


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
    """The Active Target card and its `.objective-empty` variant are both
    deleted (amendment A8, spec practice-log-entity-cards) -- there is no
    more empty-shell substitute for "the fixture is standing nowhere". What
    survives the same question: does exactly ONE `.log-card` carry the
    `.log-card-active` highlight at all? If the fixture stood nowhere (no
    `stage_changed`/`target` published), no entity would ever resolve as
    `live.activeKey` and no card would ever wear the class -- a sweep of a
    page with nothing highlighted would otherwise call it clean."""
    assert count(page, ".log-card.log-card-active") == 1, (
        "no `.log-card` (or more than one) carries `.log-card-active` -- the "
        "fixture may be standing nowhere, so every layout number taken from "
        "\"the active card\" is about a card that does not exist. "
        "serve_ui() must publish a stage_changed and a target first.")


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
    kind lost it.

    2026-08-04 (main merge): `LogCard`'s own hand-rolled `.seg-waiting-step`
    / `.seg-waiting-for` pair -- a third copy of markup main's merge gave a
    shared component (`steptrack.js::StepTrack`) -- was replaced with the
    same `<StepTrack>` the objective card renders. `.seg-waiting-for` no
    longer exists anywhere: the "Waiting for X" sentence moved onto the
    row's own `title` (a hover, same as the objective card's copy), and the
    route itself is now visible as `.step-track .step-chip`s rather than
    only named in prose."""
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
    waiting_title = page.evaluate(
        "const el = document.querySelector('.log-card .seg-waiting');"
        "return el ? el.getAttribute('title') : null")
    chip_count = count(page, ".log-card .seg-waiting .step-track .step-chip")
    assert step_text and re.search(r"Step\s*\d+\s*of\s*\d+", step_text), (
        f"seg-waiting-step reads {step_text!r} -- not the expected "
        '"Step N of M" shape')
    assert waiting_title and "Waiting for" in waiting_title, (
        f"the log card's step row title reads {waiting_title!r} -- "
        "StepTrack puts \"Waiting for …\" in its own title, not in visible "
        "text any more")
    assert chip_count >= 1, (
        "no `.step-chip`s inside the log card's step track -- StepTrack "
        "renders the whole route as chips, not just a counter")
    # The ACTIVE star in this fixture is an ordinary one (no armed_detail),
    # so its own `.log-card-active` card must show none of this -- a
    # positive count above paired with this zero is what makes both readable
    # as real signal rather than one vacuous side of a tautology.
    assert count(page, ".log-card.log-card-active .seg-waiting") == 0


def test_the_pb_tag_and_strategy_picker_are_present(page):
    """Both are columns of the card head. Missing either changes the whole
    row's geometry, which is what the rank-wash bugs were measured against.
    The strategy picker is `.log-card-strat-picker` now (amendment A2, spec
    practice-log-entity-cards) -- the card head's strategy NAME became the
    same interactive dropdown the deleted Active Target card used, replacing
    `.objective-strategy select`."""
    assert count(page, PRIMARY + ".pbtag") == 1
    assert count(page, PRIMARY + ".log-card-strat-picker select") == 1


def test_the_star_row_has_stars_in_it(page):
    """Most of the currently-owed defects live here, and none of them existed
    on any sweep before a course was loaded."""
    assert count(page, ".starcell") >= 2


def test_the_collapse_toggles_exist(page):
    """The collapsed page is a declared story. With no toggles that story
    silently degrades into a second copy of the expanded one — a whole layout
    the user asked to be held to, measured zero times.

    Was >= 3 (the selector row, the objective card, and the analysis card).
    The Active Target card is deleted (amendment A8, spec practice-log-
    entity-cards) — `LogCard`'s own fold is a SEPARATE mechanism
    (`.log-card-fold`, not `.card-collapse`; see uilab_project.py's own
    comment on the two), so the objective card's `.card-collapse` instance is
    gone rather than relocated. >= 2 (the selector row, the analysis card) is
    the honest count now."""
    assert count(page, ".card-collapse") >= 2


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


def test_the_practice_log_renders_more_than_one_card(page):
    """A one-card log makes every "two cards crowd each other" defect
    unreachable by the gate, which reports clean on a page nobody is
    looking at. Three separate defect classes have hidden this way."""
    assert count(page, ".log-card") > 1


def test_the_practice_log_offers_show_more_past_its_own_page_cap(page):
    """practicelog.js's CARDS_PER_PAGE is 5 -- with too few practiced
    entities in the fixture, `sections.length > shown` is never true and
    the "Show 5 more" control (plus a full-length list past it) had never
    been rendered by any gate (Task 7 review). ui_fixture.py now pads the
    log to 6 real sections specifically so this renders."""
    assert count(page, ".log-list-footer") == 1
    shown_before = int(page.evaluate(
        "return document.querySelectorAll('.log-card').length"))
    page.evaluate(
        "document.querySelector('.log-list-footer button').click()")
    page.wait_ms(200)
    shown_after = int(page.evaluate(
        "return document.querySelectorAll('.log-card').length"))
    assert shown_after > shown_before, (
        "clicking \"Show 5 more\" did not reveal any additional card")
    assert count(page, ".log-list-footer") == 0, (
        "the footer should disappear once every section is shown")


def test_a_manual_pick_moves_the_analysis_drawer_between_kinds(page):
    """The headline gesture of this branch, driven rather than read off
    source. A prior reviewer proved a single shared call site wrapped in a
    `!seg &&` guard silently drops a whole surface for one kind while every
    count-based test in this file stays green — and this branch widened the
    hole it lives in: EntityAnalysis, EntityDrawer, StatChipsRow and
    StandardsPanel are now four page-level components riding ONE `sec`
    conditional apiece instead of two hand-written copies each, so one
    kind-gated guard around any of the four now silences it for BOTH kinds
    at once. None of the element-count assertions elsewhere in this file
    would catch that — they only ever ask "does N of this selector exist",
    never "did the content actually follow the pick".

    Clicks the real gesture. The armed segment's log card is found by its
    `.seg-waiting` row, not by name — `ui_fixture.FIXTURE_SEGMENT` is not the
    active target in this fixture (`Practice()` suppresses its pin while a
    star target is active), so clicking it is a genuine manual browse pick
    AWAY from the active entity, exactly `ui/focustarget.js`'s spring-loaded
    mode. A star card is found by its `.log-card-context` NOT reading the
    literal "Segment" — `displayName`'s own star/segment branch (entitysection.js),
    not a hardcoded fixture name, so this keeps working if the seeded star or
    segment ever changes.
    """
    def subject():
        return page.evaluate(
            "const el = document.querySelector('.analysis-subject');"
            "return el ? el.textContent : null")

    def four_surfaces_render():
        return (count(page, ".analysis-card") >= 1
                and count(page, ".detail-drawer") >= 1
                and count(page, ".detail-drawer .stat-chips") >= 1
                and count(page, ".detail-drawer .stdpanel") >= 1)

    clicked = page.evaluate("""
        (() => {
          const card = document.querySelector('.log-card:has(.seg-waiting)');
          if (!card) return 'no armed-segment log card found';
          const btn = card.querySelector('.log-card-select');
          if (!btn) return 'the armed-segment card has no select button';
          btn.click();
          return 'clicked';
        })()
    """)
    assert clicked == "clicked", clicked
    page.wait_ms(400)
    segment_subject = subject()
    assert segment_subject, (
        "no .analysis-subject text after focusing the segment — the pick "
        "may not have reached ui/focustarget.js's manual snapshot at all")
    assert four_surfaces_render(), (
        "the analysis card, the detail drawer, the stat chips or the "
        "standards panel is missing while a SEGMENT is focused")

    clicked = page.evaluate("""
        (() => {
          const cards = Array.from(
            document.querySelectorAll('.log-card:not(.is-unassigned)'));
          const starCard = cards.find((c) => {
            const ctx = c.querySelector('.log-card-context');
            return ctx && ctx.textContent.trim() !== 'Segment';
          });
          if (!starCard) return 'no star log card found';
          const btn = starCard.querySelector('.log-card-select');
          if (!btn) return 'the star card has no select button';
          btn.click();
          return 'clicked';
        })()
    """)
    assert clicked == "clicked", clicked
    page.wait_ms(400)
    star_subject = subject()
    assert star_subject, (
        "no .analysis-subject text after focusing a star")
    assert star_subject != segment_subject, (
        f"the analysis subject reads {star_subject!r} both before and "
        "after clicking a different kind of card — the click likely never "
        "reached focustarget.js's manual pick")
    assert four_surfaces_render(), (
        "the analysis card, the detail drawer, the stat chips or the "
        "standards panel is missing while a STAR is focused — exactly the "
        "shape a kind-gated `!seg &&` guard around the shared call site "
        "would produce")


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


def test_the_recorder_opens_onto_history_with_pickable_rows(page):
    """The ARRIVAL state, and it is the whole of property 2: the recorder
    opens onto what you just did, never an empty screen waiting for input.
    `.record-rows` renders a plain-text empty state if the fixture's journal
    has no timeline rows -- both `_arm_segment`'s level_changed events and
    `seed_practice`'s star_collected events count, so this should never be
    empty in the default fixture."""
    reach(page, "recorder-open")
    assert count(page, ".record-picks") == 1
    assert count(page, ".record-row") >= 1
    # "what was the timer in game" -- the number he chooses BY, so it is on
    # the row and not in the review. The fixture's star grabs carry
    # `igt_frames`; its level edges do not, so a count EQUAL to the row count
    # would mean we were inventing one.
    times = count(page, ".record-igt")
    assert 0 < times < count(page, ".record-row"), (
        f"{times} of {count(page, '.record-row')} rows show a time")
    # Nothing picked means no review and no Save -- a start with no end can
    # never complete, so the control is absent rather than present-and-refused.
    assert count(page, ".record-review") == 0


def test_the_recorder_review_appears_at_two_picked_moments(page):
    """Two picks is the smallest definition there is, and the state the old
    three-step modal called "review". `.record-review` is its own root."""
    reach(page, "recorder-review")
    assert count(page, ".record-review") == 1
    assert count(page, ".record-row.picked") == 2
    # The two ends wear their roles, which is the only thing telling a reader
    # which end of a newest-first list is the start.
    assert count(page, ".record-mark.role-start") == 1
    assert count(page, ".record-mark.role-finish") == 1


def test_the_recorder_review_step_has_run_its_backtest(page):
    """`synth`/`btReport` are both fetched asynchronously on picking the
    second moment -- if the Story's setup did not wait for them, this measures
    "Working it out…"/"Testing against your history…" placeholders instead
    of the real content whose layout the sweep is supposed to be checking."""
    reach(page, "recorder-review")
    assert count(page, ".record-review:has-text('Working it out')") == 0
    assert count(page, ".record-review:has-text('Testing against your history')") == 0
    # Absence of a placeholder is not presence of content. `segmenttimeline.js`
    # renders "Working it out…" only while `!synth && !synthErr` — a FAILED
    # synthesize clears the placeholder and renders `.badx` instead, so both
    # this test and the one above pass green on an error state, measuring the
    # layout of an error box. (Delta review, finding 6.)
    assert count(page, ".record-review .badx") == 0, (
        "the review step rendered an error, not a synthesized definition — "
        "the placeholder assertions above cannot tell those apart")


def test_the_recorder_asks_what_the_recording_is_a_piece_of(page):
    """The ONLY door into a subsection. `parent` is absent from segments.js's
    SAVE_FIELDS and no other control in the app writes one, so if this
    control is unreachable the feature does not exist -- which is exactly
    what he reported ("what star has subsections? I don't see a way to define
    that?", 2026-08-05)."""
    reach(page, "recorder-review")
    assert count(page, ".record-parent") == 1
    assert count(page, ".record-parent .entity-trigger") == 1


def test_a_third_picked_moment_becomes_a_waypoint_the_person_chose(page):
    """Three picks is the state that did not exist before 2026-08-05. The
    middle one is a stop HE named, so the derived-walk picker is gone (its
    whole job was filling a middle nobody had named) and the review grows a
    "Then:" line."""
    reach(page, "recorder-waypoints")
    assert count(page, ".record-row.picked") == 3
    assert count(page, ".record-mark.role-stop") == 1
    assert count(page, ".record-review .step-picker") == 0
    assert count(page, ".record-review:has-text('Then:')") >= 1


def test_the_page_story_returns_to_practice_after_the_segments_tab(page):
    """The sweep's own self-healing guard (uilab_project.py's `_EXPAND_ALL`):
    without it, whichever Segments-tab story ran last in a viewport's pass
    would leave the NEXT viewport's "page" story measuring the Segments tab
    under the Practice page's name -- silently, since the sweep never
    reloads between viewports or stories."""
    reach(page, "recorder-review")
    reach(page, "page")
    assert count(page, ".log-card.log-card-active") == 1
    # >=1, not ==1: the mobile bottom-bar nav ALSO renders a "Practice" item
    # (hidden by CSS at this viewport, still present in the DOM), so a wide
    # viewport genuinely has two.
    assert page.count('button.nav-item[title="Practice"][aria-current="page"]') >= 1
