# tests/test_api.py
import asyncio
import inspect
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from sm64_events.core.events import Event
from sm64_events.server.api import SegmentBody, SegmentPatch
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
        assert service.target == ("star", 8, 2)
        r = client.post("/api/pb", json={"attempt_id": aid, "timer_mode": "igt"})
        assert r.status_code == 200 and r.json()["frames"] == 343
        assert client.post(f"/api/attempts/{aid}/clear",
                           json={"reason": "accidental"}).status_code == 200
        assert db.attempts()[0].cleared is True
        assert client.post(f"/api/attempts/{aid}/restore").status_code == 200
        assert db.attempts()[0].cleared is False
        r = client.post("/api/session/new", json={})
        assert r.status_code == 200 and r.json()["session_id"] == 2


def test_target_with_explicit_null_strat_clears_it(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        seed(service)
        asyncio.run(service.set_strat(2, 2, "cannonless"))
        r = client.post("/api/target", json={
            "course_id": 2, "star_id": 2, "strat_tag": None})
        assert r.status_code == 200
        view = client.get("/api/session?clock=igt").json()
        assert view["last_strat_by_star"].get("2:2") is None


def test_target_without_a_strat_key_leaves_the_existing_one(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        seed(service)
        asyncio.run(service.set_strat(2, 2, "cannonless"))
        r = client.post("/api/target", json={"course_id": 2, "star_id": 2})
        assert r.status_code == 200
        view = client.get("/api/session?clock=igt").json()
        assert view["last_strat_by_star"].get("2:2") == "cannonless"


def test_target_segment_with_explicit_null_strat_clears_it(tmp_path):
    """Segment sibling of the star clearing test above. segment_id=1 (LBLJ,
    migration-seeded) carries no default_strat, so an explicit-null strat_tag
    can actually reach the 'no strategy' state -- a defaulted segment instead
    falls back to its default on a falsy strat_set (projection.py caveat 17),
    which is why this test deliberately avoids a defaulted segment."""
    client, service, db = make_client(tmp_path)
    with client:
        client.post("/api/target", json={"kind": "segment", "segment_id": 1,
                                         "strat_tag": "no bljs"})
        assert service.strat_by_segment[1] == "no bljs"
        r = client.post("/api/target", json={"kind": "segment",
                                              "segment_id": 1,
                                              "strat_tag": None})
        assert r.status_code == 200
        assert service.strat_by_segment.get(1) is None


def test_target_segment_without_a_strat_key_leaves_the_existing_one(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        client.post("/api/target", json={"kind": "segment", "segment_id": 1,
                                         "strat_tag": "no bljs"})
        r = client.post("/api/target", json={"kind": "segment",
                                              "segment_id": 1})
        assert r.status_code == 200
        assert service.strat_by_segment[1] == "no bljs"


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


def test_wipe_endpoint_roundtrip_and_guards(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        seed(service)
        r = client.post("/api/wipe", json={"kind": "star", "course_id": 2,
                                           "star_id": 2, "scope": "lifetime"})
        assert r.status_code == 200
        assert all((a.course_id, a.star_id) != (2, 2) for a in db.attempts())
        assert client.post("/api/wipe", json={"kind": "nonsense"}).status_code == 409
        assert client.post("/api/wipe", json={"kind": "segment"}).status_code == 409
        r = client.post("/api/wipe", json={"kind": "all", "scope": "session"})
        assert r.status_code == 200


def test_pb_undo_roundtrip_and_guards(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        seed(service)
        aid = db.attempts()[0].id
        # nothing saved yet: this attempt is not the current PB -> 409
        r = client.post("/api/pb/undo", json={"attempt_id": aid, "timer_mode": "igt"})
        assert r.status_code == 409
        client.post("/api/pb", json={"attempt_id": aid, "timer_mode": "igt"})
        r = client.post("/api/pb/undo", json={"attempt_id": aid, "timer_mode": "igt"})
        assert r.status_code == 200 and r.json()["restored_frames"] is None
        assert db.pbs() == []
        r = client.post("/api/pb/undo", json={"attempt_id": 999, "timer_mode": "igt"})
        assert r.status_code == 404


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
        # stored form is normalized: every selection carries a params dict,
        # and order is canonical (selection_order), not submission order
        assert client.get("/api/session").json()["stat_menu"] == [
            {"key": "avg_last_n", "params": {"n": 25}},
            {"key": "best", "params": {}}]


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
        assert service.target == ("star", 8, 2)         # unmoved
        assert service.strat_by_star[(2, 2)] == "owlless"
        # registered for the star's dropdown
        view = client.get("/api/session").json()
        assert "owlless" in view["strategies"]["2:2"]


def test_strat_endpoint_accepts_segment_kind(tmp_path):
    """Segment sibling of the star strat write — the practice card's strat
    picker is shared by both kinds, so the endpoint must be too."""
    client, service, db = make_client(tmp_path)
    with client:
        client.post("/api/target", json={"kind": "segment", "segment_id": 1})
        r = client.post("/api/strat", json={"kind": "segment", "segment_id": 1,
                                            "strat_tag": "no bljs"})
        assert r.status_code == 200
        assert service.target == ("segment", 1)          # unmoved
        assert service.strat_by_segment[1] == "no bljs"
        # registered, so the picker lists the pick even before any attempt
        sec = next(s for s in client.get("/api/session").json()["segments"]
                   if s["segment_id"] == 1)
        assert sec["last_strat"] == "no bljs"
        assert "no bljs" in sec["strategies"]
        # explicit null clears it (same journaled shape as stars)
        assert client.post("/api/strat", json={
            "kind": "segment", "segment_id": 1,
            "strat_tag": None}).status_code == 200
        assert service.strat_by_segment[1] is None
        assert client.post("/api/strat", json={
            "kind": "segment", "segment_id": 9999,
            "strat_tag": "x"}).status_code == 404


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
        # stored order is canonical (selection_order), not submission order
        stored = client.get("/api/session").json()["stat_menu"]
        assert stored == [{"key": "avg_last_n", "params": {"n": 10}},
                          {"key": "avg_last_n", "params": {"n": 50}},
                          {"key": "best", "params": {}}]


def test_statmenu_dedupes_param_variants_of_unparameterized_stats(tmp_path):
    # the user's live bug: success_rate stored once with {} and once with a
    # legacy custom failures set -> ONE chip; first occurrence wins.
    client, service, db = make_client(tmp_path)
    with client:
        menu = [{"key": "success_rate"},
                {"key": "success_rate",
                 "params": {"failures": ["reset", "hard_reset"]}},
                {"key": "avg_last_n", "params": {"n": 10}},
                {"key": "avg_last_n", "params": {"n": "10"}},   # str/int collapse
                {"key": "avg_last_n", "params": {"n": 25}}]
        assert client.put("/api/statmenu", json={"selections": menu}).status_code == 200
        # stored order is canonical (selection_order), not submission order
        stored = client.get("/api/session").json()["stat_menu"]
        assert [(s["key"], s["params"].get("n")) for s in stored] == [
            ("avg_last_n", 10), ("avg_last_n", 25), ("success_rate", None)]


def test_statmenu_stores_canonical_order(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        menu = [{"key": "success_rate"},
                {"key": "avg_last_n", "params": {"n": 50}},
                {"key": "avg_last_n", "params": {"n": 10}},
                {"key": "best"}]
        assert client.put("/api/statmenu", json={"selections": menu}).status_code == 200
        stored = client.get("/api/session").json()["stat_menu"]
        assert [(s["key"], s["params"].get("n")) for s in stored] == [
            ("avg_last_n", 10), ("avg_last_n", 50),
            ("best", None), ("success_rate", None)]


# -- segments CRUD + vocab + kind-aware target + markers ----------------------

def test_vocab_endpoint_shape(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        v = client.get("/api/segments/vocab").json()
        assert "triggers" in v and "levels" in v and "guards" in v
        assert "courses" in v and "stars" in v
        assert all("template" in t for t in v["triggers"] + v["guards"])


def test_get_segments_lists_seeds(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        r = client.get("/api/segments")
        assert r.status_code == 200
        assert any(d["name"] == "LBLJ" for d in r.json())


def test_post_invalid_segment_is_409(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        r = client.post("/api/segments", json={
            "name": "x", "start_triggers": [{"type": "nope"}],
            "end_triggers": [{"type": "spawned"}]})
        assert r.status_code == 409


def test_segment_crud_roundtrip(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        r = client.post("/api/segments", json={
            "name": "Custom", "start_triggers": [{"type": "spawned"}],
            "end_triggers": [{"type": "level_enter", "to": 6}]})
        assert r.status_code == 200
        sid = r.json()["id"]
        assert client.put(f"/api/segments/{sid}",
                          json={"enabled": False}).status_code == 200
        assert client.delete(f"/api/segments/{sid}").status_code == 200
        assert client.delete(f"/api/segments/{sid}").status_code == 404


def test_post_segment_persists_the_chosen_match_mode(tmp_path):
    """Regression (fix round 1, spec 2026-07-28-multi-step-segments):
    create_segment's db.insert_segment_def call omitted match_mode entirely,
    so a client's explicit choice was silently discarded and every segment
    landed on the insert-time default ("loose") no matter what was POSTed.
    Drives the real TrackerService.create_segment through the API — the
    round-trip test in test_storage.py exercises db.insert_segment_def
    directly, which is beneath this gap and cannot see it."""
    client, service, db = make_client(tmp_path)
    with client:
        r = client.post("/api/segments", json={
            "name": "Custom", "start_triggers": [{"type": "spawned"}],
            "end_triggers": [{"type": "level_enter", "to": 6}],
            "match_mode": "strict"})
        assert r.status_code == 200
        sid = r.json()["id"]
        assert next(s for s in db.segment_defs()
                    if s["id"] == sid)["match_mode"] == "strict"


def test_put_segment_persists_a_changed_match_mode(tmp_path):
    """Regression (fix round 1, spec 2026-07-28-multi-step-segments):
    update_segment's key allowlist omitted match_mode, so a PATCH changing it
    returned 200 but never reached db.update_segment_def — a write the API
    reported as successful that never happened. Drives the real
    TrackerService.update_segment through the API."""
    client, service, db = make_client(tmp_path)
    with client:
        r = client.post("/api/segments", json={
            "name": "Custom", "start_triggers": [{"type": "spawned"}],
            "end_triggers": [{"type": "level_enter", "to": 6}]})
        sid = r.json()["id"]
        assert next(s for s in db.segment_defs()
                    if s["id"] == sid)["match_mode"] == "loose"
        assert client.put(f"/api/segments/{sid}",
                          json={"match_mode": "strict"}).status_code == 200
        assert next(s for s in db.segment_defs()
                    if s["id"] == sid)["match_mode"] == "strict"


# Fields that legitimately MUST NOT persist through create_segment/
# update_segment, with a reason per entry. Empty today, deliberately: no
# field currently on SegmentBody/SegmentPatch is allowed to be accepted and
# silently dropped. Adding an entry here is a real design decision, not a
# way to quiet this test.
_WRITE_PATH_EXEMPT: set[str] = set()

# One realistic, non-None value per field name shared by SegmentBody and
# SegmentPatch (both models use the same names for the same concepts, so one
# table serves both write paths below). A field added to either model with
# no entry here fails this test immediately with a clear message, which is
# the point — it forces a decision (give it a sample value, or exempt it
# with a reason) rather than shipping silently.
_SEGMENT_FIELD_SAMPLES = {
    "name": "Renamed", "start_triggers": [{"type": "spawned"}],
    "end_triggers": [{"type": "level_enter", "to": 6}],
    "guards": [], "enabled": False, "waypoints": [],
    "category": "Cat", "match_mode": "strict",
}


def test_every_segment_model_field_reaches_its_write_path(tmp_path, monkeypatch):
    """Guard against the exact shape of bug fix round 1 found (spec
    2026-07-28-multi-step-segments): match_mode was accepted and validated by
    SegmentBody/SegmentPatch, then silently dropped because create_segment/
    update_segment built their db.insert_segment_def/update_segment_def calls
    from a hand-maintained key list that had drifted out of sync with the
    model — "remember to update the tuple" cannot fail a build. This reads
    the model's OWN field names (never a copy of them) and spies on the REAL
    db call each write path makes when fed every field, so it asserts on
    behaviour, not on a source-text scan of the allowlist — a scan would
    happily pass with a name present in the tuple but never actually
    forwarded to db.*, which is not what "reaches storage" means."""
    for field in set(SegmentBody.model_fields) | set(SegmentPatch.model_fields):
        assert field in _SEGMENT_FIELD_SAMPLES or field in _WRITE_PATH_EXEMPT, (
            f"new model field {field!r} needs a sample value in "
            "_SEGMENT_FIELD_SAMPLES or a reasoned entry in _WRITE_PATH_EXEMPT")

    client, service, db = make_client(tmp_path)
    with client:
        # -- create_segment -> db.insert_segment_def -------------------------
        insert_sig = inspect.signature(Database.insert_segment_def)
        real_insert = db.insert_segment_def
        captured_insert = {}

        def spy_insert(*args, **kwargs):
            # Deliberately NO apply_defaults(): that would backfill an
            # OMITTED kwarg with insert_segment_def's own schema default
            # (e.g. match_mode's "loose"), making the omission invisible —
            # exactly the failure mode that let this bug through once
            # already. .arguments un-defaulted holds only what
            # create_segment ACTUALLY passed.
            bound = insert_sig.bind(db, *args, **kwargs)
            captured_insert.update(bound.arguments)
            return real_insert(*args, **kwargs)
        monkeypatch.setattr(db, "insert_segment_def", spy_insert)

        body = {f: v for f, v in _SEGMENT_FIELD_SAMPLES.items()
                if f in SegmentBody.model_fields}
        asyncio.run(service.create_segment(body))
        for field in SegmentBody.model_fields:
            if field in _WRITE_PATH_EXEMPT:
                continue
            assert field in captured_insert, (
                f"{field!r} on SegmentBody never reaches db.insert_segment_def "
                "— create_segment accepts it and silently drops it")
        monkeypatch.setattr(db, "insert_segment_def", real_insert)

        # -- update_segment -> db.update_segment_def -------------------------
        sid = asyncio.run(service.create_segment(body))
        real_update = db.update_segment_def
        captured_update = {}

        def spy_update(def_id, **fields):
            captured_update.update(fields)
            return real_update(def_id, **fields)
        monkeypatch.setattr(db, "update_segment_def", spy_update)

        patch = {f: v for f, v in _SEGMENT_FIELD_SAMPLES.items()
                 if f in SegmentPatch.model_fields}
        asyncio.run(service.update_segment(sid, patch))
        for field in SegmentPatch.model_fields:
            if field in _WRITE_PATH_EXEMPT:
                continue
            assert field in captured_update, (
                f"{field!r} on SegmentPatch never reaches db.update_segment_def "
                "— update_segment accepts it and silently drops it")


def test_reset_segment_endpoint_restores_seeded_definition(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        seg = next(s for s in client.get("/api/segments").json()
                   if s["name"] == "LBLJ")
        assert client.put(f"/api/segments/{seg['id']}",
                          json={"name": "My LBLJ"}).status_code == 200
        assert next(s for s in db.segment_defs()
                    if s["id"] == seg["id"])["seed_dirty"] == 1
        r = client.post(f"/api/segments/{seg['id']}/reset")
        assert r.status_code == 200
        row = next(s for s in db.segment_defs() if s["id"] == seg["id"])
        assert row["name"] == "LBLJ" and row["seed_dirty"] == 0


def test_reset_segment_endpoint_404_on_user_created(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        r = client.post("/api/segments", json={
            "name": "Custom", "start_triggers": [{"type": "spawned"}],
            "end_triggers": [{"type": "level_enter", "to": 6}]})
        sid = r.json()["id"]
        assert client.post(f"/api/segments/{sid}/reset").status_code == 404


def test_reset_route_endpoint_404_on_user_created(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        r = client.post("/api/routes", json={"name": "R", "steps": []})
        rid = r.json()["id"]
        assert client.post(f"/api/routes/{rid}/reset").status_code == 404


def test_segments_list_stamps_the_derived_origin(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        rows = client.get("/api/segments").json()
        lblj = next(row for row in rows if row["name"] == "LBLJ")
        assert lblj["origin"]["key"] == "6:1"
        assert lblj["origin"]["label"] == "Lobby"
        assert lblj["origin"]["region"] == "6:1"
        assert lblj["origin"]["source"] == "derived"


def test_origin_override_wins_and_can_be_cleared(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        target = next(row for row in client.get("/api/segments").json()
                      if row["name"] == "LBLJ")
        assert client.post(f"/api/segments/{target['id']}/origin",
                           json={"origin": "6:2"}).status_code == 200
        after = next(row for row in client.get("/api/segments").json()
                     if row["id"] == target["id"])
        assert after["origin"]["key"] == "6:2"
        assert after["origin"]["label"] == "Upstairs"
        assert after["origin"]["source"] == "override"

        assert client.post(f"/api/segments/{target['id']}/origin",
                           json={"origin": None}).status_code == 200
        restored = next(row for row in client.get("/api/segments").json()
                        if row["id"] == target["id"])
        assert restored["origin"]["key"] == "6:1"
        assert restored["origin"]["source"] == "derived"


def test_origin_override_does_not_dirty_a_seeded_row(tmp_path):
    # The WHY behind the KV: a seeded movement must stay eligible for corpus
    # refreshes after the user fixes its label.
    client, service, db = make_client(tmp_path)
    with client:
        target = next(row for row in client.get("/api/segments").json()
                      if row["name"] == "LBLJ")
        client.post(f"/api/segments/{target['id']}/origin",
                    json={"origin": "6:2"})
        after = next(row for row in client.get("/api/segments").json()
                     if row["id"] == target["id"])
        assert not after["seed_dirty"]
        assert after["seed_key"]   # else this passes vacuously (review M7)


def test_origin_override_rejects_a_node_outside_the_taxonomy(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        rows = client.get("/api/segments").json()
        response = client.post(f"/api/segments/{rows[0]['id']}/origin",
                               json={"origin": "not-a-node"})
        assert response.status_code == 400


def test_origin_override_404s_for_an_unknown_segment(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        assert client.post("/api/segments/99999/origin",
                           json={"origin": "6:3"}).status_code == 404


def test_a_location_free_segment_stamps_as_anywhere(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        created = client.post("/api/segments", json={
            "name": "Anywhere start", "start_triggers": [{"type": "reset_game"}],
            "end_triggers": [{"type": "level_enter", "to": 6}]})
        assert created.status_code == 200
        row = next(row for row in client.get("/api/segments").json()
                   if row["id"] == created.json()["id"])
        assert row["origin"]["key"] is None
        assert row["origin"]["region"] is None
        assert row["origin"]["label"] == "Anywhere"


def test_segments_vocab_ships_the_origin_taxonomy(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        origins = client.get("/api/segments/vocab").json()["origins"]
        assert [group["key"] for group in origins][:2] == ["16", "6:1"]
        assert origins[-1]["key"] is None      # "Anywhere" last


def test_target_accepts_segment_kind(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        # LBLJ is seed id=1
        r = client.post("/api/target", json={"kind": "segment", "segment_id": 1})
        assert r.status_code == 200
        r = client.post("/api/target", json={"kind": "segment",
                                             "segment_id": 9999})
        assert r.status_code == 404


def test_target_ranks_endpoint_returns_200_with_a_dict(tmp_path):
    """The picker's lazy per-entity rank fetch — build_entity_ranks over the
    HTTP boundary. No ranks are loaded here, so the map is empty, but the
    shape (a dict, 200) is what the picker modal actually consumes."""
    client, service, db = make_client(tmp_path)
    with client:
        seed(service)
        r = client.get("/api/target/ranks")
        assert r.status_code == 200
        assert isinstance(r.json(), dict)


def test_target_ranks_endpoint_on_a_fresh_db_is_empty(tmp_path):
    """No attempts at all -> nothing to grade -> {}, not an error."""
    client, service, db = make_client(tmp_path)
    with client:
        r = client.get("/api/target/ranks")
        assert r.status_code == 200
        assert r.json() == {}


def test_target_strategies_endpoint_returns_200_with_the_entity(tmp_path):
    """The picker's step-3 fetch -- build_entity_strategies over the HTTP
    boundary, for a real entity."""
    client, service, db = make_client(tmp_path)
    with client:
        seed(service)
        r = client.get("/api/target/strategies?entity=star:2:2")
        assert r.status_code == 200
        body = r.json()
        assert body["entity"] == "star:2:2" and body["kind"] == "star"
        assert isinstance(body["strategies"], list)


def test_target_strategies_endpoint_404s_on_a_malformed_entity(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        r = client.get("/api/target/strategies?entity=not_a_real_kind:1:2")
        assert r.status_code == 404


def test_segment_body_extra_field_is_422(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        r = client.post("/api/segments", json={
            "name": "x", "start_triggers": [{"type": "spawned"}],
            "end_triggers": [{"type": "level_enter", "to": 6}],
            "typo_field": "oops"})
        assert r.status_code == 422


def test_segment_patch_extra_field_is_422(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        r = client.put("/api/segments/1", json={"enabled": False,
                                                "typo_field": "oops"})
        assert r.status_code == 422


def test_markers_put_with_segment_id_writes_seg_key(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        seed(service)
        r = client.put("/api/markers", json={
            "segment_id": 1, "strat_tag": "default",
            "markers": [{"frames": 120, "label": "bowser hit"}]})
        assert r.status_code == 200 and r.json()["ok"] is True
        state = db.get_state("timeline_markers", {})
        assert "seg:1:default" in state
        assert state["seg:1:default"] == [{"frames": 120, "label": "bowser hit"}]


def test_markers_put_both_identities_is_409(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        r = client.put("/api/markers", json={
            "segment_id": 1, "course_id": 2, "star_id": 2,
            "markers": []})
        assert r.status_code == 409


def test_star_target_missing_star_id_is_409(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        r = client.post("/api/target", json={"kind": "star", "course_id": 2})
        assert r.status_code == 409


def test_segments_503_when_db_none(tmp_path):
    broadcaster = Broadcaster()
    service = TrackerService(None, broadcaster)
    poller = Poller(OfflineMemory(), [], service)
    app = create_app(poller, broadcaster, service=service)
    with TestClient(app) as client:
        assert client.get("/api/segments").status_code == 503
        assert client.post("/api/segments", json={
            "name": "X", "start_triggers": [{"type": "spawned"}],
            "end_triggers": [{"type": "level_enter", "to": 6}]
        }).status_code == 503
        assert client.put("/api/segments/1", json={"enabled": False}).status_code == 503
        assert client.delete("/api/segments/1").status_code == 503
        # vocab is always 200 — no db dependency
        assert client.get("/api/segments/vocab").status_code == 200


# -- route CRUD + export/import endpoints (Task 9) ----------------------------

def _lblj(db):
    return next(d["id"] for d in db.segment_defs() if d["name"] == "LBLJ")


def test_route_crud_endpoints(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        lblj = _lblj(db)
        r = client.post("/api/routes", json={"name": "R", "steps": [
            {"need": 1, "candidates": [{"type": "segment", "segment_id": lblj}]}]})
        assert r.status_code == 200
        rid = r.json()["id"]
        assert any(x["id"] == rid for x in client.get("/api/routes").json())
        v = client.get(f"/api/routes/{rid}")
        assert v.status_code == 200
        assert v.json()["steps"][0]["broken"] is False
        assert client.put(f"/api/routes/{rid}",
                          json={"name": "R2"}).status_code == 200
        assert client.delete(f"/api/routes/{rid}").status_code == 200
        assert client.get(f"/api/routes/{rid}").status_code == 404


def test_create_route_bad_segment_is_404(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        r = client.post("/api/routes", json={"name": "R", "steps": [
            {"need": 1, "candidates": [{"type": "segment", "segment_id": 99999}]}]})
        assert r.status_code == 404


def test_create_route_invalid_is_409(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        r = client.post("/api/routes", json={"name": "", "steps": []})
        assert r.status_code == 409


def test_route_select_endpoint(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        lblj = _lblj(db)
        rid = client.post("/api/routes", json={"name": "R", "steps": [
            {"need": 1, "candidates": [{"type": "segment", "segment_id": lblj}]}]}).json()["id"]
        r = client.post("/api/route/select", json={"route_id": rid})
        assert r.status_code == 200 and r.json()["ok"] is True
        ev = db.events()[-1]
        assert ev.type == "route_selected"
        assert ev.payload == {"route_id": rid, "segment_ids": [lblj]}


def test_route_select_none_clears(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        rid = client.post("/api/routes", json={"name": "R", "steps": [
            {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]}).json()["id"]
        client.post("/api/route/select", json={"route_id": rid})
        r = client.post("/api/route/select", json={"route_id": None})
        assert r.status_code == 200
        ev = db.events()[-1]
        assert ev.payload == {"route_id": None, "segment_ids": []}


def test_route_select_unknown_id_is_404(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        r = client.post("/api/route/select", json={"route_id": 9999})
        assert r.status_code == 404


def test_route_export_import_endpoints(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        lblj = _lblj(db)
        rid = client.post("/api/routes", json={"name": "R", "steps": [
            {"need": 1, "candidates": [
                {"type": "segment", "segment_id": lblj}]}]}).json()["id"]
        exp = client.get(f"/api/routes/{rid}/export").json()
        assert exp["kind"] == "sm64-route"
        prev = client.post("/api/routes/import?dry_run=true",
                           json={"payload": exp})
        assert prev.status_code == 200 and prev.json()["reused"] == ["LBLJ"]
        created = client.post("/api/routes/import", json={"payload": exp})
        assert created.status_code == 200 and "id" in created.json()


# -- run lifecycle + state + history + settings (Task 8 Phase D) ---------------

def test_run_lifecycle_endpoints(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        lblj = _lblj(db)
        rid = client.post("/api/routes", json={"name": "R", "steps": [
            {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]}).json()["id"]
        assert client.post("/api/run/start", json={"route_id": rid}).status_code == 200
        assert client.get("/api/run").json()["active"] is None      # armed, not started
        assert client.post("/api/run/end").status_code == 200


def test_run_start_unknown_route_404(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        assert client.post("/api/run/start", json={"route_id": 9999}).status_code == 404


def test_run_settings_endpoints(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        assert client.get("/api/run/settings").json()["start_offset_ms"] == 1360
        assert client.put("/api/run/settings", json={"start_offset_ms": 2000}).status_code == 200
        assert client.get("/api/run/settings").json()["start_offset_ms"] == 2000
        assert client.put("/api/run/settings", json={"start_offset_ms": -1}).status_code == 409


def test_run_history_endpoint(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        assert client.get("/api/run/history").status_code == 200
        assert "runs" in client.get("/api/run/history").json()


# -- Task 8: RouteBody / RoutePatch accept start_condition (Phase F) -----------

def test_create_route_with_start_condition(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        lblj = _lblj(db)
        r = client.post("/api/routes", json={"name": "R",
            "start_condition": {"type": "reset_game"},
            "steps": [{"need": 1, "candidates": [{"type": "segment", "segment_id": lblj}]}]})
        assert r.status_code == 200
        rid = r.json()["id"]
        assert client.get(f"/api/routes/{rid}").json()["start_condition"] == {"type": "reset_game"}


# -- run pause/resume/reset endpoints (Phase E) --------------------------------

def test_run_pause_resume_reset_endpoints(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        rid = client.post("/api/routes", json={"name": "R", "steps": [
            {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]}).json()["id"]
        client.post("/api/run/start", json={"route_id": rid})
        assert client.post("/api/run/pause").status_code == 200
        assert client.post("/api/run/resume").status_code == 200
        assert client.post("/api/run/reset").status_code == 200


# -- Task 9: PUT/DELETE star time-filter endpoints -----------------------------

def test_time_filter_put_reflags_and_delete_reverts(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        seed(service)                                   # success at igt 343
        r = client.put("/api/stars/2/2/time-filter",
                       json={"min_frames": 400, "max_frames": None})
        assert r.status_code == 200
        assert db.attempts()[0].cleared is True
        assert db.attempts()[0].cleared_reason == "auto: below 13.33s min"
        assert client.delete("/api/stars/2/2/time-filter").status_code == 200
        assert db.attempts()[0].cleared is False


def test_time_filter_rejects_bad_bounds(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        r = client.put("/api/stars/2/2/time-filter",
                       json={"min_frames": 300, "max_frames": 200})
        assert r.status_code == 409                     # ValueError taxonomy
        r = client.put("/api/stars/2/2/time-filter",
                       json={"min_frames": -1, "max_frames": None})
        assert r.status_code == 422                     # pydantic ge=0


# -- Task 4: POST /api/attempts/{id}/strat endpoint -----------------------------

def test_attempt_strat_endpoint_reclassifies_and_404s_on_unknown(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        seed(service)
        aid = db.attempts()[0].id
        r = client.post(f"/api/attempts/{aid}/strat",
                        json={"strat_tag": "Slide Kick"})
        assert r.status_code == 200
        assert db.attempts()[0].strat_tag == "Slide Kick"
        # null is a first-class value: it unlabels the attempt
        assert client.post(f"/api/attempts/{aid}/strat",
                           json={"strat_tag": None}).status_code == 200
        assert db.attempts()[0].strat_tag is None
        assert client.post("/api/attempts/999/strat",
                           json={"strat_tag": "X"}).status_code == 404


def test_icon_endpoints_set_clear_and_validate(tmp_path):
    """Per-entity icon overrides (spec 2026-07-24-segment-icon-cells): the
    kind-dispatched write mirrors /api/strat, the stem is validated against
    the bundled set, and the session view carries the override map."""
    client, service, db = make_client(tmp_path)
    with client:
        listing = client.get("/api/icons")
        assert listing.status_code == 200
        names = listing.json()["icons"]
        assert "wf1" in names and "bitdw" in names
        assert client.post("/api/icon", json={
            "course_id": 2, "star_id": 4, "icon": "wf5"}).status_code == 200
        assert client.post("/api/icon", json={
            "kind": "segment", "segment_id": 1,
            "icon": "bitdw"}).status_code == 200
        view = client.get("/api/session").json()
        # the map is wholly owned by this feature, so whole-dict is safe
        assert view["icon_overrides"] == {"star:2:4": "wf5",
                                          "segment:1": "bitdw"}
        # null clears one entity's override
        assert client.post("/api/icon", json={
            "course_id": 2, "star_id": 4, "icon": None}).status_code == 200
        assert client.get("/api/session").json()["icon_overrides"] == {
            "segment:1": "bitdw"}
        # unknown stem -> 400 (path-injection guard), unknown segment -> 404
        assert client.post("/api/icon", json={
            "course_id": 2, "star_id": 4, "icon": "nope"}).status_code == 400
        assert client.post("/api/icon", json={
            "kind": "segment", "segment_id": 9999,
            "icon": "bitdw"}).status_code == 404


def test_icon_endpoint_degraded_503(tmp_path):
    broadcaster = Broadcaster()
    service = TrackerService(None, broadcaster)
    poller = Poller(OfflineMemory(), [], service)
    app = create_app(poller, broadcaster, service=service)
    with TestClient(app) as client:
        assert client.post("/api/icon", json={
            "course_id": 2, "star_id": 2, "icon": "wf1"}).status_code == 503


def test_segment_targets_carry_strat_and_rank(tmp_path):
    """The quick-select banner's segment cells show the active strat and a
    rank medal like star cells do — the view must carry both per target."""
    client, service, db = make_client(tmp_path)
    with client:
        client.post("/api/strat", json={"kind": "segment", "segment_id": 1,
                                        "strat_tag": "no bljs"})
        view = client.get("/api/session").json()
        target = next(s for s in view["segment_targets"]
                      if s["segment_id"] == 1)
        assert target["strat"] == "no bljs"
        assert "rank" in target   # None until a ladder exists for the strat


def test_segment_editor_save_body_roundtrips_a_get_row(tmp_path):
    """The segment editor builds its PUT from a GET /api/segments row via an
    ALLOWLIST of the fields it edits. Regression 2026-07-24: it used a
    DENYLIST (strip id/created_utc), so when migration v11 grew the rows
    (seed_key/seed_dirty) those leaked into the strict SegmentPatch
    (extra=forbid) and EVERY save of a seeded segment 422'd."""
    client, service, db = make_client(tmp_path)
    with client:
        rows = client.get("/api/segments").json()
        row = next(r for r in rows if r.get("seed_key"))
        assert "seed_dirty" in row     # the trap: GET rows carry db columns
        editable = {k: row[k] for k in ("name", "enabled", "start_triggers",
                                        "end_triggers", "guards")}
        r = client.put(f"/api/segments/{row['id']}", json=editable)
        assert r.status_code == 200
        # the raw row minus id/created_utc (the old denylist) must FAIL —
        # extra=forbid is the typo guard, the UI owns the allowlist
        stale = {k: v for k, v in row.items()
                 if k not in ("id", "created_utc")}
        assert client.put(f"/api/segments/{row['id']}",
                          json=stale).status_code == 422

def test_patching_a_route_category_to_null_clears_it(tmp_path):
    """`category: null` means "move this route out of its group" — a real
    value, not an omitted field. The patch used to drop every None, so the
    Library's Category action would have silently done nothing."""
    client, service, db = make_client(tmp_path)
    with client:
        rid = client.post("/api/routes", json={
            "name": "R", "steps": [], "category": "Main Categories"}).json()["id"]
        assert client.put(f"/api/routes/{rid}",
                          json={"category": None}).status_code == 200
        row = next(r for r in client.get("/api/routes").json() if r["id"] == rid)
        assert row["category"] is None
        assert row["name"] == "R"          # an omitted field stays untouched


def test_patching_a_segment_category_to_null_clears_it(tmp_path):
    """Star<->segment parity: the same action on the Library's other panel."""
    client, service, db = make_client(tmp_path)
    with client:
        sid = client.post("/api/segments", json={
            "name": "S", "start_triggers": [{"type": "spawned", "level": 16}],
            "end_triggers": [{"type": "level_enter", "to": 6}],
            "guards": [], "category": "Tricks"}).json()["id"]
        assert client.put(f"/api/segments/{sid}",
                          json={"category": None}).status_code == 200
        row = next(s for s in db.segment_defs() if s["id"] == sid)
        assert row["category"] is None
        assert row["name"] == "S"


def test_icon_upload_roundtrip_and_validation(tmp_path, monkeypatch):
    """Custom icon files (spec addendum 2026-07-24): raw-body upload into
    user_icons_dir, listed as user_icons, assignable as `user:<name>`
    overrides, served back by /api/icons/file/{name}."""
    from sm64_events.core import paths
    monkeypatch.setattr(paths, "data_root", lambda: tmp_path)
    client, service, db = make_client(tmp_path)
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 64
    with client:
        r = client.post("/api/icons/upload?name=My Face!.PNG", content=png)
        assert r.status_code == 200
        stem = r.json()["icon"]
        assert stem.startswith("user:") and stem.endswith(".png")
        assert stem in client.get("/api/icons").json()["user_icons"]
        served = client.get(f"/api/icons/file/{stem[len('user:'):]}")
        assert served.status_code == 200 and served.content == png
        # usable as an override like any bundled stem
        assert client.post("/api/icon", json={
            "kind": "segment", "segment_id": 1, "icon": stem}).status_code == 200
        assert client.get("/api/session").json()["icon_overrides"] == {
            "segment:1": stem}
        # validation: bad extension, empty name, traversal, unknown user stem
        assert client.post("/api/icons/upload?name=notes.txt",
                           content=b"x").status_code == 400
        assert client.post("/api/icons/upload?name=..png",
                           content=png).status_code == 400
        assert client.get("/api/icons/file/..%2F..%2Ftracker.db").status_code \
            in (400, 404)
        assert client.post("/api/icon", json={
            "kind": "segment", "segment_id": 1,
            "icon": "user:ghost.png"}).status_code == 400
        # size cap
        assert client.post("/api/icons/upload?name=big.png",
                           content=b"\x89" * (2_000_001)).status_code == 413


def test_course_icons_endpoint_maps_stems_to_real_filenames(tmp_path):
    # The extensions are mixed (.webp and .png), so the client must never
    # guess: it asks for the directory listing, exactly as /api/icons does for
    # star_icons. Dropping new art in the folder then needs no code change.
    client, service, db = make_client(tmp_path)
    with client:
        courses = client.get("/api/icons/courses").json()["courses"]
        assert courses["bob"].startswith("bob.")
        assert courses["rr"].startswith("rr.")
        # every value is a real file in the bundled directory
        from pathlib import Path
        import sm64_events
        asset_dir = (Path(sm64_events.__file__).parent / "ui" / "assets"
                     / "course_icons")
        for stem, filename in courses.items():
            assert (asset_dir / filename).exists(), stem


def test_course_icon_map_lists_images_only(tmp_path):
    # Windows writes Thumbs.db into any previewed folder — the staging copy of
    # this art has one — and listing it would invent a "Thumbs" course whose
    # portrait 404s. Probed both ways so the filter cannot silently stop
    # filtering: the real art still lists, the shell droppings do not.
    from sm64_events.server.api import _course_icon_map
    (tmp_path / "bob.webp").write_bytes(b"art")
    (tmp_path / "rr.png").write_bytes(b"art")
    (tmp_path / "Thumbs.db").write_bytes(b"not art")
    (tmp_path / "desktop.ini").write_text("not art")
    assert _course_icon_map(tmp_path) == {"bob": "bob.webp", "rr": "rr.png"}
    assert _course_icon_map(tmp_path / "missing") == {}


def test_course_icons_omit_the_four_courses_the_game_has_no_painting_for(tmp_path):
    # HMC, SSL, DDD and SL are not entered through a painting, so no portrait
    # exists. The picker falls back to star-1 art for them; this asserts we
    # aren't silently shipping a wrong file under those names.
    client, service, db = make_client(tmp_path)
    with client:
        courses = client.get("/api/icons/courses").json()["courses"]
        for stem in ("hmc", "ssl", "ddd", "sl"):
            assert stem not in courses, stem


def test_segment_targets_include_locationless_defs(tmp_path):
    """Armed visibility (spec addendum): every definition must be reachable
    by the banner's armed-segment union, so segment_targets includes defs
    whose start triggers carry no location (empty start_areas/levels)."""
    client, service, db = make_client(tmp_path)
    with client:
        r = client.post("/api/segments", json={
            "name": "Anywhere", "start_triggers": [{"type": "reset_game"}],
            "end_triggers": [{"type": "star_grabbed"}]})
        assert r.status_code == 200
        sid = r.json()["id"]
        target = next(s for s in client.get("/api/session").json()
                      ["segment_targets"] if s["segment_id"] == sid)
        assert target["start_areas"] == [] and target["start_levels"] == []
