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

sys.path.insert(0, str(Path(__file__).parent))
from source_scan import python_code, strip_comments  # noqa: E402

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
    """`pipe_star_entity`/`pipe_segment_id` (activestrat.py's `reds_pipe_segments`)
    drive the "(Pipe)"/"(Star)" suffixed naming a Bowser Reds star and its
    paired `seg:reds->pipe:<abbrev>` segment borrow from each other
    (redsfamily.js::familyLabel) -- and until now no fixture ever loaded the
    corpus segment this needs (`reds_pipe_segments` matches by `seed_key`,
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
            "stopped resolving it (pb-strategy.md's reds_pipe_segments)")

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


def test_the_fixture_reaches_a_real_capture_layer():
    """`/api/setup` must answer 200 with a real payload, not 404 -- confirms
    `serve_ui()` wires `capture_layer=` into `create_app` (setup_api.py is
    mounted only when one is given, same as the inputs router above), and
    that `capture_layer_status` overrides actually reach the response, or the
    setup modal's own uilab test would be reading a fixture state it never
    asked for."""
    with serve_ui(capture_layer_status={"state": "active", "pj64_dir": "C:/PJ64"}) as base:
        with urllib.request.urlopen(f"{base}/api/setup", timeout=10) as r:
            assert r.status == 200, r.status
            body = json.loads(r.read())
        assert body["platform"] == "emu"
        assert body["emu"]["state"] == "active"
        assert body["emu"]["pj64_dir"] == "C:/PJ64"
        assert body["n64"] == {"available": False}


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

    Strategy used to carry a climb `replayKey`, so changing to Overall changed
    that key at the same moment as the rank. That outranked the ordinary
    identity guard and started a full Capless-5 climb underneath MARELO's
    short exchange; once the exchange finished, the floor climb became
    visible and made a measurement swap feel like another rank-up. The replay
    is gone altogether since 2026-08-23 (a strategy swap is an exchange too),
    and this still pins that the exchange never runs a climb underneath.
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
    erroring or rendering nothing (null is the empty-log case). A fresh
    fixture rather than a state reached by clicking
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


def test_the_scorecard_reaches_a_card_with_tiles(page):
    """The Rank tab's scorecard (spec 2026-08-23-scorecard-design, task 3)
    is behind a nav click, not on the page by default -- unreachable by
    this gate until the 'scorecard' Story's own setup navigates there."""
    reach(page, "scorecard")
    page.wait_for(".rank-page .scorecard-card .score-line", timeout_ms=15000)
    assert count(page, ".rank-page .scorecard-card .score-line") > 0


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


# --- input capture (2026-08-21) ---------------------------------------------
# Added the same day the timeline landed, for the reason the header states: the
# FIRST render of this surface showed "No inputs recorded for this attempt",
# which is a clean page nobody is looking at. The chunks were there; they were
# scoped so the attempt the drawer opens saw none of them. A sweep over that
# would have reported the whole feature healthy while measuring an empty state.

def test_the_attempt_drawer_reaches_a_populated_input_timeline(page):
    reach(page, "input-timeline")
    assert count(page, ".input-lanes") == 1, (
        "the attempt drawer rendered no input lanes -- the fixture is "
        "measuring the 'no inputs recorded' state, not the timeline")
    bars = count(page, ".input-bar:not(.is-template)")
    assert bars >= 3, (
        f"only {bars} input bars drawn; a track with fewer button runs than "
        "that cannot show a lane crowding its neighbour, which is what this "
        "surface is measured for")


def test_the_drawer_reaches_a_TEMPLATE_drawn_behind_the_run(page):
    """The comparison is the whole point of the feature. Without a template
    seeded, every sweep measures the single-track layout and the two-track one
    -- the crowded case -- is unreachable, which is exactly how the
    one-strategy star hid a class of defects for two days."""
    reach(page, "input-timeline")
    assert count(page, ".input-template-note") == 1, (
        "no template note rendered -- the fixture seeded no template, so the "
        "compared-against layout is not being measured at all")
    assert count(page, ".input-bar.is-template") >= 1, (
        "the template note rendered but no template bars did")


