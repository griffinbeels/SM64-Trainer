# tests/test_bowser_pipe_card_naming.py
"""Round 2, item 4 (live report 2026-07-30): the pinned card for the Bowser
reds->pipe segment used to read "Segment · BitDW — 8 Red Coins → Pipe" (the
eyebrow's raw "Segment" context plus the definition's own corpus name), while
the banner cell that SELECTED it already read "8 Red Coins (Pipe)". The card
should agree with the cell rather than expose the segment's own identity --
same fix SHAPE as the 100-coin star's own card (b6640ee, "the card stopped
presenting a segment and presented the star"), applied to naming only: this
section still IS the segment (its own attempts/strategies/PB are untouched),
only the HEADING borrows the paired star's course + name.

practice.js is not import-free (it pulls in preact/htm), so -- same approach
as the sibling stagebanner.js source-scan test files -- these are SOURCE-SCAN
assertions against the stripped-comment text. The backend half (the two new
views.py fields the heading reads) is tested directly in
tests/test_views.py::test_pipe_segment_carries_the_paired_stars_own_display_names.
"""
import re
from pathlib import Path

from source_scan import strip_comments

PRACTICE_JS = (Path(__file__).resolve().parents[1] / "src" / "sm64_events"
               / "ui" / "components" / "practice.js")


def _segment_section_body() -> str:
    source = strip_comments(PRACTICE_JS.read_text(encoding="utf-8"))
    match = re.search(r"^function SegmentSection\(.*?^}", source, re.S | re.M)
    assert match, "SegmentSection not found in practice.js"
    return match.group(0)


def test_the_pipe_segments_card_borrows_the_stars_family_voice():
    """The heading must read familyName ("<star name> (Pipe)") over the raw
    sec.name, and the context chip must read the star's own course name
    instead of the literal "Segment" -- gated on sec.pipe_star_entity, the
    SAME guard views.py stamps pipe_star_course_name/pipe_star_name with."""
    body = _segment_section_body()
    assert re.search(
        r'const familyName = sec\.pipe_star_entity\s*'
        r'\? `\$\{sec\.pipe_star_name \|\| "Reds"\} \(Pipe\)` : null;', body), (
        "familyName is missing or no longer derived from "
        "sec.pipe_star_entity/pipe_star_name")
    assert "<h2>${familyName || sec.name}</h2>" in body, (
        "the card heading no longer prefers the family-voice name")
    assert re.search(
        r'<span class="objective-context">\$\{sec\.broken \? "History only"\s*'
        r': familyName \? sec\.pipe_star_course_name : "Segment"\}</span>',
        body), (
        "the context chip no longer swaps 'Segment' for the star's own "
        "course name when this is the pipe-paired segment")


def test_an_ordinary_segment_keeps_its_own_raw_identity():
    """Regression guard: a segment with no pipe pairing (sec.pipe_star_entity
    is null/undefined) must still show its own corpus name and the plain
    "Segment"/"History only" context -- this fix must not repaint every
    segment card, only the one family it names."""
    body = _segment_section_body()
    # familyName is null whenever pipe_star_entity is falsy, and both the
    # heading and the context chip fall back to the segment's own fields in
    # that case -- `|| sec.name` / `: "Segment"` are the fallback branches
    # asserted above; this test exists so a future edit that hardcodes the
    # family voice unconditionally (dropping the `sec.pipe_star_entity ?`
    # guard or the `|| sec.name` fallback) is caught here rather than only
    # by a live report on an ordinary movement.
    assert "sec.pipe_star_entity\n    ? " in body or \
        "sec.pipe_star_entity ? " in body, \
        "familyName lost its pipe_star_entity guard -- every segment would " \
        "render as a Bowser Reds pipe card"
    assert "|| sec.name" in body, \
        "the heading lost its fallback to sec.name for ordinary segments"
