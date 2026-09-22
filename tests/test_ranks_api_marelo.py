# tests/test_ranks_api_marelo.py
"""REST surface for MARELO (spec 2026-07-24-marelo-rank-system, Task 8).

Reuses test_ranks_api.py's make_client (a real seeded RankStandards store),
so `entities` may be non-empty from the start -- the seeded star:9:2 ladder.
"""
import asyncio
from datetime import datetime, timezone
from urllib.parse import quote

import pytest

from sm64_events.core.events import Event
from sm64_events.library.build import SCHEMA_VERSION
from sm64_events.ranks import classify, scoring
from test_ranks_api import make_client as _make_client

# Every test builds its own client over its own tmp db, so the cases are
# independent; as one worker group this file was the merge check's long
# pole (344 s serial, 2026-09-21). `spread` lets each case take any free worker.
pytestmark = pytest.mark.spread


def make_client(tmp_path, bundled_library=True):
    """The bundled library ON by default here: the leaderboard tests adopt
    rows off it and rate the sheet's runners. A test that reasons about
    exactly the ladders it writes passes `bundled_library=False`."""
    return _make_client(tmp_path, bundled_library=bundled_library)


@pytest.fixture
def client(tmp_path):
    test_client, _service = make_client(tmp_path)
    with test_client:
        yield test_client


def test_scopes_lists_overall_first(client):
    body = client.get("/api/marelo/scopes").json()
    assert body["scopes"][0]["id"] == "overall"
    assert body["active"] in {s["id"] for s in body["scopes"]}


def test_marelo_defaults_to_overall(client):
    body = client.get("/api/marelo").json()
    assert body["scope_id"] == "overall"
    assert body["n"] >= 0
    assert set(body) >= {"marelo", "mastery", "coverage", "tier", "division",
                         "entities", "celebration"}


def test_unknown_scope_is_404(client):
    assert client.get("/api/marelo?scope=route:999999").status_code == 404
    assert client.get("/api/marelo?scope=garbage").status_code == 404


def test_entities_carry_a_display_label_and_exclusion_state(client):
    body = client.get("/api/marelo").json()
    if body["entities"]:
        entity = body["entities"][0]
        assert set(entity) >= {"key", "label", "score", "gain", "excluded",
                               "next_tier", "next_division"}
        assert isinstance(entity["label"], str) and entity["label"]


def test_exclusion_removes_an_entity_from_the_denominator(client):
    """Excluding drops the entity from the SCORED set (n/marelo/mastery/
    coverage), but the breakdown list keeps its row -- as an inert
    `excluded: true`, `score: null` entry -- so the choice is reversible from
    the UI. (This assertion was `key not in entities` before fix round 1;
    that made exclusion a one-way door with no "Include" button to click,
    since the excluded row could never be found again to flip back.)"""
    before = client.get("/api/marelo").json()
    if not before["entities"]:
        return
    key = before["entities"][0]["key"]
    assert client.post("/api/marelo/exclude",
                       json={"entity": key, "excluded": True}).status_code == 200
    after = client.get("/api/marelo").json()
    assert after["n"] == before["n"] - 1
    excluded_row = next(e for e in after["entities"] if e["key"] == key)
    assert excluded_row["excluded"] is True
    assert excluded_row["score"] is None
    client.post("/api/marelo/exclude", json={"entity": key, "excluded": False})


def test_exclusion_does_not_change_the_scope_arithmetic(client):
    """The appended excluded row must be inert: marelo comes only from the
    NON-excluded slots aggregate() actually scored, never from the dead row
    tacked on for display. Uses two entities so the remaining, non-excluded
    one still has a real slot to verify against."""
    client.put("/api/ranks/standards/star:8:2/Fast/Mario", json={"seconds": 12.5})
    before = client.get("/api/marelo").json()
    assert {"star:8:2", "star:9:2"} <= {e["key"] for e in before["entities"]}
    client.post("/api/marelo/exclude", json={"entity": "star:9:2", "excluded": True})
    after = client.get("/api/marelo").json()
    non_excluded = [e for e in after["entities"] if not e["excluded"]]
    assert after["n"] == len(non_excluded) == before["n"] - 1
    expected_marelo = sum(e["score"] or 0.0 for e in non_excluded) / after["n"]
    assert after["marelo"] == pytest.approx(expected_marelo)
    excluded_row = next(e for e in after["entities"] if e["key"] == "star:9:2")
    assert excluded_row["excluded"] is True and excluded_row["score"] is None
    client.post("/api/marelo/exclude", json={"entity": "star:9:2", "excluded": False})


