# src/sm64_events/server/app.py
"""HTTP/WebSocket surface: / (viewer), /ws/events (broadcast), /health, /state.

The viewer page lives in src/sm64_events/ui/ (the frontend work zone) and is
re-read per request so UI edits show on refresh without a server restart.
"""
import asyncio
import logging
import threading
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from sm64_events.core.events import Event
from sm64_events.server.api import create_api_router
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


# Replay teardown joins capture threads and waits for ffmpeg to flush —
# worst-case tens of seconds of SYNC work. Run inside the event loop it
# blocks uvicorn's shutdown (and the force-exit CTRL+C path) — live
# incident 2026-06-12: CTRL+C appeared to hang with ffmpeg still logging.
_REPLAY_STOP_DEADLINE_S = 15.0


async def _stop_replay_bounded(replay) -> None:
    """Run replay.lifecycle_stop() on a DAEMON thread with a deadline.
    Deliberately not asyncio.to_thread: executor threads are non-daemon and
    the interpreter joins them at exit, which would re-introduce the hang
    we are bounding. If the deadline passes we abandon the worker (daemon —
    cannot block exit) and rely on the kill-on-close job object to reap
    ffmpeg (ffmpeg_sink._assign_kill_on_close)."""
    done = threading.Event()

    def _run():
        try:
            replay.lifecycle_stop()
        except Exception:
            log.exception("replay stop failed - continuing shutdown")
        finally:
            done.set()

    threading.Thread(target=_run, name="replay-stop", daemon=True).start()
    t0 = time.monotonic()
    while not done.is_set():
        if time.monotonic() - t0 > _REPLAY_STOP_DEADLINE_S:
            log.error("replay stop exceeded %.0f s - abandoning teardown "
                      "(worker is daemon; ffmpeg is reaped by the "
                      "kill-on-close job object)", _REPLAY_STOP_DEADLINE_S)
            return
        await asyncio.sleep(0.05)


def _quiet_connection_resets(loop, context) -> None:
    """Scoped asyncio noise filter. Browsers abort in-flight Range requests
    whenever a <video> element seeks; on Windows' proactor loop the dead
    socket's connection_lost callback then raises ConnectionResetError
    (WinError 10054) INSIDE asyncio (sock.shutdown on an already-reset
    socket) and the default handler prints a full traceback per seek.
    Those are normal client disconnects, not server errors. Everything
    else still reaches the default handler unchanged."""
    if isinstance(context.get("exception"), ConnectionResetError):
        log.debug("client connection reset (normal for video seeks): %s",
                  context.get("message"))
        return
    loop.default_exception_handler(context)


def create_app(poller: Poller, broadcaster: Broadcaster,
               service=None, replay=None, debug_hooks: bool = False) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        asyncio.get_running_loop().set_exception_handler(
            _quiet_connection_resets)
        if service is not None:
            try:
                await service.start()
            except Exception:
                log.exception("tracker start failed - degrading to broadcast-only")
                service.db = None
                service.session_id = None
        if replay is not None:
            try:
                replay.lifecycle_start()
            except Exception:
                log.exception("replay start failed - continuing without replay")
            try:
                # process-wide stop-the-world pauses (gen2 GC) hit the grab
                # loop and the audio callback simultaneously - arm the
                # watchdog + freeze the startup heap once everything is built
                from sm64_events.replay._gcwatch import arm
                arm()
            except Exception:
                log.exception("gc watchdog arm failed - continuing")
        task = asyncio.create_task(poller.run())
        task.add_done_callback(_log_poller_exit)
        yield
        if replay is not None:
            await _stop_replay_bounded(replay)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    app = FastAPI(title="SM64 Event API", lifespan=lifespan)

    app.mount("/ui", StaticFiles(directory=str(_UI_INDEX.parent)), name="ui")
    if service is not None:
        app.include_router(create_api_router(service))
    if replay is not None:
        from sm64_events.server.replay_api import create_replay_router
        app.include_router(create_replay_router(replay))

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
            "db": ("absent" if service is None
                   else "error" if service.db is None else "ok"),
            "session_id": service.session_id if service is not None else None,
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
