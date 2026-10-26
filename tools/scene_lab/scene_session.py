"""The Trainer's scene viewer session (M4 E3, lab only): one package, one renderer host, one evaluator.

    session = SceneSession.open(config, package_path)            # Refused names why it cannot
    header, payload = session.picture(frame, camera, "jpeg")       # camera None: the recorded camera
    session.pose(frame)                                            # the recorded camera and Mario
    session.close()                                                # the host exits; returns its code

A frame is a picture the game showed: a package task a present displayed, in recording order
(package.outline). The recorded camera sends that task's closure as the package rebuilds it, so
the host draws the recorded picture (viewer.py check proves the host reproduces replay.py's
render byte for byte). A free camera is {"focus": xyz, "position": xyz} in the game's world
coordinates: the scene evaluator re-runs SM64's own scene-graph code for that camera and the
host draws the whole rebuilt RDRAM with the new list. When the evaluator cannot serve a frame
(no 3D scene during a level load, a stopped run, no scene state in the package) the picture is
the recorded one and `reason` says why. The evaluator starts on the first free-camera request.

Every method runs on the caller's one thread: the server gives each session a single worker
thread, so the package's caches, the evaluator's sandboxes and the host's pipe are never shared.
src/sm64_events/server/scene_lab_api.py owns the session's lifetime. The host starts only after
the GPU hold check; tests and e3_measure.py --fake inject fake_viewer_host.py's doubles.
"""
from __future__ import annotations

import importlib.util
import io
import math
import shutil
import struct
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gpu_gate  # noqa: E402
import package as scene_package  # noqa: E402
import replay  # noqa: E402
import viewer  # noqa: E402

FPS = 30                      # game frames per second; the viewer steps presented pictures
ENCODINGS = ("jpeg", "raw")
JPEG_QUALITY = 90
WORLD_LIMIT = 1e6             # SM64's world fits in +-32768; a camera far outside it is a bug, not a view
NO_SCENE = ("This frame has no 3D scene to move around in (a level load, a fade or a menu), "
            "so it shows the recorded camera.")


class Refused(Exception):
    """Why a scene cannot open, in words the viewer shows as they are."""


def world_camera(camera) -> tuple[list[float], list[float]]:
    """{"focus": [x, y, z], "position": [x, y, z]} as the evaluator takes it, or ValueError."""
    if not isinstance(camera, dict):
        raise ValueError("a free camera is {focus, position}")
    vectors = []
    for key in ("focus", "position"):
        value = camera.get(key)
        if not (isinstance(value, list) and len(value) == 3 and all(
                isinstance(part, (int, float)) and not isinstance(part, bool) and math.isfinite(part)
                and abs(part) <= WORLD_LIMIT for part in value)):
            raise ValueError(f"camera {key} must be three finite world coordinates")
        vectors.append([float(part) for part in value])
    if math.dist(*vectors) < 1.0:
        raise ValueError("the camera sits on its own focus point")
    return vectors[0], vectors[1]


@dataclass
class OwnedHost:
    """A renderer host and the private plugin folder it reads its config from (removed on close)."""
    host: viewer.Host
    plugin_dir: Path | None = None

    @property
    def pid(self) -> int:
        return self.host.process.pid

    def render(self, frame: viewer.Frame, discard: bool = False) -> viewer.Picture:
        return self.host.render(frame, None, discard)

    def close(self) -> int:
        try:
            return self.host.close()
        finally:
            if self.plugin_dir is not None:
                shutil.rmtree(self.plugin_dir, ignore_errors=True)


def renderer_host(config):
    """The real host factory: viewer_host.c over the configured renderer, with Project64's
    renderer config copied to a private folder (the live install is only read)."""
    def start(header: str) -> OwnedHost:
        plugin = config.pj64_plugin
        if plugin is None or not (plugin / replay.CONFIG_FILES[0]).is_file():
            raise Refused("The lab config does not name Project64's Plugin folder, which holds the "
                          "renderer settings the pictures are drawn with.")
        try:
            renderer = replay.resolve_renderer(config.renderer, plugin)
        except SystemExit as missing:
            raise Refused(str(missing)) from None
        plugin_dir = Path(tempfile.mkdtemp(prefix="scene-viewer-plugin-"))
        try:
            viewer.prepare_plugin_dir(plugin_dir, plugin, config.resolution)
            return OwnedHost(viewer.Host(renderer, plugin_dir, header), plugin_dir)
        except (OSError, RuntimeError) as error:
            shutil.rmtree(plugin_dir, ignore_errors=True)
            raise Refused(f"The renderer could not start: {error}") from None
    return start


