"""The scene viewer's routes (M4 E3, lab only): one recording in its reconstructed stage, in the Trainer.

None of this exists unless SM64_SCENE_LAB names a lab config: main.py::_mount_scene_lab is the
one reader, and without it this module is never imported and no route is added. With it:

    GET  /api/scene-lab           the probe the shell waits for before it shows the Lab entry
    GET  /api/scene-lab/status    the config's readiness, the packages, the open scene, the last end
    POST /api/scene-lab/open      {package: <a listed name>} -> the scene; 409 with the reason
    POST /api/scene-lab/close
    WS   /api/scene-lab/ws        pictures and poses of the open scene (protocol below)

The lab config is a private JSON file of absolute paths: {"rom", "packages" (a folder of
.sm64scene files), "pj64_plugin", "map" (the free camera's MappingUS.map), and optionally
"renderer", "resolution" ("WxH"), "idle_seconds"}. The page names a package only by a name the
server listed from that folder; no path or URL ever comes from the page. Packages stay
untrusted: package.py's reader checks every record, and the evaluator executes code only from
the configured ROM. The scene lab's code (tools/scene_lab) runs from a source checkout.

Nothing runs until a page opens a package. The session (tools/scene_lab/scene_session.py)
starts the renderer host after the GPU hold check and, on the first free-camera request, the
evaluator. Each session owns ONE worker thread: every package decode, evaluator run and host
round trip happens there, and the event loop only awaits it. A session ends on Close, on
another Open, when no page has been connected for `page_gone_seconds` (unload, leaving the
tab), after the config's idle_seconds without a request, when its host dies, and at server
shutdown; each end closes the host's pipe and waits for it to exit.

Socket protocol. The page sends JSON: {"id", "session", "frame", "camera": null or {"focus":
xyz, "position": xyz}, "encoding": "jpeg"|"raw"} for a picture, {"id", "session", "kind":
"pose", "frame"} for the recorded camera and Mario. A picture comes back as one binary message,
[u32 little-endian header length][header JSON][picture]; everything else is JSON text: a pose,
{"id", "error"[, "closed"]}, or {"id", "superseded": true}. Each socket keeps at most one
picture request waiting: a newer one replaces it and the replaced one is answered superseded,
so quick steps never queue stale renders behind the one being drawn.
"""
import asyncio
import json
import logging
import math
import struct
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

log = logging.getLogger("sm64.scene_lab")

TOOLS = Path(__file__).resolve().parents[3] / "tools" / "scene_lab"
PACKAGE_SUFFIX = ".sm64scene"
PAGE_GONE_SECONDS = 20.0       # a reload or a tab switch comes back within this; a closed page does not
DEFAULT_IDLE_SECONDS = 300.0
WATCH_SECONDS = 1.0
MAX_POSES_WAITING = 8
ENDS = {"closed": "Closed.",
        "replaced": "Closed to open another scene.",
        "page": "Closed because no page was showing it.",
        "idle": "Closed after a while without use, to free the renderer.",
        "shutdown": "Closed when the server stopped."}


class ConfigError(ValueError):
    """The lab config cannot be used; the message says why."""


class SceneRefused(Exception):
    """A scene did not open; the message is scene_session.Refused's reason."""


class SceneClosed(Exception):
    """A request named a scene that is no longer open."""


def _absolute(raw: dict, key: str, required: bool) -> Path | None:
    value = raw.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise ConfigError(f'The lab config\'s "{key}" must be an absolute path.')
    return Path(value)


