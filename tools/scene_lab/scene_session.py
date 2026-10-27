"""The Trainer's scene viewer session (M4 E3, lab only): one package, one renderer host, one evaluator.

    session = SceneSession.open(config, package_path)            # Refused names why it cannot
    header, payload = session.picture(frame, camera, "jpeg")       # camera None: the recorded camera
    pending = session.begin(frame, camera, "jpeg")                 # picture() in two halves: the host
    header, payload = session.finish(pending)                      #   draws while the next one begins
    session.pose(frame)                                            # the recorded camera and Mario
    session.pick(frame, camera, u, v)                              # where that picture drew a pixel
    session.audio_block(index)                                     # a block of the recording's sound
    session.close()                                                # the host exits; returns its code

A frame is a picture the game showed: a package task a present displayed, in recording order
(package.outline). The recorded camera sends that task's closure as the package rebuilds it, so
the host draws the recorded picture (viewer.py check proves the host reproduces replay.py's
render byte for byte). A free camera is {"focus": xyz, "position": xyz} in the game's world
coordinates, optionally with its own "roll" (the game's s16 angle the screen turns by) and
"fov" (degrees); without them the frame's own apply. The scene evaluator re-runs SM64's own
scene-graph code for that camera and the host draws the whole rebuilt RDRAM with the new list.
The pose's camera (focus, position, roll, fov) redraws the recorded picture exactly, which is
what lets a free camera start from the picture on screen.

A pick (Set focus) finds where a picture's display list drew one pixel by walking that list on
the CPU (pick.py: the renderer's own thread holds its depth), in the camera's own space. The page
owns the camera's world axes and turns that into a world point; the session never does.

When the evaluator cannot serve a frame (no 3D scene during a level load, a stopped run, no
scene state in the package) the picture is the recorded one and `reason` says why. The evaluator starts on the first free-camera request.

A free-camera picture sends the host only the RDRAM words that differ from what the host
already holds (HostImage): a drag changes a few KiB of the 8 MiB, the next frame tens of KiB.
After a free-camera picture of a new frame, a second thread resolves the frames that follow it
(evaluator.prepare; backward when stepping back), so playing or stepping finds states ready.

The package's source-audio layer, when it has one, is the recording's sound mapped to these
frames (scene_audio.py); describe() carries its table and audio_block serves its blocks. An
attempt scene without one yet plays the recorder's own sound (scene_audio.LiveSound) until the
cutter adds the layer to the whole package and grow() hands over to it.

Every method but finish and audio_block runs on the caller's one thread (the server's worker),
so the evaluator's view sandbox, the package and the host's image are never shared. finish runs
on a second thread (the server's delivery thread), once per begin and in begin's order: it reads
the host's answer and encodes it while the worker begins the next picture, which is what lets
the game's code for one camera run while the renderer draws and the encoder compresses the last
one. The host answers its requests in order (viewer.Host). The look-ahead thread touches only
the evaluator's resolution side and the package, both behind their locks. audio_block reads only
the immutable sound layer, so the server runs it off the worker, where a sound block never
waits behind a render.
src/sm64_events/server/scene_lab_api.py owns the session's lifetime. The host starts only after
the GPU hold check; tests and e3_measure.py --fake inject fake_viewer_host.py's doubles.
"""
from __future__ import annotations

import importlib.util
import math
import re
import shutil
import struct
import sys
import tempfile
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gpu_gate  # noqa: E402
import package as scene_package  # noqa: E402
import pick as list_pick  # noqa: E402
import picture_encoder  # noqa: E402
import replay  # noqa: E402
import scene_audio  # noqa: E402
import viewer  # noqa: E402

FPS = 30                      # game frames per second; the viewer steps presented pictures
ENCODINGS = ("jpeg", "raw", "shared")   # shared: the desktop window's shared memory (webview_pictures.py)
JPEG_QUALITY = 90
WORLD_LIMIT = 1e6             # SM64's world fits in +-32768; a camera far outside it is a bug, not a view
NO_SCENE = ("This frame has no 3D scene to move around in (a level load, a fade or a menu), "
            "so it shows the recorded camera.")
NOTHING_DRAWN = "Nothing is drawn there to focus on (sky, or too far away). Pick a surface."
RUN_GAP_WORDS = 16            # changed words this close together travel to the host as one run
MAX_HOST_RUNS = 65536         # viewer_host.c's MAX_RUNS
LOOK_AHEAD_STEP = 4           # a free picture this many frames before the last one is a step back
LOOK_AHEAD_FRAMES = 3         # how many frames the look-ahead resolves (the evaluator caches 4 states)
FIT_WAIT_SECONDS = 1.5        # how long an opening scene waits for its sound's fit (the renderer starts meanwhile)
SOUND_SETTLE_SECONDS = 60.0   # how long a whole scene watches for the sound the cutter adds to it


