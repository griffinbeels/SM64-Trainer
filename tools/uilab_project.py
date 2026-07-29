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
    known_defects={},
)
