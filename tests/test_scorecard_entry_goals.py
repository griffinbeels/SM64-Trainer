"""Per-entry goals: pins set from a Practice card, the per-entry automatic
floor, repeat/streak progress, and `/entry_goals` agreeing with the
Scorecard tile it mirrors."""
import time
from datetime import datetime, timedelta, timezone

import pytest

from import_fixture import make_client
from sm64_events.core.timefmt import frame_at_or_after
from sm64_events.ranks.scorecard import (
    division_goal_cs, entry_automatic_goal, hundred_coin_companion)
from sm64_events.ranks.scoring import progress_for_time, progression_key
from sm64_events.tracking.goal_progress import EARLY_RESET_CS, goal_progress
from sm64_events.tracking.projection import Attempt

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
        stored = db.get_state("scorecard_entry_goals", None)[KEY]
        assert {k: v for k, v in stored.items() if k != "set_at"} == {
            "kind": "division", "tier": "Gold", "division": "I", "count": 1, "streak": False}
        assert stored["set_at"].endswith("Z")

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


# -- repeat goals: count, streak, forgive ------------------------------------

GOAL_CS = 4500
EPOCH = datetime(2026, 9, 23, tzinfo=timezone.utc)


def _attempt(attempt_id, outcome, cs, *, at=EPOCH, session=1, cleared=False, key=KEY):
    _kind, course, star = key.split(":")
    stamp = (at + timedelta(milliseconds=attempt_id)).isoformat().replace("+00:00", "Z")
    return Attempt(id=attempt_id, session_id=session, course_id=int(course),
                   star_id=int(star), strat_tag=None, anchor_type="practice_reset",
                   anchor_frame=0, outcome=outcome, outcome_detail=None,
                   igt_frames=frame_at_or_after(cs) if cs is not None else None,
                   rta_frames=None, started_utc=stamp, ended_utc=stamp,
                   cleared=cleared, cleared_reason="manual" if cleared else None)


# under, an early reset, under, a death, under
RUN = [("success", 4000), ("reset", EARLY_RESET_CS - 100), ("success", 4200),
       ("death", 2000), ("success", 4400)]


def _run(attempts, **kwargs):
    options = {"clock": "igt", "goal_cs": GOAL_CS, "count": 3, "streak": True,
               "since": EPOCH, **kwargs}
    return goal_progress(attempts, **options)


def _attempts(run=RUN, start=1):
    return [_attempt(start + index, outcome, cs) for index, (outcome, cs) in enumerate(run)]


def test_total_counts_every_qualifying_finish():
    progress = _run(_attempts(), streak=False)
    assert (progress["done"], progress["best"], progress["broken_by"]) == (3, 3, None)


def test_streak_counts_the_run_since_the_last_real_miss():
    progress = _run(_attempts())
    # The early reset (id 2) is ignored; the death (id 4) breaks the run.
    assert (progress["done"], progress["best"], progress["broken_by"]) == (1, 2, 4)


def test_a_later_reset_and_a_slower_finish_break_the_streak():
    run = [("success", 4000), ("reset", EARLY_RESET_CS + 100), ("success", 4100),
           ("success", GOAL_CS + 10), ("success", 4300)]
    progress = _run(_attempts(run))
    assert (progress["done"], progress["best"], progress["broken_by"]) == (1, 1, 4)


def test_cleared_abandoned_forgiven_and_earlier_attempts_are_ignored():
    attempts = _attempts()
    attempts.append(_attempt(9, "death", 1000, cleared=True))
    attempts.append(_attempt(10, "abandoned", 9000))
    assert _run(attempts, forgiven={4})["done"] == 3
    before = [_attempt(1, "death", 1000, at=EPOCH - timedelta(hours=1))]
    assert _run(before + _attempts(start=2))["broken_by"] == 5


def _pin_with_attempts(client, db, service, *, count=3, streak=True):
    """Pin a repeat goal, then record RUN in the live session after it."""
    answer = _pin(client, {"kind": "time", "cs": GOAL_CS, "count": count, "streak": streak})
    set_at = datetime.fromisoformat(
        db.get_state("scorecard_entry_goals", None)[KEY]["set_at"].replace("Z", "+00:00"))
    for index, (outcome, cs) in enumerate(RUN):
        db.upsert_attempt(_attempt(900 + index, outcome, cs, session=service.session_id,
                                   at=set_at - timedelta(milliseconds=899)))
    return answer


