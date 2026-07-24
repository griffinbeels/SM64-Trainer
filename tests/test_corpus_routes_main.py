"""The star-count invariant is the headline gate: a route named "70 Star" that
does not contain exactly 70 star candidates is a transcription error, and
nothing else in the system would ever notice. It independently reproduces the
community's CCM17/CCM18 names — 13 stars precede CCM, so you leave with 17 or
18 depending on the option — which is what makes it trustworthy."""
import importlib.util
import json
import sys
from pathlib import Path

from sm64_events.tracking.routes import validate_route

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))
_spec = importlib.util.spec_from_file_location(
    "build_defaults_seed", TOOLS / "build_defaults_seed.py")
build_seed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_seed)

ROUTES = build_seed.corpus_routes_main.ROUTES
BY_KEY = {r["seed_key"]: r for r in ROUTES}

EXPECTED_STARS = {
    "route:16-no-lblj-standard": 16,
    "route:16-no-lblj-beginner": 16,
    "route:16-no-lblj-wf100c": 16,
    "route:16-lblj": 16,
    "route:70-hmc-late-beginner": 70,
    "route:70-hmc-late-intermediate": 70,
    "route:70-hmc-late-advanced": 70,
    "route:70-hmc-late-expert": 70,
    "route:70-hmc-early": 70,
    "route:120-non-lblj": 120,
    "route:120-lblj": 120,
    "route:1-star": 1,
    "route:0-star": 0,
}


def _star_total(route) -> int:
    """Stars the route requires: a step contributes `need` when its candidates
    are stars (need=2 for a "+ 100 Coins" pair, need=1 for an either/or)."""
    total = 0
    for step in route["steps"]:
        if all(c["type"] == "star" for c in step["candidates"]):
            total += step["need"]
    return total


def test_all_thirteen_routes_are_present():
    assert set(BY_KEY) == set(EXPECTED_STARS)
    assert len(ROUTES) == 13


def test_each_route_collects_exactly_its_category_star_count():
    for key, expected in EXPECTED_STARS.items():
        assert _star_total(BY_KEY[key]) == expected, key


def test_no_route_collects_the_same_star_twice():
    """A duplicate star can never complete its second step: RunTracker credits
    a candidate once per step, and the grab only happens once per file."""
    for route in ROUTES:
        seen = []
        for step in route["steps"]:
            for cand in step["candidates"]:
                if cand["type"] == "star" and step["need"] == len(step["candidates"]):
                    seen.append((cand["course"], cand["star"]))
        assert len(set(seen)) == len(seen), route["seed_key"]


def test_every_route_is_structurally_valid():
    for r in ROUTES:
        # segment candidates carry seed_key, which validate_route cannot
        # resolve — swap in a placeholder id, exactly as reconcile does.
        steps = [{"need": s["need"], "label": s.get("label"),
                  "candidates": [c if c["type"] == "star"
                                 else {"type": "segment", "segment_id": 1}
                                 for c in s["candidates"]]}
                 for s in r["steps"]]
        validate_route({"name": r["name"], "steps": steps,
                        "start_condition": r["start_condition"]})


def test_every_segment_reference_resolves_to_a_seeded_segment():
    known = {s["seed_key"] for s in build_seed.build()["segments"]}
    for r in ROUTES:
        for step in r["steps"]:
            for cand in step["candidates"]:
                if cand["type"] == "segment":
                    assert cand["seed_key"] in known, (r["seed_key"], cand)


def test_every_route_is_categorised_and_starts_on_reset():
    for r in ROUTES:
        # category is a PATH now: "Main Categories/16 Star" (sub-categories,
        # user request 2026-07-24). The top level is what the library groups by.
        assert r["category"].startswith("Main Categories/"), r["seed_key"]
        assert r["start_condition"] == {"type": "reset_game"}, r["seed_key"]


def test_every_step_carries_a_label():
    """160 unlabelled steps is unreadable in the Routes tab and the run view."""
    for r in ROUTES:
        for step in r["steps"]:
            assert step.get("label"), (r["seed_key"], step)


