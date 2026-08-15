"""JP rank standards are registered, and one rule resolves them.

The user's rule (2026-08-07): a JP time that is ANNOTATED as different gets its
own standard; where nothing is annotated, the base ladder is COMBINED and
applies to both modes. WHICH version a given attempt grades on is the game
version setting (core/modes.py, 2026-08-15): its effective version becomes the
store's `grading_version`, which every unversioned read inherits, and
`ladder_cs(ek, strat, version=)` names one explicitly (the visual switches).

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


def test_us_and_none_both_read_the_grading_ladder_by_default(store):
    ek = store.graded_entities()[0]
    strat = store.strategies(ek)[0]
    assert store.ladder_cs(ek, strat, version="us") == store.ladder_cs(ek, strat)
    assert store.ladder_cs(ek, strat, version=None) == store.ladder_cs(ek, strat)


def test_a_junk_version_is_refused_rather_than_silently_us(store):
    ek = store.graded_entities()[0]
    strat = store.strategies(ek)[0]
    with pytest.raises(ValueError):
        store.ladder_cs(ek, strat, version="pal")


# ---- the grading version (game version setting -> what every rank reads) ----

def _annotated(store, vetted_seed):
    for ek, entity in vetted_seed["entities"].items():
        for strat, deltas in entity.get("jp_strategies", {}).items():
            if deltas and strat in entity.get("strategies", {}) \
                    and "Mario" in deltas:
                return ek, strat
    pytest.fail("no annotated strategy with a Mario delta")


def test_grading_version_is_what_unversioned_reads_inherit(store, vetted_seed):
    """`grading_version` is the ONE knob every rank surface follows: with it on
    "jp", a read that names no version IS the JP read."""
    ek, strat = _annotated(store, vetted_seed)
    us = store.ladder_cs(ek, strat)
    store.grading_version = "jp"
    assert store.ladder_cs(ek, strat) == store.ladder_cs(ek, strat, "jp") != us
    assert store.ladders(ek)[strat]["Mario"] == store.jp_deltas(ek, strat)["Mario"]


def test_an_explicit_version_beats_the_grading_version(store, vetted_seed):
    """The visual switches ask for a version by name and must never be
    dragged along by the setting."""
    ek, strat = _annotated(store, vetted_seed)
    us_before = store.ladder_cs(ek, strat, "us")
    store.grading_version = "jp"
    assert store.ladder_cs(ek, strat, "us") == us_before
    assert store.ladders(ek, "us")[strat] == store._entity(ek)["strategies"][strat]


def test_a_jp_threshold_writes_the_overlay_and_clear_removes_it(tmp_path):
    s = RankStandards(tmp_path / "rs.json")
    s.load()
    s.set_threshold("star:9:1", "Mine", "Mario", 30.0)
    s.set_threshold("star:9:1", "Mine", "Mario", 29.0, version="jp")
    assert s.jp_deltas("star:9:1", "Mine") == {"Mario": 29.0}
    assert s.ladders("star:9:1", "jp")["Mine"]["Mario"] == 29.0
    assert s.ladders("star:9:1", "us")["Mine"]["Mario"] == 30.0
    assert s.jp_strategies("star:9:1") == ["Mine"]
    written = json.loads((tmp_path / "rs.json").read_text())
    assert written["entities"]["star:9:1"]["jp_strategies"] == {"Mine": {"Mario": 29.0}}
    s.clear_jp("star:9:1", "Mine")
    assert s.jp_deltas("star:9:1", "Mine") == {}
    assert s.jp_strategies("star:9:1") == []
    assert s.ladders("star:9:1", "jp")["Mine"]["Mario"] == 30.0


def test_a_user_jp_edit_overlays_a_fitted_jp_ladder_per_rank(store):
    """One typed JP rank on a sheet-fitted strategy must not hide the rest of
    the fitted JP ladder (the old vetted-wins-whole rule would have)."""
    ek, strat = next((ek, strat) for ek, layers in store._sheet_jp.items()
                     for strat in layers)
    before = store.jp_deltas(ek, strat)
    assert len(before) > 1
    store.set_threshold(ek, strat, "Mario", 1.0, version="jp")
    after = store.jp_deltas(ek, strat)
    assert after["Mario"] == 1.0
    assert {r: v for r, v in after.items() if r != "Mario"} == \
        {r: v for r, v in before.items() if r != "Mario"}
    store.clear_jp(ek, strat)
    assert store.jp_deltas(ek, strat) == before   # the fitted layer is not his to clear


def test_deleting_a_strategy_takes_its_jp_overlay_with_it(tmp_path):
    s = RankStandards(tmp_path / "rs.json")
    s.load()
    s.set_threshold("star:9:1", "Mine", "Mario", 30.0)
    s.set_threshold("star:9:1", "Mine", "Mario", 29.0, version="jp")
    s.delete_strategy("star:9:1", "Mine")
    assert s.jp_deltas("star:9:1", "Mine") == {}
    assert "Mine" not in json.loads((tmp_path / "rs.json").read_text())["entities"]["star:9:1"].get("jp_strategies", {})
