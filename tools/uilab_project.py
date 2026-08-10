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
# `.log-card-name b` and `.rank-banner-name` -- the practice log's own entity
# name and rank name, which ellipsised on `.log-card-head` below an 858-866px
# container (index.html's own measured comment, above the `@container
# (max-width: 900px)` reflow). This row catches a layout regression on any
# seeded page state; the whole corpus is measured against the real column by
# tests/test_log_card_name_fits.py, which is what catches a real name nobody
# thought about. Both, because they fail differently.
#
# `.objective-name h2` -- the Active Target card's own version of this same
# concern -- was here until amendment A8 (spec practice-log-entity-cards,
# 2026-08-04) deleted that card; `.objective-name h2` never renders on the
# page this rig measures any more (it survives only inside ui/tune.js's climb-
# inspector replica, which PROJECT does not exercise), and its own corpus
# check (test_objective_name_fits.py) is deleted with it -- a direct
# duplicate of test_log_card_name_fits.py's own method once both cards shared
# nothing left to test differently.
NEVER_TRUNCATE = (".rank-banner-kicker", ".context-label", ".nav-item span",
                  ".field-label", ".log-card-name b",
                  ".rank-banner-name")

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
  await waitFor(() => !!document.querySelector('.log-list-card'));
}
document.querySelectorAll('.card-collapse[aria-expanded="false"]')
  .forEach((b) => b.click());
document.querySelectorAll('.log-card-fold[aria-expanded="false"]')
  .forEach((b) => b.click());
""")
# `.log-card-fold` (Task 7) is a SEPARATE mechanism from `.card-collapse`
# above -- LogCard's own fold is PracticeLog's own state (auto-open-newest,
# 2026-08-04: only the newest entity's card opens on the system's own
# initiative, everything else defaults closed until a click says otherwise),
# never the shared `useCollapsed`/localStorage class -- so the two `.is-
# collapsed`/`.is-closed` layouts need their own selector even though both
# read `aria-expanded` the same way. `_EXPAND_ALL`/`_COLLAPSE_ALL` reach their
# state by clicking whatever is currently open/closed rather than assuming a
# starting point, so neither depends on what a fresh page happens to default
# to. Before this, no story ever toggled a log card's fold, so its
# `.is-closed` layout was completely unswept: this is the fix, not a new
# Story, since reaching it needs no new page state, only a control this sweep
# already knows how to drive by the same attribute.
_COLLAPSE_ALL = """
document.querySelectorAll('.card-collapse[aria-expanded="true"]')
  .forEach((b) => b.click());
document.querySelectorAll('.log-card-fold[aria-expanded="true"]')
  .forEach((b) => b.click());
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
  // Scoped to `.segments-page`, not a bare `.library-search` -- the Library
  // tab (Task 4, spec 2026-08-07-library-page) reuses that same generic
  // class for its OWN search box and stays mounted with `display:none` when
  // you leave it (library.js's own docstring: "the same display:none trick
  // Compare uses"), so an unscoped query silently grabs the HIDDEN Library
  // box instead of this tab's real one whenever the "library-target" story
  // ran first in the same sweep pass. Measured: `document.querySelectorAll(
  // '.library-search').length` was 2 on the Segments page after that
  // sequence, and the first (DOM-order) match was the Library tab's own,
  // stuck at whatever text it last held -- so nothing here ever typed into
  // the real box, `.segment-row-main` never appeared, and `.segbuilder`
  // never opened. Caught by `RuntimeError: uilab story 'segments-editor'...
  // scope selector matched nothing: .segbuilder`, not by a wrong screenshot.
  const search = document.querySelector('.segments-page .library-search');
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


