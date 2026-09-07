"""Task 0126 round 2: every tier, not merely a nonempty ladder."""
import gzip
import json
from pathlib import Path

import pytest

from import_fixture import make_client
from sm64_events.library.ladders import fit_ladder, fit_payload
from sm64_events.ranks.classify import RANK_NAMES, rank_for
from sm64_events.ranks.scoring import progress_for_time, score_for, time_for_score
from sm64_events.ranks.scoring import progression_key
from sm64_events.core.timefmt import cs_of_frame, frame_at_or_after
from sm64_events.ranks.standards import RankStandards

TIERS = set(RANK_NAMES) - {"Iron"}


@pytest.mark.parametrize("times", [[1000], [1000] * 40, [246, 250],
                                  [556, 560, 576, 583, 596, 600],
                                  [1000] * 40 + [1300] * 40])
def test_every_tier_survives_sparse_tied_and_clustered_observations(times):
    ladder = fit_ladder(times)
    assert set(ladder) == TIERS
    assert ladder["Mario"] > min(times) / 100
    assert ladder["Bronze"] >= max(times) / 100
    assert list(ladder.values()) == sorted(set(ladder.values()))


def test_more_observations_refine_the_empirical_spread():
    before = fit_ladder([1000, 2000], quantise=lambda cs: cs)
    after = fit_ladder([1000] + [1100] * 30 + [2000], quantise=lambda cs: cs)
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
        assert standard["ladder"]["Bronze"] >= 6.66


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
    store.load()
    store.apply_sheet_ladders({"segment:1": {"strategies": {"Standard": updated}}})
    assert store.ladders("segment:1", "us")["Standard"] == {**updated, "Mario": 5}
    assert store.ladders("segment:1", "jp")["Standard"] == {**updated, "Mario": 4}


@pytest.mark.parametrize("times", [[246], [246, 250], [1000, 1000, 1003]])
def test_narrow_ladders_have_finite_scores_and_derived_capless_targets(times):
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


@pytest.mark.parametrize("best_frame", range(60, 90))
def test_every_saved_frame_is_one_subdivision_on_narrow_rows(best_frame):
    # Independent expected progression: all 45 positions, in order. Vary the
    # timer phase to catch 3/3/4cs rounding, not only a conveniently round time.
    best = cs_of_frame(best_frame)
    ladder = {rank: round(seconds * 100) for rank, seconds in fit_ladder(
        [best] * 57 + [cs_of_frame(best_frame + 1)]).items()}
    assert time_for_score(ladder, 99) == best
    positions = []
    for offset in range(45):
        progress = progress_for_time(ladder, cs_of_frame(best_frame + offset))
        positions.append(progression_key(progress["tier"], progress["division"]))
    assert positions == list(range(44, -1, -1))


def test_every_current_fitted_ladder_has_45_reachable_divisions():
    path = Path(__file__).resolve().parents[1] / "src/sm64_events/data/sheet_library.seed.json.gz"
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        payload = fit_payload(json.load(stream))
    for target in payload["targets"]:
        for kind in ("approaches", "subsections"):
            for item in target[kind]:
                for name in ("ladder", "ladder_jp"):
                    if name not in item:
                        continue
                    ladder = {r: round(t * 100) for r, t in item[name].items()}
                    reached = set()
                    # Probe each division edge and adjacent frames. This checks
                    # the real grader without traversing minutes of empty time.
                    for target_score in range(1, 101):
                        edge = time_for_score(ladder, target_score)
                        frame = frame_at_or_after(edge)
                        for candidate in (frame - 1, frame, frame + 1):
                            p = progress_for_time(ladder, cs_of_frame(candidate))
                            reached.add(progression_key(p["tier"], p["division"]))
                    assert reached == set(range(45)), (item["name"], name, reached)