def test_the_inspector_reaches_a_frame_with_a_real_reading(page):
    """The controller panel is the export's renderer too, so a fixture that
    only ever shows it centred and empty measures neither consumer."""
    reach(page, "input-timeline")
    assert count(page, ".controller-panel") >= 1
    # A BARE expression, never an arrow: the driver wraps what it is given in
    # `() => { return (...); }`, so passing a function returns the function
    # itself and comes back None -- measured 2026-08-21, and indistinguishable
    # from a page with nothing on it.
    values = page.evaluate(
        "Array.from(document.querySelectorAll('.stick-value'))"
        ".map((el) => el.textContent.trim())")
    assert any(value and value != "--" for value in values), (
        "every stick value read '--' -- the inspector is parked on a frame "
        "with the stick centred, so the panel's populated layout is unmeasured")


def test_the_drawer_draws_the_TEMPLATE_S_MARIO_behind_yours(page):
    """His ruling 2026-08-22: the comparison includes "all mario data". The
    template's action row, its speed line and its facing dial all render, or
    the sweep measures a drawer that compares the pad alone."""
    reach(page, "input-timeline")
    assert count(page, ".input-lane.is-actions") == 1, (
        "both action tracks must share the same Mario lane")
    assert count(page, ".input-lane.is-actions .action-span.is-template") >= 3
    assert count(page, ".action-span.is-template") >= 3
    assert count(page, ".speed-line.is-template") == 1
    labels = page.evaluate(
        "Array.from(document.querySelectorAll('.controller-panel-label'))"
        ".map((el) => el.textContent.trim())")
    assert "Mario faces" in labels, labels
    assert count(page, ".facing-dial") == 1


def test_the_drawer_reaches_a_MOMENT_on_the_track(page):
    """Round 32 item 3: the journal's moments joined onto the track. The
    fixture publishes one pole grab inside every attempt's captured span;
    without it the moments row never renders and the sweep measures a drawer
    with no such row -- the same "clean page nobody is looking at" failure
    this file exists to catch. The tick reads the recorder's own sentence."""
    reach(page, "input-timeline")
    assert count(page, ".input-lane.is-moments") == 1, (
        "no moments row -- the fixture's attempt window holds no journal "
        "moment inside its track, or the row is not drawn")
    labels = page.evaluate(
        "Array.from(document.querySelectorAll('.moment-mark'))"
        ".map((el) => el.getAttribute('title'))")
    assert any(label.startswith("Grab a pole in ") for label in labels), labels
    # THE TICK, not the button. The button spans from its moment to the NEXT
    # one, and the design system's own `button` rule centres flex content --
    # so a guard measuring the button's left edge passed while the tick drew
    # mid-span, a third of the timeline from its own frame (his report
    # 2026-08-23). Every number here is READ OFF THE SURFACE -- the marker's
    # own axis frame, the attempt's length from the header, the lead-in's
    # from its note -- because the version that hard-coded the fixture's
    # "30 frames into 63" went stale the moment the lead-in landed and
    # reported a placement bug that did not exist (2026-08-31).
    tick_at, button_at, track, marker_frame, total = page.evaluate(
        "(() => {"
        " const tick = document.querySelector('.moment-mark-tick')"
        "  .getBoundingClientRect();"
        " const button = document.querySelector('.moment-mark');"
        " const lane = document.querySelector("
        "  '.input-lane.is-moments .input-lane-track').getBoundingClientRect();"
        " const head = document.querySelector('.input-timeline-head h4');"
        " return [tick.left, button.getBoundingClientRect().left, lane,"
        "  Number(button.getAttribute('data-frame')),"
        "  Number(head.getAttribute('data-total'))];"
        "})()")
    expected = track["x"] + (marker_frame / total) * track["width"]
    assert abs(tick_at - expected) < track["width"] / total, (
        f"the moment's TICK sits at {tick_at:.0f}, its frame is at "
        f"{expected:.0f} -- the label is being centred in the button again")
    assert abs(tick_at - button_at) < 4, (
        "the tick is not at the button's own left edge")


def test_the_playhead_travels_over_the_tracks_and_never_the_labels(page):
    """His report 2026-08-22: "I can drag the frame start position to the
    left of the row labels... Frame 0 should start AFTER the labels". The
    playhead lives in a column overlay whose left edge must be the tracks'
    own left edge -- at the wide label width and the narrow one, since the
    width is a container-query variable and a drift there is a playhead
    over the words again."""
    reach(page, "input-timeline")
    for viewport in ((1400, 900), (860, 900)):
        page.set_viewport(*viewport)
        page.wait_ms(150)
        column_left, track_left, label_right = page.evaluate(
            "(() => { const c = document.querySelector('.input-track-column')"
            ".getBoundingClientRect(); const t = document.querySelector("
            "'.input-lane-track').getBoundingClientRect(); const l = document"
            ".querySelector('.input-lane-name').getBoundingClientRect();"
            " return [c.left, t.left, l.right]; })()")
        assert abs(column_left - track_left) < 1.0, (
            f"at {viewport}: the playhead column starts at {column_left:.1f} "
            f"but the tracks start at {track_left:.1f}")
        assert column_left >= label_right - 0.5, (
            f"at {viewport}: the playhead column overlaps the label column")


