"""What uilab needs to know about SM64 Trainer. The whole per-project surface.

Everything that used to live in tools/cdp.py, tools/responsive_probe.js,
tools/responsive_sweep.py and tools/css_blocks.py is now in `uilab`, shared with
every other project on this machine and improved in one place. What stays here
is only what is TRUE OF THIS APP: how to boot it, where its stylesheet is, which
selectors are shell, what must never truncate, and which states are worth
measuring.

If this file grows past a screen, something generic has leaked into it.
"""
from __future__ import annotations

import dataclasses
import functools
import sys
from pathlib import Path

from uilab import Project, Story

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ui_fixture import FIXTURE_SEGMENT, serve_ui  # noqa: E402

REPO = Path(__file__).resolve().parents[1]

# The closed list of things a VIEWPORT media query may style: the shell.
# Everything else is component-internal and gates on @container against its own
# pane. The reason is measured, not stylistic — the sidebar is 206px wide above
# 1180px and a 76px rail below, so the pane a card lives in is NOT monotonic in
# window width (a 1181px window gives a card a 932px pane; a 1180px window gives
# it 1061px). No viewport threshold can express "this card is too narrow".
SHELL_SELECTORS = (
    "app-shell", "app-sidebar", "app-brand", "app-main", "app-notice",
    "nav-", "sidebar-", "mobile-", "workspace", "context-", "view-pane",
    "sheet-",
    # The sidebar's status block. Added 2026-07-28 after its absence caused a
    # real bug: the Wave 2 conversion read `.recording-status` as
    # component-internal and moved its rail rules into a @container query, but
    # the sidebar is not inside `.view-pane`, so the query could never match
    # and nothing styled the block at all -- the user's "the recording section
    # is clearly too big and the dot is not sized properly when the sidebar is
    # small". The test for "is this shell" is not what a name looks like, it is
    # whether the element lives inside a size container. Nothing in the sidebar
    # or the header does.
    "recording-", "connection-", "status-",
)

# Elements carrying irreducible information: a defect if they ellipsise.
#
# `.objective-name h2` is the star (or segment) being practised -- the one
# thing on that card that says WHAT you are doing, and it ellipsised mid-word
# at 850 and 900px ("Fall onto the Cag...", 2026-07-29). Note this catches only
# the star the FIXTURE seeds; the whole corpus is measured against the real
# column by tests/test_objective_name_fits.py, which is what actually holds the
# floor. Both, because they fail differently: this one catches a layout change
# on any page state, that one catches a name nobody thought about.
NEVER_TRUNCATE = (".rank-banner-kicker", ".context-label", ".nav-item span",
                  ".field-label", ".objective-name h2")

