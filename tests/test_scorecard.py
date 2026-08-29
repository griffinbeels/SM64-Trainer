"""ranks/scorecard.py -- the pure card builder.

Round 6 (2026-08-24) made composition scope-driven and cell-exact: the
Overall card is `template_rows()` (his canonical 100c pairing applied -- the
100-coin cell combined with its companion star, usually the reds star, five
named exceptions), the Secret row is all stars (the Bowser goals moved onto
the reds stars, "not the BITS entry for now"), and a route scope composes
one row per route step-group through the same cell rule."""
import json
from pathlib import Path

import pytest

from sm64_events.memory.addresses import STAR_NAMES
from sm64_events.ranks import scorecard, scoring

SEED = Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "data" / \
    "rank_standards.seed.json"

# His pairing list, verbatim from round 6 -- course -> the star the 100c cell
# is combined with. The builder derives the ten defaults by the "Red Coins"
# name rule and the five exceptions from stored names; THIS table is the
# independent copy his message is the source of, so a drift in either
# direction goes red here.
HIS_PAIRINGS = {
    1: "Find the 8 Red Coins",            # BOB
    2: "Red Coins on the Floating Isle",  # WF
    3: "Red Coins on the Ship Afloat",    # JRB
    4: "Big Penguin Race",                # CCM (exception)
    5: "Seek the 8 Red Coins",            # BBH
    6: "Elevate for 8 Red Coins",         # HMC
    7: "Hot-Foot-It into the Volcano",    # LLL (exception)
    8: "Pyramid Puzzle",                  # SSL (exception)
    9: "Pole-Jumping for Red Coins",      # DDD
    10: "Shell Shreddin' for Red Coins",  # SL
    11: "Go to Town for Red Coins",       # WDW
    12: "Scary 'Shrooms, Red Coins",      # TTM
    13: "Wiggler's Red Coins",            # THI
    14: "Stomp on the Thwomp",            # TTC (exception)
    15: "The Big House in the Sky",       # RR (exception)
}


def test_every_companion_matches_his_list_verbatim():
    for course_id, star_label in HIS_PAIRINGS.items():
        star_id = scorecard.hundred_coin_companion(course_id)
        assert STAR_NAMES[course_id][star_id] == star_label, course_id


def test_template_rows_have_six_cells_with_the_combined_100c_cell_in_place():
    rows = scorecard.template_rows()
    assert len(rows) == 16
    for row in rows[:15]:
        assert len(row["entries"]) == 6, row["label"]
        course_id = row["course_id"]
        companion = scorecard.hundred_coin_companion(course_id)
        keys = [key for key, _label, _clock in row["entries"]]
        # the combined cell sits at the companion's own template position,
        # carries the 100c ENTITY, and the companion has no cell of its own
        assert keys[min(companion, 5)] == f"star:{course_id}:6"
        assert f"star:{course_id}:{companion}" not in keys
    combined = dict(
        (key, label) for key, label, _clock in rows[0]["entries"])
    assert combined["star:1:6"] == "Find the 8 Red Coins + 100c"
    ccm = dict((key, label) for key, label, _clock in rows[3]["entries"])
    assert ccm["star:4:6"] == "Big Penguin Race + 100c"


def test_card_keys_shape():
    """105 star keys: 90 course lines (a 100c cell covers two stars) + 15
    Secret lines -- the 120 stars of his round-9 count. The fights card
    joins only when the caller resolves fight segments."""
    keys = scorecard.card_keys(scorecard.template_rows())
    assert len(keys) == 15 * 6 + 15
    assert keys[:6] == ["star:1:0", "star:1:1", "star:1:2", "star:1:6",
                        "star:1:4", "star:1:5"]
    assert keys[-15:] == ["star:19:0", "star:19:1", "star:24:0",
                           "star:16:0", "star:17:0", "star:18:0",
                           "star:21:0", "star:22:0", "star:20:0", "star:23:0",
                           "star:0:0", "star:0:1", "star:0:2",
                           "star:0:3", "star:0:4"]

    with_fights = scorecard.template_rows([("segment:9", "Bowser Battle 1")])
    assert [row["label"] for row in with_fights[-2:]] == [
        scorecard.FIGHTS_LABEL, "Secret"]
    assert len(scorecard.card_keys(with_fights)) == 15 * 6 + 15 + 1


