"""The scene viewer's routes (M4 E3, lab only): one recording in its reconstructed stage, in the Trainer.

None of this exists unless SM64_SCENE_LAB names a lab config: main.py::_mount_scene_lab is the
one reader, and without it this module is never imported and no route is added. With it:

    GET  /api/scene-lab           the probe the shell waits for before it shows the Lab entry, and
                                  "live": whether the scene recording runs ({recording, reason})
    GET  /api/scene-lab/status    the config's readiness, the packages, the open scene, the last end
    GET  /api/scene-lab/attempt-scenes?ids=12,13   each known attempt's scene: ready, cutting, unavailable
    POST /api/scene-lab/open      {package: <a listed name>} or {attempt: <id>} -> the scene; 409 with the reason
    POST /api/scene-lab/close
    WS   /api/scene-lab/ws        pictures and poses of the open scene (protocol below)
    GET  /api/scene-lab/audio/{block}?session=N   one block of the open scene's sound

The lab config is a private JSON file of absolute paths: {"rom", "packages" (a folder of
.sm64scene files), "pj64_plugin", "map" (the free camera's MappingUS.map), and optionally
"renderer", "resolution" ("WxH"), "idle_seconds", "lab_root" (where the live scene recording
and the attempt scenes live; %LOCALAPPDATA%\\SM64Trainer\\scene-lab when absent)}. The page names a
package only by a name the server listed from that folder, or an attempt by its id; no path or
URL ever comes from the page. Packages stay untrusted: package.py's reader checks every record,
and the evaluator executes code only from the configured ROM. The scene lab's code
(tools/scene_lab) runs from a source checkout.

Attempt scenes (round 2, "IMMEDIATELY AFTER FINISHING AN ATTEMPT"): while the server runs, the
lab holds live_spool.Lease (the wrapper's scene tap records into a rolling spool) and
attempt_scenes.AttemptScenes, whose cutter child writes each completed attempt's scene from that
spool (tools/scene_lab/attempt_cutter.py; the span: attempt_span.py). main.py chains the
tracker's completion hook and the broadcaster's observer to it (follow_attempts); the replay's
hook is called first and unchanged.

Nothing runs until a page opens a package. The session (tools/scene_lab/scene_session.py)
starts the renderer host after the GPU hold check and, on the first free-camera request, the
evaluator. Each session owns two threads, and the event loop only awaits them: the worker runs
every package decode, evaluator run, pose, pick and request written to the host (session.begin);
the delivery thread takes each picture back and has the picture encoder child make its JPEG
(session.finish) while the worker begins the next, so the game's code for one camera runs while
the renderer draws and the encoder compresses the one before (measured 2026-10-26 on the
headless page: a 60 Hz drag drew 59 pictures a second, every input, up from 52, and its input
to drawn p95 fell from 48 to 31 ms). A session ends on Close, on
another Open, when no page has been connected for `page_gone_seconds` (unload, leaving the
tab), after the config's idle_seconds without a request, when its host dies, and at server
shutdown; each end closes the host's pipe and waits for it to exit.

The recording's sound, when the package carries a source-audio layer, is described in the open
scene (its "audio" table, tools/scene_lab/scene_audio.py) and served block by block over HTTP:
a block is a complete .flac (or .wav) the page decodes, served once its PCM matched its hash.
Sound blocks decode on the default thread pool rather than the session's worker: they read only
the package's immutable sound layer, and a block waiting behind a render would start Play's
sound late. 404 for no sound or no such block, 409 for a scene no longer open, 422 for a block
that fails its hash.

Socket protocol. The page sends JSON: {"id", "session", "frame", "camera": null or {"focus":
xyz, "position": xyz[, "roll", "fov"]}, "encoding": "jpeg"|"raw"|"shared"} for a picture ("shared":
the desktop window's page only, once attach_window gave the scene its window; the exact pixels
go through WebView2 shared memory and the binary reply is the header alone, or a JPEG when the
post failed, as its header's encoding says), {"id",
"session", "kind": "pose", "frame"} for the recorded camera and Mario, {"id", "session", "kind":
"pick", "frame", "camera", "u", "v"} for where the picture drew a point (u, v from 0 to 1 across
and down; Set focus), and {"id", "session", "kind": "ghosts", "set": [{"ref", "label"}][, "origin":
a frame]} for the ghosts every later picture draws (the whole set, replacing the last; [] removes
them), answered {"id", "kind": "ghosts", "shown": [refs], "refused": [{"ref", "reason"}]} once
each new ghost's recording is in the renderer (scene_session.set_ghosts; clips come from the
ghost catalog's load_ghost, server/scene_ghosts.py, else `ghost_loader`). A picture of a scene
with ghosts carries "ghosts" in its header ({"asked", "chosen", "drawn", "displayed",
"pending", "refused"}). A picture comes back as one binary message, [u32 little-endian header
length][header JSON][picture]; everything else is JSON text: a pose, a pick, a ghosts answer,
{"id", "error"[, "closed"]}, or {"id", "superseded": true}. Each socket draws up to PICTURES_IN_FLIGHT pictures at
once (answered in the order they began) and keeps at most one more request waiting: a newer one
replaces it and the replaced one is answered superseded, so quick steps never queue stale
renders behind the ones being drawn. Poses and picks wait in order and are answered before a
waiting picture. The server sends its
messages uncompressed even where the page negotiated permessage-deflate (_SendUncompressed):
compressing a picture cost the event loop ~5 ms per JPEG and ~75 ms per exact-pixel picture.
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

from fastapi import APIRouter, HTTPException, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, model_validator

log = logging.getLogger("sm64.scene_lab")

TOOLS = Path(__file__).resolve().parents[3] / "tools" / "scene_lab"
PACKAGE_SUFFIX = ".sm64scene"
PAGE_GONE_SECONDS = 20.0       # a reload or a tab switch comes back within this; a closed page does not
DEFAULT_IDLE_SECONDS = 300.0
WATCH_SECONDS = 1.0
MAX_QUESTIONS_WAITING = 8     # poses and picks a socket holds; the oldest is answered superseded
PICTURES_IN_FLIGHT = 2        # pictures a socket has begun and not yet sent (the page keeps as many)
GROW_SECONDS = 0.25           # how often an open scene still being written takes its newest frames
GROW_FAILURES = 3             # publications in a row it cannot read before it stops growing
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
    lab_root: Path | None = None

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
                   renderer, resolution, float(idle), _absolute(raw, "lab_root", False))

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
                 idle_seconds: float | None = None, measure_loop: bool = False, live: bool = False,
                 lease_factory=None, cutter_factory=None, cutter_options: dict | None = None,
                 grow_seconds: float = GROW_SECONDS, live_audio=None, ghost_loader=None):
        """live: hold the recording's lease and cut attempt scenes (main.py's server only; a test,
        the UI fixture or e3_measure.py never arms the wrapper's tap). Tests replace the lease
        (lease_factory(lab_root)), the cutter (cutter_factory(folder, on_event)) or the cutter
        child's spool (cutter_options: attempt_cutter.CutterProcess's spool_factory, extra_path).
        ghost_loader(ref) loads a ghost's clip where the lab has no ghost catalog (interface C's
        shape; default: the player's own runs in the replay store, stage_ghosts.LocalGhosts)."""
        self.config_path = Path(config_path)
        self.session_options = session_options or {}
        self.live_on, self.lease_factory, self.cutter_options = live, lease_factory, cutter_options or {}
        self.cutter_factory = cutter_factory
        self.grow_seconds = grow_seconds
        self.live_audio = live_audio                       # live_audio.LiveAudio: the recorder's sound ring
        self.pages: set[_PageSocket] = set()               # the sockets a grown scene tells
        self.lease = None
        self.live_problem: str | None = None
        self.open_path: Path | None = None             # the package the open scene reads (retention spares it)
        self.open_attempt: int | None = None
        try:
            self.scenes = _lab_module("attempt_scenes").AttemptScenes(
                self._scene_folder, self._start_cutter, in_use=lambda: self.open_path, live_audio=live_audio)
        except ConfigError as error:
            self.scenes, self.live_problem = None, str(error)
        self.page_gone_seconds, self.watch_seconds = page_gone_seconds, watch_seconds
        self.idle_override = idle_seconds
        self.session = None
        self.worker: ThreadPoolExecutor | None = None      # the session's thread: all but finish
        self.delivery: ThreadPoolExecutor | None = None    # its second thread: finish, then the reply's bytes
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
        self.shared = None             # the desktop window's shared-memory pictures (attach_window)
        self.ghost_loader = ghost_loader

    def attach_window(self, window) -> None:
        """The desktop app's pywebview window: its page may then ask for encoding "shared" and get
        the renderer's exact pixels through WebView2 shared memory instead of a JPEG
        (tools/scene_lab/webview_pictures.py). A browser's page goes on getting JPEGs."""
        try:
            self.shared = _lab_module("webview_pictures").WebView2Pictures(window)
        except ConfigError:
            self.shared = None

    def _scene(self, described: dict) -> dict:
        scene = {"session": self.generation, **described, "shared_pictures": self.shared is not None}
        return scene | ({"attempt": self.open_attempt} if self.open_attempt is not None else {})

    # ------------------------------------------------------------ attempt scenes (live recording)

    def _lab_root(self) -> Path:
        config = LabConfig.load(self.config_path)
        return config.lab_root or _lab_module("attempt_scenes").default_lab_root()

    def _scene_folder(self) -> Path:
        return self._lab_root() / "attempts"

    def _start_cutter(self, folder: Path, on_event):
        if self.cutter_factory is not None:
            return self.cutter_factory(folder, on_event)
        config = LabConfig.load(self.config_path)
        return _lab_module("attempt_cutter").CutterProcess(config.rom, folder, on_event, lab_root=config.lab_root,
                                                           **self.cutter_options)

    def _start_live_blocking(self) -> None:
        if self.scenes is None or not self.live_on:
            return
        try:
            lab_root = LabConfig.load(self.config_path).lab_root
            if self.lease_factory is not None:
                self.lease = self.lease_factory(lab_root)
            elif (TOOLS / "live_spool.py").is_file():
                self.lease = _lab_module("live_spool").Lease(lab_root)
            else:
                self.live_problem = "This checkout has no live scene recording (tools/scene_lab/live_spool.py)."
                return
            self.lease.start()
        except Exception as error:  # noqa: BLE001 - the lab still opens packages without the live recording
            log.warning("The live scene recording could not start.", exc_info=True)
            self.lease, self.live_problem = None, f"The live scene recording could not start: {error}"
            return
        self.scenes.start()

    def _stop_live_blocking(self) -> None:
        if self.scenes is not None:
            self.scenes.stop()
        lease, self.lease = self.lease, None
        if lease is not None:
            lease.stop()

    async def start_live(self) -> None:
        """At server start: the lease that keeps the scene tap recording, and the attempt scenes."""
        await asyncio.to_thread(self._start_live_blocking)

    def live(self) -> dict:
        """{recording, reason} for the probe; no I/O (the cutter reports the recording's state)."""
        if not self.live_on:
            return {"recording": False, "reason": "This server does not run the live scene recording."}
        if self.live_problem:
            return {"recording": False, "reason": self.live_problem}
        live = self.scenes.live()
        if self.live_audio is not None:
            health = self.live_audio.health()
            live["sound"] = {"recording": health["recording"], "reason": health["reason"],
                             "kept_seconds": health["kept_seconds"]}
        return live

    def follow_attempts(self, tracker) -> None:
        """Every attempt the tracker completes, and every start the event stream announces, reach
        the attempt scenes. The tracker's completion hook (the replay's AttemptHistory.observe) is
        called first with the same arguments, so what the replay receives is unchanged; both
        callbacks run on the poll loop and the scenes' half only queues a note."""
        scenes = self.scenes
        if scenes is None:
            return
        previous = tracker.on_replay_history

        def observe(completed, active_start, **options):
            if previous is not None:
                previous(completed, active_start, **options)
            try:
                scenes.observe_completed(completed, active_start, **options)
            except Exception:  # noqa: BLE001 - a scene never costs tracking or the replay
                log.warning("An attempt did not reach the attempt scenes.", exc_info=True)

        tracker.on_replay_history = observe
        broadcaster = getattr(tracker, "broadcaster", None)
        if broadcaster is not None and hasattr(broadcaster, "observe"):
            broadcaster.observe(scenes.observe_event)

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
        report["session"] = self._scene(self.described) if self.session is not None and self.described else None
        report["ended"] = self.ended
        if self.loop_probe is not None:
            report["loop"] = self.loop_probe.summary()
        return report

    def _open_blocking(self, config: LabConfig, path: Path, sidecar: Path | None = None):
        module = _lab_module("scene_session")
        try:
            session = module.SceneSession.open(config, path, sidecar=sidecar, live=self.live_audio,
                                               fitter=self.scenes.fit if self.scenes is not None else None,
                                               **self.session_options)
        except module.Refused as refused:
            raise SceneRefused(str(refused)) from None
        return session, session.describe()

    async def open(self, name: str | None = None, *, attempt: int | None = None) -> dict:
        """A listed package by name, or an attempt's scene by its id (LookupError: none known;
        SceneRefused: still being cut, or never, with the reason)."""
        config = await asyncio.to_thread(LabConfig.load, self.config_path)
        sidecar = None
        if attempt is None:
            path = await asyncio.to_thread(config.package_path, name)
        elif self.scenes is None:
            raise SceneRefused(self.live_problem or "This lab has no attempt scenes.")
        else:
            try:
                path, sidecar = self.scenes.path(attempt)
            except _lab_module("attempt_scenes").SceneUnavailable as unavailable:
                raise SceneRefused(str(unavailable)) from None
        async with self.lock:
            await self._end("replaced")
            worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scene-lab")
            try:
                session, described = await asyncio.get_running_loop().run_in_executor(
                    worker, self._open_blocking, config, path, sidecar)
            except BaseException:
                worker.shutdown(wait=False)
                raise
            if attempt is not None and not described["audio"].get("available") and self.scenes is not None:
                quiet = self.scenes.sound_reason(attempt)          # the catalog knows why it is silent
                if quiet and described["audio"].get("reason") == _lab_module("scene_audio").NO_SOUND:
                    described = {**described, "audio": {**described["audio"], "reason": quiet}}
            self.session, self.worker, self.described = session, worker, described
            self.open_path, self.open_attempt = path, attempt
            self.delivery = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scene-lab-delivery")
            self.generation += 1
            self.idle_seconds = self.idle_override or config.idle_seconds
            self.last_used = self.page_seen = time.monotonic()
            self.ended = self.host_exit = None
            self._start_watch()
            return self._scene(described)

    async def close(self, code: str = "closed") -> None:
        async with self.lock:
            await self._end(code)

    async def _end(self, code: str, message: str | None = None) -> None:
        """Ends the open scene, if any: the close runs on its worker after any picture it began
        (the host's close collects answers no delivery took)."""
        session, worker, delivery = self.session, self.worker, self.delivery
        if session is None:
            return
        self.session = self.worker = self.delivery = self.described = None
        self.open_path = self.open_attempt = None
        self.ended = {"code": code, "message": message or ENDS[code], "name": session.name}
        try:
            self.host_exit = await asyncio.get_running_loop().run_in_executor(worker, session.close)
        except Exception:  # noqa: BLE001 - an end always completes; the host's own error is logged
            log.warning("The scene viewer's renderer did not close cleanly.", exc_info=True)
        finally:
            worker.shutdown(wait=False)
            delivery.shutdown(wait=False)
        if self.loop_probe is not None and self.loop_probe.task is not None:
            self.loop_probe.task.cancel()
            self.loop_probe.task = None

    async def shutdown(self) -> None:
        if self.watch_task is not None:
            self.watch_task.cancel()
        await self.close("shutdown")
        await asyncio.to_thread(self._stop_live_blocking)

    def _start_watch(self) -> None:
        loop = asyncio.get_running_loop()
        if self.watch_task is None or self.watch_task.done():
            self.watch_task = loop.create_task(self._watch(), name="scene-lab-watch")
        if self.described and not self._settled():
            loop.create_task(self._grow(self.generation), name="scene-lab-grow")
        if self.loop_probe is not None and self.loop_probe.task is None:
            self.loop_probe.task = loop.create_task(self.loop_probe.run(), name="scene-lab-loop-probe")

    def _settled(self) -> bool:
        session = self.session
        return session is None or getattr(session, "settled", not (self.described or {}).get("growing"))

    async def _grow(self, generation: int) -> None:
        """While the open scene is still being written (an attempt scene whose fade-out has not
        reached the recording yet) or its sound still comes from the recorder's ring: every
        GROW_SECONDS its worker takes the newest publication and sound, and each page showing it
        is told what it gained, {"kind": "grown", "session", "from", "frames", "growing", and
        "audio" when the sound's table changed}, until the scene is settled or closed."""
        failures = 0
        while self.session is not None and generation == self.generation:
            await asyncio.sleep(self.grow_seconds)
            try:
                grown = await self._run(generation, "grow")
                failures = 0
            except SceneClosed:
                return
            except (ValueError, struct.error) as error:          # a publication the reader refused
                failures += 1
                log.warning("A growing scene could not be read further: %s", error)
                if failures < GROW_FAILURES:
                    continue
                grown = {"from": len((self.described or {}).get("frames", ())), "frames": [], "growing": False}
            if grown:
                if self.described is not None and generation == self.generation:
                    frames = self.described["frames"][:grown["from"]] + grown["frames"]
                    self.described = {**self.described, "frames": frames, "growing": grown["growing"],
                                      **({"audio": grown["audio"]} if "audio" in grown else {})}
                message = {"kind": "grown", "session": generation, **grown}
                for page in list(self.pages):
                    try:
                        await page.send(message)
                    except Exception as error:  # noqa: BLE001 - a page that left misses nothing it needs
                        log.debug("a scene page did not hear the scene grow: %r", error)
            if self._settled() or failures >= GROW_FAILURES:
                return

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

    async def begin_picture(self, message: dict) -> asyncio.Future:
        """Starts one picture and returns the future of its reply (the message's bytes, or an error
        dict). The worker runs everything up to the host (session.begin); the delivery thread then
        reads and encodes it (session.finish) while the worker is free for the next request, so
        the game's code for the next camera runs while the renderer draws and the encoder
        compresses this one. Deliveries finish in the order they began."""
        ident, generation = message.get("id"), message.get("session")
        received = time.perf_counter()
        loop = asyncio.get_running_loop()
        try:
            pending = await self._run(generation, "begin", message.get("frame"), message.get("camera"),
                                      message.get("encoding", "jpeg"))
        except SceneClosed as closed:
            return _answered({"id": ident, "error": str(closed), "closed": True})
        except ValueError as error:
            return _answered({"id": ident, "error": str(error)})
        session, delivery = self.session, self.delivery
        if session is None or generation != self.generation:     # ended while the request was sent
            delivery = None
        self.busy += 1
        work = None if delivery is None else loop.run_in_executor(
            delivery, _delivered, session, pending, ident, generation, received, self.shared)
        return loop.create_task(self._delivery_reply(session, work, ident))

    async def _delivery_reply(self, session, work, ident):
        try:
            if work is None:
                raise SceneClosed((self.ended or {}).get("message") or "This scene is not open.")
            return await asyncio.shield(work)
        except SceneClosed as closed:
            return {"id": ident, "error": str(closed), "closed": True}
        except (RuntimeError, OSError) as error:
            message = f"The renderer stopped: {error}"
            async with self.lock:
                if session is self.session:
                    await self._end("renderer", message)
            return {"id": ident, "error": message, "closed": True}
        finally:
            self.busy -= 1
            self.last_used = time.monotonic()

    async def pose_reply(self, message: dict) -> dict:
        try:
            pose = await self._run(message.get("session"), "pose", message.get("frame"))
        except SceneClosed as closed:
            return {"id": message.get("id"), "error": str(closed), "closed": True}
        except ValueError as error:
            return {"id": message.get("id"), "error": str(error)}
        return {"id": message.get("id"), "kind": "pose", **pose}

    async def pick_reply(self, message: dict) -> dict:
        try:
            picked = await self._run(message.get("session"), "pick", message.get("frame"), message.get("camera"),
                                     message.get("u"), message.get("v"))
        except SceneClosed as closed:
            return {"id": message.get("id"), "error": str(closed), "closed": True}
        except ValueError as error:
            return {"id": message.get("id"), "error": str(error)}
        return {"id": message.get("id"), "kind": "pick", **picked}

    async def ghosts_reply(self, message: dict) -> dict:
        """The open scene's ghosts become the page's set (scene_session.set_ghosts), drawn with the
        player's ghost settings; every picture begun after this answer draws them."""
        session = self.session
        if session is not None and message.get("session") == self.generation:
            session.ghost_look = _lab_module("stage_ghosts").Look.of(self._ghost_values())
        try:
            answer = await self._run(message.get("session"), "set_ghosts", message.get("set"), self._load_ghost,
                                     message.get("origin"))
        except SceneClosed as closed:
            return {"id": message.get("id"), "error": str(closed), "closed": True}
        except ValueError as error:
            return {"id": message.get("id"), "error": str(error)}
        return {"id": message.get("id"), "kind": "ghosts", **answer}

    def _load_ghost(self, ref) -> dict:
        """Interface C: the ghost catalog's load_ghost (server/scene_ghosts.py) when the lab has
        one, else ghost_loader, else the player's own runs in the replay store."""
        catalog = getattr(self, "ghosts", None)
        loader = getattr(catalog, "load_ghost", None) or self.ghost_loader
        if loader is None:
            from sm64_events.core.paths import replays_root
            loader = self.ghost_loader = _lab_module("stage_ghosts").LocalGhosts(replays_root() / "native")
        return loader(ref)

    def _ghost_values(self) -> dict | None:
        """The player's ghost settings (the in-game ghost's), from the catalog's ghost session when
        the lab has one; None keeps the shipped defaults."""
        session = getattr(getattr(self, "ghosts", None), "ghost", None)
        try:
            return session.payload() if session is not None else None
        except Exception:  # noqa: BLE001 - the viewer draws with the defaults rather than not at all
            return None

    async def audio_block(self, generation: int, block: int) -> tuple[bytes, str]:
        """One block of the open scene's sound, decoded and checked off the session's worker."""
        session = self.session
        if session is None or generation != self.generation:
            raise SceneClosed((self.ended or {}).get("message") or "This scene is not open.")
        self.last_used = time.monotonic()
        return await asyncio.to_thread(session.audio_block, block)

    async def serve_socket(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.sockets += 1
        self.page_seen = time.monotonic()
        page = _PageSocket(self, websocket)
        self.pages.add(page)
        tasks = [asyncio.create_task(page.receive()), asyncio.create_task(page.answer()),
                 asyncio.create_task(page.deliver())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if not task.cancelled() and task.exception() is not None \
                        and not isinstance(task.exception(), WebSocketDisconnect):
                    log.warning("scene viewer socket ended: %r", task.exception())
        finally:
            for task in tasks:
                task.cancel()
            self.pages.discard(page)
            self.sockets -= 1
            self.page_seen = time.monotonic()
            # asyncio.wait, not gather: if the server is cancelling this handler, the
            # cancellation that propagates must stay the server's own, not a child's.
            await asyncio.wait(tasks)


def _answered(reply) -> asyncio.Future:
    future = asyncio.get_running_loop().create_future()
    future.set_result(reply)
    return future


def _delivered(session, pending, ident, generation, received: float, shared=None) -> bytes:
    """On the delivery thread: the host's picture for `pending`, encoded and packed as the page's
    binary message, so the event loop only sends it. A "shared" picture's pixels go to the desktop
    window's page through `shared` (attach_window), tagged with the request it answers."""
    share = None if shared is None else (lambda pixels: shared.post(pixels, {"id": ident, "session": generation}))
    header, payload = session.finish(pending, share)
    header.update(id=ident, session=generation, server_ms=round((time.perf_counter() - received) * 1000, 3))
    encoded = json.dumps(header).encode("utf-8")
    return b"".join((struct.pack("<I", len(encoded)), encoded, payload))


class _SendUncompressed:
    """A negotiated permessage-deflate that still reads what the page sends and no longer
    compresses what the server sends. uvicorn offers the extension on every socket (the app's
    default) and websockets then deflates each outgoing message on the event loop: measured
    2026-10-25, ~4.7 ms for a 236 KB JPEG picture and ~75 ms for an exact-pixel one, for pictures
    that do not shrink. RFC 7692 section 6 lets a sender leave any message uncompressed (RSV1
    clear); the page's browser inflates only messages marked compressed."""

    def __init__(self, extension):
        self.extension, self.name = extension, extension.name

    def decode(self, frame, *, max_size=None):
        return self.extension.decode(frame, max_size=max_size)

    def encode(self, frame):
        return frame


def _server_connection(send, limit: int = 16):
    """The uvicorn websockets connection behind an ASGI send: the send is its asgi_send bound
    method, or closures Starlette and middleware wrapped around it (wrap_app_handling_exceptions'
    sender); walked breadth-first through closure cells, at most `limit` callables."""
    pending, seen = [send], 0
    while pending and seen < limit:
        current = pending.pop(0)
        seen += 1
        owner = getattr(current, "__self__", None)
        if type(owner).__module__.startswith("uvicorn.protocols.websockets") \
                and isinstance(getattr(owner, "extensions", None), list):
            return owner
        for cell in getattr(current, "__closure__", None) or ():
            try:
                value = cell.cell_contents
            except ValueError:                       # an empty cell
                continue
            if callable(value):
                pending.append(value)
    return None


def _send_uncompressed(websocket: WebSocket) -> bool:
    """Swaps this socket's negotiated permessage-deflate for _SendUncompressed when the server is
    uvicorn's websockets protocol (the app's default; its connection keeps the negotiated
    extensions in a list). Call it once the page has sent a message: the handshake is done
    then. Any other server stack is left as it is; False says nothing changed."""
    connection = _server_connection(getattr(websocket, "_send", None))
    extensions = getattr(connection, "extensions", None)
    if not isinstance(extensions, list):
        return False
    for position, extension in enumerate(extensions):
        if getattr(extension, "name", None) == "permessage-deflate" and hasattr(extension, "decode"):
            extensions[position] = _SendUncompressed(extension)
            return True
    return False


class _PageSocket:
    """One page's socket: at most one picture request waiting (a newer one supersedes it), up
    to PICTURES_IN_FLIGHT pictures begun and not yet sent (answered in the order they began),
    and a short queue of questions (poses and picks), answered one at a time in that order,
    questions before a waiting picture."""

    def __init__(self, lab: SceneLab, websocket: WebSocket):
        self.lab, self.websocket = lab, websocket
        self.sending = asyncio.Lock()
        self.picture: dict | None = None
        self.questions: deque = deque()
        self.drawing: deque[asyncio.Future] = deque()   # replies of pictures begun, oldest first
        self.wake = asyncio.Event()                     # a request arrived, or a picture left
        self.begun = asyncio.Event()                    # a picture began
        self.uncompressed: bool | None = None       # set at the first send, after a message arrived

    async def send(self, reply) -> None:
        if self.uncompressed is None:
            self.uncompressed = _send_uncompressed(self.websocket)
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
            if message.get("kind") in ("pose", "pick", "ghosts"):
                if len(self.questions) >= MAX_QUESTIONS_WAITING:
                    await self.send({"id": self.questions.popleft().get("id"), "superseded": True})
                self.questions.append(message)
            else:
                replaced, self.picture = self.picture, message
                if replaced is not None:
                    await self.send({"id": replaced.get("id"), "superseded": True})
            self.wake.set()

    async def answer(self) -> None:
        """Questions, then the waiting picture once fewer than PICTURES_IN_FLIGHT are drawing."""
        while True:
            await self.wake.wait()
            self.wake.clear()
            while self.questions or (self.picture is not None and len(self.drawing) < PICTURES_IN_FLIGHT):
                if self.questions:
                    question = self.questions.popleft()
                    reply = {"pick": self.lab.pick_reply, "ghosts": self.lab.ghosts_reply}.get(
                        question.get("kind"), self.lab.pose_reply)
                    await self.send(await reply(question))
                else:
                    message, self.picture = self.picture, None
                    self.drawing.append(await self.lab.begin_picture(message))
                    self.begun.set()

    async def deliver(self) -> None:
        """Sends each begun picture's reply as it is ready, in the order they began."""
        while True:
            await self.begun.wait()
            self.begun.clear()
            while self.drawing:
                # Shielded: a page that leaves never cancels a delivery, whose host answer must be read.
                reply = await asyncio.shield(self.drawing[0])
                self.drawing.popleft()
                self.wake.set()                          # room for the waiting picture
                await self.send(reply)


class OpenBody(BaseModel):
    package: str | None = Field(default=None, min_length=1, max_length=255)
    attempt: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def one_of_them(self):
        if (self.package is None) == (self.attempt is None):
            raise ValueError("Name a package or an attempt.")
        return self


MAX_IDS = 500                  # attempt ids one attempt-scenes request may name


def _attempt_ids(text: str) -> list[int]:
    try:
        wanted = [int(part) for part in text.split(",") if part.strip()]
    except ValueError:
        raise HTTPException(400, "ids is a comma-separated list of attempt ids.") from None
    if len(wanted) > MAX_IDS:
        raise HTTPException(400, f"Ask for at most {MAX_IDS} attempts at once.")
    return wanted


def mount_scene_lab(app, config_path, **options) -> SceneLab:
    """Adds the scene viewer's routes to `app`, starts the live recording's lease and the attempt
    scenes when the app starts, and ends both and the open scene when it stops. `options` are
    SceneLab's (tests and e3_measure.py inject doubles and short timers)."""
    lab = SceneLab(Path(config_path), **options)
    router = APIRouter(prefix="/api/scene-lab")

    @router.get("")
    def probe():
        return {"lab": "scene-viewer", "live": lab.live()}

    @router.get("/status")
    async def status():
        return await lab.status()

    @router.get("/attempt-scenes")
    def attempt_scenes(ids: str = ""):
        wanted = _attempt_ids(ids)
        return {"scenes": lab.scenes.states(wanted) if lab.scenes is not None else {}}

    @router.post("/open")
    async def open_scene(body: OpenBody):
        try:
            return await lab.open(body.package, attempt=body.attempt)
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

    @router.get("/audio/{block}")
    async def audio(block: int, session: int):
        try:
            data, media_type = await lab.audio_block(session, block)
        except SceneClosed as closed:
            raise HTTPException(409, str(closed)) from None
        except LookupError as missing:
            raise HTTPException(404, str(missing)) from None
        except ValueError as damaged:
            raise HTTPException(422, f"This part of the recording's sound is damaged: {damaged}") from None
        return Response(data, media_type=media_type, headers={"Cache-Control": "no-store"})

    app.include_router(router)
    serving = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with serving(application) as state:
            await lab.start_live()
            try:
                yield state
            finally:
                await lab.shutdown()

    app.router.lifespan_context = lifespan
    app.state.scene_lab = lab
    return lab
