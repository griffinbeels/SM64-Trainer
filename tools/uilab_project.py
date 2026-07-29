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
_EXPAND_ALL = """
document.querySelectorAll('.card-collapse[aria-expanded="false"]')
  .forEach((b) => b.click());
"""
_COLLAPSE_ALL = """
document.querySelectorAll('.card-collapse[aria-expanded="true"]')
  .forEach((b) => b.click());
"""

STORIES = [
    Story(name="page", at="", setup=_EXPAND_ALL),
    Story(name="active-target", at=".objective-card",
          skip_if="!document.querySelector('.objective-card')"),
    Story(name="practice-log", at=".attempts-card",
          skip_if="!document.querySelector('.attempts-card')"),
    # Last, so it does not leave the page folded for the stories above.
    Story(name="page-collapsed", at="", setup=_COLLAPSE_ALL,
          skip_if="!document.querySelector('.card-collapse')"),
]

PROJECT = Project(
    serve=serve_ui,
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