def test_secret_row_is_all_stars_with_the_bowser_reds():
    """Round 6: "Bowser stages should also should have goals based on red
    coins times, not the BITS entry for now" -- the two course-entry
    MOVEMENTS left the Secret row; the three Bowser reds stars carry the
    template's own labels."""
    secret = scorecard.template_rows()[-1]
    assert secret["course_id"] is None and secret["label"] == "Secret"
    keys = [key for key, _label, _clock in secret["entries"]]
    # Round 9 restored the five castle secrets ("all 120 stars"); they close
    # the row so the sheet-faithful ten keep their order.
    assert keys == ["star:19:0", "star:19:1", "star:24:0",
                    "star:16:0", "star:17:0", "star:18:0",
                    "star:21:0", "star:22:0", "star:20:0", "star:23:0",
                    "star:0:0", "star:0:1", "star:0:2",
                    "star:0:3", "star:0:4"]
    assert all(clock == "igt" for _key, _label, clock in secret["entries"])
    labels = {key: label for key, label, _clock in secret["entries"]}
    assert labels["star:16:0"] == "Bowser in the Dark World Red Coins"
    assert labels["star:17:0"] == "Bowser in the Fire Sea Red Coins"
    assert labels["star:18:0"] == "Bowser in the Sky Red Coins"
    assert labels["star:20:0"] == "Cavern of the Metal Cap"
    assert labels["star:21:0"] == "Tower of the Wing Cap"
    assert labels["star:22:0"] == "Vanish Cap Under the Moat"
    assert labels["star:23:0"] == "Wing Mario Over the Rainbow"
    assert labels["star:24:0"] == "The Secret Aquarium"


def test_missing_side_leaves_both_sums():
    you = {"star:1:0": 4000, "star:1:1": 5000}
    goal = {"star:1:0": 4500}            # star:1:1 has no goal
    card = scorecard.build_card(scorecard.template_rows(), you=you, goal=goal)
    row = card["rows"][0]
    assert row["sum"] == {"you_cs": 4000, "goal_cs": 4500, "delta_cs": -500,
                           "counted": 1, "total": 6}
    tile1 = next(t for t in row["tiles"] if t["key"] == "star:1:1")
    assert tile1["you_cs"] == 5000 and tile1["goal_cs"] is None and tile1["delta_cs"] is None


def test_card_total_runs_the_same_sum_over_every_row():
    keys = [key for key, _l, _c in scorecard.template_rows()[0]["entries"]]
    you = {key: 1000 for key in keys}
    goal = {key: 900 for key in keys}
    card = scorecard.build_card(scorecard.template_rows(), you=you, goal=goal)
    assert card["total"]["counted"] == 6
    assert card["total"]["you_cs"] == 6000 and card["total"]["goal_cs"] == 5400
    assert card["total"]["total"] == 15 * 6 + 15


def _route(steps):
    return {"steps": steps}


def test_route_rows_apply_the_cell_rule_and_wear_the_course_name():
    """A 120-star-style course visit holding both the 100c and its companion
    merges them into ONE combined cell; the row wears the course's name."""
    route = _route([{"need": 7, "candidates": [
        {"type": "star", "course": 1, "star": star_id} for star_id in range(7)]}])
    rows = scorecard.rows_for_route(route, segment_labels={})
    assert len(rows) == 1
    assert rows[0]["label"] == "Bob-omb Battlefield"
    keys = [key for key, _l, _c in rows[0]["entries"]]
    assert len(keys) == 6 and "star:1:3" not in keys and "star:1:6" in keys


def test_route_rows_keep_a_companion_without_its_100c():
    """16-star style: the reds star alone stays its own cell -- the merge
    only fires when BOTH halves are in the row."""
    route = _route([{"need": 1, "candidates": [
        {"type": "star", "course": 1, "star": 3}]}])
    rows = scorecard.rows_for_route(route, segment_labels={})
    assert [key for key, _l, _c in rows[0]["entries"]] == ["star:1:3"]
    assert rows[0]["entries"][0][1] == "Find the 8 Red Coins"


