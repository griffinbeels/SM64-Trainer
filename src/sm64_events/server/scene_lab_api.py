"""The scene viewer's routes (M4 E3; the Ghost Lab): one recording in its reconstructed stage, in the Trainer.

None of this exists unless SM64_SCENE_LAB names a lab config (a developer's), or this is the installed app with
the Ghost Lab on (core/lab_inputs.py IN_APP, which then builds the config from the app's own inputs, no lab.json):
main.py::_mount_scene_lab is the one reader, and otherwise this module is never imported and no route is added.
With it:

    GET  /api/scene-lab           the probe the shell waits for before it shows the Lab entry, and
                                  "live": whether the scene recording runs ({recording, on, switch, reason})
    POST /api/scene-lab/recording {on: bool}: the Scene viewer's switch; turns the live recording's
                                  lease on or off (off: the wrapper stops within a second) -> {live}
    GET  /api/scene-lab/status    the config's readiness, the packages, the open scene, the last end
    GET  /api/scene-lab/attempt-scenes?ids=12,13   each known attempt's scene: ready, cutting, unavailable
    POST /api/scene-lab/open      {package: <a listed name>} or {attempt: <id>} -> the scene (with its
                                  "title" and "target"); 409 with the reason. {ghost: <a ref /ghosts
                                  listed>} or {stage: {course, act[, area]}}: a scene the simulation makes
                                  (server/scene_resim.py), its "resim" table saying how (start, parting_frame,
                                  save_states)
    GET  /api/scene-lab/stage?course=&star=&strat=   the stage for a target and strategy (scene_ghosts.py)
    GET  /api/scene-lab/ghosts?section=&...&page=    one page of the ghosts that could be drawn into it
                                  (section=top&strategies=all: every strategy's top ghosts at once)
    GET  /api/scene-lab/ghost?recording=&course=&star=   one recording by its id as a listed ghost
                                  (the Library's ghost button)
    GET  /api/scene-lab/camera-presets?level=&area=  the saved cameras of that level and area (slots 1-9);
                                  PUT the same {slots} to replace them (tools/scene_lab/camera_presets.py)
    GET  /api/scene-lab/camera-paths?level=&area=    the camera path of that level and area ({keys});
                                  PUT the same {keys} to replace it (tools/scene_lab/camera_paths.py)
    GET  /api/scene-lab/actions?session=N&target=stage   every frame's action of the stage's Mario or a
                                  scene ghost (its ref), for the step to the next action change
    POST /api/scene-lab/close
    WS   /api/scene-lab/ws        pictures and poses of the open scene (protocol below)
    GET  /api/scene-lab/audio/{block}?session=N[&track=music|effects|ghost:<ref>]   one block of the open scene's sound
    POST /api/scene-lab/export, GET /export/{job}, POST /export/{job}/cancel   a recording made into a video
                                  (server/scene_exports.py)
    POST /api/scene-lab/moment, POST /moment/{id}/write   one frame as a Usamune save state, into a slot of the
                                  running game (server/scene_moments.py; the lab's one write to game memory)

The lab config is a private JSON file of absolute paths: {"rom", "packages" (a folder of
.sm64scene files), "pj64_plugin", "map" (the free camera's MappingUS.map), and optionally
"renderer", "resolution" ("WxH"), "idle_seconds", "lab_root" (where the live scene recording
and the attempt scenes live; %LOCALAPPDATA%\\SM64Trainer\\scene-lab when absent)}. The installed app
has none: LabConfig.from_app takes the same fields from core/lab_inputs.py (the ROM store, the bundled
maps, the Plugin folder setup found), read again on every use, so a ROM found while the app runs counts
at once; until then each door answers with the sentence why (LabConfig.problems). The page names a
package only by a name the server listed from that folder, or an attempt by its id; no path or
URL ever comes from the page. Packages stay untrusted: package.py's reader checks every record,
and the evaluator executes code only from the configured ROM. The scene lab's code
(tools/scene_lab) runs from a source checkout, and in the installed app from its bundle
(sm64_events/scenelab_code.py, interface I1).

Attempt scenes (round 2, "IMMEDIATELY AFTER FINISHING AN ATTEMPT"): while the server runs and the
Scene viewer's recording switch is on (it shows that the recording runs; off is kept for the next
server in the lab root's RECORDING_SWITCH), the lab holds live_spool.Lease (the wrapper's scene tap
records into the live spool's fixed files only while it is held) and
attempt_scenes.AttemptScenes, whose cutter child writes each completed attempt's scene from that
spool (tools/scene_lab/attempt_cutter.py; the span: attempt_span.py). main.py chains the
tracker's completion hook and the broadcaster's observer to it (follow_attempts); the replay's
hook is called first and unchanged.

Nothing runs until a page opens a package. The session (tools/scene_lab/scene_session.py)
starts the renderer host after the GPU hold check and the evaluator once its first picture is out
and the page is idle (_warm; else on the first free-camera request). Each session owns two threads, and the event loop only awaits them: the worker runs
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
xyz, "position": xyz[, "roll", "fov"][, "recorded": weight]} (a camera path's blend toward the
recorded camera, scene_session.recorded_blend) or {"ghost": ref} or {"rig": {...}}, "encoding":
"jpeg"|"raw"|"shared"[, "hud": false][, "mario": false]} for a picture ("mario": false leaves the
stage's Mario out, its header then "mario": false; {"ghost": ref}: that scene ghost's own
recorded camera at this picture, its header's "view" {"ghost", "camera"} the camera drawn; {"rig":
{"kind", "target", "shot", "height", "radius", "speed", "phase", "side", "tuning"}}: a cinematic
camera following the stage's Mario or a scene ghost, its header's "rig" what it did,
tools/scene_lab/camera_rig.py; every header's "camera_used" is the camera the server drew, null for
the recorded one; scene_session.py; hud false: without the
game's HUD, tools/scene_lab/hud.py; "shared":
the desktop window's page only, once attach_window gave the scene its window; the exact pixels
go through WebView2 shared memory and the binary reply is the header alone, or a JPEG when the
post failed, as its header's encoding says), {"id",
"session", "kind": "pose", "frame"} for the recorded camera and Mario, {"id", "session", "kind":
"pick", "frame", "camera", "u", "v"} for where the picture drew a point (u, v from 0 to 1 across
and down; Set focus), and {"id", "session", "kind": "ghosts", "set": [{"ref", "label"}][, "origin":
a frame]} for the ghosts every later picture draws (the whole set, replacing the last; [] removes
them), answered {"id", "kind": "ghosts", "shown": [refs], "refused": [{"ref", "reason"}]} once
each new ghost's recording is in the renderer, and {"id", "session", "kind": "rig-path"} for the
cinematic cameras' path of the stage's Mario, read in a child process and answered {"id", "kind":
"rig-path", "ready", "reason"} once it is (a rig picture asked before then shows the recorded
camera and says so) (scene_session.set_ghosts; clips come from the
ghost catalog's load_ghost, server/scene_ghosts.py, unless `ghost_loader` replaces it). A set never
holds up the page's pictures: its clips load on threads of their own (a community ghost may
download for up to a minute) and sets apply in the order they came. A picture of a scene
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
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi import Path as FastPath
from pydantic import BaseModel, Field, model_validator

from sm64_events.core import lab_inputs

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
_HIS_LIBRARY = object()       # follow_spawns' default: the Slot Library in his user data
WARM_WAIT_SECONDS = 30.0      # how long a scene's first picture waits for an idle page to warm the free camera
WARM_QUIET_SECONDS = 0.5      # idle = no picture asked for this long (Play asks one every frame, so it never is)
WARM_POLL_SECONDS = 0.1
ENDS = {"closed": "Closed.",
        "replaced": "Closed to open another scene.",
        "page": "Closed because no page was showing it.",
        "idle": "Closed after a while without use, to free the renderer.",
        "shutdown": "Closed when the server stopped."}
RECORDING_SWITCH = "recording-switch.json"   # in the lab root: {"on": false} once he turned the recording off
RECORDING_OFF = "Scene recording is off. Turn it on to save a scene of each attempt."


def _read_switch(lab_root: Path) -> bool:
    """The Scene viewer's recording switch as he last left it; on unless he turned it off."""
    try:
        return json.loads((Path(lab_root) / RECORDING_SWITCH).read_text(encoding="utf-8")).get("on") is not False
    except (OSError, ValueError, AttributeError):
        return True


