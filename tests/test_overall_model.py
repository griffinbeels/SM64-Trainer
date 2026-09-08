"""Observable Overall fitting contracts, including the frozen accepted trial."""
from copy import deepcopy

import pytest

from sm64_events.core.timefmt import cs_of_frame, frame_at_or_after
from sm64_events.ranks import scoring
from sm64_events.ranks.curves import compile_curve, from_ladder, score_for, time_for_score
from sm64_events.ranks.overall import DIVISION_SCORES, _calibrate, _milestone_curve, fit_overall
from sm64_events.ranks.policy import RankingPolicy


FIT = {"percentiles": {"Mario": 6.7, "Grandmaster": 21.7, "Master": 45., "Diamond": 65.2,
                       "Platinum": 80.4, "Gold": 89.3, "Silver": 94., "Bronze": 100.},
       "peak_window_frames": 3, "peak_min_entries": 3, "peak_density_ratio": 2}


def _policy(model="family_milestones", families=None, **changes):
    settings = {"model": model, "milestone_weight": .75, "slow_quantile": .95,
                "fast_quantile": .1, "overlap_ratio": .7, "confidence_runners": 8,
                "unannotated_region": "both", "community_fit": FIT,
                "families": families or [], **changes}
    return RankingPolicy({"layers": {"overall": {"defaults": settings, "patches": []}}})


def _row(name, times, prefix="runner", version=None):
    return {"name": name, "row_id": name, "entries": [
        {"runner": f"{prefix}{i}", "time_cs": time, "version": version} for i, time in enumerate(times)]}


def _families(*names):
    return [{"id": name, "label": name, "rows": [name]} for name in names]


def test_zero_one_and_dense_populations_have_honest_coverage():
    assert fit_overall([], policy=_policy()) is None
    assert fit_overall([{"name": "empty", "entries": []}], policy=_policy()) is None
    for times in ([1000], [1000] * 40, range(1000, 2000, 10), [.1]):
        curve = fit_overall([_row("a", times)], policy=_policy())
        assert len(curve["nodes"]) == len(DIVISION_SCORES) == 45
        assert curve["metadata"]["population_count"] == len(times)
        assert all(time > 0 for time, _ in curve["nodes"])
        assert set(curve["ladder_cs"]) == set(FIT["percentiles"])


def test_published_estimate_is_rankable_without_claiming_observed_people():
    row = {"name": "estimated", "entries": [], "ideal_cs": 1000}
    curve = fit_overall([row], policy=_policy())
    assert curve["metadata"]["population_count"] == 0
    assert curve["metadata"]["estimated"]
    assert curve["metadata"]["frontier"]["best_observed_cs"] is None
    assert curve["metadata"]["frontier"]["best_evidence_cs"] == 1000


def test_single_effective_family_is_exactly_the_community_model():
    rows = [_row("a", range(1000, 1600, 10)), _row("alias", range(1000, 1600, 10))]
    progression = fit_overall(rows, policy=_policy())
    community = fit_overall(rows, policy=_policy(model="community"))
    assert progression["nodes"] == community["nodes"]
    assert progression["metadata"]["family_count"] == 1


def test_duplicates_cosmetic_aliases_and_row_order_cannot_create_stages():
    rows = [_row("slow", range(1600, 2000, 10)), _row("fast", range(1000, 1400, 10))]
    policy = _policy(families=_families("slow", "fast"))
    first = fit_overall(rows, policy=policy)
    duplicate = deepcopy(rows[0])
    duplicate["name"] = duplicate["row_id"] = "slow cosmetic alias"
    changed = list(reversed(rows)) + deepcopy(rows) + [duplicate]
    second = fit_overall(changed, policy=policy)
    assert first["nodes"] == second["nodes"]
    assert second["metadata"]["population_count"] == first["metadata"]["population_count"]
    assert second["metadata"]["family_count"] == 2


def test_new_record_participates_immediately_and_support_bounds_wider_influence():
    rows = [_row("slow", range(1700, 2100, 10)), _row("fast", range(1000, 1400, 10))]
    policy = _policy(families=_families("slow", "fast"))
    first = fit_overall(rows, policy=policy)
    breakthrough = _row("new strategy", [700], "new")
    second = fit_overall(rows + [breakthrough], policy=policy)
    assert second["nodes"][0][0] < first["nodes"][0][0]
    assert second["metadata"]["population_count"] == first["metadata"]["population_count"] + 1
    assert second["metadata"]["provisional_influence"] == 1 / 8
    assert second["metadata"]["families"][-1]["provisional"]
    repeated = fit_overall(rows + [breakthrough] * 30, policy=policy)
    assert repeated["nodes"] == second["nodes"]
    supported = fit_overall(rows + [_row("new strategy", range(700, 730, 3), "new")], policy=policy)
    assert supported["metadata"]["provisional_influence"] == 1


