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
STORIES = [
    Story(name="page", at=""),
    Story(name="active-target", at=".objective-card",
          skip_if="!document.querySelector('.objective-card')"),
    Story(name="practice-log", at=".attempts-card",
          skip_if="!document.querySelector('.attempts-card')"),
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
    stories=STORIES,
    # Sizes that earn a place regardless of what the stylesheet declares: the
    # two the user reported, the workspace's max width, and a short window.
    extra_viewports=((900, 1180), (760, 1180), (1500, 900), (1280, 720)),
    # CAVEAT: these rows are keyed on exact viewport + selector, and the
    # fixture renders a SNAPSHOT of the dev database -- so the set shifts a
    # little as real play changes the data. Two rows appeared here that a
    # worktree run did not produce. The durable fix is a deterministic fixture
    # database rather than a snapshot of the live one; until then, expect the
    # occasional new row of an already-known defect class.
    #
    # OWED, not exempted. Every row is real layout breakage on a POPULATED
    # practice page -- the state the fixture only started reaching once it
    # seeded a target and waited for the view to render. uilab's
    # stale-exemption gate deletes each row the moment its defect stops
    # occurring, so this list cannot quietly drift into fiction.
    known_defects={
        '400x1000 [page] overlap :: td.attempt-result.good x td.attempt-delta':
            'attempt-table cells collide at narrow widths',
        '401x1000 [page] overlap :: td.attempt-result.good x td.attempt-delta':
            'attempt-table cells collide at narrow widths',
        '320x800 [page] clipped :: section.practice-card.attempts-card':
            'scrollWidth 316 > clientWidth 275',
        '320x800 [page] clipped :: section.practice-card.objective-card':
            'scrollHeight 285 > clientHeight 278',
        '320x800 [page] overlap :: td.attempt-result.good x td.attempt-delta':
            'overlap 4x4px inside tr',
        '330x1000 [page] clipped :: section.practice-card.attempts-card':
            'scrollWidth 316 > clientWidth 275',
        '330x1000 [page] clipped :: section.practice-card.objective-card':
            'scrollHeight 285 > clientHeight 278',
        '330x1000 [page] overlap :: td.attempt-result.good x td.attempt-delta':
            'overlap 4x4px inside tr',
        '331x1000 [page] clipped :: section.practice-card.attempts-card':
            'scrollWidth 316 > clientWidth 275',
        '331x1000 [page] clipped :: section.practice-card.objective-card':
            'scrollHeight 285 > clientHeight 278',
        '331x1000 [page] overlap :: td.attempt-result.good x td.attempt-delta':
            'overlap 4x4px inside tr',
        '400x1000 [page] clipped :: section.practice-card.objective-card':
            'scrollHeight 285 > clientHeight 278',
        '401x1000 [page] clipped :: section.practice-card.objective-card':
            'scrollHeight 285 > clientHeight 278',
        '430x1000 [page] clipped :: section.practice-card.objective-card':
            'scrollHeight 285 > clientHeight 278',
        '431x1000 [page] clipped :: section.practice-card.objective-card':
            'scrollHeight 285 > clientHeight 278',
        '500x1000 [page] clipped :: section.practice-card.objective-card':
            'scrollHeight 285 > clientHeight 278',
        '501x1000 [page] clipped :: section.practice-card.objective-card':
            'scrollHeight 285 > clientHeight 278',
        '600x1000 [page] clipped :: section.practice-card.objective-card':
            'scrollHeight 285 > clientHeight 278',
        '601x1000 [page] clipped :: section.practice-card.objective-card':
            'scrollHeight 285 > clientHeight 278',
        '605x1000 [page] clipped :: section.practice-card.objective-card':
            'scrollHeight 285 > clientHeight 278',
        '606x1000 [page] clipped :: section.practice-card.objective-card':
            'scrollHeight 285 > clientHeight 278',
        '700x1000 [page] clipped :: section.practice-card.objective-card':
            'scrollHeight 285 > clientHeight 278',
        '701x1000 [page] clipped :: section.practice-card.objective-card':
            'scrollHeight 285 > clientHeight 278',
        '761x1000 [page] clipped :: section.practice-card.objective-card':
            'scrollHeight 285 > clientHeight 278',
        '775x1000 [page] clipped :: section.practice-card.objective-card':
            'scrollHeight 285 > clientHeight 278',
        '776x1000 [page] clipped :: section.practice-card.objective-card':
            'scrollHeight 285 > clientHeight 278',
        '780x1000 [page] clipped :: section.practice-card.objective-card':
            'scrollHeight 285 > clientHeight 278',
        '781x1000 [page] clipped :: section.practice-card.objective-card':
            'scrollHeight 285 > clientHeight 278',
    },
)
