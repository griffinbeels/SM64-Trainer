"""Full-node Overall scoring, attainable goals, and unchanged manual ladders."""
import copy
import json
import math

import pytest

from sm64_events.core.timefmt import cs_of_frame, frame_at_or_after
from sm64_events.ranks import scoring
from sm64_events.ranks.curves import (compile_curve, from_ladder, progress_for_time,
                                      score_evaluator, score_for, time_for_score, with_anchors)
from sm64_events.ranks.timecurve import frame_position


def division_scores():
    return [low + i * (high - low) / 5 for tier in reversed(scoring.RANK_NAMES)
            for low, high in [scoring.tier_band(tier)] for i in range(5)]


def calibrated_nodes(phase=0, narrow=False):
    """Full division calibration across a realistic 6s-to-30s progression.

    Consecutive frames exercise the timer's 3,3,4cs phases independently of
    the broader example's changing per-frame gains at strategy transitions.
    """
    frame = 180 + phase
    result = [[cs_of_frame(frame), 100.]]
    for i, score in enumerate(reversed(division_scores()[1:])):
        frame += 1 if narrow else 1 + i // 3
        result.append([cs_of_frame(frame), score])
    return result


def test_full_nodes_survive_json_and_define_intermediate_scores():
    nodes = [[1000, 100], [1100, 90], [1300, 10]]
    curve = compile_curve(nodes, {"model": "fixture", "samples": {"count": 7}})
    assert json.loads(json.dumps(curve)) == curve
    assert curve["nodes"] == nodes
    # Hand-calculated cubic Hermite example in frame positions 300,330,390.
    # The middle slope is -.5/frame, the final slope -2/frame.
    assert score_for(curve, 1200) == pytest.approx(61.25)
    assert score_for(curve, 1200) != pytest.approx(scoring.score_for(curve["ladder_cs"], 1200))
    assert curve["ladder_cs"] == {rank: time_for_score(curve, score)
                                  for rank, score in scoring.SCORE_ANCHORS.items()}
    nodes[0][0] = 999
    assert curve["nodes"][0][0] == 1000


@pytest.mark.parametrize("curve", [
    compile_curve([[1000, 100], [1100, 90], [1300, 10], [2000, 2]]),
    from_ladder({"Mario": 1001, "Gold": 1800, "Bronze": 2400}),
    from_ladder({}),
])
def test_bound_evaluator_preserves_scores_errors_and_detaches_mutable_inputs(curve):
    original = copy.deepcopy(curve)
    evaluate = score_evaluator(curve)
    for time in [-1, 0, .1, 999, 1000, 1001.5, 1200, 2000, 50000,
                 1e100, True, None, "1000", float("nan"), float("inf")]:
        try:
            expected = score_for(original, time)
        except ValueError as error:
            with pytest.raises(ValueError) as actual:
                evaluate(time)
            assert actual.value.args == error.args
        else:
            assert evaluate(time) == expected
    curve["ladder_cs"].clear()
    if curve["nodes"]:
        curve["nodes"][0][0] = 2
        curve["nodes"][1][1] = 99
    curve["nodes"].clear()
    curve["metadata"] = None
    assert evaluate(1200) == score_for(original, 1200)
    with pytest.raises(ValueError, match="metadata"):
        score_for(curve, 1200)


@pytest.mark.parametrize("field,value", [
    ("schema_version", 2), ("interpolation", "formula"),
    ("metadata", None), ("ladder_cs", {"Other": 100}),
    ("nodes", [[1000, 90], [999, 95]]),
])
def test_bound_evaluator_rejects_invalid_curves_at_binding(field, value):
    curve = compile_curve([[1000, 100], [1100, 90], [1300, 10]])
    curve[field] = value
    with pytest.raises(ValueError) as ordinary:
        score_for(curve, 1200)
    with pytest.raises(ValueError) as bound:
        score_evaluator(curve)
    assert bound.value.args == ordinary.value.args


@pytest.mark.parametrize("phase", range(30))
@pytest.mark.parametrize("narrow", [False, True])
def test_every_division_is_reachable_and_every_printed_goal_earns_its_score(phase, narrow):
    curve = compile_curve(calibrated_nodes(phase, narrow))
    reached = set()
    for target in division_scores()[1:] + [100]:
        time = time_for_score(curve, target)
        assert time is not None and time >= 0
        frame = frame_at_or_after(time)
        assert cs_of_frame(frame) == time
        assert score_for(curve, time) >= target
        assert score_for(curve, cs_of_frame(frame + 1)) < target
        progress = progress_for_time(curve, time)
        assert progress["score"] == score_for(curve, time)
        reached.add((progress["tier"], progress["division"]))
        if progress["next_at"] is not None:
            assert progress["next_gap_cs"] > 0
    last = curve["nodes"][-1][0]
    floor = progress_for_time(curve, last * 100)
    reached.add((floor["tier"], floor["division"]))
    assert reached == {(tier, division) for tier in scoring.RANK_NAMES
                       for division in scoring.DIVISION_NUMERALS}
    for rank, cutoff in curve["ladder_cs"].items():
        assert scoring.tier_from_score(score_for(curve, cutoff)) == rank


