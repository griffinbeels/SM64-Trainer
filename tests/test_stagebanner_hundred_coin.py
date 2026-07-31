# tests/test_stagebanner_hundred_coin.py
"""The "100 Coins" star cell is a PLAIN star cell, full stop (spec
2026-07-28-multi-step-segments, "the 100-coin star IS the segment").

Superseded history, for context: a star pick of (course, 6) used to redirect
server-side to that course's 100-coin-exit SEGMENT
(tracking/service.py::_hundred_coin_redirect, retired by this change), and
this row used to borrow that segment's rank/strat/armed state onto the star
cell (`hundredCoinSegmentFor`, live report 2026-07-29) with an
`isAmbientlyArmed` gate added 2026-07-30 to stop it glowing merely because
the engine ambiently arms on course entry. Both mechanisms are GONE now, not
narrowed further: the engine's completed attempts attribute directly to the
star (tracking/projection.py, segments.hundred_coin_entity), so views.py
excludes this family from `segment_targets`/`segments` entirely and there is
nothing left for this row to borrow -- the star's own rank_by_star/
last_strat_by_star already carry the real data. Confirmed by rendering
(tools/ui_fixture.py-style offline server, real corpus reconciled, a real
level_changed entering the course): the "100 Coins" cell shows a normal star
cell, and the Active Target card shows "Running" / "Step 1 of 2" on the
STAR's own card, never a second "ACTIVE SEGMENT" card.

stagebanner.js is not import-free (it pulls in preact/htm), so -- the same
approach tests/test_star_icons.py already takes for this file -- these are
SOURCE-SCAN assertions against the stripped-comment text: absence of the
retired special-casing, and that a "100 Coins" star is otherwise ordinary.
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


def test_the_redirect_borrowing_mechanism_is_gone():
    """None of the retired names may reappear -- their reintroduction would
    mean the star/segment merge regressed back into a borrowing hack."""
    source = strip_comments(BANNER.read_text(encoding="utf-8"))
    for retired in ("hundredCoinSegmentFor", "isHundredCoins", "hcTargeted",
                    "hcJustCompleted", "hcShowSegment", "hcRunning"):
        assert retired not in source, \
            f"{retired} reappeared -- the star/segment merge regressed"


def test_star_row_never_branches_on_which_star_it_is():
    """StarRow renders every star identically -- no per-star special case,
    which is the structural guarantee that "only star 6 changes" (upstream,
    in projection.py/views.py) needed no companion special case here too."""
    source = strip_comments(BANNER.read_text(encoding="utf-8"))
    body = _function_body("StarRow", source)
    assert "100 Coins" not in body, \
        "StarRow names the 100-coin star specifically -- it must treat " \
        "every star in `shown` the same way"
    # The cell's active/armed/rank/sub props read the PLAIN star fields
    # unconditionally (rankFor/lastStratFor), never a segment's.
    assert "active=${tgt.kind !== \"segment\"" in body
    assert "armed=${false}" in body
    assert "rank=${rankFor(i)}" in body
    assert "sub=${stratSub(lastStratFor(i))}" in body


def test_star_row_has_no_use_for_freshids_but_stagebanner_threads_it_again():
    """freshIds was retired at the StageBanner level in THIS merge (its only
    use here was the retired justCompletedSegment check onto the star cell,
    which this file's other tests confirm stays gone). Round 2 (spec
    2026-07-28-multi-step-segments) reintroduced it at StageBanner for a
    real, different consumer -- BowserCourseRow's detection-driven family
    memory (items 2/5) -- so this test's OWN claim ("one fewer prop, full
    stop") stopped being true; what stays true is that StarRow itself has no
    use for it. StageBanner threads it down to every row uniformly (simpler
    than special-casing the one row that reads it) rather than each row
    declaring its own need."""
    source = strip_comments(BANNER.read_text(encoding="utf-8"))
    assert "function StarRow({ t, v, stage })" in source
    assert "function StageBanner({ t, freshIds })" in source
    star_body = _function_body("StarRow", source)
    assert "freshIds" not in star_body
    bowser_body = _function_body("BowserCourseRow", source)
    assert "freshIds" in bowser_body


def test_hundred_coin_star_gets_no_extra_cell():
    """armedExtraCells' shownIds no longer special-cases anything for this
    row -- the 100-coin engine is excluded from segment_targets server-side
    (views.py), so armedSegments(t, v) can never surface it here regardless
    of what this row passes."""
    source = strip_comments(BANNER.read_text(encoding="utf-8"))
    body = _function_body("StarRow", source)
    call = re.search(r"armedExtraCells\(t, v,(.*?),\s*setPicking", body, re.S)
    assert call, "StarRow no longer calls armedExtraCells"
    assert call.group(1).strip() == "new Set()"


def test_the_guards_can_still_fail():
    """A raw substring search cannot tell code from prose (ui-core.md's own
    verification norm) -- a comment merely naming a retired identifier must
    not itself satisfy "the identifier is gone"."""
    comment_only = "// hundredCoinSegmentFor used to live here (retired)\n"
    assert "hundredCoinSegmentFor" not in strip_comments(comment_only)
    real_code = "const hundredCoinSegmentFor = (v, level) => null;\n"
    assert "hundredCoinSegmentFor" in strip_comments(real_code)