def command_host(command: list[str]):
    """A host factory over another program that speaks the host protocol
    (fake_viewer_host.command(): tests and dry runs)."""
    def start(header: str) -> OwnedHost:
        return OwnedHost(viewer.Host(Path("renderer.dll"), Path(tempfile.gettempdir()), header, command=command))
    return start


class GameCodeEvaluator:
    """The free camera's evaluator factory: evaluator.Evaluator over the open package, running
    SM64's own scene-graph code under Unicorn (a lab-only package, never a project dependency)."""

    def __init__(self, map_path: Path | None, source: Path):
        self.map_path, self.source = map_path, source

    def unavailable(self, package: scene_package.Package) -> str | None:
        if package.states is None:
            return "This recording carries no scene state, so it can only be shown from the recorded camera."
        if self.map_path is None or not self.map_path.is_file():
            return ("The lab config names no symbol map (MappingUS.map), which the free camera needs "
                    "to run the game's drawing code.")
        if importlib.util.find_spec("unicorn") is None:
            return ("The free camera runs the game's drawing code under Unicorn, which this server "
                    "lacks: start it with uv run --with unicorn.")
        return None

    def __call__(self, package: scene_package.Package, rom: bytes):
        import e2_georender
        import evaluator
        return evaluator.Evaluator(package, rom, e2_georender.load_map(self.map_path), source=self.source)


def _ms(since: float) -> float:
    return round((time.perf_counter() - since) * 1000, 3)