class Refused(Exception):
    """Why a scene cannot open, in words the viewer shows as they are."""


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def world_camera(camera) -> tuple:
    """{"focus": [x, y, z], "position": [x, y, z][, "roll": s16][, "fov": degrees]} as the
    evaluator takes it ((focus, position), or with (roll, fov) when either is named; a missing
    one is None, the frame's own), or ValueError."""
    if not isinstance(camera, dict):
        raise ValueError("a free camera is {focus, position}")
    vectors = []
    for key in ("focus", "position"):
        value = camera.get(key)
        if not (isinstance(value, list) and len(value) == 3 and all(
                _number(part) and abs(part) <= WORLD_LIMIT for part in value)):
            raise ValueError(f"camera {key} must be three finite world coordinates")
        vectors.append([float(part) for part in value])
    if math.dist(*vectors) < 1.0:
        raise ValueError("the camera sits on its own focus point")
    roll, fov = camera.get("roll"), camera.get("fov")
    if roll is not None and not (isinstance(roll, int) and not isinstance(roll, bool) and -32768 <= roll <= 32767):
        raise ValueError("camera roll must be the game's angle, a whole number from -32768 to 32767")
    if fov is not None and not (_number(fov) and 1.0 <= fov <= 170.0):
        raise ValueError("camera fov must be 1 to 170 degrees")
    if roll is None and fov is None:
        return vectors[0], vectors[1]
    return vectors[0], vectors[1], roll, None if fov is None else float(fov)


@dataclass
class OwnedHost:
    """A renderer host and the private plugin folder it reads its config from (removed on close).
    keeps_rdram: nothing but the session's requests writes the host's RDRAM (HostImage)."""
    host: viewer.Host
    plugin_dir: Path | None = None
    keeps_rdram: bool = False

    @property
    def pid(self) -> int:
        return self.host.process.pid

    def render(self, frame: viewer.Frame, discard: bool = False) -> viewer.Picture:
        return self.host.render(frame, None, discard)

    def use_slots(self, slots) -> None:
        """The host's reader puts each picture's pixels in one of `slots` (picture_encoder)."""
        self.host.slots = slots

    def send(self, frame: viewer.Frame) -> None:
        self.host.send(frame)

    def receive(self) -> viewer.Picture:
        return self.host.receive()

    def close(self) -> int:
        try:
            return self.host.close()
        finally:
            if self.plugin_dir is not None:
                shutil.rmtree(self.plugin_dir, ignore_errors=True)


FRAME_BUFFER_SWITCH = re.compile(r"^frameBufferEmulation\\enable\s*=\s*(\d+)\s*$", re.MULTILINE)


def renderer_keeps_rdram(plugin_dir: Path) -> bool:
    """True when the renderer config turns GLideN64's frame-buffer emulation off everywhere
    (frameBufferEmulation\\enable=0, and no section turning it on): the only way the renderer
    itself writes RDRAM. Absent, GLideN64's default is on."""
    switches = []
    for name in replay.CONFIG_FILES:
        path = plugin_dir / name
        if path.is_file():
            switches += FRAME_BUFFER_SWITCH.findall(path.read_text(encoding="utf-8", errors="replace"))
    return bool(switches) and all(value == "0" for value in switches)


