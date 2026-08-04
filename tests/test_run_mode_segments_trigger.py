"""Layer 4: does a movement, PERFORMED, actually advance a live run?

Two gates already exist and neither answers this. `test_defaults_corpus.py`
proves a real walk fires the definition, and never shows a run.
`test_defaults_corpus_routes.py` proves the step ORDER is right, but hands
`RunTracker` a `FakeAttempt` for every step — so it answers "given these
completions, does the run advance" and never "does performing the movement
produce that completion at all". Nothing joined them.

Live report 2026-08-03: *"when I got to the Bowser 1 → WF split in the run
tool, it didn't successfully trigger, despite me seeing a successful entry for
Bowser 1 → WF in the practice log."* Replaying his own journal cleared the
engine completely — run 3534 walked steps 0-4 (Lakitu Skip, LBLJ, BitDW reds,
reds → pipe, Bowser Battle 1) and sat on step 5 waiting, because every one of
the eleven Bowser 1 arena exits in that journal went to BitDW, LLL, BitS or the
grounds and **not once to Whomp's Fortress**. The movement never happened, so
nothing was there to trigger. But establishing that took a hand replay of his
database, which is exactly the position a gate exists to prevent.

So the chain here is end to end and unfaked: the corpus walker generates a real
performance of the movement, the REAL `SegmentEngine` decides whether it fires,
and the attempts it produces are fed to a REAL armed `RunTracker` parked on that
movement's own step. A movement that stops firing, or fires with a shape the run
does not recognise, fails here and NAMES ITSELF.

SCOPE, stated rather than left to be discovered: this covers the 56 castle
MOVEMENTS, which is what `movement_walk` can express — it derives a walk from
the world graph, so it handles `level_exit` / `level_enter` / `star_grabbed`
starts and raises on the rest. The pipe-entry and 100-coin families start on
`spawned` / `attempt_anchor`, i.e. on being somewhere rather than on going
somewhere, and have no walk to derive. `test_every_movement_appearing_in_a_main
_route_is_covered` is what stops that scope quietly widening.
"""
import json
from dataclasses import replace

import pytest

from sm64_events.core.paths import bundled_defaults_seed
from sm64_events.tracking.runs import RunTracker
from sm64_events.tracking.segments import MatchContext

# tests/ has no __init__.py, but pytest prepends the test file's own directory
# to sys.path, so the sibling modules import by bare name.
from test_defaults_corpus import Ev, MOVEMENTS, movement_walk, run_engine
from test_defaults_corpus_routes import MAIN_ROUTES, SEG_BY_KEY, SEG_IDS

CTX = MatchContext(level=None, prev_level=None, num_stars=0)
WALKABLE = {row["seed_key"] for row in MOVEMENTS}

# Every (seed_key, route, step index) a main route asks a movement for.
PLACEMENTS = [
    (candidate["seed_key"], route, index)
    for route in MAIN_ROUTES
    for index, step in enumerate(route["steps"])
    for candidate in step["candidates"]
    if candidate["type"] == "segment" and candidate["seed_key"] in WALKABLE]


def _armed(route):
    """A live run on `route`, started by its own start condition."""
    steps = [{"need": s["need"],
              "candidates": [c if c["type"] == "star"
                             else {"type": "segment",
                                   "segment_id": SEG_IDS[c["seed_key"]]}
                             for c in s["candidates"]]}
             for s in route["steps"]]
    tracker = RunTracker()
    tracker.feed(Ev(1, "run_started", 0, {
        "route_id": 1, "route_name": route["name"], "route_steps": steps,
        "mode": "forgiving", "start_offset_ms": 0,
        "start_condition": route["start_condition"]}), [], CTX)
    tracker.feed(Ev(2, "game_reset", 1, {}), [], CTX)   # the start condition
    assert tracker.active_run_view() is not None, route["seed_key"]
    return tracker


