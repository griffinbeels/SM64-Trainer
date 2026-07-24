"""Stage RTA routes are pure star lists for ONE course. The gates: no foreign
course leaks in, no star is listed twice, every id is in range for its course,
and the run clock starts on entering the stage rather than on F1."""
import importlib.util
import sys
from pathlib import Path

from sm64_events.memory.addresses import COURSE_BY_LEVEL, star_count
from sm64_events.tracking.routes import validate_route

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))
_spec = importlib.util.spec_from_file_location(
    "build_defaults_seed", TOOLS / "build_defaults_seed.py")
build_seed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_seed)

ROUTES = build_seed.corpus_routes_stage.ROUTES


def _candidates(route):
    return [c for s in route["steps"] for c in s["candidates"]]


def test_stage_route_count():
    """35, not the wiki's 37 documented lists: WDW's and RR's "Beginner"/
    "Expert" 120 variants differ from their standard route ONLY in which star
    carries the 100 coins — i.e. only in ORDER — so once a visit is unordered
    they are the same route. The orderings live on in the sources companion."""
    assert len(ROUTES) == 35
    assert len({r["seed_key"] for r in ROUTES}) == 35


def test_every_stage_route_is_a_single_unordered_visit():
    """A stage route is one course visit, so group_visits collapses it to one
    "collect these N stars" step. Order within a stage was never enforceable —
    what the route pins is the star SET and a clock that starts on entry."""
    for r in ROUTES:
        assert len(r["steps"]) == 1, r["seed_key"]
        step = r["steps"][0]
        # need < len only where the wiki documents an either/or — LLL's 16
        # Star route ends "Hot-Foot-It into the Volcano (or Elevator Tour)",
        # which survives the merge as "any 4 of these 5".
        assert step["need"] <= len(step["candidates"]), r["seed_key"]
        if step["need"] != len(step["candidates"]):
            assert r["seed_key"] == "route:stage-lll-16", r["seed_key"]


def test_every_stage_route_stays_inside_one_course():
    for r in ROUTES:
        courses = {c["course"] for c in _candidates(r)}
        assert len(courses) == 1, (r["seed_key"], courses)


def test_start_condition_enters_that_course():
    for r in ROUTES:
        cond = r["start_condition"]
        assert cond["type"] == "level_enter", r["seed_key"]
        course = next(iter({c["course"] for c in _candidates(r)}))
        assert COURSE_BY_LEVEL[cond["to"]] == course, r["seed_key"]


def test_no_duplicate_star_and_every_id_in_range():
    for r in ROUTES:
        ids = [c["star"] for c in _candidates(r)]
        assert len(set(ids)) == len(ids), r["seed_key"]
        course = next(iter({c["course"] for c in _candidates(r)}))
        assert all(0 <= i < star_count(course) for i in ids), r["seed_key"]


def test_every_stage_route_is_valid_and_categorised():
    for r in ROUTES:
        validate_route({"name": r["name"], "steps": r["steps"],
                        "start_condition": r["start_condition"]})
        # "Stage RTA/<course>" — one sub-group per course.
        assert r["category"].startswith("Stage RTA/"), r["seed_key"]
        assert all(s.get("label") for s in r["steps"]), r["seed_key"]


def test_no_stage_route_references_a_segment():
    for r in ROUTES:
        assert all(c["type"] == "star" for c in _candidates(r)), r["seed_key"]


def test_labels_are_derived_from_the_stage_and_the_name_table():
    """Labels are derived, never typed, so a star rename cannot leave a stale
    label behind. The merged visit reads "<stage> - N stars"; the stage comes
    from the route name so the two can never disagree."""
    from sm64_events.memory.addresses import star_name
    for r in ROUTES:
        abbrev = r["name"].split(" — ")[0]
        step = r["steps"][0]
        if len(step["candidates"]) == 1:
            # A lone star keeps its own step AND its own name — "DDD — 1
            # stars" would be both ugly and less informative.
            cand = step["candidates"][0]
            assert step["label"] == \
                f"{abbrev} — {star_name(cand['course'], cand['star'])}"
        else:
            assert step["label"] == f"{abbrev} — {step['need']} stars", \
                r["seed_key"]


def test_each_stage_route_is_filed_under_its_course():
    """"Stage RTA/<course>" — one sub-group per course, named from
    COURSE_NAMES so a course rename can never strand a group."""
    from sm64_events.memory.addresses import course_name
    for r in ROUTES:
        course = next(iter({c["course"] for c in _candidates(r)}))
        assert r["category"] == f"Stage RTA/{course_name(course)}", r["seed_key"]
    subs = {r["category"].split("/", 1)[1] for r in ROUTES}
    assert len(subs) == 15          # one per main course
