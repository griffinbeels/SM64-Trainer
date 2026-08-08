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


def test_target_rows_carry_their_adoption_state(tmp_path):
    # The Library page's link button needs three facts per row: its stable
    # key (the adopt endpoints speak row_key, not indices), whether it is
    # already assigned, and whether this instance can adopt at all.
    from sm64_events.library import adoptions as ad
    from sm64_events.ranks.standards import RankStandards

    ladder = {"Mario": 2.76, "Grandmaster": 2.80, "Master": 2.86,
              "Diamond": 2.93, "Platinum": 3.00, "Gold": 3.10,
              "Silver": 3.20, "Bronze": 3.40}

    def item(name):
        return {"ids": ["1"], "name": name, "best_cs": 276, "best_runner": "M",
                "times": {}, "ideal_cs": None, "fill_rate": 0.2,
                "ladder": dict(ladder), "ladder_samples": 40,
                "entries": [{"runner": "r", "time_cs": 276,
                             "video": None, "version": None}]}

    store = LibraryStore()
    store._payload = {
        "schema_version": 1, "sheet_revision": "2026-08-05T09:15:18",
        "fetched_at": "x", "runners": [], "ladder_model": {}, "targets": [
            {"entity_key": None, "group": "Castle Movements (Lobby)",
             "section": "★ BoB", "label": "Lobby door (L) - BoB door",
             "version": None, "miss_reason": "castle_movement",
             "approaches": [item("Lobby door (L) - BoB door")],
             "subsections": [item("First stretch")]}]}
    standards = RankStandards(tmp_path / "rank_standards.json")
    standards.load()
    adoptions = ad.Adoptions(tmp_path / "library_adoptions.json", store, standards)
    adoptions.load()
    app = FastAPI()
    app.include_router(create_library_router(store, adoptions=adoptions))
    with_adopt = TestClient(app)

    body = with_adopt.get("/api/library/target/0").json()
    assert body["adoptable"] is True
    rows = body["approaches"] + body["subsections"]
    assert all(row["row_key"] for row in rows)
    assert all(row["adopted"] is None for row in rows)

    key = body["approaches"][0]["row_key"]
    assert with_adopt.post("/api/library/adopt",
                           json={"row_key": key,
                                 "entity_key": "segment:42"}).status_code == 200
    after = with_adopt.get("/api/library/target/0").json()
    assert after["approaches"][0]["adopted"] == "segment:42"
    assert after["subsections"][0]["adopted"] is None


def test_a_read_only_instance_still_serves_row_keys_but_says_not_adoptable(client):
    # `client` mounts no adoptions store (a broadcast-only second instance).
    body = client.get("/api/library/target/0").json()
    assert body["adoptable"] is False
    assert all(row.get("row_key") for row in body["approaches"])
    # Both full-target doors serve the same decorated shape -- the page never
    # branches on which one it came through.
    entity = client.get("/api/library/entity/star:1:0").json()["targets"][0]
    assert "adoptable" in entity and "index" in entity
    assert all(row.get("row_key") for row in entity["approaches"])


def test_an_entity_less_target_carries_its_name_matched_segment(tmp_path):
    # Round 6: "we should autoassign any segments that exist already." The
    # stamp is computed per request against the LIVE segment list, so a
    # segment built after the snapshot shipped still pairs.
    from sm64_events.library import adoptions as ad
    from sm64_events.ranks.standards import RankStandards

    store = LibraryStore()
    store._payload = {
        "schema_version": 1, "sheet_revision": "2026-08-05T09:15:18",
        "fetched_at": "x", "runners": [], "ladder_model": {}, "targets": [
            {"entity_key": None, "group": "Castle Movements (Lobby)",
             "section": "★ Misc", "label": "Lakitu skip",
             "version": None, "miss_reason": "castle_movement",
             "approaches": [], "subsections": []},
            {"entity_key": "star:1:0", "group": "1. Bob-omb Battlefield",
             "section": "★ BoB", "label": "Big Bob-omb on the Summit",
             "version": None, "miss_reason": None,
             "approaches": [], "subsections": []}]}
    standards = RankStandards(tmp_path / "rank_standards.json")
    standards.load()
    adoptions = ad.Adoptions(tmp_path / "library_adoptions.json", store, standards)
    adoptions.load()
    app = FastAPI()
    app.include_router(create_library_router(
        store, adoptions=adoptions,
        segment_names=lambda: [(3, "Lakitu Skip"), (6, "BitFS Pipe Entry")]))
    api = TestClient(app)

    movement = api.get("/api/library/target/0").json()
    assert movement["matched_segment"] == {"entity": "segment:3",
                                           "name": "Lakitu Skip"}
    # an entity-bearing target never auto-matches -- its rows already grade
    star = api.get("/api/library/target/1").json()
    assert star["matched_segment"] is None


def test_without_a_segment_list_no_target_claims_a_match(client):
    body = client.get("/api/library/target/0").json()
    assert body.get("matched_segment") is None
