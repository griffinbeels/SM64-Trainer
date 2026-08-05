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

Round 2 part 2, the STAR half (live report 2026-07-31): with Star selected on
the Reds cell, the pinned card read a bare "8 Red Coins" while the cell
itself and the Pipe-mode card both already spelled out the family.

practice.js's StarSection/SegmentSection answered all of this by hand until
the 2026-08-03 practice-log-entity-cards refactor moved the derivation into
`ui/entitysection.js::displayName` (StarSection/SegmentSection called it
instead of composing familyName/familyCourseName/noRedsCourse/starDisplayName
inline -- see tests/test_ui_entity_section.py for the module's own behavioral
coverage of every scenario named above). These tests now assert the SHAPE of
that hand-off rather than the retired inline expressions.

SUPERSEDED 2026-08-04 (amendment A8, spec practice-log-entity-cards):
StarSection/SegmentSection are deleted along with the Active Target card --
`displayName`'s one remaining caller is `LogCard` (ui/components/
practicelog.js), the shared card either kind now renders through. The
assertions below moved with it: `LogCard` must still call `displayName`/
`entityKey` (never re-derive a family suffix itself), and entitysection.js
must still route through familyLabel (../redsfamily.js) for the two family
voices.

practicelog.js is not import-free (it pulls in preact/htm), so -- same
approach as the sibling stagebanner.js source-scan test files -- the
practicelog.js half of these assertions are SOURCE-SCAN against the
stripped-comment text. entitysection.js IS import-free, so its half is
driven the same way test_ui_entity_section.py drives it (a source-scan here
too, since the concern is which composer a branch calls, not what it
returns -- the return value is already pinned behaviorally by that other
file). The backend half (the views.py fields the heading reads) is tested
directly in
tests/test_views.py::test_pipe_segment_carries_the_paired_stars_own_display_names
and test_legacy_no_reds_segment_is_flagged_for_the_pinned_cards_naming.
"""
import re
from pathlib import Path

from source_scan import strip_comments

PRACTICELOG_JS = (Path(__file__).resolve().parents[1] / "src" / "sm64_events"
                  / "ui" / "components" / "practicelog.js")
ENTITYSECTION_JS = (Path(__file__).resolve().parents[1] / "src" / "sm64_events"
                    / "ui" / "entitysection.js")


def _log_card_body() -> str:
    source = strip_comments(PRACTICELOG_JS.read_text(encoding="utf-8"))
    match = re.search(r"^export function LogCard\(.*?^}", source, re.S | re.M)
    assert match, "LogCard not found in practicelog.js"
    return match.group(0)


def _entitysection_source() -> str:
    return strip_comments(ENTITYSECTION_JS.read_text(encoding="utf-8"))


def test_the_pipe_segments_card_borrows_the_stars_family_voice():
    """The heading must read the family voice ("<star name> (Pipe)") over
    the raw sec.name, and the context chip must read the star's own course
    name instead of the literal "Segment" -- gated on sec.pipe_star_entity,
    the SAME guard views.py stamps pipe_star_course_name/pipe_star_name
    with. familyLabel (../redsfamily.js) is the ONE composer, called from
    entitysection.js -- see tests/test_single_source.py for the guard
    banning a second one. LogCard may not name `familyLabel` itself any
    more -- a second inline composer in practicelog.js would be exactly that
    same second door."""
    practicelog_src = strip_comments(PRACTICELOG_JS.read_text(encoding="utf-8"))
    assert "entitysection.js" in practicelog_src, (
        "practicelog.js no longer imports the shared kind-dispatch module")
    assert "familyLabel" not in practicelog_src, (
        "practicelog.js composes a family suffix itself again -- that is "
        "entitysection.js's job, and a second composer is the exact bug "
        "rule 11 exists to stop")
    body = _log_card_body()
    assert re.search(r"displayName\(sec,\s*"
                      r"\(t\.view\.catalog \|\| \{\}\)\.courses \|\| \[\]\)",
                      body), (
        "LogCard no longer asks displayName for its heading text")
    assert "text=${named.name}" in body, (
        "the card heading no longer renders displayName's resolved name "
        "(ShrinkToFitName's own text prop)")
    assert '<span class="log-card-context">${named.context}</span>' in body, (
        "the context chip no longer renders displayName's resolved context")
    entitysection = _entitysection_source()
    assert re.search(
        r'if \(sec\.pipe_star_entity\)\s*\{\s*'
        r'return \{ name: familyLabel\(sec\.pipe_star_name \|\| "Reds", true\),',
        entitysection), (
        "entitysection.js no longer derives the pipe segment's family name "
        "from sec.pipe_star_entity/pipe_star_name via familyLabel")
    assert "courseName: sec.pipe_star_course_name" in entitysection, (
        "entitysection.js no longer carries pipe_star_course_name through "
        "as the family's course name")
    assert re.search(
        r'context: sec\.broken \? "History only"\s*'
        r': family \? family\.courseName : "Segment",',
        entitysection), (
        "entitysection.js's displayName no longer swaps 'Segment' for the "
        "resolved family course name")


def test_the_legacy_no_reds_card_also_reads_no_reds():
    """Round 2 part 2 (live report 2026-07-30): "the pinned card still says
    'BitDW Pipe Entry', not 'No Reds'... all three read 'No Reds', on the
    card as well as the cell" -- the reds->pipe fix's missing half. Gated on
    `sec.is_no_reds_pipe` (views.py), the sibling flag to pipe_star_entity;
    the course context resolves off `sec.course_id` through the session's
    own `catalog.courses`, threaded into entitysection.js by LogCard rather
    than a second server-side course-name field for a fact the client
    already has."""
    entitysection = _entitysection_source()
    assert re.search(
        r"if \(sec\.is_no_reds_pipe\)\s*\{\s*"
        r"const course = courses\.find\(\(c\) => c\.id === sec\.course_id\);",
        entitysection), (
        "entitysection.js no longer gates the no-reds course lookup on "
        "sec.is_no_reds_pipe")
    assert 'if (course) return { name: "No Reds", courseName: course.name };' \
        in entitysection, (
        "the literal 'No Reds' display name is gone from entitysection.js")
    body = _log_card_body()
    assert re.search(r"\(t\.view\.catalog \|\| \{\}\)\.courses \|\| \[\]",
                      body), (
        "LogCard no longer passes the session's catalog.courses into "
        "displayName")


def test_the_reds_stars_own_card_also_reads_star_or_pipe():
    """Round 2 part 2, the STAR half (live report 2026-07-31): with Star
    selected on the Reds cell, the pinned card read a bare "8 Red Coins"
    while the cell itself and the Pipe-mode card both already spelled out
    the family -- the star card disagreeing with its own cell is the same
    bug one surface later. `sec.pipe_segment_id` is the discriminator
    ALREADY on every star section (views.py, non-null only for a Bowser
    course's star 0) -- no new server field, and familyLabel is the SAME
    composer the Pipe half and the cell's own toggle call, now applied
    inside entitysection.js::displayName rather than a section builder."""
    body = _log_card_body()
    assert re.search(r"displayName\(sec,\s*"
                      r"\(t\.view\.catalog \|\| \{\}\)\.courses \|\| \[\]\)",
                      body), (
        "LogCard no longer asks displayName for its heading text")
    assert "text=${named.name}" in body, (
        "the card heading no longer renders displayName's resolved name "
        "(ShrinkToFitName's own text prop)")
    entitysection = _entitysection_source()
    assert re.search(
        r"name: sec\.pipe_segment_id != null\s*"
        r"\? familyLabel\(sec\.star_name, false\) : sec\.star_name,",
        entitysection), (
        "entitysection.js's displayName no longer gates a star's name on "
        "sec.pipe_segment_id / composes it via familyLabel")


def test_an_ordinary_star_keeps_its_own_raw_name():
    """Regression guard: an ordinary star (sec.pipe_segment_id is null/
    undefined) must still show its own plain star_name -- this fix must
    only touch a Bowser course's reds star, never every star's heading."""
    entitysection = _entitysection_source()
    assert "sec.pipe_segment_id != null" in entitysection, (
        "displayName lost its pipe_segment_id guard -- every star would "
        "render with a family suffix")
    assert ": sec.star_name," in entitysection, (
        "the star name lost its fallback to the plain star_name")


def test_an_ordinary_segment_keeps_its_own_raw_identity():
    """Regression guard: a segment with no pipe pairing (sec.pipe_star_entity
    is null/undefined) and no no-reds flag must still show its own corpus
    name and the plain "Segment"/"History only" context -- this fix must
    not repaint every segment card, only the two families it names."""
    entitysection = _entitysection_source()
    # `family` is null whenever both segmentFamily gates are falsy, and
    # displayName's `family ? family.name : sec.name` / `: "Segment"` are the
    # fallback branches asserted elsewhere in this file; this test exists so
    # a future edit that hardcodes either family voice unconditionally
    # (dropping a guard or the `: sec.name` fallback) is caught here rather
    # than only by a live report on an ordinary movement.
    assert "if (sec.pipe_star_entity) {" in entitysection, (
        "segmentFamily lost its pipe_star_entity guard -- every segment "
        "would render as a Bowser Reds pipe card")
    assert "if (sec.is_no_reds_pipe) {" in entitysection, (
        "segmentFamily lost its is_no_reds_pipe guard -- every segment "
        "would render as No Reds")
    assert "name: family ? family.name : sec.name," in entitysection, (
        "displayName lost its fallback to sec.name for ordinary segments")