def _write_switch(lab_root: Path, on: bool) -> None:
    Path(lab_root).mkdir(parents=True, exist_ok=True)
    (Path(lab_root) / RECORDING_SWITCH).write_text(json.dumps({"on": bool(on)}), encoding="utf-8")


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
    rom: Path | None                   # None only from_app, until the ROM store holds one (rom_problem says why)
    packages: Path
    pj64_plugin: Path | None = None
    map: Path | None = None
    renderer: str = "trainer"
    resolution: tuple[int, int] | None = None
    idle_seconds: float = DEFAULT_IDLE_SECONDS
    lab_root: Path | None = None
    in_app: bool = False               # from_app: the installed app's own inputs, no lab.json
    app_problems: tuple[str, ...] = ()   # from_app: what stops any scene, in the words the page shows
    rom_problem: str | None = None     # from_app: why there is no ROM yet
    roms: dict = field(default_factory=dict)   # from_app: each region's ROM ({"us", "jp"}: Path or None)
    maps: dict = field(default_factory=dict)   # from_app: each region's map; a scene opens with its own pair

    @classmethod
    def from_app(cls, inputs: dict) -> "LabConfig":
        """The installed app's lab, from core/lab_inputs.py::app_inputs' dict: the ROM the lab runs (None until the
        ROM store holds one), its bundled map, the Plugin folder setup found; the packages folder is the lab
        root's own (a developer's list; the app's lab works without it)."""
        from sm64_events.core.paths import scene_lab_root
        root = Path(inputs["lab_root"]) if inputs.get("lab_root") else None
        rom = Path(inputs["rom"]) if inputs.get("rom") else None
        rom_problem = None if rom is not None else ((inputs.get("rom_problems") or {}).get("us") or lab_inputs.NO_ROM)
        return cls(rom, (root or scene_lab_root()) / "packages",
                   Path(inputs["pj64_plugin"]) if inputs.get("pj64_plugin") else None,
                   Path(inputs["map"]) if inputs.get("map") else None, lab_root=root, in_app=True,
                   app_problems=tuple(inputs.get("problems") or ()), rom_problem=rom_problem,
                   roms={region: Path(found) if found else None for region, found in (inputs.get("roms") or {}).items()},
                   maps={region: Path(found) if found else None for region, found in (inputs.get("maps") or {}).items()})

    def require_rom(self) -> Path:
        """The ROM; ConfigError with the sentence why when there is none yet (the installed app before its ROM
        store found one)."""
        if self.rom is None:
            raise ConfigError(self.rom_problem or lab_inputs.NO_ROM)
        return self.rom

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
        if self.in_app:
            return list(self.app_problems)
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
    """A tools/scene_lab module, imported only when a page needs it: from this checkout, or, in the installed app,
    the one its bundle carries (sm64_events/scenelab_code.py, interface I1)."""
    if getattr(sys, "frozen", False):
        try:
            from sm64_events.scenelab_code import lab_module
            return lab_module(name)
        except ImportError:
            log.warning("The Ghost Lab's %s did not load from the bundle.", name, exc_info=True)
            raise ConfigError("This copy of the Trainer is missing part of the Ghost Lab. "
                              "Reinstall the Trainer to get it back.") from None
    if not (TOOLS / f"{name}.py").is_file():
        raise ConfigError("The scene lab runs from a source checkout, and this one has no tools/scene_lab.")
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    return __import__(name)


class RomWait:
    """The attempt scenes' cutter in an installed app whose ROM store has no ROM yet (attempt_scenes takes anything
    with send and close). The recording reads the ROM's sentence; notes wait, the newest KEPT of them; every
    POLL_SECONDS start() is asked again on a thread of its own (never the poll loop, which only calls send), and the
    first cutter it returns gets the waiting notes in order and every later one. The spool records all along (its
    lease needs no ROM), so the attempts played before the ROM turned up are cut too while the spool still holds them."""

    POLL_SECONDS = 5.0
    KEPT = 256

    def __init__(self, start, reason, on_event, *, poll_seconds: float | None = None):
        self.start, self.cutter = start, None
        self.notes: deque = deque(maxlen=self.KEPT)
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.poll_seconds = self.POLL_SECONDS if poll_seconds is None else poll_seconds
        on_event({"live": {"recording": False, "reason": reason() or lab_inputs.NO_ROM}})
        threading.Thread(target=self._wait, name="scene-cutter-rom-wait", daemon=True).start()

    def send(self, note: dict) -> None:
        with self.lock:
            cutter = self.cutter
            if cutter is None:
                self.notes.append(note)
        if cutter is not None:
            cutter.send(note)

    def close(self, timeout: float = 10.0):
        self.stopped.set()
        with self.lock:
            cutter = self.cutter
        return cutter.close(timeout) if cutter is not None else None

    def _wait(self) -> None:
        while not self.stopped.wait(self.poll_seconds):
            try:
                cutter = self.start()
            except Exception:  # noqa: BLE001 - asked again at the next poll; the log keeps why
                log.warning("The scene cutter could not start after the ROM turned up.", exc_info=True)
                continue
            if cutter is None:
                continue
            with self.lock:                                # a cutter's send only queues, so the order holds
                if not self.stopped.is_set():
                    for note in self.notes:
                        cutter.send(note)
                    self.notes.clear()
                    self.cutter = cutter
            if self.stopped.is_set() and self.cutter is not cutter:
                cutter.close()
            return