def test_no_movement_is_left_unreferenced():
    """Orphan guard: a movement no route uses is dead weight in every user's
    segment list — and usually means a route step was dropped."""
    used = {c["seed_key"] for r in ROUTES for s in r["steps"]
            for c in s["candidates"] if c["type"] == "segment"}
    for row in build_seed.corpus_movements.MOVEMENTS:
        assert row["seed_key"] in used, row["seed_key"]


def test_movements_sharing_a_start_within_one_route_have_different_ends():
    """A route that visits a stage twice (70 Star's two BoB trips, 120 Star's
    two DDD trips) contains movements with the SAME start clause, so exiting
    that stage arms both. That is benign only because their ENDS differ: the
    twin is silently disarmed by the level change into the other destination
    and records no row (verified against the simulation harness).

    Two movements sharing BOTH a start and an end inside one route would be
    genuinely indistinguishable — same attempts, same PB, and the run would
    credit whichever the engine closed first. Keep them distinguishable.
    """
    segments = {s["seed_key"]: s for s in build_seed.build()["segments"]}
    for route in ROUTES:
        by_start = {}
        for step in route["steps"]:
            for cand in step["candidates"]:
                if cand["type"] != "segment":
                    continue
                seg = segments[cand["seed_key"]]
                if not seg["guards"]:
                    continue          # legacy defs arm route-independently
                key = json.dumps(seg["start_triggers"], sort_keys=True)
                by_start.setdefault(key, {})[cand["seed_key"]] = \
                    json.dumps(seg["end_triggers"], sort_keys=True)
        for start, ends in by_start.items():
            assert len(set(ends.values())) == len(ends), (
                route["seed_key"], "indistinguishable movements", sorted(ends))


def test_no_two_adjacent_steps_are_the_same_course_visit():
    """The grouping rule (user decision 2026-07-24): a route never dictates
    the order of stars WITHIN a stage visit, so a visit is exactly one step.
    Two adjacent all-star steps of the same course means a visit got split and
    the route is enforcing an order it has no business enforcing.

    Two visits to the SAME course stay separate because the movement between
    them breaks the run — that is why this checks adjacency, not the whole
    route."""
    for route in ROUTES:
        previous = None
        for step in route["steps"]:
            courses = {c["course"] for c in step["candidates"]
                       if c["type"] == "star"}
            current = courses.pop() if len(courses) == 1 and all(
                c["type"] == "star" for c in step["candidates"]) else None
            assert current is None or current != previous, (
                route["seed_key"], "split visit", step.get("label"))
            previous = current


def test_a_multi_star_visit_is_one_step_not_several():
    """Concretely: 16 Star LBLJ's Whomp's Fortress leg is ONE step wanting
    three stars, not three steps in a fixed order."""
    lblj = BY_KEY["route:16-lblj"]
    wf = [s for s in lblj["steps"]
          if all(c["type"] == "star" and c["course"] == 2
                 for c in s["candidates"])]
    assert len(wf) == 1
    assert wf[0]["need"] == 3 and len(wf[0]["candidates"]) == 3
    assert wf[0]["label"] == "WF — 3 stars"


def test_categories_are_two_level_paths():
    """Sub-categories are a PATH inside the one free-text category field —
    no migration, no second column, any depth. The library nests a collapsible
    group per level, so a flat value here would collapse 13 routes into one
    undifferentiated list."""
    wanted = {"route:16-no-lblj-standard": "Main Categories/16 Star",
              "route:70-hmc-early": "Main Categories/70 Star",
              "route:120-lblj": "Main Categories/120 Star",
              "route:1-star": "Main Categories/1 Star",
              "route:0-star": "Main Categories/0 Star"}
    for key, category in wanted.items():
        assert BY_KEY[key]["category"] == category, key
    tops = {r["category"].split("/")[0] for r in ROUTES}
    assert tops == {"Main Categories"}
    assert all(len(r["category"].split("/")) == 2 for r in ROUTES)