def test_the_open_drawer_stays_inside_its_card_below_the_supported_width(page):
    """Below 760px the attempt rows become blocks, and a block's `height` is
    a hard size where a table row's was a minimum -- so two row-height rules
    pinned the drawer's row to 40px and the clip and the timeline painted
    over every card beneath (his report 2026-08-23: "it gets totally messed
    up on a small enough screen width"). Below the 850px floor is not swept,
    so this is the one check that the drawer's row is exempt."""
    reach(page, "input-timeline")
    for viewport in ((600, 1400), (850, 1400)):
        page.set_viewport(*viewport)
        page.wait_ms(250)
        drawer_bottom, card_bottom = page.evaluate(
            "(() => { const d = document.querySelector('.attempt-drawer');"
            " const c = d.closest('.log-card');"
            " return [d.getBoundingClientRect().bottom,"
            " c.getBoundingClientRect().bottom]; })()")
        assert drawer_bottom <= card_bottom + 1, (
            f"at {viewport}: the drawer ends at {drawer_bottom:.0f} but its "
            f"card ends at {card_bottom:.0f} -- the drawer's row is clamped")
    page.set_viewport(1400, 900)


def test_the_timeline_reaches_a_LEAD_IN_and_frame_zero_stays_the_attempt(page):
    """Round 32 items 51-52: he warps into the level, adjusts the camera,
    then resets -- the track now reaches back to that entry. The lead must
    RENDER (the shaded band + the header note), and FRAME 0 must still be
    the attempt's own start: the readout total is the attempt's length,
    not the track's."""
    reach(page, "input-timeline")
    assert count(page, ".input-lead-shade") == 1, (
        "no lead band rendered -- the fixture seeded no level entry before "
        "the anchor, so the lead-in layout is unreachable by every sweep")
    # The lead-in NOTE is gone (his 2026-09-01 ruling: "we just shouldn't
    # display the lead-in at all" -- the band stays, the numbers do not);
    # the drawn span rides the header as data attributes for the sweeps.
    assert count(page, ".input-lead-note") == 0
    total, lead = page.evaluate(
        "(() => { const h = document.querySelector('.input-timeline-head h4');"
        " return [Number(h.getAttribute('data-total')),"
        "  Number(h.getAttribute('data-lead'))]; })()")
    assert lead > 0 and total > lead
    # The header prints the ATTEMPT's own length -- the number on the row
    # above it -- never the track's, which carries the clip's buffers.
    frames_head = page.evaluate(
        "document.querySelector('.input-timeline-head h4').textContent")
    shown = int(frames_head.split("frames")[0].split("·")[-1].strip())
    assert shown < total, (frames_head, total)
    readout = page.evaluate(
        "document.querySelector('.input-inspector-frame strong').textContent")
    # BOTH halves are frame NUMBERS on the same zero-based axis, so the
    # denominator is the LAST frame rather than how many there are.
    assert int(readout.split("/")[1].strip()) == total - lead - 1


def test_the_frame_readout_can_reach_its_own_last_frame(page):
    """His report, 2026-09-05: the panel read 498 / 499 at the end of the
    clip and no step could reach 499 -- "from a user perspective this looks
    like an error, not intentional". The numerator was the zero-based axis
    frame and the denominator was the COUNT, so the readout could never
    equal itself. Seeking to the far right of the track must now land on
    n / n. Mutation proof: put the count back and this goes red."""
    reach(page, "input-timeline")
    page.evaluate(
        "(() => { const lanes = document.querySelector('.input-lanes');"
        " const box = lanes.getBoundingClientRect();"
        " lanes.dispatchEvent(new PointerEvent('pointerdown',"
        "   {bubbles: true, clientX: box.right, clientY: box.top + 4}));"
        " return true; })()")
    page.wait_ms(200)
    readout = page.evaluate(
        "document.querySelector('.input-inspector-frame strong').textContent")
    here, last = (part.strip() for part in readout.split("/"))
    assert here == last, (
        f"seeking to the end of the track reads {readout!r} -- the panel "
        "cannot reach its own last frame")