def _recorder_setup(picks: int) -> str:
    """Idempotent, order-independent: opens the RECORDER (segmenttimeline.js)
    and leaves exactly `picks` moments selected.

    Rewritten 2026-08-05 with the surface itself. The old script walked a
    three-state stepper (start -> end -> review) and read its position off
    `.record-step.on`; there are no steps now -- one list, a selection of any
    size, and the review appears at two. So the position IS the number of
    picked rows, which is what `.record-row.picked` counts.

    Picks the OLDEST rows (the list is drawn newest-first, so the LAST DOM
    rows) for the same reason the old script picked the first ones: the
    synthesize call refuses a pair that is one event, and any
    level_changed/star_collected pair the fixture holds synthesizes fine.
    """
    return _script(f"""
const segBtn = document.querySelector('button.nav-item[title="Segments"]');
if (segBtn && segBtn.getAttribute('aria-current') !== 'page') {{
  segBtn.click();
  await waitFor(() => !!document.querySelector('.segments-page'));
}}
if (!document.querySelector('.record-rows, .record-picks')) {{
  const openBtn = Array.from(document.querySelectorAll('button'))
    .find((b) => b.textContent.includes('Record a segment'));
  if (openBtn) {{
    openBtn.click();
    await waitFor(() => !!document.querySelector('.record-picks'));
  }}
}}
const picked = () => document.querySelectorAll('.record-row.picked').length;
while (picked() > {picks}) {{
  const clear = Array.from(document.querySelectorAll('.record-picks button'))
    .find((b) => b.textContent.includes('Clear'));
  if (!clear) break;
  clear.click();
  await waitFor(() => picked() === 0, 2000);
}}
let guard = 0;   // bounded: never more than a handful of picks is measured
while (picked() < {picks} && guard < 6) {{
  guard++;
  await waitFor(() => document.querySelectorAll('.record-row').length > 0, 3000);
  const rows = Array.from(document.querySelectorAll('.record-row'))
    .filter((row) => !row.classList.contains('picked'));
  if (!rows.length) break;
  const before = picked();
  rows[rows.length - 1].click();          // oldest first
  await waitFor(() => picked() !== before, 2000);
}}
if ({picks} >= 2) {{
  // The review is fetched (synthesize -> backtest -> lint), so a story that
  // does not wait measures "Working it out…" instead of the layout it is
  // named for.
  await waitFor(() => !!document.querySelector('.record-review'), 4000);
  await waitFor(() => {{
    const panel = document.querySelector('.record-review');
    return panel && !panel.textContent.includes('Testing against your history');
  }}, 6000);
}}
""")


# The Library tab's target page (Task 4, spec 2026-08-07-library-page) --
# OWED to this task by the Task 3 reviewer: `.library-target` had no story at
# all while the page was a placeholder (a story for a placeholder would be
# thrown away), and Task 4 makes it real content. `serve_ui`'s default seeding
# already sets `lastPracticed` onto star:2:4 (FIXTURE_STAR), so clicking the
# tab is the whole setup -- no course-grid navigation needed, same as how
# `test_ui_library_nav.py::test_auto_open_lands_on_the_last_practiced_target`
# reaches it.
_LIBRARY_TARGET_SETUP = _script("""
const libBtn = document.querySelector('button.nav-item[title="Library"]');
if (libBtn && libBtn.getAttribute('aria-current') !== 'page') {
  libBtn.click();
}
await waitFor(() => !!document.querySelector('.library-target .library-section'));
""")

