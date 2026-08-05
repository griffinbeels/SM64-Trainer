"""The library REST surface, driven against the snapshot we actually ship."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sm64_events.core.paths import bundled_sheet_library
from sm64_events.library.store import LibraryStore
from sm64_events.server.library_api import create_library_router


@pytest.fixture(scope="module")
def client():
    store = LibraryStore(None, bundled_sheet_library())
    store.load()
    app = FastAPI()
    app.include_router(create_library_router(store))
    return TestClient(app)


def test_status_reports_the_sheet_revision_and_the_ladder_model(client):
    body = client.get("/api/library/status").json()
    assert body["sheet_revision"] >= "2026-08-05T09:15:18"
    assert body["targets"] >= 250 and body["runners"] >= 440
    assert body["ladder_model"]["percentiles"]["Mario"] == 6.7


def test_the_index_groups_targets_without_shipping_every_entry(client):
    body = client.get("/api/library").json()
    assert len(body["groups"]) >= 18
    first = body["groups"][0]["targets"][0]
    assert {"index", "label", "entity_key", "approaches", "entries"} <= set(first)
    assert "entries" in first and isinstance(first["entries"], int)
    # the index must stay an index: no raw entry lists in it
    assert "approaches" not in body["groups"][0]["targets"][0].get("rows", {})


def test_one_target_carries_its_approaches_entries_and_ladders(client):
    body = client.get("/api/library/target/0").json()
    assert body["index"] == 0 and body["label"]
    approach = body["approaches"][0]
    assert approach["entries"] and "time_cs" in approach["entries"][0]
    assert "ladder" in approach


def test_an_index_off_the_end_is_a_404(client):
    assert client.get("/api/library/target/999999").status_code == 404


def test_an_entity_lookup_answers_even_when_the_community_has_nothing(client):
    # The book mark on the objective card asks this for whatever the user is
    # practising. "Nobody has timed this" is an answer, not an error.
    assert client.get("/api/library/entity/star:1:0").json()["targets"]
    empty = client.get("/api/library/entity/star:99:9")
    assert empty.status_code == 200 and empty.json()["targets"] == []


def test_a_100_coin_star_returns_every_route_that_shares_it(client):
    body = client.get("/api/library/entity/star:4:6").json()
    assert len(body["targets"]) >= 2, [t["label"] for t in body["targets"]]


def test_a_runner_page_gathers_that_persons_whole_sheet(client):
    body = client.get("/api/library/runner/Kally").json()
    assert body["runner"] == "Kally" and len(body["entries"]) > 100
    row = body["entries"][0]
    assert {"target", "approach", "kind", "time_cs", "video"} <= set(row)
    assert client.get("/api/library/runner/nobody at all").json()["entries"] == []


def test_the_runner_list_is_served_for_a_search_box(client):
    runners = client.get("/api/library/runners").json()["runners"]
    assert len(runners) >= 440 and "Kally" in runners


def test_a_refresh_that_cannot_reach_the_sheet_says_so(client, monkeypatch):
    # "refresh did nothing" and "refresh could not reach Google" look identical
    # to a caller unless the failure carries a reason.
    def boom():
        raise OSError("name or service not known")
    monkeypatch.setattr("sm64_events.server.library_api.fetch", boom)
    response = client.post("/api/library/refresh")
    assert response.status_code == 503
    assert "could not fetch" in response.json()["detail"]
