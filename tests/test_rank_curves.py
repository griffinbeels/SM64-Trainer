"""Full-node Overall scoring, attainable goals, and unchanged manual ladders."""
import copy
import json
import math

import pytest

from sm64_events.core.timefmt import cs_of_frame, frame_at_or_after
from sm64_events.ranks import scoring
from sm64_events.ranks.curves import compile_curve, from_ladder, progress_for_time, score_for, time_for_score, with_anchors
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
