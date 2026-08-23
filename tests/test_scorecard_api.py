"""The scorecard router, over HTTP -- the one goal KV and the resolved card.

`ranks/scorecard.py`'s own build logic (row shape, folding, Sigma math) is
`tests/test_scorecard.py`'s job; this file is only what the router adds on
top: reading/writing the KV, assembling `you`/`goal`/`fold` from the real
db + standards, and the coverage/pending flags the UI reads.
"""
from import_fixture import make_client
from sm64_events.ranks.classify import display_cs


def test_goal_round_trip(tmp_path):
    with make_client(tmp_path) as (client, _db, _svc):
        response = client.put("/api/scorecard/goal",
                              json={"kind": "division", "tier": "Gold",
                                    "division": "I"})
        assert response.status_code == 200

        card = client.get("/api/scorecard").json()
        assert card["goal"] == {"kind": "division", "tier": "Gold", "division": "I"}
        assert len(card["rows"]) == 16                     # 15 courses + Secret
        assert any(tile["goal_cs"] for row in card["rows"] for tile in row["tiles"])
        assert card["goal_coverage"]["tiles"] == sum(
            len(row["tiles"]) for row in card["rows"])
        assert card["goal_coverage"]["covered"] > 0
        assert "goal_pending" not in card


def test_iron_division_is_refused(tmp_path):
    with make_client(tmp_path) as (client, _db, _svc):
        response = client.put("/api/scorecard/goal",
                              json={"kind": "division", "tier": "Iron",
                                    "division": "I"})
        assert response.status_code == 422


def test_an_unknown_division_is_refused(tmp_path):
    with make_client(tmp_path) as (client, _db, _svc):
        response = client.put("/api/scorecard/goal",
                              json={"kind": "division", "tier": "Gold",
                                    "division": "VI"})
        assert response.status_code == 422


def test_an_unknown_goal_kind_is_refused(tmp_path):
    with make_client(tmp_path) as (client, _db, _svc):
        response = client.put("/api/scorecard/goal", json={"kind": "sponsor"})
        assert response.status_code == 422


def test_null_clears_the_goal(tmp_path):
    with make_client(tmp_path) as (client, _db, _svc):
        client.put("/api/scorecard/goal",
                   json={"kind": "division", "tier": "Gold", "division": "I"})
        response = client.put("/api/scorecard/goal", json=None)
        assert response.status_code == 200
        assert client.get("/api/scorecard").json()["goal"] is None


def test_no_goal_serves_your_times_uncolored(tmp_path):
    """A saved PB appears as you_cs with goal_cs None everywhere -- no goal
    is set, so nothing on the card has anything to grade against."""
    with make_client(tmp_path) as (client, db, _svc):
        client.post("/api/import/manual", json={
            "entity_key": "star:1:0", "strat_tag": "Standard", "time_cs": 886})

        card = client.get("/api/scorecard").json()
        assert card["goal"] is None
        row = next(r for r in card["rows"] if r["course_id"] == 1)
        tile = next(t for t in row["tiles"] if t["key"] == "star:1:0")
        pb_row = db.current_pb(1, 0, "igt")
        assert tile["you_cs"] == display_cs(pb_row["frames"])
        assert tile["goal_cs"] is None
        assert tile["delta_cs"] is None


def test_a_runner_goal_is_accepted_but_pending_until_task_6(tmp_path):
    with make_client(tmp_path) as (client, _db, _svc):
        response = client.put("/api/scorecard/goal",
                              json={"kind": "runner", "runner": "Suigi"})
        assert response.status_code == 200

        card = client.get("/api/scorecard").json()
        assert card["goal"] == {"kind": "runner", "runner": "Suigi"}
        assert card["goal_pending"] is True
        assert not any(tile["goal_cs"] for row in card["rows"] for tile in row["tiles"])
        assert card["goal_coverage"]["covered"] == 0


def test_a_runner_goal_needs_a_name(tmp_path):
    with make_client(tmp_path) as (client, _db, _svc):
        response = client.put("/api/scorecard/goal", json={"kind": "runner"})
        assert response.status_code == 422


def test_your_100c_pbs_variant_folds_its_exit_star(tmp_path):
    """A course whose 100c PB was saved under a labelled exit-star variant
    folds that star out of both sums -- the same star the variant's own
    label names, read off `ranks.variant_of`."""
    with make_client(tmp_path) as (client, _db, _svc):
        client.post("/api/import/manual", json={
            "entity_key": "star:1:6", "strat_tag": "100c + Reds · Standard",
            "time_cs": 12000})

        card = client.get("/api/scorecard").json()
        row = next(r for r in card["rows"] if r["course_id"] == 1)
        folded = {tile["key"]: tile["folded"] for tile in row["tiles"]}
        assert folded["star:1:3"] is True
        assert folded["star:1:0"] is False


def test_scorecard_serves_broadcast_only_with_an_empty_goal_map(tmp_path):
    """`service.ranks` is None on a broadcast-only instance -- the card must
    still answer, just with nothing gradeable."""
    from sm64_events.server.app import create_app
    from sm64_events.server.broadcaster import Broadcaster
    from sm64_events.server.poller import Poller
    from sm64_events.storage.db import Database
    from sm64_events.tracking.service import TrackerService
    from fastapi.testclient import TestClient
    from import_fixture import OfflineMemory

    db = Database(tmp_path / "t.db")
    broadcaster = Broadcaster()
    service = TrackerService(db, broadcaster)          # ranks=None
    poller = Poller(OfflineMemory(), [], service)
    app = create_app(poller, broadcaster, service=service,
                     adoptions_path=tmp_path / "library_adoptions.json",
                     mode_path=tmp_path / "tracker_mode.json")
    with TestClient(app) as client:
        response = client.get("/api/scorecard")
        assert response.status_code == 200
        card = response.json()
        assert card["goal_coverage"]["covered"] == 0
