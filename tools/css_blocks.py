"""Parse index.html's single <style> block into responsive blocks + selectors.

The one door for "what responsive blocks does the stylesheet declare, and what
selectors are inside them". tests/test_responsive_structure.py polices which
blocks may style what; tests/test_responsive.py checks every threshold is
probed. Both read the stylesheet through here, so neither can disagree with the
other about what a block IS.

Regex, not a real CSS parser: the stylesheet is one hand-written block in one
file with no preprocessor, and staying dependency-free keeps this usable from
pytest and from a plain `uv run` alike. The one thing it must get right is
brace matching -- a flat `@container` regex picks up the word out of three
PROSE COMMENTS in the real file and reports phantom blocks with zero rules,
which would then be silently exempt from every check built on top.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

UI_HTML = (Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "ui"
           / "index.html")

# The closed list of things a VIEWPORT media query is allowed to style: the
# application shell. Everything else is component-internal and belongs in an
# @container rule against its own pane -- see .claude/rules/ui-core.md, and the
# measurement that forces it: the sidebar is 206px above 1180px and a 76px rail
# below, so pane width is NOT monotonic in window width (a 1181px window gives
# a card a 947px pane; a 1180px window gives it 1076px).
#
# Adding a prefix here widens the law. It is a reviewed decision, not a
# convenience for making a test pass.
SHELL_PREFIXES = (
    "app-shell", "app-sidebar", "app-brand", "app-main", "app-notice",
    "nav-", "sidebar-", "mobile-", "workspace", "context-", "view-pane",
    "sheet-",
)
# Bare element/pseudo selectors that are never component-internal.
SHELL_LITERALS = ("html", "body", ":root", "*")


@dataclass
class Block:
    kind: str                                     # "media" | "container"
    condition: str                                # "(max-width: 760px)"
    line: int                                     # 1-based, within the style block
    selectors: list[str] = field(default_factory=list)


def style_block(html: str) -> str:
    """The contents of index.html's single <style> element."""
    start = html.index("<style>") + len("<style>")
    return html[start:html.index("</style>", start)]


def parse_blocks(css: str) -> list[Block]:
    """Every @media/@container block, brace-matched, with its own selectors.

    The `\\(` in the pattern is load-bearing: it is what stops a comment
    mentioning the at-rule from being read as one.
    """
    blocks: list[Block] = []
    for match in re.finditer(r"@(media|container)\s*(\([^{]*)\{", css):
        depth, index = 1, match.end()
        while depth and index < len(css):
            depth += {"{": 1, "}": -1}.get(css[index], 0)
            index += 1
        body = css[match.end():index - 1]
        blocks.append(Block(
            kind=match.group(1),
            condition=match.group(2).strip(),
            line=css[:match.start()].count("\n") + 1,
            selectors=[s.strip() for s in
                       re.findall(r"(?m)^\s*([^@{}/\n][^{}\n]*?)\s*\{", body)],
        ))
    return blocks


def size_blocks(blocks: list[Block]) -> list[Block]:
    """Blocks that gate on SIZE -- not on a user preference."""
    return [b for b in blocks if "prefers-" not in b.condition]


def thresholds(block: Block) -> list[tuple[str, int]]:
    """Every `(min|max)-(width|height): Npx` condition in the block."""
    return [(f"{bound}-{axis}", int(value)) for bound, axis, value in
            re.findall(r"(min|max)-(width|height)\s*:\s*(\d+)px", block.condition)]


def is_shell(selector: str) -> bool:
    """True when this selector only ever styles the application shell.

    Judged on the selector's HEAD -- the element the rule is anchored to. A
    descendant like `.mobile-nav .nav-item` is shell because the shell owns
    where it lives; `.practice-detail-grid > .analysis-card` is not, however
    shell-ish its ancestors are.
    """
    head = selector.split()[0].split(">")[0].strip()
    if head in SHELL_LITERALS:
        return True
    return any(name.startswith(prefix)
               for name in re.findall(r"\.([A-Za-z0-9_-]+)", head)
               for prefix in SHELL_PREFIXES)
