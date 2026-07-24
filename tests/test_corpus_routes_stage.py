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


def test_thirty_seven_stage_routes():
    assert len(ROUTES) == 37
    assert len({r["seed_key"] for r in ROUTES}) == 37


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
        assert r["category"] == "Stage RTA", r["seed_key"]
        assert all(s.get("label") for s in r["steps"]), r["seed_key"]


def test_no_stage_route_references_a_segment():
    for r in ROUTES:
        assert all(c["type"] == "star" for c in _candidates(r)), r["seed_key"]


def test_labels_come_from_the_name_table():
    """Labels are derived, never typed, so a star rename cannot leave a stale
    label behind in the seed."""
    from sm64_events.memory.addresses import star_name
    for r in ROUTES:
        for step in r["steps"]:
            names = [star_name(c["course"], c["star"]) for c in step["candidates"]]
            joiner = " + " if step["need"] == len(names) else " or "
            assert step["label"] == joiner.join(names), (r["seed_key"], step)
