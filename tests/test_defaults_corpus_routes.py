"""Layer 3: replay each main-category route end to end through RunTracker.

RunTracker only ever considers steps[current], so a step listed out of
completion-event order stalls a run PERMANENTLY — and nothing in validation,
the seed, or the UI would ever say so. This is the only gate that catches it,
and it is the reason ~700 blind-authored steps can be trusted."""
import json

from sm64_events.core.paths import bundled_defaults_seed
from sm64_events.tracking.runs import RunTracker
from sm64_events.tracking.segments import MatchContext

# tests/ has no __init__.py, but pytest prepends the test file's own directory
# to sys.path, so the sibling module imports by bare name.
from test_defaults_corpus import Ev

SEED = json.loads(bundled_defaults_seed().read_bytes().decode("utf-8"))
SEG_BY_KEY = {s["seed_key"]: s for s in SEED["segments"]}
SEG_IDS = {key: index + 1 for index, key in enumerate(SEG_BY_KEY)}
MAIN_ROUTES = [r for r in SEED["routes"] if r["category"] == "Main Categories"]
CTX = MatchContext(level=None, prev_level=None, num_stars=0)


class FakeAttempt:
    """Only the fields RunTracker._apply reads."""

    def __init__(self, course_id=None, star_id=None, segment_id=None):
        self.outcome, self.cleared = "success", False
        self.course_id = course_id
        self.star_id = star_id
        self.segment_id = segment_id


def _resolved(route):
    """Steps as reconcile persists them: seed_key -> a local segment_id."""
    return [{"need": s["need"],
             "candidates": [c if c["type"] == "star"
                            else {"type": "segment",
                                  "segment_id": SEG_IDS[c["seed_key"]]}
                            for c in s["candidates"]]}
            for s in route["steps"]]


def _step_attempts(step):
    """The successful attempts completing this step, in the order a player
    would produce them. `need` of the candidates is enough — an either/or step
    is satisfied by its first option."""
    out = []
    for cand in step["candidates"][:step["need"]]:
        if cand["type"] == "star":
            out.append(FakeAttempt(course_id=cand["course"],
                                   star_id=cand["star"]))
        else:
            out.append(FakeAttempt(segment_id=SEG_IDS[cand["seed_key"]]))
    return out


def _armed_tracker(route):
    tracker = RunTracker()
    tracker.feed(Ev(1, "run_started", 0, {
        "route_id": 1, "route_name": route["name"],
        "route_steps": _resolved(route), "mode": "forgiving",
        "start_offset_ms": 0, "start_condition": route["start_condition"]}),
        [], CTX)
    tracker.feed(Ev(2, "game_reset", 1, {}), [], CTX)   # the start condition
    assert tracker.active_run_view() is not None, route["seed_key"]
    return tracker


def test_every_main_route_finishes_when_played_in_its_own_order():
    for route in MAIN_ROUTES:
        tracker = _armed_tracker(route)
        finished, frame = [], 1
        for index, step in enumerate(route["steps"]):
            for attempt in _step_attempts(step):
                frame += 1
                finished += tracker.feed(
                    Ev(100 + frame, "star_collected", frame, {}),
                    [attempt], CTX)
            view = tracker.active_run_view()
            assert view is None or view["current_step"] > index, (
                route["seed_key"], "stalled at step", index,
                step.get("label"))
        assert len(finished) == 1, (route["seed_key"], len(finished))
        assert finished[0].status == "finished", route["seed_key"]
        assert finished[0].reached_step == len(route["steps"]), route["seed_key"]


def test_a_misordered_route_stalls_when_played_correctly():
    """Negative control — without it, a green run above would say nothing
    about ordering. The player performs the TRUE order; the route lists two
    steps the wrong way round. RunTracker discards the attempt that does not
    match steps[current], so the run must strand short of the end."""
    route = next(r for r in MAIN_ROUTES if r["seed_key"] == "route:1-star")
    misordered = dict(route)
    steps = list(route["steps"])
    steps[3], steps[4] = steps[4], steps[3]
    misordered["steps"] = steps

    tracker = _armed_tracker(misordered)
    finished = []
    for step in route["steps"]:          # the TRUE play order
        for attempt in _step_attempts(step):
            finished += tracker.feed(Ev(9, "star_collected", 9, {}),
                                     [attempt], CTX)
    assert finished == [], "a misordered route must never finish"
    assert tracker.active_run_view()["current_step"] < len(steps)


def test_no_route_step_depends_on_a_movement_that_ends_on_a_star_grab():
    """The §5.2 trap, checked statically as well as by replay: a movement
    ending on a star grab would consume that star attempt's turn, because
    `closed` is ordered stars-then-segments within one event."""
    for route in MAIN_ROUTES:
        for step in route["steps"]:
            for cand in step["candidates"]:
                if cand["type"] != "segment":
                    continue
                end = SEG_BY_KEY[cand["seed_key"]]["end_triggers"][0]
                assert end["type"] != "star_grabbed", (route["seed_key"], cand)