class RegionCutter:
    """The installed app's cutter, on the ROM of the cartridge Project64 runs. `start(region)` makes a cutter for that
    region's ROM (None while the store has none: RomWait waits for it); `game_region()` is the running cartridge's
    region, or None while no game is read (then the lab's own ROM, US first). An attempt that starts on the other
    cartridge ends this cutter (on a thread of its own: send runs on the poll loop) and starts that region's; the
    notes wait in order meanwhile, and the spool keeps the recording, so nothing played is lost."""

    def __init__(self, start, game_region, reason, on_event, *, poll_seconds: float | None = None):
        self.start, self.game_region, self.reason, self.on_event = start, game_region, reason, on_event
        self.poll_seconds = poll_seconds
        self.lock = threading.Lock()
        self.region = self._region()
        self.inner = self._make(self.region, announce=True)

    def _region(self) -> str | None:
        try:
            region = self.game_region()
        except Exception:  # noqa: BLE001 - an unread game reads as "no game"
            region = None
        return region if region in ("us", "jp") else None

    def _make(self, region, *, announce: bool):
        cutter = self.start(region)
        if cutter is not None:
            return cutter
        return RomWait(lambda: self.start(region), self.reason, self.on_event if announce else (lambda event: None),
                       poll_seconds=self.poll_seconds)

    def send(self, note: dict) -> None:
        old = None
        if note.get("op") == "start":
            region = self._region()
            with self.lock:
                if region is not None and region != self.region:
                    old, self.region = self.inner, region
                    self.inner = RomWait(lambda: self.start(region), self.reason, lambda event: None,
                                         poll_seconds=0.05 if self.poll_seconds is None else self.poll_seconds)
        if old is not None:
            threading.Thread(target=old.close, name="scene-cutter-region", daemon=True).start()
        with self.lock:
            inner = self.inner
        inner.send(note)

    def close(self, timeout: float = 10.0):
        with self.lock:
            inner = self.inner
        return inner.close(timeout)


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

    ghosts = None                  # scene_ghosts.SceneGhosts: the stage and the ghost catalog (mount_scene_lab)
    refitter = None                # slotlibrary.refit.SlotRefitter: the live server's slots follow each spawn
    _slot_loop = None              # the app's event loop, for the star sets' broadcasts from the slot worker
    heading = {"title": "Lab recording", "target": None}     # the open scene's, as _heading says it

    def __init__(self, config_path: Path | None, *, session_options: dict | None = None,
                 page_gone_seconds: float = PAGE_GONE_SECONDS, watch_seconds: float = WATCH_SECONDS,
                 idle_seconds: float | None = None, measure_loop: bool = False, live: bool = False,
                 lease_factory=None, cutter_factory=None, cutter_options: dict | None = None,
                 grow_seconds: float = GROW_SECONDS, live_audio=None, ghost_loader=None, app_inputs=None,
                 game_region=None):
        """config_path: a developer's lab.json; None: the installed app's own inputs, app_inputs() ->
        core/lab_inputs.py::app_inputs' dict, read on every config() (LabConfig.from_app).
        live: hold the recording's lease and cut attempt scenes (main.py's server only; a test,
        the UI fixture or e3_measure.py never arms the wrapper's tap). Tests replace the lease
        (lease_factory(lab_root)), the cutter (cutter_factory(folder, on_event)) or the cutter
        child's spool (cutter_options: attempt_cutter.CutterProcess's spool_factory, extra_path).
        ghost_loader(ref) loads a ghost's clip in place of the ghost catalog's (interface C's
        shape; without either, the player's own runs in the replay store, stage_ghosts.LocalGhosts)."""
        self.config_path = Path(config_path) if config_path is not None else None
        self.app_inputs = app_inputs or lab_inputs.app_inputs
        # The region ("us"/"jp") of the cartridge Project64 runs now, or None (main.py: the poller's header read): the
        # installed app's cutter cuts with that region's ROM (RegionCutter).
        self.game_region = game_region or (lambda: None)
        self.session_options = session_options or {}
        self.live_on, self.lease_factory, self.cutter_options = live, lease_factory, cutter_options or {}
        self.cutter_factory = cutter_factory
        self.grow_seconds = grow_seconds
        self.live_audio = live_audio                       # live_audio.LiveAudio: the recorder's sound ring
        self.pages: set[_PageSocket] = set()               # the sockets a grown scene tells
        self.lease = None
        self.live_problem: str | None = None
        self.recording_on = True                           # the Scene viewer's switch (RECORDING_SWITCH)
        self.switch_lock = threading.Lock()
        self.open_path: Path | None = None             # the package the open scene reads (retention spares it)
        self.open_attempt: int | None = None
        self.open_resim: dict | None = None                # how a re-simulated scene was made (scene_resim.py)
        self.resim = None                                  # scene_resim.SceneResim, made by mount_scene_lab
        self.tracker = None                                # follow_attempts': whose journal the scenes belong to
        try:
            self.scenes = _lab_module("attempt_scenes").AttemptScenes(
                self._scene_folder, self._start_cutter, in_use=lambda: self.open_path, live_audio=live_audio,
                journal=self._journal, legacy=lambda: self._lab_root() / "attempts")
        except ConfigError as error:
            self.scenes, self.live_problem = None, str(error)
        self.page_gone_seconds, self.watch_seconds = page_gone_seconds, watch_seconds
        self.idle_override = idle_seconds
        self.session = None
        self.worker: ThreadPoolExecutor | None = None      # the session's thread: all but finish
        self.delivery: ThreadPoolExecutor | None = None    # its second thread: finish, then the reply's bytes
        self.described: dict | None = None
        self.generation = 0
        self.warmed = 0                                    # the newest scene whose free camera was warmed
        self.asked_at = 0.0                                # when the page last asked for a picture
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
        self._ghost_turn: asyncio.Future | None = None   # the newest ghost set's turn to apply

    def attach_window(self, window) -> None:
        """The desktop app's pywebview window: its page may then ask for encoding "shared" and get
        the renderer's exact pixels through WebView2 shared memory instead of a JPEG
        (tools/scene_lab/webview_pictures.py). A browser's page goes on getting JPEGs."""
        try:
            self.shared = _lab_module("webview_pictures").WebView2Pictures(window)
        except ConfigError:
            self.shared = None

    def _scene(self, described: dict) -> dict:
        scene = {"session": self.generation, **described, "shared_pictures": self.shared is not None, **self.heading}
        scene |= {"resim": self.open_resim} if self.open_resim is not None else {}
        return scene | ({"attempt": self.open_attempt} if self.open_attempt is not None else {})

    def _heading(self, attempt: int | None) -> dict:
        """The open scene's "title" and "target" (scene_ghosts.SceneGhosts.heading); blocking.
        A title never stops a scene from opening: without one it says what was opened."""
        try:
            if self.ghosts is not None:
                return self.ghosts.heading(attempt)
        except Exception:  # noqa: BLE001 - the journal's names are a nicety beside the scene
            log.warning("The scene's title could not be read.", exc_info=True)
        return {"title": "Lab recording" if attempt is None else f"Attempt {attempt}", "target": None}

    def config(self) -> LabConfig:
        """The lab config as it stands now (blocking: a file read): the developer's lab.json, read again on every
        use as it always was, or the installed app's own inputs (LabConfig.from_app). ConfigError says why it
        cannot be read."""
        if self.config_path is not None:
            return LabConfig.load(self.config_path)
        return LabConfig.from_app(self.app_inputs())

    # ------------------------------------------------------------ attempt scenes (live recording)

    def _lab_root(self) -> Path:
        return self.config().lab_root or _lab_module("attempt_scenes").default_lab_root()

    def _journal(self):
        """The journal the attempt ids belong to: the followed tracker's database, else the one the
        stage reads (main.py hands both the same tracker); None without one."""
        if self.tracker is not None:
            return getattr(self.tracker, "db", None)
        return self.ghosts.db if self.ghosts is not None else None

    def _scene_folder(self) -> Path:
        """This journal's own folder of attempt scenes (attempt_scenes.journal_folder)."""
        return _lab_module("attempt_scenes").journal_folder(self._lab_root() / "attempts", self._journal())

    def _start_cutter(self, folder: Path, on_event):
        resim = self.resim

        def observed(event, on_event=on_event):
            on_event(event)
            if resim is not None:
                try:
                    resim.observe_cutter(event)
                except Exception:  # noqa: BLE001 - a start not kept never costs a scene
                    log.warning("A ready scene's ghost start was not queued.", exc_info=True)
        on_event = observed
        if self.config_path is None:
            return self._app_cutter(folder, on_event)
        if self.cutter_factory is not None:
            return self.cutter_factory(folder, on_event)
        config = LabConfig.load(self.config_path)
        return _lab_module("attempt_cutter").CutterProcess(config.rom, folder, on_event, lab_root=config.lab_root,
                                                           symbol_map=config.map, **self.cutter_options)

    def _app_cutter(self, folder: Path, on_event):
        """The installed app's cutter: the child at once when the ROM store holds a ROM, else RomWait, which starts it
        the moment one turns up (D1: the store copies it from Project64 the first time he plays), so a new player's
        first session still gets its attempt scenes without a restart. It cuts with the ROM of the game being played
        (RegionCutter): a player who keeps both ROMs and plays JP gets JP scenes, not JP play read against US code."""
        def start(region: str | None):
            config = self.config()
            rom = config.roms.get(region) if region in config.roms else config.rom
            if rom is None:
                return None
            symbol_map = config.maps.get(region) if region in config.maps else config.map
            if self.cutter_factory is not None:
                return self.cutter_factory(folder, on_event)
            return _lab_module("attempt_cutter").CutterProcess(rom, folder, on_event, lab_root=config.lab_root,
                                                               symbol_map=symbol_map, **self.cutter_options)
        return RegionCutter(start, self.game_region, lambda: self.config().rom_problem, on_event)

    def _start_live_blocking(self) -> None:
        if self.scenes is None or not self.live_on:
            return
        try:
            lab_root = self.config().lab_root
            self.recording_on = _read_switch(self._lab_root())
            if self.lease_factory is not None:
                self.lease = self.lease_factory(lab_root)
            elif getattr(sys, "frozen", False) or (TOOLS / "live_spool.py").is_file():
                self.lease = _lab_module("live_spool").Lease(lab_root)
            else:
                self.live_problem = "This checkout has no live scene recording (tools/scene_lab/live_spool.py)."
                return
            if self.recording_on:
                self.lease.start()
        except Exception as error:  # noqa: BLE001 - the lab still opens packages without the live recording
            log.warning("The live scene recording could not start.", exc_info=True)
            self.lease, self.live_problem = None, f"The live scene recording could not start: {error}"
            return
        self.scenes.start()

    def set_recording_blocking(self, on: bool) -> dict:
        """The Scene viewer's switch: on renews the lease again, off deletes it, so the wrapper stops
        recording within a second of its next check (live_spool.Lease). The cutter keeps serving the
        scenes already cut; an attempt finished while it is off has none, and says so. The choice is
        kept in the lab root (RECORDING_SWITCH) for the next server. Blocking: run it off the loop."""
        if not self.live_on or self.lease is None:
            raise SceneRefused(self.live()["reason"])
        with self.switch_lock:
            _write_switch(self._lab_root(), on)
            if on and not self.recording_on:
                self.lease.start()
            elif not on and self.recording_on:
                self.lease.stop()
            self.recording_on = on
        return self.live()

    def _stop_live_blocking(self) -> None:
        if self.scenes is not None:
            self.scenes.stop()
        lease, self.lease = self.lease, None
        if lease is not None:
            lease.stop()

    async def start_live(self) -> None:
        """At server start: the lease that keeps the scene tap recording, the attempt scenes, and the slot
        refit's thread."""
        await asyncio.to_thread(self._start_live_blocking)
        self._slot_loop = asyncio.get_running_loop()
        if self.refitter is not None:
            self.refitter.start()

    def live(self) -> dict:
        """{recording, on, switch, reason} for the probe; no I/O (the cutter reports the recording's
        state). `on`: the Trainer asks for the recording (the Scene viewer's switch); `recording`: the
        wrapper is writing it now; `switch`: this server can turn it on and off."""
        if not self.live_on:
            return {"recording": False, "on": False, "switch": False,
                    "reason": "This server does not run the live scene recording."}
        if self.live_problem:
            return {"recording": False, "on": False, "switch": False, "reason": self.live_problem}
        switch = self.lease is not None                    # none until the server's start made it
        if not self.recording_on:
            return {"recording": False, "on": False, "switch": switch, "reason": RECORDING_OFF}
        live = self.scenes.live() | {"on": True, "switch": switch}
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
        self.tracker = tracker
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

    def follow_spawns(self, tracker, *, game=None, library=_HIS_LIBRARY, log_file=None, atlases=None) -> None:
        """After every spawn the event stream announces, the slots made for that place get Mario's new place
        (slotlibrary/refit.py). The observer only stamps a deadline; the refitter's own thread and reader do the
        work, started and stopped with the app. Tests inject the game, the library and the log file.

        library=None keeps nothing, as SlotRefitter reads it; only leaving it out means his own Slot Library. Until
        2026-11-08 None meant his library too, and the attempt-scenes test's fake slot (fake fingerprint d3fca85f)
        landed in his real library as a captured BoB state."""
        broadcaster = getattr(tracker, "broadcaster", None)
        if broadcaster is None or not hasattr(broadcaster, "observe"):
            return
        from sm64_events.slotlibrary import refit, usamune
        from sm64_events.slotlibrary.atlas import default_store
        from sm64_events.slotlibrary.coursesets import CourseSets
        from sm64_events.slotlibrary.keeper import SetKeeper
        if library is _HIS_LIBRARY:
            from sm64_events.core.paths import slot_library_dir
            from sm64_events.slotlibrary.store import SlotLibrary
            library = SlotLibrary(slot_library_dir())
            atlases = default_store() if atlases is None else atlases
        keeper = None if library is None else SetKeeper(
            library, CourseSets(library.root / "course-sets.json"), atlases,
            target=lambda: getattr(tracker, "target", None), on_change=self._slot_sets_changed)
        self.refitter = refit.SlotRefitter(game or (lambda: usamune.attach(None)), library,
                                           log_file=log_file or refit.log_path(), keeper=keeper)
        broadcaster.observe(self.refitter.observe)
        self._slot_broadcaster = broadcaster

    def _slot_sets_changed(self, payload: dict) -> None:
        """From the slot worker's thread: the star sets' answer goes out on the app's socket, broadcast only."""
        loop, broadcaster = self._slot_loop, getattr(self, "_slot_broadcaster", None)
        if loop is None or broadcaster is None or loop.is_closed():
            return
        from sm64_events.core.events import Event
        event = Event(type="slot_sets_changed", frame=0, timestamp_utc=datetime.now(timezone.utc), payload=payload)
        asyncio.run_coroutine_threadsafe(broadcaster.publish(event), loop)

    def slot_sets(self):
        """The star sets' keeper, or None on a server that does not follow spawns."""
        return None if self.refitter is None else self.refitter.keeper

    async def frame_picture(self, path, package_frame: int) -> bytes | None:
        """The open scene's picture of one of its package's frames, the recorded camera's, as a JPEG: the picture a
        save state made from that frame keeps (server/scene_moments.py). None when that package is not the open
        scene, the frame is not in it, or the renderer could not draw it. It goes through the session's worker and
        delivery thread like any picture the page asks for, so it never meets one of the page's halfway."""
        session, generation, delivery = self.session, self.generation, self.delivery
        if session is None or delivery is None or self.open_path is None or Path(self.open_path) != Path(path):
            return None
        try:
            position = list(session.frames).index(package_frame)
        except (AttributeError, TypeError, ValueError):
            return None
        try:
            pending = await self._run(generation, "begin", position, None, "jpeg", True, True)
            header, payload = await asyncio.get_running_loop().run_in_executor(delivery, session.finish, pending)
        except (SceneClosed, ValueError, RuntimeError) as failed:
            log.info("A save state's frame picture was not drawn: %s", failed)
            return None
        return payload if header.get("encoding") == "jpeg" and payload else None

    # ------------------------------------------------------------ status, open, close

    def _status_blocking(self) -> dict:
        try:
            config = self.config()
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
        report["resim"] = self.resim.progress if self.resim is not None else None   # a first build, while it runs
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

    async def open(self, name: str | None = None, *, attempt: int | None = None, ghost: str | None = None,
                   stage: dict | None = None) -> dict:
        """A listed package by name, or an attempt's scene by its id (LookupError: none known;
        SceneRefused: still being cut, or never, with the reason); or a listed ghost, or a course and act,
        re-simulated into a scene (scene_resim.py; the build runs before the open scene is replaced)."""
        config = await asyncio.to_thread(self.config)
        config.require_rom()                               # the installed app before it found a ROM: the sentence why
        sidecar = resim = None
        if ghost is None and self.resim is not None:      # one build at a time: a newer open ends an older one
            await asyncio.to_thread(self.resim.cancel)     # (a ghost's own open joins or ends it, scene_resim.py)
        if ghost is not None or stage is not None:
            path, resim, sidecar = await self._resim_source(ghost, stage)   # a ghost: as soon as it starts playing
        elif attempt is None:
            path = await asyncio.to_thread(config.package_path, name)
        elif self.scenes is None:
            raise SceneRefused(self.live_problem or "This lab has no attempt scenes.")
        else:
            try:
                path, sidecar = self.scenes.path(attempt)
            except _lab_module("attempt_scenes").SceneUnavailable as unavailable:
                raise SceneRefused(str(unavailable)) from None
        heading = await asyncio.to_thread(self._heading, attempt)
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
            self.open_path, self.open_attempt, self.open_resim = path, attempt, resim
            self.heading = heading
            self.delivery =ThreadPoolExecutor(max_workers=1, thread_name_prefix="scene-lab-delivery")
            self.generation += 1
            self.idle_seconds = self.idle_override or config.idle_seconds
            self.last_used = self.page_seen = time.monotonic()
            self.ended = self.host_exit = None
            self._start_watch()
            return self._scene(described)

    async def _resim_source(self, ghost: str | None, stage: dict | None) -> tuple[Path, dict, Path | None]:
        """(package, its resim table, the sidecar of a scene still being built) for a ghost or a stage;
        SceneRefused with the sentence otherwise. A ghost answers at its first published frames."""
        from sm64_events.server.scene_resim import ResimRefused
        try:
            if ghost is not None:
                return await asyncio.to_thread(self.resim.ghost_scene_blocking, ghost, progressive=True)
            path, info = await asyncio.to_thread(self.resim.stage_scene_blocking, stage["course"], stage["act"],
                                                 stage.get("area") or 1)
            return path, info, None
        except ResimRefused as refused:
            raise SceneRefused(str(refused)) from None

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
        self.open_path = self.open_attempt = self.open_resim = None
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
        if self.resim is not None:
            self.resim.shutdown()                          # its child, by its handle
        if self.watch_task is not None:
            self.watch_task.cancel()
        await self.close("shutdown")
        await asyncio.to_thread(self._stop_live_blocking)
        if self.refitter is not None:
            await asyncio.to_thread(self.refitter.stop)

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
        reached the recording yet, a ghost's scene still being re-simulated) or its sound still comes
        from the recorder's ring: every GROW_SECONDS its worker takes the newest publication and sound,
        and each page showing it is told what it gained, {"kind": "grown", "session", "from", "frames",
        "growing", "audio" when the sound's table changed, and "resim" for a ghost's scene still being
        built: its newest table, the final one with the last message}, until the scene is settled or
        closed."""
        failures = 0
        while self.session is not None and generation == self.generation:
            await asyncio.sleep(self.grow_seconds)
            if (self.open_resim or {}).get("building") and self.resim.was_cancelled(self.open_path):
                await self._stop_growing(generation)       # its build was cancelled: it keeps what it has
                return
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
            if grown and (self.open_resim or {}).get("building") and not await self._resim_grown(grown, generation):
                return
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

    async def _resim_grown(self, grown: dict, generation: int) -> bool:
        """A ghost's scene still being built grew: its newest table ("built_to", a parting so far) rides on the
        grown message; the last one carries the final table. False when its build failed and the scene closed."""
        if generation != self.generation:
            return True
        if grown["growing"]:
            info = self.resim.current(self.open_path)
            if info is not None:
                self.open_resim = grown["resim"] = info
            return True
        info, failed = await asyncio.to_thread(self.resim.finished, self.open_path)   # the child's last word
        if failed is not None:
            async with self.lock:
                if generation == self.generation:
                    await self._end("closed", failed)
            return False
        if info is not None and generation == self.generation:
            self.open_resim = grown["resim"] = info
        return True

    async def _stop_growing(self, generation: int) -> None:
        """A ghost's scene whose build was cancelled: the pages hear it grows no more ("growing": false, its table
        "building": false and "cancelled": true); the frames it has keep playing."""
        if self.described is None or generation != self.generation:
            return
        self.open_resim = {**(self.open_resim or {}), "building": False, "cancelled": True}
        self.described = {**self.described, "growing": False}
        message = {"kind": "grown", "session": generation, "from": len(self.described.get("frames", ())),
                   "frames": [], "growing": False, "resim": self.open_resim}
        for page in list(self.pages):
            try:
                await page.send(message)
            except Exception as error:  # noqa: BLE001 - a page that left misses nothing it needs
                log.debug("a scene page did not hear the scene stop growing: %r", error)

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
        self.asked_at = time.monotonic()
        loop = asyncio.get_running_loop()
        try:
            pending = await self._run(generation, "begin", message.get("frame"), message.get("camera"),
                                      message.get("encoding", "jpeg"), message.get("hud", True),
                                      message.get("mario", True))
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
        reply = loop.create_task(self._delivery_reply(session, work, ident))
        if work is not None and self.warmed != generation:
            self.warmed = generation
            loop.create_task(self._warm(generation, message.get("frame"), reply), name="scene-lab-warm")
        return reply

    async def _warm(self, generation: int, frame, delivered: asyncio.Task) -> None:
        """Once a scene's first picture is delivered and the page has asked for nothing for
        WARM_QUIET_SECONDS (a person looks before they move the camera), the worker starts the free
        camera's evaluator and draws one view of that frame (scene_session.warm), so neither the
        first Free camera nor the first cinematic camera pays that start, ~200 ms measured
        2026-10-27. Each step waits for the page to be quiet again, so a click lands behind one step
        at most. Play never goes quiet (it asks a picture every frame): "nothing in flight" was the
        old test, true between Play's frames, and the Ghost button's Play waited ~260 ms behind the
        warm-up right after its first picture (measured 2026-10-28; ~2 s in his live report). A page
        that stays busy for WARM_WAIT_SECONDS is never made to wait: the free camera then starts on
        first use, as before."""
        await asyncio.wait([delivered])
        try:
            if not await self._idle(generation):
                return
            await self._run(generation, "warm", frame)
            if not await self._idle(generation):
                return
            await self._run(generation, "warm_ahead", frame)
            # Then the cinematic cameras' own first work, once Mario's path is read (its child started
            # as the scene opened): his runner, the smoothed track and Front's first windows.
            await asyncio.wrap_future(await self._run(generation, "prepare_path"))
            if await self._idle(generation):
                await self._run(generation, "warm_rig", frame)
        except (SceneClosed, LookupError, RuntimeError, OSError, ValueError):
            pass

    async def _idle(self, generation: int) -> bool:
        """Waits until nothing is being drawn or sent for this scene and the page has asked for no
        picture for WARM_QUIET_SECONDS; False when it closed or stayed busy for WARM_WAIT_SECONDS."""
        deadline = time.monotonic() + WARM_WAIT_SECONDS
        while self.busy or time.monotonic() - self.asked_at < WARM_QUIET_SECONDS:
            if generation != self.generation or time.monotonic() > deadline:
                return False
            await asyncio.sleep(WARM_POLL_SECONDS)
        return generation == self.generation

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

    async def rig_path_reply(self, message: dict) -> dict:
        """Mario's path for the cinematic cameras, read in the path reader's child (scene_session
        prepare_path): answered {"kind": "rig-path", "ready", "reason"} once it is read, on a task of
        its own, so neither the pictures nor the other questions wait for it."""
        try:
            future = await self._run(message.get("session"), "prepare_path")
            await asyncio.wrap_future(future)
            ready, reason = True, None
        except SceneClosed as closed:
            return {"id": message.get("id"), "error": str(closed), "closed": True}
        except (LookupError, RuntimeError, OSError, ValueError) as error:
            ready, reason = False, str(error)
        return {"id": message.get("id"), "kind": "rig-path", "ready": ready, "reason": reason}

    async def actions(self, generation, target: str) -> dict:
        """Each frame's action of the stage's Mario or a scene ghost (scene_session.actions), for the
        step to the next action change, once Mario's path (which carries his actions) is read. Over
        HTTP, not the socket, so the socket's messages stay the pictures' and their questions."""
        if target == "stage":
            try:
                await asyncio.wrap_future(await self._run(generation, "prepare_path"))
            except (LookupError, RuntimeError, OSError, ValueError):
                pass                                      # actions() says why there are none
        return {"target": target, **await self._run(generation, "actions", target)}

    async def ghosts_reply(self, message: dict) -> dict:
        """The open scene's ghosts become the page's set (scene_session.set_ghosts), drawn with the
        player's ghost settings; every picture begun after this answer draws them. The clips of
        ghosts the scene does not show yet load first, each on a thread of its own: the catalog's
        load_ghost may wait up to a minute for a community download, and the session's worker,
        which draws the pictures, never waits with it."""
        stage_ghosts = _lab_module("stage_ghosts")
        session = self.session
        current = session is not None and message.get("session") == self.generation
        if current:
            session.ghost_look = stage_ghosts.Look.of(self._ghost_values())
        try:
            wanted = stage_ghosts.checked(message.get("set"))
        except ValueError as error:
            return {"id": message.get("id"), "error": str(error)}
        # Sets apply in the order they came, whichever one's clips load first.
        turn, previous = asyncio.get_running_loop().create_future(), self._ghost_turn
        self._ghost_turn = turn
        try:
            shown = {ghost.ref for ghost in session.ghosts.shown} if current else set()
            new = [entry["ref"] for entry in wanted[:stage_ghosts.MAX_GHOSTS] if entry["ref"] not in shown]
            loaded = dict(zip(new, await asyncio.gather(*(asyncio.to_thread(self._try_load, ref) for ref in new)),
                              strict=True))

            def preloaded(ref):
                got = loaded[ref] if ref in loaded else self._try_load(ref)
                if isinstance(got, LookupError):
                    raise got
                return got
            if previous is not None:
                await asyncio.shield(previous)
            answer = await self._run(message.get("session"), "set_ghosts", wanted, preloaded, message.get("origin"))
        except SceneClosed as closed:
            return {"id": message.get("id"), "error": str(closed), "closed": True}
        except ValueError as error:
            return {"id": message.get("id"), "error": str(error)}
        finally:
            if not turn.done():
                turn.set_result(None)
        return {"id": message.get("id"), "kind": "ghosts", **answer}

    def _load_ghost(self, ref) -> dict:
        """Interface C: ghost_loader when one was given (tests, measurements), else the ghost
        catalog's load_ghost (server/scene_ghosts.py), else the player's own runs in the replay store.
        Blocking: ghosts_reply runs it on a thread of its own per new ghost, never on the session's worker."""
        loader = self.ghost_loader or getattr(getattr(self, "ghosts", None), "load_ghost", None)
        if loader is None:
            from sm64_events.core.paths import replays_root
            loader = self.ghost_loader = _lab_module("stage_ghosts").LocalGhosts(replays_root() / "native")
        return loader(ref)

    def _try_load(self, ref):
        """_load_ghost's answer, or the LookupError that refuses the ghost (its sentence shown)."""
        try:
            return self._load_ghost(ref)
        except LookupError as error:
            return error
        except Exception:  # noqa: BLE001 - one ghost's broken source refuses that ghost, not the set
            log.exception("scene viewer: loading ghost %r failed", ref)
            return LookupError("This ghost could not be loaded.")

    def _ghost_values(self) -> dict | None:
        """The player's ghost settings (the in-game ghost's), from the catalog's ghost session when
        the lab has one; None keeps the shipped defaults."""
        session = getattr(getattr(self, "ghosts", None), "ghost", None)
        try:
            return session.payload() if session is not None else None
        except Exception:  # noqa: BLE001 - the viewer draws with the defaults rather than not at all
            return None

    async def audio_block(self, generation: int, block: int, track: str = "mix") -> tuple[bytes, str]:
        """One block of the open scene's sound (or of its music or effects), decoded and checked
        off the session's worker."""
        session = self.session
        if session is None or generation != self.generation:
            raise SceneClosed((self.ended or {}).get("message") or "This scene is not open.")
        self.last_used = time.monotonic()
        return await asyncio.to_thread(session.audio_block, block, track)

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
            tasks += page.own_tasks
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
        self.own_tasks: list[asyncio.Task] = []          # ghost sets being loaded, paths being read

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
            if message.get("kind") in ("pose", "pick", "ghosts", "rig-path"):
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
                    if question.get("kind") in ("ghosts", "rig-path"):
                        self._on_own_task(question)
                        continue
                    reply = self.lab.pick_reply if question.get("kind") == "pick" else self.lab.pose_reply
                    await self.send(await reply(question))
                else:
                    message, self.picture = self.picture, None
                    self.drawing.append(await self.lab.begin_picture(message))
                    self.begun.set()

    def _on_own_task(self, question: dict) -> None:
        """A ghost set or a path to read answered on its own task: a set's clips may take a community
        download's minute to load (scene_ghosts.load_ghost), Mario's path a second or two to read
        (scene_session.prepare_path), and the page's pictures go on meanwhile."""
        reply = self.lab.ghosts_reply if question.get("kind") == "ghosts" else self.lab.rig_path_reply

        async def answer():
            await self.send(await reply(question))
        task = asyncio.create_task(answer())
        self.own_tasks = [*(other for other in self.own_tasks if not other.done()), task]

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