def test_continuous_monotonicity_and_endpoint_smoothness():
    curve = compile_curve([[1000, 100], [1100, 90], [1300, 10], [2000, 2]])
    times = [i / 4 for i in range(1, 16000)]
    scores = [score_for(curve, time) for time in times]
    assert all(0 < score <= 100 for score in scores)
    assert all(fast >= slow for fast, slow in zip(scores, scores[1:], strict=False))
    for time, _ in curve["nodes"][1:]:
        delta = .0001
        center = score_for(curve, time)
        before = (center - score_for(curve, time - delta)) / (frame_position(time) - frame_position(time - delta))
        after = (score_for(curve, time + delta) - center) / (frame_position(time + delta) - frame_position(time))
        assert before == pytest.approx(after, abs=1e-5)
    assert score_for(curve, 1e308) > 0


def test_fractional_nodes_and_off_grid_inverse_never_round_onto_a_missed_goal():
    curve = compile_curve([[888.5, 100], [919.2, 95], [1234.8, 30], [2345.1, 2]])
    for target in [i / 8 for i in range(1, 801)]:
        goal = time_for_score(curve, target)
        assert goal is not None
        assert goal == cs_of_frame(frame_at_or_after(goal))
        assert score_for(curve, goal) >= target
        assert score_for(curve, cs_of_frame(frame_at_or_after(goal) + 1)) < target
    assert time_for_score(curve, 0) is None
    assert time_for_score(curve, -1) is None
    assert time_for_score(curve, 101) is None


@pytest.mark.parametrize("ladder", [
    {}, {"Mario": 885}, {"Bronze": 940},
    {"Mario": 885, "Grandmaster": 910, "Bronze": 940},
    {"Mario": 246, "Grandmaster": 246, "Master": 300},
    {"Mario": 246, "Grandmaster": 300, "Master": 300},
    {"Mario": 246, "Grandmaster": 246, "Bronze": 246},
])
def test_legacy_matches_the_existing_real_implementation_exactly(ladder):
    curve = from_ladder(ladder)
    for time in [0, 1, 245, 246, 247, 299, 300, 885, 886, 940, 1000, 10000]:
        assert score_for(curve, time) == scoring.score_for(ladder, time)
        assert progress_for_time(curve, time) == (scoring.progress_for_time(ladder, time) if ladder else None)
    for target in division_scores() + [100]:
        assert time_for_score(curve, target) == scoring.time_for_score(ladder, target)


@pytest.mark.parametrize("nodes", [None, [], [[100, 95]], [[100, 95, 0], [200, 10]],
    [[100, 95], [100, 10]], [[200, 95], [100, 10]], [[100, 10], [200, 95]],
    [[100, 95], [200, 95]], [[100, 100], [200, 0]], [[-1, 100], [200, 10]],
    [[100, math.inf], [200, 10]], [[True, 100], [200, 10]],
    [[100, 101], [200, 10]], [[100, 100], [math.nan, 10]],
    [[100, 100], ["200", 10]], [[1, 3], [1000, 2]],
])
def test_bad_nodes_are_rejected(nodes):
    with pytest.raises(ValueError):
        compile_curve(nodes)


@pytest.mark.parametrize("patch", [
    {"schema_version": 2}, {"schema_version": True}, {"interpolation": "spline"},
    {"metadata": []}, {"nodes": []}, {"ladder_cs": {}},
    {"ladder_cs": {"Mario": -1}}, {"ladder_cs": {"Iron": 1000}},
])
def test_unsupported_or_malformed_payloads_fail_explicitly(patch):
    curve = compile_curve(calibrated_nodes()) | patch
    for evaluate in (score_for, time_for_score, progress_for_time):
        with pytest.raises(ValueError):
            evaluate(curve, 10)


def test_metadata_is_owned_but_does_not_grade_and_invalid_queries_fail():
    metadata = {"samples": {"count": 10}}
    curve = compile_curve(calibrated_nodes(), metadata)
    original = copy.deepcopy(curve)
    metadata["samples"]["count"] = 0
    assert curve == original
    other = copy.deepcopy(curve)
    other["metadata"] = {"arbitrary": "provenance"}
    assert score_for(curve, 1200) == score_for(other, 1200)
    for query in (math.nan, math.inf, "10", True):
        for evaluate in (score_for, time_for_score, progress_for_time):
            with pytest.raises(ValueError):
                evaluate(curve, query)
    with pytest.raises(ValueError):
        score_for(curve, -1)
    with pytest.raises(ValueError):
        from_ladder({"Mario": 1000, "Bronze": 900})