# States worth measuring. The Practice page renders EMPTY-state placeholders
# whenever no target is selected, and a database snapshot taken while nobody is
# playing has no target — so for a week every sweep measured placeholders and
# reported the page clean while 23 real defects sat behind a populated card.
# serve_ui() seeds a target for exactly that reason; these stories then scope
# the probes to the cards that matter.
# Collapsing every card is a LAYOUT the user asked to be held to, not a nicety:
# "we should evaluate what the actual UI looks like when vertical (like 900x1180
# when everything's collapsed)… we are ALWAYS testing our development process
# against both the horizontal design and the collapsed design" (2026-07-28).
# Nothing measured the collapsed page until these two stories existed.
#
# Both setups are IDEMPOTENT and order-independent: each clicks only the
# toggles that are in the wrong state, judged by `aria-expanded`. A setup that
# blindly clicked every toggle would invert the page when the previous story
# left it collapsed, and the sweep visits stories in a loop without reloading.
# `.card-collapse`'s persisted key (ui/components/collapsible.js::useCollapsed)
# is GLOBAL per card kind ("objective"/"analysis"/"attempts"), not scoped per
# star/segment -- fine while only the ONE primary section was ever mounted,
# but this project's `serve` now also arms a segment alongside the star
# target (below), so a second SegmentSection with the SAME three keys is
# mounted at once, inside the practice index. Clicking both copies of one key
# in the same forEach fires each instance's toggle against whatever the OTHER
# already wrote, which stops being idempotent (`tests/test_ui_collapse_
# story.py`, caught live 2026-07-29). Excluding index-item toggles avoids the
# collision entirely: they are not this project's `.is-collapsed` gate to
# begin with (that story only ever asserted on the ONE primary card), and
# their own useCollapsed state is a real, separate product question this
# project's file ownership does not cover (ui/components/collapsible.js).
# `Story.setup` is wrapped `() => { return (EXPR); }` when it starts with "("
# (uilab's PlaywrightPage.evaluate) -- an async IIFE therefore has its Promise
# AWAITED by Playwright automatically, which is what lets a setup click
# through a multi-step flow (open a tab, open a modal, wait for a fetch to
# land) as ONE atomic, ordered script instead of guessing a fixed delay.
# Required here because a `.click()` and the DOM read that follows it in the
# SAME script do NOT observe each other -- measured directly (2026-07-29):
# clicking a toggle and reading its `aria-expanded` in one evaluate() call
# still reports the PRE-click value; only a real await (a timer tick, letting
# Preact flush) sees the change.
_ASYNC_HELPERS = """
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const waitFor = async (pred, maxMs = 3000, stepMs = 40) => {
  const start = Date.now();
  while (Date.now() - start < maxMs) {
    if (pred()) return true;
    await sleep(stepMs);
  }
  return false;
};
"""


def _script(body: str) -> str:
    """One idempotent, awaited setup script, `_ASYNC_HELPERS` included."""
    return "(async () => {" + _ASYNC_HELPERS + body + "})()"


# `app.js`'s tabs UNMOUNT the page they leave (a ternary chain, not a
# display:none stack -- Compare is the one deliberate exception), so a story
# on the Segments tab leaves nothing of the Practice page behind. That is
# good for isolation and bad for the SWEEP LOOP: stories share one page
# across the whole matrix with no reload, so if the last story of a
# viewport's pass left the app on Segments, the NEXT viewport's "page" story
# would silently measure the Segments tab under the Practice page's name.
# Returning here, first, makes "page" self-healing regardless of what ran
# immediately before it.
_EXPAND_ALL = _script("""
const practiceBtn = document.querySelector('button.nav-item[title="Practice"]');
if (practiceBtn && practiceBtn.getAttribute('aria-current') !== 'page') {
  practiceBtn.click();
  await waitFor(() => !!document.querySelector('.objective-card, .objective-empty'));
}
document.querySelectorAll('.card-collapse[aria-expanded="false"]')
  .forEach((b) => { if (!b.closest('.practice-index-item')) b.click(); });
document.querySelectorAll('details.practice-index-item:not([open])')
  .forEach((d) => { d.open = true; });
""")
_COLLAPSE_ALL = """
document.querySelectorAll('.card-collapse[aria-expanded="true"]')
  .forEach((b) => { if (!b.closest('.practice-index-item')) b.click(); });
"""

