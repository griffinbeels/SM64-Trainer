# src/sm64_events/server/app.py
"""HTTP/WebSocket surface: /ws/events (broadcast), /health, /state."""
import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from sm64_events.core.events import Event
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.poller import Poller

log = logging.getLogger("sm64.server")

_INDEX_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>SM64 Event API</title>
<style>
  body { font-family: Consolas, monospace; background: #14161a; color: #d8dee9;
         max-width: 760px; margin: 2rem auto; padding: 0 1rem; }
  h1 { font-size: 1.2rem; } #status { padding: .2rem .5rem; border-radius: 4px; }
  .ok { background: #1d3a1d; color: #a3e0a3; } .bad { background: #3a1d1d; color: #e0a3a3; }
  li { margin: .25rem 0; list-style: none; } ul { padding: 0; }
  .star { color: #ffd75f; } .meta { color: #6c7686; font-size: .85em; }
</style></head><body>
<h1>SM64 Event API <span id="status" class="bad">connecting…</span></h1>
<p class="meta">Live feed from <code>/ws/events</code>. Grab a star in-game.</p>
<ul id="log"></ul>
<script>
  const log = document.getElementById("log"), status = document.getElementById("status");
  const ws = new WebSocket(`ws://${location.host}/ws/events`);
  ws.onopen = () => { status.textContent = "connected"; status.className = "ok"; };
  ws.onclose = () => { status.textContent = "disconnected"; status.className = "bad"; };
  ws.onmessage = (e) => {
    const ev = JSON.parse(e.data), li = document.createElement("li");
    if (ev.type === "star_collected") {
      const p = ev.payload;
      li.innerHTML = `<span class="star">⭐ ${p.course_name} — ${p.star_name}</span>`
        + `${p.igt_running ? ` <b>${p.igt}</b>` : ""}`
        + ` <span class="meta">course ${p.course_id} star ${p.star_id}`
        + `${p.already_collected ? " (already collected)" : ""} · frame ${ev.frame} · #${ev.seq}</span>`;
    } else {
      li.innerHTML = `${ev.type} <span class="meta">frame ${ev.frame} · #${ev.seq}</span>`;
    }
    log.prepend(li);
  };
</script></body></html>
"""


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
        return _INDEX_HTML

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
