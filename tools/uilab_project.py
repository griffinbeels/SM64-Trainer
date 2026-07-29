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

import functools
import sys
from pathlib import Path

from uilab import Project, Story

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ui_fixture import serve_ui  # noqa: E402

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
NEVER_TRUNCATE = (".rank-banner-kicker", ".context-label", ".nav-item span",
                  ".field-label")

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
_EXPAND_ALL = """
document.querySelectorAll('.card-collapse[aria-expanded="false"]')
  .forEach((b) => { if (!b.closest('.practice-index-item')) b.click(); });
document.querySelectorAll('details.practice-index-item:not([open])')
  .forEach((d) => { d.open = true; });
"""
_COLLAPSE_ALL = """
document.querySelectorAll('.card-collapse[aria-expanded="true"]')
  .forEach((b) => { if (!b.closest('.practice-index-item')) b.click(); });
"""

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
    # Last, so it does not leave the page folded for the stories above.
    Story(name="page-collapsed", at="", setup=_COLLAPSE_ALL,
          skip_if="!document.querySelector('.card-collapse')"),
]

PROJECT = Project(
    # LBLJ (segment id 1) armed alongside the default star target -- additive,
    # see ui_fixture.py::_arm_segment. It is one of the ten legacy tricks the
    # schema MIGRATION itself inserts, so it exists in this empty, deterministic
    # fixture with no defaults-corpus reconcile (that only runs from main.py).
    serve=functools.partial(serve_ui, arm_segment=1),
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
    # Sizes that earn a place regardless of what the stylesheet declares: the
    # two the user reported, the workspace's max width, and a short window.
    extra_viewports=((900, 1180), (760, 1180), (1500, 900), (1280, 720)),
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
        '320x800 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 184 > clientHeight 150',
        '320x800 [page] clipped :: span.starname':
            'scrollWidth 33 > clientWidth 30',
        '320x800 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '330x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 184 > clientHeight 150',
        '330x1000 [page] clipped :: span.starname':
            'scrollWidth 33 > clientWidth 30',
        '330x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '331x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 184 > clientHeight 150',
        '331x1000 [page] clipped :: span.starname':
            'scrollWidth 33 > clientWidth 30',
        '331x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '400x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 152 > clientHeight 150',
        '400x1000 [page] clipped :: span.starname':
            'scrollHeight 30 > clientHeight 20',
        '400x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '400x1000 [page] overlap :: td.attempt-result.good x td.attempt-delta':
            'overlap 4x21px inside tr',
        # [practice-log] here is unchanged (the star's own attempts table,
        # `.attempts-card` first match). [page-collapsed] joins at 401/430/431
        # below, not 400 (2026-07-29): LBLJ armed alongside the star target
        # (this file's `serve`) sits in the practice index, which `_EXPAND_ALL`
        # opens so its own .seg-waiting can be measured -- its `<details>`
        # stays open through [page-collapsed] too (that story's setup only
        # re-collapses .card-collapse OUTSIDE the practice index, never the
        # index's own <details> or its cards' internal folds -- see
        # `_EXPAND_ALL`/`_COLLAPSE_ALL`'s own comment for why: their SHARED,
        # ungrouped `useCollapsed` key would otherwise cross-toggle against
        # the star's identically-keyed card, which is what broke
        # tests/test_ui_collapse_story.py before this exclusion existed).
        # Same PRE-EXISTING overlap as [page]/[practice-log], a second real
        # instance of it: LBLJ's own attempts table is a narrower card than
        # the star's and clears the overlap at a slightly different width.
        '400x1000 [practice-log] overlap :: td.attempt-result.good x td.attempt-delta':
            'overlap 4x21px inside tr',
        # Right at this probe point's own boundary (400 is a derived
        # threshold), [page-collapsed] catches the same LBLJ overlap only
        # SOME runs -- reproduced across several `uv run pytest` invocations
        # but not every one, unlike the stabler 401/430/431 rows below.
        # Included rather than chased: same selector, same detail, same
        # underlying defect as its neighbours, and the flakiness reads as
        # sub-pixel measurement timing at an edge, not a different bug.
        '400x1000 [page-collapsed] overlap :: td.attempt-result.good x td.attempt-delta':
            'overlap 4x21px inside tr',
        '401x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 152 > clientHeight 150',
        '401x1000 [page] clipped :: span.starname':
            'scrollHeight 30 > clientHeight 20',
        '401x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '401x1000 [page] overlap :: td.attempt-result.good x td.attempt-delta':
            'overlap 4x21px inside tr',
        '401x1000 [page-collapsed] overlap :: td.attempt-result.good x td.attempt-delta':
            'overlap 4x21px inside tr',
        '401x1000 [practice-log] overlap :: td.attempt-result.good x td.attempt-delta':
            'overlap 4x21px inside tr',
        '430x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 153 > clientHeight 151',
        '430x1000 [page] clipped :: span.starname':
            'scrollHeight 30 > clientHeight 19',
        '430x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        # LBLJ's attempts table (see the 400px comment above) needs a little
        # more room than the star's own before this same overlap clears --
        # true through 431px, gone by 480px (below).
        '430x1000 [page] overlap :: td.attempt-result.good x td.attempt-delta':
            'overlap 4x21px inside tr',
        '430x1000 [page-collapsed] overlap :: td.attempt-result.good x td.attempt-delta':
            'overlap 4x21px inside tr',
        '431x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 153 > clientHeight 151',
        '431x1000 [page] clipped :: span.starname':
            'scrollHeight 30 > clientHeight 19',
        '431x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '431x1000 [page] overlap :: td.attempt-result.good x td.attempt-delta':
            'overlap 4x21px inside tr',
        '431x1000 [page-collapsed] overlap :: td.attempt-result.good x td.attempt-delta':
            'overlap 4x21px inside tr',
        '500x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 162 > clientHeight 160',
        '500x1000 [page] clipped :: span.starname':
            'scrollHeight 30 > clientHeight 20',
        '500x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '501x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 162 > clientHeight 160',
        '501x1000 [page] clipped :: span.starname':
            'scrollHeight 30 > clientHeight 20',
        '501x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '600x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 176 > clientHeight 174',
        '600x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '601x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 176 > clientHeight 174',
        '601x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '605x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 177 > clientHeight 175',
        '605x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '606x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 177 > clientHeight 175',
        '606x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '700x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 190 > clientHeight 188',
        '700x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '701x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 190 > clientHeight 188',
        '701x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '760x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 198 > clientHeight 196',
        '760x1000 [page] clipped :: span.starname':
            'scrollHeight 22 > clientHeight 20',
        '760x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '760x1180 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 198 > clientHeight 196',
        '760x1180 [page] clipped :: span.starname':
            'scrollHeight 22 > clientHeight 20',
        '760x1180 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '761x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 187 > clientHeight 185',
        '761x1000 [page] clipped :: span.starname':
            'scrollHeight 20 > clientHeight 18',
        '761x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '775x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 189 > clientHeight 187',
        '775x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '776x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 189 > clientHeight 187',
        '776x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '780x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 189 > clientHeight 187',
        '780x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '781x1000 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 189 > clientHeight 187',
        '781x1000 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
        '900x1180 [page] clipped :: section.practice-card.selector-card.stagebanner':
            'scrollHeight 206 > clientHeight 204',
        '900x1180 [page] overlap :: span.starholder x span.starrank':
            'overlap 7x2px inside button.starcell',
    },
)