# This branch's OWN surfaces (spec 2026-07-28-multi-step-segments) were never
# in this file at all until 2026-07-29 -- every one of `page`/`active-target`/
# `armed-segment`/`practice-log`/`page-collapsed` is Practice-page state
# inherited from main, and `ready_selector` never leaves it. So the recorder
# modal (segmenttimeline.js, three states, the feature this whole branch
# exists for), the segments editor (which now also carries the match-mode
# select, the lint panel, the backtest panel and the split/merge panels) and
# the split/merge panels themselves had been rendered by this gate exactly
# zero times, at any breakpoint, the whole time this branch has existed --
# `tests/test_fixture_reaches_the_real_page.py`'s own lesson, applied to a
# fourth instance of the same failure it names three of.
#
# `_seed_editor_fixtures()` (ui_fixture.py) POSTs two saved, byte-identical
# segments purpose-built for this: opening either one shows a REAL `duplicate`
# lint finding (a definition with no finding renders NO lint panel at all --
# `${lintFindings.length > 0 && ...}` -- so a quiet definition sweeps an
# invisible one) and a split panel (needs exactly one waypoint, which neither
# LBLJ nor any of the ten legacy tricks carries).
_EDITOR_SETUP = _script("""
const segBtn = document.querySelector('button.nav-item[title="Segments"]');
if (segBtn && segBtn.getAttribute('aria-current') !== 'page') {
  segBtn.click();
  await waitFor(() => !!document.querySelector('.segments-page'));
}
if (!document.querySelector('.segbuilder')) {
  const search = document.querySelector('.library-search');
  if (search) {
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype, 'value').set;
    setter.call(search, 'Editor Fixture A');
    search.dispatchEvent(new Event('input', { bubbles: true }));
    await waitFor(() => !!document.querySelector('.segment-row-main'));
  }
  const row = document.querySelector('.segment-row-main');
  if (row) { row.click(); await waitFor(() => !!document.querySelector('.segbuilder')); }
}
// The lint effect is DEBOUNCED 400ms from mount; waited for explicitly and
// BEFORE the backtest click below, or the two waits race and this one
// sometimes loses (measured: waiting only on the backtest panel left lint
// at 0 findings often enough to be the first thing this story's own
// mutation proof caught).
await waitFor(() => document.querySelectorAll('.lint-panel .lint-finding').length > 0, 1500);
const btBtn = Array.from(document.querySelectorAll('.builder-actions button'))
  .find((b) => b.textContent.includes('Try it against my history'));
if (btBtn && !document.querySelector('.backtest-panel')) {
  btBtn.click();
  await waitFor(() => !!document.querySelector('.backtest-panel'), 3000);
}
""")


def _recorder_setup(target_step: int) -> str:
    """Idempotent, order-independent: walks the "record a segment" modal
    (segmenttimeline.js) FORWARD from wherever it currently is to
    `target_step` (0 start / 1 end / 2 review), backing up to "start" first
    if it is already further along. Each forward click picks the FIRST
    currently-listed `.record-row` -- any row synthesizes into a segment
    (any level_changed/star_collected pair works), and picking the first
    (oldest) one at every step guarantees rows remain for the step after it;
    picking the LAST (most recent) one at "start" was tried and left nothing
    later to pick for "end" (measured, not assumed)."""
    return _script(f"""
const segBtn = document.querySelector('button.nav-item[title="Segments"]');
if (segBtn && segBtn.getAttribute('aria-current') !== 'page') {{
  segBtn.click();
  await waitFor(() => !!document.querySelector('.segments-page'));
}}
if (!document.querySelector('.record-steps')) {{
  const openBtn = Array.from(document.querySelectorAll('button'))
    .find((b) => b.textContent.includes('Record a segment'));
  if (openBtn) {{
    openBtn.click();
    await waitFor(() => !!document.querySelector('.record-steps'));
  }}
}}
const stepIndex = () => {{
  const el = document.querySelector('.record-step.on');
  if (!el) return -1;
  const label = el.textContent.trim();
  return label.startsWith('1') ? 0 : label.startsWith('2') ? 1 : 2;
}};
if (stepIndex() > {target_step}) {{
  // The FIRST `.record-picked` "Change" is always Start's -- one back-click
  // from either "end" or "review" returns to "start" (segmenttimeline.js's
  // own backTo("start") clears endRow too).
  const back = document.querySelector('.record-picked button');
  if (back) {{ back.click(); await waitFor(() => stepIndex() <= {target_step}); }}
}}
let guard = 0;   // bounded: at most two forward steps ever needed
while (stepIndex() >= 0 && stepIndex() < {target_step} && guard < 5) {{
  guard++;
  const before = stepIndex();
  await waitFor(() => document.querySelectorAll('.record-row').length > 0, 3000);
  const rows = document.querySelectorAll('.record-row');
  if (!rows.length) break;
  rows[0].click();
  await waitFor(() => stepIndex() !== before, 2000);
}}
""")


