# tests/test_stagebanner_bowser_row.py
"""BitDW/BitFS/BitS now practice THREE independent things at once, not two
with a picker between them (live report 2026-07-29, same surface/root-cause
class as the 100-coin fix in test_stagebanner_hundred_coin.py).

Background: the corpus reshape that arms the pipe-entry segments on stage
ENTRY gives every Bowser level TWO segment_targets entries sharing the same
start_levels — the legacy EXCLUSIVE pipe-only segment and the new STRICT
reds->pipe segment (waypointed on the reds grab). `BowserCourseRow` used to
render exactly ONE pipe cell hardcoded "No reds" and ENFORCE mutual exclusion
by writing the OTHER segment's `enabled` flag whenever one was picked — with
two real segments sharing a level that flag-toggle (a) hid one of them under
a duplicate "No reds" label and (b) fought the matcher, which already keeps
both armed in parallel by design ("if 1 is armed, 2 should always be armed",
user). The fix deletes the toggle and the auto-restore effect that depended
on it, and renders both pipe segments through the shared StandardSegmentCell
so each shows its own name/rank/strat instead of a shared literal.

stagebanner.js is not import-free, so — same approach as
test_stagebanner_hundred_coin.py and test_star_icons.py — these are
SOURCE-SCAN assertions; the rendered behaviour is verified by hand against
the real app (see this task's own report).
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


def test_bowser_row_never_writes_the_enabled_flag():
    """The retired mutual-exclusion mechanism PUT enabled:true/false on the
    OTHER segment whenever one was picked — that write must be gone; picking
    a cell now only ever sets the target, same as every other segment row."""
    body = _bowser_row_body()
    assert "send(" not in body, (
        "BowserCourseRow still calls send(...) directly -- the enabled-flag "
        f"mutual-exclusion toggle may have come back: {body!r}"[:400])
    assert "enabled: false" not in body and "enabled: true" not in body


def test_bowser_row_no_longer_restores_a_last_pick():
    """The useEffect that re-derived 'which one was picked' from the enabled
    flag and auto-re-picked it is gone -- there is no longer a single 'last
    pick' for three things that all track simultaneously."""
    body = _bowser_row_body()
    assert "useEffect" not in body, \
        "BowserCourseRow still has a restore-on-entry effect -- the three " \
        "cells are independent now, nothing to restore"
    assert "enabledPipe" not in body


def test_bowser_row_renders_both_pipe_cells_through_standard_segment_cell():
    """Each pipe-related segment shows its OWN name/rank/strat (the shared
    cell already does this) instead of a hand-rolled cell hardcoding a single
    "No reds" label onto whichever segment(s) start_levels turns up."""
    body = _bowser_row_body()
    assert "<${StandardSegmentCell}" in body, \
        "BowserCourseRow no longer reuses StandardSegmentCell for the pipe cells"
    assert '"No reds"' not in body, \
        "a hardcoded \"No reds\" label would mislabel BOTH pipe segments " \
        "identically now that there are two of them"


def test_bowser_row_reds_pick_is_a_plain_target_write():
    """pickReds must not touch any segment's enabled flag -- the matcher's
    own EXCLUSIVE mode already cancels the pipe-only segment on any star
    grab, and the reds->pipe segment's waypoint IS the reds grab."""
    body = _bowser_row_body()
    pick_reds = re.search(r"async function pickReds\(\) \{.*?\n  \}\n", body, re.S)
    assert pick_reds, "pickReds not found (or changed shape) in BowserCourseRow"
    assert "requestTarget(" in pick_reds.group(0)
    assert "send(" not in pick_reds.group(0)
    assert "enabled" not in pick_reds.group(0)


def test_bowser_row_subtitle_no_longer_implies_a_choice():
    """The old subtitle framed the two cells as an either/or ("... or the
    pipe-entry skip (no reds)"); that phrasing is wrong now that all three
    track together with no exclusivity."""
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
