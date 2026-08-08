"""Assigning a library row to a segment the user built.

The sheet's movements are finer than our segments and its subsections have no
segment at all, so the user builds one and points a row at it -- we never
invent 113 segments nobody asked for."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sm64_events.library import adoptions as ad
from sm64_events.library.audit import row_key
from sm64_events.library.store import LibraryStore
from sm64_events.ranks.standards import RankStandards
from sm64_events.server.library_api import create_library_router

LADDER = {"Mario": 2.76, "Grandmaster": 2.80, "Master": 2.86, "Diamond": 2.93,
          "Platinum": 3.00, "Gold": 3.10, "Silver": 3.20, "Bronze": 3.40}


def _payload():
    def item(name, ladder=True, entries=40):
        return {"ids": ["1"], "name": name, "best_cs": 276, "best_runner": "M",
                "times": {}, "ideal_cs": None, "fill_rate": 0.2,
                "ladder": dict(LADDER) if ladder else None,
                "ladder_samples": entries,
                "entries": [{"runner": f"r{i}", "time_cs": 276 + i,
                             "video": None, "version": None}
                            for i in range(entries)]}
    return {"schema_version": 1, "sheet_revision": "2026-08-05T09:15:18",
            "fetched_at": "x", "runners": [], "ladder_model": {}, "targets": [
                {"entity_key": None, "group": "Castle Movements (Lobby)",
                 "section": "★ BoB", "label": "Lobby door (L) - BoB door",
                 "version": None, "miss_reason": "castle_movement",
                 "approaches": [item("Lobby door (L) - BoB door"),
                                item("Thin one", ladder=False, entries=3)],
                 "subsections": []}]}


@pytest.fixture()
def wiring(tmp_path):
    store = LibraryStore()
    store._payload = _payload()
    standards = RankStandards(tmp_path / "rank_standards.json")
    standards.load()
    adoptions = ad.Adoptions(tmp_path / "library_adoptions.json", store, standards)
    adoptions.load()
    target = store.payload["targets"][0]
    keys = {item["name"]: row_key(target, item["name"], item["ids"])
            for item in target["approaches"]}
    return adoptions, standards, keys, tmp_path


def test_adopting_gives_the_segment_the_communitys_ladder(wiring):
    adoptions, standards, keys, _ = wiring
    result = adoptions.adopt(keys["Lobby door (L) - BoB door"], "segment:42")
    # The approach is named after its target, so filing it under a segment of
    # the same name would stutter; it lands as "Standard".
    assert result["strategy"] == ad.DEFAULT_STRATEGY
    assert standards.strategies("segment:42") == [ad.DEFAULT_STRATEGY]
    assert standards.ladder_cs("segment:42", ad.DEFAULT_STRATEGY)["Mario"] == 276
    assert standards.is_fitted("segment:42", ad.DEFAULT_STRATEGY)
    assert "segment:42" in standards.graded_entities()


def test_unadopting_actually_takes_the_strategy_away(wiring):
    adoptions, standards, keys, _ = wiring
    key = keys["Lobby door (L) - BoB door"]
    adoptions.adopt(key, "segment:42")
    adoptions.unadopt(key)
    assert standards.strategies("segment:42") == []


def test_an_assignment_survives_a_restart(wiring, tmp_path):
    adoptions, standards, keys, _ = wiring
    adoptions.adopt(keys["Lobby door (L) - BoB door"], "segment:42")
    fresh_standards = RankStandards(tmp_path / "rank_standards.json")
    fresh_standards.load()
    fresh = ad.Adoptions(tmp_path / "library_adoptions.json", adoptions.store,
                         fresh_standards)
    fresh.load()
    assert fresh_standards.strategies("segment:42") == [ad.DEFAULT_STRATEGY]


def test_a_row_with_no_ladder_is_refused_by_name(wiring):
    adoptions, _, keys, _ = wiring
    with pytest.raises(ad.AdoptionError) as err:
        adoptions.adopt(keys["Thin one"], "segment:42")
    assert "only 3 recorded times" in str(err.value)


def test_an_unknown_row_is_refused(wiring):
    adoptions, _, _, _ = wiring
    with pytest.raises(ad.AdoptionError):
        adoptions.adopt("no such row", "segment:42")


def test_a_variant_qualified_entity_is_refused(wiring):
    adoptions, _, keys, _ = wiring
    adoptions.qualified = {"star:4:6"}
    with pytest.raises(ad.AdoptionError) as err:
        adoptions.adopt(keys["Lobby door (L) - BoB door"], "star:4:6")
    assert "exit-star variant" in str(err.value)


def test_a_vetted_strategy_of_the_same_name_wins(wiring):
    """ROUND 6 (2026-08-07), reversing round 5's refusal arm: assigning a row
    to a segment that already carries a vetted strategy of the same name is
    ALLOWED -- the assignment is what the Library page's display association
    rides on -- and the vetted ladder still wins on the merged read, because
    the standards store's read-merge keeps vetted structurally. "We should
    autoassign any segments that exist already, and otherwise let them be
    associated by hand." """
    adoptions, standards, keys, _ = wiring
    standards.create_strategy("segment:42", ad.DEFAULT_STRATEGY)
    standards.set_threshold("segment:42", ad.DEFAULT_STRATEGY, "Mario", 2.00)
    result = adoptions.adopt(keys["Lobby door (L) - BoB door"], "segment:42")
    assert result["adopted"] is True
    assert adoptions.rows()[keys["Lobby door (L) - BoB door"]] == "segment:42"
    # the vetted cutoff, not the fitted row's 276
    assert standards.ladder_cs("segment:42", ad.DEFAULT_STRATEGY)["Mario"] == 200
    assert not standards.is_fitted("segment:42", ad.DEFAULT_STRATEGY)