STORIES = [
    Story(name="page", at="", setup=_EXPAND_ALL),
    Story(name="active-target", at=".objective-card",
          skip_if="!document.querySelector('.objective-card')"),
    # `--objective-card-narrow` was measured against a STAR card and never
    # re-measured after this branch (2026-07-28-multi-step-segments) put a
    # `.seg-waiting` line inside the same fixed-height, overflow:hidden card
    # -- and neither this sweep nor tools/measure_objective_card.py could
    # even REACH a state with that line rendered (final review, finding 2):
    # `serve_ui`'s fixture always targeted a star, and `.seg-waiting` renders
    # only while `sec.armed_detail` is non-null. `serve` below arms a real
    # segment definition alongside the existing star target (ui_fixture.py's
    # `_arm_segment`) so this card exists on the SAME page the other stories
    # already measure. It sits inside a closed `<details>` in the practice
    # index until `_EXPAND_ALL` (above, already run by the "page" story
    # earlier in this viewport's pass) opens it.
    Story(name="armed-segment", at=".objective-card:has(.seg-waiting)",
          skip_if="!document.querySelector('.seg-waiting')"),
    Story(name="practice-log", at=".attempts-card",
          skip_if="!document.querySelector('.attempts-card')"),
    # Last of the PRACTICE-page stories, so it does not leave the page folded
    # for the ones above.
    Story(name="page-collapsed", at="", setup=_COLLAPSE_ALL,
          skip_if="!document.querySelector('.card-collapse')"),
    # The four SEGMENTS-tab stories below are last on purpose: `_EXPAND_ALL`
    # (the "page" story's own setup, which runs first on every viewport) is
    # what returns the app to Practice for the next pass, so nothing after
    # these needs to.
    # No `skip_if` on these four: unlike `.attempts-card`/`.seg-waiting`
    # (server-seeded state that may or may not exist before any setup runs),
    # reaching a segment/the recorder is ENTIRELY the setup's own job --
    # `skip_if` runs BEFORE `setup` (uilab's sweep loop), so gating on the
    # state setup is meant to create would skip every single time.
    Story(name="segments-editor", at=".segbuilder", setup=_EDITOR_SETUP),
    Story(name="recorder-start", at=".modal", setup=_recorder_setup(0)),
    Story(name="recorder-end", at=".modal", setup=_recorder_setup(1)),
    Story(name="recorder-review", at=".modal", setup=_recorder_setup(2)),
]

