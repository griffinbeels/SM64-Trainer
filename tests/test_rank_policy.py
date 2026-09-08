"""Scoped policy inheritance is independent of source identity and other layers."""
from copy import deepcopy

import pytest

from sm64_events.library.ladders import fit_ladder, fit_payload
from sm64_events.ranks.policy import RankingPolicy


def test_shipped_policy_has_valid_effective_settings_and_semantic_family_keys():
    policy = RankingPolicy()
    for layer in ("strategy", "overall", "route"):
        assert policy.resolve("new-target", layer=layer)["model"]
    for target in ("star:2:4", "star:16:0", "seg:hmc-toad-result"):
        families = policy.resolve(target)["families"]
        assert families and all(isinstance(row, str) for family in families for row in family["rows"])
    assert len(policy.revision) == 64


def test_target_region_strategy_and_route_patches_do_not_leak():
    policy = RankingPolicy({"layers": {
        "overall": {"patches": [
            {"target_id": "star:2:4", "parameters": {"milestone_weight": .4}},
            {"target_id": "star:2:4", "version": "jp", "parameters": {"milestone_weight": .2}}]},
        "strategy": {"patches": [{"target_id": "star:2:4", "strategy": "Owl",
                                   "parameters": {"peak_min_entries": 12}}]},
        "route": {"patches": [{"target_id": "route:16", "parameters": {"weights": {"star:2:4": 2}}}]},
    }})
    base = RankingPolicy()
    assert policy.resolve("star:2:4")["milestone_weight"] == .4
    assert policy.resolve("star:2:4", version="jp")["milestone_weight"] == .2
    assert policy.resolve("star:1:5") == {**base.resolve("star:1:5"), "families": []}
    assert policy.resolve("star:2:4", strategy="Owl", layer="strategy")["peak_min_entries"] == 12
    assert policy.resolve("star:2:4", strategy="Standard", layer="strategy") == base.resolve("", layer="strategy")
    assert policy.resolve("route:16", layer="route")["weights"] == {"star:2:4": 2}
    assert policy.resolve("route:70", layer="route")["weights"] == {}


def test_revision_is_content_based_and_effective_revision_is_local():
    patch = {"layers": {"overall": {"patches": [
        {"target_id": "one", "parameters": {"milestone_weight": .13}}]}}}
    first, second = RankingPolicy(patch), RankingPolicy(deepcopy(patch))
    assert first.revision == second.revision
    changed = deepcopy(patch)
    changed["layers"]["overall"]["patches"][0]["parameters"]["milestone_weight"] = .27
    third = RankingPolicy(changed)
    assert third.revision != first.revision
    assert third.effective_revision("one") != first.effective_revision("one")
    assert third.effective_revision("two") == first.effective_revision("two")
    settings = first.resolve("one")
    settings["community_fit"]["percentiles"]["Mario"] = -100
    assert first.resolve("one")["community_fit"]["percentiles"]["Mario"] > 0


@pytest.mark.parametrize("parameters", [
    {"model": "__import__('os')"}, {"milestone_weight": float("nan")},
    {"milestone_weight": 1.01}, {"confidence_runners": 0},
    {"fast_quantile": .99, "slow_quantile": .1}, {"formula": "score * 2"},
    {"community_fit": ["percentiles", "peak_window_frames", "peak_min_entries", "peak_density_ratio"]},
    {"families": [{"id": "family", "label": "Family", "rows": [0]}]},
    {"families": [{"id": "a", "label": "A", "rows": ["same"]},
                  {"id": "b", "label": "B", "rows": ["same"]}]},
])
def test_invalid_models_numbers_and_ambiguous_mappings_fail(parameters):
    with pytest.raises(ValueError):
        RankingPolicy({"layers": {"overall": {"patches": [{"parameters": parameters}]}}})


def test_invalid_region_and_cross_layer_selector_fail():
    with pytest.raises(ValueError, match="version"):
        RankingPolicy().resolve("x", version="pal")
    with pytest.raises(ValueError, match="only to the strategy"):
        RankingPolicy({"layers": {"overall": {"patches": [{"strategy": "Owl", "parameters": {}}]}}})


def test_strategy_policy_changes_ladder_without_changing_matching_profile():
    row = {"name": "Standard", "entries": [{"runner": str(i), "time_cs": 1000 + 10 * i}
                                            for i in range(100)]}
    payload = {"targets": [{"entity_key": "star:2:4", "approaches": [row], "subsections": []}]}
    original = fit_payload(deepcopy(payload))["targets"][0]["approaches"][0]
    policy = RankingPolicy({"layers": {"strategy": {"patches": [
        {"target_id": "star:2:4", "parameters": {"percentiles": {"Master": 50}}}]}}})
    updated = fit_payload(deepcopy(payload), policy=policy)["targets"][0]["approaches"][0]
    assert original["ladder"] != updated["ladder"]
    assert original["matching_profile"] == updated["matching_profile"]
    assert fit_ladder([1000, float("inf"), float("nan"), -1, 0, True]) == fit_ladder([1000])
