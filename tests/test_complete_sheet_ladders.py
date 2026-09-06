"""Task 0126 round 2: every tier, not merely a nonempty ladder."""
import gzip
import json
from pathlib import Path

import pytest

from import_fixture import make_client
from sm64_events.library.ladders import fit_ladder, fit_payload
from sm64_events.ranks.classify import RANK_NAMES, rank_for
from sm64_events.ranks.scoring import progress_for_time, score_for, time_for_score
from sm64_events.ranks.standards import RankStandards

TIERS = set(RANK_NAMES) - {"Iron"}


@pytest.mark.parametrize("times", [[1000], [1000] * 40, [246, 250],
                                  [556, 560, 576, 583, 596, 600],
                                  [1000] * 40 + [1300] * 40])
def test_every_tier_survives_sparse_tied_and_clustered_observations(times):
    ladder = fit_ladder(times)
    assert set(ladder) == TIERS
    assert ladder["Mario"] == pytest.approx(min(times) / 100)
    assert ladder["Bronze"] == pytest.approx(max(times) / 100)
    assert list(ladder.values()) == sorted(ladder.values())


def test_more_observations_refine_interior_cutoffs_without_changing_endpoints():
    before = fit_ladder([1000, 2000], quantise=lambda cs: cs)
    after = fit_ladder([1000] + [1100] * 30 + [2000], quantise=lambda cs: cs)
    assert before["Mario"] == after["Mario"] == 10
    assert before["Bronze"] == after["Bronze"] == 20
    assert before["Gold"] != after["Gold"]


def test_every_current_sheet_row_and_regional_population_has_every_tier():
    path = Path(__file__).resolve().parents[1] / "src/sm64_events/data/sheet_library.seed.json.gz"
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        payload = fit_payload(json.load(stream))
    rows = [item for target in payload["targets"]
            for kind in ("approaches", "subsections") for item in target[kind]]
    assert len(rows) >= 634
    missing = [(item["name"], key, sorted(TIERS - set(item.get(key, {}))))
               for item in rows for key in ("ladder", "ladder_jp")
               if (key == "ladder" or key in item) and set(item.get(key, {})) != TIERS]
    assert not missing


def test_seeded_lakitu_defaults_cannot_replace_complete_sheet_ladders(tmp_path):
    with make_client(tmp_path) as (client, db, service):
        target = next(t for group in client.get("/api/library").json()["groups"]
                      for t in group["targets"] if t["label"].lower() == "lakitu skip")
        shown = client.get(f"/api/library/target/{target['index']}").json()
        for row in shown["approaches"]:
            entity, strategy = row["entity_key"], row["strategy"]
            for version in ("us", "jp"):
                practice = client.get("/api/ranks/standards", params={
                    "entity": entity, "version": version}).json()["strategies"][strategy]
                assert set(practice) == TIERS, (row["name"], version, practice)
                assert practice == service.ranks.ladders(entity, version)[strategy]
            assert row["ladder"] == service.ranks.ladders(entity, "us")[strategy]
        standard = next(row for row in shown["approaches"] if row["strategy"] == "Standard")
        assert standard["ladder"]["Mario"] >= 5.60
        assert standard["ladder"]["Bronze"] < 7


def test_seed_defaults_yield_but_explicit_edits_survive_sheet_refresh(tmp_path):
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps({"version": 1, "entities": {"segment:1": {
        "strategies": {"Standard": {"Mario": 5, "Gold": 6, "Silver": 7}},
        "jp_strategies": {"Standard": {"Mario": 4}}}}}))
    store = RankStandards(tmp_path / "user.json", seed_path=seed)
    store.load()
    fit = fit_ladder([560, 666])
    store.apply_sheet_ladders({"segment:1": {"strategies": {"Standard": fit}}})
    assert store.ladders("segment:1", "us")["Standard"] == fit
    assert store.ladders("segment:1", "jp")["Standard"] == fit
    # Choosing the OLD seed's number is still an explicit edit, even though
    # equality to a seed value alone cannot distinguish it from a default.
    store.set_threshold("segment:1", "Standard", "Mario", 5)
    store.set_threshold("segment:1", "Standard", "Mario", 4, version="jp")
    updated = fit_ladder([550, 700])
    store.apply_sheet_ladders({"segment:1": {"strategies": {"Standard": updated}}})
    store.load()
    assert store.ladders("segment:1", "us")["Standard"] == {**updated, "Mario": 5}
    assert store.ladders("segment:1", "jp")["Standard"] == {**updated, "Mario": 4}


@pytest.mark.parametrize("times", [[246], [246, 250], [1000, 1000, 1003]])
def test_shared_cutoffs_have_finite_scores_and_derived_capless_targets(times):
    ladder = {rank: round(seconds * 100) for rank, seconds in fit_ladder(times).items()}
    assert set(ladder) == TIERS
    for time in (min(times) - 3, min(times), max(times), max(times) * 2):
        assert 0 <= score_for(ladder, time) <= 100
        assert progress_for_time(ladder, time)
    for score in (100, 95, 90, 80, 70, 60, 45, 25, 10, 8, 6, 4, 2):
        assert time_for_score(ladder, score) is not None
    capless_one = time_for_score(ladder, 8)
    assert capless_one > max(times)
    assert rank_for(ladder, capless_one) == "Iron"
