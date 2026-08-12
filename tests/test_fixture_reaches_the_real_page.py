"""The layout gate's fixture must render the page a human actually looks at.

This is the canary for the failure that has cost more than any other in this
project. Every layout gate here is only as good as the state `ui_fixture.py`
reaches, and when that state is wrong the gate does not go red — it reports a
clean page nobody is looking at. Three times, each hidden by the one before:

  2026-07-28  no stage       -> the Active Target card rendered "Nothing to
                                practice here". 26 real defects invisible, and
                                a whole feature (per-card collapse) served
                                correctly and rendered zero times, no error.
  2026-07-28  no strat/PB    -> the card rendered, but its rank display did
                                not. That display is the crowded part.
  2026-07-29  a one-strategy star -> the strategy ladder was also the star's
                                best ladder, so the card drew ONE combined
                                banner instead of the two-measure layout, and
                                that entire class of crowding defects was
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
                           BOWSER_COURSE, BOWSER_LEVEL,
                           _script as _ASYNC)
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

        # Task 7 fix round 1's own precondition: the ACTIVE card is a PAIRED
        # segment (`pipe_star_entity` set), the one shape whose book mark must
        # open the paired STAR's Library page rather than its own -- see
        # tests/test_ui_library_links.py for the full regression. The book
        # mark lives in `.log-card-head`, which renders whether or not the
        # card is open, so no fold click is needed here -- this only pins
        # that the fixture can produce the card the other file's test drives.
        has_book_mark = opened.evaluate(
            "!!document.querySelector('.log-card.log-card-active .log-card-library-link')")
        assert has_book_mark, (
            "the active paired-segment card carries no book mark button -- "
            "either ui_fixture.py stopped reaching this state, or "
            "practicelog.js stopped rendering one on a card an openLibrary "
            "caller was given")


def test_the_star_kind_carries_its_own_armed_detail():
    """The rule-11 ASYMMETRY, re-pointed at the payload 2026-08-06.

    Every star but the 100-coin one carries no `armed_detail`, because an
    ordinary star is one atomic grab with nothing to be part-way through
    (test_star_sections_carry_no_arm_detail). `_arm_segment` exercises the
    SEGMENT half; this is the STAR half, and until 2026-08-05 nothing rendered
    it at all -- `segments.hundred_coin_entity`'s reattribution path had only
    ever been unit-tested against hand-built dicts.

    It used to prove the asymmetry through the DRAWN `.seg-waiting` row. That
    row is deleted (Griffin, 2026-08-06: "we should just remove the step
    indicator entirely from the display here"), so the question moves down one
    layer to where the asymmetry actually lives -- the section the real server
    publishes to the real page. Still end to end: a live `serve_ui`, a real
    `POST /api/target`, the app's own `/api/session`. It just no longer asserts
    that a deleted element exists.

    `arm_hundred_coin` posts a synthetic def matching the pattern
    `hundred_coin_entity` looks for (a `star_grabbed(star=6, course=...)`
    WAYPOINT) and arms it, coexisting with the ordinary star target `serve_ui`
    seeds by default on the same course. The 100-coin star is TARGETED here
    too: arming its engine alone no longer publishes a card
    (`.claude/rules/hundred-coin.md`, "one CARD, only when the entity is the
    target"). Star 6 has no rank standards in the bundled seed, hence
    `with_pb=False`.
    """
    with serve_ui(arm_hundred_coin=(FIXTURE_COURSE, FIXTURE_LEVEL)) as base:
        with get_driver().launch() as opened:
            opened.goto(f"{base}/ui/index.html")
            opened.wait_for(".log-list-card")
            opened.wait_ms(300)
            _seed_target(base, FIXTURE_COURSE, 6, with_pb=False)
            opened.wait_ms(300)
            armed = json.loads(opened.evaluate("""
              fetch('/api/session?clock=igt').then(r => r.json()).then((view) =>
                JSON.stringify((view.stars || [])
                  .filter((sec) => sec && sec.armed_detail)
                  .map((sec) => ({slot: sec.star_id,
                                  steps: sec.armed_detail.steps,
                                  progress: sec.armed_detail.progress}))))
            """))
            assert len(armed) == 1, (
                f"expected exactly one STAR carrying armed_detail, got {armed}"
                " -- either the synthetic 100-coin engine never armed, was "
                "never targeted, or armed_detail leaked onto an ordinary star")
            only = armed[0]
            assert only["slot"] == 6, (
                f"the armed star is slot {only['slot']}, not the 100-coin "
                f"slot 6: {only}")
            assert only["steps"], (
                f"the 100-coin engine armed with no steps: {only}")
            assert isinstance(only["progress"], int)


# --- the Compare fold-in's backend (Task 6 fix round 2) --------------------
# This file's own canary lesson, a fifth time: `serve_ui()` had NO Compare
# backend at all until this round -- `create_app()` was never given
# `compare=`, so `/api/compare/view` 404d regardless of what the app code
# did, and every render test asserting `.compare-cmp` CONTENT (Task 6 fix
# round 1) could only be proved against a hand-built harness, never this
# shared fixture. Not a UI reach check (test_ui_library_compare.py already
# drives the real click-through) -- this is the fixture's own promise that
# the ROUTE exists at all, which is the thing that silently wasn't true.
#
# Placed HERE, above the module's own `page` fixture, for the SAME reason
# the two tests above it are: `serve_ui()`'s own seeding calls `asyncio.run()`
# internally, and running that in a thread whose event-loop state a prior
# `get_driver().launch()` has already touched hits the identical, real
# `asyncio.run() cannot be called from a running event loop` this file's own
# `reach()` docstring names for two Playwright launches in one process —
# measured directly: placed at the end of the file (after `page`'s many
# launches) this failed with exactly that traceback; moved here, clean.

def test_the_fixture_reaches_a_real_compare_backend():
    """`/api/compare/view` must answer 200 with a real (empty) payload, not
    404 -- confirms `serve_ui()` actually wires `compare=` into `create_app`
    rather than leaving it `None`. A 404 here is indistinguishable from a
    typo in the route path unless this asserts the STATUS, not just that a
    response arrived."""
    with serve_ui() as base:
        with urllib.request.urlopen(
                f"{base}/api/compare/view?entity=star:2:4", timeout=10) as r:
            assert r.status == 200, r.status
            body = json.loads(r.read())
        assert body["entity"] == "star:2:4", body
        assert body["saved"] == [], (
            "a fresh fixture should have no saved comparisons yet")


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


def test_one_rank_banner_with_both_mode_buttons_renders(page):
    """The active multi-ladder fixture reaches the new combined rank display."""
    banners = count(page, PRIMARY + ".rank-banner")
    assert banners == 1, f"{banners} rank banners rendered; expected exactly one"
    buttons = page.evaluate(
        "return Array.from(document.querySelectorAll("
        "'.log-card.log-card-active .rank-mode-button'))"
        ".map(b => [b.textContent.trim(), b.getAttribute('aria-pressed')])")
    assert buttons == [["Strategy", "true"], ["Overall", "false"]]


def test_rank_mode_button_runs_the_shared_swap_and_remembers_the_entity(page):
    """A mode pick is only an exchange, never a second earned-rank climb.

    Strategy alone carries a climb `replayKey`, so changing to Overall changes
    that key at the same moment as the rank. That used to outrank the ordinary
    identity guard and start a full Capless-5 climb underneath MARELO's short
    exchange. Once the exchange finished, the floor climb became visible and
    made a measurement swap feel like another rank-up.
    """
    page.evaluate(
        "Array.from(document.querySelectorAll("
        "'.log-card.log-card-active .rank-mode-button'))"
        ".find(b => b.textContent.trim() === 'Overall').click()")
    page.wait_ms(40)
    assert count(page, PRIMARY + ".rank-banner.is-swapping") == 1
    assert count(page, PRIMARY + ".rank-banner.is-climbing") == 0, (
        "a Strategy/Overall exchange also started the earned-rank climb")
    pressed = page.evaluate(
        "return Array.from(document.querySelectorAll("
        "'.log-card.log-card-active .rank-mode-button'))"
        ".filter(b => b.getAttribute('aria-pressed') === 'true')"
        ".map(b => b.textContent.trim())")
    assert pressed == ["Overall"]
    remembered = page.evaluate("""
const card = document.querySelector('.log-card.log-card-active');
const modes = JSON.parse(localStorage.getItem('sm64.practiceRankModes'));
return [card.dataset.feedKey, modes[card.dataset.feedKey]];
""")
    assert remembered[1] == "overall", remembered
    page.wait_ms(500)
    assert count(page, PRIMARY + ".rank-banner.is-swapping") == 0
    assert count(page, PRIMARY + ".rank-banner.is-climbing") == 0, (
        "the hidden floor climb outlived the short rank exchange")

    # Restore the module-scoped browser fixture for the rest of this file.
    page.evaluate(
        "Array.from(document.querySelectorAll("
        "'.log-card.log-card-active .rank-mode-button'))"
        ".find(b => b.textContent.trim() === 'Strategy').click()")
    page.wait_ms(40)
    assert count(page, PRIMARY + ".rank-banner.is-swapping") == 1
    assert count(page, PRIMARY + ".rank-banner.is-climbing") == 0, (
        "returning to Strategy replayed its floor climb instead of swapping")
    page.wait_ms(460)


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


def test_no_visible_log_card_draws_more_than_one_rank_banner(page):
    """The simplification is global, not special-cased to the active card."""
    per_card = page.evaluate(
        "return Array.from(document.querySelectorAll('.log-card'))"
        ".map(c => c.querySelectorAll('.rank-banner').length)")
    assert 1 in per_card, f"the fixture reached no rendered rank banner: {per_card}"
    assert all(n <= 1 for n in per_card), \
        f"a log card still paints side-by-side rank banners: {per_card}"


def test_no_log_card_draws_a_step_track_any_more(page):
    """INVERTED 2026-08-06, and kept rather than deleted because the fixture
    is the only place that reliably reaches the state.

    This used to assert the opposite: an armed segment's card drew "Step N of
    M" plus the whole route as chips, which Griffin had asked for by name --
    "i like the idea of knowing for sure the system is aware of me grabbing
    that first star, proven by it progressing to the next step." He retired
    that need once the engine was trusted: "the PURPOSE of that indicator was
    to make it clear that the segment logic was working for me during
    development, but now that it is indeed working, I don't think we really
    need this anymore."

    The SERVER side is untouched and is asserted just below -- the section
    still carries `armed_detail` with its cursor, so the capability is
    unpainted, not lost. That pair is the whole point of this test: a zero on
    the drawn side next to a live cursor on the data side is what tells a
    future reader the row was deleted deliberately rather than quietly broken.
    """
    assert count(page, ".log-card .seg-waiting") == 0, (
        "a practice-log card is drawing the armed step row again -- it was "
        "deleted for crowding the head (2026-08-06), and two attempts to "
        "seat it on the identity line were rejected before that")
    assert count(page, ".log-card .step-track") == 0
    armed = page.evaluate("""
      fetch('/api/session?clock=igt').then(r => r.json()).then((view) => {
        const all = (view.segments || []).concat(view.stars || []);
        const hit = all.find((sec) => sec && sec.armed_detail);
        return hit ? JSON.stringify(hit.armed_detail) : null;
      })
    """)
    assert armed, (
        "the fixture armed no segment -- without a live `armed_detail` the "
        "zero counts above are vacuous, which is the failure mode this file "
        "exists to catch")


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
    `.log-card-context` reading the literal "Segment" — it was found by its
    `.seg-waiting` row until 2026-08-06, when that row was deleted from every
    card, and a selector that matches nothing turns "the pick did not move the
    drawer" into "there was nothing to click", which reads identically in the
    failure. `ui_fixture.FIXTURE_SEGMENT` is not the active target in this
    fixture (`Practice()` suppresses its pin while a star target is active),
    so clicking it is a genuine manual browse pick AWAY from the active
    entity, exactly `ui/focustarget.js`'s spring-loaded mode. A star card is
    found by the mirror-image test — its `.log-card-context` NOT reading the
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
          const card = Array.from(document.querySelectorAll('.log-card'))
            .find((c) => (c.querySelector('.log-card-context')?.textContent
              || '').trim().toLowerCase() === 'segment');
          if (!card) return 'no segment log card found';
          const btn = card.querySelector('.log-card-select');
          if (!btn) return 'the segment card has no select button';
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


def test_the_clock_start_control_is_on_the_open_definition(page):
    """Round 15 item 3: a stored `clock_start` invisible in the editor is the
    exact shape match_mode shipped in for a day — the branch's central
    concept, unseeable and unchangeable from the app. The control must exist
    and show the STORED value (the fixture's definitions predate the field,
    so they read "trigger")."""
    reach(page, "segments-editor")
    assert count(page, ".builder-clockstart select") == 1
    # Against the ROW, never a literal: the fixture's definitions arrive
    # through whichever creation path it uses (the API body defaults "move"
    # since this round; a db-default row reads "trigger"), and the claim
    # here is only that the control shows the STORED value.
    verdict = page.evaluate("""
(async () => {
  const shown = document.querySelector('.builder-clockstart select').value;
  const name = document.querySelector('.builder-name input').value;
  const rows = await (await fetch('/api/segments')).json();
  const row = rows.find((r) => r.name === name);
  if (!row) return 'no row named ' + name;
  return shown === (row.clock_start || 'trigger')
    ? 'ok' : `control ${shown} != stored ${row.clock_start}`;
})()
""")
    assert verdict == "ok", verdict


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


def test_the_save_button_flashes_saved_after_a_real_save(page):
    """Round 18 item 2, driven end to end: click Save on the open editor and
    the button reads "Saved" (with the check icon) once the PUT resolves,
    then returns to "Save segment". Saving the untouched fixture definition
    is a no-op PUT, so the shared page's later tests see the same rows."""
    reach(page, "segments-editor")
    # `waitFor` RESOLVES false on timeout — it never throws (its own source,
    # tools/uilab_project.py). The first version of this test wrapped it in
    # try/catch and returned 'ok' unconditionally: green with the flash
    # mutated off, the exact vacuous-guard shape ui-core.md warns about.
    verdict = page.evaluate(_ASYNC("""
const saveBtn = Array.from(document.querySelectorAll('.builder-actions button'))
  .find((b) => b.textContent.includes('Save segment'));
saveBtn.click();
if (!await waitFor(() => saveBtn.textContent.includes('Saved'), 4000))
  return 'never flashed: ' + saveBtn.textContent;
if (!await waitFor(() => !saveBtn.textContent.includes('Saved'), 4000))
  return 'stuck on: ' + saveBtn.textContent;
return 'ok';
"""))
    assert verdict == "ok", verdict


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
    # the row and not in the review.
    #
    # REVERSED 2026-08-06. This asserted `0 < times < rows` -- SOME rows timed
    # and not all -- because the fixture's level edges carried no `igt_frames`
    # and neither did the real detector. His report: *"It looks like some
    # events have the timer next to them, most don't? I would expect the timer
    # for all of them."* `area.py`, `level.py` and `spawn.py` stamp the shared
    # clock now, and `ui_fixture._place_time` puts the same trio on every
    # hand-built place event -- so a blank cell here means a detector stopped
    # stamping, which is the only thing this can now be about.
    rows, times = count(page, ".record-row"), count(page, ".record-igt")
    assert times == rows, (
        f"only {times} of {rows} rows show a time — every type the recorder "
        "draws stamps one")
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


def test_the_rerecord_door_opens_the_recorder_carrying_the_row(page):
    """Round 16. The editor's re-record door is the ONLY entry into the
    recorder's replace intent — if it is unreachable, or opens a recorder
    that has forgotten which row it replaces, the feature does not exist
    (the same rule that produced the parent test above). Three claims, end
    to end in the real app: the door is on a saved definition's editor;
    clicking it opens the recorder in Re-record with NOTHING picked (a
    re-record starts from a fresh recording, not the old picks); and after
    two picks the name field holds the ROW's name, not the auto-name — the
    pre-fill arrives pre-marked as his, which is what stops every pick
    toggle overwriting it. Closes the modal after, so the recorder stories'
    own idempotent setups never inherit a replace intent."""
    reach(page, "segments-editor")
    # The recorder stories above leave the CREATE recorder open (their setups
    # are idempotent, not self-closing) — a human cannot click the editor's
    # door through a modal, so close it before this test does.
    page.evaluate(_ASYNC("""
const cancel = Array.from(document.querySelectorAll(
  '.modal .builder-actions button')).find((b) => b.textContent === 'Cancel');
if (cancel) { cancel.click();
  await waitFor(() => !document.querySelector('.record-picks')); }
"""))
    assert count(page, ".builder-rerecord button") == 1
    row_name = page.evaluate(
        "return document.querySelector('.builder-name input').value")
    page.evaluate(_ASYNC("""
document.querySelector('.builder-rerecord button').click();
await waitFor(() => !!document.querySelector('.record-picks'));
"""))
    # The Modal renders its title as a bare <h2 id="modal-title-N"> — no
    # .modal-title class exists to select on, so match by content.
    assert count(page, ".modal h2:has-text('Re-record')") == 1
    assert count(page, ".record-row.picked") == 0
    # Reuse the ONE script that reaches two-picked (the file's own rule) —
    # it finds this modal already open and picks into it.
    reach(page, "recorder-review")
    shown = page.evaluate(
        "return document.querySelector('.record-review .builder-name input')"
        + ".value")
    assert shown == row_name, (
        f"the recorder shows {shown!r} where the replaced row is named "
        f"{row_name!r} — the auto-name overwrote the pre-fill, so the save "
        "would silently rename the segment")
    assert count(page, ".record-replace-note") == 1
    save_label = page.evaluate("""
return Array.from(document.querySelectorAll('.builder-actions button'))
  .map((b) => b.textContent).join('|')
""")
    assert "Replace segment" in save_label, save_label
    # Drive the save itself: replace must land on the SAME row (the client's
    # PUT, not a second POST — a duplicate here orphans nothing visibly and
    # is exactly the silent failure the whole feature exists to avoid), with
    # the recording actually moved. Safe against the shared page: this is the
    # file's last test, and the second viewport gets its own fresh server.
    before = page.evaluate(_ASYNC("""
const rows = await (await fetch('/api/segments')).json();
const mine = rows.filter((r) => r.name === %s);
return JSON.stringify({n: rows.length, ids: mine.map((r) => r.id),
                       triggers: mine[0].start_triggers});
""" % json.dumps(row_name)))
    page.evaluate(_ASYNC("""
const saveBtn = Array.from(document.querySelectorAll(
  '.modal .builder-actions button'))
  .find((b) => b.textContent.includes('Replace segment'));
saveBtn.click();
await waitFor(() => !document.querySelector('.record-picks'), 5000);
"""))
    after = page.evaluate(_ASYNC("""
const rows = await (await fetch('/api/segments')).json();
const mine = rows.filter((r) => r.name === %s);
return JSON.stringify({n: rows.length, ids: mine.map((r) => r.id),
                       triggers: mine[0].start_triggers});
""" % json.dumps(row_name)))
    was, now = json.loads(before), json.loads(after)
    assert now["ids"] == was["ids"], (
        f"row ids for {row_name!r} moved {was['ids']} -> {now['ids']} — the "
        "save created a new row instead of replacing the old one")
    assert now["n"] == was["n"], (
        f"the library grew {was['n']} -> {now['n']} rows — the save POSTed a "
        "duplicate instead of PUTting the replaced id")
    assert now["triggers"] != was["triggers"], (
        "the replaced row still holds its old start triggers — the save "
        "landed nowhere")
    # Round 17 item 1: the OPEN editor below must show the server's version.
    # The Builder is keyed by segment id and the replace keeps the id, so
    # without a forced remount its `d` state (read from `initial` exactly
    # once) keeps rendering the PRE-replace definition — which is his report
    # verbatim: "it doesn't feel like the start/finish fields were changed".
    # Three comparisons, all against the API row rather than guessed
    # constants: the start clause's TYPE select, its `to` param when the
    # clause has one, and the Then section's step count.
    editor = json.loads(page.evaluate(_ASYNC("""
await waitFor(() => !!document.querySelector('.segbuilder'), 5000);
const start = document.querySelector('.seg-start');
const selects = Array.from(start.querySelectorAll('select'))
  .map((s) => s.value);
return JSON.stringify({
  startType: selects[0] || null,
  startValues: selects,
  thenSteps: document.querySelectorAll('.then-step').length,
});
""")))
    row = json.loads(page.evaluate(_ASYNC("""
const rows = await (await fetch('/api/segments')).json();
const mine = rows.find((r) => r.name === %s);
return JSON.stringify({start: mine.start_triggers[0],
                       waypoints: mine.waypoints.length});
""" % json.dumps(row_name))))
    assert editor["startType"] == row["start"]["type"], (
        f"the editor's Start clause reads {editor['startType']!r} where the "
        f"server row now holds {row['start']['type']!r} — the Builder kept "
        "its pre-replace state instead of remounting on the fresh row")
    if "to" in row["start"]:
        assert str(row["start"]["to"]) in editor["startValues"], (
            f"the editor's Start params {editor['startValues']} do not show "
            f"the replaced clause's to={row['start']['to']} — stale state")
    assert editor["thenSteps"] == row["waypoints"], (
        f"the editor's Then section draws {editor['thenSteps']} step(s) "
        f"where the server row holds {row['waypoints']} — his second "
        "screenshot exactly (the CCM door stop missing from Then)")


def test_a_castle_area_tile_opens_its_movements_and_still_answers_as_itself(page):
    """2026-08-09: "I should be able to click into Upstairs, and be able to
    select any segment within Upstairs OR select a general 'Upstairs'
    association (as it is today)." Round 14 made the tile TERMINAL, which put
    every castle movement out of reach as a parent — a piece of an Upstairs
    BLJ could never name the BLJ. The tile drills now, its layer 2 leads with
    the area's own cell, and picking that cell is the answer round 14 asked
    for. Both halves are driven here: the drill has to expose at least one
    movement beside the self cell (a drill onto an empty grid would read as
    the feature working while nothing new is selectable), and the self cell
    has to close the dialog with the area's name on the trigger."""
    reach(page, "recorder-review")
    verdict = page.evaluate("""
(async () => {
  const waitFor = async (test, ms = 4000) => {
    const until = Date.now() + ms;
    while (Date.now() < until) {
      if (test()) return true;
      await new Promise((r) => setTimeout(r, 20));
    }
    return false;
  };
  // SCOPED to the recorder's own dialog, never a bare `.entity-grid`. The
  // Library tab (spec 2026-08-07-library-page) renders the SAME picker as
  // its course browser and stays mounted with `display:none` when you leave
  // it, so an unscoped query finds that hidden grid first (measured: one
  // `.entity-grid` inside `.library-courses`, `offsetParent` null, present
  // before this dialog opens) -- its own "Castle Movements (Lobby)" tile
  // matched the text below and the drill it performed read as this dialog
  // refusing to close. Same trap, same remedy as the `.library-search` scope
  // in tools/uilab_project.py.
  const grid = () => document.querySelector('.record-review .entity-grid');
  document.querySelector('.record-parent .entity-trigger').click();
  if (!await waitFor(() => !!grid()))
    return 'the parent dialog never opened';
  const tiles = Array.from(grid().querySelectorAll('button'));
  const lobby = tiles.find((b) => b.textContent.includes('Lobby'));
  if (!lobby) return 'no Lobby tile: ' +
    JSON.stringify(tiles.map((b) => b.textContent.trim()).slice(-8));
  lobby.click();
  if (!await waitFor(() =>
        !!document.querySelector('.record-review .entity-back')))
    return 'the tile never drilled in';
  const cells = Array.from(grid().querySelectorAll('button'));
  if (cells.length < 2)
    return 'the Lobby holds no movements to pick: ' +
      JSON.stringify(cells.map((b) => b.textContent.trim()));
  if (!cells[0].textContent.includes('Lobby'))
    return 'the area does not lead its own layer: ' +
      JSON.stringify(cells.map((b) => b.textContent.trim()).slice(0, 3));
  cells[0].click();
  if (!await waitFor(() => !grid()))
    return 'the dialog stayed open — the area cell did not answer';
  const trigger = document.querySelector('.record-parent .entity-trigger');
  if (!trigger.textContent.includes('Lobby'))
    return 'the trigger reads ' + trigger.textContent.trim();
  // Leave the parent as it was found, through the dialog's own clear cell.
  trigger.click();
  await waitFor(() => !!document.querySelector('.record-review .entity-clear'));
  document.querySelector('.record-review .entity-clear').click();
  await waitFor(() => !grid());
  return 'ok';
})()
""")
    assert verdict == "ok", verdict


def test_a_typed_segment_name_survives_a_pick_change(page):
    """Round 12 item 4: "once I set the name for the segment name it
    shouldn't change". Every pick toggle re-derives the definition and used
    to overwrite the name field with the fresh auto-name — the auto-fill may
    only fill a field he has not edited."""
    reach(page, "recorder-review")
    typed = page.evaluate(
        "const input = document.querySelector('.builder-name input');"
        "input.value = 'My Own Name';"
        "input.dispatchEvent(new Event('input', { bubbles: true }));"
        "return input.value;")
    assert typed == "My Own Name"
    page.evaluate(
        "document.querySelector('.record-row:not(.picked)').click();")
    page.wait_ms(600)   # derive() round-trips /api/segments/synthesize
    held = page.evaluate(
        "return document.querySelector('.builder-name input').value;")
    assert held == "My Own Name", (
        f"the pick change re-derived the auto-name over his ({held!r})")
    # Put the third pick back so later stories start from their own setup
    # with nothing extra picked (Story setups are idempotent, but this click
    # was ours, not theirs).
    page.evaluate(
        "const picked = document.querySelectorAll('.record-row.picked');"
        "picked[1].click();")
    page.wait_ms(200)


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


# --- the Library tab (Task 3, spec 2026-08-07-library-page) ---------------
# Appended at the end, per this file's own canary lesson at the top: a fixture
# that does not reach the state a feature needs does not go red, it reports a
# clean page nobody is looking at. Placed last so it inherits the "page" story
# above's own self-healing return to Practice, rather than measuring whatever
# tab the LAST test above it happened to leave open.

CLICK_LIBRARY_TAB = 'document.querySelector(\'.nav-item[title="Library"]\').click()'


@pytest.fixture(scope="module")
def fresh_db_page():
    """An UNSEEDED instance -- no stage, no target, no attempts anywhere --
    so `librarymodel.js::lastPracticed` has nothing to resolve and the
    Library tab's auto-open must fall back to the course grid rather than
    erroring or rendering nothing (task-3-caveats.md point 3: null is the
    empty-log case). A fresh fixture rather than a state reached by clicking
    around `page` -- that fixture's own default seeding is exactly what the
    OTHER new test below needs present."""
    with serve_ui(seed=False) as base, get_driver().launch() as opened:
        opened.goto(f"{base}/ui/index.html")
        opened.wait_for(".log-list-card")
        yield opened


def test_the_library_tab_reaches_the_target_page(page):
    """Auto-open's whole point: switching to the Library tab with a practiced
    entity in hand lands straight on that entity's target page, not on the
    course grid the user would then have to re-navigate through by hand.
    FIXTURE_STAR (star:2:4, "Fall onto the Caged Island") is the NEWEST
    entity by journal_id in this fixture (ui_fixture.py's own comment on
    FIXTURE_STAR has the measurement), so `lastPracticed` resolves to it
    every time."""
    page.evaluate(CLICK_LIBRARY_TAB)
    page.wait_for(".library-target", timeout_ms=15000)


def test_an_empty_log_falls_back_to_the_course_grid(fresh_db_page):
    """The other half of the same rule, with nothing to land on."""
    fresh_db_page.evaluate(CLICK_LIBRARY_TAB)
    fresh_db_page.wait_for(".library-courses", timeout_ms=15000)


def test_the_library_search_story_reaches_its_own_result_rows(page):
    """Round 12's story earns its line here for the reason this whole file
    exists: the results REPLACE the course grid, so a sweep of the landing
    page can never draw a result row, and a story whose setup silently fails
    reports a clean surface nobody is looking at rather than going red.

    Two things are asserted, not one: that rows exist, and that a row carries
    BOTH its lines. A row list that rendered with empty text would satisfy a
    selector wait and measure nothing -- the vacuous-guard shape ui-core.md
    names."""
    reach(page, "library-search")
    verdict = page.evaluate("""
      (() => {
        const rows = Array.from(document.querySelectorAll(
          '.library-searching .library-result'));
        if (!rows.length) return 'the story drew no result rows';
        const bad = rows.find((row) => {
          const name = row.querySelector('.library-result-name');
          const sub = row.querySelector('.library-result-sub');
          return !name || !sub || !name.textContent.trim() || !sub.textContent.trim();
        });
        if (bad) return 'a row rendered with an empty line: ' + bad.textContent;
        if (document.querySelector('.library-searching .entity-grid'))
          return 'the course grid is still drawn beside the results';
        return 'ok';
      })()
    """)
    assert verdict == "ok", verdict
    # Leave the tab as the other stories expect to find it.
    page.evaluate("""
      (() => {
        const box = document.querySelector('.library-page .library-find-input');
        if (!box) return;
        const setter = Object.getOwnPropertyDescriptor(
          window.HTMLInputElement.prototype, 'value').set;
        setter.call(box, '');
        box.dispatchEvent(new Event('input', {bubbles: true}));
      })()
    """)
