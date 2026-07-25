# tests/test_ranks_api_marelo.py
"""REST surface for MARELO (spec 2026-07-24-marelo-rank-system, Task 8).

Reuses test_ranks_api.py's make_client (a real seeded RankStandards store),
so `entities` may be non-empty from the start -- the seeded star:9:2 ladder.
"""
import pytest

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
    before = client.get("/api/marelo").json()
    if not before["entities"]:
        return
    key = before["entities"][0]["key"]
    assert client.post("/api/marelo/exclude",
                       json={"entity": key, "excluded": True}).status_code == 200
    after = client.get("/api/marelo").json()
    assert after["n"] == before["n"] - 1
    assert key not in {e["key"] for e in after["entities"]}
    client.post("/api/marelo/exclude", json={"entity": key, "excluded": False})


def test_history_returns_points_for_a_valid_scope(client):
    body = client.get("/api/marelo/history?scope=overall").json()
    assert body["scope_id"] == "overall"
    assert isinstance(body["points"], list)


def test_history_of_an_unknown_scope_is_404(client):
    assert client.get("/api/marelo/history?scope=route:999999").status_code == 404


def test_ack_is_accepted(client):
    assert client.post("/api/marelo/ack",
                       json={"scope": "overall", "key": 3}).status_code == 200
