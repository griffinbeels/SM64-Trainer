"""The star-count invariant is the headline gate: a route named "70 Star" that
does not contain exactly 70 star candidates is a transcription error, and
nothing else in the system would ever notice. It independently reproduces the
community's CCM17/CCM18 names — 13 stars precede CCM, so you leave with 17 or
18 depending on the option — which is what makes it trustworthy."""
import importlib.util
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
        assert r["category"] == "Main Categories", r["seed_key"]
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
