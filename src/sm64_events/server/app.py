# src/sm64_events/server/app.py
"""HTTP/WebSocket surface: / (viewer), /ws/events (broadcast), /health, /state.

The viewer page lives in src/sm64_events/ui/ (the frontend work zone) and is
re-read per request so UI edits show on refresh without a server restart.
"""
import asyncio
import logging
import os
import signal
import threading
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from sm64_events.core.events import Event
from sm64_events.core.paths import pidfile_path
from sm64_events.core.perfmon import PerfMonitor
from sm64_events.core.relaunch import spawn_replacement
from sm64_events.server.api import create_api_router
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.poller import Poller

log = logging.getLogger("sm64.server")

_UI_INDEX = Path(__file__).resolve().parent.parent / "ui" / "index.html"


def _dispatch(fn) -> None:
    """Run a shutdown/restart action OFF the request thread: blocking inside
    the handler (joining the server thread) would deadlock graceful
    shutdown."""
    threading.Thread(target=fn, daemon=True).start()


def _fallback_shutdown() -> None:
    signal.raise_signal(signal.SIGINT)


def _fallback_restart() -> None:
    spawn_replacement()
    signal.raise_signal(signal.SIGINT)


class PauseBody(BaseModel):
    paused: bool


def pause_state(poller, replay) -> dict:
    """The ONE pause truth the UI renders. Two sources, strict precedence:

    - reason "manual" (user pressed the button): poller paused (no events)
      AND replay discarding; player movement is ignored — only an explicit
      unpause clears it.
    - reason "afk" (recorder idle gate): replay discarding, but the poller
      KEEPS running — it must, the activity tap that detects the player's
      return rides it (and while AFK no events fire anyway). Any input
      resumes instantly; the UI just shows it happened.
    """
    if poller.paused:
        return {"paused": True, "reason": "manual"}
    if replay is not None and replay.recorder.status().get("idle"):
        return {"paused": True, "reason": "afk"}
    return {"paused": False, "reason": None}


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


# Outermost layer of the "bound every shutdown layer" doctrine (ffaff23
# bounded replay teardown; this bounds the WHOLE process). Why it exists:
# uvicorn's graceful shutdown waits for in-flight connections BEFORE
# lifespan teardown, and that wait is UNBOUNDED unless
# timeout_graceful_shutdown is set — the uvicorn CLI default is None, and
# a browser that stops reading a streaming response (a paused <video>
# holding a Range request) wedges the drain forever in flow_control.drain()
# (live incident 2026-06-13: CTRL+C -> "Shutting down" -> capture threads
# still logging at full rate 30 s later; repro: a stalled-reader client
# keeps serve() alive indefinitely with timeout=None, exits in 3 s with
# timeout=3). `python -m sm64_events.main` passes the bound; this watchdog
# covers every OTHER launch mode. Force-exit consequences are all already
# handled: ffmpeg dies with the kill-on-close job object, scratch is wiped
# on next start, SQLite journaling survives mid-write death, and the
# instance lock is an OS file-region lock released on process death.
_FORCE_EXIT_AFTER_S = 30.0


class ForceExitWatchdog:
    """First shutdown signal arms a one-shot daemon timer; if the process
    is still alive deadline_s later, log the wedge and force-exit. Daemon
    timer + os._exit: it cannot itself keep the process alive, and nothing
    wedging the event loop or a connection can block it."""

    def __init__(self, deadline_s: float = _FORCE_EXIT_AFTER_S,
                 exit_fn=os._exit):
        self._deadline_s = deadline_s
        self._exit = exit_fn
        self._lock = threading.Lock()
        self._armed = False

    def arm(self) -> None:
        with self._lock:
            if self._armed:
                return
            self._armed = True
        timer = threading.Timer(self._deadline_s, self._fire)
        timer.daemon = True
        timer.start()

    def _fire(self) -> None:
        log.error(
            "shutdown still incomplete %.0f s after the stop signal - "
            "force-exiting (something is wedging uvicorn's connection "
            "drain; launch via 'uv run python -m sm64_events.main' to "
            "bound it gracefully)", self._deadline_s)
        self._exit(1)