def test_route_segments_bucket_by_kind():
    """Round 9's grouping: a FIGHT (named by the caller, by category) gets
    the fights card; a segment with a course joins that course's card; a
    courseless non-fight (an included-back castle movement) lands in the
    Secret card -- the misc bucket, like the reference sheet's own Secret
    column. Fights then Secret close the set, after the course cards."""
    route = _route([
        {"need": 1, "candidates": [{"type": "segment", "segment_id": 41}]},
        {"need": 1, "candidates": [{"type": "segment", "segment_id": 9}]},
        {"need": 1, "candidates": [{"type": "segment", "segment_id": 77}]},
        {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}])
    rows = scorecard.rows_for_route(
        route,
        segment_labels={41: "LBLJ", 9: "Bowser Battle 1", 77: "DDD Entry"},
        segment_courses={77: 9},
        fight_segment_ids={9})
    assert [row["label"] for row in rows] == [
        "Dire, Dire Docks", "Whomp's Fortress",
        scorecard.FIGHTS_LABEL, "Secret"]
    by_label = {row["label"]: row for row in rows}
    assert by_label["Secret"]["entries"] == [("segment:41", "LBLJ", "rta")]
    assert by_label[scorecard.FIGHTS_LABEL]["entries"] == [
        ("segment:9", "Bowser Battle 1", "rta")]
    assert by_label["Dire, Dire Docks"]["entries"] == [
        ("segment:77", "DDD Entry", "rta")]


def test_route_revisits_merge_into_one_course_card():
    """Round 9: "BOB is all of the bobomb battlefield stars" -- a course
    visited twice is ONE card at its first-touch position, duplicate
    entities kept once."""
    route = _route([
        {"need": 1, "candidates": [{"type": "star", "course": 6, "star": 0}]},
        {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]},
        {"need": 2, "candidates": [
            {"type": "star", "course": 6, "star": 0},
            {"type": "star", "course": 6, "star": 1}]}])
    rows = scorecard.rows_for_route(route, segment_labels={})
    assert [row["label"] for row in rows] == [
        "Hazy Maze Cave", "Whomp's Fortress"]
    assert [key for key, _l, _c in rows[0]["entries"]] == [
        "star:6:0", "star:6:1"]


def test_route_castle_secrets_and_bowser_reds_share_the_secret_card():
    route = _route([
        {"need": 1, "candidates": [{"type": "star", "course": 0, "star": 3}]},
        {"need": 1, "candidates": [{"type": "star", "course": 16, "star": 0}]}])
    rows = scorecard.rows_for_route(route, segment_labels={})
    assert len(rows) == 1 and rows[0]["label"] == "Secret"
    labels = {key: label for key, label, _c in rows[0]["entries"]}
    assert labels["star:0:3"] == "MIPS 1st Star"
    assert labels["star:16:0"] == "Bowser in the Dark World Red Coins"


def test_route_rows_drop_a_deleted_segments_cell_and_an_empty_step():
    route = _route([{"need": 1, "candidates": [
        {"type": "segment", "segment_id": 999}]}])
    assert scorecard.rows_for_route(route, segment_labels={}) == []


def test_a_course_visit_drops_the_steps_own_star_count_label():
    """Round 8: "We also don't need the 'DDD -- 3 stars' or 'WDW -- 7 stars'
    the '-- X stars' count. Just the name of the course." The corpus really
    does author that label (`corpus_vocab._merge_label`) and the Run tab
    still shows it -- the scorecard prefers the course's own name."""
    route = _route([{"label": "WF — 7 stars", "need": 2, "candidates": [
        {"type": "star", "course": 2, "star": 0},
        {"type": "star", "course": 2, "star": 1}]}])
    rows = scorecard.rows_for_route(route, segment_labels={})
    assert rows[0]["label"] == "Whomp's Fortress"


def test_card_keys_deduplicates_a_route_that_revisits_an_entity():
    route = _route([
        {"need": 1, "candidates": [{"type": "star", "course": 1, "star": 0}]},
        {"need": 1, "candidates": [{"type": "star", "course": 1, "star": 0}]}])
    rows = scorecard.rows_for_route(route, segment_labels={})
    assert scorecard.card_keys(rows) == ["star:1:0"]


