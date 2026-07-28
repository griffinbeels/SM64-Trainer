"""The stylesheet parser both responsive gates are built on.

Kept separate from the gates because a parser bug would make either of them
green for the wrong reason -- a block it fails to see is a block it cannot
police, and the failure looks exactly like "no violations found".
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from css_blocks import (UI_HTML, is_shell, parse_blocks,  # noqa: E402
                        size_blocks, style_block, thresholds)

SAMPLE = """
  .a { color: red; }
  @media (prefers-reduced-motion: reduce) { .b { color: blue; } }
  @media (max-width: 760px) {
    .app-sidebar { display: none; }
    .objective-card { height: 258px; }
    .x:hover { color: red; }
  }
  @container (max-width: 1100px) { .rank-slot { gap: .3rem; } }
"""


def test_parse_finds_every_block_with_its_kind_and_condition():
    blocks = parse_blocks(SAMPLE)
    assert [(b.kind, b.condition) for b in blocks] == [
        ("media", "(prefers-reduced-motion: reduce)"),
        ("media", "(max-width: 760px)"),
        ("container", "(max-width: 1100px)"),
    ]


def test_selectors_are_listed_per_block():
    media760 = parse_blocks(SAMPLE)[1]
    assert media760.selectors == [".app-sidebar", ".objective-card", ".x:hover"]


def test_size_blocks_drops_preference_queries():
    assert [b.condition for b in size_blocks(parse_blocks(SAMPLE))] == [
        "(max-width: 760px)", "(max-width: 1100px)"]


def test_thresholds_are_parsed_as_numbers():
    blocks = size_blocks(parse_blocks(SAMPLE))
    assert thresholds(blocks[0]) == [("max-width", 760)]
    assert thresholds(blocks[1]) == [("max-width", 1100)]


def test_is_shell_recognises_shell_and_component_selectors():
    assert is_shell(".app-sidebar")
    assert is_shell(".mobile-nav .nav-item")
    assert not is_shell(".objective-card")
    assert not is_shell(".practice-detail-grid > .analysis-card")


def test_a_prose_comment_naming_container_is_not_parsed_as_a_block():
    """The regex requires `(` after the at-rule name for exactly this reason.

    A flat `@container` count over the real stylesheet picks up the word out of
    three explanatory comments and reports phantom blocks with zero rules --
    which would then be silently exempt from every check below.
    """
    prose = "/* These are @container rules, on .practice-page's own box. */\n"
    shape = lambda css: [(b.kind, b.condition, b.selectors)   # noqa: E731
                         for b in parse_blocks(css)]
    assert shape(prose + SAMPLE) == shape(SAMPLE)


def test_a_comment_quoting_a_REAL_condition_is_not_parsed_as_a_block():
    """The harder case, and the one that actually bit (2026-07-28).

    Requiring `(` after the at-rule name is not enough, because this
    stylesheet's comments quote conditions verbatim: the @container block for
    the objective card explains what it replaced by writing
    `@media (max-width: 760px)` in prose.  A raw scan read that sentence as a
    block with twelve rules in it and inflated the violation count the
    structural guard depends on.
    """
    prose = ("/* Moved out of @media (max-width: 760px) on 2026-07-28,\n"
             "   because the pane there is 725px and not 642px. */\n")
    shape = lambda css: [(b.kind, b.condition, b.selectors)   # noqa: E731
                         for b in parse_blocks(css)]
    assert shape(prose + SAMPLE) == shape(SAMPLE)


def test_line_numbers_survive_comment_stripping():
    """Stripping must blank comments, not delete them: `line` is how a failure
    message points a reader at the offending rule."""
    css = "/* one\n   two\n   three */\n@media (max-width: 500px) { .a { color: red; } }"
    assert parse_blocks(css)[0].line == 4


def test_the_real_stylesheet_parses_and_has_the_blocks_we_expect():
    css = style_block(UI_HTML.read_text(encoding="utf-8"))
    blocks = size_blocks(parse_blocks(css))
    conditions = {b.condition for b in blocks}
    assert "(max-width: 760px)" in conditions
    assert "(max-width: 1180px)" in conditions
    assert all(b.condition.startswith("(") for b in blocks)
    assert all(b.selectors for b in blocks), (
        "a size block with no rules in it is a parse artifact, not a block")