def install_force_exit_watchdog(dog: ForceExitWatchdog | None = None) -> bool:
    """Chain dog.arm() in FRONT of the existing SIGINT/SIGTERM/SIGBREAK
    handlers (uvicorn's handle_exit when running under uvicorn), so the
    graceful path proceeds unchanged but a hard deadline starts ticking.
    Signal handlers can only be installed on the main thread — under
    TestClient the lifespan runs on a portal thread, so this is a no-op
    there (tests don't CTRL+C). Non-callable handlers (SIG_DFL/SIG_IGN)
    are left untouched so default semantics never change. Returns whether
    at least one handler was chained."""
    if threading.current_thread() is not threading.main_thread():
        return False
    dog = dog or ForceExitWatchdog()
    installed = False
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        signum = getattr(signal, name, None)
        if signum is None:
            continue
        prev = signal.getsignal(signum)
        if not callable(prev):
            continue

        def _chained(sig, frame, _prev=prev):
            dog.arm()
            _prev(sig, frame)

        try:
            signal.signal(signum, _chained)
        except (ValueError, OSError):
            return installed  # non-main-thread race or exotic host
        installed = True
    return installed


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
    # Observability for long-running sessions: samples self + CHILD (ffmpeg)
    # memory, handle/GDI/USER counts, system pressure, and a per-type heap
    # histogram on a cadence — logs an expanded line, fires one-shot per-class
    # leak alarms, and persists a time-series to data/perf_log.jsonl. Backs
    # /health.memory. scratch_dir + ring gauges make replay churn visible too.
    def _perf_gauges() -> dict:
        g: dict = {}
        try:
            g.update(poller.perf_stats())     # tick-compute latency trend
        except Exception:
            pass
        if replay is not None:
            try:
                st = replay.recorder.status()
                g.update(ring_bytes=st.get("disk_bytes"), idle=st.get("idle"),
                         recording=st.get("recording"),
                         audio_mode=st.get("audio_mode"))
            except Exception:
                pass
        return g

    monitor = PerfMonitor(
        scratch_dir=replay.cfg.scratch_dir if replay is not None else None,
        gauges=_perf_gauges)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Installed here (not main.py) so EVERY launch mode gets the
        # bound — uvicorn installs its own handlers before lifespan
        # startup, so chaining at this point always finds them.
        install_force_exit_watchdog()
        try:
            pf = pidfile_path()
            pf.parent.mkdir(parents=True, exist_ok=True)
            pf.write_text(str(os.getpid()))
        except Exception:
            log.warning("could not write pidfile", exc_info=True)
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
                # watchdog + freeze the startup heap once everything is built.
                # is_idle drives the manual gen-2 collector (runs while
                # footage is discarded) so disabling auto-gen-2 can't leak.
                from sm64_events.replay._gcwatch import arm
                arm(is_idle=replay.recorder.is_idle)
            except Exception:
                log.exception("gc watchdog arm failed - continuing")
        task = asyncio.create_task(poller.run())
        task.add_done_callback(_log_poller_exit)
        mon_task = asyncio.create_task(monitor.run())
        yield
        mon_task.cancel()
        with suppress(asyncio.CancelledError):
            await mon_task
        if replay is not None:
            await _stop_replay_bounded(replay)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        with suppress(Exception):
            pidfile_path().unlink()

    app = FastAPI(title="SM64 Event API", lifespan=lifespan)

    @app.middleware("http")
    async def _ui_always_revalidate(request, call_next):
        """The UI contract is edit + refresh (no build, no restart). With
        no Cache-Control, browsers apply HEURISTIC freshness to /ui module
        files and can serve a STALE module alongside fresh ones — live
        incident 2026-06-12: cached store.js (no togglePause) + fresh
        header.js (with the pause button) = a dead control and no request
        ever sent. no-cache forces revalidation on every load (cheap 304s
        on localhost) so module versions can never mix."""
        response = await call_next(request)
        p = request.url.path
        if p == "/" or p.startswith("/ui"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    app.mount("/ui", StaticFiles(directory=str(_UI_INDEX.parent)), name="ui")
    if service is not None:
        app.include_router(create_api_router(service))
    if replay is not None:
        from sm64_events.server.replay_api import create_replay_router
        app.include_router(create_replay_router(replay))

    @app.get("/", response_class=HTMLResponse)
    def index():
        return _UI_INDEX.read_text(encoding="utf-8")

    @app.get("/api/pause")
    def get_pause():
        return pause_state(poller, replay)

    @app.post("/api/pause")
    def set_pause(body: PauseBody):
        """MANUAL pause switch (reason precedence in pause_state): the
        poller stops reading and dispatching (no events, no journal rows)
        and the replay recorder discards footage (rides the idle
        machinery). Pausing while AFK escalates to manual — movement no
        longer resumes. Unpausing while the player is still AFK lets the
        idle gate re-trigger naturally (~idle_after_s later). Lives HERE,
        not api.py — it spans poller + replay, which only this composition
        surface holds."""
        poller.set_paused(body.paused)
        if replay is not None:
            replay.recorder.set_session_paused(body.paused)
        return pause_state(poller, replay)

    @app.post("/api/admin/shutdown")
    def admin_shutdown():
        """Localhost-only graceful shutdown — the 'close the other instance'
        takeover path. The desktop sets app.state.request_shutdown to a FULL
        GUI quit; a terminal launch has none, so fall back to SIGINT."""
        _dispatch(getattr(app.state, "request_shutdown", None)
                  or _fallback_shutdown)
        return {"shutting_down": True}

    @app.post("/api/admin/restart")
    def admin_restart():
        """Localhost-only full-process relaunch (the 'Restart server'
        button) — picks up edited backend code. The desktop sets
        app.state.request_restart; a terminal launch falls back to
        spawn_replacement() + SIGINT (run() waits for the port via
        SM64_RESTART)."""
        _dispatch(getattr(app.state, "request_restart", None)
                  or _fallback_restart)
        return {"restarting": True}

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
            "memory": monitor.latest,
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
