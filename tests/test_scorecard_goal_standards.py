"""Reported targets must grade on the same row the Library link opens."""
import pytest

from import_fixture import make_client
from sm64_events.ranks.scorecard import division_goal_cs
from sm64_events.ranks.scoring import best_ladder, progress_for_time


def _tiles(client, scope="overall"):
    card = client.get("/api/scorecard", params={"scope": scope}).json()
    return {tile["key"]: tile for row in card["rows"] for tile in row["tiles"]}


@pytest.mark.parametrize("version", ["us", "jp"])
@pytest.mark.parametrize("course,star,active,canonical", [
    (4, 6, "100c + Race · Standard", "Big Penguin Race + 100c"),
    (8, 2, "Pillarless", "Standard"),
])
def test_rank_target_uses_the_linked_library_row_not_another_routes_minimum(
        tmp_path, version, course, star, active, canonical):
    with make_client(tmp_path) as (client, _db, service):
        key = f"star:{course}:{star}"
        service.ranks.grading_version = version
        service.strat_by_star[(course, star)] = active
        targets = client.get(f"/api/library/entity/{key}").json()["targets"]
        row = next(row for target in targets for row in target["approaches"]
                   if row.get("strategy") == canonical)
        ladder = {tier: round(seconds * 100) for tier, seconds in
                  row.get("ladder_jp" if version == "jp" else "ladder", row["ladder"]).items()}
        expected = division_goal_cs(ladder, "Iron", "II")
        wrong = division_goal_cs(best_ladder(service.ranks.ladders(key)), "Iron", "II")
        assert expected != wrong, "Fixture must expose the unrelated faster route"
        assert client.put("/api/scorecard/goal", json={
            "kind": "division", "tier": "Iron", "division": "II"}).status_code == 200
        tile = _tiles(client)[key]
        assert tile["strat"] == canonical
        assert tile["goal_cs"] == expected
        assert progress_for_time(ladder, tile["goal_cs"])["division"] == "II"
        assert progress_for_time(ladder, tile["goal_cs"] + 1)["division"] == "III"
        # A PB far ahead of the selected division must not move the goal.
        assert client.post("/api/import/manual", json={
            "entity_key": key, "strat_tag": active, "time_cs": 1783}).status_code == 200
        assert _tiles(client)[key]["goal_cs"] == expected


@pytest.mark.parametrize("active", [None, "Standard"])
def test_automatic_and_manual_share_targets_and_unpracticed_100c_uses_its_companion(tmp_path, monkeypatch, active):
    with make_client(tmp_path) as (client, db, service):
        service.strat_by_star[(4, 6)] = active
        route = db.insert_route("Race only", [{"need": 1, "candidates": [
            {"type": "star", "course": 4, "star": 6}]}], "2026-09-06T00:00:00Z")
        monkeypatch.setattr("sm64_events.server.scorecard_api._score_scope",
                            lambda *_: {"tier": "Bronze", "division": "I"})
        automatic = _tiles(client, f"route:{route}")["star:4:6"]
        assert automatic["strat"] == "Big Penguin Race + 100c"
        client.put("/api/scorecard/goal", json={"kind": "division", "tier": "Silver", "division": "V"})
        manual = _tiles(client, f"route:{route}")["star:4:6"]
        assert automatic["goal_cs"] == manual["goal_cs"]


def test_saved_set_adds_entries_across_scopes_and_can_be_selected_as_one_source(tmp_path):
    with make_client(tmp_path) as (client, db, _service):
        segment = db.segment_defs()[0]["id"]
        for times in ({"star:1:0": 1000}, {f"segment:{segment}": 2000}, {"star:4:6": 9000}):
            response = client.put("/api/scorecard/goal", json={"kind": "multi", "sources": [
                {"kind": "division", "tier": "Bronze", "division": "I"},
                {"kind": "runner", "runner": "RONC3NA"},
                {"kind": "custom", "name": "Practice set", "times": times}]})
            assert response.status_code == 200
        assert db.get_state("scorecard_custom_goals", {})["Practice set"] == {
            "star:1:0": 1000, f"segment:{segment}": 2000, "star:4:6": 9000}
        choice = response.json()["goal"]
        client.put("/api/scorecard/goal", json={**choice, "sources": choice["sources"][:-1]})
        assert client.put("/api/scorecard/goal", json=choice).json()["goal"] == choice
        assert client.get("/api/scorecard?scope=course:4").json()["goal"] == choice


def test_multiple_legacy_divisions_keep_only_the_last_and_never_remove_rank(tmp_path):
    with make_client(tmp_path) as (client, db, _service):
        old = {"kind": "multi", "sources": [
            {"kind": "division", "tier": "Gold", "division": "I"},
            {"kind": "runner", "runner": "RONC3NA"},
            {"kind": "division", "tier": "Iron", "division": "II"}]}
        db.set_state("scorecard_goal", old)
        goal = client.get("/api/scorecard").json()["goal"]
        assert goal["sources"] == [old["sources"][2], old["sources"][1]]
        assert db.get_state("scorecard_goal", None) == old  # Reads never migrate saved choices.


def test_a_rejected_combined_save_does_not_partially_write_a_custom_set(tmp_path):
    with make_client(tmp_path) as (client, db, _service):
        response = client.put("/api/scorecard/goal", json={"kind": "multi", "sources": [
            {"kind": "custom", "name": "Unfinished set", "times": {"star:1:0": 1000}},
            {"kind": "division", "tier": "Unknown", "division": "I"}]})
        assert response.status_code == 422
        assert db.get_state("scorecard_custom_goals", {}) == {}
        assert db.get_state("scorecard_goal", None) is None


def test_selecting_the_faster_strategy_changes_both_target_and_library_identity(tmp_path):
    with make_client(tmp_path) as (client, _db, service):
        service.strat_by_star[(8, 2)] = "Nuts Pless"
        client.put("/api/scorecard/goal", json={"kind": "division", "tier": "Iron", "division": "II"})
        tile = _tiles(client)["star:8:2"]
        assert tile["strat"] == "Nuts Pless"
        ladder = service.ranks.ladder_cs("star:8:2", "Nuts Pless")
        assert tile["goal_cs"] == division_goal_cs(ladder, "Iron", "II")
        assert tile["goal_cs"] != division_goal_cs(service.ranks.ladder_cs("star:8:2", "Standard"), "Iron", "II")
