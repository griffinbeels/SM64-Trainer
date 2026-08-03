"""One answer per question about a practice section, whatever kind it is.

ui/entitysection.js, driven through node. StarSection and SegmentSection each
wrote these five answers out by hand, which is the exact shape rule 11 exists
to stop -- and the shared LogCard cannot be written until they agree by
construction rather than by a test comparing two copies.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
UI = REPO / "src" / "sm64_events" / "ui"
ENTITYSECTION_JS = (UI / "entitysection.js").as_uri()

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")

STAR = {"course_name": "Shifting Sand Land", "star_name": "Pyramid Puzzle",
        "course_id": 8, "star_id": 5, "pipe_segment_id": None,
        "pb": {"igt": {"display": "0'16\"70"}, "rta": {"display": "0'17\"10"}}}
SEGMENT = {"kind": "segment", "segment_id": 12, "name": "DDD -> BitFS",
           "broken": False, "pipe_star_entity": None, "is_no_reds_pipe": False,
           "course_id": None, "pb": {"rta": {"display": "0'26\"13"}}}


def call(fn: str, *args: object) -> object:
    """One exported function, one call, JSON in and JSON out."""
    script = (f"import * as mod from {ENTITYSECTION_JS!r};\n"
              f"console.log(JSON.stringify(mod.{fn}("
              + ",".join(json.dumps(a) for a in args) + ")));")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_a_star_section_is_not_a_segment_even_though_it_names_no_kind():
    """views.py omits `kind` from star sections deliberately, so the branch
    must be `=== "segment"` and never `=== "star"`."""
    assert call("isSegment", STAR) is False
    assert call("isSegment", SEGMENT) is True


def test_entity_keys_match_what_every_other_surface_builds():
    assert call("entityKey", STAR) == "star:8:5"
    assert call("entityKey", SEGMENT) == "segment:12"


def test_a_segment_is_measured_on_rta_whatever_clock_the_view_is_on():
    """Segments are RTA-only by design -- igt is null everywhere on them."""
    assert call("sectionClock", SEGMENT, "igt") == "rta"
    assert call("sectionClock", STAR, "igt") == "igt"
    assert call("sectionClock", STAR, "rta") == "rta"


def test_the_pb_follows_that_same_clock():
    assert call("sectionPb", SEGMENT, "igt") == {"display": "0'26\"13"}
    assert call("sectionPb", STAR, "igt") == {"display": "0'16\"70"}


def test_an_ordinary_star_and_segment_name_themselves_plainly():
    assert call("displayName", STAR, []) == {
        "context": "Shifting Sand Land", "name": "Pyramid Puzzle"}
    assert call("displayName", SEGMENT, []) == {
        "context": "Segment", "name": "DDD -> BitFS"}


def test_a_bowser_reds_star_is_named_in_the_family_voice():
    """`pipe_segment_id` is the exact discriminator -- views.py stamps it on
    star 0 of a Bowser course only, and the cell that selects it already
    reads "8 Red Coins (Star)"."""
    reds = {**STAR, "course_name": "Bowser in the Dark World",
            "star_name": "8 Red Coins", "star_id": 0, "pipe_segment_id": 7}
    assert call("displayName", reds, []) == {
        "context": "Bowser in the Dark World", "name": "8 Red Coins (Star)"}


def test_a_paired_pipe_segment_borrows_its_stars_name_and_course():
    paired = {**SEGMENT, "name": "BitDW - 8 Red Coins -> Pipe",
              "pipe_star_entity": "star:16:0", "pipe_star_name": "8 Red Coins",
              "pipe_star_course_name": "Bowser in the Dark World"}
    assert call("displayName", paired, []) == {
        "context": "Bowser in the Dark World", "name": "8 Red Coins (Pipe)"}


def test_the_legacy_no_reds_segment_resolves_its_course_from_the_catalog():
    """It has no paired star to borrow a name from, so the course comes from
    its own `course_id` through the session's `catalog.courses`."""
    no_reds = {**SEGMENT, "name": "BitDW Pipe Entry", "is_no_reds_pipe": True,
               "course_id": 16}
    courses = [{"id": 16, "name": "Bowser in the Dark World"}]
    assert call("displayName", no_reds, courses) == {
        "context": "Bowser in the Dark World", "name": "No Reds"}


def test_a_deleted_definition_says_so_and_outranks_the_family_voice():
    """`broken` wins the context line, matching SegmentSection's own order."""
    broken = {**SEGMENT, "broken": True, "pipe_star_entity": "star:16:0",
              "pipe_star_name": "8 Red Coins",
              "pipe_star_course_name": "Bowser in the Dark World"}
    assert call("displayName", broken, [])["context"] == "History only"