def test_reincluding_restores_the_denominator_and_the_normal_row(client):
    before = client.get("/api/marelo").json()
    if not before["entities"]:
        return
    key = before["entities"][0]["key"]
    client.post("/api/marelo/exclude", json={"entity": key, "excluded": True})
    client.post("/api/marelo/exclude", json={"entity": key, "excluded": False})
    after = client.get("/api/marelo").json()
    assert after["n"] == before["n"]
    matching = [e for e in after["entities"] if e["key"] == key]
    assert len(matching) == 1          # no leftover dead row alongside the real one
    assert matching[0]["excluded"] is False


def test_exclusions_endpoint_reports_the_raw_set(client):
    """The strategy modal's "include in ranking" tick reads this: it opens on
    ONE entity, possibly one with no standards yet and therefore in no scope
    at all, so it can't get the answer from /api/marelo's per-entity
    `excluded` flag (spec 2026-07-25 round 7)."""
    # The EFFECTIVE set, defaults included (every seeded movement/trick
    # segment, round 1's fifth read) -- the tick reads what ranking uses.
    baseline = client.get("/api/marelo/exclusions").json()["excluded"]
    assert "star:9:2" not in baseline
    client.post("/api/marelo/exclude", json={"entity": "star:9:2", "excluded": True})
    assert client.get("/api/marelo/exclusions").json()["excluded"] == sorted(baseline + ["star:9:2"])
    client.post("/api/marelo/exclude", json={"entity": "star:9:2", "excluded": False})
    assert client.get("/api/marelo/exclusions").json()["excluded"] == baseline


def test_history_returns_points_for_a_valid_scope(client):
    body = client.get("/api/marelo/history?scope=overall").json()
    assert body["scope_id"] == "overall"
    assert isinstance(body["points"], list)


def test_history_of_an_unknown_scope_is_404(client):
    assert client.get("/api/marelo/history?scope=route:999999").status_code == 404


def test_ack_is_accepted(client):
    assert client.post("/api/marelo/ack",
                       json={"scope": "overall", "key": 3}).status_code == 200


def _ev(type_, frame, payload=None):
    return Event(type=type_, frame=frame,
                 timestamp_utc=datetime(2026, 7, 25, tzinfo=timezone.utc),
                 payload=payload or {})


# -- /api/marelo/summary (op.gg-style always-visible chip row, Task A) -------

def test_summary_lists_overall_first_with_the_chip_shape(tmp_path):
    client, svc = make_client(tmp_path)
    with client:
        body = client.get("/api/marelo/summary").json()
        assert body["chips"][0]["scope_id"] == "overall"
        assert set(body["chips"][0]) >= {"scope_id", "label", "tier",
                                         "division", "marelo", "n", "practiced"}


def test_summary_never_errors_on_an_empty_route_list(tmp_path):
    """Contract: an empty route list yields {"chips": [ {overall...} ]},
    never an error -- this store has no routes at all."""
    client, svc = make_client(tmp_path)
    with client:
        body = client.get("/api/marelo/summary").json()
        assert body == {"chips": [body["chips"][0]]}
        assert body["chips"][0]["scope_id"] == "overall"


def test_summary_includes_main_category_routes_but_not_others(tmp_path):
    client, svc = make_client(tmp_path)
    with client:
        svc.db.insert_route("16 Star", [], "2026-01-01T00:00:00Z",
                            category="Main Categories/16 Star")
        svc.db.insert_route("Side Quest", [], "2026-01-01T00:00:00Z",
                            category="Custom/Whatever")
        body = client.get("/api/marelo/summary").json()
        labels = [chip["label"] for chip in body["chips"]]
        assert labels[0] == "Overall"
        assert "16 Star" in labels
        assert "Side Quest" not in labels


def test_summary_caps_at_six_chips(tmp_path):
    client, svc = make_client(tmp_path)
    with client:
        for index in range(8):
            svc.db.insert_route(f"Route {index}", [], "2026-01-01T00:00:00Z",
                                category="Main Categories/Route")
        body = client.get("/api/marelo/summary").json()
        assert len(body["chips"]) == 6
        assert body["chips"][0]["scope_id"] == "overall"


def test_summary_appends_the_active_scope_when_not_already_present(tmp_path):
    client, svc = make_client(tmp_path)
    with client:
        other_id = svc.db.insert_route("Odd Ball", [], "2026-01-01T00:00:00Z",
                                       category="Custom/Whatever")
        asyncio.run(svc.select_route(other_id))
        body = client.get("/api/marelo/summary").json()
        assert body["chips"][-1]["scope_id"] == f"route:{other_id}"


def test_summary_reuses_build_marelos_scoring_path(tmp_path):
    """The contract's central guarantee: a chip's tier/division/marelo must
    be the SAME numbers /api/marelo computes for that scope -- not a second
    tier lookup that could quietly disagree with it."""
    client, svc = make_client(tmp_path)
    with client:
        full = client.get("/api/marelo?scope=overall").json()
        chip = next(c for c in client.get("/api/marelo/summary").json()["chips"]
                   if c["scope_id"] == "overall")
        assert chip["tier"] == full["tier"]
        assert chip["division"] == full["division"]
        assert chip["marelo"] == full["marelo"]
        assert chip["n"] == full["n"]
        assert chip["practiced"] == full["practiced"]


