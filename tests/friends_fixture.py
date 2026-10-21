"""Isolated Friends UI/API scenarios. Production never loads these peers.

serve_friends opens only an owned loopback fixture, using the existing fixture
thread lifecycle. It never builds the real app or starts a recorder/SDK.
"""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import re
import sys
import threading
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "src/sm64_events/ui"
PRIVATE_CODE = "ET1.fixture-only-private-player-code"
NAMES = ["griffman1212", "RenéPatientRunner123", "PatientRunner12345678", "CuriousMarioExplorer"]


def scene(name="idle"):
    state = {"available": True, "unavailable_reason": None, "phase": "idle", "room": None,
             "invitations": [], "source": {"state": "waiting"}, "error": None, "can_invite": False}
    if name == "unavailable":
        state.update(available=False, unavailable_reason="Sign in to your Trainer account to use Friends.")
    if name == "invites":
        state["invitations"] = [{"id": "fixture-invite", "from_name": NAMES[1], "member_count": 2}]
    if name in {"hosting", "full", "reconnecting", "error"}:
        count = 1 if name == "hosting" else 4
        members = [{"id": f"fixture-player-{index}", "name": NAMES[index], "state": "playing", "self": index == 0}
                   for index in range(count)]
        state.update(phase="hosting", room={"owner": True, "members": members, "max_members": 4}, can_invite=count < 4)
    if name == "reconnecting":
        state["phase"] = "reconnecting"
        state["room"]["members"][2]["state"] = "reconnecting"
    if name == "error":
        state.update(phase="error", error="Couldn’t reconnect. Leave this room and try again.")
    return state


class FriendsFixture:
    def __init__(self, name="idle", *, loading=False):
        self._lock = threading.RLock()
        self.data = scene(name)
        self.actions, self.observations = [], []
        self.loading = loading
        self.release = threading.Event()

    def snapshot(self):
        with self._lock:
            return deepcopy(self.data)

    def change(self, name):
        with self._lock:
            self.data = scene(name)

    def action(self, action, body):
        with self._lock:
            self.actions.append((action, deepcopy(body)))
            if action == "identity":
                return {"code": PRIVATE_CODE}
            if action == "host":
                self.data = scene("hosting")
            elif action == "join":
                invitation = next(row for row in self.data["invitations"] if row["id"] == body["invite_id"])
                self.data = scene("hosting")
                self.data.update(phase="connected", can_invite=True)
                self.data["room"].update(owner=False, members=[
                    {"id": "friend", "name": invitation["from_name"], "self": False, "state": "playing"},
                    {"id": "self", "name": NAMES[0], "self": True, "state": "waiting"}])
            elif action == "decline":
                self.data["invitations"] = [row for row in self.data["invitations"] if row["id"] != body["invite_id"]]
            elif action == "leave":
                self.data = scene()
            elif action == "kick":
                members = self.data["room"]["members"]
                self.data["room"]["members"] = [row for row in members if row["id"] != body["member_id"]]
                self.data["can_invite"] = True
            elif action != "invite":
                raise HTTPException(404, "Unknown fixture action")
            return {"queued": True, "snapshot": deepcopy(self.data)}


def fixture_html():
    source = (UI / "index.html").read_text(encoding="utf-8")
    styles = "\n".join(re.findall(r"<style>([\s\S]*?)</style>", source))
    imports = re.search(r'<script type="importmap">([\s\S]*?)</script>', source)[1]
    return ("<!doctype html><html><head><meta charset='utf-8'><title>Friends isolated fixture</title>"
            + '<link rel="icon" href="/ui/favicon.png">'
            + '<script type="importmap">' + imports + "</script><style>" + styles + "</style>"
            + '<link rel="stylesheet" href="/ui/friends.css"></head><body><div class="app-shell">'
            + '<aside class="app-sidebar"><div class="app-brand">SM64 Trainer</div><nav id="friends-fixture"></nav></aside>'
            + '<main class="app-main"><h1>Practice</h1><p>Isolated Friends fixture</p></main></div>'
            + '<script type="module">import {h,render} from "preact";'
            + 'import {FriendsControl} from "/ui/components/friends.js";'
            + 'render(h(FriendsControl,{className:"nav-item"}),document.querySelector("#friends-fixture"));'
            + "</script></body></html>")


def create_friends_app(state):
    app = FastAPI()

    @app.get("/", response_class=HTMLResponse)
    def page():
        return fixture_html()

    @app.get("/api/local-access")
    def local_access():
        return {"request_token": "isolated-friends-fixture"}

    @app.get("/api/multiplayer")
    def status():
        if state.loading:
            state.release.wait(5)
        return state.snapshot()

    @app.post("/api/multiplayer/{action}")
    async def act(action: str, request: Request):
        if request.headers.get("X-SM64-Request-Token") != "isolated-friends-fixture":
            raise HTTPException(403, "Missing fixture request capability")
        return state.action(action, await request.json())

    @app.post("/api/uilog")
    async def observation(request: Request):
        state.observations.append(await request.json())
        return {"ok": True}

    app.mount("/ui", StaticFiles(directory=UI), name="ui")
    return app


@contextmanager
def serve_friends(name="idle", *, loading=False):
    """Parent-owned browser gate only; closes its exact listener and thread."""
    sys.path.insert(0, str(ROOT / "tools"))
    import ui_fixture
    state = FriendsFixture(name, loading=loading)
    listener = ui_fixture._bound_socket()
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_friends_app(state), host="127.0.0.1", port=port,
        log_level="error", lifespan="off", ws="none",
        loop="asyncio:SelectorEventLoop" if sys.platform == "win32" else "auto"))
    worker, errors = ui_fixture._fixture_server_thread(server, [listener])
    worker.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not server.started:
            raise RuntimeError("Friends fixture did not start") from (errors[0] if errors else None)
        yield f"http://127.0.0.1:{port}", state
    finally:
        state.release.set()
        ui_fixture._stop_fixture_server(server, worker, errors)
        listener.close()
