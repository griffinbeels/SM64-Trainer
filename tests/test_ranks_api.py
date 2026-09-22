# tests/test_ranks_api.py
import json
from fastapi.testclient import TestClient
from sm64_events.storage.db import Database
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.tracking.service import TrackerService
from sm64_events.server.poller import Poller
from sm64_events.server.app import create_app
from sm64_events.ranks.standards import RankStandards
from calibration_reuse import reusing_calibrations

class OfflineMemory:
    attached = False
    def attach(self): return False
    def detach(self): pass

def _seed(tmp_path):
    """A real seed file so seeded_strategies() is non-empty (star:9:2 stays
    clear of the star:8:2/star:2:1 entities the rest of this file pokes at
    directly, so existing tests' "starts empty" assumptions hold)."""
    p = tmp_path / "seed.json"
    p.write_text(json.dumps({"version": 1, "entities": {
        "star:9:2": {"clock": "igt", "strategies": {
            "Nuts Pless": {"Mario": 12.93, "Master": 13.16, "Diamond": 13.36}}}}}))
    return p

def make_client(tmp_path, bundled_library=False):
    db = Database(tmp_path / "t.db")
    b = Broadcaster()
    ranks = RankStandards(tmp_path / "rs.json", seed_path=_seed(tmp_path)); ranks.load()
    svc = TrackerService(db, b, ranks=ranks)
    # `adoptions_path` MUST be scratch: create_app's default resolves the
    # adoption store cwd-relative (`core.paths.library_adoptions_path()`),
    # so a driven test adopting a row through the API was writing the
    # CHECKOUT's own data/library_adoptions.json -- and every later test in
    # the same checkout inherited that row as ambient board state (caught
    # 2026-08-23 by the wrap's clean-worktree run: a leaderboard test that
    # passed here for weeks failed there, because "here" had a leaked
    # adoption and the clean tree did not).
    # `library_bundled_path` to a file that does not exist: these tests model
    # a standards store holding ONLY what they put in it, and since round 33
    # the bundled library's fitted rows would otherwise join every star's
    # ladders at load (that is the feature, not a leak -- see
    # tests/test_library_refresh.py for the tests that want it on).
    with reusing_calibrations():
        app = create_app(Poller(OfflineMemory(), [], svc), b, service=svc,
                         adoptions_path=tmp_path / "library_adoptions.json",
                         # ...and the LOCAL snapshot too, or the checkout's own
                         # refreshed data/sheet_library.json.gz answers instead.
                         library_path=tmp_path / "sheet_library.json.gz",
                         library_bundled_path=(None if bundled_library
                                               else tmp_path / "no-library.json.gz"))
    return TestClient(app), svc

def test_make_client_never_touches_the_checkouts_own_adoption_file(tmp_path):
    """The wrap's clean-worktree run caught this (2026-08-23): without
    `adoptions_path`, create_app resolves the adoption store cwd-relative,
    so `_adopt_row_onto` tests WROTE the checkout's data/library_adoptions.json
    and later tests inherited that row as ambient board state -- a
    leaderboard test passed for weeks here and failed in a clean tree.
    Mutation-proved: dropping the kwarg from make_client reds this."""
    import pathlib
    client, _svc = make_client(tmp_path)
    with client:
        store_path = pathlib.Path(client.app.state.library_adoptions.path)
        assert store_path.is_relative_to(tmp_path), store_path


def test_get_empty_then_put_then_read_back(tmp_path):
    client, svc = make_client(tmp_path)
    with client:
        r = client.get("/api/ranks/standards", params={"entity": "star:8:2"})
        assert r.status_code == 200 and r.json()["strategies"] == {}
        r = client.put("/api/ranks/standards/star:8:2/Nuts%20Pless/Mario",
                       json={"seconds": 12.93})
        assert r.status_code == 200
        r = client.get("/api/ranks/standards", params={"entity": "star:8:2"})
        assert r.json()["strategies"]["Nuts Pless"]["Mario"] == 12.93