class StageBody(BaseModel):
    course: int = Field(ge=1, le=25)
    act: int = Field(ge=1, le=6)
    area: int = Field(default=1, ge=1, le=8)


class OpenBody(BaseModel):
    package: str | None = Field(default=None, min_length=1, max_length=255)
    attempt: int | None = Field(default=None, ge=0)
    ghost: str | None = Field(default=None, min_length=1, max_length=512)
    stage: StageBody | None = None

    @model_validator(mode="after")
    def one_of_them(self):
        if sum(value is not None for value in (self.package, self.attempt, self.ghost, self.stage)) != 1:
            raise ValueError("Name one package, attempt, ghost or stage.")
        return self


class RecordingBody(BaseModel):
    on: bool


MAX_IDS = 500                 # attempt ids one attempt-scenes request may name


def _attempt_ids(text: str) -> list[int]:
    try:
        wanted = [int(part) for part in text.split(",") if part.strip()]
    except ValueError:
        raise HTTPException(400, "ids is a comma-separated list of attempt ids.") from None
    if len(wanted) > MAX_IDS:
        raise HTTPException(400, f"Ask for at most {MAX_IDS} attempts at once.")
    return wanted


def _mount_ghost_routes(router: APIRouter, lab: SceneLab) -> None:
    """GET /stage and /ghosts (server/scene_ghosts.py): the stage for a target and strategy, and
    one page of a section of the ghosts that could be drawn into it. Both read the journal and,
    the first time, whole clips, so that work runs on a thread; /ghosts first awaits, on the
    loop the community client runs on, whatever its section needs that is not held yet
    (scene_community.py: nothing inside the publication lease)."""
    from sm64_events.server.scene_ghosts import TargetError

    def target_of(course, star, segment):
        try:
            return lab.ghosts.target(course=course, star=star, segment=segment)
        except TargetError as error:
            raise HTTPException(422, str(error)) from None

    @router.get("/stage")
    def stage(course: int | None = None, star: int | None = None, segment: int | None = None,
              strat: str | None = Query(default=None, max_length=256),
              strategies: str = Query(default="one", pattern="^(one|all)$"),
              ghost: str | None = Query(default=None, min_length=1, max_length=512)):
        found = lab.ghosts.stage(target_of(course, star, segment), strat or None, every=strategies == "all")
        _warm_target(lab, course, star, "current")          # its ghosts then start from a stage made ahead
        return _app_blocked(lab, _stage_open(found, ghost))

    @router.get("/ghosts")
    async def ghosts(section: str = "yours", course: int | None = None, star: int | None = None,
                     segment: int | None = None, strat: str | None = Query(default=None, max_length=256),
                     sort: str = "fastest", page: int = Query(default=1, ge=1, le=100000),
                     q: str = Query(default="", max_length=128), player: str = Query(default="", max_length=128),
                     strategies: str = Query(default="one", pattern="^(one|all)$")):
        target = await asyncio.to_thread(target_of, course, star, segment)
        _warm_target(lab, course, star, "list")
        every = strategies == "all"
        try:
            await lab.ghosts.fill(target, strat or None, section=section, page=page, query=q, player=player,
                                  every=every)
            return await asyncio.to_thread(lambda: lab.ghosts.ghosts(
                target, strat or None, section=section, page=page, sort=sort, query=q, player=player, every=every))
        except TargetError as error:
            raise HTTPException(422, str(error)) from None

    # The Library's ghost button: one recording by its id, as a row of this target's list.
    @router.get("/ghost")
    async def ghost(recording: str = Query(min_length=1, max_length=128), course: int | None = None,
                    star: int | None = None, segment: int | None = None):
        target = await asyncio.to_thread(target_of, course, star, segment)
        _warm_target(lab, course, star, "list")
        await lab.ghosts.fill_recording(recording)
        return await asyncio.to_thread(lab.ghosts.recording_item, target, recording)


