"""ranks/scorecard.py -- the pure card builder.

`resolve_seed`'s contract here is the CONTROLLER's amendment to the task
brief's Step-1 sketch: `build_card` takes no `labels` parameter (it derives
star labels itself via `star_name`/`COURSE_NAMES`), and `resolve_seed(seed_key)`
returns `None` (deleted) or `(entity_key, label)` -- not a bare key -- so the
two Secret-row movements' labels come from the caller (who owns the real
segment record), not from a hardcoded string in this module."""
import json
from pathlib import Path

from sm64_events.ranks import scorecard, scoring

SEED = Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "data" / \
    "rank_standards.seed.json"


def _resolve(seed_key):
    return {"seg:bitdw-pipe": ("segment:41", "Bowser in the Dark World"),
            "seg:bitfs-pipe": ("segment:42", "Bowser in the Fire Sea")}.get(seed_key)


def test_card_keys_shape():
    keys = scorecard.card_keys(_resolve)
    assert len(keys) == 15 * 7 + 10
    assert keys[:7] == [f"star:1:{s}" for s in range(7)]
    assert keys[-10:] == ["star:19:0", "star:19:1", "star:24:0",
                           "segment:41", "segment:42", "star:18:0",
                           "star:21:0", "star:22:0", "star:20:0", "star:23:0"]


def test_a_deleted_movement_drops_its_tile_rather_than_drawing_a_dead_one():
    def resolve(seed_key):
        return None if seed_key == "seg:bitdw-pipe" else _resolve(seed_key)

    keys = scorecard.card_keys(resolve)
    assert len(keys) == 15 * 7 + 9
    assert "segment:41" not in keys
    assert "segment:42" in keys

    card = scorecard.build_card(you={}, goal={}, fold={}, resolve_seed=resolve)
    secret_row = card["rows"][-1]
    assert len(secret_row["tiles"]) == 9
    assert secret_row["sum"]["total"] == 9


def test_secret_row_pinned_against_the_template():
    secret_row = scorecard.build_card(you={}, goal={}, fold={}, resolve_seed=_resolve)["rows"][-1]
    assert secret_row["course_id"] is None
    assert secret_row["label"] == "Secret"
    assert [tile["key"] for tile in secret_row["tiles"]] == [
        "star:19:0", "star:19:1", "star:24:0", "segment:41", "segment:42",
        "star:18:0", "star:21:0", "star:22:0", "star:20:0", "star:23:0"]
    clocks = {tile["key"]: tile["clock"] for tile in secret_row["tiles"]}
    assert clocks["segment:41"] == "rta" and clocks["segment:42"] == "rta"
    assert clocks["star:19:0"] == "igt"


def test_movement_label_comes_from_resolve_seed_not_a_hardcoded_string():
    """The controller ruling: resolve_seed OWNS the movement's label (it has
    the real segment record); this module's own SECRET_ROW string is only a
    fallback for reading SECRET_ROW in isolation, never used once a resolver
    is supplied."""
    def resolve(seed_key):
        return {"seg:bitdw-pipe": ("segment:41", "custom label from the caller")}.get(seed_key)

    card = scorecard.build_card(you={}, goal={}, fold={}, resolve_seed=resolve)
    secret_row = card["rows"][-1]
    tile = next(t for t in secret_row["tiles"] if t["key"] == "segment:41")
    assert tile["label"] == "custom label from the caller"


def test_missing_side_leaves_both_sums():
    you = {"star:1:0": 4000, "star:1:1": 5000}
    goal = {"star:1:0": 4500}            # star:1:1 has no goal
    card = scorecard.build_card(you=you, goal=goal, fold={}, resolve_seed=_resolve)
    row = card["rows"][0]
    assert row["sum"] == {"you_cs": 4000, "goal_cs": 4500, "delta_cs": -500,
                           "counted": 1, "total": 7}
    tile0 = next(t for t in row["tiles"] if t["key"] == "star:1:0")
    tile1 = next(t for t in row["tiles"] if t["key"] == "star:1:1")
    assert tile0["delta_cs"] == -500
    assert tile1["you_cs"] == 5000 and tile1["goal_cs"] is None and tile1["delta_cs"] is None


def test_fold_skips_the_same_star_on_both_sides():
    you = {f"star:1:{s}": 1000 for s in range(7)}
    goal = {f"star:1:{s}": 900 for s in range(7)}
    card = scorecard.build_card(you=you, goal=goal, fold={1: 3}, resolve_seed=_resolve)
    row = card["rows"][0]
    assert row["sum"]["counted"] == 6
    assert row["sum"]["you_cs"] == 6000 and row["sum"]["goal_cs"] == 5400
    assert row["sum"]["total"] == 7
    folded = [t for t in row["tiles"] if t["folded"]]
    assert [t["key"] for t in folded] == ["star:1:3"]
    assert folded[0]["you_cs"] == 1000   # the tile itself still draws


def test_card_total_runs_the_same_sum_over_every_row():
    you = {f"star:1:{s}": 1000 for s in range(7)}
    goal = {f"star:1:{s}": 900 for s in range(7)}
    card = scorecard.build_card(you=you, goal=goal, fold={}, resolve_seed=_resolve)
    assert card["total"]["counted"] == 7
    assert card["total"]["you_cs"] == 7000 and card["total"]["goal_cs"] == 6300
    assert card["total"]["total"] == 15 * 7 + 10


def test_division_goal_round_trips():
    # Same seed-loading idiom as tests/test_ranks_scoring_seed.py -- no
    # RankStandards constructor is named "load_default" anywhere in the repo,
    # so read the bundled seed JSON directly, exactly like that file does.
    entities = json.loads(SEED.read_text())["entities"]
    ladders = entities["star:1:0"]["strategies"]
    ladder = scoring.best_ladder(ladders)
    assert len(scoring.defined_tiers(ladder)) >= 3, "fixture needs a ragged ladder"

    cs = scorecard.division_goal_cs(ladder, "Gold", "I")
    graded = scoring.progress_for_time(ladder, cs)
    assert (graded["tier"], graded["division"]) == ("Gold", "I")
    worse = scoring.progress_for_time(ladder, cs + 1)
    assert (worse["tier"], worse["division"]) != ("Gold", "I")


def test_division_goal_refuses_an_undefined_tier():
    assert scorecard.division_goal_cs({"Bronze": 5000}, "Master", "III") is None


def test_division_goal_returns_none_for_a_division_no_centisecond_reaches():
    """A real seeded ladder (star:8:1) whose Mario band spans only 5.0 score
    points across 3 real centiseconds (674-676cs): 673cs already grades
    Mario II and 674cs already grades Mario IV, so no integer centisecond
    ever grades Mario III on this entity. Found by sweeping all 4,580
    ladder/tier/division combinations in the bundled seed -- 32 of them hit
    this, all near the fastest tiers where the extrapolation slope is
    steepest. This is "no goal for this tile", not an error."""
    entities = json.loads(SEED.read_text())["entities"]
    ladder = scoring.best_ladder(entities["star:8:1"]["strategies"])
    assert ladder["Mario"] == 676          # pin the fixture so a seed update is visible here
    assert scorecard.division_goal_cs(ladder, "Mario", "III") is None