def test_get_standards_names_which_strategies_are_sheet_derived(tmp_path):
    """fitted_strategies must list strategies with a Sheet foundation --
    ranks.is_fitted's own contract, exposed as a
    sibling list because "strategies" is a {name: ladder} dict a per-entry
    "fitted" key would collide inside (a tier could be named "fitted")."""
    client, svc = make_client(tmp_path)
    with client:
        svc.ranks.apply_sheet_ladders(
            {"star:8:2": {"strategies": {"Sheet Strat": {"Mario": 11.0}}}})
        r = client.get("/api/ranks/standards", params={"entity": "star:8:2"})
        assert r.json()["fitted_strategies"] == ["Sheet Strat"]
        r = client.get("/api/ranks/standards", params={"entity": "star:9:2"})
        assert r.json()["fitted_strategies"] == []   # vetted-only, none fitted


def test_seeded_standards_api_serves_the_complete_fit_and_preserves_explicit_edits(tmp_path):
    client, svc = make_client(tmp_path)
    us = {"Mario": 12.0, "Grandmaster": 13.0, "Master": 14.0, "Diamond": 15.0,
          "Platinum": 16.0, "Gold": 17.0, "Silver": 18.0, "Bronze": 19.0}
    jp = {rank: value - 1.0 for rank, value in us.items()}
    mapping = {"star:9:2": {"strategies": {"Nuts Pless": us},
                            "jp_strategies": {"Nuts Pless": jp}}}
    with client:
        svc.ranks.apply_sheet_ladders(mapping)
        params = {"entity": "star:9:2"}
        result = client.get("/api/ranks/standards", params=params).json()
        assert result["strategies_us"]["Nuts Pless"] == us
        assert result["strategies_jp"]["Nuts Pless"] == jp
        assert result["fitted_strategies"] == ["Nuts Pless"]
        assert result["clearable_jp_strategies"] == []
        response = client.put("/api/ranks/standards/star:9:2/Nuts%20Pless/Master",
                              json={"seconds": 13.16})  # the old seed's exact number
        assert response.status_code == 200
        svc.ranks.load()
        svc.ranks.apply_sheet_ladders(mapping)
        result = client.get("/api/ranks/standards", params=params).json()
        assert result["strategies_us"]["Nuts Pless"] == {**us, "Master": 13.16}
        assert result["strategies_jp"]["Nuts Pless"] == jp
        assert result["fitted_strategies"] == ["Nuts Pless"]
        assert client.post("/api/ranks/standards/star:9:2/reset").status_code == 200
        result = client.get("/api/ranks/standards", params=params).json()
        assert result["strategies_us"]["Nuts Pless"] == us


def test_delete_strategy_and_bad_rank(tmp_path):
    client, svc = make_client(tmp_path)
    with client:
        client.post("/api/ranks/standards/star:8:2", json={"strategy": "X"})
        r = client.delete("/api/ranks/standards/star:8:2/X")
        assert r.status_code == 200
        r = client.put("/api/ranks/standards/star:8:2/X/NotARank", json={"seconds": 1.0})
        assert r.status_code == 409          # ValueError -> 409

def test_get_standards_no_entity_returns_all(tmp_path):
    """GET /api/ranks/standards with no entity param returns 200 with all standards."""
    client, svc = make_client(tmp_path)
    with client:
        # Seed some data for two entities
        client.put("/api/ranks/standards/star:8:2/Fast/Mario", json={"seconds": 12.5})
        client.put("/api/ranks/standards/star:2:1/Cannonless/Diamond", json={"seconds": 30.0})
        r = client.get("/api/ranks/standards")
        assert r.status_code == 200
        data = r.json()
        # to_json() returns the full store with an "entities" key
        assert "entities" in data
        assert "star:8:2" in data["entities"]
        assert "star:2:1" in data["entities"]