def test_summary_leaves_marelo_watermarks_untouched(tmp_path):
    """The one thing that will bite you (Task A brief): _build_marelo syncs,
    seeds, and reads a watermark as a side effect of scoring a scope --
    that drives the rank-up celebration overlay. The seeded star:9:2 ladder
    is unpracticed, so `overall` scores 0.0 and tiers as "Iron" (truthy),
    which is enough to make a naive implementation that loops _build_marelo
    seed a watermark for it. A summary fetch must leave marelo_watermarks
    byte-identical -- seeding here would silently swallow that scope's real
    first rank-up later."""
    client, svc = make_client(tmp_path)
    with client:
        before = svc.marelo_watermarks()
        assert before == {}
        client.get("/api/marelo/summary")
        after = svc.marelo_watermarks()
        assert after == before == {}


# -- the ack endpoint after per-entity celebrations were removed -------------
#
# Task 0012 (2026-07-26) deleted entity-level rank-up celebrations entirely:
# a star's or segment's own rank-up is performed live by the rank banner
# climbing (ui/rankclimb.js) instead of being held server-side until a client
# renders and acks it. What is asserted here is that the removal is COMPLETE
# and LOUD -- no vestigial payload key, and an out-of-date client's entity ack
# rejected rather than silently accepted.

def _mario_reset_and_collect(svc, frame_reset, frame_collect, igt_frames):
    asyncio.run(svc.publish(_ev("practice_reset", frame_reset,
                                {"igt_frames_before": 0})))
    asyncio.run(svc.publish(_ev("star_collected", frame_collect,
                                {"course_id": 9, "star_id": 2,
                                 "igt_frames": igt_frames})))


def test_the_payload_no_longer_carries_entity_celebrations(client):
    body = client.get("/api/marelo").json()
    assert "entity_celebrations" not in body
    # The SCOPE celebration is a different feature and stays.
    assert "celebration" in body


def test_an_entity_ack_from_an_out_of_date_client_is_rejected(client):
    """A 400, never a 200. Answering "ok" to an ack for a celebration that no
    longer exists would hide a stale client instead of surfacing it."""
    assert client.post("/api/marelo/ack",
                       json={"entity": "star:9:2", "key": 1}).status_code == 400
    assert client.post("/api/marelo/ack", json={"key": 1}).status_code == 400


def test_the_service_holds_no_entity_watermarks(tmp_path):
    """The KV and its three methods are gone, not merely unused -- a dormant
    read plus write on every /api/marelo request is what this removed."""
    _client, svc = make_client(tmp_path)
    for gone in ("entity_watermarks", "sync_and_seed_entity_watermarks",
                 "ack_entity_celebration"):
        assert not hasattr(svc, gone), f"{gone} survived the removal"
    # The SCOPE watermark trio is untouched.
    for kept in ("marelo_watermarks", "sync_watermark", "seed_watermark",
                 "ack_celebration"):
        assert hasattr(svc, kept), f"{kept} was removed by mistake"


def test_entity_tier_matches_rank_for_on_a_ragged_ladder(tmp_path):
    """THE invariant (scoring.py:8) for an entity whose ladder is missing
    tiers. Confirmed repro: ladder {Mario 10.00, Gold 20.00, Silver 30.00},
    time 10.50s -> score 92.5. A full-table lookup (the pre-fix bug) names
    that Grandmaster III -- a tier this ladder does not define -- while
    `classify.rank_for` (and `defined_tiers`-aware `division_for`) both say
    Gold I. Same star, same time must not disagree between the score and the
    medal beside it."""
    # No bundled library: the sheet-fitted ladders on star:8:2 would join
    # the best ladder and move the 92.5 this test pins.
    client, svc = make_client(tmp_path, bundled_library=False)
    with client:
        asyncio.run(svc.publish(_ev("practice_reset", 1000, {"igt_frames_before": 0})))
        asyncio.run(svc.publish(_ev("star_collected", 1315,
                                    {"course_id": 8, "star_id": 2, "igt_frames": 315})))
        client.put("/api/ranks/standards/star:8:2/Standard/Mario", json={"seconds": 10.0})
        client.put("/api/ranks/standards/star:8:2/Standard/Gold", json={"seconds": 20.0})
        client.put("/api/ranks/standards/star:8:2/Standard/Silver", json={"seconds": 30.0})
        asyncio.run(svc.set_strat(8, 2, "Standard"))
        # The attempt was journaled before the strategy existed; stamp it
        # directly, the same way test_views_marelo.py does.
        svc.db._conn.execute("UPDATE attempts SET strat_tag='Standard' WHERE course_id=8")
        svc.db._conn.commit()
        # CHANGED 2026-07-28 (task 0034): pb mode grades the SAVED row now,
        # not the fastest attempt -- save it explicitly rather than relying
        # on the attempt landing.
        star_aid = next(a.id for a in svc.db.attempts() if a.course_id == 8)
        asyncio.run(svc.save_pb(star_aid, "igt"))

        body = client.get("/api/marelo").json()
        entity = next(e for e in body["entities"] if e["key"] == "star:8:2")
        ladder = scoring.best_ladder(svc.ranks.ladders("star:8:2"))
        assert entity["score"] == 92.5
        assert entity["tier"] == classify.rank_for(ladder, 1050) == "Gold"
        assert entity["division"] == "I"