def test_a_corrupt_assignments_file_is_simply_empty(tmp_path):
    path = tmp_path / "library_adoptions.json"
    path.write_text("{not json", encoding="utf-8")
    assert ad.load(path) == {}
    path.write_text(json.dumps({"rows": {"a": 5, "b": "segment:1"}}), encoding="utf-8")
    assert ad.load(path) == {"b": "segment:1"}


def test_the_routes_report_a_refusal_rather_than_failing_silently(wiring):
    adoptions, _, keys, _ = wiring
    app = FastAPI()
    app.include_router(create_library_router(adoptions.store, adoptions=adoptions))
    client = TestClient(app)
    ok = client.post("/api/library/adopt",
                     json={"row_key": keys["Lobby door (L) - BoB door"],
                           "entity_key": "segment:42"})
    assert ok.status_code == 200 and ok.json()["adopted"] is True
    # 409, not 400: the request is well formed and the refusal is about the
    # state of the world.
    bad = client.post("/api/library/adopt",
                      json={"row_key": keys["Thin one"], "entity_key": "segment:42"})
    assert bad.status_code == 409 and "no rank standards" in bad.json()["detail"]
    assert client.post("/api/library/adopt", json={}).status_code == 400
    assert client.get("/api/library/adoptions").json()["rows"]
    client.post("/api/library/unadopt",
                json={"row_key": keys["Lobby door (L) - BoB door"]})
    assert client.get("/api/library/adoptions").json()["rows"] == {}


def test_a_refresh_re_syncs_adopted_ladders(wiring, monkeypatch):
    """standards._sheet holds ladders derived from the payload as of the last
    adopt/unadopt/load -- a refresh replaces that payload in place with no
    call of its own, so an adopted strategy keeps grading against the
    PRE-refresh ladder until something re-syncs it. The refresh route must."""
    adoptions, standards, keys, _ = wiring
    key = keys["Lobby door (L) - BoB door"]
    adoptions.adopt(key, "segment:42")
    assert standards.ladder_cs("segment:42", ad.DEFAULT_STRATEGY)["Mario"] == 276

    def fake_refresh(fetch_fn, overrides=None):
        # The SAME store object refresh() mutates in place, exactly as a real
        # POST /api/library/refresh does -- the adopted row's ladder moved.
        moved = _payload()
        moved["targets"][0]["approaches"][0]["ladder"]["Mario"] = 3.00
        adoptions.store._payload = moved
        return {"applied": True, "sheet_revision": "2026-08-06T00:00:00"}
    monkeypatch.setattr(adoptions.store, "refresh", fake_refresh)

    app = FastAPI()
    app.include_router(create_library_router(adoptions.store, adoptions=adoptions))
    client = TestClient(app)
    response = client.post("/api/library/refresh")
    assert response.status_code == 200 and response.json()["applied"] is True
    assert standards.ladder_cs("segment:42", ad.DEFAULT_STRATEGY)["Mario"] == 300


def test_the_adopt_routes_are_absent_without_a_standards_store(wiring):
    adoptions, _, _, _ = wiring
    app = FastAPI()
    app.include_router(create_library_router(adoptions.store))
    client = TestClient(app)
    assert client.post("/api/library/adopt", json={}).status_code == 404
    assert client.get("/api/library").status_code == 200


def test_auto_match_pairs_by_normalized_name_only():
    """ROUND 6: "we should autoassign any segments that exist already."
    Ladder proximity was measured structurally unable here (the corpus
    segments' hand-seeded 3-tier rows never reach `_distance`'s 4-shared-tier
    floor; 184 rows x 18 vetted strategies scored zero pairs), so the auto
    key is normalized NAME equality -- case and punctuation blind, nothing
    fuzzier. Exactly one pair exists on today's snapshot (Lakitu skip), and
    a segment the user builds tomorrow with a movement's name pairs on the
    next page load."""
    segments = [(3, "Lakitu Skip"), (6, "BitFS Pipe Entry")]
    assert ad.auto_match("Lakitu skip", segments) == {
        "entity": "segment:3", "name": "Lakitu Skip"}
    assert ad.auto_match("lakitu-skip!", segments) == {
        "entity": "segment:3", "name": "Lakitu Skip"}
    assert ad.auto_match("Lobby door (L) - BoB door", segments) is None
    assert ad.auto_match("", segments) is None