def test_a_disagreeing_picture_reaches_the_timeline_header(page):
    """The clip's pad check is SILENT when it passes and speaks only when the
    timeline contradicts what the game held. The fixture's synthetic view
    carries one contradicted picture, so the chip renders here; the test
    below proves it vanishes when nothing disagrees.

    His ruling on the passing state, 2026-09-05: "displaying this to the user
    is really weird lol, worthless information for them". A check that has
    never failed is a fact about our plumbing, not about his run -- the same
    call he made retiring the segment step indicator once the segment logic
    worked."""
    reach(page, "input-timeline")
    page.wait_for(".input-screen-check", timeout_ms=8000)
    chip = page.evaluate(
        "document.querySelector('.input-screen-check').textContent")
    assert "1 picture" in chip and "disagree with the game" in chip, chip
    # No ratio and no count of what passed: the chip is news, not a statistic.
    assert "/" not in chip and "all agree" not in chip, chip
    # The chip is a DOOR (his rule: a datum on a summary surface leads to its
    # evidence): click it and every disagreeing picture is listed as the panel
    # frame it sits on, with what the game held and what the timeline holds;
    # click a row and the panel goes there.
    page.click(".input-screen-check")
    page.wait_for(".input-screen-check-row", timeout_ms=4000)
    row_text, frame_text = page.evaluate(
        "(() => { const row = document.querySelector('.input-screen-check-row');"
        " return [row.textContent, row.querySelector('.frame').textContent]; })()")
    assert frame_text.startswith("frame ") and "71,0" in row_text and "70,0" in row_text, row_text
    page.click(".input-screen-check-row")
    page.wait_ms(200)
    readout = page.evaluate(
        "document.querySelector('.input-inspector-frame strong').textContent")
    listed = int(frame_text.split()[1])
    assert int(readout.split("/")[0].strip()) == listed, (readout, frame_text)


def test_a_clip_whose_pads_all_agree_draws_no_chip_at_all(page):
    """The half his ruling is actually about, and a state live data always
    holds but the fixture cannot seed twice: every picture agreeing. Reached
    the way `.claude/rules/ui-core.md` prescribes -- rewrite the captured
    response on its way in -- so the REAL component renders a clean view.

    The header must then carry the time, the frame count and nothing else.
    Mutation proof: restore the "all agree" branch in `screenCheck` and this
    goes red while its sibling above stays green."""
    # A fresh page first: the drawer fetches its view once, on open, so a
    # patch installed while it is already open changes nothing (measured --
    # the previous test's payload rendered straight through).
    page.evaluate("(() => { location.reload(); return true; })()")
    page.wait_for(PROJECT.ready_selector, timeout_ms=15000)
    page.wait_ms(400)
    page.evaluate(
        "(() => {"
        "  const real = window.fetch;"
        "  window.fetch = async (input, init) => {"
        "    const response = await real(input, init);"
        "    const url = typeof input === 'string' ? input : input.url;"
        "    if (!/\/replay$/.test(url || '')) return response;"
        "    const body = await response.clone().json();"
        "    if (body && body.pad_stamp_agreement) {"
        "      body.pad_stamp_agreement.agree = body.pad_stamp_agreement.pictures;"
        "      body.pad_stamp_agreement.disagreements = [];"
        "    }"
        "    return new Response(JSON.stringify(body), {status: 200,"
        "      headers: {'content-type': 'application/json'}});"
        "  };"
        "  return true; })()")
    reach(page, "input-timeline")
    page.wait_for(".input-timeline", timeout_ms=8000)
    page.wait_ms(400)
    head = page.evaluate(
        "(() => { const h = document.querySelector('.input-timeline-head');"
        "  return [h.textContent, document.querySelectorAll('.input-screen-check').length];"
        " })()")
    assert head[1] == 0, f"a clip whose pads all agree still drew a chip: {head[0]!r}"
    assert "frames" in head[0] and "fps" in head[0], head[0]
    # Put the real fetch back for whatever runs next on this shared page.
    page.evaluate("(() => { location.reload(); return true; })()")
    page.wait_for(PROJECT.ready_selector, timeout_ms=15000)


# --- and it must not leave the state it reached behind ---------------------

_SHARED_STORE_READ = re.compile(r"\brank_standards_path\b")