# The tray and the grid overlay (Task 5, spec 2026-08-07-library-page).
# Shares its navigation with `_LIBRARY_TARGET_SETUP` above and its own
# populate-the-tray step with tests/test_ui_library_tray.py's own
# `_ADD_N_EXAMPLES` helper -- the SAME "click the open section's first two
# enabled + buttons" mechanism, so a story and its test can never disagree
# about how the tray gets populated.
_LIBRARY_NAV = """
const libBtn = document.querySelector('button.nav-item[title="Library"]');
if (libBtn && libBtn.getAttribute('aria-current') !== 'page') {
  libBtn.click();
}
await waitFor(() => !!document.querySelector('.library-target .library-section'));
"""
_LIBRARY_ADD_TWO = """
if (document.querySelectorAll('.library-tray-chip').length < 2) {
  // Subdivision groups ship collapsed (round 1, 2026-08-07): expand them all
  // first, or the card query below finds nothing.
  await waitFor(() => !!document.querySelector(
    '.library-section.open .library-division-head'));
  Array.from(document.querySelectorAll(
    '.library-section.open .library-division-head')).forEach((head) =>
    head.getAttribute('aria-expanded') === 'true' || head.click());
  await waitFor(() => !!document.querySelector(
    '.library-section.open .library-example'));
  const cards = Array.from(document.querySelectorAll(
    '.library-section.open .library-example'));
  let added = 0;
  for (const card of cards) {
    if (added >= 2) break;
    const btn = card.querySelector('.library-example-plus');
    if (btn && !btn.disabled) { btn.click(); added++; }
  }
  await waitFor(() => document.querySelectorAll('.library-tray-chip').length >= 2);
}
"""
_LIBRARY_TRAY_SETUP = _script(_LIBRARY_NAV + _LIBRARY_ADD_TWO)
# Play all, on top of the tray setup -- `.library-grid-panel` rather than the
# inner `.library-grid` so the sheet also shows the honesty line and the
# restart/close header, not just the tile grid.
_LIBRARY_GRID_SETUP = _script(_LIBRARY_NAV + _LIBRARY_ADD_TWO + """
if (!document.querySelector('.library-grid')) {
  document.querySelector('.library-tray-playall').click();
  await waitFor(() => !!document.querySelector('.library-grid iframe'));
}
""")