def test_pins_preserve_unpinned_tiers_and_interior_relative_frame_positions():
    curve = compile_curve(calibrated_nodes())
    original = copy.deepcopy(curve)
    before = curve["ladder_cs"]
    pin = cs_of_frame(frame_at_or_after(before["Silver"]) + 3)
    pinned = with_anchors(curve, {"Silver": pin})
    assert pinned["ladder_cs"] == before | {"Silver": pin}
    assert curve == original
    assert with_anchors(curve, {}) == original
    assert score_for(pinned, pin) == scoring.SCORE_ANCHORS["Silver"]
    old_nodes = {score: frame_position(time) for time, score in curve["nodes"]}
    new_nodes = {score: frame_position(time) for time, score in pinned["nodes"]}
    for score in [29., 33., 37., 41.]:
        old_fraction = ((old_nodes[score] - old_nodes[45.]) / (old_nodes[25.] - old_nodes[45.]))
        new_fraction = ((new_nodes[score] - new_nodes[45.]) / (new_nodes[25.] - new_nodes[45.]))
        assert new_fraction == pytest.approx(old_fraction)


def test_pins_work_when_raw_nodes_do_not_include_tier_anchors():
    curve = compile_curve([[1000, 100], [1200, 75], [2000, 20], [2500, 2]])
    pin = cs_of_frame(frame_at_or_after(curve["ladder_cs"]["Silver"]) + 1)
    pinned = with_anchors(curve, {"Silver": pin})
    assert pinned["ladder_cs"] == curve["ladder_cs"] | {"Silver": pin}
    assert score_for(pinned, pin) == 25


@pytest.mark.parametrize("pin", [{"Silver": 0}, {"Silver": -1}, {"Silver": math.inf},
                                {"Silver": 1021}, {"Iron": 2000}, {"Silver": 1}])
def test_conflicting_or_unattainable_pins_are_rejected(pin):
    with pytest.raises(ValueError):
        with_anchors(compile_curve(calibrated_nodes()), pin)


def test_legacy_pins_retain_literal_cutoffs_and_do_not_mutate():
    curve = from_ladder({"Mario": 885, "Bronze": 940})
    assert with_anchors(curve, {"Mario": 881}) == from_ladder({"Mario": 881, "Bronze": 940})
    assert curve["ladder_cs"] == {"Mario": 885, "Bronze": 940}
    with pytest.raises(ValueError, match="cross"):
        with_anchors(curve, {"Mario": 950})


def test_new_pin_cannot_erase_divisions_even_with_distinct_tier_cutoffs():
    curve = compile_curve(calibrated_nodes())
    pin = cs_of_frame(frame_at_or_after(curve["ladder_cs"]["Gold"]) + 1)
    with pytest.raises(ValueError, match="no attainable"):
        with_anchors(curve, {"Silver": pin})


def assert_complete_curve(curve):
    assert curve["interpolation"] == "pchip"
    for target in division_scores()[1:]:
        goal = time_for_score(curve, target)
        assert goal is not None and goal > 0
        assert goal == cs_of_frame(frame_at_or_after(goal))
        actual = progress_for_time(curve, goal)
        assert (actual["tier"], actual["division"]) == scoring.division_for(target)
    scores = [score_for(curve, cs_of_frame(frame)) for frame in range(1, 1200)]
    assert all(a >= b for a, b in zip(scores, scores[1:], strict=False))
    assert all(0 < score <= 100 for score in scores)


def test_existing_pin_survives_faster_refresh_crossing_it_and_other_tiers_still_evolve():
    old = compile_curve(calibrated_nodes(), {"revision": "old"})
    pin = {"Silver": old["ladder_cs"]["Silver"]}
    refreshed = []
    for shift in (120, 110):
        fresh = compile_curve([[cs_of_frame(frame_at_or_after(time) - shift), score]
                               for time, score in calibrated_nodes()], {"revision": f"fresh-{shift}"})
        original = copy.deepcopy(fresh)
        assert fresh["ladder_cs"]["Bronze"] < pin["Silver"]
        with pytest.raises(ValueError, match="cross"):
            with_anchors(fresh, pin)
        result = with_anchors(fresh, pin, preserve_unpinned=False)
        assert fresh == original
        assert result["ladder_cs"]["Silver"] == pin["Silver"]
        assert result["ladder_cs"]["Mario"] == fresh["ladder_cs"]["Mario"]
        assert {score for _, score in result["nodes"]} >= {score for _, score in fresh["nodes"]}
        provenance = result["metadata"]["anchor_adjustments"]
        assert result["metadata"]["revision"] == f"fresh-{shift}"
        assert provenance["fixed_cs"] == pin
        assert provenance["moved_automatic_cs"]["Bronze"]["generated"] == fresh["ladder_cs"]["Bronze"]
        assert provenance["all_divisions_reachable"]
        assert provenance["fallback_reason"] is None
        assert_complete_curve(result)
        refreshed.append(result)
    assert refreshed[0]["ladder_cs"]["Mario"] != refreshed[1]["ladder_cs"]["Mario"]


