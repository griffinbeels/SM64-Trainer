"""What the Lineup shows for each state of the ghost session, driven through node.

`ui/lineupmodel.js` is import-free apart from the tier registry, so every
state the practice card can be in is checked here without a page: the
rendered half lives in tests/test_ui_lineup.py. Sessions come from
`tools/ghost_fixture.py`, the same payloads the rendered tests serve, so the
two halves cannot drift onto different shapes of `session.rivals`.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
UI = REPO / "src" / "sm64_events" / "ui"
MODEL_JS = (UI / "lineupmodel.js").as_uri()
CAPS_JS = (UI / "components" / "caps.js").as_uri()
ENTITY = "star:2:4"

sys.path.insert(0, str(REPO / "tools"))
from ghost_fixture import (FIXTURE_TARGET, LINEUP_PLAYERS, ghost_session,  # noqa: E402
                           lineup_rivals, lineup_row)

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")


def run_node(body: str):
    script = (f"import {{ lineupModel, youSeat, LINEUP_WORDS }} "
              f"from {MODEL_JS!r};\nimport {{ RANK_FLOOR }} from {CAPS_JS!r};\n"
              f"console.log(JSON.stringify({body}));")
    result = subprocess.run(["node", "--input-type=module", "-"], input=script, capture_output=True,
                            text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def model(session, *, known=True, reachable=True, on=True, strategies=("Standard",)):
    args = {"session": session, "known": known, "reachable": reachable, "on": on,
            "entityKey": ENTITY, "strategies": list(strategies)}
    return run_node(f"lineupModel({json.dumps(args)})")


def lineup_session(**rivals):
    return {**ghost_session(enabled=True), "rivals": lineup_rivals(**rivals)}


WORDS = run_node("LINEUP_WORDS") if shutil.which("node") else {}
FLOOR = run_node("RANK_FLOOR") if shutil.which("node") else {}


def test_no_strategy_offers_the_strategies_themselves():
    """Nobody to race without a strategy, so the row offers the strategies."""
    session = {**lineup_session(state="no_strategy"), "target": {**FIXTURE_TARGET, "strat": None}}
    shown = model(session, strategies=("Standard", "Sideflip"))
    assert shown["people"] == []
    assert shown["lineup"] == f"{ENTITY}|none"
    assert shown["note"]["text"] == WORDS["noStrategy"]
    assert shown["note"]["choices"] == [{"label": "Standard", "strategy": "Standard"},
                                        {"label": "Sideflip", "strategy": "Sideflip"}]


def test_every_strategy_is_offered_in_the_order_the_server_sends():
    """Round 10: "we have the realestate to show more of the strategies" --
    every one is a button, none folds behind "Other…", and the order is the
    list's own: the server sends it in the rank standards table's order."""
    session = {**lineup_session(state="no_strategy"), "target": {**FIXTURE_TARGET, "strat": None}}
    names = ("Owl", "Mad Owl", "Standard", "TJ Owlless", "DJ Owlless", "Sideflip", "No Reds", "BLJ")
    choices = model(session, strategies=names)["note"]["choices"]
    assert choices == [{"label": name, "strategy": name} for name in names]


def test_the_strategy_buttons_stand_open_with_ghosts_off():
    """Round 11: "That strategy selector should be visible REGARDLESS of ghosts
    being on or off." The buttons pick the card's strategy either way; off,
    the words drop "to race ghosts"."""
    session = {**lineup_session(state="no_strategy"), "target": {**FIXTURE_TARGET, "strat": None}}
    off = model(session, on=False, strategies=("Standard", "Sideflip"))
    assert off["on"] is True and off["note"]["text"] == WORDS["noStrategyOff"]
    assert [c["label"] for c in off["note"]["choices"]] == ["Standard", "Sideflip"]
    assert model(session, on=True, strategies=("Standard",))["note"]["text"] == WORDS["noStrategy"]


def test_no_strategies_at_all_asks_for_one_to_be_created():
    session = {**lineup_session(state="no_strategy"), "target": {**FIXTURE_TARGET, "strat": None}}
    shown = model(session, strategies=())
    assert shown["note"] == {"text": WORDS["noStrategies"]}


@pytest.mark.parametrize("count", [1, 2, 3, 4])
def test_loading_holds_one_place_per_rival_it_will_seat(count):
    """The card never jumps: your seat plus `options.count` quiet places."""
    shown = model(lineup_session(state="loading", count=count))
    people = shown["people"]
    assert shown["loading"] is True
    assert people[0]["you"] is True and people[0]["frames"] == 343
    holes = people[1:]
    assert len(holes) == count
    assert all(hole["placeholder"] for hole in holes)
    assert [hole["key"] for hole in holes] == [f"s{slot}" for slot in range(count)]
    assert shown["lineup"] == f"{ENTITY}|{FIXTURE_TARGET['strat']}|overall"


def test_loading_and_ready_share_a_lineup_so_your_seat_stays_put():
    """Loading resolving is the same lineup: the core slides people in rather
    than exchanging the whole row."""
    assert model(lineup_session(state="loading"))["lineup"] == model(lineup_session())["lineup"]


def test_ready_seats_you_then_the_rivals_with_their_marks():
    rows = [lineup_row(0, "Alexander_Speedruns", "Diamond", "V", 339, pinned=True),
            lineup_row(1, "Quinn", "Diamond", "IV", 335, pending=True),
            lineup_row(2, "Kai", "Diamond", "III", 334)]
    rows[2]["verified"] = True
    rows[1]["strategy"] = "Cannonless"
    shown = model(lineup_session(rows=rows))
    you, *seats = shown["people"]
    assert you["you"] and you["ghost"] is True and you["tier"] == "Platinum"
    assert [s["name"] for s in seats] == ["Alexander_Speedruns", "Quinn", "Kai"]
    assert [s["key"] for s in seats] == ["s0", "s1", "s2"]
    assert [(s["pinned"], s["pending"], s["verified"]) for s in seats] == [
        (True, False, False), (False, True, False), (False, False, True)]
    assert [s["hue"] for s in seats] == [row["hue_degrees"] for row in rows]
    # Every lineup races all strategies, so each seat names its own.
    assert [p["strategy"] for p in shown["people"]] == [FIXTURE_TARGET["strat"], FIXTURE_TARGET["strat"],
                                                        "Cannonless", FIXTURE_TARGET["strat"]]
    assert "note" not in shown


def test_a_row_without_a_slot_is_not_seated():
    """Only a slotted row is a seat; anything else would draw a seat with no
    place to swap into."""
    rows = [lineup_row(0, *LINEUP_PLAYERS[0]), {**lineup_row(1, *LINEUP_PLAYERS[1]), "slot": None}]
    assert [p["key"] for p in model(lineup_session(rows=rows))["people"]] == ["you", "s0"]


@pytest.mark.parametrize("reason,words", [("nobody", "nobody"), ("offline", "offline"),
                                          ("signed_out", "signedOut"), ("a_new_reason", "nobody")])
def test_an_empty_lineup_seats_you_and_says_why(reason, words):
    shown = model(lineup_session(rows=[], reason=reason))
    assert [p["key"] for p in shown["people"]] == ["you"]
    assert shown["note"] == {"text": WORDS[words]}


def test_an_empty_lineup_nobody_has_asked_about_yet_draws_as_loading():
    """No seats and no reason: the overlay was off, so the list is about to
    load. Saying "nobody" there would be a claim nothing has checked."""
    shown = model(lineup_session(rows=[], reason=None, count=3))
    assert shown["loading"] is True and "note" not in shown
    assert [p.get("placeholder", False) for p in shown["people"]] == [False, True, True, True]


def test_a_pinned_run_of_your_own_says_so_by_identity_not_by_name():
    rows = [{**lineup_row(0, "Quinn", "Diamond", "IV", 335), "own": True},
            {**lineup_row(1, "You", "Diamond", "III", 334), "own": False}]
    assert [p["name"] for p in model(lineup_session(rows=rows))["people"][1:]] == ["Your run", "You"]


def test_an_unknown_rom_says_what_to_load_and_seats_nobody():
    session = lineup_session()
    session["source"] = {**session["source"], "available": False, "reason": "unknown_rom"}
    shown = model(session)
    assert shown["people"] == [] and shown["note"] == {"text": WORDS["unknownRom"]}
    assert shown["lineup"] == f"{ENTITY}|rom"


def test_unreachable_offers_retry_even_before_any_session():
    shown = model(None, known=False, reachable=False, on=False)
    assert shown["on"] is True, "an unreachable session must not collapse the row that says so"
    assert shown["note"] == {"text": WORDS["unreachable"], "action": "Retry", "kind": "retry"}
    assert shown["people"] == []


def test_nothing_is_drawn_until_the_session_answers_for_this_card():
    assert model(lineup_session(), known=False) is None
    assert model(None) is None
    assert model(ghost_session(enabled=True)) is None, "a session without rivals is the older controls"


def test_no_pb_yet_seats_you_at_the_floor_with_no_ghost():
    """Capless 5 with no time until your first finish, and nothing of yours races."""
    you = model(lineup_session(pb_frames=None))["people"][0]
    assert (you["tier"], you["division"]) == (FLOOR["tier"], FLOOR["division"])
    assert you["frames"] is None and you["ghost"] is False


def test_not_racing_your_pb_keeps_your_seat_without_a_ghost():
    you = model(lineup_session(include_pb=False))["people"][0]
    assert you["frames"] == 343 and you["ghost"] is False
    unavailable = lineup_session()
    unavailable["rivals"]["pb"]["available"] = False
    assert model(unavailable)["people"][0]["ghost"] is False


@pytest.mark.parametrize("state", [{}, {"state": "loading"}, {"rows": [], "reason": "nobody"}])
def test_a_pb_without_poses_seats_your_best_recorded_run_in_its_colour(state):
    """His decision, 2026-10-25: your best recorded run races in the PB's seat;
    the card labels it "Best recorded run" and keeps the PB as the rank."""
    people = model(lineup_session(best_run_frames=352, **state))["people"]
    you, best = people[:2]
    assert (you["you"], you["frames"], you["tier"], you["division"], you["ghost"]) == (
        True, 343, "Platinum", "II", False)
    assert (best["key"], best["best"], best["name"], best["frames"], best["ghost"]) == (
        "best", True, WORDS["bestRun"], 352, True)
    assert best["hue"] == you["hue"] and (best["tier"], best["division"]) == ("Platinum", "IV")
    assert sum(bool(person.get("best")) for person in people) == 1


def test_no_best_run_seat_while_your_pb_races_or_is_unticked():
    assert not any(person.get("best") for person in model(lineup_session())["people"])
    unticked = lineup_session(best_run_frames=352)
    unticked["rivals"]["options"]["include_pb"] = False
    assert not any(person.get("best") for person in model(unticked)["people"])
    failed = lineup_session(best_run_frames=352)
    failed["rivals"]["best_run"]["available"] = False
    assert model(failed)["people"][1]["ghost"] is False  # a dashed ring, never a false race


def test_the_overlay_switch_rides_on_the_model():
    assert model(lineup_session(), on=False)["on"] is False
    assert model(lineup_session(), on=True)["on"] is True


def test_scope_is_part_of_the_lineup_identity():
    """Strategy and Overall are different lineups: switching exchanges the row."""
    assert model(lineup_session(scope="strategy"))["lineup"] != model(lineup_session())["lineup"]