def test_reset_entity_endpoint(tmp_path):
    client, svc = make_client(tmp_path)
    with client:
        # seed a user edit, confirm it's there
        client.post("/api/ranks/standards/star:8:2", json={"strategy": "Custom"})
        r = client.get("/api/ranks/standards", params={"entity": "star:8:2"})
        assert "Custom" in r.json()["strategies"]
        # reset (no seed configured in this test store -> entity reverts to empty)
        r = client.post("/api/ranks/standards/star:8:2/reset")
        assert r.status_code == 200
        r = client.get("/api/ranks/standards", params={"entity": "star:8:2"})
        assert r.json()["strategies"] == {}

def test_get_standards_includes_videos(tmp_path):
    client, svc = make_client(tmp_path)
    # seed a video directly into the store
    svc.ranks._data["entities"]["star:8:2"] = {
        "clock": "igt", "strategies": {"Nuts": {"Mario": 12.6}},
        "videos": {"Nuts": "https://youtu.be/A"}}
    with client:
        r = client.get("/api/ranks/standards", params={"entity": "star:8:2"})
        assert r.status_code == 200
        assert r.json()["videos"] == {"Nuts": "https://youtu.be/A"}


def test_get_standards_includes_cutoff_videos_and_xcams(tmp_path):
    client, svc = make_client(tmp_path)
    svc.ranks._data["entities"]["star:8:2"] = {
        "clock": "igt", "strategies": {"Nuts": {"Mario": 12.93, "Diamond": 13.36}},
        "clips": {"Nuts": [[1290, "mario"], [1326, "diamond"]]}}
    with client:
        d = client.get("/api/ranks/standards", params={"entity": "star:8:2"}).json()
        assert d["cutoff_videos"]["Nuts"] == {"Mario": "mario", "Diamond": "diamond"}
        assert d["xcams_url"].endswith("?star=ssl_3")
        assert d["user_videos"] == {}


def test_put_and_delete_cutoff_video_override(tmp_path):
    client, svc = make_client(tmp_path)
    with client:
        client.put("/api/ranks/standards/star:8:2/Nuts/Mario", json={"seconds": 12.93})
        r = client.put("/api/ranks/standards/star:8:2/Nuts/Gold/video",
                       json={"url": "https://youtu.be/g"})
        assert r.status_code == 200
        d = client.get("/api/ranks/standards", params={"entity": "star:8:2"}).json()
        assert d["user_videos"]["Nuts"]["Gold"] == "https://youtu.be/g"
        assert d["cutoff_videos"]["Nuts"]["Gold"] == "https://youtu.be/g"
        r = client.delete("/api/ranks/standards/star:8:2/Nuts/Gold/video")
        assert r.status_code == 200
        d = client.get("/api/ranks/standards", params={"entity": "star:8:2"}).json()
        assert d["user_videos"] == {}


def test_put_video_bad_rank_is_409(tmp_path):
    client, svc = make_client(tmp_path)
    with client:
        r = client.put("/api/ranks/standards/star:8:2/Nuts/Iron/video", json={"url": "x"})
        assert r.status_code == 409


# -- rank mode (average rank mode spec) ----------------------------------------

def test_put_rank_mode_persists_and_validates(tmp_path):
    client, svc = make_client(tmp_path)
    with client:
        r = client.put("/api/ranks/mode", json={"mode": "avg10"})
        assert r.status_code == 200 and r.json() == {"ok": True}
        assert svc.db.get_state("rank_mode", "pb") == "avg10"
        # every registry key round-trips
        for mode in ["pb", "avg50", "best10", "best50", "lifetime"]:
            assert client.put("/api/ranks/mode",
                              json={"mode": mode}).status_code == 200
        # junk -> 409, stored value untouched
        r = client.put("/api/ranks/mode", json={"mode": "bogus"})
        assert r.status_code == 409
        assert svc.db.get_state("rank_mode", "pb") == "lifetime"


def test_set_rank_mode_broadcasts_rank_mode_changed(tmp_path):
    import asyncio
    client, svc = make_client(tmp_path)
    seen = []

    async def capture(event):
        seen.append(event)

    svc.broadcaster.publish = capture
    asyncio.run(svc.set_rank_mode("best10"))
    assert [e.type for e in seen] == ["rank_mode_changed"]
    assert seen[0].payload == {"mode": "best10"}