def test_relaxed_projection_keeps_multiple_pins_and_fractional_interior_nodes():
    nodes = calibrated_nodes()
    index = next(i for i, (_, score) in enumerate(nodes) if score == 37)
    nodes.insert(index + 1, [(nodes[index][0] + nodes[index + 1][0]) / 2, 35])
    curve = compile_curve(nodes)
    pins = {"Gold": cs_of_frame(frame_at_or_after(curve["ladder_cs"]["Gold"]) - 6),
            "Silver": cs_of_frame(frame_at_or_after(curve["ladder_cs"]["Silver"]) + 6)}
    result = with_anchors(curve, pins, preserve_unpinned=False)
    assert {rank: result["ladder_cs"][rank] for rank in pins} == pins
    assert any(score == 35 for _, score in result["nodes"])
    assert_complete_curve(result)


def test_minimum_feasible_pin_spacing_retains_all_five_divisions_per_tier():
    curve = compile_curve(calibrated_nodes())
    ranks = list(scoring.SCORE_ANCHORS)
    pins = {rank: cs_of_frame(5 + i * 5) for i, rank in enumerate(ranks)}
    result = with_anchors(curve, pins, preserve_unpinned=False)
    assert result["ladder_cs"] == pins
    assert_complete_curve(result)


@pytest.mark.parametrize("pins,reason", [
    ({"Gold": 1000, "Silver": 1000}, "too few"),
    ({"Gold": 1000, "Silver": 1003}, "too few"),
    ({"Mario": 3}, "too few"),
    ({"Silver": 1021}, "whole game-frame"),
])
def test_inherited_unattainable_pins_have_exact_labeled_legacy_fallback(pins, reason):
    curve = compile_curve(calibrated_nodes(), {"revision": "fresh"})
    result = with_anchors(curve, pins, preserve_unpinned=False)
    assert result["interpolation"] == "legacy"
    assert {rank: result["ladder_cs"][rank] for rank in pins} == pins
    assert result["metadata"]["revision"] == "fresh"
    provenance = result["metadata"]["anchor_adjustments"]
    assert provenance["mode"] == "existing_pins"
    assert provenance["fixed_cs"] == pins
    assert reason in provenance["fallback_reason"]
    assert provenance["interpolation"] == "legacy"
    if reason == "too few":
        assert not provenance["all_divisions_reachable"]
        assert provenance["unreachable_divisions"]
    times = list(result["ladder_cs"].values())
    assert times == sorted(times)
    for time in range(1, 2500, 7):
        assert progress_for_time(result, time) == scoring.progress_for_time(result["ladder_cs"], time)


@pytest.mark.parametrize("pins", [{"Gold": 1200, "Silver": 1100}, {"Gold": -1},
                                {"Silver": math.nan}, {"Mario": 0}])
def test_relaxed_mode_never_accepts_contradictory_or_invalid_explicit_pins(pins):
    with pytest.raises(ValueError):
        with_anchors(compile_curve(calibrated_nodes()), pins, preserve_unpinned=False)


def test_existing_legacy_curve_keeps_its_literal_pins_and_records_projection():
    curve = from_ladder({"Mario": 885, "Grandmaster": 900, "Bronze": 940})
    result = with_anchors(curve, {"Mario": 901}, preserve_unpinned=False)
    assert result["ladder_cs"] == {"Mario": 901, "Grandmaster": 901, "Bronze": 940}
    assert result["metadata"]["anchor_adjustments"]["moved_automatic_cs"] == {
        "Grandmaster": {"generated": 900, "effective": 901}}
    assert curve["ladder_cs"]["Mario"] == 885


def test_no_pins_or_unchanged_pins_preserve_generated_curve_without_input_mutation():
    curve = compile_curve(calibrated_nodes())
    assert with_anchors(curve, {}, preserve_unpinned=False) == curve
    result = with_anchors(curve, {"Silver": curve["ladder_cs"]["Silver"]}, preserve_unpinned=False)
    assert result["ladder_cs"] == curve["ladder_cs"]
    assert result["nodes"] == curve["nodes"]
    assert result["metadata"]["anchor_adjustments"]["moved_automatic_cs"] == {}
    assert "anchor_adjustments" not in curve["metadata"]
