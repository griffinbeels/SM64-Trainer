"""A cell that grades must also say whether a ladder EXISTS.

`PracticeCell` draws the ladder floor (Capless 5) instead of a bare "–" when
an entity is rankable but untimed — but only if the caller passes
`hasStandards`, which defaults to False. That default is what made this
invisible: the floor shipped on 2026-07-30 with exactly ONE of the two
practice-banner call sites updated (the segment cell), so every ordinary STAR
cell in the course selector kept drawing "–" and the feature looked
unimplemented. Live report 2026-07-31, Tick Tock Clock: seven star cells, seven
dashes, while `rank_standards.seed.json` carries star:14:0 through star:14:5.

The comment above `StandardSegmentCell` asserted the two call shapes were
"byte-for-byte the SAME" the whole time it was false — which is the point of
this file. A claim in prose cannot fail a build; this can.

Scoped by what a call actually does, not by which file it is in:
  * no `rank=` at all      -> a folder/group cell, nothing to grade
  * `rankBadge=`           -> the picker grid, which deliberately renders
                              NOTHING when unranked (an in-flow placeholder per
                              grid row is most of what made it scroll)
  * anything else grading  -> must pass `hasStandards=`
"""
import re
from pathlib import Path

from source_scan import strip_comments

UI = Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "ui"

_CALL = re.compile(r"<\$\{PracticeCell\}(.*?)/>", re.S)


def cells_grading_without_a_floor(source: str) -> list[str]:
    """Props of every PracticeCell call that grades but cannot draw a floor."""
    offenders = []
    for body in _CALL.findall(strip_comments(source)):
        if "rank=" not in body or "rankBadge=" in body:
            continue
        if "hasStandards=" not in body:
            offenders.append(" ".join(body.split())[:80])
    return offenders


def _callers():
    return [path for path in sorted(UI.rglob("*.js"))
            if "<${PracticeCell}" in path.read_text(encoding="utf-8")]


def test_every_grading_cell_can_draw_the_ladder_floor():
    assert _callers(), "no PracticeCell call sites found -- did the tag rename?"
    for path in _callers():
        offenders = cells_grading_without_a_floor(
            path.read_text(encoding="utf-8"))
        assert not offenders, (
            f"{path.name}: PracticeCell call grades `rank` but never passes "
            f"`hasStandards`, so a rankable-but-untimed entity draws a bare "
            f"dash instead of the Capless 5 floor: {offenders}")


def test_the_guard_can_still_fail():
    # (comment-only -> clean, code -> caught). A guard a comment trips reports
    # fine work as a violation; a guard blind to code reports the reverse.
    assert cells_grading_without_a_floor(
        "// <${PracticeCell} rank=${r} /> used to be written without a floor\n"
    ) == []
    assert cells_grading_without_a_floor(
        "<${PracticeCell} rank=${rankFor(i)} name=${n} />") != []
    # The two documented exemptions stay exempt.
    assert cells_grading_without_a_floor(
        "<${PracticeCell} rank=${o.rank} rankBadge=${true} />") == []
    assert cells_grading_without_a_floor(
        "<${PracticeCell} name=${group.label} />") == []
    # And the shape the fix produces is accepted.
    assert cells_grading_without_a_floor(
        "<${PracticeCell} rank=${rankFor(i)} hasStandards=${h} />") == []
