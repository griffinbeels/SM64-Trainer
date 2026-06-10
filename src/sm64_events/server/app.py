# src/sm64_events/server/app.py
"""HTTP/WebSocket surface: / (viewer), /ws/events (broadcast), /health, /state.

The viewer page lives in src/sm64_events/ui/ (the frontend work zone) and is
re-read per request so UI edits show on refresh without a server restart.
"""
import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from sm64_events.core.events import Event
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.poller import Poller

log = logging.getLogger("sm64.server")

_UI_INDEX = Path(__file__).resolve().parent.parent / "ui" / "index.html"


def _log_poller_exit(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.critical("poll loop died: %r", exc)


def create_app(poller: Poller, broadcaster: Broadcaster,
               debug_hooks: bool = False) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(poller.run())
        task.add_done_callback(_log_poller_exit)
        yield
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    app = FastAPI(title="SM64 Event API", lifespan=lifespan)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return _UI_INDEX.read_text(encoding="utf-8")

    @app.get("/health")
    def health():
        latest = poller.latest
        return {
            "status": "ok",
            "emulator_attached": poller.memory.attached,
            "clients": broadcaster.client_count,
            "last_frame": latest.global_timer if latest else None,
        }

    @app.get("/state")
    def state():
        latest = poller.latest
        if latest is None:
            return {"snapshot": None}
        d = asdict(latest)
        d["wall_time_utc"] = latest.wall_time_utc.isoformat().replace("+00:00", "Z")
        return {"snapshot": d}

    @app.websocket("/ws/events")
    async def ws_events(websocket: WebSocket):
        await websocket.accept()
        broadcaster.register(websocket)
        try:
            while True:
                await websocket.receive_text()  # ignore input; detect disconnect
        except WebSocketDisconnect:
            pass
        finally:
            broadcaster.unregister(websocket)

    if debug_hooks:
        @app.post("/debug/emit")
        async def debug_emit():
            await broadcaster.publish(Event(
                type="debug", frame=0,
                timestamp_utc=datetime.now(timezone.utc), payload={}))
            return {"ok": True}

    return app