PROJECT = Project(
    # FIXTURE_SEGMENT (BitFS Pipe Entry, id 6) armed alongside the default
    # star target -- additive, see ui_fixture.py::_arm_segment. It is one of
    # the ten legacy tricks the schema MIGRATION itself inserts, so it exists
    # in this empty, deterministic fixture with no defaults-corpus reconcile
    # (that only runs from main.py). Picked over LBLJ (id 1) specifically for
    # its four bundled strategies -- see FIXTURE_SEGMENT's own comment: LBLJ
    # has exactly one, so its strategy ladder IS its best ladder and the
    # armed-segment card drew a single combined rank banner instead of two.
    # `seed_editor_fixtures=True` additionally seeds the two segments the
    # segments-editor story opens (see `_EDITOR_SETUP` above).
    serve=functools.partial(serve_ui, arm_segment=FIXTURE_SEGMENT,
                            seed_editor_fixtures=True),
    page_path="/ui/index.html",
    # The shell paints before the session view lands, and a sweep that starts
    # measuring at that moment reports a page with none of its content on it --
    # and calls it clean. `.objective-card` exists only once the view has
    # rendered, so it is the honest "ready" signal.
    ready_selector=".objective-card",
    stylesheet=REPO / "src" / "sm64_events" / "ui" / "index.html",
    shell_selectors=SHELL_SELECTORS,
    never_truncate=NEVER_TRUNCATE,
    # A collapsed card hides its content BY DEFINITION -- that is the feature,
    # not a defect. Without this the collapsed sweep reported 108 clipping
    # "defects", every one of them correct behaviour, which is exactly the
    # noise that gets a probe exemption-listed into uselessness.
    may_clip=(".practice-card.is-collapsed",),
    stories=STORIES,
    # The narrowest SUPPORTED width (user's call, 2026-07-29: "the minimum
    # officially supported width we should support is 850px. Height can be any
    # height, that's fine"). Widths below it leave the matrix.
    #
    # This is only legitimate because the shipped app ENFORCES it -- the
    # desktop window's `min_size`, its default geometry and a clamp on restored
    # geometry all use `desktop/window.py::MIN_WINDOW_WIDTH`, which is this
    # number. A floor the product does not hold would just hide defects, and
    # `tests/test_min_supported_width.py` fails if the two ever disagree.
    #
    # What it costs, stated rather than buried: the WCAG 1.4.10 reflow probe at
    # 320px stops running, and the mobile shell under `@media (max-width:
    # 760px)` -- bottom nav bar, appbar, the "More" sheet -- is no longer
    # measured at all. That code still ships. Deleting it is a separate
    # decision nobody has made.
    min_viewport_width=850,
    # Sizes that earn a place regardless of what the stylesheet declares: the
    # supported floor and one pixel above it, the width the user reported, the
    # workspace's max width, and a short window.
    #
    # 912/913 are BOTH SIDES of the `@container (max-width: 793px)` tight band,
    # and they have to be listed by hand because the matrix derives its probe
    # points in VIEWPORT pixels while that threshold is in CONTAINER pixels.
    # Measured on the shipping shell, the pane runs 119px narrower than the
    # window in this range (850 -> 731, 910 -> 791), so a 793px container
    # threshold flips at a 912px WINDOW -- while the derived points sit at 793
    # and 794, below the supported floor, where they are dropped entirely. A
    # container threshold is therefore never self-probing here; whenever you
    # add one below ~1180, add its window equivalent to this list.
    extra_viewports=((850, 1180), (851, 1000), (900, 1180), (912, 1000),
                     (913, 1000), (1500, 900), (1280, 720)),
    # OWED, not exempted. These became VISIBLE on 2026-07-28 when the
    # fixture finally rendered a populated practice page -- a stage, an
    # active target, a strategy and a PB. Everything on the star row and
    # the stage banner only exists once a course is loaded, so no earlier
    # sweep could see any of it. uilab's stale-exemption gate deletes each
    # row the moment its defect stops occurring.
    known_defects={
        '1060x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 228 > clientHeight 226',
        '1060x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1061x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 228 > clientHeight 226',
        '1061x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1100x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1100x1000 [page] clipped :: span.starname':
            'scrollHeight 29 > clientHeight 27',
        '1100x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1101x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1101x1000 [page] clipped :: span.starname':
            'scrollHeight 29 > clientHeight 27',
        '1101x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1180x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1180x1000 [page] clipped :: span.starname':
            'scrollHeight 29 > clientHeight 27',
        '1180x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1181x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 227 > clientHeight 225',
        '1181x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1250x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1250x1000 [page] clipped :: span.starname':
            'scrollHeight 29 > clientHeight 27',
        '1250x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1251x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1251x1000 [page] clipped :: span.starname':
            'scrollHeight 29 > clientHeight 27',
        '1251x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1280x720 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1280x720 [page] clipped :: span.starname':
            'scrollHeight 29 > clientHeight 27',
        '1280x720 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1400x760 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1400x760 [page] clipped :: span.starname':
            'scrollHeight 29 > clientHeight 27',
        '1400x760 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1400x761 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1400x761 [page] clipped :: span.starname':
            'scrollHeight 29 > clientHeight 27',
        '1400x761 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1500x900 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1500x900 [page] clipped :: span.starname':
            'scrollHeight 29 > clientHeight 27',
        '1500x900 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1920x1080 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1920x1080 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '850x1180 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 199 > clientHeight 197',
        '850x1180 [page] clipped :: span.starname':
            'scrollHeight 22 > clientHeight 20',
        '850x1180 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '851x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 199 > clientHeight 197',
        '851x1000 [page] clipped :: span.starname':
            'scrollHeight 22 > clientHeight 20',
        '851x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '900x1180 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 206 > clientHeight 204',
        '900x1180 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '912x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 207 > clientHeight 205',
        '912x1000 [page] clipped :: span.starname':
            'scrollHeight 24 > clientHeight 22',
        '912x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '913x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 208 > clientHeight 206',
        '913x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
    },
)