def test_the_fixture_never_reaches_for_the_shared_ladder_store():
    """`serve_ui` gives every fixture its OWN scratch rank-standards file, and
    that has to stay structural rather than remembered.

    Measured 2026-08-21. One new test cleared four of `star:2:4`'s five
    strategies through the real endpoint and did not put them back; the next
    full suite returned **6 failures and 4 errors across four unrelated
    files** -- the JP toggles, the Library's overall ladder, the rank-mode
    swap, the you-marker -- every one of them a test that simply needed that
    star to still have its strategies. Not one of them could name the culprit:
    the damage was in a gitignored file no assertion mentions.

    `conftest.py` HAS an autouse `_isolate_rank_standards` for exactly this,
    and it never reached any of it. It rebinds the attribute on the `paths`
    MODULE, while `ui_fixture.py` held a `from ... import rank_standards_path`
    alias taken at import time -- so the patch moved a name the fixture was
    not looking at. An isolation fixture that protects nothing looks identical
    to one that works, which is what makes this worth a check of its own
    rather than a comment on the import.

    Hence the invariant is stated where it can be enforced, in two halves.
    The fixture may not NAME the shared path at all -- it serves from its own
    scratch store. And NOTHING under src/ or tools/ may hold a MODULE-LEVEL
    alias of it (`main.py`'s is function-local, which re-resolves on every
    call and is therefore inside the monkeypatch's reach), so the conftest
    fixture is honest again for every caller that remains, and the next
    driven harness nobody has written yet cannot reopen the hole. Restoring
    the store by hand is not an accepted alternative -- it works only when
    the test passes, and the run that most needs the store intact is the run
    where something failed halfway."""
    repo = Path(__file__).resolve().parents[1]
    fixture = repo / "tools" / "ui_fixture.py"
    # `python_code`, not `strip_comments` -- the latter removes JS/CSS comment
    # styles and leaves Python `#` lines standing, so this guard would have
    # tripped on a comment explaining the very absence it checks for. Caught by
    # its own probe below, which is why both directions get asserted and not
    # just the one that was failing.
    source = python_code(fixture.read_text(encoding="utf-8"))
    assert not _SHARED_STORE_READ.search(source), (
        "tools/ui_fixture.py names rank_standards_path again, so every driven "
        "test is once more writing the worktree's own data/rank_standards.json "
        "-- and conftest's autouse isolation cannot help, because a "
        "`from paths import` alias is invisible to its monkeypatch.")
    # ... and it must still hand RankStandards a path under a scratch dir,
    # rather than simply having dropped the store.
    assert "rank_standards.json" in source

    aliased = [str(path.relative_to(repo)).replace("\\", "/")
               for folder in ("src", "tools")
               for path in (repo / folder).rglob("*.py")
               if path.name != "paths.py" and _module_level_alias(path)]
    assert not aliased, (
        f"{aliased} import rank_standards_path at module level, which takes "
        "the name at import time and is invisible to conftest's "
        "_isolate_rank_standards monkeypatch. Call paths.rank_standards_path() "
        "through the module, or import it inside the function that needs it.")


def _module_level_alias(path: Path) -> bool:
    """Does this file hold `rank_standards_path` as a MODULE-level import
    alias? A function-local `from ... import` re-resolves at call time and is
    fine; a top-level one is a copy the monkeypatch cannot reach."""
    import ast
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(isinstance(node, ast.ImportFrom)
               and any(alias.name == "rank_standards_path" for alias in node.names)
               for node in tree.body)


def test_the_shared_store_guard_can_still_fail():
    """Both directions through the same `python_code` the guard uses: real code
    must trip it, and a Python comment naming the function must not.

    The second half is not ceremony -- it failed on its first run, because the
    guard was reaching for `strip_comments`, which only knows JS and CSS
    comment styles. A `#` line naming `rank_standards_path` survived it, so the
    guard would have gone red at the mere mention of what it forbids."""
    real = python_code("ranks = RankStandards(rank_standards_path())\n")
    prose = python_code("# never call rank_standards_path() from here\n")
    assert _SHARED_STORE_READ.search(real)
    assert not _SHARED_STORE_READ.search(prose)


def test_the_alias_scan_can_still_fail(tmp_path):
    """The module-level alias is the whole mechanism of the 2026-08-21 leak,
    so the scan has to tell it from the function-local import main.py holds."""
    top = tmp_path / "top.py"
    top.write_text("from sm64_events.core.paths import rank_standards_path\n")
    local = tmp_path / "local.py"
    local.write_text("def build():\n"
                     "    from sm64_events.core.paths import rank_standards_path\n"
                     "    return rank_standards_path()\n")
    assert _module_level_alias(top)
    assert not _module_level_alias(local)