# -- breakdown "next rank" column (task C) -----------------------------------

def test_unpracticed_entity_next_rank_targets_gold_with_no_division(client):
    """An entity with no score has nothing to be a division INTO yet -- only
    the tier a first attempt targets is shown (spec's own example: "-> Gold",
    no division)."""
    body = client.get("/api/marelo").json()
    entity = next(e for e in body["entities"] if e["key"] == "star:9:2")
    assert entity["score"] is None
    assert entity["next_tier"] == "Gold"
    assert entity["next_division"] is None


def test_practiced_entity_next_rank_is_one_division_step_not_a_whole_tier(
        tmp_path):
    client, svc = make_client(tmp_path)
    with client:
        asyncio.run(svc.set_strat(9, 2, "Nuts Pless"))
        _mario_reset_and_collect(svc, 1000, 1420, 420)   # -> Iron I
        # CHANGED 2026-07-28 (task 0034): pb mode grades the SAVED row now,
        # not the fastest attempt -- save it explicitly.
        star_aid = next(a.id for a in svc.db.attempts() if a.course_id == 9)
        asyncio.run(svc.save_pb(star_aid, "igt"))

        body = client.get("/api/marelo").json()
        entity = next(e for e in body["entities"] if e["key"] == "star:9:2")
        defined = scoring.defined_tiers(scoring.best_ladder(
            svc.ranks.ladders("star:9:2")))
        # Recomputed from the same function the endpoint calls, not
        # hand-derived -- the contract is "matches division_progress",
        # never a guessed tier name.
        expected = scoring.division_progress(entity["score"], defined)
        assert entity["next_tier"] == expected["next_tier"]
        assert entity["next_division"] == expected["next_division"]
        # Iron I is not the top of the ladder, so there IS a next step.
        assert entity["next_tier"] is not None


def test_excluded_entitys_next_rank_reads_the_same_as_unpracticed(client):
    """An excluded row is unscored too -- it just got there by choice, not by
    never being played -- so its next-rank shape must match the unpracticed
    case exactly, not read as broken/blank."""
    before = client.get("/api/marelo").json()
    if not before["entities"]:
        return
    key = before["entities"][0]["key"]
    client.post("/api/marelo/exclude", json={"entity": key, "excluded": True})
    after = client.get("/api/marelo").json()
    row = next(e for e in after["entities"] if e["key"] == key)
    assert row["excluded"] is True
    assert row["next_tier"] == "Gold"
    assert row["next_division"] is None
    client.post("/api/marelo/exclude", json={"entity": key, "excluded": False})


# -- arriving at a scope is not earning a rank in it -------------------------
#
# Live report, 2026-07-28: "I just swapped the route around at the top over and
# over again, from a lower rank to a higher rank, and it seems to have
# triggered the middle-of-screen rank up animation. Swapping between routes
# like that should never trigger any rank up."
#
# A watermark could only ever be RAISED by ack_celebration -- i.e. by a
# celebration having been shown -- so every scope silently accumulated a
# rank-up it had never displayed and discharged it the first time the user
# looked at that scope. Measured on the live db: 3 of 7 watermarked scopes
# fired on sight, two of them tier crossings (the full-screen takeover).

def test_switching_the_active_scope_absorbs_instead_of_celebrating(tmp_path):
    test_client, service = make_client(tmp_path)
    with test_client:
        # Stand the trap up exactly as it occurred: a watermark left BELOW the
        # scope's real rank, which is what an unshown rank-up looks like.
        service.db.set_state("marelo_watermarks", {"overall": 0})
        service.db.set_state("marelo_active_scope", "route:1")   # we were elsewhere
        body = test_client.get("/api/marelo?scope=overall").json()
        if body["tier"] is None:
            pytest.skip("seeded fixture has no rankable overall score")
        assert body["celebration"] is None, body["celebration"]
        # ...and the arrival raised the watermark, so it cannot fire later.
        assert service.marelo_watermarks()["overall"] == scoring.progression_key(
            body["tier"], body["division"])