class _AppBackground:
    """The installed app's lab work that needs no visit to the Ghost Lab (Griffin, 2026-11-09: "just be able to
    immediately jump into all the functionality"): the ROM copied from the running game in the background
    (scenelab/roms.py follow_game), and the stage warmer started as soon as a ROM is known, so a new player's first
    ghost opens from a made stage. The warm begins with the star the Practice tab targets, then his route, then
    every stage (scene_warm.StageWarmer's order: one idle child on one core, paused while his game runs slow).
    A developer's lab (SM64_SCENE_LAB) runs none of this: its warm still starts on the first stage he opens."""

    POLL_SECONDS = 5.0

    def __init__(self, lab: SceneLab, follow=None):
        self.lab, self.stopped = lab, threading.Event()
        self.stop_follow = follow() if follow is not None else None
        self.thread = threading.Thread(target=self._wait_for_rom, name="lab-first-warm", daemon=True)

    @classmethod
    def start(cls, lab: SceneLab) -> "_AppBackground":
        from sm64_events.scenelab.roms import follow_game
        background = cls(lab, follow=follow_game)
        background.thread.start()
        return background

    def _wait_for_rom(self) -> None:
        while not self.stopped.is_set():
            if self.kick():
                return
            self.stopped.wait(self.POLL_SECONDS)

    def kick(self) -> bool:
        """Starts the warmer once the lab has a ROM and a map; True once started."""
        try:
            config = self.lab.config()
        except Exception:  # noqa: BLE001 - a config that cannot be read yet is asked again
            return False
        if config.rom is None or config.map is None or self.lab.resim is None:
            return False
        from sm64_events.server import scene_warm
        stage = scene_warm.stage_of(*self._target())
        self.lab.resim.warmer.ask_many([stage] if stage is not None else [], scene_warm.CURRENT)
        return True

    def _target(self) -> tuple[int | None, int | None]:
        """The star the Practice tab targets now (course, star), or (None, None)."""
        tracker = self.lab.tracker
        try:
            target = tracker.target_payload() if tracker is not None else {}
        except Exception:  # noqa: BLE001 - no target: the warm starts with his route
            target = {}
        if target.get("kind") != "star":
            return None, None
        return target.get("course_id"), target.get("star_id")

    def stop(self) -> None:
        self.stopped.set()
        self.thread.join(timeout=5.0)
        if self.stop_follow is not None:
            self.stop_follow()


