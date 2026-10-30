"""Library discovery and target-addressed saves through the app's ghost router."""
import asyncio
import json
import threading
from copy import deepcopy
from types import SimpleNamespace

import pytest

from sm64_events.community.config import CommunityError
from sm64_events.ghost.community_library import decorate_page, portable_filter
from sm64_events.ghost.session import GhostSession
from sm64_events.ghost.sharing_targets import describe
from tests.test_ghost_api import session_client
from tests.test_ghost_rivals import TARGET, controller
from tests.test_ghost_saved_rivals import OTHER, live_state, other_record

UID = "11111111-1111-4111-8111-111111111111"


class Community:
    refused_assets = staticmethod(frozenset)

    def __init__(self, row):
        self.state = {"uid": UID, "enabled": True, "status": "active"}
        self.account = SimpleNamespace(snapshot=lambda: dict(self.state))
        self.row, self.calls, self.error = row, [], None

    async def library_summary(self, cursor, **filters):
        self.calls.append(("summary", cursor, filters))
        if self.error:
            raise self.error
        row = {key: self.row[key] for key in ("target", "clock", "game_version", "strategy")}
        return {"generation": "g1", "entries": [{**row, "recordings": 3, "players": 2,
                "best_frames": self.row["frames"]}], "next_cursor": None, "complete": True}

    async def library_recordings(self, *args):
        self.calls.append(("recordings", args))
        return {"generation": "g1", "recordings": [dict(self.row)], "next_cursor": None, "complete": True}

    async def recording_metadata(self, identifier):
        self.calls.append(("metadata", identifier))
        if self.error:
            raise self.error
        return dict(self.row)


@pytest.fixture
def library(tmp_path, monkeypatch):
    rivals, tracker, native, population, _ = controller(tmp_path, monkeypatch)
    row = other_record(population)
    row["target"] = describe("star", OTHER["entity_key"], OTHER["strat"], ())
    community = Community(row)
    session = GhostSession.__new__(GhostSession)
    session._tracker, session._rivals, session._community = tracker, rivals, community
    session._lock, session._lifecycle_lock = asyncio.Lock(), threading.RLock()
    session._closing, session._target = False, dict(TARGET)
    session.refreshes = []

    async def refresh(**kwargs):
        session.refreshes.append(kwargs)

    session.refresh = refresh
    with session_client(session) as client:
        yield client, session, row, native, population
    tracker.db.close()


def test_actual_router_keeps_discovery_separate_and_passes_region_target_filters(library):
    client, session, row, native, _ = library
    before = live_state(session._rivals, native)
    response = client.get("/api/library/community", params={"entity": "star:2:2", "rom": "jp"})
    assert response.status_code == 200
    page = response.json()
    assert page["available"] and page["complete"]
    assert page["entries"][0]["local_target"] == OTHER
    assert page["entries"][0]["entity_key"] == "star:2:2"
    assert page["entries"][0]["can_add"]
    assert session._community.calls[0] == ("summary", None, {
        "target": describe("star", "2-2", None, ()), "game_version": "jp"})
    response = client.get("/api/library/community/recordings", params={
        "target": json.dumps(row["target"]), "clock": "igt", "rom": "us",
        "strategy": "Standard"})
    assert response.status_code == 200 and response.json()["recordings"][0]["id"] == row["id"]
    assert live_state(session._rivals, native) == before
    assert session.refreshes == []


def test_invalid_cloud_cursor_requests_a_fresh_sequence(library):
    client, session, _, _, _ = library
    session._community.error = CommunityError("invalid_cursor", "Page expired.", status=400)
    response = client.get("/api/library/community", params={"cursor": "old-signed-page"})
    assert response.status_code == 409 and "Reload results" in response.json()["detail"]


def test_save_remove_for_b_while_a_is_active_never_cycles_practice(library):
    client, session, row, native, _ = library
    before = live_state(session._rivals, native)
    # The Library names no scope: a saved rival joins the star's one lineup.
    command = {"target": OTHER, "action": "add", "id": row["id"], "version": "us"}
    response = client.post("/api/ghost/rivals/saved", json=command)
    assert response.status_code == 200 and response.json()["state"] == "added"
    assert session._community.calls == [("metadata", row["id"])]
    assert live_state(session._rivals, native) == before and session.refreshes == []
    restored = client.get("/api/library/community/recordings", params={
        "target": json.dumps(row["target"]), "clock": "igt", "rom": "us", "strategy": "Standard"})
    assert restored.json()["recordings"][0]["saved_slot"] == response.json()["slot"]
    reads_before_remove = list(session._community.calls)
    command["action"] = "remove"
    assert client.post("/api/ghost/rivals/saved", json=command).json()["state"] == "removed"
    assert session._community.calls == reads_before_remove and live_state(session._rivals, native) == before


@pytest.mark.parametrize("identity", ["local-owner", UID])
def test_own_recordings_stay_visible_but_forged_add_and_replace_cannot_write(library, identity):
    client, session, row, native, _ = library
    session._community.state.update(authenticated=True, name="Same name")
    session._rivals.community.sync = session._community
    session._tracker.db.set_state("ghost_player_id", "local-owner")
    row.update(player_id=identity, player_name="Same name")
    key = session._rivals._key(OTHER)
    session._rivals.pins[key] = {"3": row["id"]}
    session._rivals._store()
    before = live_state(session._rivals, native), session._tracker.db.get_state("ghost_rivals_v1", None)
    response = client.get("/api/library/community/recordings", params={
        "target": json.dumps(row["target"]), "clock": "igt", "rom": "us", "strategy": "Standard"})
    own = response.json()["recordings"][0]
    assert own["id"] == row["id"] and own["is_own"] and own["saved_slot"] == 3
    assert not own["can_add"] and "PB ghost is included" in own["unavailable_reason"]
    for slot in (None, 0):
        response = client.post("/api/ghost/rivals/saved", json={
            "target": OTHER, "action": "add", "id": row["id"], "slot": slot})
        assert response.status_code == 422 and "PB ghost is included" in response.json()["detail"]
        assert (live_state(session._rivals, native), session._tracker.db.get_state("ghost_rivals_v1", None)) == before
    response = client.post("/api/ghost/rivals/saved", json={"target": OTHER, "action": "remove", "id": row["id"]})
    assert response.status_code == 200 and response.json()["state"] == "removed"
    assert response.json()["pins"] == {} and live_state(session._rivals, native) == before[0]


