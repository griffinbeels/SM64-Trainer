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
        assert set(entity) >= {"key", "label", "score", "gain", "excluded"}
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