STORIES = [
    Story(name="page", at="", setup=_EXPAND_ALL),
    # Re-pointed 2026-08-04 (amendment A8, spec practice-log-entity-cards):
    # the Active Target card is DELETED -- ".objective-card" never renders on
    # the real practice page any more, so `skip_if` here would have started
    # returning true on EVERY run, forever, the exact "a skip's stated reason
    # is a claim" trap this file's own history has already fallen into twice
    # (see the "armed-segment" and "practice-log" stories' comments below).
    # The entity actually being practised now gets the SAME highlight this
    # story exists to probe (`.log-card-active`, LogCard's own class), so
    # this is a repoint rather than a deletion -- the crowded content this
    # story was written for (rank banners + PB + fold, all in one head row)
    # still exists, just inside the log's own card.
    Story(name="active-target", at=".log-card.log-card-active",
          skip_if="!document.querySelector('.log-card.log-card-active')"),
    # Re-pointed 2026-08-03 (final whole-branch review, spec practice-log-
    # entity-cards): `.seg-waiting` moved off `.objective-card` entirely once
    # the practice index was deleted (Task 6) -- an armed-but-untargeted
    # segment now surfaces ONLY as a `.log-card` in the practice log
    # (`ui_fixture.py::_arm_segment`'s own docstring), never as
    # `.objective-card`, since `Practice()` suppresses every segment pin while
    # a star target is active and `serve` below never targets the armed
    # segment. The old `at=".objective-card:has(.seg-waiting)"` therefore
    # matched nothing here; `skip_if` only ever checked for a bare
    # `.seg-waiting`, which DOES exist (inside the log card), so the story
    # never skipped and uilab's `_probe` returned "scope selector matched
    # nothing" on every single run since Task 6 landed -- silently discarded
    # by `continue` in uilab's own sweep loop (fixed at the root in
    # uilab/sweep.py, which now raises instead). CONFIRMED contributing zero
    # defects at every viewport for that whole stretch (final review).
    #
    # Re-pointed AGAIN 2026-08-06, and for the fourth time on this one story
    # the reason is that its selector stopped matching: the waiting-for row is
    # deleted (Griffin, "we should just remove the step indicator entirely
    # from the display here"), so `.seg-waiting` no longer exists anywhere on
    # the practice page and BOTH the `at` and the `skip_if` would have been
    # satisfied by nothing -- this time skipping silently rather than erroring,
    # which is the worse of the two failures and exactly what this file's own
    # history keeps warning about.
    # What survives of the crowded combination it exists to protect is the
    # TWO-LADDER card (two stacked rank banners in one head), which is the
    # same `_arm_segment` seeding drawn by `LogCard` -- so the story follows
    # that class instead of the row that used to sit under it.
    Story(name="armed-segment", at=".log-card.log-card-two-ladder",
          skip_if="!document.querySelector('.log-card.log-card-two-ladder')"),
    # Renamed 2026-08-03 (Task 7): `.attempts-card` died with StarSection/
    # SegmentSection's own attempts table (Task 6) -- the practice log is
    # now the page-level `.log-list-card` (practicelog.js). The old selector
    # never matched anything, so this story silently skipped every run since
    # Task 6 landed, which is the exact "a skip's stated reason is a claim"
    # trap this file's own history warns about, a third time over.
    Story(name="practice-log", at=".log-list-card",
          skip_if="!document.querySelector('.log-list-card')"),
    # Last of the PRACTICE-page stories, so it does not leave the page folded
    # for the ones above.
    Story(name="page-collapsed", at="", setup=_COLLAPSE_ALL,
          skip_if="!document.querySelector('.card-collapse')"),
    # Neither Practice-page nor Segments-tab state -- sits between the two
    # groups. `_EXPAND_ALL` (the "page" story, first on every viewport pass)
    # returns the app to Practice regardless of what the PREVIOUS viewport's
    # last story left it on, so nothing after this needs to clean up either.
    Story(name="library-target", at=".library-target", setup=_LIBRARY_TARGET_SETUP),
    Story(name="library-tray", at=".library-tray", setup=_LIBRARY_TRAY_SETUP),
    Story(name="library-grid", at=".library-grid-panel", setup=_LIBRARY_GRID_SETUP),
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
    Story(name="recorder-open", at=".modal", setup=_recorder_setup(0)),
    Story(name="recorder-review", at=".modal", setup=_recorder_setup(2)),
    # THREE moments, which is the shape that only exists since 2026-08-05:
    # the middle one is a waypoint the PERSON picked, so this is the state
    # where the review grows a "Then:" line per stop and drops the derived
    # step picker entirely.
    Story(name="recorder-waypoints", at=".modal", setup=_recorder_setup(3)),
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
    # and calls it clean. Re-pointed from the deleted `.objective-card`
    # (amendment A8, spec practice-log-entity-cards) to `.log-list-card` --
    # `PracticeLog` renders that wrapper unconditionally the moment `v`
    # exists (practice.js's early "still loading" return is the only gate),
    # so it exists only once the view has genuinely rendered, same honesty
    # the old selector had.
    ready_selector=".log-list-card",
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
    #
    # 1019/1020 are the same shape for `.log-card-head`'s own `@container
    # (max-width: 900px)` reflow (final review, item 1): measured on the
    # shipping shell, the pane runs the SAME 119px narrower than the window
    # in this range (900 -> container 781, 1019 -> container 900, 1020 ->
    # container 901 -- confirmed directly, `.log-card-head`'s own
    # `gridTemplateColumns` switches from the 2-column reflow to the 4-column
    # wide template between these two exact window widths), so the derived
    # matrix's own 900/901 VIEWPORT points land at a ~781px container deep
    # inside the narrow layout and never exercise the actual crossover.
    #
    # 979/980 are the same shape again, for the log card's own rank-display
    # narrow/wide split, `@container (max-width: 860px)` against
    # `.log-card-ranks .rank-banner` (2026-08-04, layout-matrix round): the
    # SAME ~119px window-to-container offset holds in this range (975 ->
    # container 856, 980 -> container 861), and 979/980 is the exact window
    # pair where a two-ladder card's shape (read off `.rank-banner`'s own
    # `grid-template-areas`) flips from Column to stackedRow -- confirmed
    # directly, not estimated from the offset alone. The derived matrix's own
    # literal-860/861 VIEWPORT points are a coincidence of this threshold's
    # chosen number, not a probe of this crossover; they land relatively
    # narrow in real container terms (~741-742px, comfortably inside the
    # Column band already) and happen to have surfaced an unrelated,
    # already-owed stagebanner defect instead (see known_defects, below).
    extra_viewports=((850, 1180), (851, 1000), (900, 1180), (912, 1000),
                     (913, 1000), (1019, 1000), (1020, 1000),
                     (979, 1000), (980, 1000),
                     (1500, 900), (1280, 720)),
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
        '1100x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1101x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1101x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1180x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1180x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1181x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 227 > clientHeight 225',
        '1181x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1250x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1250x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1251x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1251x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1280x720 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1280x720 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1400x760 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1400x760 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1400x761 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1400x761 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1500x900 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1500x900 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1920x1080 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1920x1080 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '850x1180 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 199 > clientHeight 197',
        '850x1180 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '851x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 199 > clientHeight 197',
        '851x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        # 860/861 are BOTH SIDES of `.log-card-ranks`' own new narrow/wide
        # `@container (max-width: 860px)` split (2026-08-04, layout-matrix
        # round) -- the matrix derives its probe points from every declared
        # threshold in the stylesheet, so this pair is new PURELY because
        # nothing named 860 before. The defect itself is this same
        # already-owed stagebanner/starcell class (see every other entry in
        # this dict), unrelated to the log card: confirmed by diffing this
        # round's index.html against HEAD restricted to every stagebanner/
        # starcell/starholder/starrank/starname selector -- byte-identical,
        # zero lines changed -- and by direct measurement that the SAME
        # ~2px stagebanner shortfall recurs at literally every width from
        # 859px through 900px tried by hand, not just these two.
        '860x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 200 > clientHeight 198',
        '860x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '861x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 200 > clientHeight 198',
        '861x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        # 979/980 are the REAL window-pixel crossover for the SAME log-card
        # threshold (extra_viewports' own comment above has the measurement);
        # same already-owed defect class again.
        '979x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 217 > clientHeight 215',
        '979x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '980x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 217 > clientHeight 215',
        '980x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '900x1180 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 206 > clientHeight 204',
        '900x1180 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '912x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 207 > clientHeight 205',
        '912x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '913x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 208 > clientHeight 206',
        '913x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        # 900x1000/901x1000/1019x1000/1020x1000: the SAME long-owed stage-
        # banner-card shortfall + star-cell overlap class as every row above
        # (both pre-existing, both unrelated to the practice log), newly
        # REACHED at these four exact points only because `.log-card-head`'s
        # own `@container (max-width: 900px)` (item 1, final review) added
        # 900 as a css threshold value -- `_candidate_matrix` auto-derives a
        # probe point at that literal VIEWPORT width whenever any stylesheet
        # rule declares it, and 1019/1020 are this same threshold's window-
        # equivalent pair (see `extra_viewports`, above). Not a new defect;
        # a new coordinate on an old one.
        '900x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 206 > clientHeight 204',
        '900x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '901x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 206 > clientHeight 204',
        '901x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1019x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 222 > clientHeight 220',
        '1019x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '1020x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 222 > clientHeight 220',
        '1020x1000 [page] overlap :: span.starholder x span.starrank':
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

        # 900x1000/901x1000/1019x1000/1020x1000: PROJECT's own new rows
        # (final review, item 1 -- `.log-card-head`'s `@container (max-width:
        # 900px)` reflow adds 900 as a css threshold, auto-deriving these
        # four VIEWPORT points for every project that shares this stylesheet,
        # BOWSER_PROJECT included) reached the SAME long-owed stagebanner/
        # starcell defect class here too, at a genuinely different magnitude:
        # this row's own 2-cell BowserCourseRow compresses further than the
        # 7-cell StarRow does at these widths, so the wing-spill overlap is
        # 26x2px here, not 7x2px. Measured directly against this project's
        # own sweep output, not copied from PROJECT's rows.
        '900x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 206 > clientHeight 204',
        '900x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '900x1000 [bowser-row] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '901x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 206 > clientHeight 204',
        '901x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '901x1000 [bowser-row] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1019x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 222 > clientHeight 220',
        '1019x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1019x1000 [bowser-row] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1020x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 222 > clientHeight 220',
        '1020x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1020x1000 [bowser-row] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',

        # 860x1000/861x1000/979x1000/980x1000: PROJECT's own new rows again
        # (2026-08-04, layout-matrix round -- the log card's rank-display
        # `@container (max-width: 860px)` split; see PROJECT's own
        # `extra_viewports` comment for the full measurement), same shape as
        # the 900/901/1019/1020 block just above: the SAME long-owed defect
        # class, at this row's own 26x2px overlap magnitude rather than
        # PROJECT's 7x2px. Measured directly against this project's own
        # sweep output.
        '860x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 200 > clientHeight 198',
        '860x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '860x1000 [bowser-row] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '861x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 200 > clientHeight 198',
        '861x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '861x1000 [bowser-row] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '979x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 217 > clientHeight 215',
        '979x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '979x1000 [bowser-row] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '980x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 217 > clientHeight 215',
        '980x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '980x1000 [bowser-row] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
    },
)