def test_without_keys_drops_excluded_cells_and_the_rows_they_empty():
    """Round 7 item 3: the card reads the same exclusion set the scope's own
    rating drops, so a movement the route ranking ignores draws no cell --
    and a step that was ONLY that movement draws no row at all."""
    route = _route([
        {"need": 1, "candidates": [{"type": "segment", "segment_id": 41}]},
        {"need": 2, "candidates": [
            {"type": "star", "course": 1, "star": 0},
            {"type": "segment", "segment_id": 42}]}])
    rows = scorecard.rows_for_route(
        route, segment_labels={41: "Lakitu Skip", 42: "LBLJ"})
    assert len(rows) == 2

    kept = scorecard.without_keys(rows, {"segment:41", "segment:42"})
    assert len(kept) == 1, "a row emptied by the filter must not draw"
    assert [key for key, _l, _c in kept[0]["entries"]] == ["star:1:0"]


def test_without_keys_leaves_an_unexcluded_card_untouched():
    rows = scorecard.template_rows()
    assert scorecard.without_keys(rows, set()) == rows
    assert scorecard.without_keys(rows, {"segment:999"}) == rows


def test_rows_for_course_serves_main_secret_and_refuses_the_castle():
    assert len(scorecard.rows_for_course(4)) == 1
    assert len(scorecard.rows_for_course(4)[0]["entries"]) == 6
    bitdw = scorecard.rows_for_course(16)[0]
    assert [key for key, _l, _c in bitdw["entries"]] == ["star:16:0"]
    assert bitdw["label"] == "Bowser in the Dark World"
    # Course 0 = the five castle secrets, real cells since round 9.
    castle = scorecard.rows_for_course(0)[0]
    assert len(castle["entries"]) == 5
    with pytest.raises(LookupError):
        scorecard.rows_for_course(25)


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


def test_division_goal_snaps_to_at_least_when_no_centisecond_lands_exactly():
    """A real seeded ladder (star:8:1) whose Mario band spans only 5.0 score
    points across 3 real centiseconds (674-676cs): no integer centisecond
    grades EXACTLY Mario III on this entity. Until round 12 that was "no
    goal for this tile" -- and four of his live tiles showed "set a
    time..." under a Metal goal for exactly this reason (his ladders put
    3cs across Get a Hand's whole Metal tier). His ruling: "We should never
    be missing a tier like this in our system." The resolver now returns
    the slowest centisecond grading AT LEAST the asked division -- reaching
    it honours the goal -- and still resolves identically wherever the
    exact division is reachable (the sweep companion below)."""
    entities = json.loads(SEED.read_text())["entities"]
    ladder = scoring.best_ladder(entities["star:8:1"]["strategies"])
    assert ladder["Mario"] == 676          # pin the fixture so a seed update is visible here
    goal = scorecard.division_goal_cs(ladder, "Mario", "III")
    assert goal is not None
    graded = scoring.progress_for_time(ladder, goal)
    assert graded["tier"] == "Mario"
    assert graded["division"] in ("I", "II", "III"), (
        "the snapped goal must grade AT LEAST Mario III")
    # ...and one centisecond slower must NOT meet Mario III any more, or the
    # goal is not the SLOWEST honouring time.
    slower = scoring.progress_for_time(ladder, goal + 1)
    assert (slower["tier"], slower["division"]) not in (
        ("Mario", "I"), ("Mario", "II"), ("Mario", "III"))


def test_division_goal_covers_every_defined_tier_and_division_in_the_seed():
    """Round 12's whole point, as a sweep: for every seeded entity, every
    tier its best ladder defines resolves a goal for all five divisions --
    zero holes (was 32 before the at-least snap)."""
    entities = json.loads(SEED.read_text())["entities"]
    holes = []
    for key, spec in entities.items():
        ladder = scoring.best_ladder(spec["strategies"])
        for tier in ladder:
            for division in ("I", "II", "III", "IV", "V"):
                if scorecard.division_goal_cs(ladder, tier, division) is None:
                    holes.append((key, tier, division))
    assert not holes, holes[:10]
