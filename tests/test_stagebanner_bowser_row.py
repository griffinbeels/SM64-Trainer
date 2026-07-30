# tests/test_stagebanner_bowser_row.py
"""BitDW/BitFS/BitS: two cells since 2026-07-30 (spec 2026-07-28-multi-step-
segments, "the Bowser Reds star/pipe toggle"), superseding the THREE-cell row
912466d landed (live report 2026-07-29, same surface/root-cause class as the
100-coin fix in test_stagebanner_hundred_coin.py) and this file's own earlier
version, which pinned that three-cell shape.

Background, in order: the corpus reshape that arms the pipe-entry segments on
stage ENTRY gave every Bowser level TWO segment_targets entries sharing the
same start_levels — the legacy EXCLUSIVE pipe-only segment and the new STRICT
reds->pipe segment (waypointed on the reds grab). `BowserCourseRow` used to
render exactly ONE pipe cell hardcoded "No reds" and ENFORCE mutual exclusion
by writing the OTHER segment's `enabled` flag whenever one was picked — with
two real segments sharing a level that flag-toggle (a) hid one of them under
a duplicate "No reds" label and (b) fought the matcher, which already keeps
both armed in parallel by design ("if 1 is armed, 2 should always be armed",
user). 912466d deleted the toggle and rendered both pipe segments through the
shared StandardSegmentCell, giving THREE cells total (the reds star + both
pipe segments). This task folds the reds->pipe cell into a star/pipe TOGGLE
inside the Reds cell itself ("the third cell goes away... what replaces it is
a toggle inside the Reds cell", user) — TWO cells: "No Reds" (still the
legacy exclusive pipe-only segment, unchanged) and "Reds" (the star, now with
an internal toggle picking between its own grab-only timing and the
reds->pipe segment's timing).

stagebanner.js is not import-free, so — same approach as
test_stagebanner_hundred_coin.py and test_star_icons.py — these are
SOURCE-SCAN assertions; the rendered behaviour is verified by rendering (see
this task's own report, including a mid-transition frame of the rank icon's
squash/pop between the two families).
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


def _bowser_row_body() -> str:
    source = strip_comments(BANNER.read_text(encoding="utf-8"))
    return _function_body("BowserCourseRow", source)


def _reds_cell_body() -> str:
    source = strip_comments(BANNER.read_text(encoding="utf-8"))
    return _function_body("RedsCell", source)


def test_bowser_row_never_writes_the_enabled_flag_to_the_OTHER_segment():
    """The retired mutual-exclusion mechanism disabled the sibling segment
    whenever one was picked -- that write must stay gone. `RedsCell`'s own
    `onPickPipe` (wired from `pickPipe` below) DOES legitimately write
    `enabled: true` to enable the segment it is ABOUT to target, the exact
    same pattern `StandardSegmentCell.pick()` already uses elsewhere in this
    file -- an enable-before-targeting write, never a disable-the-sibling
    one. What must never come back is `enabled: false`."""
    body = _bowser_row_body()
    assert "enabled: false" not in body, (
        "BowserCourseRow writes enabled:false -- the retired mutual-exclusion "
        f"toggle may have come back: {body!r}"[:400])


def test_bowser_row_no_longer_restores_a_last_pick():
    """The useEffect that re-derived 'which one was picked' from the enabled
    flag and auto-re-picked it is gone -- the toggle's displayed selection is
    DERIVED from the current target every render, nothing to restore."""
    body = _bowser_row_body()
    assert "useEffect" not in body, \
        "BowserCourseRow still has a restore-on-entry effect -- the toggle's " \
        "selection is derived from the target, nothing to restore"
    assert "enabledPipe" not in body


def test_bowser_row_renders_the_no_reds_cell_through_standard_segment_cell():
    """The remaining "No Reds" cell shows its OWN name/rank/strat (the
    shared cell already does this) instead of a hand-rolled cell hardcoding
    a label -- and the Reds cell's own toggle is a NEW cell, not another
    StandardSegmentCell (it is a star's art + an in-cell control, not a
    segment's cell)."""
    body = _bowser_row_body()
    assert "<${StandardSegmentCell}" in body, \
        "BowserCourseRow no longer reuses StandardSegmentCell for 'No Reds'"
    assert "<${RedsCell}" in body, \
        "BowserCourseRow no longer renders the Reds star/pipe toggle cell"
    assert '"No reds"' not in body, \
        "a hardcoded \"No reds\" label belongs to the segment's own name now"


def test_bowser_row_star_pick_is_a_plain_target_write():
    """pickStar (the toggle's STAR half) must not touch any segment's
    enabled flag -- picking the star grades the grab alone and never arms
    or disarms anything."""
    body = _bowser_row_body()
    pick_star = re.search(r"async function pickStar\(\) \{.*?\n  \}\n", body, re.S)
    assert pick_star, "pickStar not found (or changed shape) in BowserCourseRow"
    assert "requestTarget(" in pick_star.group(0)
    assert "send(" not in pick_star.group(0)
    assert "enabled" not in pick_star.group(0)


def test_bowser_row_pipe_pick_only_ever_enables_never_disables():
    """pickPipe (the toggle's PIPE half) may enable the reds->pipe segment
    before targeting it (a segment can start disabled), but must never write
    `enabled: false` to ANY segment -- that write, on the SIBLING segment, is
    exactly the retired mutual-exclusion mechanism this file exists to keep
    out."""
    body = _bowser_row_body()
    pick_pipe = re.search(r"async function pickPipe\(\) \{.*?\n  \}\n", body, re.S)
    assert pick_pipe, "pickPipe not found (or changed shape) in BowserCourseRow"
    assert "requestTarget(" in pick_pipe.group(0)
    assert "enabled: false" not in pick_pipe.group(0)


def test_reds_cell_toggle_buttons_never_disable_the_star():
    """The star half of the toggle is disabled (an HTML `disabled` attribute)
    inside a route, never hidden and never made to silently no-op -- and
    RedsCell itself must not carry a second segment-enable write of its own
    (that belongs to BowserCourseRow's onPickPipe, passed in as a prop, so
    there is exactly one place that ever does it)."""
    body = _reds_cell_body()
    assert "disabled=" in body, \
        "RedsCell no longer disables the star toggle inside a route"
    assert "send(" not in body, \
        "RedsCell should not itself write segment state -- that belongs to " \
        "BowserCourseRow's onPickPipe, passed down as a prop"


def test_bowser_row_subtitle_no_longer_implies_a_choice():
    """The old subtitle framed the two PIPE cells as an either/or ("... or the
    pipe-entry skip (no reds)"); that phrasing is wrong here regardless of
    cell count."""
    source = strip_comments(BANNER.read_text(encoding="utf-8"))
    assert "or the pipe-entry skip (no reds)" not in source, \
        "the subtitle still frames this as a two-way choice"


def test_the_guards_can_still_fail():
    """Probe both directions (ui-core.md's own norm) so a comment mentioning
    the retired mechanism can't trip these guards, and real code restoring it
    can't hide from them."""
    comment_only = (
        '// we used to call send("PUT", ..., { enabled: false }) here,'
        " and had a useEffect to restore the pick, but not anymore\n")
    real_code = (
        'async function pickReds() {\n'
        '  await send("PUT", `/api/segments/${s.segment_id}`, { enabled: false });\n'
        '  await requestTarget(t, { course_id: stage.course_id, star_id: 0 });\n'
        '}\n')
    stripped_comment = strip_comments(comment_only)
    stripped_code = strip_comments(real_code)
    assert "send(" not in stripped_comment and "useEffect" not in stripped_comment
    assert "send(" in stripped_code
