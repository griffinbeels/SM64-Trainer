"""Automatic goals follow the real scope rating without becoming saved picks."""
import pytest

from import_fixture import make_client
from sm64_events.ranks import scorecard, scoring


@pytest.mark.parametrize("tier,division,expected", [
    ("Iron", "V", ("Iron", "IV")),
    ("Iron", "III", ("Iron", "II")),
    ("Iron", "I", ("Bronze", "V")),
    ("Bronze", "II", ("Bronze", "I")),
    ("Bronze", "I", ("Silver", "V")),
    ("Mario", "II", ("Mario", "I")),
    ("Mario", "I", ("Mario", "I")),
    (None, None, (None, None)),  # No rankable entries, or standards unavailable.
])
def test_automatic_goal_advances_exactly_one_subdivision(tier, division, expected):
    assert scorecard.automatic_goal(tier, division) == {
        "kind": "automatic", "tier": expected[0], "division": expected[1]}


@pytest.mark.parametrize("division", ["IV", "III", "II", "I"])
@pytest.mark.parametrize("ladder", [
    {"Bronze": 1000, "Silver": 800},
    {"Gold": 900},  # Capless uses the easiest defined anchor on ragged ladders.
])
def test_capless_goals_are_the_slowest_time_reaching_the_subdivision(ladder, division):
    cs = scorecard.division_goal_cs(ladder, "Iron", division)
    assert cs is not None
    at_goal = scoring.progress_for_time(ladder, cs)
    slower = scoring.progress_for_time(ladder, cs + 1)
    assert (at_goal["tier"], at_goal["division"]) == ("Iron", division)
    assert scoring.progression_key(slower["tier"], slower["division"]) < (
        scoring.progression_key("Iron", division))


def test_capless_floor_and_missing_ladders_have_no_finite_cutoff():
    assert scorecard.division_goal_cs({"Bronze": 1000}, "Iron", "V") is None
    assert scorecard.division_goal_cs({}, "Iron", "IV") is None


def _route(db, star):
    return "route:" + str(db.insert_route("One star", [
        {"need": 1, "candidates": [{"type": "star", "course": 1, "star": star}]}
    ], "2026-09-06T00:00:00Z"))


def test_automatic_goal_follows_scope_rank_and_updates_after_a_time_changes(tmp_path):
    with make_client(tmp_path) as (client, db, service):
        practiced = _route(db, 0)
        untouched = _route(db, 1)
        before = client.get("/api/scorecard", params={"scope": practiced}).json()
        assert before["goal"] == {"kind": "automatic", "tier": "Iron", "division": "IV"}
        assert before["goal_coverage"]["covered"] == 1
        assert client.post("/api/import/manual", json={
            "entity_key": "star:1:0", "strat_tag": "Standard", "time_cs": 4500
        }).status_code == 200

        goals = {}
        for scope in ("overall", practiced, untouched, "course:1"):
            rank = client.get("/api/marelo", params={"scope": scope}).json()
            watermarks = service.marelo_watermarks()
            card = client.get("/api/scorecard", params={"scope": scope}).json()
            goal = card["goal"]
            expected_key = min(scoring.progression_key(rank["tier"], rank["division"]) + 1,
                               scoring.progression_key("Mario", "I"))
            assert scoring.progression_key(goal["tier"], goal["division"]) == expected_key
            assert goal["kind"] == "automatic"
            assert service.marelo_watermarks() == watermarks
            assert db.get_state("scorecard_goal", None) is None
            goals[scope] = goal
        assert goals[practiced] != goals[untouched], "The scopes must exercise different ranks"
        assert goals[practiced] != before["goal"]


@pytest.mark.parametrize("manual", [
    {"kind": "division", "tier": "Bronze", "division": "I"},
    {"kind": "runner", "runner": "RONC3NA"},
    {"kind": "custom", "name": "My goal", "times": {"star:1:0": 4500}},
    {"kind": "multi", "sources": [
        {"kind": "division", "tier": "Bronze", "division": "I"},
        {"kind": "runner", "runner": "RONC3NA"}]},
])
def test_manual_choices_persist_across_scopes_and_clearing_restores_auto(tmp_path, manual):
    with make_client(tmp_path) as (client, db, _service):
        route = _route(db, 0)
        response = client.put("/api/scorecard/goal", json=manual)
        assert response.status_code == 200
        saved = response.json()["goal"]
        for scope in ("overall", route, "course:4", "overall"):
            goal = client.get("/api/scorecard", params={"scope": scope}).json()["goal"]
            if saved.get("kind") == "multi" and saved["sources"][0]["kind"] == "automatic":
                assert goal["sources"][1:] == saved["sources"][1:]
                rank = client.get("/api/marelo", params={"scope": scope}).json()
                assert goal["sources"][0] == scorecard.automatic_goal(rank["tier"], rank["division"])
            else:
                assert goal == saved
            assert db.get_state("scorecard_goal", None) == saved
        assert client.put("/api/scorecard/goal", json=None).status_code == 200
        assert client.get("/api/scorecard", params={"scope": route}).json()["goal"]["kind"] == "automatic"
        if manual["kind"] == "custom":
            assert client.get("/api/scorecard").json()["custom_goals"] == ["My goal"]


def test_empty_multi_restores_automatic_mode(tmp_path):
    with make_client(tmp_path) as (client, db, _service):
        assert client.put("/api/scorecard/goal", json={
            "kind": "multi", "sources": []}).status_code == 200
        assert db.get_state("scorecard_goal", None) is None
        assert client.get("/api/scorecard").json()["goal"]["kind"] == "automatic"