def test_matching_account_name_does_not_disable_a_strangers_recording(library):
    client, session, row, _, _ = library
    session._community.state.update(authenticated=True, name=row["player_name"])
    session._rivals.community.sync = session._community
    result = decorate_page({"recordings": [row]}, session._tracker, session._rivals, "recordings")
    assert result["recordings"][0]["can_add"] and not result["recordings"][0]["is_own"]
    response = client.post("/api/ghost/rivals/saved", json={"target": OTHER, "action": "add", "id": row["id"]})
    assert response.status_code == 200 and response.json()["state"] == "added"


def test_active_saved_namespace_uses_normal_refresh_door(library):
    client, session, row, _, _ = library
    session._target = OTHER
    response = client.post("/api/ghost/rivals/saved", json={
        "target": OTHER, "action": "add", "id": row["id"], "version": "us"})
    assert response.status_code == 200
    assert session.refreshes == [{"resolve": False}]


@pytest.mark.parametrize("status", [404, 405, 500, 429, 401])
def test_cloud_failure_is_readable_and_cannot_write_pin(library, status):
    client, session, row, _, _ = library
    session._community.error = CommunityError("offline", "Shared ghosts could not be reached.", status=status)
    before = deepcopy(session._rivals.pins)
    response = client.post("/api/ghost/rivals/saved", json={"target": OTHER, "action": "add", "id": row["id"]})
    assert response.status_code == (status if status in (401, 429) else 503)
    assert isinstance(response.json()["detail"], str)
    assert session._rivals.pins == before


def test_owner_change_during_metadata_gate_never_saves(library):
    client, session, row, _, _ = library

    async def changed(_identifier):
        session._community.state["uid"] = "different-owner"
        return row

    session._community.recording_metadata = changed
    response = client.post("/api/ghost/rivals/saved", json={"target": OTHER, "action": "add", "id": row["id"]})
    assert response.status_code == 422 and "account changed" in response.json()["detail"]
    assert not session._rivals.pins.get(session._rivals._key(OTHER))


def test_owner_change_after_read_before_local_decoration_never_returns_rows(library):
    client, session, _, _, _ = library
    original = session._community.library_summary

    async def changed(*args, **kwargs):
        page = await original(*args, **kwargs)
        session._community.state["status"] = "signed_out"
        return page

    session._community.library_summary = changed
    response = client.get("/api/library/community")
    assert response.status_code == 422 and "account changed" in response.json()["detail"]
    assert "entries" not in response.json()


@pytest.mark.parametrize("state,word", [({"status": "signed_out"}, "Sign in"), ({"enabled": False}, "build")])
def test_unavailable_service_is_distinct_from_an_empty_library(library, state, word):
    client, session, _, _, _ = library
    session._community.state.update(state)
    response = client.get("/api/library/community")
    assert response.status_code == 200
    assert response.json()["available"] is False and word in response.json()["reason"]
    assert session._community.calls == []


def test_no_ghost_session_and_invalid_inputs_have_honest_answers(library):
    with session_client(None) as client:
        assert client.get("/api/library/community").json()["available"] is False
        assert client.post("/api/ghost/rivals/saved", json={"target": OTHER, "action": "add", "id": "a"*64}).status_code == 503
    client, session, _, _, _ = library
    for params in ({"entity": "segment:999999"}, {"entity": "star:2:99"}, {"rom": "eu"}, {"cursor": "a"*2049}):
        assert client.get("/api/library/community", params=params).status_code == 422
    assert client.get("/api/library/community/recordings", params={"target": "{", "clock": "igt", "rom": "us", "strategy": "Standard"}).status_code == 422
    assert client.post("/api/ghost/rivals/saved", json={"target": OTHER, "action": "remove", "id": "bad"}).status_code == 422
    assert session.refreshes == []


def test_clock_mismatch_and_foreign_segment_are_browseable_with_real_reasons(library):
    _, session, row, _, _ = library
    wrong_clock = {**row, "clock": "rta"}
    foreign = {**row, "target": {"kind": "segment", "entity_key": "912", "signature": "e"*64, "name": "Foreign segment"}}
    result = decorate_page({"recordings": [wrong_clock, foreign]}, session._tracker, session._rivals, "recordings")
    assert result["recordings"][0]["can_add"] is False
    assert "RTA" in result["recordings"][0]["unavailable_reason"]
    assert result["recordings"][1]["local_target"] is None
    assert "matching definition" in result["recordings"][1]["unavailable_reason"]
    with pytest.raises(ValueError):
        portable_filter("segment:912", session._tracker)


def test_closing_session_refuses_worker_before_any_saved_write(library):
    client, session, row, _, _ = library
    session._closing = True
    response = client.post("/api/ghost/rivals/saved", json={"target": OTHER, "action": "add", "id": row["id"]})
    assert response.status_code == 422 and "closing" in response.json()["detail"]
    assert not session._rivals.pins.get(session._rivals._key(OTHER))