@dataclass(frozen=True)
class LabConfig:
    rom: Path
    packages: Path
    pj64_plugin: Path | None = None
    map: Path | None = None
    renderer: str = "trainer"
    resolution: tuple[int, int] | None = None
    idle_seconds: float = DEFAULT_IDLE_SECONDS

    @classmethod
    def load(cls, path: Path) -> "LabConfig":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ConfigError(f"The lab config {path} cannot be read: {error}") from None
        if not isinstance(raw, dict):
            raise ConfigError("The lab config must be a JSON object.")
        resolution = raw.get("resolution")
        if resolution is not None:
            try:
                width, height = (int(part) for part in str(resolution).lower().split("x"))
            except ValueError:
                raise ConfigError('The lab config\'s "resolution" must look like 1600x1200.') from None
            if not (64 <= width <= 8192 and 64 <= height <= 8192):
                raise ConfigError('The lab config\'s "resolution" is outside 64..8192.')
            resolution = (width, height)
        idle = raw.get("idle_seconds", DEFAULT_IDLE_SECONDS)
        if isinstance(idle, bool) or not isinstance(idle, (int, float)) or not 5 <= idle <= 86400:
            raise ConfigError('The lab config\'s "idle_seconds" must be 5 to 86400.')
        renderer = raw.get("renderer", "trainer")
        if not isinstance(renderer, str) or not renderer:
            raise ConfigError('The lab config\'s "renderer" must be trainer, link or a DLL path.')
        return cls(_absolute(raw, "rom", True), _absolute(raw, "packages", True),
                   _absolute(raw, "pj64_plugin", False), _absolute(raw, "map", False),
                   renderer, resolution, float(idle))

    def problems(self) -> list[str]:
        """What stops any scene from opening, in words the page shows."""
        found = []
        if not self.rom.is_file():
            found.append("The ROM named in the lab config is missing.")
        if not self.packages.is_dir():
            found.append("The scene package folder named in the lab config is missing.")
        if self.pj64_plugin is None or not self.pj64_plugin.is_dir():
            found.append("The lab config does not name Project64's Plugin folder.")
        return found

    def listing(self) -> list[dict]:
        if not self.packages.is_dir():
            return []
        rows = []
        for path in sorted(self.packages.iterdir(), key=lambda item: item.name.lower()):
            if path.suffix.lower() == PACKAGE_SUFFIX and path.is_file():
                stat = path.stat()
                rows.append({"name": path.name, "bytes": stat.st_size,
                             "modified_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()})
        return rows

    def package_path(self, name: str) -> Path:
        """The listed package called `name`; LookupError for anything the folder does not list."""
        if name not in {row["name"] for row in self.listing()}:
            raise LookupError("No scene package by that name in the lab's folder.")
        return self.packages / name


def _lab_module(name: str):
    """A tools/scene_lab module, imported from this checkout only when a page needs it."""
    if getattr(sys, "frozen", False) or not (TOOLS / f"{name}.py").is_file():
        raise ConfigError("The scene lab runs from a source checkout, and this one has no tools/scene_lab.")
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    return __import__(name)


class LoopProbe:
    """How late the event loop answers a short timer while a scene is open (measurement runs only:
    e3_measure.py mounts with measure_loop=True; the app never runs it)."""
    INTERVAL = 0.005

    def __init__(self):
        self.late: deque[float] = deque(maxlen=50000)
        self.task: asyncio.Task | None = None

    async def run(self) -> None:
        last = time.perf_counter()
        while True:
            await asyncio.sleep(self.INTERVAL)
            now = time.perf_counter()
            self.late.append((now - last - self.INTERVAL) * 1000)
            last = now

    def summary(self) -> dict:
        values = sorted(self.late)
        if not values:
            return {"samples": 0}

        def at(p: float) -> float:
            return round(values[min(len(values) - 1, math.ceil(p * len(values)) - 1)], 2)
        return {"samples": len(values), "late_ms": {"p50": at(.5), "p99": at(.99), "max": round(values[-1], 2)},
                "over_50ms": sum(value > 50 for value in values)}


class SceneLab:
    """The one open scene (or none) and the pages showing it."""

    def __init__(self, config_path: Path, *, session_options: dict | None = None,
                 page_gone_seconds: float = PAGE_GONE_SECONDS, watch_seconds: float = WATCH_SECONDS,
                 idle_seconds: float | None = None, measure_loop: bool = False):
        self.config_path = Path(config_path)
        self.session_options = session_options or {}
        self.page_gone_seconds, self.watch_seconds = page_gone_seconds, watch_seconds
        self.idle_override = idle_seconds
        self.session = None
        self.worker: ThreadPoolExecutor | None = None
        self.described: dict | None = None
        self.generation = 0
        self.lock = asyncio.Lock()
        self.sockets = self.busy = 0
        self.last_used = self.page_seen = 0.0
        self.idle_seconds = DEFAULT_IDLE_SECONDS
        self.ended: dict | None = None
        self.host_exit: int | None = None
        self.watch_task: asyncio.Task | None = None
        self.loop_probe = LoopProbe() if measure_loop else None

    # ------------------------------------------------------------ status, open, close

    def _status_blocking(self) -> dict:
        try:
            config = LabConfig.load(self.config_path)
        except ConfigError as error:
            return {"ready": False, "problems": [str(error)], "packages": [], "hold": None}
        problems = config.problems()
        try:
            hold = _lab_module("gpu_gate").hold_reason()
        except ConfigError as error:
            problems.append(str(error))
            hold = None
        return {"ready": not problems, "problems": problems, "packages": config.listing(), "hold": hold}

    async def status(self) -> dict:
        report = await asyncio.to_thread(self._status_blocking)
        report["session"] = ({"session": self.generation, **self.described}
                             if self.session is not None and self.described else None)
        report["ended"] = self.ended
        if self.loop_probe is not None:
            report["loop"] = self.loop_probe.summary()
        return report

    def _open_blocking(self, config: LabConfig, path: Path):
        module = _lab_module("scene_session")
        try:
            session = module.SceneSession.open(config, path, **self.session_options)
        except module.Refused as refused:
            raise SceneRefused(str(refused)) from None
        return session, session.describe()

    async def open(self, name: str) -> dict:
        config = await asyncio.to_thread(LabConfig.load, self.config_path)
        path = await asyncio.to_thread(config.package_path, name)
        async with self.lock:
            await self._end("replaced")
            worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scene-lab")
            try:
                session, described = await asyncio.get_running_loop().run_in_executor(
                    worker, self._open_blocking, config, path)
            except BaseException:
                worker.shutdown(wait=False)
                raise
            self.session, self.worker, self.described = session, worker, described
            self.generation += 1
            self.idle_seconds = self.idle_override or config.idle_seconds
            self.last_used = self.page_seen = time.monotonic()
            self.ended = self.host_exit = None
            self._start_watch()
            return {"session": self.generation, **described}

    async def close(self, code: str = "closed") -> None:
        async with self.lock:
            await self._end(code)

    async def _end(self, code: str, message: str | None = None) -> None:
        """Ends the open scene, if any: the close runs on its worker after any render in flight."""
        session, worker = self.session, self.worker
        if session is None:
            return
        self.session = self.worker = self.described = None
        self.ended = {"code": code, "message": message or ENDS[code], "name": session.name}
        try:
            self.host_exit = await asyncio.get_running_loop().run_in_executor(worker, session.close)
        except Exception:  # noqa: BLE001 - an end always completes; the host's own error is logged
            log.warning("The scene viewer's renderer did not close cleanly.", exc_info=True)
        finally:
            worker.shutdown(wait=False)
        if self.loop_probe is not None and self.loop_probe.task is not None:
            self.loop_probe.task.cancel()
            self.loop_probe.task = None

    async def shutdown(self) -> None:
        if self.watch_task is not None:
            self.watch_task.cancel()
        await self.close("shutdown")

    def _start_watch(self) -> None:
        loop = asyncio.get_running_loop()
        if self.watch_task is None or self.watch_task.done():
            self.watch_task = loop.create_task(self._watch(), name="scene-lab-watch")
        if self.loop_probe is not None and self.loop_probe.task is None:
            self.loop_probe.task = loop.create_task(self.loop_probe.run(), name="scene-lab-loop-probe")

    async def _watch(self) -> None:
        """Releases a scene nobody is looking at or using; stops once no scene is open."""
        while self.session is not None:
            await asyncio.sleep(self.watch_seconds)
            now = time.monotonic()
            if self.session is None or self.busy:
                continue
            if self.sockets == 0 and now - self.page_seen > self.page_gone_seconds:
                await self.close("page")
            elif now - self.last_used > self.idle_seconds:
                await self.close("idle")

    # ------------------------------------------------------------ requests

    async def _run(self, generation, method: str, *args):
        if self.session is None or generation != self.generation:
            raise SceneClosed((self.ended or {}).get("message") or "This scene is not open.")
        session, worker = self.session, self.worker
        self.busy += 1
        self.last_used = time.monotonic()
        try:
            return await asyncio.get_running_loop().run_in_executor(worker, getattr(session, method), *args)
        except (RuntimeError, OSError) as error:
            # viewer.Host raises these when the host died or broke protocol: the scene cannot go on.
            message = f"The renderer stopped: {error}"
            async with self.lock:
                if session is self.session:
                    await self._end("renderer", message)
            raise SceneClosed(message) from None
        finally:
            self.busy -= 1
            self.last_used = time.monotonic()

    async def picture_reply(self, message: dict):
        ident = message.get("id")
        received = time.perf_counter()
        try:
            header, payload = await self._run(message.get("session"), "picture", message.get("frame"),
                                              message.get("camera"), message.get("encoding", "jpeg"))
        except SceneClosed as closed:
            return {"id": ident, "error": str(closed), "closed": True}
        except ValueError as error:
            return {"id": ident, "error": str(error)}
        header.update(id=ident, session=message.get("session"),
                      server_ms=round((time.perf_counter() - received) * 1000, 3))
        encoded = json.dumps(header).encode("utf-8")
        return struct.pack("<I", len(encoded)) + encoded + payload

    async def pose_reply(self, message: dict) -> dict:
        try:
            pose = await self._run(message.get("session"), "pose", message.get("frame"))
        except SceneClosed as closed:
            return {"id": message.get("id"), "error": str(closed), "closed": True}
        except ValueError as error:
            return {"id": message.get("id"), "error": str(error)}
        return {"id": message.get("id"), "kind": "pose", **pose}

    async def serve_socket(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.sockets += 1
        self.page_seen = time.monotonic()
        page = _PageSocket(self, websocket)
        tasks = [asyncio.create_task(page.receive()), asyncio.create_task(page.answer())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if not task.cancelled() and task.exception() is not None \
                        and not isinstance(task.exception(), WebSocketDisconnect):
                    log.warning("scene viewer socket ended: %r", task.exception())
        finally:
            for task in tasks:
                task.cancel()
            self.sockets -= 1
            self.page_seen = time.monotonic()
            # asyncio.wait, not gather: if the server is cancelling this handler, the
            # cancellation that propagates must stay the server's own, not a child's.
            await asyncio.wait(tasks)


class _PageSocket:
    """One page's socket: at most one picture request waiting (a newer one supersedes it),
    a short queue of pose requests, answered one at a time in that order, poses first."""

    def __init__(self, lab: SceneLab, websocket: WebSocket):
        self.lab, self.websocket = lab, websocket
        self.sending = asyncio.Lock()
        self.picture: dict | None = None
        self.poses: deque = deque()
        self.wake = asyncio.Event()

    async def send(self, reply) -> None:
        async with self.sending:
            if isinstance(reply, bytes):
                await self.websocket.send_bytes(reply)
            else:
                await self.websocket.send_text(json.dumps(reply))

    async def receive(self) -> None:
        while True:
            event = await self.websocket.receive()
            if event["type"] == "websocket.disconnect":
                return
            try:
                message = json.loads(event.get("text") or "")
            except ValueError:
                message = None
            if not isinstance(message, dict):
                await self.send({"id": None, "error": "a request is one JSON object"})
                continue
            if message.get("kind") == "pose":
                if len(self.poses) >= MAX_POSES_WAITING:
                    await self.send({"id": self.poses.popleft().get("id"), "superseded": True})
                self.poses.append(message)
            else:
                replaced, self.picture = self.picture, message
                if replaced is not None:
                    await self.send({"id": replaced.get("id"), "superseded": True})
            self.wake.set()

    async def answer(self) -> None:
        while True:
            await self.wake.wait()
            self.wake.clear()
            while self.poses or self.picture is not None:
                if self.poses:
                    await self.send(await self.lab.pose_reply(self.poses.popleft()))
                else:
                    message, self.picture = self.picture, None
                    await self.send(await self.lab.picture_reply(message))


class OpenBody(BaseModel):
    package: str = Field(min_length=1, max_length=255)


def mount_scene_lab(app, config_path, **options) -> SceneLab:
    """Adds the scene viewer's routes to `app` and closes its scene when the app stops.
    `options` are SceneLab's (tests and e3_measure.py inject doubles and short timers)."""
    lab = SceneLab(Path(config_path), **options)
    router = APIRouter(prefix="/api/scene-lab")

    @router.get("")
    def probe():
        return {"lab": "scene-viewer"}

    @router.get("/status")
    async def status():
        return await lab.status()

    @router.post("/open")
    async def open_scene(body: OpenBody):
        try:
            return await lab.open(body.package)
        except LookupError as missing:
            raise HTTPException(404, str(missing)) from None
        except (ConfigError, SceneRefused) as refused:
            raise HTTPException(409, str(refused)) from None

    @router.post("/close")
    async def close_scene():
        await lab.close("closed")
        return {"closed": True, "host_exit": lab.host_exit}

    @router.websocket("/ws")
    async def scene_socket(websocket: WebSocket):
        await lab.serve_socket(websocket)

    app.include_router(router)
    serving = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with serving(application) as state:
            try:
                yield state
            finally:
                await lab.shutdown()

    app.router.lifespan_context = lifespan
    app.state.scene_lab = lab
    return lab
