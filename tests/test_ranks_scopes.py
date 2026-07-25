from sm64_events.ranks.scopes import (
    aggregate, celebration_delta, entity_groups, gain_for, rankable_entities,
    scope_list)
from sm64_events.ranks.scoring import progression_key

LADDERS = {"star:1:0": {"Standard": {"Mario": 45.4}},
           "star:1:1": {"Standard": {"Mario": 30.0}},
           "star:2:0": {"Standard": {"Mario": 20.0}},
           "segment:5": {"Standard": {"Mario": 10.0}},
           "segment:99": {}}                       # has an entry but no ladder

ROUTES = [{"id": 3, "name": "16 Star", "steps": [
    {"need": 1, "candidates": [{"type": "star", "course": 1, "star": 0}]},
    {"need": 1, "candidates": [{"type": "star", "course": 1, "star": 1},
                               {"type": "star", "course": 2, "star": 0}]},
    {"need": 1, "candidates": [{"type": "segment", "segment_id": 42}]},
]}]
SEGMENT_COURSES = {5: 16}


def test_rankable_skips_ladderless_entities_and_exclusions():
    assert rankable_entities(LADDERS) == [
        "star:1:0", "star:1:1", "star:2:0", "segment:5"]
    assert "star:1:1" not in rankable_entities(LADDERS, excluded={"star:1:1"})


def test_rankable_skips_a_strategy_named_with_no_cutoffs():
    """`RankStandards.create_strategy` writes `{strat: {}}` -- naming a
    strategy purely to tag attempts (the ordinary practice-card flow) must
    not make the entity rankable. A non-empty STRATEGIES dict with an empty
    cutoff dict inside it has no ladder (`best_ladder` collapses it to `{}`)
    and every scoring path needs `best_ladder` non-empty
    (`tracking/marelo.py`); admitting it here would hold a permanent,
    unscoreable denominator slot -- absent, not zero, is the contract."""
    assert rankable_entities({"star:1:0": {"Fast": {}}}) == []


def test_overall_scope_is_one_group_per_entity():
    groups = entity_groups("overall", rankable=rankable_entities(LADDERS),
                           routes=ROUTES, segment_courses=SEGMENT_COURSES)
    assert groups == [{"need": 1, "candidates": [k]}
                      for k in rankable_entities(LADDERS)]


def test_course_scope_takes_its_stars_and_its_segments():
    groups = entity_groups("course:16", rankable=rankable_entities(LADDERS),
                           routes=ROUTES, segment_courses=SEGMENT_COURSES)
    assert groups == [{"need": 1, "candidates": ["segment:5"]}]
    groups = entity_groups("course:1", rankable=rankable_entities(LADDERS),
                           routes=ROUTES, segment_courses=SEGMENT_COURSES)
    assert [g["candidates"][0] for g in groups] == ["star:1:0", "star:1:1"]


def test_route_scope_keeps_k_of_n_and_drops_unrankable_candidates():
    groups = entity_groups("route:3", rankable=rankable_entities(LADDERS),
                           routes=ROUTES, segment_courses=SEGMENT_COURSES)
    # step 3's segment 42 has no standards -> the whole step drops
    assert groups == [
        {"need": 1, "candidates": ["star:1:0"]},
        {"need": 1, "candidates": ["star:1:1", "star:2:0"]},
    ]


def test_unknown_scope_is_none():
    assert entity_groups("route:999", rankable=[], routes=ROUTES,
                         segment_courses={}) is None
    assert entity_groups("nonsense", rankable=[], routes=ROUTES,
                         segment_courses={}) is None


def test_scope_list_offers_overall_then_routes_then_courses():
    scopes = scope_list(routes=ROUTES, courses={1: "Bob-omb Battlefield"})
    # Full-list equality, not just membership, so a reordering (e.g. courses
    # before routes) fails this test instead of slipping through.
    assert scopes == [
        {"id": "overall", "label": "Overall", "kind": "overall"},
        {"id": "route:3", "label": "16 Star", "kind": "route"},
        {"id": "course:1", "label": "Bob-omb Battlefield", "kind": "course"},
    ]


