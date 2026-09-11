"""Compiled Overall consumers preserve PB identity, coverage and source context."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from sm64_events.library import board, ratings
from sm64_events.ranks import curves, history, scopes, scoring
from sm64_events.ranks.classify import display_cs
from sm64_events.tracking import marelo
from test_marelo_bridge import att, pb

KEY = "star:1:0"
GROUPS = [{"need": 1, "candidates": [KEY]}]


def curve(scale=1):
    return curves.compile_curve([[time * scale, score] for time, score in
                                 [(1000, 95), (1800, 70), (2000, 61.3),
                                  (3000, 25), (6000, 5)]])


class Standards:
    """A store boundary fixture; curve compilation/evaluation are production code."""
    calibration_revision = "first"

    def __init__(self):
        self.by_version = {"us": curve(), "jp": curve(.5)}

    def overall_curve(self, key, version=None):
        return self.by_version[version or "us"] if key == KEY else curves.from_ladder({})

    def ladders(self, key, version=None):
        # Deliberately unrelated strategy standards cannot grade Overall.
        return {"Manual": {"Mario": 1, "Gold": 2}} if key == KEY else {}

    def clock_for(self, key):
        return "igt"

    def to_json(self):
        return {"same": "strategy settings"}


def payload(*entries, name="Standard"):
    return {"sheet_revision": "unchanged", "targets": [{
        "entity_key": KEY, "label": "Star", "group": "g", "section": "s",
        "version": "jp", "subsections": [], "approaches": [{
            "name": name, "ids": ["one"], "entries": list(entries),
            "ladder": {"Mario": 1, "Gold": 2}, "ladder_jp": None}]}]}


def entry(runner="Runner", time_cs=2000, version=None):
    return {"runner": runner, "time_cs": time_cs, "version": version, "video": None}


def contextual_score(standards):
    def score(key, frames, context):
        progress = curves.progress_for_time(
            standards.overall_curve(key, context.get("game_version")), display_cs(frames))
        return progress["score"] if progress else None
    return score


def test_pb_average_and_community_use_complete_curve_nodes():
    standards = Standards()
    # This exact observation is an interior node, not a tier cutoff.
    expected = 61.3
    assert scoring.progress_for_time(curve()["ladder_cs"], 2000)["score"] != expected
    assert marelo.entity_scores([], standards, [KEY], "pb", [pb(frames=600)])[KEY] == expected
    assert marelo.entity_scores([att(igt_frames=600)], standards, [KEY], "avg10")[KEY] == expected
    rated = ratings.rate_runners(payload(entry()), standards, {})
    assert rated.scores["Runner"][KEY] == expected
    assert marelo.entity_ladders(standards, [KEY])[KEY] == curve()["ladder_cs"]


def test_history_keeps_latest_pb_per_rom_and_matches_current_marelo():
    standards = Standards()
    rows = [pb(id=1, frames=540, game_version="us", saved_utc="a"),
            pb(id=2, frames=300, game_version="jp", saved_utc="b"),
            pb(id=3, frames=900, game_version="us", saved_utc="c")]
    feed = marelo.pb_feed(rows, standards.clock_for)
    assert [(event["game_version"], event["timer_mode"]) for event in feed] == [
        ("us", "igt"), ("jp", "igt"), ("us", "igt")]
    series = history.history_series(feed, GROUPS, lambda *_: pytest.fail("legacy scorer called"),
                                    "pb", context_scorer=contextual_score(standards))
    current = scopes.aggregate(marelo.entity_scores([], standards, [KEY], "pb", rows), GROUPS)
    assert series[-1]["marelo"] == current["marelo"] == 61.3
    assert series[0]["marelo"] > series[-1]["marelo"]


def test_average_history_keeps_imported_regions_in_separate_windows():
    standards = Standards()
    runs = [replace(att(id=1, igt_frames=600, ended_utc="a"), game_version="us"),
            replace(att(id=2, igt_frames=300, ended_utc="b"), game_version="jp"),
            replace(att(id=3, igt_frames=1200, ended_utc="c"), game_version="us")]
    feed = marelo.successes_for(runs, standards.clock_for)
    current = marelo.entity_scores(runs, standards, [KEY], "avg10")
    series = history.history_series(feed, GROUPS, lambda *_: None, "avg10",
                                    context_scorer=contextual_score(standards))
    assert current[KEY] == series[-1]["marelo"] == 61.3
    assert {event["game_version"] for event in feed} == {"us", "jp"}


def test_legacy_attempts_keep_unknown_rom_and_current_selected_clock():
    legacy = SimpleNamespace(**{key: value for key, value in att(igt_frames=600).__dict__.items()
                                if key != "game_version"})
    feed = marelo.successes_for([legacy], Standards().clock_for)
    assert feed[0]["game_version"] is None
    assert feed[0]["timer_mode"] == "igt"


def test_missing_original_rom_curve_does_not_fall_back_to_current_rom():
    standards = Standards()
    standards.by_version["jp"] = curves.from_ladder({})
    assert marelo.entity_scores([], standards, [KEY], "pb",
                                [pb(frames=300, game_version="jp")]) == {}
    assert marelo.entity_scores([], standards, [KEY], "pb",
                                [pb(frames=300, timer_mode="rta")]) == {}


def test_board_time_and_replay_follow_highest_scoring_original_rom_pb():
    rows = [pb(id=1, frames=540, game_version="us", attempt_id=11),
            pb(id=2, frames=300, game_version="jp", attempt_id=22)]
    selected = board.you_pb_by_entity(rows, Standards(), [KEY])[KEY]
    assert selected == {"time_cs": 1800, "attempt_id": 11,
                        "game_version": "us", "timer_mode": "igt"}


def test_compiled_rankability_preserves_best_k_equal_slots_and_absence():
    rankable = scopes.rankable_entities({KEY: curve(), "b": curve(),
                                        "c": curve(), "empty": curves.from_ladder({})})
    assert set(rankable) == {KEY, "b", "c"}
    groups = scopes.entity_groups("route:1", rankable=rankable, segment_courses={}, routes=[{
        "id": 1, "steps": [{"need": 2, "candidates": [
            {"type": "star", "course": 1, "star": 0}]}]}])
    assert groups == GROUPS  # need is capped at the number of rankable candidates.
    result = scopes.aggregate({KEY: 62., "b": 0.}, [
        {"need": 2, "candidates": [KEY, "b", "c"]},
        {"need": 1, "candidates": ["c"]}])
    assert result["n"] == 3 and result["practiced"] == 2
    assert result["marelo"] == pytest.approx(62 / 3)


def test_board_scoring_uses_actual_rom_while_library_visibility_stays_broad():
    source = payload(entry("Only JP", version="jp"), entry("Unannotated"))
    assert "Only JP" in ratings.runner_times(source, {}, version="us")
    us = ratings.rate_runners(source, Standards(), {}, version="us")
    jp = ratings.rate_runners(source, Standards(), {}, version="jp")
    assert set(us.scores) == {"Unannotated"}
    assert set(jp.scores) == {"Unannotated", "Only JP"}
    assert us.scores["Unannotated"][KEY] == 61.3  # target.version never supplies ROM.


def test_real_time_rows_cannot_supply_igt_board_scores():
    source = payload(entry(), name="[N64 REAL TIME] w/ sub")
    assert ratings.runner_times(source, {})["Runner"][KEY] == 2000
    assert ratings.rate_runners(source, Standards(), {}).scores == {}


def test_distinct_runner_ids_cannot_collapse_into_one_display_name():
    source = payload({**entry(), "runner_id": "person-one"},
                     {**entry(time_cs=3000), "runner_id": "person-two"})
    assert set(ratings.rate_runners(source, Standards(), {}).scores) == {
        "id:person-one", "id:person-two"}


def test_board_uses_effective_policy_for_unannotated_observations():
    standards = Standards()
    standards.by_version["us"]["metadata"]["unannotated_region"] = "exclude"
    source = payload(entry("Unknown"), entry("US", version="us"))
    assert set(ratings.rate_runners(source, standards, {}).scores) == {"US"}


@pytest.mark.parametrize("bad", [0, -10, None, "2000", float("nan"), float("inf"), True])
def test_invalid_observations_never_become_board_times(bad):
    source = payload(entry(time_cs=bad))
    assert ratings.rate_runners(source, Standards(), {}).scores == {}


def test_cache_tracks_effective_curve_revision_and_same_timestamp_corrections():
    standards = Standards()
    library = SimpleNamespace(payload=payload(entry()), revision="unchanged")
    cache = board.RatingsCache()
    first = cache.current(library, {}, standards, version="us")
    assert cache.current(library, {}, standards, version="us") is first
    standards.by_version["us"] = curve(.8)
    standards.calibration_revision = "second"
    second = cache.current(library, {}, standards, version="us")
    assert second is not first and second.scores != first.scores
    library.payload["targets"][0]["approaches"][0]["entries"][0]["time_cs"] = 1800
    third = cache.current(library, {}, standards, version="us")
    assert third is not second and third.times["Runner"][KEY] == 1800
