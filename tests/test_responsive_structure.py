"""Component layout gates on the CONTAINER; @media is for the shell only.

Why this exists: index.html already carried the correct diagnosis in a comment
-- "VIEWPORT WIDTH IS THE WRONG SIGNAL HERE: the sidebar is 206px wide above
1180px and a 76px rail below it, so the pane a card lives in is NOT monotonic
in window width.  Measured on the shipping shell: a 1181px window gives the
card a 947px pane, while a 1180px window gives it 1076px" -- and that insight
was applied to two rules and then never generalised.  145 component-internal
rules were still keyed to the viewport, and the Active Target card clipped its
own contents at 900x1180 as a direct result (live report 2026-07-28).

"Don't key components to the viewport" cannot fail a build.  This can.

LEGACY_VIEWPORT_RULES is the pre-existing debt, enumerated rather than
grandfathered silently, so it is visible and countable: Wave 2's job is to
empty it.  A NEW viewport rule for a component selector is a red build with no
exemption available except a reviewed row here.

Which selectors count as shell is decided in ONE place, tools/css_blocks.py's
SHELL_PREFIXES -- widening that list widens the law, so it is a reviewed edit
rather than a way to make this test pass.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from css_blocks import (UI_HTML, is_shell, parse_blocks,  # noqa: E402
                        size_blocks, style_block)

# Generated 2026-07-28 from the shipping stylesheet; the command is in
# docs/superpowers/plans/2026-07-28-responsive-wave1-audit-rig.md, Task 2.
# Every row is Wave 2 work, not a permanent excuse.
LEGACY_VIEWPORT_RULES: dict[str, str] = {
    '(max-width:820px) :: .compare-grid': "pre-existing 2026-07-28; Wave 2",
    '(max-width:820px) :: .compare-col, .compare-center': "pre-existing 2026-07-28; Wave 2",
    '(max-width:820px) :: .compare-center': "pre-existing 2026-07-28; Wave 2",
    '(max-width:820px) :: .compare-transport': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 900px) :: .marelo-track, .marelo-split': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 640px) :: .rank-cell-next': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 1180px) :: .connection-line': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 1180px) :: .recording-status': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 1180px) :: .recording-status .dot': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 1180px) :: .recording-status .dot::before': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 1180px) :: .recording-status .recording-light': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 1180px) :: .practice-detail-grid': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 1180px) :: .objective-card, .detail-drawer': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 1180px) :: .analysis-card, .attempts-card': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 1180px) :: .practice-index-item > .practice-detail-grid': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 1180px) :: .segments-workshop': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 1180px) :: .routes-workshop': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 1180px) :: .segment-definition-grid': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 1180px) :: .segsection.seg-guard': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 1180px) :: .routepick': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 1180px) :: .routepick > button': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 1180px) :: .compare-stage-card': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 1180px) :: .cf-row': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 900px) :: .segments-workshop, .routes-workshop': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 900px) :: .segment-editor': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 900px) :: .segment-library': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 900px) :: .segment-library, .segment-editor, .route-library, .route-empty-card': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 900px) :: .segment-library, .route-library': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 900px) :: .segment-list, .route-list': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 900px) :: .runbar': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 900px) :: .run-pb-slot': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 900px) :: .run-clock-row': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 900px) :: .run-clock-note': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 900px) :: .compare-grid': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 900px) :: .compare-stage-card': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .workshop-hero': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .workshop-title-icon': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .workshop-title p': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .workshop-hero > .primary-button': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .workshop-hero > .primary-button .ui-icon': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .workshop-card': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .builder-heading h2, .route-identity h2': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .segment-definition-grid': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .segsection.seg-guard': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .builder-actions .meta': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .routestart, .routeadd': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .routepick': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .routepick select:nth-of-type(3)': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .routepick > button': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .route-stat': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .routestep-actions': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .routecands, .routestep-foot': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .routeavg .meta': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .run-state, .compare-sync-note': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .run-console-card, .run-history-card, .feed-card': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .runbar': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .run-actions': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .run-actions button': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .run-pb-slot': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .run-live-slot': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .run-empty': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .run-clock-row': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .runclock': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .run-split-time': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .run-history-row': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .run-history-date': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .run-history-splits': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .compare-transport-card': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .compare-transport': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .compare-transport button': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .cf-head': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .cf-head select': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .cf-row': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .cf-stage': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .cf-item': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .cf-row .chip, .cf-row > .meta': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .cf-load-icon': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .compare-stage-heading': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .compare-stage-heading .cmp-strat': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .compare-drop': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .wa-btns button': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .feed-scroll': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .feed-footer': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .page-state': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .modal-backdrop': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .modal': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .modal-header': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .modal-icon': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .modal-heading p': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .modal-body': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .modal-actions': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .strategy-ladder-heading > span, .strategy-ladder-labels': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .strategy-rank-row': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .strategy-rank-row label:last-child': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .stdtools a.meta': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .update-summary em': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .replay-transport': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .replay-transport button': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .replay-frame-note': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .replay-settings-popover': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .replay-setting-row': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .practice-toolbar': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .route-focus-control': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .route-focus-control .field-label, .toolbar-note': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .route-focus-control select': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .practice-card': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .selector-card': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .selector-card .starrow': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .objective-card': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .objective-heading': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .objective-heading .eyebrow': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .objective-name': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .objective-name h2': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .objective-strategy': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .objective-strategy select': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .objective-metrics': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .rank-slot': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .objective-strategy-fastest': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .objective-live-state': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .objective-card .pbtag': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .analysis-card': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .attempts-card': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .sort-control select': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .attempt-table, .attempt-table tbody': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .attempt-table tr:not(.replay-row)': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .attempt-table td': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .attempt-result': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .attempt-delta': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .attempt-strategy': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .attempt-actions': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .attempt-strategy select': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .save-pb-wide': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .save-pb-narrow': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .replay-row, .replay-row td': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .index-heading': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .index-heading > .meta': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .practice-index-item > summary': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .practice-index-item > summary > .meta': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 760px) :: .settings-actions': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 430px) :: .objective-metrics': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 430px) :: .reset-toggle span': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 430px) :: .attempt-actions button': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 430px) :: .save-pb-narrow': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 430px) :: .replay-setting-row': "pre-existing 2026-07-28; Wave 2",
    '(max-width: 430px) :: .replay-setting-controls': "pre-existing 2026-07-28; Wave 2",}


def _violations() -> list[str]:
    css = style_block(UI_HTML.read_text(encoding="utf-8"))
    return [f"{block.condition} :: {selector}"
            for block in size_blocks(parse_blocks(css))
            if block.kind == "media"
            for selector in block.selectors if not is_shell(selector)]


def test_no_new_viewport_rule_styles_a_component():
    new = [v for v in _violations() if v not in LEGACY_VIEWPORT_RULES]
    assert not new, (
        "These @media rules style component-internal selectors.  Component "
        "layout must gate on @container against its own pane (see "
        ".claude/rules/ui-core.md, Responsiveness) -- viewport width is not "
        "monotonic in pane width in this shell, so no viewport threshold can "
        "express 'this card is too narrow':\n  " + "\n  ".join(new))


def test_the_legacy_list_does_not_outlive_its_rules():
    """A stale exemption is a lie about the size of the debt.

    Removing a rule must also remove its row, or the count silently stops
    meaning anything and Wave 2 looks finished while it isn't.
    """
    stale = [key for key in LEGACY_VIEWPORT_RULES if key not in _violations()]
    assert not stale, (
        f"Fixed, but still exempted -- delete {len(stale)} row(s): {stale[:10]}")


def test_the_guard_can_still_fail():
    """Mutation proof.  A scan that matches nothing is green forever, so pin
    that the classifier really does call a component selector a violation."""
    assert not is_shell(".objective-card")
    assert not is_shell(".rank-slot")
    assert not is_shell(".analysis-card, .attempts-card")
    assert is_shell(".app-sidebar")
    assert is_shell(".mobile-nav .nav-item")