def test_aggregate_counts_unpracticed_as_zero_in_the_denominator():
    groups = [{"need": 1, "candidates": ["a"]}, {"need": 1, "candidates": ["b"]}]
    out = aggregate({"a": 60.0}, groups)
    assert out["n"] == 2 and out["practiced"] == 1
    assert out["mastery"] == 60.0
    assert out["coverage"] == 0.5
    assert out["marelo"] == 30.0                     # mastery * coverage


def test_aggregate_takes_the_best_k_of_a_group():
    groups = [{"need": 1, "candidates": ["a", "b"]}]
    out = aggregate({"a": 20.0, "b": 80.0}, groups)
    assert out["n"] == 1 and out["marelo"] == 80.0
    assert [e["key"] for e in out["entities"]] == ["b"]


def test_aggregate_counts_a_genuine_zero_as_practiced():
    """A candidate present in `scores` with value 0.0 is a real (if brutal)
    practiced run, not an absent one. `practiced` must be counted by presence
    in `scores` (`score is not None`), never by truthiness (`if score`) --
    the latter would silently reclassify this scored zero as unpracticed and
    corrupt coverage, which is half of MARELO = mastery * coverage."""
    groups = [{"need": 1, "candidates": ["a"]}, {"need": 1, "candidates": ["b"]}]
    out = aggregate({"a": 0.0}, groups)
    assert out["n"] == 2 and out["practiced"] == 1
    assert out["coverage"] == 0.5
    assert out["mastery"] == 0.0
    assert out["marelo"] == 0.0


def test_aggregate_best_k_prefers_a_scored_zero_over_an_absent_candidate():
    """The best-k sort must rank a genuine 0.0 above an absent candidate.
    Coalescing a missing score to -1.0 (never `score or -1.0`, which treats
    0.0 itself as missing) is what guarantees this; `absent` is listed FIRST
    so a broken (tied) sort would keep it first via stability, exposing the
    bug instead of hiding it behind incidental list order."""
    groups = [{"need": 1, "candidates": ["absent", "zero"]}]
    out = aggregate({"zero": 0.0}, groups)
    assert out["practiced"] == 1 and out["coverage"] == 1.0
    assert [entity["key"] for entity in out["entities"]] == ["zero"]


def test_aggregate_of_an_empty_scope_is_none_not_zero():
    out = aggregate({}, [])
    assert out["marelo"] is None and out["n"] == 0 and out["entities"] == []


def test_aggregate_reports_tier_and_division():
    # Gold spans 45-60 in 5 equal-width divisions (V..I): 50.0 lands in
    # 48-51, i.e. division IV -- pinned independently by
    # test_ranks_scoring.py::division_for(48.0) == ("Gold", "IV").
    out = aggregate({"a": 50.0}, [{"need": 1, "candidates": ["a"]}])
    assert out["tier"] == "Gold" and out["division"] == "IV"


def test_aggregate_reports_progress_through_the_current_division():
    """The header track needs depth into the CURRENT division, and only the
    server knows the band edges -- duplicating the math in JS would let the
    two drift."""
    # Gold spans 45-60, so division III is 51-54: 52.5 is halfway through it.
    out = aggregate({"a": 52.5}, [{"need": 1, "candidates": ["a"]}])
    assert out["next_division_at"] == 54.0
    assert out["division_progress"] == 0.5
    # bottom of a division reads 0, not 1
    assert aggregate({"a": 51.0}, [{"need": 1, "candidates": ["a"]}])[
        "division_progress"] == 0.0


def test_gain_is_the_marelo_the_next_tier_on_this_entity_is_worth():
    assert gain_for(50.0, 10) == (60.0 - 50.0) / 10   # Gold -> Platinum
    assert gain_for(None, 10) == 45.0 / 10            # unpracticed -> reach Gold
    assert gain_for(96.0, 10) == (100.0 - 96.0) / 10  # top tier still has room


def test_celebration_fires_only_on_a_rise_and_reports_the_span():
    below = progression_key("Silver", "II")
    out = celebration_delta("Gold", "V", below)
    assert out["from"] == {"tier": "Silver", "division": "II"}
    assert out["to"] == {"tier": "Gold", "division": "V"}
    assert out["tiers_gained"] == 1
    assert celebration_delta("Silver", "II", below) is None      # unchanged
    assert celebration_delta("Bronze", "I", below) is None        # a drop


def test_first_ever_rank_is_not_a_celebration():
    assert celebration_delta("Gold", "V", None) is None