def _performed(seed_key):
    """The attempts the REAL matcher records for a real performance of this
    movement — nothing fabricated, and EMPTY when it does not fire.

    `segment_id` is re-stamped to the route's resolved id because `run_engine`
    builds a one-def engine numbered 1. The id mapping is reconcile's job and
    is gated by `test_route_candidates_all_resolve`; what is under test here is
    whether the movement fires, and with what outcome.
    """
    row = SEG_BY_KEY[seed_key]
    events, level, area, _nodes = movement_walk(row)
    return [replace(attempt, segment_id=SEG_IDS[seed_key])
            for attempt in run_engine(row, events, level, area)]


def _feed(tracker, attempts):
    for offset, attempt in enumerate(attempts):
        tracker.feed(Ev(100 + offset, "segment_closed", 0, {}), [attempt], CTX)


@pytest.mark.parametrize("seed_key,route,index", PLACEMENTS,
                         ids=[f"{k}@{r['seed_key']}#{i}"
                              for k, r, i in PLACEMENTS])
def test_performing_the_movement_advances_the_run_past_its_step(
        seed_key, route, index):
    attempts = _performed(seed_key)
    assert [a.outcome for a in attempts] == ["success"], (
        f"{seed_key}: performing this movement records {attempts!r} — a run "
        f"parked on {route['name']} step {index} could never leave it")
    tracker = _armed(route)
    tracker._active["current"] = index          # park the run on that split
    _feed(tracker, attempts)
    view = tracker.active_run_view()
    reached = len(route["steps"]) if view is None else view["current_step"]
    assert reached == index + 1, (
        f"{seed_key}: STALLED on {route['name']} step {index} — the movement "
        f"was performed and recorded a success, and the run did not advance")


def test_bowser_1_to_wf_advances_the_run_when_it_is_actually_performed():
    """The reported split, called out by name so a regression here reads as
    itself rather than as one of ~90 parametrised cases.

    The live report was NOT this: his eleven arena exits went everywhere but
    Whomp's Fortress, so the movement never happened. This is the check that
    says so in one line instead of a database replay."""
    assert [a.outcome for a in _performed("seg:bowser1->wf")] == ["success"]
    assert any(key == "seg:bowser1->wf" for key, _, _ in PLACEMENTS), (
        "no main route asks for Bowser 1 → WF any more — the parametrised "
        "cases above stopped covering the reported split")


def test_every_movement_appearing_in_a_main_route_is_covered():
    """The scope note in this module's docstring, enforced. A movement that
    drops out of `MOVEMENTS` — or a route step that starts naming one the
    walker cannot perform — silently shrinks this gate to nothing while every
    remaining case stays green, which is the failure mode a stated scope is
    supposed to prevent."""
    wanted = {c["seed_key"] for route in MAIN_ROUTES for step in route["steps"]
              for c in step["candidates"] if c["type"] == "segment"}
    seed = json.loads(bundled_defaults_seed().read_bytes().decode("utf-8"))
    by_key = {s["seed_key"]: s for s in seed["segments"]}
    # A CASTLE MOVEMENT in a main route must be covered. Everything else in a
    # route is a legacy trick or a pipe/100-coin family member — they start on
    # being SOMEWHERE rather than on going somewhere, so the world graph has no
    # walk to derive for them and `movement_walk` raises rather than guessing.
    missing = sorted(key for key in wanted - WALKABLE
                     if by_key[key]["category"] == "Castle Movement"
                     and by_key[key]["guards"])
    assert missing == [], (
        f"these castle movements are in a main route and NOT covered by this "
        f"gate: {missing}")
    assert len(PLACEMENTS) > 40, (
        f"only {len(PLACEMENTS)} movement placements covered — the gate has "
        f"collapsed")


def test_the_gate_can_still_fail():
    """Every assertion above passes on a run that was already past its step.
    This is the one proving an empty performance — the exact shape that stalls
    a run forever — does NOT advance it."""
    route = next(r for r in MAIN_ROUTES if "16" in r["name"])
    tracker = _armed(route)
    before = tracker.active_run_view()["current_step"]
    _feed(tracker, [])                          # performed nothing
    assert tracker.active_run_view()["current_step"] == before
