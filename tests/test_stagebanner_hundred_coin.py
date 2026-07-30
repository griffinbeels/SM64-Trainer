# tests/test_stagebanner_hundred_coin.py
"""The "100 Coins" star cell represents its redirect segment on the star
row, rather than duplicating it as an eighth cell (live report 2026-07-29).
Detection is untouched by this fix -- these tests are about DISPLAY only.

Background: a star pick of (course, 6) already redirects server-side to that
course's 100-coin-exit SEGMENT (tracking/service.py::_hundred_coin_redirect,
"nobody times just the 100 star grab, it's always with something else" --
user 2026-07-24). The corpus reshape that arms this segment on COURSE ENTRY
(corpus_movements.py, 2026-07-29) made it armed on every visit, which exposed
two display defects:

1. It rendered as an EXTRA cell (armedExtraCells) beside the seven real
   stars, instead of overwriting the existing "100 Coins" cell.
2. It read as ACTIVE (glow + the running chip) merely because it is armed,
   even though nothing was deliberately chosen -- confusing, since it now
   arms in the background on every course visit rather than on a genuine
   pick. This narrows the "a running segment is never invisible" rule
   (2026-07-24) for this auto-armed case: the segment still tracks silently,
   it just no longer LOOKS chosen until it is.

stagebanner.js is not import-free (it pulls in preact/htm), so -- the same
approach tests/test_star_icons.py already takes for this file -- these are
SOURCE-SCAN assertions against the stripped-comment text, pinning the
structural facts a mutation would break. The rendered behaviour itself was
verified against the real app (chrome-devtools MCP over tools/ui_fixture.py
with the real corpus reconciled); see this task's own report for that half.
"""
import re
from pathlib import Path

from source_scan import strip_comments

UI = Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "ui"
BANNER = UI / "components" / "stagebanner.js"


def _function_body(name: str, source: str) -> str:
    match = re.search(rf"^(?:export )?function {name}\(.*?^}}", source, re.S | re.M)
    assert match, f"{name} not found in stagebanner.js"
    return match.group(0)


def test_hundred_coin_segment_is_identified_structurally_not_by_name():
    """Found the same way armedExtraCells already filters extras for this
    row -- startsInLevel(stage.level) -- never a hand-written seed_key/name
    table (this row never imports the corpus). Disabled degrades to the
    plain star, matching _hundred_coin_redirect's own fallback."""
    source = strip_comments(BANNER.read_text(encoding="utf-8"))
    assert "hundredCoinSegmentFor" in source
    definition = re.search(
        r"const hundredCoinSegmentFor = \(v, level\) =>.*?;\n", source, re.S)
    assert definition, "hundredCoinSegmentFor's definition changed shape"
    body = definition.group(0)
    assert "startsInLevel(level)" in body, \
        "hundredCoinSegmentFor no longer reuses startsInLevel's own criterion"
    assert "s.enabled" in body, \
        "a disabled 100-coin segment must degrade to the plain star"
    assert '"100' not in body and "seed_key" not in body, \
        "hundredCoinSegmentFor should not hand-match a name/seed_key table"


def test_just_completed_reuses_freshids_not_a_second_recency_notion():
    """Pins the recency check to freshIds (practice.js's useFreshAttemptIds
    Set) rather than a second timer/Date.now invented locally on this file."""
    source = strip_comments(BANNER.read_text(encoding="utf-8"))
    assert "justCompletedSegment" in source
    definition = re.search(
        r"const justCompletedSegment = \(v, freshIds, segmentId\) => \{.*?\n\};\n",
        source, re.S)
    assert definition, "justCompletedSegment's definition changed shape"
    body = definition.group(0)
    assert "freshIds.has(" in body, \
        "justCompletedSegment no longer checks membership in freshIds"
    assert "outcome === \"success\"" in body, \
        "justCompletedSegment no longer requires a SUCCESS outcome"
    assert "setTimeout" not in body and "Date.now" not in body, \
        "justCompletedSegment invented its own recency clock instead of " \
        "reusing freshIds' existing window"


def test_star_row_reads_and_forwards_freshids():
    source = strip_comments(BANNER.read_text(encoding="utf-8"))
    assert "function StarRow({ t, v, stage, freshIds })" in source, \
        "StarRow no longer accepts freshIds"
    assert "function StageBanner({ t, freshIds })" in source, \
        "StageBanner no longer accepts freshIds"
    banner_body = _function_body("StageBanner", source)
    assert "freshIds=${freshIds}" in banner_body, \
        "StageBanner no longer forwards freshIds to its Row"


def test_hundred_coin_segment_is_excluded_from_the_extra_cells():
    """Defect 1: it must not ALSO render as a duplicate armedExtraCells
    entry -- the existing "100 Coins" star cell already represents it."""
    source = strip_comments(BANNER.read_text(encoding="utf-8"))
    body = _function_body("StarRow", source)
    call = re.search(r"armedExtraCells\(t, v,(.*?),\s*setPicking", body, re.S)
    assert call, "StarRow no longer calls armedExtraCells"
    assert "hundredCoinSeg" in call.group(1), (
        "armedExtraCells' shownIds argument no longer excludes the "
        f"hundred-coin segment: {call.group(1)!r}")


def test_hundred_coin_cell_active_state_requires_a_pick_or_a_completion():
    """Defect 2: must not glow/run merely because the segment is armed."""
    source = strip_comments(BANNER.read_text(encoding="utf-8"))
    body = _function_body("StarRow", source)
    assert "hcTargeted" in body and "hcJustCompleted" in body
    assert "hcShowSegment = hcTargeted || hcJustCompleted;" in body, \
        "the cell's active state is no longer exactly (targeted OR just " \
        "completed) -- it must never be driven by armed state alone"
    assert "hcRunning = hcTargeted &&" in body, \
        "the running chip/armed border must require the DELIBERATE target, " \
        "not merely that the segment happens to be armed"
    assert "active=${isHundredCoins ? hcShowSegment" in body
    assert "armed=${isHundredCoins ? hcRunning : false}" in body


def test_hundred_coin_cell_keeps_the_stars_own_name_and_art():
    """The user asked for the star row KEPT: the cell's identity (name,
    icon) always stays the star's, even while it represents the segment's
    rank/strat/running state."""
    source = strip_comments(BANNER.read_text(encoding="utf-8"))
    body = _function_body("StarRow", source)
    # Both are unconditional in the cell's JSX -- never swapped for the
    # segment's own name/art the way rank/sub are.
    assert "iconSrc=${entityIconSrc(t, starKey(stage.course_id, i))}" in body
    assert "name=${name}" in body


def test_the_guards_can_still_fail():
    """A raw substring search cannot tell code from prose (ui-core.md's own
    verification norm) -- probe both directions against a comment-only and a
    real-code sample so a future edit to this file can't trip these checks
    on a docstring alone."""
    comment_only = "// hcShowSegment = hcTargeted || hcJustCompleted (see below)\n"
    real_code = "  const hcShowSegment = hcTargeted || hcJustCompleted;\n"
    assert "hcShowSegment = hcTargeted || hcJustCompleted" in strip_comments(real_code)
    assert "hcShowSegment = hcTargeted || hcJustCompleted" not in strip_comments(comment_only)