# -- the SELECTOR'S SUBSECTION BADGES (task 0087; round 22, 2026-08-08) -------
#
# Nothing in the shipped corpus carries a `parent`, so a star with pieces was
# unreachable by every instrument until `ui_fixture.seed_subsections` existed
# -- and an unreachable state is one no gate is looking at. Three real defects
# lived in it, all invisible to the node-driven rule tests and all obvious in
# the first render: the STAR row had no subsection wiring at all (the primary
# case), a selected subsection collapsed the row to a single cell with no way
# back, and `arm_level` could not place a moment, so a subsection defined by
# one appeared in no row anywhere.
#
# The two stories were `selector-expanded`/`selector-folded` until round 22
# replaced progressive disclosure with badges. There is no expanded row to
# measure now, and the states worth measuring are the two the BADGES have --
# every piece tracked, and one of them dimmed off.
_TOGGLE_A_PIECE_OFF = _script("""
const badge = document.querySelector('.stagebanner .cell-toggle-btn.is-selected');
if (badge) {
  badge.click();
  await waitFor(() => !document.querySelector(
    '.stagebanner .cell-toggle-btn.is-selected:first-of-type'));
}
""")

SUBSECTION_STORIES = [
    # `page` first, for the reason BOWSER_STORIES states: uilab's probe never
    # checks its own scope root, so a story scoped to `.stagebanner` cannot
    # see `.stagebanner`'s OWN clipping.
    Story(name="page", at="", setup=_EXPAND_ALL),
    Story(name="selector-pieces-on", at=".stagebanner"),
    Story(name="selector-piece-off", at=".stagebanner",
          setup=_TOGGLE_A_PIECE_OFF),
]

