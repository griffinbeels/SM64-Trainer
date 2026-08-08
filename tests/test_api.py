# tests/test_api.py
import asyncio
import inspect
import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from sm64_events.core.events import Event
from sm64_events.core.paths import bundled_defaults_seed
from sm64_events.server.api import SegmentBody, SegmentPatch
from sm64_events.server.app import create_app
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.poller import Poller
from sm64_events.storage.db import Database
from sm64_events.tracking.eventlabel import TRIGGER_JOURNAL_TYPES
from sm64_events.tracking.segments import TRIGGERS
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


def test_timeline_returns_labelled_recent_events_oldest_first(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        seed(service)  # practice_reset (id 1), then star_collected (id 2)
        r = client.get("/api/segments/timeline?limit=50&view=all")
        assert r.status_code == 200
        rows = r.json()["rows"]
        assert all(row["label"] for row in rows)
        # Ordered by journal id, oldest first (newest last) -- NOT by frame;
        # see test_timeline_orders_by_journal_id_not_frame below for why
        # frame can't be the sort key.
        assert [row["id"] for row in rows] == sorted(row["id"] for row in rows)


def test_timeline_limit_is_bounded(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        assert client.get(
            "/api/segments/timeline?limit=99999").status_code == 422


def test_timeline_journal_cache_extends_instead_of_rereading(tmp_path):
    """The journal cache (round 8 item 1, approved: "we definitely shouldn't
    be re-reading the journal from disk on every poll"): the first poll
    decodes the whole journal, every later one asks only for `id > cached
    max`. Asserted on the DOOR (which query the db was asked), never on a
    timing — a timing assertion flakes, and the cost lives entirely in which
    query runs. The second half asserts the rows still ARRIVE, so a cache
    that extends wrongly cannot pass by never returning the new row."""
    client, service, db = make_client(tmp_path)
    with client:
        seed(service)  # journal ids 2, 3 (session_started is id 1)
        asked = []
        whole_journal = service.db.events

        def recording_events(after_id=None):
            asked.append(after_id)
            return whole_journal(after_id=after_id)

        service.db.events = recording_events
        first = client.get(
            "/api/segments/timeline?limit=50&view=all").json()["rows"]
        assert asked == [None], "the first poll fetches the whole journal"
        cached_max = max(row.id for row in whole_journal())
        newest_shown = first[-1]["id"]

        async def go():
            await service.publish(Event(
                type="star_collected", frame=2000, timestamp_utc=T0,
                payload={"course_id": 2, "star_id": 3, "igt_frames": 500}))
        asyncio.run(go())
        second = client.get(
            "/api/segments/timeline?limit=50&view=all").json()["rows"]
        assert asked[1:] == [cached_max], (
            "a later poll asks only for the tail above the cached max id "
            f"(got {asked[1:]})")
        assert second[-1]["id"] > newest_shown, (
            "the tail-extended cache must still serve the new row")


def test_timeline_journal_cache_does_not_survive_a_db_swap(tmp_path):
    """A reattach after a db-less boot swaps `service.db` (attach_db), and
    another file's journal rows must not answer for the new one — same
    object-identity rule the label memo already follows."""
    client, service, db = make_client(tmp_path)
    with client:
        seed(service)
        primed = client.get(
            "/api/segments/timeline?limit=50&view=all").json()["rows"]
        assert primed, "the cache must hold rows for the swap to matter"
        service.db = Database(tmp_path / "second.db")
        swapped = client.get(
            "/api/segments/timeline?limit=50&view=all").json()["rows"]
        assert swapped == [], (
            "a fresh, empty journal must answer empty — rows here are the "
            "old db's cache outliving the object it belongs to")


def test_timeline_orders_by_journal_id_not_frame(tmp_path):
    """CORRECTION to the task-11 brief: its sketch asserted rows sorted by
    `frame`, but `frame` is the raw game-frame counter and is NOT
    chronological -- it drops toward 0 across every practice reset and
    session boundary (measured against the real journal, 2026-07-28: 469
    backward jumps). Journal `id` is the field that stays monotonic. Build
    two events where id-order and frame-order disagree (the second event is
    chronologically later -- higher id -- but carries a LOWER frame, as a
    reset would produce) and assert the endpoint follows id. This fails
    under `ORDER BY frame`, which would return frame 100 before frame 5000."""
    client, service, db = make_client(tmp_path)
    with client:
        async def go():
            await service.publish(Event(type="level_changed", frame=5000,
                                        timestamp_utc=T0,
                                        payload={"from": 1, "to": 9}))
            await service.publish(Event(type="level_changed", frame=100,
                                        timestamp_utc=T0,
                                        payload={"from": 9, "to": 15}))
        asyncio.run(go())
        r = client.get("/api/segments/timeline?limit=50")
        assert r.status_code == 200
        rows = r.json()["rows"]
        assert [row["id"] for row in rows] == sorted(row["id"] for row in rows)
        assert [row["frame"] for row in rows] == [5000, 100]


def test_timeline_default_view_hides_high_volume_bookkeeping_types(tmp_path):
    """The labelling-volume decision this task owns (see the endpoint's
    docstring + tracking/eventlabel.py's module docstring for the counts):
    of eventlabel.LABELLABLE_TYPES's 9 types, `practice_reset` alone is
    2,829 of 18,656 real events and names no place -- showing it by default
    would bury the level/star/warp/key rows a human can actually act on.
    Default `view=steps` excludes it; `view=all` still reaches it."""
    client, service, db = make_client(tmp_path)
    with client:
        seed(service)  # practice_reset, then star_collected
        default_rows = client.get(
            "/api/segments/timeline?limit=50").json()["rows"]
        assert [row["type"] for row in default_rows] == ["star_collected"]
        all_rows = client.get(
            "/api/segments/timeline?limit=50&view=all").json()["rows"]
        assert [row["type"] for row in all_rows] == \
            ["practice_reset", "star_collected"]


def test_timeline_default_view_includes_area_changed(tmp_path):
    """Measurement against the real seed corpus (src/sm64_events/data/
    defaults.seed.json): 4 of the 84 seeded definitions END on area_enter
    (BoB/BBH/Bowser 2 -> Basement/Upstairs, SL -> Basement) and 1 STARTS on
    it (BitS Entry) -- 5 definitions with NO OTHER route in/out, unlike
    `spawned`'s raw type (1 sole use, but only its kind="intro" subset --
    see test_timeline_default_view_includes_intro_spawn_but_not_ordinary_
    respawn below), the attempt_anchor pair (0 sole uses -- always an
    OR-alternative behind level_enter), and `game_reset` (0 sole uses).
    Hiding area_changed the same way those are hidden would make that class
    unrecordable through the default flow. Default `view=steps` must
    therefore include it despite its raw volume (1,678 of 18,656 events).
    See test_timeline_default_view_membership_matches_seed_corpus_sole_
    route_types below for the general rule this is one case of."""
    client, service, db = make_client(tmp_path)
    with client:
        async def go():
            await service.publish(Event(type="area_changed", frame=10,
                                        timestamp_utc=T0,
                                        payload={"level": 6, "from": 1,
                                                 "to": 3}))
        asyncio.run(go())
        default_rows = client.get(
            "/api/segments/timeline?limit=50").json()["rows"]
        assert [row["type"] for row in default_rows] == ["area_changed"]
        assert default_rows[0]["label"] == "Moved into the Basement"


def test_timeline_rejects_unknown_view(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        assert client.get(
            "/api/segments/timeline?view=bogus").status_code == 422


def test_timeline_default_view_includes_intro_spawn_but_not_ordinary_respawn(tmp_path):
    """Lakitu Skip's only start trigger is `{"type": "spawned", "level": 16}`
    (defaults.seed.json) -- sole-route for the raw `spawned` type, by the
    same criterion that earns area_changed its spot. But 1,136 of 1,164 real
    spawned events (2026-07-28) are ordinary respawns after a death or reset
    that no definition needs -- the corpus need is narrower than the type.
    detectors/spawn.py's payload `kind` distinguishes them ("intro" = edge
    out of the file-select cutscene, "spawn" = an ordinary respawn-in), and
    every kind="intro" spawn at level 16 (what Lakitu Skip's clause matches)
    is measured to be a fresh-file start, never an ordinary respawn. Default
    view therefore shows kind="intro" spawns only; `view=all` still reaches
    both -- proving the filter is narrower than raw-type membership, not just
    documenting it (see the comment above _TIMELINE_STEP_TYPES in api.py)."""
    client, service, db = make_client(tmp_path)
    with client:
        async def go():
            await service.publish(Event(type="spawned", frame=10,
                                        timestamp_utc=T0,
                                        payload={"level": 16, "kind": "spawn"}))
            await service.publish(Event(type="spawned", frame=20,
                                        timestamp_utc=T0,
                                        payload={"level": 16, "kind": "intro"}))
        asyncio.run(go())
        default_rows = client.get(
            "/api/segments/timeline?limit=50").json()["rows"]
        assert [row["label"] for row in default_rows] == \
            ["Started the file in Castle Grounds"]
        all_rows = client.get(
            "/api/segments/timeline?limit=50&view=all").json()["rows"]
        assert [row["label"] for row in all_rows] == \
            ["Spawned into Castle Grounds", "Started the file in Castle Grounds"]


def _sole_route_trigger_types(segments: list[dict]) -> tuple[frozenset, frozenset]:
    """Every trigger-clause TYPE that is some seeded definition's ONLY start
    (or end) route -- the definition has no OR-alternative clause that could
    record it instead. "Sole" means start_triggers (or end_triggers) has
    exactly one clause; a type that's always one of SEVERAL OR-alternatives
    for every definition it appears in (attempt_anchor's shape: always
    paired with a level_enter) is never sole, no matter how many definitions
    use it. Pure, derived fresh from data/defaults.seed.json every run --
    never a hard-coded list, which is exactly what went wrong in the
    reviewed revision (a hand-copied "1 def-use" that was actually 7)."""
    sole_start, sole_end = set(), set()
    for seg in segments:
        starts, ends = seg["start_triggers"], seg["end_triggers"]
        if len(starts) == 1:
            sole_start.add(starts[0]["type"])
        if len(ends) == 1:
            sole_end.add(ends[0]["type"])
    return frozenset(sole_start), frozenset(sole_end)


def test_timeline_default_view_membership_matches_seed_corpus_sole_route_types(tmp_path):
    """The default view's four MARGINAL types -- area_changed (area_enter),
    practice_reset/state_loaded (attempt_anchor), spawned, game_reset
    (reset_game) -- are included IFF the seed corpus says the trigger type is
    some definition's ONLY route in or out. The base four (level_changed/
    star_collected/warp_entered/key_grabbed) are the endpoint's foundation
    and are never in question here; this test only re-derives the marginal
    ones, straight from data/defaults.seed.json via _sole_route_trigger_types
    and TRIGGER_JOURNAL_TYPES (the same shared mapping test_eventlabel.py's
    completeness guard uses -- ONE DOOR, not a third hand-written copy).

    This fails in EITHER direction: if a future corpus edit makes an
    excluded type (attempt_anchor, game_reset) sole-route for a new
    definition and nobody promotes it into the default view, or if
    _TIMELINE_STEP_TYPES/_is_default_timeline_row is changed to include one
    of these four without the corpus backing it up -- exactly the defect
    task-11-rev-review found: a hand-copied, wrong def-use count that made
    the module comment's own justification incoherent."""
    seed = json.loads(bundled_defaults_seed().read_bytes().decode("utf-8"))
    sole_start, sole_end = _sole_route_trigger_types(seed["segments"])

    # trigger key -> a representative PAYLOAD that (a) satisfies label_event
    # (never labels None) and (b) for "spawned" specifically, lands in the
    # kind="intro" subset -- the dedicated test above proves kind="spawn" is
    # excluded even though the raw type clears the bar. The journal TYPE
    # itself is NOT repeated here -- it comes from the shared
    # TRIGGER_JOURNAL_TYPES (eventlabel.py), the same mapping test_
    # eventlabel.py's completeness guard reads, so there is exactly one
    # place in the whole test suite naming "attempt_anchor -> practice_reset/
    # state_loaded" etc.
    marginal_payloads = {
        "area_enter": {"from": 1, "to": 3},
        "attempt_anchor": {},
        "spawned": {"level": 16, "kind": "intro"},
        "reset_game": {},
    }
    assert set(marginal_payloads) <= set(TRIGGERS)  # no stale/renamed key

    client, service, db = make_client(tmp_path)
    with client:
        for trigger_key, payload in marginal_payloads.items():
            journal_type = sorted(TRIGGER_JOURNAL_TYPES[trigger_key])[0]
            is_sole_route = trigger_key in sole_start or trigger_key in sole_end

            async def go(jt=journal_type, p=payload):
                await service.publish(Event(type=jt, frame=1,
                                            timestamp_utc=T0, payload=p))
            asyncio.run(go())

            default_types = {row["type"] for row in client.get(
                "/api/segments/timeline?limit=500").json()["rows"]}
            assert (journal_type in default_types) == is_sole_route, \
                trigger_key


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


# -- backtest endpoint (Task 8, spec 2026-07-28-multi-step-segments) --------
# Contract note: this endpoint's error taxonomy differs from a domain
# ValueError -> 409, a "definition fails validate_definition" test belongs at
# 409, NOT 422 -- 422 is reserved for a body Pydantic itself rejects (wrong
# types, missing required fields), before the handler ever runs. See
# server/api.py's module docstring / _http.

def test_backtest_endpoint_accepts_an_unsaved_definition(tmp_path):
    # The whole point: you find out BEFORE you save.
    client, service, db = make_client(tmp_path)
    with client:
        r = client.post("/api/segments/backtest", json={
            "definition": {"name": "candidate", "match_mode": "loose",
                          "start_triggers": [{"type": "level_exit", "from": 23}],
                          "end_triggers": [{"type": "level_enter", "to": 19}],
                          "guards": []},
            "replaces": None})
        assert r.status_code == 200
        body = r.json()
        assert set(body) >= {"fires", "attempts", "unclosed",
                             "pb_before", "pb_after", "gained", "lost"}
        # a brand-new candidate has nothing to compare against
        assert body["pb_before"] is None and body["gained"] == 0


def test_backtest_endpoint_rejects_a_domain_invalid_candidate_with_409(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        r = client.post("/api/segments/backtest", json={
            "definition": {"name": "bad", "start_triggers": [{"type": "nope"}],
                          "end_triggers": [{"type": "spawned"}], "guards": []},
            "replaces": None})
        assert r.status_code == 409


def test_backtest_endpoint_422s_on_a_malformed_body(tmp_path):
    # start_triggers wrong TYPE (a string, not a list of clauses) -- Pydantic
    # rejects this before the handler runs, distinct from the 409 above.
    client, service, db = make_client(tmp_path)
    with client:
        r = client.post("/api/segments/backtest", json={
            "definition": {"name": "bad", "start_triggers": "nope",
                          "end_triggers": [], "guards": []},
            "replaces": None})
        assert r.status_code == 422


def test_backtest_endpoint_404s_on_an_unknown_replaces_id(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        r = client.post("/api/segments/backtest", json={
            "definition": {"name": "candidate", "match_mode": "loose",
                          "start_triggers": [{"type": "level_exit", "from": 23}],
                          "end_triggers": [{"type": "level_enter", "to": 19}],
                          "guards": []},
            "replaces": 999999})
        assert r.status_code == 404


def test_backtest_endpoint_counts_real_history(tmp_path):
    # Drives the real journal, not a fixture -- two DDD-exit-through-the-sub
    # walks the candidate's loose definition catches.
    client, service, db = make_client(tmp_path)
    with client:
        async def go():
            await service.publish(Event(type="level_changed", frame=100,
                                        timestamp_utc=T0,
                                        payload={"from": 23, "to": 6}))
            await service.publish(Event(type="level_changed", frame=400,
                                        timestamp_utc=T0,
                                        payload={"from": 6, "to": 19}))
        asyncio.run(go())
        r = client.post("/api/segments/backtest", json={
            "definition": {"name": "DDD exit -> BitFS", "match_mode": "loose",
                          "start_triggers": [{"type": "level_exit", "from": 23}],
                          "end_triggers": [{"type": "level_enter", "to": 19}],
                          "guards": []},
            "replaces": None})
        assert r.status_code == 200
        body = r.json()
        assert body["fires"] == 1
        assert body["attempts"][0]["rta_frames"] == 300


# --- POST /api/segments/lint (Task 16, spec 2026-07-28-multi-step-segments)
# -- author-time findings for a not-yet-saved definition, tracking/lint.py's
# four rules wired up behind an endpoint for the first time. Positive-control
# fixtures for unfireable/unrunnable_arm_position are the SAME ones
# tests/test_lint.py already uses (this file drives them through the API,
# tests/test_lint.py drives lint_definition directly -- a failure here that
# passes there means the WIRING is wrong, not the rule).

def test_lint_endpoint_returns_no_findings_for_a_clean_definition(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        r = client.post("/api/segments/lint", json={
            "definition": {"name": "clean", "match_mode": "loose",
                          "start_triggers": [{"type": "level_exit", "from": 24}],
                          "end_triggers": [{"type": "level_enter", "to": 8}],
                          "guards": []},
            "segment_id": None})
        assert r.status_code == 200
        assert r.json()["warnings"] == []


def test_lint_endpoint_flags_an_unfireable_definition(tmp_path):
    # Exiting Hazy Maze Cave (level 7) lands directly in the castle basement
    # in ONE level_changed -- same fixture as
    # tests/test_lint.py::test_flags_a_definition_whose_start_and_end_are_
    # the_same_event.
    client, service, db = make_client(tmp_path)
    with client:
        r = client.post("/api/segments/lint", json={
            "definition": {"name": "bad", "match_mode": "loose",
                          "start_triggers": [{"type": "level_exit", "from": 7}],
                          "end_triggers": [{"type": "level_enter", "to": 6}],
                          "guards": []},
            "segment_id": None})
        assert r.status_code == 200
        findings = r.json()["warnings"]
        assert any(f["rule"] == "unfireable" and f["severity"] == "error"
                   for f in findings), findings


def test_lint_endpoint_flags_an_unrunnable_arm_position(tmp_path):
    # Same fixture as tests/test_lint.py::test_flags_a_definition_that_can_
    # never_arm_anywhere_it_can_be_run_from -- reset in the castle lobby,
    # then "enter the castle" can never be completed.
    client, service, db = make_client(tmp_path)
    with client:
        r = client.post("/api/segments/lint", json={
            "definition": {"name": "bad", "match_mode": "loose",
                          "start_triggers": [{"type": "attempt_anchor",
                                              "level": 6, "area": 1}],
                          "end_triggers": [{"type": "level_enter", "to": 6}],
                          "guards": []},
            "segment_id": None})
        assert r.status_code == 200
        findings = r.json()["warnings"]
        assert any(f["rule"] == "unrunnable_arm_position"
                   and f["severity"] == "error" for f in findings), findings


def test_lint_endpoint_flags_a_duplicate_against_the_real_library(tmp_path):
    # THE TRAP (brief's own words): all_defs must be the REAL saved library,
    # never []. Passing [] would silently drop this rule with no symptom --
    # this test fails immediately if the endpoint ever does that (verified by
    # mutation: temporarily changing the handler's `service.segment_defs` to
    # `[]` turns this red and leaves every other lint test green).
    client, service, db = make_client(tmp_path)
    with client:
        original = client.post("/api/segments", json={
            "name": "Original", "match_mode": "loose",
            "start_triggers": [{"type": "level_exit", "from": 24}],
            "end_triggers": [{"type": "level_enter", "to": 8}]}).json()["id"]
        r = client.post("/api/segments/lint", json={
            "definition": {"name": "A near-duplicate", "match_mode": "loose",
                          "start_triggers": [{"type": "level_exit", "from": 24}],
                          "end_triggers": [{"type": "level_enter", "to": 8}],
                          "guards": []},
            "segment_id": None})
        assert r.status_code == 200
        findings = r.json()["warnings"]
        dup = [f for f in findings if f["rule"] == "duplicate"]
        assert dup and dup[0]["severity"] == "warning", findings
        assert str(original) in dup[0]["message"]


def test_lint_endpoint_excludes_the_definition_being_edited_from_its_own_duplicate_check(tmp_path):
    # The self-exclusion half of the same trap: editing an EXISTING segment
    # without changing its start/end/waypoints/guards must not report it as
    # a duplicate of its own on-disk row. lint_definition's duplicate rule
    # excludes by id (tracking/lint.py) -- `segment_id` is what supplies it.
    client, service, db = make_client(tmp_path)
    with client:
        sid = client.post("/api/segments", json={
            "name": "Original", "match_mode": "loose",
            "start_triggers": [{"type": "level_exit", "from": 24}],
            "end_triggers": [{"type": "level_enter", "to": 8}]}).json()["id"]
        r = client.post("/api/segments/lint", json={
            "definition": {"name": "Original", "match_mode": "loose",
                          "start_triggers": [{"type": "level_exit", "from": 24}],
                          "end_triggers": [{"type": "level_enter", "to": 8}],
                          "guards": []},
            "segment_id": sid})
        assert r.status_code == 200
        assert r.json()["warnings"] == []


def test_lint_endpoint_tolerates_a_domain_invalid_shape_without_409ing(tmp_path):
    # Deliberate deviation from backtest's contract: the editor calls this on
    # EVERY edit, including the in-progress states a form passes through
    # before it's complete (an unknown-to-Pydantic-but-well-typed clause is
    # not reachable from the vocab-driven UI, but an unrecognised trigger
    # TYPE is exactly what lint.py's own rules are written to tolerate --
    # see server/api.py's lint_segment docstring). No validate_definition
    # call here, so this never 409s the way POST /api/segments would.
    client, service, db = make_client(tmp_path)
    with client:
        r = client.post("/api/segments/lint", json={
            "definition": {"name": "in-progress", "match_mode": "loose",
                          "start_triggers": [{"type": "nope"}],
                          "end_triggers": [{"type": "spawned"}],
                          "guards": []},
            "segment_id": None})
        assert r.status_code == 200
        assert r.json()["warnings"] == []


def test_lint_endpoint_422s_on_a_malformed_body(tmp_path):
    # start_triggers wrong TYPE (a string, not a list of clauses) -- Pydantic
    # rejects this before the handler runs, same convention as backtest's own
    # 422 test.
    client, service, db = make_client(tmp_path)
    with client:
        r = client.post("/api/segments/lint", json={
            "definition": {"name": "bad", "start_triggers": "nope",
                          "end_triggers": [], "guards": []},
            "segment_id": None})
        assert r.status_code == 422


# --- GET /api/segments/synthesize (Task 13, spec 2026-07-28-multi-step-
# segments) -- two picked timeline-row ids -> the clause pair + name a new
# segment would be defined by. The picker only ever holds row IDS (never the
# raw event), so the endpoint re-reads the real journal by id rather than
# trusting anything the client says about the row's shape.
#
# Real ids are never hardcoded here: `make_client` journals a `session_started`
# event of its own before any test publishes anything (id 1 on a fresh db),
# and session_started isn't LABELLABLE at all -- so it never appears via
# GET /api/segments/timeline, but it DOES shift every id a hardcoded 1/2 test
# would have assumed. Reading the real ids back through the timeline endpoint
# (view=all, so practice_reset rows are included too) is robust to that and
# to any future bookkeeping event landing before the ones under test.

def _timeline_ids(client):
    rows = client.get("/api/segments/timeline?limit=50&view=all").json()["rows"]
    return [row["id"] for row in rows]


def test_synthesize_endpoint_builds_a_clause_pair_a_name_and_sentences(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        async def go():
            await service.publish(Event(type="level_changed", frame=100,
                                        timestamp_utc=T0,
                                        payload={"from": 23, "to": 6}))
            await service.publish(Event(type="level_changed", frame=500,
                                        timestamp_utc=T0,
                                        payload={"from": 6, "to": 19}))
        asyncio.run(go())
        start_id, end_id = _timeline_ids(client)
        r = client.get(
            f"/api/segments/synthesize?ids={start_id},{end_id}")
        assert r.status_code == 200
        body = r.json()
        assert body["start_clause"] == {"type": "level_exit", "from": 23}
        assert body["end_clause"] == {"type": "level_enter", "to": 19}
        assert body["start_sentence"] == "Exit Dire, Dire Docks"
        assert body["end_sentence"] == "Enter Bowser in the Fire Sea"
        assert body["name"] == "Dire, Dire Docks → Bowser in the Fire Sea"


def test_synthesize_endpoint_404s_on_an_unknown_event_id(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        r = client.get("/api/segments/synthesize?ids=999998,999999")
        assert r.status_code == 404


def test_synthesize_endpoint_422s_on_the_same_event_picked_twice(tmp_path):
    # segments.py's COROLLARY (a definition armed and closed on the identical
    # tick) reached one step earlier than it used to be: `ids` is DEDUPED
    # before it is counted, so picking one moment twice is not a pair at all.
    # This was a 409 "same moment for both start and end" while the endpoint
    # took a start_id/end_id pair.
    client, service, db = make_client(tmp_path)
    with client:
        async def go():
            await service.publish(Event(type="level_changed", frame=100,
                                        timestamp_utc=T0,
                                        payload={"from": 7, "to": 6}))
        asyncio.run(go())
        [only_id] = _timeline_ids(client)
        r = client.get(f"/api/segments/synthesize?ids={only_id},{only_id}")
        assert r.status_code == 422
        assert "at least two" in r.json()["detail"]


def test_synthesize_endpoint_409s_when_the_start_row_has_no_synthesis_rule(tmp_path):
    # practice_reset never synthesizes -- attempt_anchor's position lives in
    # live MatchContext, not the payload (tracking/synthesize.py's
    # _NOT_SYNTHESIZABLE).
    client, service, db = make_client(tmp_path)
    with client:
        async def go():
            await service.publish(Event(type="practice_reset", frame=10,
                                        timestamp_utc=T0,
                                        payload={"igt_frames_before": 0}))
            await service.publish(Event(type="level_changed", frame=500,
                                        timestamp_utc=T0,
                                        payload={"from": 6, "to": 19}))
        asyncio.run(go())
        start_id, end_id = _timeline_ids(client)
        r = client.get(
            f"/api/segments/synthesize?ids={start_id},{end_id}")
        assert r.status_code == 409
        assert "start" in r.json()["detail"]


def test_synthesize_endpoint_409s_when_the_end_row_has_no_synthesis_rule(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        async def go():
            await service.publish(Event(type="level_changed", frame=100,
                                        timestamp_utc=T0,
                                        payload={"from": 23, "to": 6}))
            await service.publish(Event(type="practice_reset", frame=500,
                                        timestamp_utc=T0,
                                        payload={"igt_frames_before": 0}))
        asyncio.run(go())
        start_id, end_id = _timeline_ids(client)
        r = client.get(
            f"/api/segments/synthesize?ids={start_id},{end_id}")
        assert r.status_code == 409
        assert "end" in r.json()["detail"]


def test_synthesize_endpoint_503s_in_degraded_mode(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        service.db = None
        r = client.get("/api/segments/synthesize?ids=1,2")
        assert r.status_code == 503


# --- N picked moments, not two (2026-08-05, the recorder) ----------------
# "I should be able to select any number of the events, in chronological
# order, to define the segment that I want to capture." The middles become
# waypoints; the two-moment case is byte-for-byte what it always was.

def test_synthesize_makes_every_middle_moment_a_waypoint_in_journal_order(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        async def go():
            for frame, payload in ((100, {"from": 23, "to": 6}),
                                   (200, {"from": 6, "to": 24}),
                                   (300, {"from": 24, "to": 6}),
                                   (400, {"from": 6, "to": 19})):
                await service.publish(Event(type="level_changed", frame=frame,
                                            timestamp_utc=T0, payload=payload))
        asyncio.run(go())
        ids = _timeline_ids(client)
        r = client.get(f"/api/segments/synthesize?ids={','.join(map(str, ids))}")
        assert r.status_code == 200
        body = r.json()
        assert body["start_clause"] == {"type": "level_exit", "from": 23}
        assert body["end_clause"] == {"type": "level_enter", "to": 19}
        # Both middles, in journal order, each as the clause for the role a
        # waypoint plays -- a place you REACH, which is the END role.
        assert [step["clause"] for step in body["picked"]] == [
            {"type": "level_enter", "to": 24}, {"type": "level_enter", "to": 6}]
        assert body["picked"][0]["sentence"] == "Enter Whomp's Fortress"


def test_synthesize_sorts_the_picked_ids_rather_than_trusting_click_order(tmp_path):
    # The list is drawn NEWEST FIRST, so clicking down it hands the ids over
    # backwards. Chronological order is a property the events already have,
    # and reading it off the clicks instead would author a definition whose
    # steps run backwards through a walk that only happened one way.
    client, service, db = make_client(tmp_path)
    with client:
        async def go():
            for frame, payload in ((100, {"from": 23, "to": 6}),
                                   (200, {"from": 6, "to": 24}),
                                   (300, {"from": 24, "to": 19})):
                await service.publish(Event(type="level_changed", frame=frame,
                                            timestamp_utc=T0, payload=payload))
        asyncio.run(go())
        ids = _timeline_ids(client)
        forwards = client.get(
            f"/api/segments/synthesize?ids={','.join(map(str, ids))}").json()
        backwards = client.get(
            f"/api/segments/synthesize?ids={','.join(map(str, reversed(ids)))}"
        ).json()
        assert forwards == backwards
        assert forwards["start_clause"] == {"type": "level_exit", "from": 23}


def test_synthesize_422s_on_fewer_than_two_moments(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        r = client.get("/api/segments/synthesize?ids=7")
        assert r.status_code == 422
        assert "at least two" in r.json()["detail"]


def test_synthesize_422s_on_ids_that_are_not_numbers(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        r = client.get("/api/segments/synthesize?ids=7,nope")
        assert r.status_code == 422


def test_synthesize_409s_when_a_MIDDLE_moment_carries_no_clause(tmp_path):
    # A definition cannot hold a step it cannot express, so a middle refuses
    # for the same reason either end does -- and says "step", not "end", or
    # the sentence points at the wrong row.
    client, service, db = make_client(tmp_path)
    with client:
        async def go():
            await service.publish(Event(type="level_changed", frame=100,
                                        timestamp_utc=T0,
                                        payload={"from": 23, "to": 6}))
            await service.publish(Event(type="practice_reset", frame=200,
                                        timestamp_utc=T0,
                                        payload={"igt_frames_before": 0}))
            await service.publish(Event(type="level_changed", frame=300,
                                        timestamp_utc=T0,
                                        payload={"from": 6, "to": 19}))
        asyncio.run(go())
        ids = _timeline_ids(client)
        r = client.get(f"/api/segments/synthesize?ids={','.join(map(str, ids))}")
        assert r.status_code == 409
        assert "step" in r.json()["detail"]


# --- the timeline's live tail + the in-game timer (2026-08-05) -----------

def test_timeline_rows_carry_the_in_game_timer_when_the_event_does(tmp_path):
    # "what was the timer in game" -- surfaced from the payload the detector
    # already stamped, never derived here. A type that carries none reports
    # null rather than a computed stand-in.
    client, service, db = make_client(tmp_path)
    with client:
        async def go():
            await service.publish(Event(type="moment_reached", frame=100,
                                        timestamp_utc=T0,
                                        payload={"kind": "door_open",
                                                 "level": 16, "ordinal": 1,
                                                 "igt": "0'06\"03",
                                                 "igt_frames": 181}))
            await service.publish(Event(type="level_changed", frame=200,
                                        timestamp_utc=T0,
                                        payload={"from": 16, "to": 6}))
        asyncio.run(go())
        rows = client.get(
            "/api/segments/timeline?limit=50&view=all").json()["rows"]
        by_type = {row["type"]: row for row in rows}
        assert by_type["moment_reached"]["igt_frames"] == 181
        assert by_type["level_changed"]["igt_frames"] is None
        # FRAMES only, never the payload's own pre-formatted string: the
        # browser renders it through fmtIgtShort like every other time on
        # screen, and shipping the string would put a second formatter's
        # output on the page beside it (they differ — the display form drops
        # an empty minutes field).
        assert "igt" not in by_type["moment_reached"]


def test_timeline_rows_carry_where_they_happened(tmp_path):
    # "we should segment each of the events by the course / area that the
    # event occurred in" (2026-08-05). Most rows do not say where they are, so
    # the place is a running position over the whole journal -- derived here
    # because the browser holds a windowed tail with no beginning to walk from.
    client, service, db = make_client(tmp_path)
    with client:
        async def go():
            # Standing in HMC, grab a star there, leave for the castle.
            await service.publish(Event(type="area_changed", frame=100,
                                        timestamp_utc=T0,
                                        payload={"level": 7, "from": None,
                                                 "to": 1}))
            await service.publish(Event(type="star_collected", frame=200,
                                        timestamp_utc=T0,
                                        payload={"course_id": 6, "star_id": 0,
                                                 "igt_frames": 900}))
            # The exit and its establishing area row land on ONE frame, exactly
            # as the real detectors emit them.
            await service.publish(Event(type="level_changed", frame=300,
                                        timestamp_utc=T0,
                                        payload={"from": 7, "to": 6}))
            await service.publish(Event(type="area_changed", frame=300,
                                        timestamp_utc=T0,
                                        payload={"level": 6, "from": 1,
                                                 "to": 3}))
            await service.publish(Event(type="level_changed", frame=400,
                                        timestamp_utc=T0,
                                        payload={"from": 6, "to": 22}))
        asyncio.run(go())
        rows = client.get(
            "/api/segments/timeline?limit=50&view=all").json()["rows"]
        by_label = {row["label"]: row for row in rows}
        star = by_label["Grabbed Swimming Beast in the Cavern in Hazy Maze Cave"]
        assert (star["place"], star["place_label"], star["place_level"]) \
            == ("7", "Hazy Maze Cave", 7)
        # THE ROW THAT SAYS YOU LEFT closes the card you were in, because that
        # is what its own sentence is about -- a row is filed under where its
        # FRAME BEGAN, not under the destination.
        assert by_label["Exited Hazy Maze Cave into Castle Inside"]["place"] == "7"
        # ...and so does the establishing area row that shares its frame, which
        # is the whole reason for the per-frame collapse: judged raw it would
        # open a one-frame card of its own between the two real places.
        assert by_label["Moved into the Basement"]["place"] == "7"
        # The NEXT frame is where the basement's own card begins.
        assert by_label["Exited Castle Inside into Lethal Lava Land"]["place"] \
            == "6:3"


def test_timeline_reads_area_changed_only_never_level_changed(tmp_path):
    # The obvious reading -- move the position on a level edge too -- puts a
    # one-frame "Castle Inside" card between the course you left and the
    # basement you are standing in, for a place nobody was ever in. Same rule
    # `walked_steps` and `SegmentEngine.feed` already use, and this is the
    # assertion that fails if someone "simplifies" it back.
    client, service, db = make_client(tmp_path)
    with client:
        async def go():
            await service.publish(Event(type="area_changed", frame=100,
                                        timestamp_utc=T0,
                                        payload={"level": 24, "from": None,
                                                 "to": 1}))
            await service.publish(Event(type="level_changed", frame=200,
                                        timestamp_utc=T0,
                                        payload={"from": 24, "to": 6}))
            await service.publish(Event(type="area_changed", frame=200,
                                        timestamp_utc=T0,
                                        payload={"level": 6, "from": 1,
                                                 "to": 3}))
            await service.publish(Event(type="warp_entered", frame=300,
                                        timestamp_utc=T0,
                                        payload={"level": 6, "to": 22}))
        asyncio.run(go())
        rows = client.get(
            "/api/segments/timeline?limit=50&view=all").json()["rows"]
        # Two places over the whole run: Whomp's Fortress, then the Basement.
        # A bare "6" anywhere would mean a level edge had moved the position.
        # The leading None is the establishing area row itself -- it is filed
        # under where its own frame began, and nothing preceded it.
        assert [row["place"] for row in rows] == [None, "24", "24", "6:3"]
        assert "6" not in [row["place"] for row in rows]


def test_timeline_place_is_null_before_the_first_area_row(tmp_path):
    # Position genuinely unknown is not a place to invent. In practice this is
    # the first frames of a fresh database only -- the walk covers the WHOLE
    # journal while the timeline shows its last 200 rows.
    client, service, db = make_client(tmp_path)
    with client:
        async def go():
            await service.publish(Event(type="game_reset", frame=10,
                                        timestamp_utc=T0, payload={}))
        asyncio.run(go())
        [row] = client.get(
            "/api/segments/timeline?limit=50&view=all").json()["rows"]
        assert row["place"] is None and row["place_label"] is None


def test_timeline_after_id_returns_only_what_happened_since(tmp_path):
    # The live tail. A broadcast event carries `seq`, never the journal `id`
    # these rows are picked by, so a live surface has to come back for the id
    # regardless -- asking for the tail costs one round trip instead of the
    # whole list, and keeps `label_event` server-side.
    client, service, db = make_client(tmp_path)
    with client:
        async def go(payload):
            await service.publish(Event(type="level_changed", frame=100,
                                        timestamp_utc=T0, payload=payload))
        asyncio.run(go({"from": 23, "to": 6}))
        first = _timeline_ids(client)[-1]
        asyncio.run(go({"from": 6, "to": 19}))
        tail = client.get(
            f"/api/segments/timeline?limit=50&view=all&after_id={first}"
        ).json()["rows"]
        assert [row["id"] for row in tail] == [first + 1]
        assert tail[0]["label"] == "Exited Castle Inside into Bowser in the Fire Sea"
        # Nothing new since the newest row is an EMPTY tail, not the last row
        # over again -- a surface that prepends what it gets back would
        # otherwise double every row it already holds.
        assert client.get(
            f"/api/segments/timeline?limit=50&view=all&after_id={first + 1}"
        ).json()["rows"] == []


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
    # A subsection's parent entity (task 0087). Must be a WELL-FORMED key --
    # validate_definition rejects anything else, so a placeholder string here
    # would fail the write for the wrong reason and read as this guard
    # working when it was not.
    "parent": "segment:7",
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


def test_segments_list_stamps_the_hundred_coin_engine_flag(tmp_path):
    """spec 2026-07-28-multi-step-segments: GET /api/segments must tell the
    target picker which rows are a 100-coin star's own engine (never
    pickable as a segment any more), without the client re-deriving the
    structural clause-search itself -- one door
    (tracking.segments.hundred_coin_entity), stamped once here."""
    client, service, db = make_client(tmp_path)
    with client:
        created = client.post("/api/segments", json={
            "name": "100c course 2", "match_mode": "strict",
            "start_triggers": [{"type": "level_enter", "to": 24},
                              {"type": "attempt_anchor", "level": 24}],
            "waypoints": [[{"type": "star_grabbed", "course": 2, "star": 6}]],
            "end_triggers": [{"type": "star_grabbed", "course": 2, "star": s}
                            for s in range(6)]})
        assert created.status_code == 200
        rows = client.get("/api/segments").json()
        hc_row = next(r for r in rows if r["id"] == created.json()["id"])
        lblj_row = next(r for r in rows if r["name"] == "LBLJ")
        assert hc_row["is_hundred_coin_engine"] is True
        assert lblj_row["is_hundred_coin_engine"] is False


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
        assert client.get("/api/segments/timeline").status_code == 503
        assert client.post("/api/segments", json={
            "name": "X", "start_triggers": [{"type": "spawned"}],
            "end_triggers": [{"type": "level_enter", "to": 6}]
        }).status_code == 503
        assert client.put("/api/segments/1", json={"enabled": False}).status_code == 503
        assert client.delete("/api/segments/1").status_code == 503
        assert client.post("/api/segments/backtest", json={
            "definition": {"name": "X", "start_triggers": [{"type": "spawned"}],
                          "end_triggers": [{"type": "level_enter", "to": 6}]},
            "replaces": None
        }).status_code == 503
        assert client.post("/api/segments/lint", json={
            "definition": {"name": "X", "start_triggers": [{"type": "spawned"}],
                          "end_triggers": [{"type": "level_enter", "to": 6}]},
            "segment_id": None
        }).status_code == 503
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


# -- split/merge endpoints (Task 18, spec 2026-07-28-multi-step-segments) ---
# tracking/segments.py::split_definition/merge_definitions are pure and
# already tested directly (tests/test_segments.py, Task 17); these drive the
# real HTTP surface -- the id lookup, the 404/409 error mapping (ValueError
# from the pure op is a domain refusal, same convention as every other
# segment endpoint: _http maps it to 409, never 422 -- 422 stays reserved for
# a body Pydantic itself rejects), and that both are NON-DESTRUCTIVE end to
# end (the original row(s) still read back byte-identical afterward).

def test_split_endpoint_creates_two_new_segments_and_keeps_the_original(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        r = client.post("/api/segments", json={
            "name": "WF -> SSL", "match_mode": "loose",
            "start_triggers": [{"type": "level_exit", "from": 24}],
            "end_triggers": [{"type": "level_enter", "to": 8}],
            "waypoints": [[{"type": "area_enter", "level": 6, "area": 3}]]})
        sid = r.json()["id"]
        before = next(s for s in db.segment_defs() if s["id"] == sid)

        r = client.post(f"/api/segments/{sid}/split", json={
            "mid": [{"type": "area_enter", "level": 6, "area": 3}],
            "first_name": "WF -> Basement", "second_name": "Basement -> SSL"})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        first_id, second_id = body["first_id"], body["second_id"]
        assert len({sid, first_id, second_id}) == 3   # three distinct rows

        rows = {s["id"]: s for s in db.segment_defs()}
        assert rows[sid] == before                     # original UNTOUCHED
        assert rows[first_id]["name"] == "WF -> Basement"
        assert rows[first_id]["start_triggers"] == before["start_triggers"]
        assert rows[first_id]["end_triggers"] == [
            {"type": "area_enter", "level": 6, "area": 3}]
        assert rows[first_id]["seed_key"] is None
        assert rows[second_id]["name"] == "Basement -> SSL"
        assert rows[second_id]["start_triggers"] == [
            {"type": "area_enter", "level": 6, "area": 3}]
        assert rows[second_id]["end_triggers"] == before["end_triggers"]
        assert rows[second_id]["seed_key"] is None


def test_split_endpoint_404s_on_an_unknown_segment_id(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        r = client.post("/api/segments/999999/split", json={
            "mid": [{"type": "level_enter", "to": 6}],
            "first_name": "a", "second_name": "b"})
        assert r.status_code == 404


def test_split_endpoint_409s_on_an_unfireable_half(tmp_path):
    # Same collision test_segments.py's pure-op test uses: exiting Hazy Maze
    # Cave (level 7) lands directly in the castle basement in ONE
    # level_changed, so a def arming there and closing on a plain
    # level_enter(to=6) mid-point would arm and close on the same event.
    client, service, db = make_client(tmp_path)
    with client:
        r = client.post("/api/segments", json={
            "name": "x", "match_mode": "loose",
            "start_triggers": [{"type": "level_exit", "from": 7}],
            "end_triggers": [{"type": "level_enter", "to": 8}]})
        sid = r.json()["id"]
        before = len(db.segment_defs())
        r = client.post(f"/api/segments/{sid}/split", json={
            "mid": [{"type": "level_enter", "to": 6}],
            "first_name": "first half", "second_name": "second half"})
        assert r.status_code == 409
        assert "unfireable" in r.json()["detail"]
        assert len(db.segment_defs()) == before   # nothing inserted on refusal


def test_merge_endpoint_creates_one_new_segment_and_keeps_both_inputs(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        first_id = client.post("/api/segments", json={
            "name": "WF -> Basement", "match_mode": "loose",
            "start_triggers": [{"type": "level_exit", "from": 24}],
            "end_triggers": [{"type": "area_enter", "level": 6, "area": 3}],
        }).json()["id"]
        second_id = client.post("/api/segments", json={
            "name": "Basement -> SSL", "match_mode": "loose",
            "start_triggers": [{"type": "area_enter", "level": 6, "area": 3}],
            "end_triggers": [{"type": "level_enter", "to": 8}],
        }).json()["id"]
        before_first = next(s for s in db.segment_defs() if s["id"] == first_id)
        before_second = next(s for s in db.segment_defs() if s["id"] == second_id)

        r = client.post("/api/segments/merge", json={
            "first_id": first_id, "second_id": second_id, "name": "WF -> SSL"})
        assert r.status_code == 200
        new_id = r.json()["id"]

        rows = {s["id"]: s for s in db.segment_defs()}
        assert rows[first_id] == before_first     # both inputs UNTOUCHED
        assert rows[second_id] == before_second
        merged = rows[new_id]
        assert merged["name"] == "WF -> SSL"
        assert merged["start_triggers"] == before_first["start_triggers"]
        assert merged["end_triggers"] == before_second["end_triggers"]
        assert merged["waypoints"] == [before_second["start_triggers"]]
        assert merged["seed_key"] is None


def test_merge_endpoint_404s_on_an_unknown_id(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        sid = client.post("/api/segments", json={
            "name": "a", "start_triggers": [{"type": "level_exit", "from": 24}],
            "end_triggers": [{"type": "area_enter", "level": 6, "area": 3}],
        }).json()["id"]
        r = client.post("/api/segments/merge", json={
            "first_id": sid, "second_id": 999999, "name": "nope"})
        assert r.status_code == 404


def test_merge_endpoint_409s_on_a_pair_that_does_not_meet(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        first_id = client.post("/api/segments", json={
            "name": "WF -> Basement", "match_mode": "loose",
            "start_triggers": [{"type": "level_exit", "from": 24}],
            "end_triggers": [{"type": "area_enter", "level": 6, "area": 3}],
        }).json()["id"]
        second_id = client.post("/api/segments", json={
            "name": "DDD -> BitFS", "match_mode": "loose",
            "start_triggers": [{"type": "area_enter", "level": 26}],
            "end_triggers": [{"type": "level_enter", "to": 19}],
        }).json()["id"]
        before = len(db.segment_defs())
        r = client.post("/api/segments/merge", json={
            "first_id": first_id, "second_id": second_id, "name": "nope"})
        assert r.status_code == 409
        assert "do not meet" in r.json()["detail"]
        assert len(db.segment_defs()) == before


# --- split/merge responses carry lint warnings (Task 16, spec 2026-07-28-
# multi-step-segments) -- Task 18's own save/create paths, covered here per
# the Task 16 dispatch: informational only (see server/api.py's own
# docstrings for why this doesn't refuse -- measured against the real corpus,
# gating on `unrunnable_arm_position` would have blocked ~12% of otherwise
# topologically-legal merges).

def test_split_endpoint_response_carries_lint_warnings_for_both_halves(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        sid = client.post("/api/segments", json={
            "name": "WF -> SSL", "match_mode": "loose",
            "start_triggers": [{"type": "level_exit", "from": 24}],
            "end_triggers": [{"type": "level_enter", "to": 8}],
            "waypoints": [[{"type": "area_enter", "level": 6, "area": 3}]],
        }).json()["id"]
        r = client.post(f"/api/segments/{sid}/split", json={
            "mid": [{"type": "area_enter", "level": 6, "area": 3}],
            "first_name": "WF -> Basement", "second_name": "Basement -> SSL"})
        assert r.status_code == 200
        # This exact split is clean (test_lint.py-style reasoning: neither
        # half's start collides with its own next required step, and neither
        # arms somewhere it can't run from) -- both lists are empty, not just
        # present.
        assert r.json()["warnings"] == {"first": [], "second": []}


def test_merge_endpoint_response_carries_lint_warnings(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        first_id = client.post("/api/segments", json={
            "name": "WF -> Basement", "match_mode": "loose",
            "start_triggers": [{"type": "level_exit", "from": 24}],
            "end_triggers": [{"type": "area_enter", "level": 6, "area": 3}],
        }).json()["id"]
        second_id = client.post("/api/segments", json={
            "name": "Basement -> SSL", "match_mode": "loose",
            "start_triggers": [{"type": "area_enter", "level": 6, "area": 3}],
            "end_triggers": [{"type": "level_enter", "to": 8}],
        }).json()["id"]
        r = client.post("/api/segments/merge", json={
            "first_id": first_id, "second_id": second_id, "name": "WF -> SSL"})
        assert r.status_code == 200
        assert r.json()["warnings"] == []


def test_timeline_counts_repeats_of_the_same_sentence(tmp_path):
    """His ask, 2026-08-06, against EIGHT consecutive rows reading "Open the
    Maze Door in Hazy Maze Cave": *"if we've already recorded that specific
    landmark, it should show as a duplicate… we simply see a counter rising"*.
    Naming the door correctly removed the ordinal that used to separate two
    rows, and the count is what puts it back.

    The key is the SENTENCE, not the landmark, which is why nothing extra was
    needed for arrivals (item 7 asked for the counter there too): rows that
    read identically are exactly the rows he cannot tell apart.
    """
    client, service, db = make_client(tmp_path)
    with client:
        async def go():
            for _ in range(3):
                await service.publish(Event(
                    type="moment_reached", frame=1000, timestamp_utc=T0,
                    payload={"kind": "door_open", "level": 7, "ordinal": 1,
                             "landmark": {"key": "7:1:door:1,2,3"}}))
            await service.publish(Event(
                type="moment_reached", frame=1000, timestamp_utc=T0,
                payload={"kind": "textbox", "level": 7, "ordinal": 1}))
        asyncio.run(go())
        rows = client.get(
            "/api/segments/timeline?limit=50").json()["rows"]
        doors = [r for r in rows if r["label"].startswith("Open")]
        assert [r["repeat"] for r in doors] == [1, 2, 3]
        assert [r["label"] for r in doors] == [
            "Open a door in Hazy Maze Cave",
            "Open a door in Hazy Maze Cave (2)",
            "Open a door in Hazy Maze Cave (3)"]
        other = [r for r in rows if r["label"].startswith("Trigger")]
        assert [r["repeat"] for r in other] == [1], "a different sentence counts alone"


def test_timeline_repeat_count_survives_the_live_tail(tmp_path):
    """The count is taken BEFORE the `after_id` skip. Without that the
    recorder's own live-tail fetch -- which is how every row after the first
    reaches the screen -- would restart it at 1 on every poll, so the surface
    the feature exists for would be the one place it never worked."""
    client, service, db = make_client(tmp_path)
    with client:
        async def go():
            for _ in range(4):
                await service.publish(Event(
                    type="moment_reached", frame=1000, timestamp_utc=T0,
                    payload={"kind": "door_open", "level": 7, "ordinal": 1}))
        asyncio.run(go())
        everything = client.get("/api/segments/timeline?limit=50").json()["rows"]
        cutoff = everything[1]["id"]
        tail = client.get(
            f"/api/segments/timeline?limit=50&after_id={cutoff}").json()["rows"]
        assert [r["repeat"] for r in tail] == [3, 4]


def test_timeline_labels_follow_a_rename_through_the_memo(tmp_path):
    """A rename applies BACKWARDS (the /api/landmark route's own contract),
    and since 2026-08-06 the timeline memoises labels per row id -- so this is
    the memo's one failure mode made into a test: fetch (populates the memo),
    rename, fetch again, and the OLD sentence coming back is the cache
    surviving its own invalidation rule."""
    client, service, db = make_client(tmp_path)
    with client:
        async def go():
            await service.publish(Event(
                type="moment_reached", frame=1000, timestamp_utc=T0,
                payload={"kind": "door_open", "level": 7, "ordinal": 1,
                         "landmark": {"key": "7:1:aa:1,2,3", "placed": True}}))
        asyncio.run(go())
        before = client.get("/api/segments/timeline?limit=50").json()["rows"]
        assert any(r["label"] == "Open a door in Hazy Maze Cave" for r in before)
        client.post("/api/landmark",
                    json={"key": "7:1:aa:1,2,3", "name": "Maze Door"})
        after = client.get("/api/segments/timeline?limit=50").json()["rows"]
        assert any(r["label"] == "Open the Maze Door in Hazy Maze Cave"
                   for r in after), (
            "the rename never reached the row -- the label memo is serving a "
            "sentence from before the catalogue changed")


def test_an_entrance_rows_rename_identity_is_the_entrance_itself(tmp_path):
    """Round 12 items 5+6, one cause: an entrance row's PAYLOAD landmark is
    whatever object Mario last engaged -- his three CCM painting entries each
    carried the lobby DOOR's key, so the pencil autofilled "CCM Door" on the
    entrance and committing renamed the door ("If I'm setting the name for a
    specific row, I would expect THAT row to change"). The row's rename
    identity is DERIVED from the row's own place + destination instead,
    which also covers every historical row, and renaming it must touch the
    entrance alone."""
    client, service, db = make_client(tmp_path)
    with client:
        door_key = "6:1:800ebc8c:-2303,0,-1074"

        async def go():
            await service.publish(Event(
                type="moment_reached", frame=1000, timestamp_utc=T0,
                payload={"kind": "door_open", "level": 6, "ordinal": 1,
                         "landmark": {"key": door_key, "placed": True,
                                      "nameable": True}}))
            # The touch 38 frames later still wears the door -- the exact
            # journal shape (ids 3908/3933/3943) this exists to survive.
            await service.publish(Event(
                type="warp_entered", frame=1038, timestamp_utc=T0,
                payload={"level": 6, "area": 1, "to": 5,
                         "landmark": {"key": door_key, "placed": True,
                                      "nameable": True}}))
        asyncio.run(go())
        client.post("/api/landmark", json={"key": door_key, "name": "CCM Door"})
        rows = client.get(
            "/api/segments/timeline?limit=50&view=all").json()["rows"]
        [entrance] = [r for r in rows if r["type"] == "warp_entered"]
        assert entrance["landmark"] == "entrance:6:1:5", (
            "the entrance row still offers the payload's foreign landmark -- "
            "renaming it edits the door he opened on the way in")
        assert entrance["landmark_nameable"] is True
        assert entrance["landmark_name"] is None, (
            "a new entrance must start unnamed -- 'CCM Door' autofilling here "
            "is item 5 verbatim")
        # Naming the ENTRANCE lands on the entrance: the row re-labels, and
        # the door's own name is untouched.
        client.post("/api/landmark",
                    json={"key": "entrance:6:1:5", "name": "CCM Painting"})
        rows = client.get(
            "/api/segments/timeline?limit=50&view=all").json()["rows"]
        [entrance] = [r for r in rows if r["type"] == "warp_entered"]
        assert entrance["label"] == "Touched CCM Painting in Castle Inside"
        names = client.get("/api/landmarks").json()["names"]
        assert names[door_key] == "CCM Door"


def test_renaming_one_half_of_a_named_pair_moves_the_whole_door(tmp_path):
    """Round 13 items 2+3: a star door is TWO objects, and his rule is the
    spec — "if I rename them to the same name, they should all collapse to
    the same landmark". Naming the second half like the first IS the merge;
    after it, a rename through EITHER key moves both, and a blank erases
    both. A same-named key in another level never moves."""
    client, service, db = make_client(tmp_path)
    with client:
        half_a = "6:2:800eb180:-281,3174,3772"   # his real 70-star-door keys
        half_b = "6:2:800eb180:-127,3174,3772"
        elsewhere = "7:1:800eb180:-281,3174,3772"
        client.post("/api/landmark", json={"key": half_a, "name": "70 Star Door"})
        client.post("/api/landmark", json={"key": elsewhere, "name": "70 Star Door"})
        # The merge gesture: the second half takes the same name.
        client.post("/api/landmark", json={"key": half_b, "name": "70 Star Door"})
        # One landmark now: renaming through half B moves half A too, and
        # the same-named door in another level stays put.
        names = client.post("/api/landmark", json={
            "key": half_b, "name": "Star Door (70)"}).json()["names"]
        assert names[half_a] == "Star Door (70)"
        assert names[half_b] == "Star Door (70)"
        assert names[elsewhere] == "70 Star Door"
        # A blank erases the whole group, nothing else.
        names = client.post("/api/landmark",
                            json={"key": half_a, "name": ""}).json()["names"]
        assert half_a not in names and half_b not in names
        assert names[elsewhere] == "70 Star Door"