class SceneSession:
    """One open scene package bound to the configured ROM, drawn by one host."""

    def __init__(self, name: str, package: scene_package.Package, rom: bytes, host, frames: list[int],
                 outlines: list[dict], evaluator_factory, free_reason: str | None):
        self.name, self.package, self.rom, self.host = name, package, rom, host
        self.frames, self.outlines = frames, outlines
        self.evaluator_factory, self.free_reason = evaluator_factory, free_reason
        self._evaluator = None
        self.served = 0

    @classmethod
    def open(cls, config, path: Path, *, host_factory=None, evaluator_factory=None) -> SceneSession:
        """Reads and checks the package against the configured ROM, then starts and warms the host.
        Refused carries the reason for every refusal: the GPU hold, a missing ROM, a package the
        reader refuses (another ROM, truncated, corrupt), a package with nothing shown, a renderer
        that cannot start."""
        held = gpu_gate.hold_reason()
        if held:
            raise Refused(f"The renderer is on hold until Griffin lifts it: {held}")
        if not config.rom.is_file():
            raise Refused("The ROM named in the lab config is missing.")
        try:
            if path.stat().st_size > scene_package.MAX_PACKAGE_BYTES:
                raise Refused("This scene package is larger than the viewer accepts.")
            rom = config.rom.read_bytes()
            package = scene_package.Package(path.read_bytes(), rom)
            outlines = [package.outline(index) for index in range(len(package))]
        except OSError as error:
            raise Refused(f"This scene package could not be read: {error.strerror or error}.") from None
        except (ValueError, struct.error) as error:     # package.PackageError is a ValueError
            raise Refused(f"This scene package cannot be opened: {error}.") from None
        frames = [index for index, outline in enumerate(outlines) if outline["vi"] is not None]
        if not frames:
            raise Refused("This scene package holds no picture the game showed.")
        factory = evaluator_factory or GameCodeEvaluator(config.map, path)
        unavailable = getattr(factory, "unavailable", None)
        host = (host_factory or renderer_host(config))(scene_package.rom_header_hex(rom))
        session = cls(path.name, package, rom, host, frames, outlines, factory,
                      unavailable(package) if unavailable else None)
        try:
            session.host.render(session._recorded_frame(frames[0], {}), discard=True)  # a first list renders wrong
        except (RuntimeError, OSError, ValueError) as error:
            session.close()
            raise Refused(f"The renderer could not draw this recording: {error}") from None
        return session

    def describe(self) -> dict:
        """What the page lists: every frame's level, area and game frame, and the free camera's state."""
        frames = []
        for index in self.frames:
            identity = self.outlines[index]["identity"]
            frames.append({"level": identity.get("curr_level"), "area": identity.get("curr_area"),
                           "game_frame": identity.get("global_timer")})
        return {"name": self.name, "fps": FPS, "frames": frames, "renderer_pid": self.host.pid,
                "free_camera": {"available": self.free_reason is None, "reason": self.free_reason}}

    def _index(self, frame) -> int:
        if not isinstance(frame, int) or isinstance(frame, bool) or not 0 <= frame < len(self.frames):
            raise ValueError("no such frame")
        return self.frames[frame]

    def _recorded_frame(self, index: int, timings: dict) -> viewer.Frame:
        started = time.perf_counter()
        capture = self.package.frame(index)
        timings["decode_ms"] = _ms(started)
        runs = struct.pack(f"<{2 * len(capture.runs)}I", *(edge for run in capture.runs for edge in run))
        return viewer.Frame(f"frame {index}", capture.task, capture.vi, runs, capture.run_bytes)

    def _evaluator_or_reason(self):
        if self.free_reason is None and self._evaluator is None:
            try:
                self._evaluator = self.evaluator_factory(self.package, self.rom)
            except Refused as refused:
                self.free_reason = str(refused)
            except Exception as error:  # noqa: BLE001 - the page shows it as the reason
                self.free_reason = f"The free camera could not start: {error}"
        return self._evaluator, self.free_reason

    def _free_frame(self, index: int, camera: tuple, timings: dict) -> tuple[viewer.Frame | None, str | None]:
        evaluator, reason = self._evaluator_or_reason()
        if evaluator is None:
            return None, reason
        started = time.perf_counter()
        try:
            view = evaluator.display_list(index, camera)
        except Exception as error:  # noqa: BLE001 - the page shows it as the reason
            return None, f"The game's drawing code could not redraw this frame ({error}), so it shows the recorded camera."
        timings["evaluate_ms"] = _ms(started)
        if view is None:
            return None, NO_SCENE
        if view.ram is None:
            return None, ("The game's drawing code stopped on this view (it reached memory the recording "
                          "does not hold), so it shows the recorded camera.")
        task = list(view.task)
        task[12], task[13] = view.data_ptr & 0x1FFFFFFF, view.data_size
        data = scene_package.swap_words(bytes(view.ram))      # the host takes Project64's word order
        return viewer.Frame(f"frame {index} free", tuple(task), self.outlines[index]["vi"],
                            struct.pack("<II", 0, len(data)), data), None

    def picture(self, frame: int, camera=None, encoding: str = "jpeg") -> tuple[dict, bytes]:
        """Frame `frame` (a position in the frame list) as the renderer draws it: from the recorded
        camera when `camera` is None, else from that world camera. Returns the header the page
        reads and the picture: JPEG, or ReadScreen's own BGR rows bottom-up ("raw")."""
        index = self._index(frame)
        if encoding not in ENCODINGS:
            raise ValueError("unknown encoding")
        free = None if camera is None else world_camera(camera)
        started = time.perf_counter()
        timings: dict = {}
        host_frame, reason = (None, None) if free is None else self._free_frame(index, free, timings)
        drawn_free = host_frame is not None
        if host_frame is None:
            host_frame = self._recorded_frame(index, timings)
        picture = self.host.render(host_frame)
        timings.update(render_ms=round(picture.render_ms, 3), read_ms=round(picture.read_ms, 3),
                       host_ms=round(picture.host_ms, 3))
        encoding_started = time.perf_counter()
        if encoding == "jpeg":
            from PIL import Image
            buffer = io.BytesIO()
            Image.frombuffer("RGB", (picture.width, picture.height), picture.pixels, "raw", "BGR", 0, -1) \
                .save(buffer, "JPEG", quality=JPEG_QUALITY)
            payload = buffer.getvalue()
        else:
            payload = picture.pixels
        timings["encode_ms"] = _ms(encoding_started)
        timings["session_ms"] = _ms(started)
        self.served += 1
        return {"frame": frame, "width": picture.width, "height": picture.height, "encoding": encoding,
                "format": "image/jpeg" if encoding == "jpeg" else "bgr24-bottom-up",
                "camera": "free" if drawn_free else "recorded", "reason": reason,
                "sha256": picture.sha256 if encoding == "raw" else None,
                "payload_bytes": len(payload), "timings": timings}, payload

    def pose(self, frame: int) -> dict:
        """The frame's recorded camera and Mario's position, or None and the reason."""
        index = self._index(frame)
        evaluator, reason = self._evaluator_or_reason()
        if evaluator is None:
            return {"pose": None, "reason": reason}
        try:
            return {"pose": evaluator.pose(index), "reason": None}
        except Exception as error:  # noqa: BLE001 - the page shows it as the reason
            return {"pose": None, "reason": f"This frame's camera could not be read: {error}"}

    def close(self) -> int:
        """Ends the host (its pipe closes; viewer.Host.close waits for the exit) and drops the
        evaluator's sandboxes and the package's caches."""
        self._evaluator = None
        return self.host.close()
