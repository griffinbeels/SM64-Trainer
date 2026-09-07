"""A materialized seed yields to a complete Sheet fit; actual edits survive.

The numeric ladders are injected reference data, independent of the fitter.
These tests exercise persistence and the public resolver both pages consume.
"""
import json

import pytest

from sm64_events.ranks.standards import RankStandards, SEED_MOVES


ENTITY = "segment:42"
STRATEGY = "Standard"
US = {"Mario": 8.0, "Grandmaster": 9.0, "Master": 10.0, "Diamond": 11.0,
      "Platinum": 12.0, "Gold": 13.0, "Silver": 14.0, "Bronze": 15.0}
JP = {rank: seconds - 1.0 for rank, seconds in US.items()}
SEED_US = {"Mario": 10.0, "Diamond": 12.0, "Bronze": 16.0}
SEED_JP = {"Mario": 9.5, "Bronze": 15.5}


def _write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def _fit(store, us=US, jp=JP):
    layers = {"strategies": {STRATEGY: dict(us)}}
    if jp is not None:
        layers["jp_strategies"] = {STRATEGY: dict(jp)}
    store.apply_sheet_ladders({ENTITY: layers})


@pytest.fixture
def store(tmp_path):
    seed = tmp_path / "seed.json"
    _write(seed, {"version": 1, "entities": {ENTITY: {
        "clock": "rta", "strategies": {STRATEGY: SEED_US},
        "jp_strategies": {STRATEGY: SEED_JP}}}})
    result = RankStandards(tmp_path / "standards.json", seed_path=seed)
    result.load()
    return result


def _restart(store):
    fresh = RankStandards(store.path, seed_path=store.seed_path)
    fresh.load()
    return fresh


@pytest.mark.parametrize("version, expected", [("us", US), ("jp", JP)])
def test_materialized_defaults_yield_to_all_eight_fitted_cutoffs(store, version, expected):
    stored = store.to_json()
    _fit(store)
    assert store.ladders(ENTITY, version)[STRATEGY] == expected
    assert store.ladder_cs(ENTITY, STRATEGY, version) == {
        rank: round(seconds * 100) for rank, seconds in expected.items()}
    assert store.is_fitted(ENTITY, STRATEGY)
    assert store.fitted_strategies(ENTITY) == [STRATEGY]
    store.save()
    assert store.to_json() == stored, "fitted numbers must stay out of persisted data"


def test_legacy_manual_cutoffs_survive_refresh_and_restart_without_freezing_other_tiers(store):
    stored = store.to_json()
    entity = stored["entities"][ENTITY]
    entity["strategies"][STRATEGY]["Diamond"] = 12.5
    entity["jp_strategies"][STRATEGY]["Bronze"] = 14.5
    _write(store.path, stored)
    store = _restart(store)
    _fit(store)
    assert store.ladders(ENTITY, "us")[STRATEGY] == {**US, "Diamond": 12.5}
    assert store.ladders(ENTITY, "jp")[STRATEGY] == {**JP, "Bronze": 14.5}
    refreshed_us = {**US, "Master": 10.5}
    refreshed_jp = {**JP, "Master": 9.5}
    _fit(store, refreshed_us, refreshed_jp)
    store.save()
    store = _restart(store)
    _fit(store, refreshed_us, refreshed_jp)
    assert store.ladders(ENTITY, "us")[STRATEGY] == {**refreshed_us, "Diamond": 12.5}
    assert store.ladders(ENTITY, "jp")[STRATEGY] == {**refreshed_jp, "Bronze": 14.5}


def test_a_detected_legacy_edit_survives_a_later_bundled_seed_upgrade(store):
    stored = store.to_json()
    stored["entities"][ENTITY]["strategies"][STRATEGY]["Mario"] = 10.5
    stored["entities"][ENTITY]["jp_strategies"][STRATEGY]["Mario"] = 9.75
    _write(store.path, stored)
    store = _restart(store)  # the matching seed version identifies legacy edits
    seed = json.loads(store.seed_path.read_text(encoding="utf-8"))
    seed["version"] = 2
    seed["entities"][ENTITY]["strategies"][STRATEGY]["Mario"] = 11.0
    seed["entities"][ENTITY]["jp_strategies"][STRATEGY]["Mario"] = 10.0
    _write(store.seed_path, seed)
    store = _restart(store)
    _fit(store)
    assert store.ladders(ENTITY, "us")[STRATEGY] == {**US, "Mario": 10.5}
    assert store.ladders(ENTITY, "jp")[STRATEGY] == {**JP, "Mario": 9.75}


@pytest.mark.parametrize("version, value", [("us", 10.0), ("jp", 9.5)])
@pytest.mark.parametrize("edit_before_fit", [False, True])
def test_explicit_edits_equal_to_seed_survive_new_fits_restart_and_seed_reconcile(
        store, version, value, edit_before_fit):
    if not edit_before_fit:
        _fit(store)
    store.set_threshold(ENTITY, STRATEGY, "Mario", value, version=version)
    _fit(store)
    expected = {**(JP if version == "jp" else US), "Mario": value}
    assert store.ladders(ENTITY, version)[STRATEGY] == expected
    seed = json.loads(store.seed_path.read_text(encoding="utf-8"))
    seed["version"] = 2
    seed["entities"][ENTITY]["strategies"][STRATEGY]["Mario"] = 10.5
    seed["entities"][ENTITY]["jp_strategies"][STRATEGY]["Mario"] = 10.0
    _write(store.seed_path, seed)
    store = _restart(store)
    assert store.ladders(ENTITY, version)[STRATEGY]["Mario"] == value
    refreshed_us = {**US, "Master": 10.5}
    refreshed_jp = {**JP, "Master": 9.5}
    _fit(store, refreshed_us, refreshed_jp)
    expected = {**(refreshed_jp if version == "jp" else refreshed_us), "Mario": value}
    assert store.ladders(ENTITY, version)[STRATEGY] == expected
    store.grading_version = version
    assert store.ladders(ENTITY)[STRATEGY] == expected