SUBSECTION_PROJECT = dataclasses.replace(
    PROJECT,
    serve=functools.partial(serve_ui, seed_subsections=True),
    stories=SUBSECTION_STORIES,
    # Its own list, story-scoped keys, taken VERBATIM from this project's own
    # sweep output rather than hand-typed -- see BOWSER_PROJECT's note. Every
    # row is one of the SAME two long-owed classes PROJECT and BOWSER_PROJECT
    # already exempt (the `.stagebanner` card's own fixed-height shortfall and
    # `.starcell`'s starholder/starrank overlap), classified rather than
    # assumed: 26 clipped + 78 overlap, and nothing else. So the expanded row
    # and its child treatment introduce no defect of their own.
    #
    # RE-TAKEN 2026-08-06 after merging main, which deleted the Active Target
    # card: every fixed-height shortfall in the selector shifted by two pixels
    # and eight viewports joined the matrix, so all 104 keys moved at once.
    # Re-taken by running the sweep and classifying its output (0 rows outside
    # the two owed classes), never by patching the numbers that went red.
    known_defects={
        '1019x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 222 > clientHeight 220',
        '1019x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1019x1000 [selector-pieces-on] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1019x1000 [selector-piece-off] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1020x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 222 > clientHeight 220',
        '1020x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1020x1000 [selector-pieces-on] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1020x1000 [selector-piece-off] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1060x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 228 > clientHeight 226',
        '1060x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1060x1000 [selector-pieces-on] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1060x1000 [selector-piece-off] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1061x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 228 > clientHeight 226',
        '1061x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1061x1000 [selector-pieces-on] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1061x1000 [selector-piece-off] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1100x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1100x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1100x1000 [selector-pieces-on] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1100x1000 [selector-piece-off] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1101x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1101x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1101x1000 [selector-pieces-on] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1101x1000 [selector-piece-off] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1180x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1180x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1180x1000 [selector-pieces-on] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1180x1000 [selector-piece-off] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1181x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 227 > clientHeight 225',
        '1181x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1181x1000 [selector-pieces-on] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1181x1000 [selector-piece-off] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1250x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1250x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1250x1000 [selector-pieces-on] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1250x1000 [selector-piece-off] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1251x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1251x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1251x1000 [selector-pieces-on] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1251x1000 [selector-piece-off] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1280x720 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1280x720 [page] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1280x720 [selector-pieces-on] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1280x720 [selector-piece-off] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1400x760 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1400x760 [page] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1400x760 [selector-pieces-on] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1400x760 [selector-piece-off] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1400x761 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1400x761 [page] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1400x761 [selector-pieces-on] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1400x761 [selector-piece-off] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1500x900 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1500x900 [page] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1500x900 [selector-pieces-on] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1500x900 [selector-piece-off] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1920x1080 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 230 > clientHeight 228',
        '1920x1080 [page] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1920x1080 [selector-pieces-on] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '1920x1080 [selector-piece-off] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '850x1180 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 199 > clientHeight 197',
        '850x1180 [page] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '850x1180 [selector-pieces-on] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '850x1180 [selector-piece-off] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '851x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 199 > clientHeight 197',
        '851x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '851x1000 [selector-pieces-on] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '851x1000 [selector-piece-off] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '860x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 200 > clientHeight 198',
        '860x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '860x1000 [selector-pieces-on] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '860x1000 [selector-piece-off] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '861x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 200 > clientHeight 198',
        '861x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '861x1000 [selector-pieces-on] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '861x1000 [selector-piece-off] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '900x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 206 > clientHeight 204',
        '900x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '900x1000 [selector-pieces-on] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '900x1000 [selector-piece-off] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '900x1180 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 206 > clientHeight 204',
        '900x1180 [page] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '900x1180 [selector-pieces-on] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '900x1180 [selector-piece-off] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '901x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 206 > clientHeight 204',
        '901x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '901x1000 [selector-pieces-on] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '901x1000 [selector-piece-off] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '912x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 207 > clientHeight 205',
        '912x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '912x1000 [selector-pieces-on] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '912x1000 [selector-piece-off] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '913x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 208 > clientHeight 206',
        '913x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '913x1000 [selector-pieces-on] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '913x1000 [selector-piece-off] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '979x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 217 > clientHeight 215',
        '979x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '979x1000 [selector-pieces-on] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '979x1000 [selector-piece-off] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '980x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 217 > clientHeight 215',
        '980x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '980x1000 [selector-pieces-on] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
        '980x1000 [selector-piece-off] overlap :: span.starholder x span.starrank':
            'overlap 26x2px inside button.starcell',
    },
)
