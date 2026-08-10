# tests/test_subsection_parent.py
"""`SegmentDef.parents` — the one field that makes a segment a subsection.

Not a third kind of practiced thing. Attempts, personal bests, strategies,
ladders, ranks, the practice log and the matcher are all already
kind-dispatched between stars and segments (rule 11); a third kind fans out
across roughly twenty files and buys nothing. This field is the whole
difference, and it is what the selector filters on for progressive disclosure
and what lets a castle MOVEMENT own subsections of its own.

PLURAL since round 20 — his ask, from the first real star-subsection session:
"sometimes the same subsection might be practicable in multiple stars (e.g.,
in LLL, both Hot Foot it Into The Volcano and Elevator Tour into the Volcano
would do volcano entry in the same way)". [] and absence both mean top-level,
exactly as None did for the scalar.
"""
import pytest

from sm64_events.tracking.segments import SegmentDef, validate_definition


def definition(**overrides) -> dict:
    d = {"name": "a subsection",
         "start_triggers": [{"type": "moment_reached", "kind": "door_open",
                             "level": 6}],
         "end_triggers": [{"type": "moment_reached", "kind": "door_open",
                           "level": 6, "ordinal": 5}],
         "guards": []}
    d.update(overrides)
    return d


def test_a_definition_without_parents_is_top_level():
    """Every definition that existed before 2026-08-05 is one of these, which
    is why the field is defaulted rather than positional -- a non-default
    field would TypeError every existing SegmentDef(...) construction, the
    same reason waypoints, default_strat and match_mode are defaulted."""
    d = SegmentDef(id=1, name="x", enabled=True, start_triggers=[],
                   end_triggers=[], guards=[])
    assert d.parents == []


def test_a_star_parent_validates():
    validate_definition(definition(parents=["star:2:1"]))


def test_a_segment_parent_validates():
    """A castle MOVEMENT owns subsections through the identical field --
    "castle movement sometimes is a specific subsection of castle movement
    rather than movement between courses only" (task 0087)."""
    validate_definition(definition(parents=["segment:7"]))


def test_no_parents_validates():
    validate_definition(definition())
    validate_definition(definition(parents=[]))
    validate_definition(definition(parents=None))


def test_two_parents_validate():
    """Round 20 item 1, his example pair: LLL's Hot-Foot-It and Elevator Tour
    both own "Volcano Entry", so one piece lists both stars."""
    validate_definition(definition(parents=["star:22:0", "star:22:1"]))


def test_a_castle_area_parent_validates():
    """Round 14, his ruling on the parent picker: "for the castle areas,
    those are the high level areas, so it shouldn't have a further drill
    down… Or, it's a castle movement which is high level" — a castle-side
    piece parents to its AREA, so the area is a parent kind of its own.
    Both shapes: a castle subarea node ("area:6:1", the Lobby) and a
    whole-level node ("area:16", the Grounds)."""
    validate_definition(definition(parents=["area:6:1"]))
    validate_definition(definition(parents=["area:16"]))


@pytest.mark.parametrize("bad", [
    "whomps fortress",       # a name, not a key
    "star:2",                # a star key needs both course and slot
    "star:2:1:0",            # too many parts
    "segment:",              # no id
    "segment:abc",           # not an id
    "Star:2:1",              # the prefix is lower-case
    "route:3",               # a route is not a practiced entity
    "area:",                 # no node
    "area:6:1:2",            # too many parts for a node
    "area:lobby",            # a node is numeric, not a name
    7,                       # a bare id says nothing about which kind
])
def test_a_malformed_parent_is_refused(bad):
    with pytest.raises(ValueError, match="parent"):
        validate_definition(definition(parents=[bad]))


def test_a_bare_string_is_refused():
    """The scalar form is GONE; a caller still sending one gets told, not
    silently iterated character by character."""
    with pytest.raises(ValueError, match="list"):
        validate_definition(definition(parents="star:2:1"))


def test_a_duplicate_parent_is_refused():
    """Listing one star twice says nothing a single listing does not, and a
    membership check that counts would double it."""
    with pytest.raises(ValueError, match="twice"):
        validate_definition(definition(parents=["star:2:1", "star:2:1"]))


def test_the_error_names_the_value_it_refused():
    """A 409 that does not say WHAT was wrong sends the user back to guessing."""
    with pytest.raises(ValueError, match="whomps"):
        validate_definition(definition(parents=["whomps fortress"]))


def test_the_parent_key_format_matches_what_the_library_maps_to():
    """sheet-library's mapping module turns a community sheet label into
    `star:c:s` or `segment:id`. A subsection IS a `segment:id`, so the two
    branches meet with no bridge: this tooling makes the entities, that
    mapper already knows how to name them. Pinned so a format drift here
    breaks loudly rather than silently orphaning 145 unmapped sheet rows."""
    validate_definition(definition(parents=["star:15:6"]))
    validate_definition(definition(parents=["segment:84"]))
