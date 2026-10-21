"""Friends controls without an SDK, emulator, recorder lease or live server."""
import asyncio
import threading
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from sm64_events.multiplayer.room import RoomError, RoomService
from sm64_events.server.local_access import HEADER, mount_local_access
from sm64_events.server.multiplayer_api import create_multiplayer_router, unavailable_state


class Room:
    def __init__(self):
        self.calls = []

    def snapshot(self):
        return {**unavailable_state(), "available": True, "unavailable_reason": None,
                "phase": "ready", "error": None}

    def identity(self):
        self.calls.append(("identity",))
        return "ET1-" + "a" * 32

    def __getattr__(self, method):
        def mutate(*arguments):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                pytest.fail("room mutation blocked the ASGI event loop")
            self.calls.append((method, *arguments))
            return self.snapshot()
        return mutate


def client_for(room=None, *, protected=False):
    app = FastAPI()
    app.include_router(create_multiplayer_router(room))
    mount_local_access(app, port=18991, protected=protected)
    return TestClient(app, base_url="http://127.0.0.1:18991")


def test_cached_read_has_no_identity_or_transport_side_effects():
    room = Room()
    with client_for(room) as client:
        for _ in range(3):
            response = client.get("/api/multiplayer")
            assert response.json()["phase"] == "ready"
            assert response.headers["cache-control"] == "no-store"
            assert "code" not in response.json()
        assert room.calls == []
    with client_for() as client:
        assert client.get("/api/multiplayer").json() == unavailable_state()
        assert client.post("/api/multiplayer/host").status_code == 503


def test_unconfigured_app_and_unavailable_room_have_the_same_public_shape():
    room = RoomService(None, None, available=False)
    assert room.snapshot() == unavailable_state()
    assert room._thread is None
    room.close()


@pytest.mark.parametrize("action,body,arguments", [
    ("host", {}, ()), ("invite", {"code": "ET1-" + "b" * 32}, ("ET1-" + "b" * 32,)),
    ("join", {"invite_id": "opaque-invitation"}, ("opaque-invitation",)),
    ("decline", {"invite_id": "opaque-invitation"}, ("opaque-invitation",)),
    ("leave", {}, ()), ("kick", {"member_id": "opaque-member"}, ("opaque-member",)),
])
def test_controls_reach_the_room_off_the_event_loop(action, body, arguments):
    room = Room()
    with client_for(room, protected=True) as client:
        assert client.post(f"/api/multiplayer/{action}", json=body).status_code == 403
        assert room.calls == []
        token = client.get("/api/local-access").json()["request_token"]
        response = client.post(f"/api/multiplayer/{action}", json=body, headers={HEADER: token})
        assert response.status_code == 200
        assert response.json() == room.snapshot()
        assert response.headers["cache-control"] == "no-store"
        assert room.calls == [(action, *arguments)]


def test_identity_is_explicit_and_never_reappears_in_cached_state():
    room = Room()
    with client_for(room) as client:
        response = client.post("/api/multiplayer/identity")
        assert response.json() == {"code": "ET1-" + "a" * 32}
        assert response.headers["cache-control"] == "no-store"
        assert "ET1-" not in client.get("/api/multiplayer").text
        assert room.calls == [("identity",)]


def test_slow_identity_does_not_block_cached_reads():
    room, started, release = Room(), threading.Event(), threading.Event()

    def identity():
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            pytest.fail("identity blocked the ASGI event loop")
        started.set()
        assert release.wait(3)
        return "ET1-" + "a" * 32

    room.identity = identity
    results = []
    with client_for(room) as client:
        request = threading.Thread(target=lambda: results.append(client.post("/api/multiplayer/identity")), daemon=True)
        request.start()
        try:
            assert started.wait(2)
            assert client.get("/api/multiplayer").status_code == 200
        finally:
            release.set()
            request.join(3)
        assert not request.is_alive() and results[0].status_code == 200


@pytest.mark.parametrize("body", [{}, {"code": 123}, {"code": "x" * 65},
                                  {"code": "x", "private": "must-not-echo"}])
def test_invalid_input_is_bounded_and_not_echoed(body):
    room = Room()
    with client_for(room) as client:
        response = client.post("/api/multiplayer/invite", json=body)
        assert response.status_code == 422
        assert response.json() == {"detail": "invalid_room_request"}
        assert response.headers["cache-control"] == "no-store"
        assert room.calls == []


