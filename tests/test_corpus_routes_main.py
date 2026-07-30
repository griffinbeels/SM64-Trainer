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

# tests/ has no __init__.py, but pytest prepends the test file's own directory
# to sys.path, so this sibling import works the way test_defaults_corpus_
# routes.py already relies on.
from test_defaults_corpus import Ev, run_engine

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))
_spec = importlib.util.spec_from_file_location(
    "build_defaults_seed", TOOLS / "build_defaults_seed.py")
build_seed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_seed)

ROUTES = build_seed.corpus_routes_main.ROUTES
BY_KEY = {r["seed_key"]: r for r in ROUTES}

# The two orphan tests below ask a CORPUS-WIDE question — "does any seeded
# route use this movement" — so they read every route, not just the 13 main
# ones the rest of this file is scoped to. Main-only is wrong in the dangerous
# direction: a movement referenced solely by a Stage RTA route would read as an
# orphan, and the tempting fix for a false orphan is to rewrite a route to
# reference it — which is exactly the regression of 2026-07-28 (see
# UNREFERENCED_MOVEMENTS_EXEMPT). Stage routes happen to reference no segments
# today, so this changes no current answer; what it changes is that the
# exemption's "nothing uses this" is now true of the whole corpus rather than
# true by luck. Deliberately NOT pinned with an "and stage routes reference
# zero segments" assertion — that would fail the day someone legitimately adds
# one, which is a shipped default's contents, not a law.
ALL_ROUTES = ROUTES + build_seed.corpus_routes_stage.ROUTES


def _segments_used_by(routes) -> set[str]:
    return {c["seed_key"] for r in routes for s in r["steps"]
            for c in s["candidates"] if c["type"] == "segment"}

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


# Movements deliberately left unreferenced by every route (seed_key -> why).
# The default posture is still "unreferenced = dropped route step" — add a
# row here only when a real-walk test has PROVEN the movement's shape is
# correct standalone, so an orphan is a documented decision rather than a
# silent gap (route regression fix, 2026-07-28: test_no_movement_is_left_
# unreferenced used to be "satisfied" by rewriting a route to use the new
# movement instead of its two-step predecessor, which quietly dropped a named
# BLJ split from the 0/1-star routes — see _LOW_STAR_TAIL in
# corpus_routes_main.py).
UNREFERENCED_MOVEMENTS_EXEMPT = {
    "seg:bowser2->bits":
        "proven correct on its own real walk "
        "(test_bowser_2_to_bits_survives_the_whole_detour), but every seeded "
        "route that reaches BitS from Bowser 2 (16-star via _16_TAIL, 0/1-star "
        "via _LOW_STAR_TAIL) uses the two-step seg:bowser2->upstairs + "
        "seg:bits-entry sequence instead, because that keeps 'Endless "
        "Staircase BLJ' as its own named, separately-timed split for the "
        "BLJ-heavy low-star categories. Kept rather than deleted — requested "
        "by name, and valid whether or not a route uses it.",
}


def test_no_movement_is_left_unreferenced():
    """Orphan guard: a movement no route uses is dead weight in every user's
    segment list — and usually means a route step was dropped."""
    used = _segments_used_by(ALL_ROUTES)
    for row in build_seed.corpus_movements.MOVEMENTS:
        if row["seed_key"] in UNREFERENCED_MOVEMENTS_EXEMPT:
            continue
        assert row["seed_key"] in used, row["seed_key"]


def test_unreferenced_movements_exemption_is_still_actually_unreferenced():
    """A stale exemption is a lie about what is orphaned — if a future route
    edit starts using this movement, the exemption must be deleted, not kept
    alongside a real reference."""
    used = _segments_used_by(ALL_ROUTES)
    known = {row["seed_key"] for row in build_seed.corpus_movements.MOVEMENTS}
    for seed_key in UNREFERENCED_MOVEMENTS_EXEMPT:
        assert seed_key in known, f"exempted movement no longer exists: {seed_key}"
        assert seed_key not in used, (
            f"{seed_key} is exempted as unreferenced but a route now uses it "
            "— drop the exemption")


def test_a_definition_that_ends_on_a_star_grab_is_in_no_route():
    """The run-ordering trap, turned from latent into a red build.

    `runs.py::RunTracker._apply` only ever considers `steps[current]`, and
    `projection.py` builds `closed` **stars-then-segments** within ONE event.
    So a segment closing on the same event as a star closes AFTER that star has
    already advanced the run past the segment's own step — and the run stalls
    permanently and silently. That is why the corpus rule is "a movement may
    start on a star grab but must NEVER end on one".

    The 15 hundred-coin exits DO end on a star grab, and that is the feature,
    not a mistake: 100 coins, then a different star to actually leave the level
    — the user asked for exactly this shape by name. They are safe purely
    because no route references them (measured: 0 of 15). This asserts that
    stays true, so the day someone routes one it fails here with the reason
    instead of stalling a run in front of a player.

    Deliberately DERIVED, not a hardcoded list: any definition ending on a star
    grab is caught, including ones nobody has written yet.
    """
    segments = build_seed.build()["segments"]
    used = _segments_used_by(ALL_ROUTES)
    offenders = sorted(
        row["seed_key"] for row in segments
        if any(t.get("type") == "star_grabbed" for t in row["end_triggers"])
        and row["seed_key"] in used)
    assert not offenders, (
        f"{offenders} end on a star grab AND are referenced by a route. "
        "projection.py closes stars before segments within one event, so the "
        "star advances the run past this step and the segment's own closure "
        "arrives too late — the run stalls silently. Either give the segment a "
        "non-star end trigger, or keep it out of routes.")