# --- the Rank tab's leaderboard (Task 4, spec 2026-08-20-ranked-leaderboard) -
# This file's own canary lesson, a sixth time: no story ever navigated to the
# Rank tab at all before this, so a whole tab -- not just one card on it --
# was invisible to the responsive sweep. `uilab_project.py`'s new
# "rank-leaderboard" story is what tests/test_responsive.py now drives; this
# reuses the SAME named story on the SAME shared `page`, so the sweep and this
# assertion can never quietly disagree about whether the state is reachable.

def test_the_rank_tab_story_reaches_a_real_leaderboard(page):
    """The default fixture needs no extra seeding for this -- `serve_ui()`'s
    app builds its LibraryStore off the bundled Ultimate Sheet snapshot
    unconditionally, so the default "overall" scope already carries hundreds
    of real community rows plus the user's own. Confirmed directly before
    writing this file (443 rows, one `you` row, never omitted from it)."""
    reach(page, "rank-leaderboard")
    rows = page.evaluate(
        "document.querySelectorAll('.leaderboard-row').length")
    assert rows > 20, f"the leaderboard drew only {rows} rows"
    you_rows = page.evaluate(
        "document.querySelectorAll('.leaderboard-row.is-you').length")
    assert you_rows == 1, (
        f"expected exactly one .is-you row, found {you_rows} -- the user's "
        "own row must always place, even on a fresh fixture")
    # Leave the tab as the "page" story's own self-heal expects to find it —
    # it only clicks Practice if aria-current says otherwise, so this just
    # confirms that guard actually sees the Rank tab as active.
    on_rank = page.evaluate(
        'document.querySelector(\'button.nav-item[title="Rank"]\')'
        ".getAttribute('aria-current')")
    assert on_rank == "page", (
        "the rank-leaderboard story left the tab in an unexpected state")


# --- the runner page (Task 5, spec 2026-08-20-ranked-leaderboard) ----------
# Same lesson as the leaderboard story above, one layer deeper: a story that
# reaches the BOARD does not thereby reach the PAGE it opens. `uilab_project`'s
# new "runner-page" story clicks the gesture leaderboard.js itself wires
# (Task 5) and this reuses that SAME named story on the SAME shared `page`.

def test_the_runner_page_story_reaches_a_real_runner(page):
    reach(page, "runner-page")
    heading = page.evaluate(
        "document.querySelector('.runner-page h2')?.textContent || ''")
    assert heading, "the runner page rendered with no name in its heading"
    assert page.count(".runner-page .scope-chip") > 0, (
        "the runner page drew no scope chips")
    headers = page.evaluate(
        "Array.from(document.querySelectorAll('.runner-page .rank-breakdown th'))"
        ".map(e => e.textContent.trim())")
    assert "Their time" in headers and "Gap" in headers, headers
    # `.rank-entity-link` is the entity name's door into the Library (round
    # 1) -- a button that navigates, not one that edits -- so it is excluded.
    ignore_buttons = page.evaluate(
        "document.querySelectorAll('.runner-page .rank-breakdown tbody "
        "button:not(.rank-entity-link):not(.rank-row-play)').length")
    assert ignore_buttons == 0, (
        f"found {ignore_buttons} button(s) in the runner page's breakdown -- "
        "read-only means no Ignore/Include control")


def test_the_template_and_export_buttons_live_inside_the_timeline_box(page):
    """His report 2026-09-02: "the buttons ... are a bit silly, because the
    input timeline doesn't exist yet... so they should be hidden until the
    input timeline is available. Then they should be put at the bottom of the
    actual Inputs timeline box". They ride the timeline as its `tools` slot,
    so they cannot exist without it and they sit under the controller
    panel."""
    reach(page, "input-timeline")
    inside, after_panel = page.evaluate(
        "(() => { const t = document.querySelector('.input-timeline');"
        " const tools = t && t.querySelector('.attempt-drawer-tools');"
        " if (!tools) return [false, false];"
        " const panel = t.querySelector('.input-inspector');"
        " return [true, panel"
        "   ? tools.getBoundingClientRect().top >= panel.getBoundingClientRect().top"
        "   : false]; })()")
    assert inside, "the tools are not inside the input timeline box"
    assert after_panel, "the tools sit above the controller panel"
    # And nothing renders them a second time outside the timeline.
    stray = page.evaluate(
        "document.querySelectorAll('.attempt-drawer-tools').length")
    assert stray == 1, f"{stray} tool rows on the page"
