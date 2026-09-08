"""Population fixtures describe people and clocks, never positional row IDs."""
from copy import deepcopy

from sm64_events.library.populations import collect_populations, eligible_entries, runner_identity
from sm64_events.ranks.policy import RankingPolicy


def _settings(families=None):
    return RankingPolicy({"layers": {"overall": {"patches": []}}}).resolve("") | {
        "fast_quantile": .1, "slow_quantile": .95, "overlap_ratio": .7,
        "confidence_runners": 8, "unannotated_region": "both", "families": families or []}


def _row(name, times, prefix="runner"):
    return {"name": name, "row_id": name, "entries": [
        {"runner": f"{prefix}{i}", "time_cs": time} for i, time in enumerate(times)]}


def test_best_per_runner_and_family_ignore_duplicates_and_order():
    settings = _settings([{"id": "family", "label": "Family", "rows": ["a", "b"]}])
    rows = [_row("a", [1000, 1100]), _row("b", [900, 1200])]
    first = collect_populations(rows, settings=settings)
    second = collect_populations(list(reversed(rows)) + deepcopy(rows), settings=settings)
    assert first.best_by_runner == second.best_by_runner == {"runner0": 900, "runner1": 1100}
    assert first.families == second.families
    assert first.metadata["population_count"] == 2


def test_exact_runner_identity_does_not_fuzzily_merge_different_people():
    row = {"name": "x", "entries": [{"runner": name, "time_cs": time} for name, time in
                                     (("Alex", 1000), ("alex", 1100), (" Alex", 1200))]}
    pop = collect_populations([row], settings=_settings())
    assert len(pop.best_by_runner) == 3


def test_strict_eligibility_retains_video_and_distinct_provider_ids():
    one = {"runner": "same label", "runner_id": "one", "time_cs": 1000, "video": "clip-one"}
    two = {"runner": "same label", "runner_id": "two", "time_cs": 1100, "video": "clip-two"}
    row = {"name": "x", "entries": [one, two,
        {"runner": "invalid", "time_cs": 999, "version": "pal"}]}
    result = eligible_entries(row, "us")
    assert result == [one, two] and result[0] is one
    assert result[0]["video"] == "clip-one"
    assert runner_identity(one) != runner_identity(two)
    assert runner_identity({"runner": "same label", "runner_id": []}) is None
    assert collect_populations([row], settings=_settings()).metadata["population_count"] == 2


def test_versions_are_separate_and_unannotated_policy_is_explicit():
    row = {"name": "merged", "version": "jp", "entries": [
        {"runner": "one", "time_cs": 1500, "version": "us"},
        {"runner": "one", "time_cs": 1000, "version": "jp"},
        {"runner": "two", "time_cs": 1600}]}
    assert collect_populations([row], settings=_settings(), version="us").best_by_runner == {"one": 1500, "two": 1600}
    assert collect_populations([row], settings=_settings(), version="jp").best_by_runner == {"one": 1000, "two": 1600}
    strict = _settings() | {"unannotated_region": "exclude"}
    assert collect_populations([row], settings=strict, version="us").best_by_runner == {"one": 1500}


def test_full_hmc_clocks_do_not_mix_or_accept_pickup_only_rows():
    rows = [{**_row("result", [1440]), "target_id": "seg:hmc-toad-result"},
            {**_row("door", [1390]), "target_id": "seg:hmc-toad-door"},
            {**_row("pickup", [230]), "target_id": "star:0:0"}]
    pop = collect_populations(rows, settings=_settings(), target_id="seg:hmc-toad-result")
    assert list(pop.best_by_runner.values()) == [1440]
    assert pop.metadata["excluded_entries"] == {"incompatible_target": 2}


def test_overlapping_variants_share_one_stage_while_a_distinct_new_family_is_provisional():
    rows = [_row("known", range(1000, 1200, 10)), _row("alias", range(1000, 1200, 10)),
            _row("minor variant", range(1030, 1220, 10)), _row("breakthrough", [500], "new")]
    settings = _settings([{"id": "known", "label": "Known", "rows": ["known"]}])
    pop = collect_populations(rows, settings=settings)
    assert len(pop.families) == 2
    known = next(family for family in pop.families if not family.provisional)
    assert len(known.rows) == 3
    fresh = next(family for family in pop.families if family.provisional)
    assert fresh.confidence == 1 / 8
    assert pop.best_by_runner["new0"] == 500


def test_invalid_entries_are_excluded_with_actual_counts():
    row = {"name": "x", "entries": [None, {"runner": "a", "time_cs": float("nan")},
        {"runner": "b", "time_cs": -1}, {"runner": "c", "time_cs": float("inf")},
        {"runner": "d", "time_cs": True}, {"time_cs": 1000},
        {"runner": "e", "time_cs": 1000, "version": "pal"},
        {"runner": "f", "time_cs": 1100}]}
    pop = collect_populations([row], settings=_settings())
    assert pop.metadata["population_count"] == 1
    assert pop.metadata["excluded_entries"] == {"invalid_time": 5, "missing_runner": 1, "invalid_region": 1}


def test_estimates_are_labeled_and_real_people_supersede_them():
    proxy = {"name": "estimated", "entries": [], "estimate_times_cs": [500, 600],
             "estimate_version": "us", "estimate_provenance": {
                 "method": "related_row", "source_rows": ["compatible source"], "source_samples": 2}}
    pop = collect_populations([proxy], settings=_settings())
    assert pop.proxy_times == (500, 600)
    assert pop.metadata["population_count"] == 0 and pop.metadata["estimated"]
    assert pop.metadata["estimates"][0]["source_samples"] == 2
    measured = collect_populations([proxy, _row("real", [1000])], settings=_settings())
    assert measured.proxy_times == () and not measured.metadata["estimated"]
    assert measured.metadata["population_count"] == 1