def _warm_target(lab: SceneLab, course: int | None, star: int | None, tier: str) -> None:
    """A star target he is looking at: its stage made ahead (scene_warm.py; "current" for the stage he opened,
    "list" for a target on a list he is looking at). Never waits."""
    if lab.resim is None or course is None or star is None:
        return
    from sm64_events.server import scene_warm
    lab.resim.warm(course, star, scene_warm.CURRENT if tier == "current" else scene_warm.LIST)


def _app_blocked(lab: SceneLab, answer: dict) -> dict:
    """The installed app's lab before it can open anything (no ROM found yet, Project64 not set up): the stage
    names that sentence where it names why there is no stage, and opens nothing (D6: the page says why). A
    developer's lab answers as before."""
    if lab.config_path is not None:
        return answer
    problems = lab.config().problems()
    return answer if not problems else {**answer, "open": None, "resim": False, "reason": problems[0]}


def _stage_open(found: dict, ghost: str | None) -> dict:
    """What POST /open takes for this stage: his scene of the target, else (with the Library ghost that asked)
    that ghost re-simulated into a scene of its own (scene_resim.py), which needs no scene of his."""
    if found.get("attempt") is not None:
        return {**found, "open": {"attempt": found["attempt"]}, "resim": False}
    if ghost is not None:
        return {**found, "open": {"ghost": ghost}, "resim": True, "reason": None}
    return {**found, "open": None, "resim": False}


