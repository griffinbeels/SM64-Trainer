# tests/test_api.py
import asyncio
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from sm64_events.core.events import Event
from sm64_events.server.app import create_app
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.poller import Poller
from sm64_events.storage.db import Database
from sm64_events.tracking.service import TrackerService

T0 = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)


class OfflineMemory:
    attached = False
    def attach(self): return False
    def detach(self): pass


def make_client(tmp_path):
    db = Database(tmp_path / "t.db")
    broadcaster = Broadcaster()
    service = TrackerService(db, broadcaster)
    poller = Poller(OfflineMemory(), [], service)
    app = create_app(poller, broadcaster, service=service)
    return TestClient(app), service, db


def seed(service):
    async def go():
        await service.publish(Event(type="practice_reset", frame=1000,
                                    timestamp_utc=T0,
                                    payload={"igt_frames_before": 0}))
        await service.publish(Event(type="star_collected", frame=1350,
                                    timestamp_utc=T0,
                                    payload={"course_id": 2, "star_id": 2,
                                             "igt_frames": 343}))
    asyncio.run(go())


def test_session_view_roundtrip(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        seed(service)
        r = client.get("/api/session?clock=igt")
        assert r.status_code == 200
        body = r.json()
        assert body["stars"][0]["star_name"] == "Shoot into the Wild Blue"


def test_target_clear_restore_pb_session_endpoints(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        seed(service)
        aid = db.attempts()[0].id
        assert client.post("/api/target", json={
            "course_id": 8, "star_id": 2, "strat_tag": "carpetless"
        }).status_code == 200
        assert service.target == (8, 2)
        r = client.post("/api/pb", json={"attempt_id": aid, "timer_mode": "igt"})
        assert r.status_code == 200 and r.json()["frames"] == 343
        assert client.post(f"/api/attempts/{aid}/clear",
                           json={"reason": "accidental"}).status_code == 200
        assert db.attempts()[0].cleared is True
        assert client.post(f"/api/attempts/{aid}/restore").status_code == 200
        assert db.attempts()[0].cleared is False
        r = client.post("/api/session/new", json={})
        assert r.status_code == 200 and r.json()["session_id"] == 2


def test_pb_on_missing_attempt_is_404(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        r = client.post("/api/pb", json={"attempt_id": 999, "timer_mode": "igt"})
        assert r.status_code == 404


def test_pb_bad_mode_is_409(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        seed(service)
        aid = db.attempts()[0].id
        r = client.post("/api/pb", json={"attempt_id": aid, "timer_mode": "lap"})
        assert r.status_code == 409


def test_restore_unknown_attempt_is_404(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        assert client.post("/api/attempts/999/restore").status_code == 404


def test_stats_registry_and_statmenu(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        r = client.get("/api/stats/registry")
        assert any(s["key"] == "success_rate" for s in r.json())
        menu = [{"key": "best"}, {"key": "avg_last_n", "params": {"n": 25}}]
        assert client.put("/api/statmenu", json={"selections": menu}).status_code == 200
        # stored form is normalized: every selection carries a params dict
        assert client.get("/api/session").json()["stat_menu"] == [
            {"key": "best", "params": {}},
            {"key": "avg_last_n", "params": {"n": 25}}]


def test_links_endpoint(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        r = client.get("/api/links/2/2")
        assert r.json()["ukikipedia"].endswith("Shoot_into_the_Wild_Blue")


def test_health_reports_db_and_session(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        body = client.get("/health").json()
        assert body["db"] == "ok" and body["session_id"] == 1


def test_degraded_service_returns_503(tmp_path):
    broadcaster = Broadcaster()
    service = TrackerService(None, broadcaster)
    poller = Poller(OfflineMemory(), [], service)
    app = create_app(poller, broadcaster, service=service)
    with TestClient(app) as client:
        assert client.get("/api/session").status_code == 503
        assert client.post("/api/target",
                           json={"course_id": 2, "star_id": 2}).status_code == 503
        assert client.put("/api/markers", json={
            "course_id": 2, "star_id": 2, "strat_tag": None,
            "markers": []}).status_code == 503
        assert client.get("/health").json()["db"] == "error"


def test_api_absent_when_no_service(tmp_path):
    broadcaster = Broadcaster()
    poller = Poller(OfflineMemory(), [], broadcaster)
    app = create_app(poller, broadcaster)
    with TestClient(app) as client:
        assert client.get("/api/session").status_code == 404
        assert client.get("/health").json()["db"] == "absent"


def test_statmenu_rejects_shapeless_selections(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        r = client.put("/api/statmenu", json={"selections": [{"params": {}}]})
        assert r.status_code == 422   # key is required


def test_bad_stat_params_do_not_500_the_view(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        seed(service)
        menu = [{"key": "avg_last_n", "params": {"n": "abc"}}]
        assert client.put("/api/statmenu", json={"selections": menu}).status_code == 200
        r = client.get("/api/session")
        assert r.status_code == 200
        [sec] = r.json()["stars"]
        assert sec["stats"][0]["value"] is None


def test_replay_failure_degrades_to_broadcast_only(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    broadcaster = Broadcaster()
    service = TrackerService(db, broadcaster)
    async def boom():
        raise RuntimeError("corrupt journal")
    monkeypatch.setattr(service, "start", boom)
    poller = Poller(OfflineMemory(), [], service)
    app = create_app(poller, broadcaster, service=service)
    with TestClient(app) as client:   # startup must NOT raise
        assert client.get("/health").json()["db"] == "error"
        assert client.get("/api/session").status_code == 503


# -- scope param tests --------------------------------------------------------

def test_session_scope_param_lifetime_echoed(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        seed(service)
        r = client.get("/api/session?scope=lifetime")
        assert r.status_code == 200
        assert r.json()["scope"] == "lifetime"


def test_session_scope_param_invalid_returns_422(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        r = client.get("/api/session?scope=bogus")
        assert r.status_code == 422


# -- session continue/delete endpoint tests -----------------------------------

def test_session_continue_happy_path(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        seed(service)
        s1 = service.session_id  # = 1
        # open a new session so s1 is a past session
        asyncio.run(service.new_session())
        r = client.post("/api/session/continue", json={"session_id": s1})
        assert r.status_code == 200
        assert r.json()["session_id"] == s1


def test_session_continue_unknown_returns_404(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        r = client.post("/api/session/continue", json={"session_id": 999})
        assert r.status_code == 404


def test_session_delete_active_returns_409(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        seed(service)
        active = service.session_id
        r = client.delete(f"/api/session/{active}")
        assert r.status_code == 409


def test_session_delete_unknown_returns_404(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        r = client.delete("/api/session/999")
        assert r.status_code == 404


def test_session_delete_past_session_removes_from_sessions_list(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        seed(service)
        s1 = service.session_id  # = 1
        asyncio.run(service.new_session())  # now active = 2
        r = client.delete(f"/api/session/{s1}")
        assert r.status_code == 200
        assert r.json()["ok"] is True
        view = client.get("/api/session").json()
        ids = [s["id"] for s in view["sessions"]]
        assert s1 not in ids


# -- timeline markers ----------------------------------------------------------

def test_markers_roundtrip_sorted_by_frames(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        seed(service)
        r = client.put("/api/markers", json={
            "course_id": 2, "star_id": 2, "strat_tag": "cannonless",
            "markers": [{"frames": 600, "label": "pyramid warp"},
                        {"frames": 90, "label": "bobomb grab"}]})
        assert r.status_code == 200 and r.json()["ok"] is True
        sec = client.get("/api/session").json()["stars"][0]
        assert sec["markers_by_strat"]["cannonless"] == [
            {"frames": 90, "label": "bobomb grab"},
            {"frames": 600, "label": "pyramid warp"}]


def test_markers_null_strat_lands_in_empty_key(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        seed(service)
        client.put("/api/markers", json={
            "course_id": 2, "star_id": 2, "strat_tag": None,
            "markers": [{"frames": 90, "label": "bobomb grab"}]})
        sec = client.get("/api/session").json()["stars"][0]
        assert sec["markers_by_strat"][""] == [{"frames": 90, "label": "bobomb grab"}]


def test_markers_empty_list_clears(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        seed(service)
        client.put("/api/markers", json={
            "course_id": 2, "star_id": 2, "strat_tag": None,
            "markers": [{"frames": 90, "label": "x"}]})
        client.put("/api/markers", json={
            "course_id": 2, "star_id": 2, "strat_tag": None, "markers": []})
        sec = client.get("/api/session").json()["stars"][0]
        assert sec["markers_by_strat"][""] == []


def test_markers_validation_422s(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        for bad in ({"frames": -1, "label": "x"},
                    {"frames": 0, "label": ""},
                    {"frames": 0, "label": "   "},
                    {"frames": 0, "label": "y" * 61}):
            r = client.put("/api/markers", json={
                "course_id": 2, "star_id": 2, "strat_tag": None,
                "markers": [bad]})
            assert r.status_code == 422, bad
        too_many = [{"frames": i, "label": f"m{i}"} for i in range(31)]
        assert client.put("/api/markers", json={
            "course_id": 2, "star_id": 2, "strat_tag": None,
            "markers": too_many}).status_code == 422


def test_markers_label_is_trimmed(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        seed(service)
        client.put("/api/markers", json={
            "course_id": 2, "star_id": 2, "strat_tag": None,
            "markers": [{"frames": 90, "label": "  bobomb grab  "}]})
        sec = client.get("/api/session").json()["stars"][0]
        assert sec["markers_by_strat"][""][0]["label"] == "bobomb grab"


def test_markers_put_preserves_other_keys(tmp_path):
    # the RMW must merge into the dict — a regression to a blind set_state
    # would clobber every other star/strat's markers and still pass the
    # single-key tests.
    client, service, db = make_client(tmp_path)
    with client:
        seed(service)
        client.put("/api/markers", json={
            "course_id": 2, "star_id": 2, "strat_tag": "cannonless",
            "markers": [{"frames": 200, "label": "owl"}]})
        client.put("/api/markers", json={
            "course_id": 2, "star_id": 2, "strat_tag": None,
            "markers": [{"frames": 90, "label": "wall jump"}]})
        sec = client.get("/api/session").json()["stars"][0]
        assert sec["markers_by_strat"] == {
            "cannonless": [{"frames": 200, "label": "owl"}],
            "": [{"frames": 90, "label": "wall jump"}],
        }


def test_strat_endpoint_sets_without_moving_target(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        seed(service)                                   # target -> (2,2)
        client.post("/api/target", json={"course_id": 8, "star_id": 2,
                                         "strat_tag": "carpetless"})
        r = client.post("/api/strat", json={"course_id": 2, "star_id": 2,
                                            "strat_tag": "owlless"})
        assert r.status_code == 200
        assert service.target == (8, 2)                 # unmoved
        assert service.strat_by_star[(2, 2)] == "owlless"
        # registered for the star's dropdown
        view = client.get("/api/session").json()
        assert "owlless" in view["strategies"]["2:2"]


def test_strat_endpoint_degraded_503(tmp_path):
    broadcaster = Broadcaster()
    service = TrackerService(None, broadcaster)
    poller = Poller(OfflineMemory(), [], service)
    app = create_app(poller, broadcaster, service=service)
    with TestClient(app) as client:
        assert client.post("/api/strat", json={
            "course_id": 2, "star_id": 2, "strat_tag": "x"}).status_code == 503


def test_statmenu_put_dedupes_exact_selections(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        menu = [{"key": "best"}, {"key": "best"},
                {"key": "avg_last_n", "params": {"n": 10}},
                {"key": "avg_last_n", "params": {"n": 10}},
                {"key": "avg_last_n", "params": {"n": 50}}]
        assert client.put("/api/statmenu", json={"selections": menu}).status_code == 200
        stored = client.get("/api/session").json()["stat_menu"]
        assert stored == [{"key": "best", "params": {}},
                          {"key": "avg_last_n", "params": {"n": 10}},
                          {"key": "avg_last_n", "params": {"n": 50}}]