def test_community_improvements_refit_elite_and_every_target_without_old_pins():
    rows = [_row("a", range(1100, 1800, 10))]
    old = fit_overall(rows, policy=_policy())
    faster = deepcopy(rows)
    for entry in faster[0]["entries"]:
        entry["time_cs"] -= 100
    refreshed = fit_overall(faster, policy=_policy())
    assert refreshed["metadata"]["frontier"]["mario_cs"] < old["metadata"]["frontier"]["mario_cs"]
    assert refreshed["metadata"]["frontier"]["metal_cs"] < old["metadata"]["frontier"]["metal_cs"]
    assert score_for(refreshed, 1400) < score_for(old, 1400)


def test_strategy_cutoffs_do_not_supply_overall_and_policy_patches_stay_local():
    rows = [_row("a", range(1000, 1400, 10)), _row("b", range(1500, 2200, 10), "other")]
    policy = _policy(families=_families("a", "b"))
    first = fit_overall(rows, policy=policy, target_id="one")
    changed = deepcopy(rows)
    changed[0]["ladder"] = {rank: 9999 for rank in FIT["percentiles"]}
    assert fit_overall(changed, policy=policy, target_id="one")["nodes"] == first["nodes"]
    patch = {"layers": {"overall": {"defaults": policy.resolve(""), "patches": [
        {"target_id": "one", "parameters": {"milestone_weight": .05}}]}}}
    tuned = RankingPolicy(patch)
    assert fit_overall(rows, policy=tuned, target_id="one")["nodes"] != first["nodes"]
    assert fit_overall(rows, policy=tuned, target_id="two")["nodes"] == first["nodes"]


def test_separate_regions_and_full_hmc_clocks_fit_independently():
    rows = [{**_row("result", range(1440, 1500, 3), version="us"), "target_id": "seg:hmc-toad-result"},
            {**_row("result-jp", range(1396, 1450, 3), version="jp"), "target_id": "seg:hmc-toad-result"},
            {**_row("door", range(1390, 1420, 3), version="us"), "target_id": "seg:hmc-toad-door"}]
    us = fit_overall(rows, policy=_policy(), target_id="seg:hmc-toad-result", version="us")
    jp = fit_overall(rows, policy=_policy(), target_id="seg:hmc-toad-result", version="jp")
    door = fit_overall(rows, policy=_policy(), target_id="seg:hmc-toad-door", version="us")
    assert us["metadata"]["frontier"]["best_observed_cs"] == 1440
    assert jp["metadata"]["frontier"]["best_observed_cs"] == 1396
    assert door["metadata"]["frontier"]["best_observed_cs"] == 1390


def test_every_printed_division_is_attainable_and_faster_frames_never_score_less():
    curve = fit_overall([_row("slow", range(1500, 3000, 17)),
                         _row("fast", range(1000, 1700, 11))],
                        policy=_policy(families=_families("slow", "fast")))
    frames = [frame_at_or_after(time) for time, _ in curve["nodes"]]
    assert all(b > a for a, b in zip(frames, frames[1:], strict=False))
    for score in DIVISION_SCORES:
        cutoff = time_for_score(curve, score)
        assert cutoff == cs_of_frame(frame_at_or_after(cutoff))
        assert score_for(curve, cutoff) >= score - 1e-9
        assert score_for(curve, cs_of_frame(frame_at_or_after(cutoff) + 1)) < score
    values = [score_for(curve, cs_of_frame(frame)) for frame in range(1, frames[-1] + 300)]
    assert all(a >= b for a, b in zip(values, values[1:], strict=False))


def test_frozen_accepted_caged_trial_extracts_about_39_without_pinning_production():
    # Reference: accepted 2026-09-08 private experiment, frozen sufficient
    # statistics and old elite control. Production never reads these anchors.
    families = [
        {"id": "owl", "label": "Owl", "fast_cs": 1583., "slow_cs": 1972.4},
        {"id": "standard", "label": "Standard", "fast_cs": 1173., "slow_cs": 1381.5},
        {"id": "dj", "label": "DJ variants", "fast_cs": 1100., "slow_cs": 1180.6},
    ]
    empirical = {"Mario": 1116, "Grandmaster": 1133, "Master": 1153, "Diamond": 1226,
                 "Platinum": 1266, "Gold": 1310, "Silver": 1373, "Bronze": 2246}
    community = compile_curve([[1100, 100], *[
        [time, scoring.SCORE_ANCHORS[rank]] for rank, time in empirical.items()]])
    old = from_ladder({"Mario": 1110, "Grandmaster": 1126, "Master": 1143, "Diamond": 1160,
                       "Platinum": 1176, "Gold": 1193, "Silver": 1210, "Bronze": 1276})
    milestone, landmarks = _milestone_curve(families, old, _policy().resolve(""))
    overlap = next(point for point in landmarks if len(point["labels"]) == 2)
    assert overlap["cs"] == pytest.approx((1173 * 1180.6) ** .5)
    assert overlap["score"] == 75
    curve = compile_curve(_calibrate(lambda time: .75 * score_for(milestone, time)
                                     + .25 * score_for(community, time), old, 12000))
    assert score_for(curve, 1386) == pytest.approx(39.26578, abs=.5)
    assert scoring.division_for(score_for(curve, 1386)) == ("Silver", "II")
