"""The sheet-derived ladders, and the invariant that keeps them out of the way.

A fitted ladder must never overwrite a vetted one. That is structural here
rather than a rule a test has to remember: the sheet ladders live in their own
file, are merged only on READ, and nothing can write them into the user's
standards file -- so `save()` cannot spill them even by accident."""
import json
from pathlib import Path

import pytest

from sm64_events.core.paths import bundled_rank_standards, bundled_sheet_ladders
from sm64_events.ranks.standards import RankStandards

SHEET = Path(__file__).resolve().parent.parent / "src" / "sm64_events" / "data" / "sheet_ladders.seed.json"


@pytest.fixture(scope="module")
def sheet():
    return json.loads(SHEET.read_text(encoding="utf-8"))


@pytest.fixture()
def store(tmp_path):
    out = RankStandards(tmp_path / "rank_standards.json",
                        bundled_rank_standards(), bundled_sheet_ladders())
    out.load()
    return out


def test_the_file_ships_and_says_where_it_came_from(sheet):
    assert sheet["source"] == "sheet"
    # The model rides with the file; its VALUES are a retunable default and
    # deliberately not pinned (CLAUDE.md's shipped-default rule).
    assert set(sheet["model"]) and "Mario" in sheet["model"]
    assert len(sheet["entities"]) >= 40
    assert sum(len(v) for v in sheet["entities"].values()) >= 70


def test_no_fitted_strategy_shares_a_name_with_a_vetted_one(sheet):
    vetted = json.loads(Path(bundled_rank_standards()).read_text(encoding="utf-8"))
    clashes = [(ek, name) for ek, strategies in sheet["entities"].items()
               for name in strategies
               if name in vetted["entities"].get(ek, {}).get("strategies", {})]
    assert clashes == [], clashes


def test_a_fitted_ladder_is_served_and_marked(store, sheet):
    entity, strategies = next(iter(sheet["entities"].items()))
    name = next(iter(strategies))
    assert name in store.strategies(entity)
    assert store.is_fitted(entity, name)
    assert store.ladder_cs(entity, name)


def test_a_vetted_ladder_always_wins(store, sheet):
    for entity in sheet["entities"]:
        for name in store.ladders(entity):
            stored = store._stored_ladders(entity)
            if name in stored:
                assert store.ladders(entity)[name] == stored[name]
                assert not store.is_fitted(entity, name)


def test_saving_never_spills_a_fitted_ladder_into_the_user_file(store, sheet, tmp_path):
    store.save()
    written = json.loads((tmp_path / "rank_standards.json").read_text(encoding="utf-8"))
    leaked = [(ek, name) for ek, strategies in sheet["entities"].items()
              for name in strategies
              if name in written["entities"].get(ek, {}).get("strategies", {})]
    assert leaked == [], leaked


def test_mutating_a_ladder_edits_the_store_not_the_merge(store, sheet):
    entity = next(iter(sheet["entities"]))
    before = len(store.ladders(entity))
    store.delete_strategy(entity, "definitely not a strategy")
    assert len(store.ladders(entity)) == before
    store.create_strategy(entity, "Mine")
    assert "Mine" in store._stored_ladders(entity)
    store.delete_strategy(entity, "Mine")
    assert "Mine" not in store.ladders(entity)


def test_nothing_is_adopted_onto_a_variant_qualified_entity(sheet):
    """A 100-coin star's strategies are variant-qualified ("100c + Race ·
    Open") because CCM publishes two exit-star variants that both define
    "Standard" AND "Open" -- a bare name cannot identify a ladder there.

    The sheet row does not say which exit star it ran, so these wait rather
    than guess. Found by tests/test_ui_hundred_coin_render.py, which caught an
    unqualified "100 coin star Xcam" reaching the strategy picker."""
    vetted = json.loads(Path(bundled_rank_standards()).read_text(encoding="utf-8"))
    qualified = {ek for ek, entity in vetted["entities"].items()
                 if entity.get("exit_variants")}
    assert qualified, "no entity carries exit variants -- this guard is vacuous"
    overlap = sorted(set(sheet["entities"]) & qualified)
    assert overlap == [], overlap


def test_a_missing_sheet_file_simply_means_no_fitted_ladders(tmp_path):
    bare = RankStandards(tmp_path / "rank_standards.json",
                         bundled_rank_standards(), tmp_path / "absent.json")
    bare.load()
    assert bare.fitted_strategies("star:1:4") == []
    assert bare.ladders("star:1:4")
