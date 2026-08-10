"""JP rank standards are registered, and one rule resolves them.

The user's rule (2026-08-07): a JP time that is ANNOTATED as different gets its
own standard; where nothing is annotated, the base ladder is COMBINED and
applies to both modes. WHICH version a given attempt grades on is deliberately
not decided here -- that is the console-support branch's N64-mode spec, and
`ladder_cs(ek, strat, version=)` is the door it resolves through.

Two sources, one rule: the vetted seed annotates SPARSELY (jp_strategies holds
only the ranks whose JP time differs -- tools/scrape_ranks.py has emitted them
since the beginning, unread until now), while the sheet layer annotates with a
FULL fitted JP ladder. Both resolve as an overlay onto the base."""
import json
from pathlib import Path

import pytest

from sm64_events.core.paths import bundled_rank_standards, bundled_sheet_ladders
from sm64_events.core.timefmt import GAME_FPS
from sm64_events.ranks.standards import RankStandards

DATA = Path(__file__).resolve().parent.parent / "src" / "sm64_events" / "data"


@pytest.fixture(scope="module")
def vetted_seed():
    return json.loads((DATA / "rank_standards.seed.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def sheet_seed():
    return json.loads((DATA / "sheet_ladders.seed.json").read_text(encoding="utf-8"))


@pytest.fixture()
def store(tmp_path):
    out = RankStandards(tmp_path / "rank_standards.json",
                        bundled_rank_standards(), bundled_sheet_ladders())
    out.load()
    return out


def test_the_vetted_seed_carries_registered_jp_annotations(vetted_seed):
    """The Daily Star data has held JP deltas all along; this is the pin that
    says they are load-bearing now rather than dead freight."""
    strategies = sum(len(e.get("jp_strategies", {}))
                    for e in vetted_seed["entities"].values())
    cells = sum(len(deltas) for e in vetted_seed["entities"].values()
                for deltas in e.get("jp_strategies", {}).values())
    assert strategies >= 70, strategies
    assert cells >= 500, cells


def test_an_annotated_rank_resolves_to_its_jp_value(store, vetted_seed):
    """Overlay rank by rank: annotated ranks move, unannotated ranks keep the
    base value -- driven through a real annotated (entity, strategy)."""
    for ek, entity in vetted_seed["entities"].items():
        for strat, deltas in entity.get("jp_strategies", {}).items():
            if not deltas or strat not in entity.get("strategies", {}):
                continue
            base = store.ladder_cs(ek, strat)
            jp = store.ladder_cs(ek, strat, version="jp")
            for rank, seconds in deltas.items():
                assert jp[rank] == int(round(seconds * 100)), (ek, strat, rank)
            for rank in base:
                if rank not in deltas:
                    assert jp[rank] == base[rank], (ek, strat, rank)
            assert store.has_jp_ladder(ek, strat)
            return
    pytest.fail("no annotated strategy found to drive the overlay through")


def test_no_annotation_means_one_combined_ladder_for_both_modes(store):
    """The user's rule verbatim: no annotated difference, no version split."""
    for ek in store.graded_entities():
        for strat in store.strategies(ek):
            if not store.has_jp_ladder(ek, strat):
                assert store.ladder_cs(ek, strat, version="jp") == \
                    store.ladder_cs(ek, strat), (ek, strat)
                return
    pytest.fail("no unannotated strategy found")


def test_the_sheet_layer_annotates_where_both_populations_carry(sheet_seed):
    """58 library rows hold both a fittable US and a fittable JP population
    (measured 2026-08-07); the adopted grading set keeps the ones that
    survived adoption. A floor, not an equality -- the sheet grows."""
    jp = sum(len(layers.get("jp_strategies", {}))
             for layers in sheet_seed["entities"].values())
    assert jp >= 5, jp
    for layers in sheet_seed["entities"].values():
        for name in layers.get("jp_strategies", {}):
            assert name in layers["strategies"], name   # never JP without a base


def test_a_sheet_jp_ladder_resolves_through_the_same_door(store, sheet_seed):
    for ek, layers in sheet_seed["entities"].items():
        for strat in layers.get("jp_strategies", {}):
            base = store.ladder_cs(ek, strat)
            jp = store.ladder_cs(ek, strat, version="jp")
            assert jp and base and jp != base, (ek, strat)
            assert store.has_jp_ladder(ek, strat)
            return
    pytest.fail("no sheet-layer JP ladder found to resolve")


def test_fitted_jp_cutoffs_are_times_usamune_can_show(sheet_seed):
    displayable = {(f % GAME_FPS) * 100 // GAME_FPS for f in range(GAME_FPS)}
    bad = [(ek, name, rank, seconds)
           for ek, layers in sheet_seed["entities"].items()
           for name, ladder in layers.get("jp_strategies", {}).items()
           for rank, seconds in ladder.items()
           if int(round(seconds * 100)) % 100 not in displayable]
    assert bad == [], bad[:5]


def test_an_unknown_version_gets_the_combined_ladder(store):
    ek = store.graded_entities()[0]
    strat = store.strategies(ek)[0]
    assert store.ladder_cs(ek, strat, version="us") == store.ladder_cs(ek, strat)
    assert store.ladder_cs(ek, strat, version=None) == store.ladder_cs(ek, strat)
