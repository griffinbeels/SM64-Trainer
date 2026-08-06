"""RankBanner's `layout` prop must stay a LAYOUT VARIANT, never a fork.

The compact (MARELO-shaped) rank display is "same hook, same climb plan, same
animated values -- only the arrangement of the same DOM parts differs,
selected by a class" (`.claude/rules/ui-climb.md`, `.claude/rules/ui-ranks.md`,
spec 2026-08-03-practice-log-entity-cards): `.rank-banner-row` turns
`display: contents` under `.rank-banner-stacked` (index.html) so its own
children become direct grid items of `.rank-banner` alongside
`.rank-progress-track`, its already-existing sibling -- ranks.js itself never
branches on `layout` inside the JSX it returns.

A later "fix" that special-cased `layout === "stacked"` into a SECOND render
tree would silently stop feeding it the climb: a fresh JSX branch has no
reason to carry `--climb-reveal`/`climb.vars`/the `is-climbing` class unless
whoever wrote it remembered to copy them by hand, and nothing about the
resulting page would look broken until an actual climb ran (which is exactly
what tests/test_ui_rank_stacked_climb.py drives, live, in a browser -- this
file is that test's cheap, structural companion: it runs in milliseconds with
no browser, and catches the fork itself rather than one of its symptoms).

Two facts, proven directly against ranks.js's own source text, comment-
stripped so a docstring cannot satisfy either: RankBanner calls
`useRankClimb` exactly once (one climb plan, never a second implementation
for the compact shape), and its body returns `html` exactly twice -- the
SENTINEL path (no rank to grade at all) and the ONE graded-rank render tree
that both layouts share. A third `return html` is precisely the shape a
`layout === "stacked" ? ... : ...` fork would add. Both are mutation-proved
against a tmp_path copy, never the tracked file -- a sibling session may run
this suite in this same worktree (CLAUDE.md's dev-process rules).
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from source_scan import strip_comments  # noqa: E402

RANKS_JS = (REPO / "src" / "sm64_events" / "ui" / "components" / "ranks.js")


def rank_banner_body(source: str) -> str:
    """The `export function RankBanner` block alone, comment-stripped -- a
    top-level `\\n}` closes it, the same convention every function in this
    file (and `source_scan.strip_comments`'s own callers) already relies on."""
    code = strip_comments(source)
    start = code.index("export function RankBanner(")
    end = code.index("\n}", start)
    return code[start:end]


def climb_hook_call_count(source: str) -> int:
    """How many times ANYTHING in this file starts a climb -- not just inside
    RankBanner, so a second implementation added elsewhere in the same file
    cannot slip past a body-scoped check."""
    return len(re.findall(r"\buseRankClimb\(", strip_comments(source)))


def graded_render_count(source: str) -> int:
    """How many `return html\\`` statements RankBanner's body contains. The
    sentinel (no rank at all) is one; the graded rank -- ROW today, STACKED
    since this spec, chosen by nothing but a class name on the same tree --
    must stay the other, for a total of exactly two."""
    return len(re.findall(r"return html`", rank_banner_body(source)))


# ---- The guards themselves --------------------------------------------------

def test_rankbanner_starts_exactly_one_climb():
    source = RANKS_JS.read_text(encoding="utf-8")
    count = climb_hook_call_count(source)
    assert count == 1, (
        f"ranks.js calls useRankClimb {count} times -- the compact layout "
        "must reuse the SAME climb as the row layout, never start a second "
        "one of its own")


def test_rankbanner_returns_exactly_two_render_trees():
    source = RANKS_JS.read_text(encoding="utf-8")
    count = graded_render_count(source)
    assert count == 2, (
        f"RankBanner's body contains {count} `return html` statements, not "
        "the sentinel-plus-one-graded-tree shape `layout` is supposed to "
        "share -- a `layout === \"stacked\" ? html`...` : html`...`` fork "
        "would show up here as a third, and it would stop feeding the "
        "stacked shape the climb's own animated values")


def test_neither_guard_reads_the_layout_prop_itself_as_a_fork():
    """The one place `layout` legitimately appears inside a string -- the
    root class ternary -- must not itself look like a second render tree to
    the guard above (a false positive here would make the real, correct
    single-tree implementation fail this suite)."""
    source = RANKS_JS.read_text(encoding="utf-8")
    body = rank_banner_body(source)
    assert 'layout === "stacked"' in body, (
        "sanity: the class ternary this guard is meant to tolerate is not "
        "even present -- rewrite the probe against the real shape")
    assert graded_render_count(source) == 2


# ---- Mutation proofs, over tmp_path copies of the REAL text ----------------
#
# Never the tracked file: a sibling session may be running this same suite in
# this same worktree, and an in-place mutate-then-revert is what produced a
# real round of phantom flaky tests on this branch already (CLAUDE.md).

def test_the_render_fork_guard_can_fail(tmp_path):
    real = RANKS_JS.read_text(encoding="utf-8")
    assert graded_render_count(real) == 2, (
        "the real file already fails this check -- fix ranks.js first")
    # The exact shape a "helpful" special-case would add: a second
    # `return html\`` right after the first, standing in for a
    # `layout === "stacked" ? html`...` : html`...`` fork -- the guard counts
    # occurrences, so a second literal one trips it identically to a real
    # ternary would.
    forked = real.replace(
        'return html`<div class=${`rank-banner${climb.climbing ? " is-climbing" : ""}${\n'
        '      layout === "stacked" ? " rank-banner-stacked" : ""}${\n'
        '      layout === "column" ? " rank-banner-column" : ""}${\n'
        '      nextStepMode === "hover" ? " rank-banner-nextstep-hover" : ""}`} style=${vars}>',
        'return html`<div class="rank-banner-stacked-mutant"></div>`;\n'
        '  return html`<div class=${`rank-banner${climb.climbing ? " is-climbing" : ""}${\n'
        '      layout === "stacked" ? " rank-banner-stacked" : ""}${\n'
        '      layout === "column" ? " rank-banner-column" : ""}${\n'
        '      nextStepMode === "hover" ? " rank-banner-nextstep-hover" : ""}`} style=${vars}>',
        1)
    assert forked != real, "the mutation's anchor text did not match ranks.js -- update it"
    mutated_path = tmp_path / "ranks.js"
    mutated_path.write_text(forked, encoding="utf-8")
    assert graded_render_count(forked) == 3
    assert graded_render_count(mutated_path.read_text(encoding="utf-8")) == 3


def test_the_climb_hook_count_guard_can_fail(tmp_path):
    real = RANKS_JS.read_text(encoding="utf-8")
    assert climb_hook_call_count(real) == 1, (
        "the real file already fails this check -- fix ranks.js first")
    duplicated = real.replace(
        "const climb = useRankClimb(graded, identity, { lane, order, replayKey });",
        "const climb = useRankClimb(graded, identity, { lane, order, replayKey });\n"
        "  const secondClimb = useRankClimb(graded, identity, { lane, order, replayKey });",
        1)
    assert duplicated != real, "the mutation's anchor text did not match ranks.js -- update it"
    mutated_path = tmp_path / "ranks.js"
    mutated_path.write_text(duplicated, encoding="utf-8")
    assert climb_hook_call_count(mutated_path.read_text(encoding="utf-8")) == 2