def test_a_repeat_pin_reports_progress_and_forgiving_restores_the_run(tmp_path):
    with make_client(tmp_path) as (client, db, service):
        answer = _pin_with_attempts(client, db, service)
        assert (answer["count"], answer["streak"]) == (3, True)
        assert answer["progress"]["done"] == 0            # set before any attempt
        goal = _entry_goals(client, [KEY])[KEY]
        assert goal["progress"]["done"] == 1 and goal["progress"]["best"] == 2
        assert goal["progress"]["broken_by"] == 903 and goal["achieved"] is False

        forgiven = client.post("/api/scorecard/entry_goal/forgive",
                               json={"key": KEY, "attempt_id": 903}).json()
        assert forgiven["progress"]["done"] == 3 and forgiven["achieved"] is True
        assert forgiven["progress"]["broken_by"] is None
        assert db.get_state("scorecard_goal_forgiven", None) == {KEY: [903]}

        undone = client.post("/api/scorecard/entry_goal/forgive",
                             json={"key": KEY, "attempt_id": 903, "undo": True}).json()
        assert undone["progress"]["done"] == 1
        assert db.get_state("scorecard_goal_forgiven", None) == {}


def test_total_mode_and_an_unpinned_entry(tmp_path):
    with make_client(tmp_path) as (client, db, service):
        _pin_with_attempts(client, db, service, streak=False)
        goals = _entry_goals(client, [KEY, "star:1:1"])
        assert goals[KEY]["progress"]["done"] == 3 and goals[KEY]["achieved"] is True
        assert (goals["star:1:1"]["count"], goals["star:1:1"]["streak"],
                goals["star:1:1"]["progress"]) == (1, False, None)


def test_the_window_restarts_on_a_new_session_and_on_a_changed_pin(tmp_path):
    with make_client(tmp_path) as (client, db, service):
        _pin_with_attempts(client, db, service, streak=False)
        set_at = db.get_state("scorecard_entry_goals", None)[KEY]["set_at"]
        # Re-saving the SAME goal keeps its window.
        time.sleep(0.01)
        assert _pin(client, {"kind": "time", "cs": GOAL_CS, "count": 3})["progress"]["done"] == 3
        assert db.get_state("scorecard_entry_goals", None)[KEY]["set_at"] == set_at
        # A changed repeat re-arms it.
        assert _pin(client, {"kind": "time", "cs": GOAL_CS, "count": 4})["progress"]["done"] == 0
        _pin(client, {"kind": "time", "cs": GOAL_CS, "count": 3})
        later = datetime.now(timezone.utc) + timedelta(seconds=10)
        for index, (outcome, cs) in enumerate(RUN):
            db.upsert_attempt(_attempt(950 + index, outcome, cs,
                                       session=service.session_id, at=later))
        assert _entry_goals(client, [KEY])[KEY]["progress"]["done"] == 3
        # A new sitting starts from zero while the pin persists.
        service.session_id = db.insert_session(
            (later + timedelta(hours=1)).isoformat().replace("+00:00", "Z"))
        goal = _entry_goals(client, [KEY])[KEY]
        assert goal["pinned"] is True and goal["progress"]["done"] == 0


@pytest.mark.parametrize("repeat", [
    {"count": 0}, {"count": 1000}, {"count": "3"}, {"count": True}, {"count": 2.5},
    {"streak": "yes"}, {"streak": 1},
])
def test_bad_repeats_are_422(tmp_path, repeat):
    with make_client(tmp_path) as (client, db, _service):
        response = client.put("/api/scorecard/entry_goal", json={
            "key": KEY, "goal": {"kind": "time", "cs": GOAL_CS, **repeat}})
        assert response.status_code == 422
        assert db.get_state("scorecard_entry_goals", None) is None


def test_forgive_validates_and_broadcasts(tmp_path):
    with make_client(tmp_path) as (client, _db, service):
        seen = []

        async def capture(event):
            seen.append(event)
            return len(seen)

        service.broadcaster.publish = capture
        assert client.post("/api/scorecard/entry_goal/forgive",
                           json={"key": "bogus", "attempt_id": 1}).status_code == 422
        assert client.post("/api/scorecard/entry_goal/forgive",
                           json={"key": KEY, "attempt_id": True}).status_code == 422
        assert client.post("/api/scorecard/entry_goal/forgive",
                           json={"key": KEY, "attempt_id": 7}).status_code == 200
        assert [(event.type, event.payload) for event in seen] == [("goal_changed", {"key": KEY})]
