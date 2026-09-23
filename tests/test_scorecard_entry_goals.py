"""Per-entry goals: pins set from a Practice card, the per-entry automatic
floor, and `/entry_goals` agreeing with the Scorecard tile it mirrors."""
import pytest

from import_fixture import make_client
from sm64_events.ranks.scorecard import (
    division_goal_cs, entry_automatic_goal, hundred_coin_companion)
from sm64_events.ranks.scoring import progress_for_time, progression_key

KEY = "star:1:0"


def _tiles(client, scope="overall"):
    card = client.get("/api/scorecard", params={"scope": scope}).json()
    return {tile["key"]: tile for row in card["rows"] for tile in row["tiles"]}


def _entry_goals(client, keys, scope="overall"):
    response = client.get("/api/scorecard/entry_goals",
                          params={"scope": scope, "keys": ",".join(keys)})
    assert response.status_code == 200, response.text
    return response.json()["goals"]


def _pin(client, goal, key=KEY):
    response = client.put("/api/scorecard/entry_goal", json={"key": key, "goal": goal})
    assert response.status_code == 200, response.text
    return response.json()


def _ladder(client, service, key=KEY):
    return service.ranks.ladder_cs(key, _tiles(client)[key]["strat"])


# -- the per-entry automatic floor, pure ------------------------------------

LADDER = {"Mario": 1000, "Diamond": 1100, "Gold": 1200, "Bronze": 1500}
SCOPE_TOAD_V = {"kind": "automatic", "tier": "Bronze", "division": "V"}


def _grade(ladder, cs):
    progress = progress_for_time(ladder, cs)
    return progress["tier"], progress["division"]


def test_holding_the_scope_goal_raises_the_entry_one_step_above_yours():
    you = division_goal_cs(LADDER, "Gold", "II")
    assert _grade(LADDER, you) == ("Gold", "II")
    goal = entry_automatic_goal(LADDER, you, SCOPE_TOAD_V)
    assert (goal["kind"], goal["tier"], goal["division"]) == ("automatic", "Gold", "I")


def test_below_the_scope_goal_keeps_the_scope_goal():
    slow = 2500
    assert _grade(LADDER, slow)[0] == "Iron"
    assert entry_automatic_goal(LADDER, slow, SCOPE_TOAD_V) == SCOPE_TOAD_V
    assert entry_automatic_goal(LADDER, None, SCOPE_TOAD_V) == SCOPE_TOAD_V
    assert entry_automatic_goal({}, 900, SCOPE_TOAD_V) == SCOPE_TOAD_V


@pytest.mark.parametrize("ladder,top", [
    (LADDER, ("Mario", "I")),
    ({"Gold": 1000, "Bronze": 1500}, ("Gold", "I")),   # a ragged ladder's own top
])
def test_the_floor_caps_at_the_top_of_the_entrys_ladder(ladder, top):
    goal = entry_automatic_goal(ladder, 500, SCOPE_TOAD_V)
    assert (goal["tier"], goal["division"]) == top


def test_no_scope_rank_still_floors_on_your_own_time():
    none = {"kind": "automatic", "tier": None, "division": None}
    goal = entry_automatic_goal(LADDER, division_goal_cs(LADDER, "Gold", "II"), none)
    assert (goal["tier"], goal["division"]) == ("Gold", "I")


# -- the floor through the real card -----------------------------------------

