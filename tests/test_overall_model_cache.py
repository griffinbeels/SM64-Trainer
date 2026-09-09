"""Reuse identical validated fits without freezing observations or leaking edits."""
from copy import deepcopy

import pytest

from sm64_events.ranks import overall
from sm64_events.ranks.calibration import fingerprint
from sm64_events.ranks.fit_cache import FitCache
from sm64_events.ranks.policy import RankingPolicy


def rows():
    return [{"row_id": name, "name": name, "entries": [
        {"runner": f"{name}-{index}", "time_cs": start + index * 30}
        for index in range(12)]} for name, start in (("Slow", 1700), ("Fast", 1000))]


@pytest.fixture
def cache(monkeypatch):
    cache = FitCache(maxsize=8)
    monkeypatch.setattr(overall, "_FIT_CACHE", cache)
    return cache


@pytest.fixture
def fits(monkeypatch, cache):
    calls = []
    original = overall.MODEL_BUILDERS["family_milestones"]

    def counted(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setitem(overall.MODEL_BUILDERS, "family_milestones", counted)
    return calls


def test_fresh_inputs_reuse_the_fit_but_validate_every_time(monkeypatch, fits):
    collected = []
    original = overall.collect_populations

    def collect(*args, **kwargs):
        collected.append(kwargs["version"])
        return original(*args, **kwargs)

    monkeypatch.setattr(overall, "collect_populations", collect)
    first = overall.fit_overall(rows(), policy=RankingPolicy(), target_id="star:2:4")
    repeated = overall.fit_overall(deepcopy(rows()), policy=RankingPolicy(), target_id="star:2:4")
    assert collected == ["us", "us"]
    assert len(fits) == 1
    assert first == repeated and first is not repeated


def test_returned_curves_and_original_provenance_cannot_mutate_cache(fits):
    source = [{"row_id": "estimate", "estimate_times_cs": [1000, 1300],
               "estimate_provenance": {"method": "related", "origins": ["source"]}}]
    original = deepcopy(source)
    first = overall.fit_overall(source)
    expected = deepcopy(first)
    first["nodes"][0][0] = 1
    first["ladder_cs"]["Mario"] = 2
    first["metadata"]["estimates"][0]["origins"].append("caller edit")
    source[0]["estimate_provenance"]["origins"].append("source edit")
    repeated = overall.fit_overall(original)
    assert repeated == expected
    repeated["metadata"]["frontier"]["ceiling_cs"] = -1
    assert overall.fit_overall(original) == expected
    assert len(fits) == 1


@pytest.mark.parametrize("change", ["record", "runner", "strategy", "invalid", "target", "rom",
                                     "policy", "families"])
def test_changed_resolved_inputs_refit_and_equal_an_uncached_fit(fits, monkeypatch, change):
    source = rows()
    options = {"policy": RankingPolicy(), "target_id": "star:2:4", "version": "us"}
    before = overall.fit_overall(source, **options)
    if change == "record":
        source[1]["entries"][0]["time_cs"] = 750
    elif change == "runner":
        source[1]["entries"][0]["runner_id"] = "provider-id"
    elif change == "strategy":
        source.append({"row_id": "breakthrough", "name": "New strategy", "entries": [
            {"runner": "new runner", "time_cs": 700}]})
    elif change == "invalid":
        source[0]["entries"].append({"runner": "invalid", "time_cs": float("nan")})
    elif change in ("target", "rom"):
        options[{"target": "target_id", "rom": "version"}[change]] = {
            "target": "star:2:5", "rom": "jp"}[change]
    else:
        parameters = ({"milestone_weight": .5} if change == "policy" else {"families": [
            {"id": "reviewed", "label": "Reviewed slow route", "rows": ["Slow"]}]})
        options["policy"] = RankingPolicy({"layers": {"overall": {"patches": [
            {"target_id": "star:2:4", "parameters": parameters}]}}})
    actual = overall.fit_overall(source, **options)
    assert len(fits) == 2
    # Disable only reuse, keeping the public validation and production fitter.
    monkeypatch.setattr(overall._FIT_CACHE, "get_or_compute", lambda _key, compute: compute())
    expected = overall.fit_overall(source, **options)
    assert len(fits) == 3
    assert actual == expected
    assert fingerprint(actual) == fingerprint(expected)
    if change != "runner":
        assert actual != before


def test_proxy_provenance_and_its_container_types_participate_in_key(fits):
    source = [{"row_id": "estimate", "estimate_times_cs": [1000],
               "estimate_provenance": {"method": "related", "origins": ["old"]}}]
    before = overall.fit_overall(source)
    source[0]["estimate_provenance"]["origins"] = ["new"]
    changed = overall.fit_overall(source)
    source[0]["estimate_provenance"]["origins"] = ("new",)
    typed = overall.fit_overall(source)
    assert len(fits) == 3
    assert before["nodes"] == changed["nodes"] == typed["nodes"]
    assert before["metadata"]["estimates"][0]["origins"] == ["old"]
    assert changed["metadata"]["estimates"][0]["origins"] == ["new"]
    assert typed["metadata"]["estimates"][0]["origins"] == ("new",)


def test_empty_rows_accept_new_estimates_updated_proxies_and_later_submissions(fits):
    source = [{"row_id": "future", "entries": []}]
    assert overall.fit_overall(source) is None
    source[0].update(estimate_times_cs=[1000], estimate_version="us",
                     estimate_provenance={"method": "related", "source_rows": ["neighbor"]})
    first = overall.fit_overall(source)
    assert first["metadata"]["estimated"]
    assert first["metadata"]["frontier"]["best_evidence_cs"] == 1000
    assert overall.fit_overall(source, version="jp") is None
    source[0]["estimate_times_cs"] = [800]
    updated = overall.fit_overall(source)
    assert updated["metadata"]["frontier"]["best_evidence_cs"] == 800
    assert updated["nodes"] != first["nodes"]
    source[0]["entries"] = [{"runner": "first participant", "time_cs": 900, "version": "us"}]
    observed = overall.fit_overall(source)
    assert not observed["metadata"]["estimated"]
    assert observed["metadata"]["population_count"] == 1
    assert observed["metadata"]["frontier"]["best_observed_cs"] == 900
    assert observed["nodes"] != updated["nodes"]
    assert len(fits) == 3


def test_provenance_numeric_types_and_signed_zero_keep_their_fingerprints(fits):
    source = [{"row_id": "estimate", "estimate_times_cs": [1000],
               "estimate_provenance": {"method": "related"}}]
    results = []
    for value in (0, 0., -0.):
        source[0]["estimate_provenance"]["annotation"] = value
        results.append(overall.fit_overall(source))
    assert len(fits) == 3
    assert len({fingerprint(result) for result in results}) == 3


def test_replaced_builder_and_instrumented_evaluator_are_not_hidden_by_reuse(cache, monkeypatch):
    source = rows()
    first = overall.fit_overall(source)
    original = overall.MODEL_BUILDERS["family_milestones"]

    def replacement(*args):
        evaluate, details = original(*args)
        return evaluate, {**details, "builder": "replacement"}

    monkeypatch.setitem(overall.MODEL_BUILDERS, "family_milestones", replacement)
    replaced = overall.fit_overall(source)
    assert replaced["metadata"]["builder"] == "replacement"
    assert "builder" not in first["metadata"]
    called = []
    evaluate = overall.score_evaluator

    def instrumented(curve):
        called.append(curve)
        return evaluate(curve)

    monkeypatch.setattr(overall, "score_evaluator", instrumented)
    assert overall.fit_overall(source) == replaced
    assert called, "an unchanged curve must still execute the replacement evaluator"


def test_validation_and_errors_are_not_bypassed_on_a_warm_cache(fits, monkeypatch):
    source = rows()
    overall.fit_overall(source)

    def reject(*_args, **_kwargs):
        raise ValueError("sample validation failed")

    monkeypatch.setattr(overall, "collect_populations", reject)
    with pytest.raises(ValueError, match="sample validation failed"):
        overall.fit_overall(source)
    with pytest.raises(ValueError, match="version"):
        overall.fit_overall(source, version="unsupported")
    assert len(fits) == 1


def test_cache_is_bounded_least_recently_used_and_does_not_retain_failures():
    cache = FitCache(maxsize=2)
    computed = []

    def read(key):
        def compute():
            computed.append(key)
            return {"key": key}
        return cache.get_or_compute(key, compute)

    for key in (1, 2, 1, 3, 2):
        assert read(key) == {"key": key}
        assert len(cache) <= 2
    assert computed == [1, 2, 3, 2]

    def fail():
        raise ValueError("fitting failed")

    with pytest.raises(ValueError, match="fitting failed"):
        cache.get_or_compute("bad", fail)
    assert len(cache) == 2
    assert cache.get_or_compute("bad", lambda: "recovered") == "recovered"
    cache.clear()
    assert len(cache) == 0


def test_nonstandard_provenance_is_fitted_without_lossy_cache_coercion(fits, cache):
    source = [{"row_id": "estimate", "best_cs": 1000, "estimate_times_cs": [1000],
               "estimate_provenance": {"method": "custom", "origins": {"set member"}}}]
    first = overall.fit_overall(source)
    assert overall.fit_overall(source) == first
    assert first["metadata"]["estimates"][0]["origins"] == {"set member"}
    assert len(fits) == 2 and len(cache) == 0