class PresetsBody(BaseModel):
    slots: list = Field(max_length=9)


class PathBody(BaseModel):
    keys: list = Field(max_length=64)


class SlotMoveBody(BaseModel):
    to: str = Field(pattern="^(star|course)$")


NO_SLOT_SETS = "This server does not keep star sets: it does not follow the running game."


def _mount_slot_set_routes(router: APIRouter, lab: SceneLab) -> None:
    """The Ghost Lab's star sets strip (slotlibrary/keeper.py; the shape is frozen in docs/api.md):
        GET    /slot-sets                     -> the current course and star, and Usamune's nine slots
        POST   /slot-sets/{slot}/move {to}    -> the slot's state into the star's set or the course default
        DELETE /slot-sets/{slot}?set=         -> the slot out of that set; the kept state and the game stay
        GET    /slot-sets/pictures/{state}    -> the frame picture a kept state was made from (JPEG)
    Every answer is the GET body; `slot_sets_changed` carries it on the app's socket. Plain routes on the
    threadpool; an edit wakes the slot worker, which writes the resolved set when it changed."""
    from fastapi.responses import FileResponse

    from sm64_events.slotlibrary.keeper import empty_view
    from sm64_events.slotlibrary.usamune import Refusal

    def empty() -> dict:
        return empty_view()

    def edit(work):
        keeper = lab.slot_sets()
        if keeper is None:
            raise HTTPException(409, NO_SLOT_SETS)
        try:
            with keeper.lock:
                payload = work(keeper)
        except Refusal as refused:
            raise HTTPException(409, f"{str(refused)[:1].upper()}{str(refused)[1:]}.") from None
        except LookupError as missing:
            raise HTTPException(404, f"{str(missing)[:1].upper()}{str(missing)[1:]}.") from None
        lab.refitter.poke("slot_sets")
        return payload

    @router.get("/slot-sets")
    def slot_sets():
        keeper = lab.slot_sets()
        return empty() if keeper is None else keeper.view()

    @router.post("/slot-sets/{slot}/move")
    def move_slot(body: SlotMoveBody, slot: int = FastPath(ge=1, le=9)):
        return edit(lambda keeper: keeper.move(slot, body.to))

    @router.delete("/slot-sets/{slot}")
    def remove_slot(slot: int = FastPath(ge=1, le=9), set: str = Query(pattern="^(star|course)$")):  # noqa: A002
        return edit(lambda keeper: keeper.remove(slot, set))

    @router.get("/slot-sets/pictures/{state}")       # keeper.PICTURE_ROUTE names it to the page
    def slot_picture(state: str):
        keeper = lab.slot_sets()
        try:
            path = None if keeper is None else keeper.library.picture_path(state)
        except Refusal:
            path = None
        if path is None:
            raise HTTPException(404, "This save state has no picture.")
        return FileResponse(path, media_type="image/jpeg")


