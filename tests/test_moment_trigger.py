# tests/test_moment_trigger.py
"""The `moment_reached` trigger type and the registries it must appear in.

The per-module completeness guards (test_eventlabel.py, test_synthesize.py)
already fail when a new TriggerType lands with no row of their own — those are
not restated here. What IS here is what they cannot say: that a moment clause
survives validation at all (its `kind` is a STRING, and every param before it
was an integer id), and that `step_node` places it.
"""
import pytest

from sm64_events.detectors.moment import MOMENTS
from sm64_events.tracking import segments as S


def moment_clause(**overrides) -> dict:
    clause = {"type": "moment_reached", "kind": "door_open", "level": 6}
    clause.update(overrides)
    return clause


def definition(**overrides) -> dict:
    d = {"name": "a subsection", "start_triggers": [moment_clause()],
         "end_triggers": [moment_clause(ordinal=2)], "guards": []}
    d.update(overrides)
    return d


# -- placement ----------------------------------------------------------------

def test_a_moment_clause_resolves_to_a_world_node():
    """step_node answering None means UNCONSTRAINED, which switches the
    topological wrong-turn cancel OFF for whatever uses the clause -- the
    silent, total failure task 0081 documents. A moment names where it
    happens, so it must place."""
    assert S.step_node(moment_clause(level=6, area=1)) is not None
    assert S.step_node(moment_clause(level=24, area=None)) is not None


def test_a_moment_with_no_level_stays_unconstrained():
    """`level` is optional -- a subsection may say "any door" -- and an
    unplaced clause is the codebase's existing "no constraint" answer, not a
    bug. Stated so the None above is never read as a failure."""
    assert S.step_node({"type": "moment_reached", "kind": "door_open"}) is None


def test_the_origin_and_precondition_tables_both_place_a_moment():
    assert S._ORIGIN_PARAMS["moment_reached"] == ("level", "area")
    assert S._PRECONDITION_PARAM["moment_reached"] == "level"


def test_a_moment_subsection_can_be_run_from_where_it_fires():
    assert S.fires_from(moment_clause(level=6), 6) is True
    assert S.fires_from(moment_clause(level=6), 24) is False


# -- validation ---------------------------------------------------------------

def test_a_moment_kind_is_a_string_and_still_validates():
    """Every trigger param before this one was an integer id, so the clause
    checker demanded `isinstance(value, int)` outright. A moment kind is a
    name from the MOMENTS registry; the checker dispatches on the param's own
    declared kind now rather than assuming."""
    S.validate_definition(definition())


def test_an_unknown_moment_kind_is_refused():
    with pytest.raises(ValueError, match="door_opne|unknown moment"):
        S.validate_definition(definition(
            start_triggers=[moment_clause(kind="door_opne")]))


def test_a_moment_kind_that_is_an_integer_is_refused():
    with pytest.raises(ValueError):
        S.validate_definition(definition(
            start_triggers=[moment_clause(kind=3)]))


def test_the_ordinal_must_still_be_an_integer():
    with pytest.raises(ValueError, match="ordinal"):
        S.validate_definition(definition(
            start_triggers=[moment_clause(ordinal="fifth")]))


def test_every_other_trigger_still_refuses_a_string_param():
    """The validator was loosened for ONE declared param kind, not generally
    -- a level is still an id and a string level is still a mistake."""
    with pytest.raises(ValueError, match="integer"):
        S.validate_definition(definition(
            start_triggers=[{"type": "level_enter", "to": "castle"}]))


# -- matching -----------------------------------------------------------------

class _Ev:
    def __init__(self, payload):
        self.type = "moment_reached"
        self.payload = payload


def match(clause, payload) -> bool:
    return S.TRIGGERS["moment_reached"].match(clause, _Ev(payload), None)


def test_a_moment_matches_its_own_kind_only():
    payload = {"kind": "door_open", "ordinal": 1, "level": 6, "area": 1}
    assert match(moment_clause(level=6), payload) is True
    assert match(moment_clause(kind="textbox", level=6), payload) is False


def test_an_unset_ordinal_matches_any_occurrence():
    clause = moment_clause(level=6)
    for ordinal in (1, 5, 40):
        assert match(clause, {"kind": "door_open", "ordinal": ordinal,
                              "level": 6, "area": 1}) is True


def test_a_set_ordinal_matches_only_that_occurrence():
    """The fifth door in Big Boo's Haunt is a start trigger, and a start has
    no arm to count from -- which is the whole reason ordinals exist."""
    clause = moment_clause(level=4, ordinal=5)
    assert match(clause, {"kind": "door_open", "ordinal": 5, "level": 4}) is True
    assert match(clause, {"kind": "door_open", "ordinal": 4, "level": 4}) is False


def test_a_moment_in_the_wrong_place_does_not_match():
    clause = moment_clause(level=6)
    assert match(clause, {"kind": "door_open", "ordinal": 1, "level": 4}) is False


# -- the builder's dropdown ---------------------------------------------------

def test_vocab_serves_every_moment_kind_with_its_label():
    served = S.vocab()["moments"]
    assert {row["key"] for row in served} == {m.kind for m in MOMENTS}
    assert all(row["label"] for row in served)


# -- in-course subareas (task 0087: "entering a subarea within the level") ----

def test_a_non_castle_subarea_is_expressible():
    """SSL's pyramid interior is area 2 of level 8, and "entering a subarea
    within the level" is one of the conditions subsections are built out of.
    The MATCHER never had a castle restriction -- its lambda compares the
    payload's level to the clause's -- but the VOCABULARY gated both the
    level list and the subarea selectors, so the clause could not be
    authored at all."""
    level_param = S.TRIGGERS["area_enter"].params["level"]
    assert "enum" not in level_param, \
        "area_enter's level list is pinned to castle levels"


def test_a_subarea_selector_never_shows_where_it_means_nothing():
    """CORRECTED 2026-08-05 after a live report, and this test asserted the
    opposite for a few hours.

    Dropping `only_when` alongside the enum made the builder offer a subarea
    dropdown for EVERY level -- and the only subarea names the vocabulary
    has are the castle interior's, so picking Shifting Sand Land offered
    "Lobby / Upstairs / Basement". Worse than not offering it, and squarely
    the "I don't understand the builder" complaint this round is about.

    Dropping the level ENUM is what makes an in-course subarea authorable at
    all, and that stays. What is still NOT authorable by NAME is WHICH
    subarea: nothing names a course's own areas. The record-what-I-just-did
    path synthesizes those from real play instead, which is the path this
    feature wants people on anyway.
    """
    for key, params in (("area_enter", ("area", "from")),
                        ("moment_reached", ("area",))):
        for name in params:
            assert "only_when" in S.TRIGGERS[key].params[name], \
                f"{key}.{name} would draw a castle dropdown for any level"


def test_the_matcher_accepts_a_course_subarea_clause():
    clause = {"type": "area_enter", "level": 8, "area": 2}
    S.validate_definition(definition(start_triggers=[clause]))

    class _AreaEv:
        type = "area_changed"
        payload = {"level": 8, "to": 2, "from": 1}

    assert S.TRIGGERS["area_enter"].match(clause, _AreaEv(), None) is True


def test_a_course_subarea_clause_places_for_the_topological_cancel():
    """topology.node_for deliberately counts subareas only INSIDE the castle
    interior -- courses have their own areas and the world graph does not
    model them. So this resolves to the LEVEL node, which is a real answer
    and not a gap: it keeps the wrong-turn rule working at level
    granularity rather than switching it off."""
    assert S.step_node({"type": "area_enter", "level": 8, "area": 2}) is not None