def _grade_a_star(test_client, service, course=8, star=2):
    """Practise one star and grade it, so a scope holding it is RANKABLE.

    Without this the bare fixture grades Iron V -- the floor -- and every
    celebration path returns before it decides anything, which is how two
    tests in this file managed to pass while proving nothing."""
    asyncio.run(service.publish(_ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(service.publish(_ev("star_collected", 1315,
                                    {"course_id": course, "star_id": star, "igt_frames": 315})))
    for rank, seconds in (("Mario", 10.0), ("Gold", 20.0), ("Silver", 30.0)):
        test_client.put(f"/api/ranks/standards/star:{course}:{star}/Standard/{rank}",
                        json={"seconds": seconds})
    asyncio.run(service.set_strat(course, star, "Standard"))
    service.db._conn.execute("UPDATE attempts SET strat_tag='Standard' WHERE course_id=?", (course,))
    service.db._conn.commit()
    attempt_id = next(a.id for a in service.db.attempts() if a.course_id == course)
    asyncio.run(service.save_pb(attempt_id, "igt"))


def test_staying_on_a_scope_still_celebrates_a_real_rise(tmp_path):
    """The direction the absorb rule must never swallow: a rank earned while
    the scope stays active still fires.

    It seeds a graded PB first, and that is the point. The bare fixture grades
    Iron V -- the FLOOR, progression key 0 -- so there is nothing below it to
    rise from, and this test skipped on every machine and every run from the
    day it was written until 2026-09-17. A skip that can never lift is a hole:
    the "never celebrate" half is asserted four times over, and nothing proved
    the other half still worked."""
    test_client, service = make_client(tmp_path, bundled_library=False)
    with test_client:
        _grade_a_star(test_client, service)

        body = test_client.get("/api/marelo").json()      # arrive, absorbing
        current = scoring.progression_key(body["tier"], body["division"])
        assert current > 0, body                          # above the floor, so a rise exists
        # Drop the watermark as a genuine rank-up leaves it, WITHOUT changing
        # the active scope: this is the earned case, not navigation.
        service.db.set_state("marelo_watermarks", {"overall": current - 1})
        again = test_client.get("/api/marelo").json()
        assert again["celebration"] is not None
        assert again["celebration"]["to"]["tier"] == again["tier"]


def test_browsing_a_non_active_scope_never_celebrates(tmp_path):
    """It MAKES the route it browses. The bare fixture seeds no routes, so
    this test skipped on every machine and every run from the day it was
    written until 2026-09-17 -- the one direction that proves browsing is not
    an arrival had no gate at all, which is the same hole
    `test_staying_on_a_scope_still_celebrates_a_real_rise` had."""
    test_client, service = make_client(tmp_path, bundled_library=False)
    with test_client:
        _grade_a_star(test_client, service)
        made = test_client.post("/api/routes", json={"name": "Browsed", "steps": [
            {"need": 1, "candidates": [{"type": "star", "course": 8, "star": 2}]}]})
        assert made.status_code == 200, made.text
        test_client.get("/api/marelo")            # overall becomes the noted scope
        routes = [s for s in test_client.get("/api/marelo/scopes").json()["scopes"]
                  if s["kind"] == "route"]
        assert routes, "the route just created must be a pickable scope"
        other = routes[0]["id"]
        # A watermark BELOW the route's real rank: if browsing were treated as
        # an arrival, this is the rise it would wrongly announce.
        service.db.set_state("marelo_watermarks", {"overall": 0, other: 0})
        body = test_client.get(f"/api/marelo?scope={other}").json()
        assert body["tier"], f"the browsed scope must be rankable: {body}"
        assert body["celebration"] is None
        # Browsing must not move the ACTIVE-scope memory either, or the next
        # header fetch would read as an arrival and swallow a real rank-up.
        assert service.db.get_state("marelo_active_scope", None) == "overall"


# -- /api/leaderboard + /api/leaderboard/runner/{name}[/summary] (Task 3) ---
#
# `make_client` wires a real `library`/`adoptions` pair (server/app.py mounts
# them unconditionally), so these routes see the actual bundled Ultimate
# Sheet snapshot -- real runners, real ladders. Runner-specific tests read a
# real name back off `/api/leaderboard` rather than hardcoding one, since the
# sheet grows and any fixed name could stop scoring on "overall" someday.

def test_the_three_leaderboard_routes_are_sync_so_fastapi_threadpools_them(client):
    """Fix wave (final review, M3): these three routes used to be `async
    def` with only the cache-hitting call itself hand-threadpooled, so
    `_groups`/`_you_scores` ran on the poller's shared asyncio loop --
    measured 13.32ms per `/api/leaderboard` request, one frame being 33.3ms.
    A plain `def` route is what `/api/marelo` already does and lets FastAPI
    threadpool the WHOLE body; a route sliding back to `async def` silently
    reopens the on-loop cost with no red test anywhere else, since every
    other test here only checks the RESPONSE shape."""
    endpoints = {route.path: route.endpoint for route in client.app.routes
                 if getattr(route, "path", "").startswith("/api/leaderboard")}
    assert endpoints, "no /api/leaderboard routes found on the app"
    for path, endpoint in endpoints.items():
        assert not asyncio.iscoroutinefunction(endpoint), (
            f"{path} is `async def` -- it must be a plain `def` so FastAPI "
            "threadpools the whole route instead of running it on the "
            "poller's shared asyncio loop")


def test_leaderboard_shape(client):
    body = client.get("/api/leaderboard?scope=overall").json()
    assert body["scope_id"] == "overall" and body["basis"] == "pb"
    assert set(body) >= {"scope_id", "label", "n", "basis", "rank_mode",
                         "sheet_revision", "omitted", "rows"}
    assert isinstance(body["omitted"], int)
    # Overall respects exclusions too. The Sheet catalog now reaches runners
    # whose only scores belong to default-excluded movements or subsections;
    # the controlled population test below pins exactly who is omitted.
    assert body["omitted"] >= 0
    you_rows = [row for row in body["rows"] if row["you"]]
    assert len(you_rows) == 1 and you_rows[0]["runner"] is None
    for row in body["rows"]:
        assert set(row) >= {"position", "runner", "you", "marelo", "tier",
                            "division", "mastery", "practiced", "n"}
        # `omitted` counts what `rows` left out; a runner already IN `rows`
        # is never also part of that count.
        if row["runner"] is not None:
            assert row["practiced"] >= 1


def _omission_population():
    """An included runner, an excluded-only runner, and two unrated cases."""
    def target(label, entity, reason, runner):
        return {"group": "Example", "section": "Example", "label": label,
                "version": None, "entity_key": entity, "miss_reason": reason,
                "approaches": [{"name": label, "ids": ["1"],
                    "ladder": {"Mario": 10.0, "Gold": 20.0},
                    "entries": [{"runner": runner, "time_cs": 1500,
                                 "version": None, "video": None}]}],
                "subsections": []}
    return {"schema_version": SCHEMA_VERSION, "sheet_revision": "2026-08-05T09:15:18",
            "runners": ["Included", "Excluded only", "Unrated", "Roster only"],
            "targets": [target("Star", "star:9:2", None, "Included"),
                        target("Excluded movement", None, "castle_movement", "Excluded only"),
                        target("Unplaced route", None, "route", "Unrated")]}


def test_overall_counts_excluded_only_runners_and_include_restores_them(tmp_path):
    """The board counts rated runners outside scope, never the whole roster.

    The same cached ratings must respond when the exclusion control changes
    scope membership; unplaced entries and roster-only names stay uncounted.
    """
    from sm64_events.library.audit import row_key
    test_client, service = make_client(tmp_path, bundled_library=False)
    with test_client:
        library = test_client.app.state.library
        assert library.absorb(_omission_population())["applied"]
        adoptions = test_client.app.state.library_adoptions
        target = library.payload["targets"][1]
        entity = adoptions.rows()[row_key(target, "Excluded movement", ["1"])]
        assert entity in service.rank_excluded()

        before = test_client.get("/api/leaderboard?scope=overall").json()
        assert {r["runner"] for r in before["rows"]} == {"Included", None}
        assert before["omitted"] == 1
        assert test_client.post("/api/marelo/exclude", json={
            "entity": entity, "excluded": False}).status_code == 200
        included = test_client.get("/api/leaderboard?scope=overall").json()
        assert {r["runner"] for r in included["rows"]} == {"Included", "Excluded only", None}
        assert included["omitted"] == 0 and included["n"] == before["n"] + 1

        assert test_client.post("/api/marelo/exclude", json={
            "entity": entity, "excluded": True}).status_code == 200
        restored = test_client.get("/api/leaderboard?scope=overall").json()
        assert restored["rows"] == before["rows"]
        assert restored["omitted"] == 1 and restored["n"] == before["n"]


def test_leaderboard_defaults_to_the_active_scope(client):
    assert client.get("/api/leaderboard").json()["scope_id"] == "overall"


def test_leaderboard_unknown_scope_is_404(client):
    assert client.get("/api/leaderboard?scope=route:999999").status_code == 404
    assert client.get("/api/leaderboard?scope=garbage").status_code == 404


def test_leaderboard_rows_are_marelo_descending_and_competition_ranked(client):
    rows = client.get("/api/leaderboard?scope=overall").json()["rows"]
    scored = [row["marelo"] for row in rows if row["marelo"] is not None]
    assert scored == sorted(scored, reverse=True)
    positions = [row["position"] for row in rows]
    assert positions == sorted(positions)
    assert positions[0] == 1


def test_leaderboard_basis_is_pb_even_under_avg_mode(client):
    """The board grades PB-basis always -- `rank_mode` rides along only so
    the UI can tell him the board's number and the Rank tab's differ."""
    client.put("/api/ranks/mode", json={"mode": "avg10"})
    body = client.get("/api/leaderboard?scope=overall").json()
    assert body["basis"] == "pb" and body["rank_mode"] == "avg10"
    client.put("/api/ranks/mode", json={"mode": "pb"})


def test_leaderboard_applies_the_users_exclusions(client):
    """Round 1, third read (2026-08-23), reversing the design-session rule
    that a runner's denominator must not shrink by the user's choices: "if
    I have personally excluded certain segments... it should also be
    excluded for all of the fake leaderboards & their pages as well". So an
    exclusion narrows the board's `n` exactly as it narrows his own, and
    the entity leaves every runner's breakdown -- on the very next fetch,
    with none of the cache's four inputs having moved (the per-scope row
    memo keys on the resolved groups). star:9:2 is the seeded ladder every
    fixture in this file relies on being present, so it is restored.

    Seeds its OWN board state (a star:8:2 ladder over the sheet's real
    coverage, plus a row adopted onto star:9:2) the same way the omitted
    test below does -- the first version read the board bare and passed
    only while the checkout's data/library_adoptions.json held a leaked
    adoption from an earlier, unisolated run (make_client's own comment)."""
    client.put("/api/ranks/standards/star:8:2/Standard/Mario", json={"seconds": 10.0})
    _adopt_row_onto(client, "star:9:2")
    before = client.get("/api/leaderboard?scope=overall").json()
    runner = next(row["runner"] for row in before["rows"] if row["runner"])
    assert "star:9:2" in {entity["key"] for entity in
                          client.get(f"/api/leaderboard/runner/{runner}?scope=overall").json()["entities"]}
    client.post("/api/marelo/exclude", json={"entity": "star:9:2", "excluded": True})
    try:
        after = client.get("/api/leaderboard?scope=overall").json()
        assert after["n"] == before["n"] - 1
        keys = {entity["key"] for entity in
                client.get(f"/api/leaderboard/runner/{runner}?scope=overall").json()["entities"]}
        assert "star:9:2" not in keys
    finally:
        client.post("/api/marelo/exclude", json={"entity": "star:9:2", "excluded": False})
    assert client.get("/api/leaderboard?scope=overall").json()["n"] == before["n"]


def test_leaderboard_omitted_counts_a_runner_scored_elsewhere_but_not_here(client):
    """`omitted` must count a runner who has SOME score in the whole corpus
    but none in THIS narrower scope -- not merely "everyone minus rows",
    which "overall" over the fixture's single laddered entity (star:9:2)
    can't distinguish: with only one entity graded anywhere, any runner
    with a score at all necessarily has it for that one entity, so `omitted`
    can never move there. `course:9` (star:9:2 only) stays that narrow
    scope; giving star:8:2 a ladder brings in the sheet's own REAL,
    unadopted coverage of it (a real star, plenty of community times) as
    the "elsewhere" population -- runners scored there and nowhere in
    course 9, which is exactly the shape `omitted` exists to count.

    Checked against the EXACT count, computed independently over
    `ratings.rate_runners`'s own output rather than through
    `scopes.aggregate` -- reusing that would just be board.py's own
    aggregation checking itself, and `>= 1` alone would pass under an
    off-by-one or a wrong-population bug just as easily as a correct one
    (the re-reviewer measured `omitted=251` against `rows=46` on this exact
    scenario, and `>= 1` cannot tell 251 from 1)."""
    from sm64_events.library import ratings
    client.put("/api/ranks/standards/star:8:2/Standard/Mario", json={"seconds": 10.0})
    in_scope = _adopt_row_onto(client, "star:9:2")
    # The independence claim above rests on course:9 resolving to exactly
    # this one rankable entity -- star:8:2 (just laddered, two lines up) is
    # course 8 and must NOT join it. Assert that directly: if a second
    # course-9 entity ever gains a ladder, `expected_omitted` below silently
    # starts measuring a different scope than `body["omitted"]` does.
    # Round 33: every course-9 star with a fitted sheet ladder grades now, so
    # the narrow scope is the WHOLE course rather than the one adopted star;
    # `expected_omitted` counts against exactly the entities the scope holds.
    course_scope = client.get("/api/marelo?scope=course:9").json()
    course_keys = {entity["key"] for entity in course_scope["entities"]}
    assert "star:9:2" in course_keys, course_keys
    library = client.app.state.library
    adoptions = client.app.state.library_adoptions
    scores = ratings.rate_runners(library.payload, adoptions.standards,
                                  adoptions.rows(), version="us").scores
    expected_omitted = sum(1 for by_entity in scores.values()
                           if not (course_keys & set(by_entity)))
    assert expected_omitted >= 1, "scenario didn't produce anyone to omit"
    body = client.get("/api/leaderboard?scope=course:9").json()
    runners = [row["runner"] for row in body["rows"]]
    assert in_scope in runners
    assert body["omitted"] == expected_omitted


def test_leaderboard_never_moves_marelo_watermarks(tmp_path):
    """THE trap: `_build_marelo` seeds/syncs/lowers a celebration watermark
    as a side effect of scoring a scope -- a board read must never fire a
    rank-up the user did not earn. `_score_scope` (used here, never
    `_build_marelo`) has no such side effect; this proves it end to end."""
    test_client, service = make_client(tmp_path)
    with test_client:
        before = service.marelo_watermarks()
        test_client.get("/api/leaderboard?scope=overall")
        assert service.marelo_watermarks() == before


def _adopt_row_onto(test_client, entity_key: str, skip_runner: str | None = None
                    ) -> str:
    """Points a real library row (one with a fitted ladder and at least one
    runner entry, whose first runner is not `skip_runner`) at `entity_key`,
    and returns the runner name that adoption guarantees will score.
    Whether the bundled sheet's OWN mapping happens to reach a given entity
    on its own is not something a test should depend on -- the sheet grows
    and a real intersection today is not one tomorrow; adopting one makes
    the scenario deterministic.

    Mutates the in-memory `Adoptions` object directly rather than calling
    `POST /api/library/adopt`: that endpoint SAVES to the real, un-overridden
    `library_adoptions_path()` this fixture wires (`make_client` passes no
    `adoptions_path`), and a test must never write into a real data file
    beside the repo it runs from."""
    from sm64_events.library.audit import row_key
    adoptions = test_client.app.state.library_adoptions
    library = test_client.app.state.library
    for target in library.payload["targets"]:
        for item in target["approaches"]:
            entries = [e for e in item["entries"] if e.get("runner")
                      and e["runner"] != skip_runner]
            if entries and item.get("ladder"):
                key = row_key(target, item["name"], item["ids"])
                adoptions._rows[key] = entity_key
                adoptions._sync()
                return entries[0]["runner"]
    pytest.fail(f"bundled sheet has no approach with both a ladder and a "
               f"runner entry (excluding {skip_runner!r}) -- nothing left "
               f"to adopt onto {entity_key!r}")


def _adopt_a_scored_runner(test_client) -> str:
    """The single-entity case: adopts onto star:9:2, the fixture's one
    seeded ladder."""
    return _adopt_row_onto(test_client, "star:9:2")


def test_leaderboard_runner_breakdown_shape(client):
    name = _adopt_a_scored_runner(client)
    body = client.get(
        f"/api/leaderboard/runner/{quote(name, safe='')}?scope=overall").json()
    assert body["runner"] == name and body["scope_id"] == "overall"
    assert set(body) >= {"runner", "scope_id", "label", "marelo", "mastery",
                         "coverage", "tier", "division", "next_division_at",
                         "division_progress", "n", "practiced", "entities"}
    assert body["entities"], "a scored runner must widen at least one entity"
    entity = next(e for e in body["entities"] if e["key"] == "star:9:2")
    assert entity["score"] is not None and entity["time_cs"] is not None
    assert set(entity) >= {"key", "label", "score", "tier", "division",
                           "next_tier", "next_division", "gain",
                           "excluded", "time_cs", "you"}
    assert entity["excluded"] is False
    assert set(entity["you"]) == {"score", "time_cs", "tier", "division"}


def test_leaderboard_runner_excluded_entities_stay_false(client):
    """The board's denominator ignores exclusions (see above); a runner's
    own breakdown must say the same about every one of its rows."""
    name = _adopt_a_scored_runner(client)
    client.post("/api/marelo/exclude", json={"entity": "star:9:2", "excluded": True})
    body = client.get(
        f"/api/leaderboard/runner/{quote(name, safe='')}?scope=overall").json()
    assert all(entity["excluded"] is False for entity in body["entities"])
    client.post("/api/marelo/exclude", json={"entity": "star:9:2", "excluded": False})


def test_leaderboard_runner_unknown_is_404(client):
    assert client.get(
        "/api/leaderboard/runner/ThisRunnerDoesNotExist999").status_code == 404


def test_leaderboard_runner_summary_shape(client):
    name = _adopt_a_scored_runner(client)
    body = client.get(
        f"/api/leaderboard/runner/{quote(name, safe='')}/summary").json()
    assert body["chips"] and body["chips"][0]["scope_id"] == "overall"
    assert set(body["chips"][0]) >= {"scope_id", "label", "tier", "division",
                                     "marelo", "n", "practiced"}
    chip = next(c for c in body["chips"] if c["scope_id"] == "overall")
    # >= 1, not == 1: since round 33 the sheet's own fitted ladders grade a
    # real runner on every star they have a time for, not the adopted row alone.
    assert chip["practiced"] >= 1


def test_leaderboard_runner_summary_unknown_is_404(client):
    assert client.get(
        "/api/leaderboard/runner/ThisRunnerDoesNotExist999/summary"
    ).status_code == 404
