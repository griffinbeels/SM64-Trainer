"""Browser attack entry points, using the actual ASGI request middleware."""
import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

from sm64_events.server.local_access import HEADER, mount_local_access


def protected_app():
    app = FastAPI()
    mutations = []

    @app.post("/api/action")
    def action():
        mutations.append(True)
        return {"ok": True}

    @app.websocket("/ws/events")
    async def events(socket: WebSocket):
        await socket.accept()
        await socket.send_text("accepted")
        await socket.close()

    mount_local_access(app, port=8072)
    return app, mutations


def test_only_exact_loopback_origin_can_bootstrap_and_mutate():
    app, changes = protected_app()
    with TestClient(app, base_url="http://127.0.0.1:8072") as client:
        result = client.get("/api/local-access")
        assert result.headers["cache-control"] == "no-store"
        token = result.json()["request_token"]
        assert client.post("/api/action").status_code == 403
        assert client.post("/api/action", headers={HEADER: "wrong"}).status_code == 403
        for attacker in ("https://attacker.example", "null", "http://127.0.0.1:8073"):
            assert client.get("/api/local-access", headers={"Origin": attacker}).status_code == 403
            assert client.post("/api/action", headers={HEADER: token, "Origin": attacker}).status_code == 403
        assert not changes
        assert client.post("/api/action", headers={HEADER: token,
                           "Origin": "http://127.0.0.1:8072"}).status_code == 200
        assert changes == [True]


@pytest.mark.parametrize("host", ["attacker.example:8072", "127.0.0.1:80", "localhost", "127.0.0.1:8072.attacker.example"])
def test_rebinding_and_wrong_port_cannot_read_local_capability(host):
    app, _ = protected_app()
    with TestClient(app, base_url="http://127.0.0.1:8072") as client:
        assert client.get("/api/local-access", headers={"Host": host}).status_code == 403


def test_cross_site_fetch_and_websocket_are_refused():
    from starlette.websockets import WebSocketDisconnect
    app, _ = protected_app()
    with TestClient(app, base_url="http://127.0.0.1:8072") as client:
        assert client.get("/api/local-access", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
        for origin in ("https://attacker.example", None):
            with pytest.raises(WebSocketDisconnect) as error:
                with client.websocket_connect("ws://127.0.0.1:8072/ws/events", headers={"Origin": origin} if origin else {}):
                    pass
            assert error.value.code == 1008
        with client.websocket_connect("ws://127.0.0.1:8072/ws/events", headers={"Origin": "http://127.0.0.1:8072"}) as socket:
            assert socket.receive_text() == "accepted"


def test_process_restart_rotates_capability():
    first, _ = protected_app()
    second, changes = protected_app()
    with TestClient(first, base_url="http://127.0.0.1:8072") as client:
        old = client.get("/api/local-access").json()["request_token"]
    with TestClient(second, base_url="http://127.0.0.1:8072") as client:
        assert client.post("/api/action", headers={HEADER: old}).status_code == 403
        assert not changes