def _mount_preset_routes(router: APIRouter, lab: SceneLab) -> None:
    """GET and PUT /camera-presets?level=&area=: the camera presets of one level and area, in the lab
    folder's camera-presets.json (tools/scene_lab/camera_presets.py owns the file and its checks).
    Plain routes on the threadpool: a read or an atomic write of one small file."""
    store = None

    def presets():
        nonlocal store
        if store is None:
            presets_module = _lab_module("camera_presets")
            store = presets_module.CameraPresets(lab._lab_root() / presets_module.FILE_NAME)
        return store

    def place(level: int = Query(ge=0, le=999), area: int = Query(ge=0, le=999)) -> tuple[int, int]:
        return level, area

    @router.get("/camera-presets")
    def camera_presets(where: tuple[int, int] = Depends(place)):
        return presets().slots(*where)

    @router.put("/camera-presets")
    def replace_camera_presets(body: PresetsBody, where: tuple[int, int] = Depends(place)):
        try:
            return presets().replace(*where, body.slots)
        except ValueError as refused:            # camera_presets.PresetError
            raise HTTPException(422, str(refused)) from None

    paths_store = None

    def paths():
        nonlocal paths_store
        if paths_store is None:
            paths_module = _lab_module("camera_paths")
            paths_store = paths_module.CameraPaths(lab._lab_root() / paths_module.FILE_NAME)
        return paths_store

    # The camera path of one level and area (tools/scene_lab/camera_paths.py owns the file and its checks).
    @router.get("/camera-paths")
    def camera_paths(where: tuple[int, int] = Depends(place)):
        return paths().keys(*where)

    @router.put("/camera-paths")
    def replace_camera_paths(body: PathBody, where: tuple[int, int] = Depends(place)):
        try:
            return paths().replace(*where, body.keys)
        except ValueError as refused:            # camera_paths.PathError
            raise HTTPException(422, str(refused)) from None

    # Every frame's action of the open scene's Mario or a scene ghost (SceneLab.actions).
    @router.get("/actions")
    async def actions(session: int, target: str = Query(default="stage", min_length=1, max_length=512)):
        try:
            return await lab.actions(session, target)
        except SceneClosed as closed:
            raise HTTPException(409, str(closed)) from None


def _mount_job_routes(router: APIRouter, lab: SceneLab, export_options: dict, moment_options: dict,
                      resim_options: dict) -> None:
    """What runs in a child of its own: a recording made into a video (scene_exports.py), one frame made into a
    Usamune save state (scene_moments.py), and a ghost or a stage re-simulated into a scene (scene_resim.py, which
    POST /open and /moment reach through lab.resim)."""
    from sm64_events.server.scene_exports import SceneExports, mount_export_routes
    from sm64_events.server.scene_moments import SceneMoments, mount_moment_routes
    from sm64_events.server.scene_resim import SceneResim
    lab.resim = SceneResim(lab, **resim_options)
    lab.exports = SceneExports(lab, **export_options)
    mount_export_routes(router, lab.exports)
    lab.moments = SceneMoments(lab, **moment_options)
    mount_moment_routes(router, lab.moments)

    @router.post("/open/cancel")
    async def cancel_build():
        """Ends a ghost's scene still being built (its child, its partial package); the open scene keeps the frames
        it has. {"cancelled": False} when nothing was building."""
        return {"cancelled": await asyncio.to_thread(lab.resim.cancel)}


def _mount_live_routes(router: APIRouter, lab: SceneLab) -> None:
    """The probe (with whether the live recording runs) and the Scene viewer's recording switch
    (SceneLab.set_recording_blocking)."""
    @router.get("")
    def probe():
        return {"lab": "scene-viewer", "live": lab.live()}

    @router.post("/recording")
    async def recording(body: RecordingBody):
        try:
            return {"live": await asyncio.to_thread(lab.set_recording_blocking, body.on)}
        except SceneRefused as refused:
            raise HTTPException(409, str(refused)) from None


def mount_scene_lab(app, config_path, **options) -> SceneLab:
    """Adds the scene viewer's routes to `app`, starts the live recording's lease and the attempt
    scenes when the app starts, and ends both and the open scene when it stops. `config_path` is a
    developer's lab.json, or None for the installed app's own inputs (`app_inputs`, LabConfig.from_app).
    `options` are SceneLab's (tests and e3_measure.py inject doubles and short timers), and `ghost_sources`,
    what the stage and the ghost catalog read: {"tracker", "replay", "ghost", "community"} (scene_ghosts.py)."""
    sources = options.pop("ghost_sources", None) or {}
    # The installed server's background (_AppBackground): main.py asks for it; tests and the UI fixture do not.
    app_background = options.pop("app_background", False)
    export_options, moment_options, resim_options = (options.pop(name, None) or {} for name in (
        "export_options", "moment_options", "resim_options"))
    lab = SceneLab(Path(config_path) if config_path is not None else None, **options)
    from sm64_events.server.scene_ghosts import SceneGhosts
    lab.ghosts = SceneGhosts(sources.get("tracker"), scenes=lambda: lab.scenes, replay=sources.get("replay"),
                             ghost=sources.get("ghost"), community=sources.get("community"))
    router = APIRouter(prefix="/api/scene-lab")
    _mount_ghost_routes(router, lab)
    _mount_preset_routes(router, lab)
    _mount_slot_set_routes(router, lab)
    _mount_job_routes(router, lab, export_options, moment_options, resim_options)

    _mount_live_routes(router, lab)

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
            return await lab.open(body.package, attempt=body.attempt, ghost=body.ghost,
                                  stage=body.stage.model_dump() if body.stage is not None else None)
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
    async def audio(block: int, session: int, track: str = "mix"):
        try:
            data, media_type = await lab.audio_block(session, block, track)
        except SceneClosed as closed:
            raise HTTPException(409, str(closed)) from None
        except LookupError as missing:
            raise HTTPException(404, str(missing)) from None
        except ValueError as damaged:
            raise HTTPException(422, f"This part of the recording's sound is damaged: {damaged}") from None
        return Response(data, media_type=media_type, headers={"Cache-Control": "no-store"})

    if config_path is None:
        # The installed app finds its own ROM (scenelab/roms.py): the no-ROM card's routes (ui/components/rompick.js).
        from sm64_events.server.lab_roms_api import create_rom_router
        app.include_router(create_rom_router())
    app.include_router(router)
    _wrap_lifespan(app, lab, app_background=config_path is None and app_background)
    app.state.scene_lab = lab
    return lab


def _wrap_lifespan(app, lab: SceneLab, *, app_background: bool) -> None:
    """The lab's live work runs while the app serves: the recording's lease and the attempt scenes, and (the
    installed server) _AppBackground; all of it ends, with the open scene, when the app stops."""
    serving = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with serving(application) as state:
            await lab.start_live()
            background = _AppBackground.start(lab) if app_background else None
            try:
                yield state
            finally:
                if background is not None:
                    await asyncio.to_thread(background.stop)
                await asyncio.to_thread(lab.exports.shutdown)
                lab.moments.shutdown()
                await lab.shutdown()

    app.router.lifespan_context = lifespan