class HostImage:
    """The host's RDRAM as the session last left it, in the game's big-endian order, so a
    free-camera picture sends only the words that changed and the host ends each request
    holding exactly the image a full send would have left. That holds because viewer_host.c
    writes nothing into RDRAM but a request's runs (the session never asks for its camera
    rewrite) and the session uses a HostImage only for a renderer that keeps RDRAM
    (renderer_keeps_rdram). Unknown (None) until the first free-camera picture sends
    everything; a recorded frame's closure runs are applied as they go."""

    def __init__(self):
        self.words: np.ndarray | None = None

    def recorded(self, runs: list, run_bytes: bytes) -> None:
        """A recorded frame's closure went to the host (Project64's word order)."""
        if self.words is None:
            return
        image, at = self.words.view(np.uint8), 0
        for begin, end in runs:
            if end > image.size:                          # not the image this mirror holds
                self.words = None
                return
            image[begin:end] = np.frombuffer(run_bytes, np.uint32, (end - begin) // 4, at).byteswap().view(np.uint8)
            at += end - begin

    def free(self, ram: bytes) -> tuple[bytes, bytes]:
        """(packed <II runs, their bytes in Project64's word order) that bring the host to `ram`."""
        new = np.frombuffer(ram, np.uint32)
        if self.words is None or self.words.size != new.size:
            self.words = new.copy()
            return struct.pack("<II", 0, len(ram)), scene_package.swap_words(ram)
        changed = np.flatnonzero(new != self.words)
        if not changed.size:
            return b"", b""
        breaks = np.flatnonzero(np.diff(changed) > RUN_GAP_WORDS)
        starts = changed[np.concatenate(([0], breaks + 1))]
        ends = changed[np.concatenate((breaks, [changed.size - 1]))] + 1
        if starts.size > MAX_HOST_RUNS:
            self.words = None
            return self.free(ram)
        pieces = [new[start:end] for start, end in zip(starts.tolist(), ends.tolist(), strict=True)]
        self.words[changed] = new[changed]
        runs = np.empty(2 * starts.size, np.uint32)
        runs[0::2], runs[1::2] = starts * 4, ends * 4
        return runs.tobytes(), np.concatenate(pieces).byteswap().tobytes()


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
            return OwnedHost(viewer.Host(renderer, plugin_dir, header), plugin_dir, renderer_keeps_rdram(plugin_dir))
        except (OSError, RuntimeError) as error:
            shutil.rmtree(plugin_dir, ignore_errors=True)
            raise Refused(f"The renderer could not start: {error}") from None
    return start


def command_host(command: list[str], keeps_rdram: bool = True):
    """A host factory over another program that speaks the host protocol
    (fake_viewer_host.command(): tests and dry runs)."""
    def start(header: str) -> OwnedHost:
        return OwnedHost(viewer.Host(Path("renderer.dll"), Path(tempfile.gettempdir()), header, command=command),
                         keeps_rdram=keeps_rdram)
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


def start_encoder():
    """The picture encoder child (picture_encoder.PictureEncoder), or None where it cannot start:
    the session then encodes in process."""
    try:
        return picture_encoder.PictureEncoder(JPEG_QUALITY)
    except OSError:
        return None


class Growth:
    """A package its writer is still writing in place (attempt_cutter.py's takes): `sidecar` is
    the writer's newest publication (package.PackageWriter.publish), a header and index over the
    file's body as it stood. read() takes only the body bytes the file gained since the last
    read, opening and closing the file each time (the writer's process keeps writing it), and
    returns the grown package, the same one when nothing was published since, or None once the
    sidecar is gone: the writer finished and the file is a whole package."""

    def __init__(self, path: Path, sidecar: Path):
        self.path, self.sidecar = path, sidecar
        self.body: list[bytes] = []
        self.read_to = scene_package.HEADER.size

    def read(self, rom: bytes, previous: scene_package.Package | None = None):
        try:
            published = self.sidecar.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise scene_package.PackageError(f"the growing scene's publication could not be read: {error}") from None
        if len(published) < scene_package.HEADER.size:
            raise scene_package.PackageError("the growing scene's publication is shorter than a header")
        header = scene_package.Header(*scene_package.HEADER.unpack_from(published[:scene_package.HEADER.size]))
        if previous is not None and header.frame_count == len(previous):
            return previous
        if header.index_offset > self.read_to:
            try:
                with self.path.open("rb") as handle:
                    handle.seek(self.read_to)
                    piece = handle.read(header.index_offset - self.read_to)
            except OSError as error:
                raise scene_package.PackageError(f"the growing scene's file could not be read: {error}") from None
            if len(piece) != header.index_offset - self.read_to:
                raise scene_package.PackageError("the growing scene's file is shorter than its publication")
            self.body.append(piece)
            self.read_to = header.index_offset
        return scene_package.grown_package(published, list(self.body), rom, previous)


def _live_sound(package, outlines: list[dict], frames: list[int], live, fitter) -> scene_audio.LiveSound | None:
    """A package without a sound layer yet plays the recorder's own (scene_audio.LiveSound), its
    fit asked for now so the cutter child makes it while the host starts."""
    clock = package.index.meta.get("clock")
    if live is None or fitter is None or not isinstance(clock, dict) or package.audio is not None:
        return None
    sound = scene_audio.LiveSound(live, clock, fitter)
    try:
        sound.request(outlines, frames)
    except Exception as error:  # noqa: BLE001 - the scene opens silent and says why
        sound.reason = f"This scene's sound could not be built: {error}"
    return sound


@dataclass(eq=False)
class PendingPicture:
    """A picture begin() sent to the host and finish() has yet to collect (compared by identity)."""
    frame: int
    encoding: str
    camera: str                  # what the host was sent: "free" or "recorded"
    reason: str | None           # why a free camera's request shows the recorded camera
    timings: dict
    started: float


class SceneSession:
    """One open scene package bound to the configured ROM, drawn by one host."""

    def __init__(self, name: str, package: scene_package.Package, rom: bytes, host, frames: list[int],
                 outlines: list[dict], evaluator_factory, free_reason: str | None, encoder=None):
        self.name, self.package, self.rom, self.host = name, package, rom, host
        # Pictures come back through shared memory and the encoder child makes their JPEGs
        # (picture_encoder.py: the game's code then never waits on the encoder for the GIL).
        self.encoder, self.slots = encoder, picture_encoder.PictureSlots() if encoder is not None else None
        if self.slots is not None:
            host.use_slots(self.slots)
        self.frames, self.outlines = frames, outlines
        self.sound = scene_audio.SceneSound.of(package, [outlines[index]["present"] for index in frames])
        self.evaluator_factory, self.free_reason = evaluator_factory, free_reason
        self._evaluator = None
        self.image = HostImage() if getattr(host, "keeps_rdram", False) else None
        self._ahead: ThreadPoolExecutor | None = None    # the look-ahead thread, with the evaluator
        self._ahead_generation = 0                       # bumped by every free-camera request
        self._last_free: int | None = None               # the frame of the last free-camera picture
        self._last_view = self._last_walk = None         # the newest view, and its list walked (_view)
        self._begun: deque[PendingPicture] = deque()     # sent to the host, not yet finished, oldest first
        self.served = 0
        self.growth: Growth | None = None                # set while the package is still being written
        self.live_sound: scene_audio.LiveSound | None = None   # sound from the recorder's ring, until the layer
        self.silence: str | None = None                  # why there is no sound, when the page should know
        self.path: Path | None = None
        self.read_stat: tuple[int, int] | None = None    # the whole file as last read: (size, mtime)
        self.whole_at: float | None = None               # when the package was whole

    @classmethod
    def open(cls, config, path: Path, *, sidecar: Path | None = None, live=None, fitter=None, host_factory=None,
             evaluator_factory=None,
             encoder_factory=None) -> SceneSession:
        """Reads and checks the package against the configured ROM, then starts and warms the host.
        Refused carries the reason for every refusal: the GPU hold, a missing ROM, a package the
        reader refuses (another ROM, truncated, corrupt), a package with nothing shown, a renderer
        that cannot start. With `sidecar`, the package is still being written (an attempt scene
        while its recording grows, Growth): the session opens what is published and grow()
        takes the rest. With `live` (live_audio.LiveAudio) and `fitter` (AttemptScenes.fit), a
        scene whose package has no sound yet plays the recorder's own (scene_audio.LiveSound)."""
        held = gpu_gate.hold_reason()
        if held:
            raise Refused(f"The renderer is on hold until Griffin lifts it: {held}")
        if not config.rom.is_file():
            raise Refused("The ROM named in the lab config is missing.")
        growth = None if sidecar is None else Growth(path, sidecar)
        try:
            if path.stat().st_size > scene_package.MAX_PACKAGE_BYTES:
                raise Refused("This scene package is larger than the viewer accepts.")
            rom = config.rom.read_bytes()
            stat = path.stat()
            package = scene_package.Package(path.read_bytes(), rom) if growth is None else growth.read(rom)
            if package is None:                         # finished between the lookup and now
                stat = path.stat()
                growth, package = None, scene_package.Package(path.read_bytes(), rom)
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
        live_sound = _live_sound(package, outlines, frames, live, fitter)
        encoder = (encoder_factory or start_encoder)()      # starts beside the host's warm-up
        try:
            host = (host_factory or renderer_host(config))(scene_package.rom_header_hex(rom))
        except BaseException:
            if encoder is not None:
                encoder.close()
            raise
        session = cls(path.name, package, rom, host, frames, outlines, factory,
                      unavailable(package) if unavailable else None, encoder)
        session.growth, session.path, session.live_sound = growth, path, live_sound
        if growth is None:
            session.read_stat, session.whole_at = (stat.st_size, stat.st_mtime_ns), time.monotonic()
        try:
            session.host.render(session._recorded_frame(frames[0], {}), discard=True)  # a first list renders wrong
        except (RuntimeError, OSError, ValueError) as error:
            session.close()
            raise Refused(f"The renderer could not draw this recording: {error}") from None
        if live_sound is not None:
            live_sound.adopt(wait=max(0.0, live_sound.requested + FIT_WAIT_SECONDS - time.monotonic()))
            session._sound(0)
        return session

    def describe(self) -> dict:
        """What the page lists: every frame's level, area, game frame and recorded time (when the
        recording showed it, ms after its first picture: Play holds each picture that long; None
        throughout when the package lacks the times, and Play then runs at FPS), the free camera's
        state, and the recording's sound (scene_audio.py's table, or why there is none)."""
        return {"name": self.name, "fps": FPS, "frames": self._rows(0), "renderer_pid": self.host.pid,
                "free_camera": {"available": self.free_reason is None, "reason": self.free_reason},
                "audio": self._audio(), "growing": self.growth is not None}

    def _audio(self) -> dict:
        return self.sound.describe() if self.sound else {**scene_audio.absent(), **(
            {"reason": self.silence} if self.silence else {})}

    def _rows(self, start: int) -> list[dict]:
        """The page's rows for frames start.. (recorded_ms from the first frame's present)."""
        times = [self.outlines[index]["present_us"] for index in self.frames]
        ordered = None not in times and all(a <= b for a, b in zip(times, times[1:], strict=False))
        rows = []
        for position in range(start, len(self.frames)):
            identity = self.outlines[self.frames[position]]["identity"]
            rows.append({"level": identity.get("curr_level"), "area": identity.get("curr_area"),
                         "game_frame": identity.get("global_timer"),
                         "recorded_ms": (times[position] - times[0]) / 1000 if ordered else None})
        return rows

    @property
    def settled(self) -> bool:
        """Nothing about the scene changes any more: its package is whole, and its sound is the
        package's own (or the cutter did not add it within SOUND_SETTLE_SECONDS)."""
        return self.growth is None and (self.live_sound is None or self.whole_at is not None
                                        and time.monotonic() - self.whole_at > SOUND_SETTLE_SECONDS)

    def grow(self) -> dict | None:
        """Until the scene is settled: takes the package's newest publication, or the finished
        file once the writer is done, and the sound's progress (_sound), and returns what the
        page gains ({"from": the first frame that changed, "frames": its rows on, "growing",
        and "audio" when the sound's table changed}), or None when nothing changed. Runs on the
        worker, like every other read of the package."""
        start = self._grow_package() if self.growth is not None else None
        sound = self._sound(start)
        if start is None and not sound:
            return None
        start = len(self.frames) if start is None else start
        grown = {"from": start, "frames": self._rows(start), "growing": self.growth is not None}
        if sound:
            grown["audio"] = self._audio()
        return grown

    def _grow_package(self) -> int | None:
        """The first frame that changed, None when nothing did."""
        growth = self.growth
        package = growth.read(self.rom, self.package)
        if package is None:                             # the writer finished: the file is whole
            try:
                stat = growth.path.stat()
                package = scene_package.Package(growth.path.read_bytes(), self.rom)
            except OSError as error:
                raise scene_package.PackageError(f"the finished scene could not be read: {error}") from None
            package.carry(self.package)
            self.growth, self.read_stat, self.whole_at = None, (stat.st_size, stat.st_mtime_ns), time.monotonic()
        elif package is self.package:
            return None
        outlines = self.outlines[:len(package)]
        outlines += [package.outline(index) for index in range(len(outlines), len(package))]
        frames = [index for index, outline in enumerate(outlines) if outline["vi"] is not None]
        start = next((at for at, (a, b) in enumerate(zip(frames, self.frames, strict=False)) if a != b),
                     min(len(frames), len(self.frames)))
        self._use(package)
        self.outlines, self.frames = outlines, frames
        return start

    def _use(self, package: scene_package.Package) -> None:
        self.package = package
        if self._evaluator is not None and hasattr(self._evaluator, "package"):
            self._evaluator.package = package

    def _sound(self, start: int | None) -> bool:
        """The scene's sound while it comes from the recorder's ring (LiveSound): the cutter
        child's fit once it arrives, the ring's newer chunks and frames `start` on, then the
        layer the cutter adds to the whole package. True when the page's table changed."""
        live = self.live_sound
        if live is None:
            return False
        try:
            changed = live.adopt()
            if live.available:
                changed |= live.extend(self.outlines, self.frames, len(self.frames) if start is None else start,
                                       self.growth is None)
        except Exception as error:  # noqa: BLE001 - the scene goes on silent and says why
            live.segments, live.reason, changed = None, f"This scene's sound could not be built: {error}", True
        if self.growth is None and self._attached():
            return True
        state = (live, None) if live.available else (None, live.reason)
        if (self.sound, self.silence) != state:
            self.sound, self.silence = state
            changed = True
        return changed

    def _attached(self) -> bool:
        """Once the cutter has added the sound to the whole package (live_audio.attach_span, which
        writes the file again), its layer takes over from the ring's: the same stream in the same
        blocks, so the page's blocks stay valid while the frames' samples move by the fit's own
        error. A layer whose stream differs (the ring had dropped the scene's start meanwhile)
        waits for the next open. True when it took over."""
        try:
            stat = self.path.stat()
        except OSError:
            return False
        if (stat.st_size, stat.st_mtime_ns) == self.read_stat:
            return False
        self.read_stat = (stat.st_size, stat.st_mtime_ns)
        try:
            package = scene_package.Package(self.path.read_bytes(), self.rom)
        except (OSError, ValueError, struct.error):     # mid-replace, or refused: the next look
            self.read_stat = None
            return False
        sound = scene_audio.SceneSound.of(package, [self.outlines[index]["present"] for index in self.frames])
        live = self.live_sound
        if sound is None or len(package) != len(self.package):
            return False
        try:
            if live.available:
                ours = bytes(live.pcm[:scene_audio.LIVE_BLOCK_SAMPLES * 4])
                theirs = np.asarray(sound.layer.block(0), np.int16).tobytes()
                if sound.layer.block_samples != scene_audio.LIVE_BLOCK_SAMPLES or theirs[:len(ours)] != ours[:len(theirs)]:
                    return False
            package.carry(self.package)
        except ValueError:                              # a block that fails its hash, a package that differs
            return False
        self._use(package)
        self.sound, self.silence, self.live_sound = sound, None, None
        return True

    def audio_block(self, index: int, track: str = "mix") -> tuple[bytes, str]:
        """(file bytes, media type) of one block of the recording's sound, or of its music or
        effects (scene_audio.TRACKS): LookupError when there is no sound, no such track or no
        such block, ValueError when the block fails its hash."""
        if self.sound is None:
            raise LookupError(scene_audio.NO_SOUND)
        return self.sound.block(index, track)

    def _index(self, frame) -> int:
        if not isinstance(frame, int) or isinstance(frame, bool) or not 0 <= frame < len(self.frames):
            raise ValueError("no such frame")
        return self.frames[frame]

    def _recorded_frame(self, index: int, timings: dict) -> viewer.Frame:
        started = time.perf_counter()
        capture = self.package.frame(index)
        timings["decode_ms"] = _ms(started)
        runs = struct.pack(f"<{2 * len(capture.runs)}I", *(edge for run in capture.runs for edge in run))
        if self.image is not None:
            self.image.recorded(capture.runs, capture.run_bytes)
        return viewer.Frame(f"frame {index}", capture.task, capture.vi, runs, capture.run_bytes)

    def _evaluator_or_reason(self):
        if self.free_reason is None and self._evaluator is None:
            try:
                self._evaluator = self.evaluator_factory(self.package, self.rom)
            except Refused as refused:
                self.free_reason = str(refused)
            except Exception as error:  # noqa: BLE001 - the page shows it as the reason
                self.free_reason = f"The free camera could not start: {error}"
            if self._evaluator is not None and hasattr(self._evaluator, "prepare"):
                self._ahead = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scene-lab-ahead")
        return self._evaluator, self.free_reason

    def _look_ahead(self, frame: int) -> None:
        """After a free-camera picture of `frame`, resolve the LOOK_AHEAD_FRAMES frames that follow
        it on the look-ahead thread (cached ones cost nothing): backward when the previous picture
        was a few frames after it (stepping back), else forward (playing, stepping, or a seek or
        the first picture, after which play goes forward). The frames next to it, not the last
        step repeated: playback that fell behind once asks for the frame due next. A drag (the
        same frame again) predicts nothing new. A job stops before its next frame once another
        free-camera request arrives (_ahead_generation), so a request never waits for more than
        the one frame being resolved, and the newest request's job runs next."""
        step = None if self._last_free is None else frame - self._last_free
        self._last_free = frame
        if self._ahead is None or step == 0:
            return
        direction = -1 if step is not None and -LOOK_AHEAD_STEP <= step < 0 else 1
        targets = [self.frames[at] for at in range(frame + direction, frame + direction * (LOOK_AHEAD_FRAMES + 1),
                                                   direction) if 0 <= at < len(self.frames)]
        if targets:
            self._ahead.submit(self._prepare, targets, self._ahead_generation)

    def _prepare(self, indices: list[int], generation: int) -> None:
        for index in indices:
            if generation != self._ahead_generation:
                return
            self._evaluator.prepare(index)

    def _view(self, evaluator, index: int, camera: tuple):
        """The evaluator's view of `index` from `camera`, kept for the one most recent pair: a pick
        of the picture on screen (Set focus, a double-click) asks for the view its picture was
        just drawn from, so it neither re-runs the game's code nor walks the list twice."""
        last = self._last_view
        if last is not None and last[0] == index and last[1] == camera:
            return last[2]
        view = evaluator.display_list(index, camera)
        self._last_view, self._last_walk = (index, camera, view), None
        return view

    def _walk(self, view):
        if self._last_walk is None or self._last_walk[0] is not view:
            self._last_walk = (view, list_pick.walk(view.ram, view.data_ptr))
        return self._last_walk[1]

    def _free_frame(self, index: int, camera: tuple, timings: dict) -> tuple[viewer.Frame | None, str | None]:
        evaluator, reason = self._evaluator_or_reason()
        if evaluator is None:
            return None, reason
        started = time.perf_counter()
        try:
            view = self._view(evaluator, index, camera)
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
        started = time.perf_counter()
        if self.image is not None:
            runs, data = self.image.free(view.ram)               # the words the host lacks
        else:                                                    # the whole image; Project64's word order
            data = scene_package.swap_words(bytes(view.ram))
            runs = struct.pack("<II", 0, len(data))
        timings.update(send_bytes=len(data), pack_ms=_ms(started))
        return viewer.Frame(f"frame {index} free", tuple(task), self.outlines[index]["vi"], runs, data), None

    def picture(self, frame: int, camera=None, encoding: str = "jpeg") -> tuple[dict, bytes]:
        """Frame `frame` (a position in the frame list) as the renderer draws it: from the recorded
        camera when `camera` is None, else from that world camera. Returns the header the page
        reads and the picture: JPEG, or ReadScreen's own BGR rows bottom-up ("raw")."""
        return self.finish(self.begin(frame, camera, encoding))

    def begin(self, frame: int, camera=None, encoding: str = "jpeg") -> PendingPicture:
        """picture()'s first half, on the session's thread: everything up to the host (the package
        decode or the evaluator's view, the words the host lacks) and the request written. The host
        draws it while this thread begins the next picture; finish() collects it."""
        index = self._index(frame)
        if encoding not in ENCODINGS:
            raise ValueError("unknown encoding")
        free = None if camera is None else world_camera(camera)
        if free is not None:
            self._ahead_generation += 1          # the look-ahead stops before its next frame
        started = time.perf_counter()
        timings: dict = {}
        host_frame, reason = (None, None) if free is None else self._free_frame(index, free, timings)
        drawn_free = host_frame is not None
        if drawn_free:
            self._look_ahead(frame)          # resolves beside this picture's render and encode
        if host_frame is None:
            host_frame = self._recorded_frame(index, timings)
        self.host.send(host_frame)
        timings["begin_ms"] = _ms(started)
        pending = PendingPicture(frame, encoding, "free" if drawn_free else "recorded", reason, timings, started)
        self._begun.append(pending)
        return pending

    def finish(self, pending: PendingPicture, share=None) -> tuple[dict, bytes]:
        """picture()'s second half: the host's picture for `pending`, encoded. One thread calls it,
        in begin()'s order (the host answers in order); it never touches the evaluator, the package
        or the host's image, so it runs beside the next begin(). A picture begun before `pending`
        that nobody finished (its caller gave up) has its answer read and dropped first, so an
        answer is never taken for another request's. For encoding "shared", `share(pixels)` hands
        the exact pixels to the page another way (the desktop window's shared memory) and the
        payload is empty; without `share`, or when it returns False, the picture goes as JPEG and
        the header says so."""
        if pending not in tuple(self._begun):          # a snapshot: begin() appends from its own thread
            raise RuntimeError("this picture is not being drawn")
        while True:
            oldest = self._begun.popleft()
            picture = self.host.receive()
            if oldest is pending:
                break
            self._release(picture)                          # an abandoned picture's answer
        timings = pending.timings
        timings.update(render_ms=round(picture.render_ms, 3), read_ms=round(picture.read_ms, 3),
                       host_ms=round(picture.host_ms, 3))
        encoding_started, encoding, digest, payload = time.perf_counter(), pending.encoding, None, b""
        try:
            if encoding == "shared" and not (share is not None and share(picture.pixels)):
                encoding = "jpeg"
            if encoding == "jpeg":
                payload, timings["encoder"] = self._jpeg(picture)
            elif encoding == "raw":
                payload, digest = bytes(picture.pixels), picture.sha256
        except (ValueError, BufferError):                  # close() released the slot under it
            raise RuntimeError("the scene closed while this picture was drawn") from None
        finally:
            self._release(picture)
        timings["encode_ms"] = _ms(encoding_started)
        timings["session_ms"] = _ms(pending.started)
        self.served += 1
        return {"frame": pending.frame, "width": picture.width, "height": picture.height, "encoding": encoding,
                "format": "image/jpeg" if encoding == "jpeg" else "bgr24-bottom-up",
                "camera": pending.camera, "reason": pending.reason, "sha256": digest,
                "payload_bytes": len(payload), "timings": timings}, payload

    def _jpeg(self, picture: viewer.Picture) -> tuple[bytes, str]:
        """The picture as JPEG and where it was made: by the encoder child from its slot, or here
        (no slot, the child not ready yet, or gone: it is dropped after its first failure)."""
        encoder = self.encoder
        if encoder is not None and picture.slot is not None:
            try:
                made = encoder.jpeg(self.slots, picture.slot, picture.width, picture.height)
            except RuntimeError:
                self.encoder, made = None, None
                encoder.close()
            if made is not None:
                return made, "process"
        return picture_encoder.encode(picture.pixels, picture.width, picture.height, JPEG_QUALITY), "thread"

    def _release(self, picture: viewer.Picture) -> None:
        if picture.slot is not None and self.slots is not None:
            self.slots.release(picture.slot)

    def pick(self, frame: int, camera, u, v) -> dict:
        """Where the picture of `frame` from `camera` (None: the recorded camera) drew the pixel at
        (u, v), its place in the picture from 0 to 1 across and down: {"point": [x, y, z] in that
        camera's space (pick.py), "distance", "mario", "reason"}, or point None and the reason.
        "mario" is the frame's Mario (a double-click's flight ends looking at him), so one round
        trip carries all a fly-to needs. The recorded camera walks the captured list; a free
        camera walks the list its view makes, the one its picture was just drawn from."""
        index = self._index(frame)
        if not all(_number(value) and 0 <= value <= 1 for value in (u, v)):
            raise ValueError("a pick names a place in the picture: u and v from 0 to 1")
        evaluator, reason = (None, None) if camera is None and self._evaluator is None else self._evaluator_or_reason()
        missed = {"point": None, "distance": None, "mario": None}
        try:
            if camera is None:
                capture = self.package.frame(index)
                walked = list_pick.walk(list_pick.ram_from_runs(capture.runs, capture.run_bytes), capture.task[12])
            else:
                free = world_camera(camera)
                if evaluator is None:
                    return {**missed, "reason": reason}
                self._ahead_generation += 1
                view = self._view(evaluator, index, free)
                if view is None or view.ram is None:          # the page showed the recorded picture
                    return {**missed, "reason": NO_SCENE}
                walked = self._walk(view)
        except ValueError as error:
            return {**missed, "reason": f"This picture's list could not be read: {error}."}
        point = list_pick.nearest(walked, u, v)
        if point is None:
            return {**missed, "reason": NOTHING_DRAWN}
        mario = evaluator.mario(index) if evaluator is not None and hasattr(evaluator, "mario") else None
        return {"point": point, "distance": math.hypot(*point), "mario": mario, "reason": None}

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
        evaluator's sandboxes and the package's caches, after any look-ahead finishes."""
        ahead, self._ahead = self._ahead, None
        if ahead is not None:
            self._ahead_generation += 1                  # a running job stops after its frame
            ahead.shutdown(wait=True, cancel_futures=True)
        self._evaluator = self._last_view = self._last_walk = None
        try:
            return self.host.close()
        finally:
            encoder, self.encoder = self.encoder, None
            if encoder is not None:
                encoder.close()
            if self.slots is not None:
                self.slots.close()
