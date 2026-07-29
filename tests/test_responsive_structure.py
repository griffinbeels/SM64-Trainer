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

# Generated 2026-07-28 from the shipping stylesheet.  Every row is Wave 2 work,
# not a permanent excuse.  Regenerate the whole dict with:
#
#   uv run python -c "import sys; sys.path.insert(0, 'tools'); \
#     from css_blocks import *; \
#     css = style_block(UI_HTML.read_text(encoding='utf-8')); \
#     [print(repr(b.condition + ' :: ' + s) + ': \"...\",') \
#      for b in size_blocks(parse_blocks(css)) if b.kind == 'media' \
#      for s in b.selectors if not is_shell(s)]"
LEGACY_VIEWPORT_RULES: dict[str, str] = {}
# EMPTY as of 2026-07-28. All 145 rows were converted, not exempted: every
# component-internal rule now gates on @container against its own pane, and
# `.view-pane` became a size container so the five non-Practice pages had one
# to gate against. What stays in @media is the shell -- the sidebar rail, the
# mobile app bar and bottom nav, the context bar (which lives OUTSIDE
# .view-pane and therefore has no container to ask).


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