def test_a_star_you_already_hold_shows_one_step_above_your_rank(tmp_path):
    with make_client(tmp_path) as (client, _db, service):
        strat = _tiles(client)[KEY]["strat"]
        ladder = _ladder(client, service)
        low, high = (division_goal_cs(ladder, "Gold", "II"),
                     division_goal_cs(ladder, "Gold", "I"))
        assert low and high and low > high, "fixture star needs a Gold ladder"
        assert client.post("/api/import/manual", json={
            "entity_key": KEY, "strat_tag": strat,
            "time_cs": (low + high) // 2 + 1}).status_code == 200
        tile = _tiles(client)[KEY]
        assert tile["strat"] == strat
        assert _grade(ladder, tile["you_cs"]) == ("Gold", "II")
        scope_goal = client.get("/api/scorecard").json()["goal"]
        assert progression_key(scope_goal["tier"], scope_goal["division"]) < (
            progression_key("Gold", "II")), "the scope's automatic goal must be easier"

        assert tile["goal_cs"] == division_goal_cs(ladder, "Gold", "I")
        goal = _entry_goals(client, [KEY])[KEY]
        assert goal["source"]["kind"] == "automatic"
        assert (goal["source"]["tier"], goal["source"]["division"]) == ("Gold", "I")
        assert goal["goal_cs"] == tile["goal_cs"] and goal["achieved"] is False

        # An untouched star on the same card keeps the scope's own goal.
        other = _entry_goals(client, ["star:1:1"])["star:1:1"]
        assert (other["source"]["tier"], other["source"]["division"]) == (
            scope_goal["tier"], scope_goal["division"])


# -- pins -------------------------------------------------------------------

def test_division_pin_round_trips_onto_the_tile_and_clears(tmp_path):
    with make_client(tmp_path) as (client, db, service):
        ladder = _ladder(client, service)
        answer = _pin(client, {"kind": "division", "tier": "Gold", "division": "I"})
        expected = division_goal_cs(ladder, "Gold", "I")
        assert answer["pinned"] is True and answer["goal_cs"] == expected
        assert answer["source"] == {"kind": "division", "tier": "Gold",
                                    "division": "I", "label": "Gold I"}
        assert db.get_state("scorecard_entry_goals", None) == {
            KEY: {"kind": "division", "tier": "Gold", "division": "I"}}

        tiles = _tiles(client)
        assert tiles[KEY]["goal_cs"] == expected
        assert tiles[KEY]["goal_pinned"] is True and tiles[KEY]["goal_source"] is None
        assert tiles["star:1:1"]["goal_pinned"] is False
        # Pins are per entity: every scope reads the same one.
        assert _entry_goals(client, [KEY], scope="course:1")[KEY]["pinned"] is True
        # No keys = every pinned key.
        omitted = client.get("/api/scorecard/entry_goals").json()["goals"]
        assert list(omitted) == [KEY]

        cleared = _pin(client, None)
        assert cleared["pinned"] is False and cleared["source"]["kind"] == "automatic"
        assert _tiles(client)[KEY]["goal_pinned"] is False
        assert db.get_state("scorecard_entry_goals", None) == {}


def test_runner_and_time_pins_replace_the_fastest_offer(tmp_path):
    with make_client(tmp_path) as (client, _db, _service):
        # A multi goal whose fastest offer would otherwise win this entry.
        options = client.get("/api/scorecard/entry_goal_options",
                             params={"key": KEY}).json()
        fastest, slowest = options["runners"][0], options["runners"][-1]
        assert fastest["cs"] < slowest["cs"]
        assert client.put("/api/scorecard/goal", json={"kind": "runner",
                          "runner": fastest["runner"]}).status_code == 200
        assert _tiles(client)[KEY]["goal_cs"] == fastest["cs"]

        answer = _pin(client, {"kind": "runner", "runner": slowest["runner"]})
        assert answer["goal_cs"] == slowest["cs"]
        assert answer["source"] == {"kind": "runner", "label": slowest["runner"],
                                    "runner": slowest["runner"]}
        assert _tiles(client)[KEY]["goal_cs"] == slowest["cs"]

        assert _pin(client, {"kind": "time", "cs": 10500})["source"] == {
            "kind": "time", "label": "sub 1'45\""}
        snapped = _pin(client, {"kind": "time", "cs": 4501})
        assert snapped["goal_cs"] == 4503          # the next time the timer can show
        assert snapped["source"]["label"] == "45\"03"
        assert _tiles(client)[KEY]["goal_cs"] == 4503


def test_a_pin_with_nothing_to_offer_still_replaces_the_goal(tmp_path):
    with make_client(tmp_path) as (client, _db, _service):
        assert _tiles(client)[KEY]["goal_cs"] is not None
        answer = _pin(client, {"kind": "runner", "runner": "Nobody Ran This"})
        assert answer["pinned"] is True and answer["goal_cs"] is None
        assert _tiles(client)[KEY]["goal_cs"] is None


def test_options_list_finite_divisions_and_runners_fastest_first(tmp_path):
    with make_client(tmp_path) as (client, _db, service):
        options = client.get("/api/scorecard/entry_goal_options",
                             params={"key": KEY}).json()
        assert options["key"] == KEY and options["clock"] == "igt"
        divisions = options["divisions"]
        times = [row["cs"] for row in divisions]
        assert times == sorted(times) and len(divisions) > 5
        assert ("Iron", "V") not in {(row["tier"], row["division"]) for row in divisions}
        ladder = _ladder(client, service)
        assert all(row["cs"] == division_goal_cs(ladder, row["tier"], row["division"])
                   for row in divisions)
        runners = [row["cs"] for row in options["runners"]]
        assert runners == sorted(runners) and len(runners) > 1
        assert len({row["runner"] for row in options["runners"]}) == len(runners)


# -- parity: one derivation for tiles and entry goals ------------------------

def test_entry_goals_agree_with_every_tile(tmp_path):
    with make_client(tmp_path) as (client, _db, _service):
        options = client.get("/api/scorecard/entry_goal_options",
                             params={"key": KEY}).json()
        assert client.put("/api/scorecard/goal", json={"kind": "multi", "sources": [
            {"kind": "division", "tier": "Bronze", "division": "I"},
            {"kind": "runner", "runner": options["runners"][0]["runner"]}]}).status_code == 200
        assert client.post("/api/import/manual", json={
            "entity_key": "star:1:1", "strat_tag": "Standard",
            "time_cs": 3000}).status_code == 200
        _pin(client, {"kind": "time", "cs": 9000}, key="star:2:0")
        tiles = _tiles(client)
        goals = _entry_goals(client, list(tiles))
        sources = client.get("/api/scorecard").json()["goal"]["sources"]
        for key, tile in tiles.items():
            goal = goals[key]
            assert (goal["goal_cs"], goal["you_cs"], goal["clock"], goal["pinned"]) == (
                tile["goal_cs"], tile["you_cs"], tile["clock"], tile["goal_pinned"]), key
            if tile["goal_source"] is not None:
                assert goal["source"]["kind"] == sources[tile["goal_source"]]["kind"], key


def test_keys_off_the_card_still_answer(tmp_path):
    with make_client(tmp_path) as (client, _db, service):
        tiles = _tiles(client)
        companions = [f"star:{course}:{hundred_coin_companion(course)}"
                      for course in range(1, 16)]
        assert not set(companions) & set(tiles)          # merged into the 100c cells
        goals = _entry_goals(client, [*companions, "segment:99999"])
        laddered = [key for key in companions if service.ranks.ladders(key)]
        assert laddered, "some companion star must carry its own ladder"
        for key in companions:
            assert goals[key]["clock"] == "igt"
            assert (goals[key]["goal_cs"] is not None) == (key in laddered), key
        assert goals["segment:99999"]["clock"] == "rta"
        assert goals["segment:99999"]["goal_cs"] is None


# -- validation and broadcast ------------------------------------------------

@pytest.mark.parametrize("body", [
    {"key": "course:1", "goal": None},
    {"key": KEY, "goal": {"kind": "multi"}},
    {"key": KEY, "goal": {"kind": "division", "tier": "Nope", "division": "I"}},
    {"key": KEY, "goal": {"kind": "division", "tier": "Iron", "division": "V"}},
    {"key": KEY, "goal": {"kind": "runner", "runner": " "}},
    {"key": KEY, "goal": {"kind": "time", "cs": 0}},
    {"key": KEY, "goal": {"kind": "time", "cs": -5}},
    {"key": KEY, "goal": {"kind": "time", "cs": "12"}},
    {"key": KEY, "goal": {"kind": "time", "cs": True}},
])
def test_bad_pins_are_422_and_write_nothing(tmp_path, body):
    with make_client(tmp_path) as (client, db, _service):
        assert client.put("/api/scorecard/entry_goal", json=body).status_code == 422
        assert db.get_state("scorecard_entry_goals", None) is None


def test_bad_keys_and_scopes_are_refused(tmp_path):
    with make_client(tmp_path) as (client, db, _service):
        assert client.get("/api/scorecard/entry_goals",
                          params={"keys": "star:1:0,bogus"}).status_code == 422
        assert client.put("/api/scorecard/entry_goal", json={
            "key": KEY, "scope": "route:99999",
            "goal": {"kind": "time", "cs": 4500}}).status_code == 404
        assert db.get_state("scorecard_entry_goals", None) is None


def test_a_pin_broadcasts_goal_changed(tmp_path):
    with make_client(tmp_path) as (client, _db, service):
        seen = []

        async def capture(event):
            seen.append(event)
            return len(seen)

        service.broadcaster.publish = capture
        _pin(client, {"kind": "time", "cs": 4500})
        _pin(client, None)
        assert [(event.type, event.payload) for event in seen] == [
            ("goal_changed", {"key": KEY}), ("goal_changed", {"key": KEY})]