# --- The Bowser banner row (task-bowser-sweep) ------------------------------
# stagebanner.js dispatches on stage.mode via STAGE_ROWS, and none of the
# stories above ever puts the stage in "bowser_course" -- so BowserCourseRow
# (three cells since 912466d rewrote it from two: the "reds" 8-coin star plus
# BOTH BitDW pipe segments, each now a full StandardSegmentCell with its own
# name, rank and strategy sub-line, and the longest name in the app, "BitDW —
# 8 Red Coins → Pipe") had never been rendered by this gate, at any
# breakpoint, before OR after that rewrite.
#
# `stage` is SERVER state, seeded once when the fixture starts
# (ui_fixture.py::serve_ui(bowser_stage=...)). All of PROJECT's stories share
# ONE page and ONE server, and `Story` carries only name/at/setup — no story
# above can move the app into bowser_course mid-sweep. uilab's sweep fixture
# reads `uilab_project` off the TEST MODULE, though (pytest_plugin.py), so a
# second test module gets its OWN sweep with its OWN Project — that is the
# seam, and no uilab change is needed.
#
# BitDW is course 16 / level 17 (memory/addresses.py's COURSE_BY_LEVEL,
# `17: 16` — confirmed against detectors/stage.py's own docstring, which
# names the identical course/level pair for BitDW). Picked over BitFS/BitS
# arbitrarily; all three render through the same BowserCourseRow.
BOWSER_COURSE = 16
BOWSER_LEVEL = 17

BOWSER_STORIES = [
    # `page` FIRST, mirroring PROJECT's own story list, and for the same
    # reason: uilab's probe scopes its DOM walk to `root.querySelectorAll("*")`
    # (uilab/probes.js), which never includes the scope root ITSELF — only
    # its descendants. A story scoped to `.stagebanner` (below) therefore
    # cannot see `.stagebanner`'s OWN clipping (measured directly, 2026-07-29:
    # scrollHeight 230 > clientHeight 228 at 1100x1000 — the identical
    # fixed-height-card shortfall PROJECT's own known_defects already owes for
    # the star row, now also true of this row, invisible to "bowser-row"
    # alone). Without this story that would be a SECOND blind spot introduced
    # by this very task, in exactly the shape CLAUDE.md warns about ("reaching
    # the card is not the same as reaching its content").
    Story(name="page", at="", setup=_EXPAND_ALL),
    # No setup needed for this one: the app's default tab is Practice
    # (app.js), the fixture seeds the stage server-side before the page ever
    # loads, and StageBanner is mounted unconditionally at the top of
    # .practice-page — not inside a collapsible <details>, unlike the
    # practice index below it. Scoped to the row itself so probes and
    # screenshots target it rather than the whole page.
    Story(name="bowser-row", at=".stagebanner"),
]

