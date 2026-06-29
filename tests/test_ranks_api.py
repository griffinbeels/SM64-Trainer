# tests/test_ranks_api.py
from fastapi.testclient import TestClient
from sm64_events.storage.db import Database
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.tracking.service import TrackerService
from sm64_events.server.poller import Poller
from sm64_events.server.app import create_app
from sm64_events.ranks.standards import RankStandards

class OfflineMemory:
    attached = False
    def attach(self): return False
    def detach(self): pass

def make_client(tmp_path):
    db = Database(tmp_path / "t.db")
    b = Broadcaster()
    ranks = RankStandards(tmp_path / "rs.json"); ranks.load()
    svc = TrackerService(db, b, ranks=ranks)
    app = create_app(Poller(OfflineMemory(), [], svc), b, service=svc)
    return TestClient(app), svc

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
    import json
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