@pytest.mark.parametrize("error,status,detail", [
    (RoomError("invalid_identity"), 400, "invalid_identity"),
    (RoomError("already_in_room"), 409, "already_in_room"),
    (RoomError("transport_timeout"), 503, "transport_timeout"),
    (RoomError("private-provider-reference"), 503, "room_operation_failed"),
    (RuntimeError("private-provider-reference"), 503, "room_operation_failed"),
])
def test_errors_are_fixed_safe_codes_and_uncacheable(error, status, detail):
    room = Room()

    def host():
        raise error

    room.host = host
    with client_for(room) as client:
        response = client.post("/api/multiplayer/host", json={})
        assert response.status_code == status
        assert response.json() == {"detail": detail}
        assert "private-provider-reference" not in response.text
        assert response.headers["cache-control"] == "no-store"


def test_main_composes_a_dormant_room_from_explicit_paths(monkeypatch, tmp_path):
    from sm64_events import main
    from sm64_events.multiplayer import eos_process
    calls = []
    for name in ("SM64_EOS_HELPER", "SM64_EOS_CONFIG", "SM64_EOS_CACHE"):
        monkeypatch.delenv(name, raising=False)
    replay = SimpleNamespace(recorder=object())
    dormant = main._build_multiplayer(replay)
    assert dormant.snapshot()["available"] is False and dormant._thread is None
    helper, config, cache = tmp_path / "helper.exe", tmp_path / "private.cfg", tmp_path / "cache"
    helper.write_bytes(b"test-only")
    config.write_text("test-only", encoding="utf-8")
    (tmp_path / "EOSSDK-Win64-Shipping.dll").write_bytes(b"test-only")
    for name, path in (("SM64_EOS_HELPER", helper), ("SM64_EOS_CONFIG", config), ("SM64_EOS_CACHE", cache)):
        monkeypatch.setenv(name, str(path))
    monkeypatch.setattr(eos_process.EOSProcess, "__init__", lambda *a, **k: calls.append(True))
    appearance = lambda: ("appearance", False)
    configured = main._build_multiplayer(replay, SimpleNamespace(snapshot=lambda: {"name": "Griffin"}),
                                         appearance=appearance)
    assert configured.snapshot()["phase"] == "idle" and configured._thread is None
    assert configured._name() == "Griffin" and calls == [] and configured._appearance is appearance
    configured.close()
    dormant.close()


@pytest.mark.parametrize("close_fails", [False, True])
def test_lifespan_closes_the_room_before_recorder_teardown(monkeypatch, tmp_path, close_fails):
    from sm64_events.server import app as module
    order = []

    async def idle():
        await asyncio.Event().wait()

    async def start_service(_):
        pass

    async def stop_replay(_):
        order.append("replay")

    monkeypatch.setattr(module, "_start_app_service", start_service)
    monkeypatch.setattr(module, "_start_app_replay", lambda _: None)
    monkeypatch.setattr(module, "_stop_replay_bounded", stop_replay)
    monkeypatch.setattr(module, "pidfile_path", lambda: tmp_path / "test.pid")
    poller = SimpleNamespace(run=idle, latest=None, _tick_lock=asyncio.Lock())
    def close():
        order.append("room")
        if close_fails:
            raise RoomError("room_cleanup_pending")

    room = SimpleNamespace(close=close)
    lifespan = module._create_lifespan(poller, None, object(), SimpleNamespace(run=idle), None,
                                      False, multiplayer=room)
    with TestClient(FastAPI(lifespan=lifespan)):
        assert order == []
    assert order == ["room", "replay"]


def test_room_actions_do_not_trigger_saved_pb_publication():
    from sm64_events.server.app import _install_progress_invalidation
    invalidations = []
    app = FastAPI()
    app.include_router(create_multiplayer_router(Room()))
    _install_progress_invalidation(app, SimpleNamespace(invalidate=lambda: invalidations.append(True)))

    @app.post("/api/example-saved-pb-edit")
    def edit():
        return {"saved": True}

    with TestClient(app) as client:
        assert client.post("/api/multiplayer/host", json={}).status_code == 200
        assert invalidations == []
        assert client.post("/api/example-saved-pb-edit").status_code == 200
        assert invalidations == [True]