# -- strategy-delete addendum: ?purge=true + GET seeded (Task 10) ------------

def test_get_lists_seeded_strategies(tmp_path):
    client, svc = make_client(tmp_path)
    with client:
        ek = next(iter(svc.ranks.to_json()["entities"]))
        body = client.get("/api/ranks/standards", params={"entity": ek}).json()
        assert body["seeded"] == svc.ranks.seeded_strategies(ek)


def test_delete_purge_true_fully_deletes_custom(tmp_path):
    client, svc = make_client(tmp_path)
    with client:
        ek = next(iter(svc.ranks.to_json()["entities"]))
        client.post(f"/api/ranks/standards/{ek}", json={"strategy": "customx"})
        r = client.delete(f"/api/ranks/standards/{ek}/customx", params={"purge": "true"})
        assert r.status_code == 200
        assert "customx" not in svc.ranks.strategies(ek)
        assert "customx" in svc.db.get_state("deleted_strats", {}).get(ek, [])


def test_delete_purge_true_refuses_seeded(tmp_path):
    client, svc = make_client(tmp_path)
    with client:
        ek = next(iter(svc.ranks.to_json()["entities"]))
        seeded = svc.ranks.seeded_strategies(ek)[0]
        r = client.delete(f"/api/ranks/standards/{ek}/{seeded}", params={"purge": "true"})
        assert r.status_code == 409
        assert seeded in svc.ranks.strategies(ek)


def test_delete_without_purge_keeps_clear_semantics(tmp_path):
    client, svc = make_client(tmp_path)
    with client:
        ek = next(iter(svc.ranks.to_json()["entities"]))
        client.post(f"/api/ranks/standards/{ek}", json={"strategy": "customy"})
        r = client.delete(f"/api/ranks/standards/{ek}/customy")
        assert r.status_code == 200
        assert "customy" not in svc.db.get_state("deleted_strats", {}).get(ek, [])


def test_a_dead_example_video_never_reaches_the_payload(tmp_path):
    """Round 2 of task 0098, end to end at the endpoint: every community pool
    (clips, cutoff_videos, videos) is filtered through the liveness verdicts,
    the tier link falls back to the next eligible clip, and a hand-attached
    override survives its own URL being marked dead."""
    from fastapi import FastAPI
    from sm64_events.library import videocheck
    from sm64_events.server.ranks_api import create_ranks_router

    rs = tmp_path / "rs2.json"
    rs.write_text(json.dumps({"version": 3, "entities": {
        "star:8:2": {"clock": "igt",
            "strategies": {"Nuts": {"Mario": 12.93, "Diamond": 13.36}},
            "clips": {"Nuts": [[1280, "https://v/dead-fastest"],
                               [1290, "https://v/alive-next"]]},
            "videos": {"Nuts": "https://v/dead-fastest"},
            "user_videos": {"Nuts": {"Diamond": "https://v/dead-fastest"}}}}}))
    ranks = RankStandards(rs); ranks.load()
    checks_path = tmp_path / "checks.json.gz"
    videocheck.save_checks(checks_path, {
        "https://v/dead-fastest": {"status": "dead",
                                   "checked": "2026-08-14T00:00:00Z"}})
    db = Database(tmp_path / "t2.db")
    svc = TrackerService(db, Broadcaster(), ranks=ranks)
    app = FastAPI()
    app.include_router(create_ranks_router(svc, video_checks_path=checks_path))
    payload = TestClient(app).get("/api/ranks/standards",
                                  params={"entity": "star:8:2"}).json()
    assert payload["clips"]["Nuts"] == [[1290, "https://v/alive-next"]]
    assert payload["cutoff_videos"]["Nuts"]["Mario"] == "https://v/alive-next"
    assert payload["videos"] == {}
    # the override is HIS fact — never filtered
    assert payload["cutoff_videos"]["Nuts"]["Diamond"] == "https://v/dead-fastest"