def test_us_fit_without_jp_annotation_supplies_the_combined_ladder(store):
    _fit(store, jp=None)
    assert store.ladders(ENTITY, "us")[STRATEGY] == US
    assert store.ladders(ENTITY, "jp")[STRATEGY] == US
    assert store.jp_deltas(ENTITY, STRATEGY) == {}
    assert store.clearable_jp_strategies(ENTITY) == []
    store.set_threshold(ENTITY, STRATEGY, "Mario", 9.5, version="jp")
    assert store.ladders(ENTITY, "jp")[STRATEGY] == {**US, "Mario": 9.5}


def test_clear_jp_and_reset_remove_explicit_edits_but_keep_the_sheet_foundation(store):
    _fit(store)
    assert store.clearable_jp_strategies(ENTITY) == []
    store.set_threshold(ENTITY, STRATEGY, "Mario", 10.0)
    store.set_threshold(ENTITY, STRATEGY, "Mario", 9.5, version="jp")
    assert store.clearable_jp_strategies(ENTITY) == [STRATEGY]
    store.clear_jp(ENTITY, STRATEGY)
    store = _restart(store)
    _fit(store)
    assert store.clearable_jp_strategies(ENTITY) == []
    assert store.ladders(ENTITY, "jp")[STRATEGY] == JP
    assert store.ladders(ENTITY, "us")[STRATEGY] == {**US, "Mario": 10.0}
    store.reset_entity(ENTITY)
    assert store.ladders(ENTITY, "us")[STRATEGY] == US
    assert store.ladders(ENTITY, "jp")[STRATEGY] == JP
    saved = store.to_json()["entities"][ENTITY]
    assert saved["strategies"][STRATEGY] == SEED_US
    assert not saved.get("sheet_overrides") and not saved.get("sheet_jp_overrides")


def test_delete_drops_explicit_edits_and_recreating_a_name_does_not_reuse_them(store):
    _fit(store)
    store.set_threshold(ENTITY, STRATEGY, "Mario", 10.0)
    store.set_threshold(ENTITY, STRATEGY, "Mario", 9.5, version="jp")
    store.delete_strategy(ENTITY, STRATEGY)
    store = _restart(store)
    _fit(store)
    store.create_strategy(ENTITY, STRATEGY)
    assert store.ladders(ENTITY, "us")[STRATEGY] == US
    assert store.ladders(ENTITY, "jp")[STRATEGY] == JP
    assert store.clearable_jp_strategies(ENTITY) == []


def test_manual_only_strategies_keep_their_existing_ladders(store):
    store.create_strategy(ENTITY, "Mine")
    store.set_threshold(ENTITY, "Mine", "Mario", 20.0)
    store.set_threshold(ENTITY, "Mine", "Mario", 19.0, version="jp")
    store = _restart(store)
    _fit(store)
    assert store.ladders(ENTITY, "us")["Mine"] == {"Mario": 20.0}
    assert store.ladders(ENTITY, "jp")["Mine"] == {"Mario": 19.0}
    assert not store.is_fitted(ENTITY, "Mine")
    store.apply_sheet_ladders({})
    assert store.ladders(ENTITY, "us")[STRATEGY] == SEED_US
    assert store.ladders(ENTITY, "jp")[STRATEGY] == {**SEED_US, **SEED_JP}


def test_seed_baseline_is_cached_and_never_aliased_to_mutable_user_data(store, monkeypatch):
    def unexpected_read(path):
        pytest.fail(f"a standards lookup reread {path}")

    _fit(store)
    monkeypatch.setattr(store, "_read_valid", unexpected_read)
    store.set_threshold(ENTITY, STRATEGY, "Mario", 10.0)
    store.reset_entity(ENTITY)
    store.set_threshold(ENTITY, STRATEGY, "Mario", 10.0)
    assert store.ladders(ENTITY, "us")[STRATEGY] == {**US, "Mario": 10.0}
    assert store.seeded_strategies(ENTITY) == [STRATEGY]
    store.reset_entity(ENTITY)
    assert store.ladders(ENTITY, "us")[STRATEGY] == US
    assert store.ladders(ENTITY, "jp")[STRATEGY] == JP
    copy = store.ladders(ENTITY, "us")
    copy[STRATEGY]["Diamond"] = 99.0
    assert store.ladders(ENTITY, "us")[STRATEGY] == US


@pytest.mark.parametrize("version", ["us", "jp"])
def test_moved_seed_repair_preserves_explicit_edits_even_equal_to_its_old_value(tmp_path, version):
    (old_entity, strat), (_new_entity, old_ladder) = next(iter(SEED_MOVES.items()))
    store = RankStandards(tmp_path / "standards.json")
    store.load()
    _write(store.path, {"version": 1, "entities": {old_entity: {
        "clock": "igt", "strategies": {strat: dict(old_ladder)}}}})
    # Model an edit made before the first startup carrying the repair.
    store._data = json.loads(store.path.read_text(encoding="utf-8"))
    store.set_threshold(old_entity, strat, "Mario", old_ladder["Mario"], version=version)
    store = _restart(store)
    assert store.ladders(old_entity, "us")[strat] == old_ladder
    assert store.ladders(old_entity, version)[strat]["Mario"] == old_ladder["Mario"]
