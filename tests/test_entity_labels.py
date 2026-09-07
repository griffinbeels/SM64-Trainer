"""Scope labels use one fresh segment catalog, including excluded rows."""
import pytest

from sm64_events.library.board import RatedSheet, RatingsCache
from sm64_events.library.ratings import RatedRunners
from sm64_events.server import ranks_api
from sm64_events.tracking.views import entity_label, entity_labels
from test_ranks_api import make_client


class Catalog:
    def __init__(self):
        self.rows = [{"id": i, "name": f"Movement {i}"} for i in range(200)]
        self.reads = 0

    def segment_defs(self):
        self.reads += 1
        return self.rows


def test_one_snapshot_labels_a_whole_catalog_and_preserves_single_key_names():
    db = Catalog()
    keys = [f"segment:{i}" for i in range(200)] + ["segment:999", "segment:01",
                                                    "star:1:0", "star:1:6"]
    labels = entity_labels(db, iter(keys))
    assert db.reads == 1
    assert labels["segment:0"] == "Movement 0"
    assert labels["segment:199"] == "Movement 199"
    assert labels["segment:999"] == "segment 999"
    assert labels["segment:01"] == "segment 01"
    assert labels["star:1:0"] == "Bob-omb Battlefield — Big Bob-omb on the Summit"
    assert labels["star:1:6"] == "Bob-omb Battlefield — 100 Coins"
    assert labels == {key: entity_label(db, key) for key in keys}
    db.rows[1]["name"] = "Renamed movement"
    assert entity_labels(db, ["segment:1"])["segment:1"] == "Renamed movement"
    assert labels["segment:1"] == "Movement 1", "a completed batch stays a snapshot"


@pytest.mark.parametrize("keys", [[], ["star:1:0", "star:1:6"]])
def test_batches_without_segments_never_read_the_catalog(keys):
    db = Catalog()
    assert set(entity_labels(db, keys)) == set(keys)
    assert db.reads == 0


def _add_segment(service, name):
    ident = service.db.insert_segment_def(name, [], [], [], "2026-09-06T00:00:00Z")
    key = f"segment:{ident}"
    service.ranks.set_threshold(key, "Standard", "Mario", 10)
    service.ranks.set_threshold(key, "Standard", "Bronze", 20)
    return ident, key


@pytest.mark.parametrize("runner", [False, True])
def test_scope_and_runner_batch_labels_and_refresh_renames(tmp_path, monkeypatch, runner):
    client, service = make_client(tmp_path, bundled_library=False)
    with client:
        ident, included = _add_segment(service, "Included movement")
        excluded_id, excluded = _add_segment(service, "Excluded movement")
        service.db.set_state("rank_included", [included])
        # The real runner breakdown consumes the batch through its label
        # callback; its ratings are reference data, independent of the Sheet.
        rated = RatedSheet(RatedRunners(
            times={"Runner": {included: 1500}}, videos={},
            scores={"Runner": {included: 50}}), service.ranks)
        monkeypatch.setattr(RatingsCache, "current", lambda *args, **kwargs: rated)
        batches = []

        def record_batch(db, keys):
            keys = list(keys)
            batches.append(keys)
            return entity_labels(db, keys)

        monkeypatch.setattr(ranks_api, "entity_labels", record_batch)
        path = "/api/leaderboard/runner/Runner" if runner else "/api/marelo"
        response = client.get(path)
        assert response.status_code == 200
        rows = {row["key"]: row for row in response.json()["entities"]}
        assert rows[included]["label"] == "Included movement"
        assert rows[included]["excluded"] is False
        if runner:
            assert excluded not in rows
        else:
            assert rows[excluded]["label"] == "Excluded movement"
            assert rows[excluded]["excluded"] is True
        assert len(batches) == 1
        assert set(batches[0]) == set(rows)

        service.db.update_segment_def(ident, name="Renamed included")
        service.db.update_segment_def(excluded_id, name="Renamed excluded")
        batches.clear()
        updated = client.get(path)
        assert updated.status_code == 200
        renamed = {row["key"]: row for row in updated.json()["entities"]}
        assert renamed[included]["label"] == "Renamed included"
        if not runner:
            assert renamed[excluded]["label"] == "Renamed excluded"
        assert len(batches) == 1
        assert set(batches[0]) == set(renamed)