# dataclasses.replace, not a second hand-written Project: the shell selector
# list, may_bleed, the viewport matrix and min_viewport_width are the LAW
# (`.claude/rules/ui-core.md`), not this project's to restate — a second
# hand-authored config is exactly the second door CLAUDE.md's "one door" rule
# exists to prevent for values that must stay in lockstep.
BOWSER_PROJECT = dataclasses.replace(
    PROJECT,
    serve=functools.partial(serve_ui, reconcile_full_corpus=True,
                            bowser_stage=(BOWSER_COURSE, BOWSER_LEVEL)),
    stories=BOWSER_STORIES,
    # Its own list, not PROJECT's: even though every row below is the SAME
    # underlying, already-owed defect class PROJECT's own known_defects
    # tracks for the star row (the `.stagebanner` card's own fixed-height
    # shortfall, and `.starcell`'s starholder/starrank overlap — identical
    # detail strings, same widths, same component, just never measured on
    # THIS row before), the keys are story-scoped ("[page]"/"[bowser-row]")
    # and this project's own stories produce a different set than PROJECT's
    # -- inheriting PROJECT's dict verbatim would fail
    # test_the_known_defect_list_does_not_outlive_its_defects on the first
    # run (its ~40 rows are keyed against selectors/stories this project's
    # own sweep also produces, but not identically -- e.g. PROJECT has no
    # "[bowser-row]" story and this project's "[page]" story never sees a
    # 7-star StarRow, so `span.starname` clips at slightly different widths).
    # Every row below was taken VERBATIM from this project's own sweep
    # output (task-bowser-sweep), not hand-typed, to guarantee the keys
    # match byte-for-byte. RE-DERIVED 2026-07-30 (spec 2026-07-28-multi-
    # step-segments, "the Bowser Reds star/pipe toggle"): the row went from
    # three cells to two, which dropped the `span.starname` clipping class
    # entirely (24 rows) and left the other two classes' own numbers
    # unchanged (measured, not assumed -- both were re-run against the
    # sweep's own output, not edited by hand).
    known_defects={
        # `.stagebanner`'s own fixed-height card shortfall -- PROJECT's
        # identical, long-owed "scrollHeight > clientHeight" defect for the
        # SAME card, now visible on this row too now that anything ever
        # measures it. Only the "page" story can see it: uilab's probe never
        # checks its OWN scope root (root.querySelectorAll("*") excludes
        # root), so "bowser-row" (at=".stagebanner") structurally cannot.
        '1060x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 228 > clientHeight 226',
        '1061x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 228 > clientHeight 226',
        '1100x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1101x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1180x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1181x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 227 > clientHeight 225',
        '1250x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1251x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1280x720 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1400x760 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1400x761 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1500x900 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1920x1080 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '850x1180 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 199 > clientHeight 197',
        '851x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 199 > clientHeight 197',
        '900x1180 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 206 > clientHeight 204',
        '912x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 207 > clientHeight 205',
        '913x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 208 > clientHeight 206',

        # `span.starname` clipping is GONE (re-derived 2026-07-30, spec
        # 2026-07-28-multi-step-segments, "the Bowser Reds star/pipe
        # toggle"): the row dropped from three cells to two -- "Reds" (which
        # now carries the star/pipe toggle instead of the longest name in the
        # app, "BitDW — 8 Red Coins → Pipe") and "No Reds" -- so each
        # remaining cell gets more width per cell at every viewport and
        # neither name clips any more. Removing this task's own toggle later
        # without re-running the sweep would leave this comment as the only
        # record that the class existed at all.
        #
        # `.starholder` x `.starrank` overlap inside `.starcell` -- PROJECT's
        # identical, long-owed 7x2px overlap, at every viewport a cell
        # renders at all (both cells on this row share it; the probe
        # reports one row per colliding SELECTOR pair, not per cell).
        '1060x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1060x1000 [bowser-row] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1061x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1061x1000 [bowser-row] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1100x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1100x1000 [bowser-row] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1101x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1101x1000 [bowser-row] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1180x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1180x1000 [bowser-row] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1181x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1181x1000 [bowser-row] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1250x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1250x1000 [bowser-row] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1251x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1251x1000 [bowser-row] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1280x720 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1280x720 [bowser-row] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1400x760 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1400x760 [bowser-row] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1400x761 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1400x761 [bowser-row] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1500x900 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1500x900 [bowser-row] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1920x1080 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1920x1080 [bowser-row] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '850x1180 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '850x1180 [bowser-row] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '851x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '851x1000 [bowser-row] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '900x1180 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '900x1180 [bowser-row] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '912x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '912x1000 [bowser-row] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '913x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '913x1000 [bowser-row] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
    },
)
