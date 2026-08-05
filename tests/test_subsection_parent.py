# tests/test_subsection_parent.py
"""`SegmentDef.parent` — the one field that makes a segment a subsection.

Not a third kind of practiced thing. Attempts, personal bests, strategies,
ladders, ranks, the practice log and the matcher are all already
kind-dispatched between stars and segments (rule 11); a third kind fans out
across roughly twenty files and buys nothing. This field is the whole
difference, and it is what the selector filters on for progressive disclosure
and what lets a castle MOVEMENT own subsections of its own.
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


def test_a_definition_without_a_parent_is_top_level():
    """Every definition that existed before 2026-08-05 is one of these, which
    is why the field is defaulted rather than positional -- a non-default
    field would TypeError every existing SegmentDef(...) construction, the
    same reason waypoints, default_strat and match_mode are defaulted."""
    d = SegmentDef(id=1, name="x", enabled=True, start_triggers=[],
                   end_triggers=[], guards=[])
    assert d.parent is None


def test_a_star_parent_validates():
    validate_definition(definition(parent="star:2:1"))


def test_a_segment_parent_validates():
    """A castle MOVEMENT owns subsections through the identical field --
    "castle movement sometimes is a specific subsection of castle movement
    rather than movement between courses only" (task 0087)."""
    validate_definition(definition(parent="segment:7"))


def test_no_parent_validates():
    validate_definition(definition())
    validate_definition(definition(parent=None))


@pytest.mark.parametrize("bad", [
    "whomps fortress",       # a name, not a key
    "star:2",                # a star key needs both course and slot
    "star:2:1:0",            # too many parts
    "segment:",              # no id
    "segment:abc",           # not an id
    "Star:2:1",              # the prefix is lower-case
    "route:3",               # a route is not a practiced entity
    7,                       # a bare id says nothing about which kind
])
def test_a_malformed_parent_is_refused(bad):
    with pytest.raises(ValueError, match="parent"):
        validate_definition(definition(parent=bad))


def test_the_error_names_the_value_it_refused():
    """A 409 that does not say WHAT was wrong sends the user back to guessing."""
    with pytest.raises(ValueError, match="whomps"):
        validate_definition(definition(parent="whomps fortress"))


def test_the_parent_key_format_matches_what_the_library_maps_to():
    """sheet-library's mapping module turns a community sheet label into
    `star:c:s` or `segment:id`. A subsection IS a `segment:id`, so the two
    branches meet with no bridge: this tooling makes the entities, that
    mapper already knows how to name them. Pinned so a format drift here
    breaks loudly rather than silently orphaning 145 unmapped sheet rows."""
    validate_definition(definition(parent="star:15:6"))
    validate_definition(definition(parent="segment:84"))
