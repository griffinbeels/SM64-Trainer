# src/sm64_events/server/app.py
"""HTTP/WebSocket surface: /ws/events (broadcast), /health, /state."""
import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from sm64_events.core.events import Event
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.poller import Poller


def create_app(poller: Poller, broadcaster: Broadcaster,
               debug_hooks: bool = False) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(poller.run())
        yield
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    app = FastAPI(title="SM64 Event API", lifespan=lifespan)

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "emulator_attached": poller.memory.attached,
            "clients": broadcaster.client_count,
            "last_frame": poller.latest.global_timer if poller.latest else None,
        }

    @app.get("/state")
    def state():
        if poller.latest is None:
            return {"snapshot": None}
        d = asdict(poller.latest)
        d["wall_time_utc"] = poller.latest.wall_time_utc.isoformat().replace("+00:00", "Z")
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