_BOWSER_REDS_STAR = {"seg:bitdw-pipe": (16, 0), "seg:reds->pipe:bitdw": (16, 0),
                     "seg:bitfs-pipe": (17, 0), "seg:reds->pipe:bitfs": (17, 0),
                     "seg:bits-pipe": (18, 0), "seg:reds->pipe:bits": (18, 0)}


def _route_collects_star(route, course, star_id) -> bool:
    return any(c["type"] == "star" and c["course"] == course
               and c["star"] == star_id
               for step in route["steps"] for c in step["candidates"])


def test_a_route_collecting_a_bowser_reds_star_never_uses_the_exclusive_pipe_entry():
    """Real regression, found live-testing the 2026-07-29 corpus reshape by
    simulating a real route: seg:bitdw-pipe/bitfs-pipe/bits-pipe carry
    match_mode="exclusive" since that reshape (corpus_legacy.py) — they
    cancel the instant a star or key is grabbed that isn't their own end
    trigger, which is exactly right for STANDALONE "pipe entry without going
    for the reds" practice. But `star(16/17/18, 0, ...)` is itself an
    earlier route STEP in every route that wants that stage's reds star
    (16/70/120-Star), and grabbing it is precisely the event that would
    cancel an exclusive seg:X-pipe armed since course entry — silently, with
    the segment never recording success. Per this file's own rule (steps
    must close in completion-event order or the run stalls PERMANENTLY),
    that stalls the run at the Bowser step forever the moment the runner
    does exactly what the route requires. Proven live with SegmentEngine
    directly (armed at course entry, star_collected disarms it with no row,
    the later warp_entered then does nothing).

    The fix (corpus_routes_main.py, BOWSER_1_REDS/2_REDS/3_REDS) swaps the
    route-step CANDIDATE to seg:reds->pipe:* wherever that course's reds
    star is ALSO a route requirement — strict, with the star as its own
    WAYPOINT, so grabbing it is the expected next step instead of a cancel.
    This test is DERIVED, not a hardcoded route list: any route that ever
    pairs a reds star with the exclusive segment fails here, including one
    nobody has written yet."""
    exclusive_keys = {"seg:bitdw-pipe", "seg:bitfs-pipe", "seg:bits-pipe"}
    used = _segments_used_by(ROUTES)
    for route in ROUTES:
        for step in route["steps"]:
            for cand in step["candidates"]:
                if cand["type"] != "segment" or cand["seed_key"] not in exclusive_keys:
                    continue
                course, star_id = _BOWSER_REDS_STAR[cand["seed_key"]]
                assert not _route_collects_star(route, course, star_id), (
                    route["seed_key"], cand["seed_key"], "also collects",
                    (course, star_id), "-- use seg:reds->pipe:* instead")
    # And the converse: reds->pipe should only appear where the swap was
    # actually warranted -- an UNUSED reds->pipe route reference would mean
    # this test is vacuously trivial for that stage.
    for reds_key in ("seg:reds->pipe:bitdw", "seg:reds->pipe:bitfs",
                     "seg:reds->pipe:bits"):
        assert reds_key in used, (
            reds_key, "expected at least one route to reference this "
            "(otherwise the exclusive-vs-strict distinction above is untested)")


def test_the_bowser_reds_route_step_actually_closes_through_the_real_matcher():
    """The structural test above cannot prove the fix WORKS, only that the
    wrong pairing is absent -- it never runs an event through SegmentEngine.
    This does: replays "enter BitDW -> grab the reds star -> enter the pipe"
    -- the exact sequence route:16-lblj requires -- through whichever
    segment that route ACTUALLY references, using the real matcher.

    Mutation-proved inline: feeding the SAME events through the segment this
    route step used to reference (seg:bitdw-pipe, exclusive) reproduces the
    stall this fix was for -- star_collected cancels it, so it never closes,
    which is the bug report this test exists to keep fixed."""
    route = BY_KEY["route:16-lblj"]
    seg_keys = [c["seed_key"] for step in route["steps"]
                for c in step["candidates"]
                if c["type"] == "segment" and c["seed_key"] in
                ("seg:bitdw-pipe", "seg:reds->pipe:bitdw")]
    assert seg_keys == ["seg:reds->pipe:bitdw"], seg_keys

    segments = {s["seed_key"]: s for s in build_seed.build()["segments"]}
    events = [
        Ev(1, "level_changed", 100, {"from": 16, "to": 17}),
        Ev(2, "star_collected", 150, {"course_id": 16, "star_id": 0, "num_stars": 0}),
        Ev(3, "warp_entered", 250, {"level": 17}),
    ]

    fixed_row = segments["seg:reds->pipe:bitdw"]
    closed = run_engine(fixed_row, events, 16, None)
    assert [a.outcome for a in closed] == ["success"], (
        "the route's actual candidate must close on this real sequence")

    old_row = segments["seg:bitdw-pipe"]      # what the route used to reference
    closed_old = run_engine(old_row, events, 16, None)
    assert closed_old == [], (
        "sanity check on the bug this fixes: the OLD (exclusive) reference "
        "must NOT close on the same sequence -- if it does, the regression "
        "this test guards no longer reproduces and the test should be "
        "revisited")


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
