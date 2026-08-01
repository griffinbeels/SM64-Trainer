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

Round 2 PART 2 (live report 2026-07-30, again) extended this to the legacy
"no reds" pipe-only segments (seg:bitdw-pipe/seg:bitfs-pipe/seg:bits-pipe):
the first pass reached seg:reds->pipe:* and left this sibling family reading
its own raw corpus name ("BitDW Pipe Entry") -- same treatment, "No Reds",
on the card as well as the cell.

practice.js is not import-free (it pulls in preact/htm), so -- same approach
as the sibling stagebanner.js source-scan test files -- these are SOURCE-SCAN
assertions against the stripped-comment text. The backend half (the views.py
fields the heading reads) is tested directly in tests/test_views.py::
test_pipe_segment_carries_the_paired_stars_own_display_names and
test_legacy_no_reds_segment_is_flagged_for_the_pinned_cards_naming.
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


def _star_section_body() -> str:
    source = strip_comments(PRACTICE_JS.read_text(encoding="utf-8"))
    match = re.search(r"^function StarSection\(.*?^}", source, re.S | re.M)
    assert match, "StarSection not found in practice.js"
    return match.group(0)


def test_the_pipe_segments_card_borrows_the_stars_family_voice():
    """The heading must read familyName ("<star name> (Pipe)") over the raw
    sec.name, and the context chip must read the star's own course name
    instead of the literal "Segment" -- gated on sec.pipe_star_entity, the
    SAME guard views.py stamps pipe_star_course_name/pipe_star_name with.
    familyLabel (../redsfamily.js) is the ONE composer -- see
    tests/test_single_source.py for the guard banning a second one."""
    body = _segment_section_body()
    assert "import { familyLabel } from \"../redsfamily.js\";" in \
        strip_comments(PRACTICE_JS.read_text(encoding="utf-8")), \
        "practice.js no longer imports the shared family-suffix composer"
    assert re.search(
        r'const familyName = sec\.pipe_star_entity\s*'
        r'\? familyLabel\(sec\.pipe_star_name \|\| "Reds", true\)\s*'
        r': noRedsCourse \? "No Reds" : null;', body), (
        "familyName is missing or no longer derived from "
        "sec.pipe_star_entity/pipe_star_name via familyLabel")
    assert "<h2>${familyName || sec.name}</h2>" in body, (
        "the card heading no longer prefers the family-voice name")
    assert re.search(
        r'const familyCourseName = sec\.pipe_star_entity\s*'
        r'\? sec\.pipe_star_course_name\s*'
        r': noRedsCourse \? noRedsCourse\.name : null;', body), (
        "familyCourseName is missing or no longer derived from "
        "sec.pipe_star_course_name")
    assert re.search(
        r'<span class="objective-context">\$\{sec\.broken \? "History only"\s*'
        r': familyName \? familyCourseName : "Segment"\}</span>',
        body), (
        "the context chip no longer swaps 'Segment' for the resolved "
        "family course name")


def test_the_legacy_no_reds_card_also_reads_no_reds():
    """Round 2 part 2 (live report 2026-07-30): "the pinned card still says
    'BitDW Pipe Entry', not 'No Reds'... all three read 'No Reds', on the
    card as well as the cell" -- the reds->pipe fix's missing half. Gated on
    `sec.is_no_reds_pipe` (views.py), the sibling flag to pipe_star_entity;
    the course context resolves off `sec.course_id` through the session's
    own `catalog.courses` rather than a second server-side course-name
    field for a fact the client already has."""
    body = _segment_section_body()
    assert re.search(
        r"const noRedsCourse = sec\.is_no_reds_pipe\s*"
        r"\? \(\(t\.view\.catalog \|\| \{\}\)\.courses \|\| \[\]\)"
        r"\.find\(\(c\) => c\.id === sec\.course_id\)\s*"
        r": null;", body), (
        "noRedsCourse is missing or no longer gated on sec.is_no_reds_pipe")
    assert '"No Reds"' in body, \
        "the literal 'No Reds' display name is gone from the pinned card"


def test_the_reds_stars_own_card_also_reads_star_or_pipe():
    """Round 2 part 2, the STAR half (live report 2026-07-31): with Star
    selected on the Reds cell, the pinned card read a bare "8 Red Coins"
    while the cell itself and the Pipe-mode card both already spelled out
    the family -- the star card disagreeing with its own cell is the same
    bug one surface later. `sec.pipe_segment_id` is the discriminator
    ALREADY on every star section (views.py, non-null only for a Bowser
    course's star 0) -- no new server field, and familyLabel is the SAME
    composer the Pipe half and the cell's own toggle call."""
    body = _star_section_body()
    assert re.search(
        r"const starDisplayName = sec\.pipe_segment_id != null\s*"
        r"\? familyLabel\(sec\.star_name, false\) : sec\.star_name;", body), (
        "starDisplayName is missing or no longer gated on "
        "sec.pipe_segment_id / composed via familyLabel")
    assert "<h2>${starDisplayName}</h2>" in body, (
        "the star card heading no longer prefers the resolved display name")


def test_an_ordinary_star_keeps_its_own_raw_name():
    """Regression guard: an ordinary star (sec.pipe_segment_id is null/
    undefined) must still show its own plain star_name -- this fix must
    only touch a Bowser course's reds star, never every star's heading."""
    body = _star_section_body()
    assert "sec.pipe_segment_id != null" in body, \
        "starDisplayName lost its pipe_segment_id guard -- every star " \
        "would render with a family suffix"
    assert ": sec.star_name;" in body, \
        "the star heading lost its fallback to the plain star_name"


def test_an_ordinary_segment_keeps_its_own_raw_identity():
    """Regression guard: a segment with no pipe pairing (sec.pipe_star_entity
    is null/undefined) and no no-reds flag must still show its own corpus
    name and the plain "Segment"/"History only" context -- this fix must
    not repaint every segment card, only the two families it names."""
    body = _segment_section_body()
    # familyName is null whenever BOTH gates are falsy, and both the
    # heading and the context chip fall back to the segment's own fields in
    # that case -- `|| sec.name` / `: "Segment"` are the fallback branches
    # asserted above; this test exists so a future edit that hardcodes
    # either family voice unconditionally (dropping a guard or the
    # `|| sec.name` fallback) is caught here rather than only by a live
    # report on an ordinary movement.
    assert "sec.pipe_star_entity\n    ? " in body or \
        "sec.pipe_star_entity ? " in body, \
        "familyName lost its pipe_star_entity guard -- every segment would " \
        "render as a Bowser Reds pipe card"
    assert "sec.is_no_reds_pipe" in body, \
        "familyName/noRedsCourse lost their is_no_reds_pipe guard -- every " \
        "segment would render as No Reds"
    assert "|| sec.name" in body, \
        "the heading lost its fallback to sec.name for ordinary segments"
