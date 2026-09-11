"""A scope read must not resolve the rest of the community's curves."""
from test_ranks_api import make_client


def test_route_only_resolves_its_candidates_and_preserves_best_k(tmp_path, monkeypatch):
    client, service = make_client(tmp_path)
    with client:
        for star in (0, 1, 2):
            client.put(f"/api/ranks/standards/star:2:{star}/Standard/Mario",
                       json={"seconds": 12})
        client.post("/api/marelo/exclude", json={"entity": "star:2:2", "excluded": True})
        route = client.post("/api/routes", json={"name": "Scoped", "steps": [
            {"need": 3, "candidates": [
                {"type": "star", "course": 2, "star": star} for star in (0, 1, 2, 3)]}
        ]}).json()
        empty = client.post("/api/routes", json={"name": "Empty", "steps": []}).json()
        touched = []
        original = service.ranks.overall_curve

        def resolve(key, version=None):
            touched.append(key)
            return original(key, version)

        monkeypatch.setattr(service.ranks, "overall_curve", resolve)
        result = client.get(f"/api/marelo?scope=route:{route['id']}").json()
        assert result["n"] == 2  # excluded and unrankable candidates hold no slot
        assert {row["key"] for row in result["entities"]} == {
            "star:2:0", "star:2:1", "star:2:2"}
        assert next(row for row in result["entities"] if row["key"] == "star:2:2")["excluded"]
        assert touched and set(touched) <= {f"star:2:{star}" for star in range(4)}
        touched.clear()
        assert client.get(f"/api/marelo?scope=route:{empty['id']}").json()["n"] == 0
        assert touched == []
