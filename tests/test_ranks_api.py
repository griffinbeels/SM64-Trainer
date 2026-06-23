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
