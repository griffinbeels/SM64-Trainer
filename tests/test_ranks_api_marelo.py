# tests/test_ranks_api_marelo.py
"""REST surface for MARELO (spec 2026-07-24-marelo-rank-system, Task 8).

Reuses test_ranks_api.py's make_client (a real seeded RankStandards store),
so `entities` may be non-empty from the start -- the seeded star:9:2 ladder.
"""
import asyncio
from datetime import datetime, timezone

import pytest

from sm64_events.core.events import Event
from sm64_events.ranks import classify, scoring
from test_ranks_api import make_client


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
    assert client.get("/api/marelo/exclusions").json()["excluded"] == []
    client.post("/api/marelo/exclude", json={"entity": "star:9:2", "excluded": True})
    assert client.get("/api/marelo/exclusions").json()["excluded"] == ["star:9:2"]
    client.post("/api/marelo/exclude", json={"entity": "star:9:2", "excluded": False})
    assert client.get("/api/marelo/exclusions").json()["excluded"] == []


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
    client, svc = make_client(tmp_path)
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
