"""The Trainer's scene viewer session (M4 E3, lab only): one package, one renderer host, one evaluator.

    session = SceneSession.open(config, package_path)            # Refused names why it cannot
    header, payload = session.picture(frame, camera, "jpeg")       # camera None: the recorded camera;
                                                                   #   {"ghost": ref}: that ghost's view;
                                                                   #   {"rig": {...}}: a cinematic camera
    pending = session.begin(frame, camera, "jpeg")                 # picture() in two halves: the host
    header, payload = session.finish(pending)                      #   draws while the next one begins
    session.pose(frame)                                            # the recorded camera and Mario
    session.pick(frame, camera, u, v)                              # where that picture drew a pixel
    session.set_ghosts([{"ref", "label"}], load_ghost)             # the ghosts every picture draws
    session.audio_block(index)                                     # a block of the recording's sound
    session.close()                                                # the host exits; returns its code

A frame is a picture the game showed: a package task a present displayed, in recording order
(package.outline). The recorded camera sends that task's closure as the package rebuilds it, so
the host draws the recorded picture (viewer.py check proves the host reproduces replay.py's
render byte for byte). A free camera is {"focus": xyz, "position": xyz} in the game's world
coordinates, optionally with its own "roll" (the game's s16 angle the screen turns by) and
"fov" (degrees); without them the frame's own apply. The scene evaluator re-runs SM64's own
scene-graph code for that camera and the host draws the whole rebuilt RDRAM with the new list.
The evaluator runs under Unicorn in a simulation process of its own (evaluator_child.py, through
GameCodeEvaluator): this process never loads Unicorn, which is GPL-2.0-only (sim_boundary.py).
The pose's camera (focus, position, roll, fov) redraws the recorded picture exactly, which is
what lets a free camera start from the picture on screen, but for Mario's model: every moved
camera draws his most detailed one whatever its distance (camera_state.full_detail), where the
game may have drawn a plainer one. A free camera with "recorded": weight
(a camera path easing into or out of the recorded camera, ui/components/scenepath.js) is drawn
that share of the way to the frame's recorded camera (recorded_blend).

A picture without the HUD (begin's hud=False) sends either list with the 2D overlay the game
draws after the world turned into no-ops (hud.py: lives, coins, stars, the timer, Usamune's
text, the pause screen, dialogs); the world, the screen transition and the borders stay, and so
does the cached view, which the cut copies. The header's "hud" says which picture it is.

A pick (Set focus) finds where a picture's display list drew one pixel by walking that list on
the CPU (pick.py: the renderer's own thread holds its depth), in the camera's own space. The page
owns the camera's world axes and turns that into a world point; the session never does.

When the evaluator cannot serve a frame (no 3D scene during a level load, a stopped run, no
scene state in the package) the picture is the recorded one and `reason` says why. The evaluator starts on the first free-camera request, or before it in warm() and warm_ahead(), which the lab calls once the page is idle after the first picture.

A free-camera picture sends the host only the RDRAM words that differ from what the host
already holds (HostImage): a drag changes a few KiB of the 8 MiB, the next frame tens of KiB.
After a free-camera picture of a new frame, the frames that follow it (backward when stepping
back) are resolved ahead, so playing or stepping finds states ready: by the state resolvers'
child processes (state_resolver.py, RESOLVED_AHEAD frames, started by the evaluator's process)
once the package is whole and they are ready; before that by a second thread (evaluator.prepare,
LOOK_AHEAD_FRAMES), in the evaluator's process, whose work shares that process's Python lock with
the views (in the server, Play with the camera moving lost frames to it).

Ghosts (stage_ghosts.py): set_ghosts loads each added ghost's recording into the renderer once, and
every picture after it, recorded or free camera, names the ghosts and each one's frame, so the
renderer draws them into the stage with the in-game ghost's look and name boxes, hidden behind its
walls. A recorded picture with ghosts sends the frame's scene state beside its closure: the
in-game ghost reads the stage's camera, level and area from the game's RAM, as it does live. A
picture without ghosts is sent exactly as before. Pick and pose read the stage alone: a ghost never
changes what Set focus hits. A picture's camera may also be {"ghost": ref}: that ghost's view, the
stage's world from the camera its own recording drew with at the row its drawing shows in that
picture (stage_ghosts.GhostCameras), drawn through the free camera's path; the header's "view" says
whose it is and which camera it drew (None: the recorded camera, and "reason" says why).

A cinematic camera, {"rig": {"kind": "front"|"side"|"orbit", "target": "stage"|a ghost's ref, ...}}
(camera_rig.py, round 6): a camera riding the runner's smoothed path, aimed at him, worked out from
the whole path for each picture and drawn through the free camera's path. The stage's Mario is read
from every frame's scene state once, in the path reader's child process (rig_path.py: ~1 s for an
800-frame take, measured 2026-10-27), started as the scene opens; a growing scene reads only the
frames it gained, and a rig picture of a frame not read yet is the recorded one, saying so
(camera_rig.PATH_PENDING), never a wait; a ghost's from its clip through the same frame count its
drawing uses; the walls are the level's static collision in the
scene state (camera_rig.static_walls, the symbol map's sSurfacePool). The header's "rig" says which
and what it did ({"kind", "target", "shot", "side", "pulled", "raised"}). Every header's "camera_used" is the
camera drawn whenever the server drew one (free, a ghost's view, a rig: {"focus", "position", "roll",
"fov"}, roll and fov None where the frame's own applied), None for the recorded camera.

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

import hashlib
import logging
import math
import re
import struct
import sys
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import camera_rig  # noqa: E402
import rig_path  # noqa: E402
import stage_ghosts  # noqa: E402
import ghost_sound  # noqa: E402
import gpu_gate  # noqa: E402
import hud as hud_cut  # noqa: E402
import package as scene_package  # noqa: E402
import pick as list_pick  # noqa: E402
import picture_encoder  # noqa: E402
import replay  # noqa: E402
import scene_audio  # noqa: E402
import viewer  # noqa: E402

log = logging.getLogger("sm64.scene_lab")
FPS = 30                      # game frames per second; the viewer steps presented pictures
ENCODINGS = ("jpeg", "raw", "shared")   # shared: the desktop window's shared memory (webview_pictures.py)
JPEG_QUALITY = 90
WORLD_LIMIT = 1e6             # SM64's world fits in +-32768; a camera far outside it is a bug, not a view
NO_SCENE = ("This frame has no 3D scene to move around in (a level load, a fade or a menu), "
            "so it shows the recorded camera.")
NOTHING_DRAWN = "Nothing is drawn there to focus on (sky, or too far away). Pick a surface."
MARIO_SHOWN = "This picture cannot be drawn again without Mario (a level load, a fade or a menu), so he shows."
RUN_GAP_WORDS = 16            # changed words this close together travel to the host as one run
MAX_HOST_RUNS = 65536         # viewer_host.c's MAX_RUNS
LOOK_AHEAD_STEP = 4           # a free picture this many frames before the last one is a step back
LOOK_AHEAD_FRAMES = 3         # how many frames the look-ahead resolves (the evaluator caches 4 states)
RESOLVED_AHEAD = 6            # how many the state resolvers' children resolve (each kept as one 8 MiB state)
WARM_FRAMES = 2               # states warm_ahead() resolves here after the frame on screen (the evaluator caches 4)
FIT_WAIT_SECONDS = 1.5        # how long an opening scene waits for its sound's fit (the renderer starts meanwhile)
SOUND_SETTLE_SECONDS = 60.0   # how long a whole scene watches for the sound the cutter adds to it
GHOST_WARM_PICTURES = 40      # pictures the renderer may take to set its ghost overlay up (it retries per list)


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


def blend_cameras(camera: dict, toward: dict, weight: float) -> dict:
    """`camera` moved `weight` (0 to 1) of the way to `toward` (both {"focus", "position", "roll",
    "fov"}; a roll or fov of None counts as the other's): place and aim along straight lines, the
    roll (the game's s16) the short way round, the field of view evenly in log(tan(fov / 2)), as a
    camera path moves its lens (scenepath.js)."""
    def mix(a, b):
        return [x + (y - x) * weight for x, y in zip(a, b, strict=True)]
    roll, other = camera.get("roll"), toward.get("roll")
    roll = other if roll is None else roll
    if roll is not None and other is not None:
        turn = (other - roll + 32768) % 65536 - 32768
        roll = (round(roll + turn * weight) + 32768) % 65536 - 32768
    fov, aim = camera.get("fov"), toward.get("fov")
    fov = aim if fov is None else fov
    if fov is not None and aim is not None:
        low, high = (math.log(math.tan(math.radians(value) / 2)) for value in (fov, aim))
        fov = math.degrees(2 * math.atan(math.exp(low + (high - low) * weight)))
    focus, position = mix(camera["focus"], toward["focus"]), mix(camera["position"], toward["position"])
    if math.dist(focus, position) < 1.0:                  # never on its own aim
        position[2] += 1.5
    return {"focus": focus, "position": position, "roll": roll, "fov": fov}


@dataclass
class OwnedHost:
    """A renderer host the session owns. keeps_rdram: nothing but the session's requests writes
    the host's RDRAM (HostImage)."""
    host: viewer.Host
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

    def call(self, frame: viewer.Frame) -> viewer.Picture:
        return self.host.call(frame)

    def ghost_setup(self, layout: bytes, rom: bytes) -> int:
        return self.host.ghost_setup(layout, rom)

    def ghost_load(self, slot: int, clip: bytes, label: str) -> int:
        return self.host.ghost_load(slot, clip, label)

    def close(self) -> int:
        return self.host.close()


FRAME_BUFFER_SWITCH = re.compile(r"^frameBufferEmulation\\enable\s*=\s*(\d+)\s*$", re.MULTILINE)


def renderer_keeps_rdram(settings: Path) -> bool:
    """True when the renderer config turns GLideN64's frame-buffer emulation off everywhere
    (frameBufferEmulation\\enable=0, and no section turning it on): the only way the renderer
    itself writes RDRAM. Absent, GLideN64's default is on."""
    switches = []
    for name in replay.CONFIG_FILES:
        path = settings / name
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


def scene_inputs(config, path: Path) -> tuple[Path, Path | None]:
    """The ROM and map a scene package opens with: of the lab's ROMs (config.rom, then each region's in
    `config.roms`), the one whose bytes the package was written from (its header's rom_sha), with that
    region's map (`config.maps`). So a JP scene opens with the JP ROM and MappingJP.map whichever region the
    lab runs first. Otherwise the configured pair, and the package reader names the mismatch."""
    rom, map_path = Path(config.rom), config.map
    try:
        with Path(path).open("rb") as handle:
            head = handle.read(scene_package.HEADER.size)
        header = scene_package.Header(*scene_package.HEADER.unpack_from(head))
    except (OSError, struct.error):
        return rom, map_path
    if header.magic != scene_package.MAGIC:
        return rom, map_path
    maps = getattr(config, "maps", None) or {}
    seen = set()
    for region, candidate in [(None, rom), *(getattr(config, "roms", None) or {}).items()]:
        if candidate is None or Path(candidate) in seen:
            continue
        seen.add(Path(candidate))
        try:
            digest = hashlib.sha256(Path(candidate).read_bytes()).digest()
        except OSError:
            continue
        if digest == header.rom_sha:
            return Path(candidate), map_path if region is None else (maps.get(region) or None)
    return rom, map_path


def renderer_host(config):
    """The real host factory: viewer_host.c over the Trainer's renderer where the bundle keeps it
    (replay.trainer_renderer(): the graphics plugin, loaded in place, never copied, and run as the
    renderer alone through SM64RendererOnlyV1), reading Project64's renderer
    settings from the renderer settings folder (replay.settings_folder: Project64's Plugin folder
    is only read). No other renderer: one that cannot take a settings folder would read and write
    the folder it sits in."""
    def start(header: str) -> OwnedHost:
        plugin = config.pj64_plugin
        if plugin is None or not (plugin / replay.CONFIG_FILES[0]).is_file():
            raise Refused("The Ghost Lab cannot find Project64's Plugin folder, which holds the settings its "
                          "pictures are drawn with. Finish the setup steps, then try again.")
        if config.renderer != "trainer":
            raise Refused('This developer lab file (SM64_SCENE_LAB) names another renderer. The Ghost Lab draws '
                          'only with the Trainer\'s own renderer, so remove "renderer" from that file.')
        try:
            renderer = replay.trainer_renderer()
        except SystemExit as missing:
            raise Refused(str(missing)) from None
        try:
            # The Record child's config (scene_export.py) carries no lab root: the default one.
            settings = replay.settings_folder(plugin, config.resolution, getattr(config, "lab_root", None))
            return OwnedHost(viewer.Host(renderer, settings, header, ghosts=True), renderer_keeps_rdram(settings))
        except replay.SettingsFolderTooLong as error:
            raise Refused(str(error)) from None                   # one plain sentence, before any host starts
        except (OSError, RuntimeError) as error:
            raise Refused(f"The renderer could not start: {error}") from None
    return start


def command_host(command: list[str], keeps_rdram: bool = True):
    """A host factory over another program that speaks the host protocol
    (fake_viewer_host.command(): tests and dry runs)."""
    def start(header: str) -> OwnedHost:
        return OwnedHost(viewer.Host(Path("renderer.dll"), Path("settings"), header, command=command),
                         keeps_rdram=keeps_rdram)
    return start


class GameCodeEvaluator:
    """The free camera's evaluator factory: evaluator.Evaluator over the open package, running SM64's
    own scene-graph code under Unicorn, in a simulation process of its own (evaluator_child.py): this
    process never loads Unicorn (sim_boundary.py), and the evaluator's process starts its own state
    resolvers (start_resolvers). It starts as the scene opens (starts_early), beside the renderer:
    the process and Unicorn take ~0.4 s more than the evaluator alone did (measured 2026-11-09)."""

    starts_early = True

    def __init__(self, map_path: Path | None, source: Path, rom_path: Path | None = None):
        self.map_path, self.source, self.rom_path = map_path, source, rom_path

    def unavailable(self, package: scene_package.Package) -> str | None:
        import evaluator_child
        if package.states is None:
            return "This recording carries no scene state, so it can only be shown from the recorded camera."
        if self.map_path is None or not self.map_path.is_file():
            return ("The free camera needs the game's address map, and this copy of the Trainer does not have "
                    "it. Reinstall the Trainer to get it back.")
        if self.rom_path is None:
            return "The free camera needs the ROM's file, and this scene was opened without it."
        if not evaluator_child.has_unicorn():
            return ("The free camera needs a part of the Trainer that is missing from this copy (the Unicorn "
                    "emulator). Reinstall the Trainer to get it back.")
        return None

    def __call__(self, package: scene_package.Package, rom: bytes):
        import evaluator_child
        return evaluator_child.EvaluatorClient(package, self.rom_path, self.map_path, self.source, wait=False)


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
    hud: bool = True             # the picture has the game's HUD (hud.py): False once it was cut
    hud_reason: str | None = None   # why a picture asked for without the HUD still has it
    ghosts: bool = False         # the picture carried the scene's ghosts
    view: dict | None = None     # a ghost's view: {"ghost": ref, "camera": the camera drawn, or None}
    rig: dict | None = None      # a cinematic camera: {"kind", "target", "shot", "side", "pulled", "raised"}
    used: dict | None = None     # the camera drawn when the server drew one: {"focus", "position", "roll", "fov"}
    mario: bool = True           # the stage's Mario is in the picture (False: left out, camera_state.hide_mario)


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
        self._evaluator_asked = False                    # a free camera, a pose or warm() used the evaluator
        self.image = HostImage() if getattr(host, "keeps_rdram", False) else None
        self._ahead: ThreadPoolExecutor | None = None    # the look-ahead thread, with the evaluator
        self._ahead_generation = 0                       # bumped by every free-camera request
        self._resolvers = None                           # state_resolver.Resolvers once the package is whole
        self._resolvers_tried = False
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
        self.ghosts = stage_ghosts.StageGhosts()         # the stage ghosts every picture draws (set_ghosts)
        self.ghost_look = stage_ghosts.Look()            # their settings (the lab sets the player's)
        self.ghost_origin = 0                            # the package frame that is every ghost's first
        self._ghost_problem: str | None = None           # why ghosts cannot draw ("" once set up)
        self._ghosts_warm = False                        # the renderer's ghost overlay has drawn a picture
        self._warmed_ahead = False                       # warm_ahead ran (once a scene)
        self.ghost_sounds = ghost_sound.SceneGhostSounds(map_path=getattr(evaluator_factory, "map_path", None))
        self.rigs = camera_rig.Planner()                 # the cinematic cameras' kept work (camera_rig.py)
        self._rig_tuning: dict | None = None             # the registry's rows a rig request may leave out
        self._runners: dict = {}                         # target -> (what it was read from, camera_rig.Runner)
        self._stage_reads: dict[int, tuple] = {}         # package frame -> the stage Mario's (position, yaw)
        self._stage_actions: dict[int, int | None] = {}  # package frame -> his action (gMarioStates.action)
        self.path_reader_factory = rig_path.PathReader   # open() may name another (tests: InProcessReader)
        self._path_reader = None                         # the reader of his path, once a rig asked for it
        self._path_reading: tuple | None = None          # (future, the package frames it reads)
        self._path_problem: str | None = None            # why his path could not be read
        self._walls: dict = {}                           # (level, area) -> camera_rig.StaticWalls or None

    @classmethod
    def open(cls, config, path: Path, *, sidecar: Path | None = None, live=None, fitter=None, host_factory=None,
             evaluator_factory=None, encoder_factory=None, path_reader_factory=None) -> SceneSession:
        """Reads and checks the package against the configured ROM, then starts and warms the host.
        Refused carries the reason for every refusal: the GPU hold, a missing ROM, a package the
        reader refuses (another ROM, truncated, corrupt), a package with nothing shown, a renderer
        that cannot start. With `sidecar`, the package is still being written (an attempt scene
        while its recording grows, Growth): the session opens what is published and grow()
        takes the rest. With `live` (live_audio.LiveAudio) and `fitter` (AttemptScenes.fit), a
        scene whose package has no sound yet plays the recorder's own (scene_audio.LiveSound)."""
        held = gpu_gate.hold_reason()
        if held:
            raise Refused(f"The Ghost Lab's pictures are on hold on this computer: {held}")
        rom_path, map_path = scene_inputs(config, path)
        if not rom_path.is_file():
            raise Refused("The game's ROM file that the Ghost Lab uses is missing. Open the game in Project64 once "
                          "while the Trainer is running, or choose its ROM file again.")
        growth = None if sidecar is None else Growth(path, sidecar)
        try:
            if path.stat().st_size > scene_package.MAX_PACKAGE_BYTES:
                raise Refused("This scene package is larger than the viewer accepts.")
            rom = rom_path.read_bytes()
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
        factory = evaluator_factory or GameCodeEvaluator(map_path, path, rom_path)
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
        session.path_reader_factory = path_reader_factory or rig_path.PathReader
        session._start_helpers()
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
                         "game_frame": identity.get("global_timer"), "package_frame": self.frames[position],
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
        if track.startswith(ghost_sound.TRACK_PREFIX):              # a ghost's own sound (ghost_sound.py)
            return self.ghost_sounds.block(self, track[len(ghost_sound.TRACK_PREFIX):], index)
        return self.sound.block(index, track)

    def _index(self, frame) -> int:
        if not isinstance(frame, int) or isinstance(frame, bool) or not 0 <= frame < len(self.frames):
            raise ValueError("no such frame")
        return self.frames[frame]

    def _recorded_frame(self, index: int, timings: dict, cuts: list | None = None) -> viewer.Frame:
        started = time.perf_counter()
        capture = self.package.frame(index)
        timings["decode_ms"] = _ms(started)
        runs = struct.pack(f"<{2 * len(capture.runs)}I", *(edge for run in capture.runs for edge in run))
        run_bytes = capture.run_bytes
        if cuts is not None:
            blanked = self._hud_cut(hud_cut.closure_words(capture.runs, run_bytes), capture.task, timings, cuts)
            run_bytes = hud_cut.blank_closure(capture.runs, run_bytes, blanked)
        if self.image is not None:
            self.image.recorded(capture.runs, run_bytes)
        return viewer.Frame(f"frame {index}", capture.task, capture.vi, runs, run_bytes)

    @staticmethod
    def _hud_cut(words, task, timings: dict, cuts: list) -> tuple:
        """The commands a picture without the HUD skips in the list at task (data_ptr, data_size:
        task words 12 and 13), its hud.Cut appended to `cuts`."""
        started = time.perf_counter()
        cuts.append(hud_cut.cut(words, task[12], task[13]))
        timings["hud_ms"] = _ms(started)
        return cuts[-1].blanked

    def _evaluator_or_reason(self, early: bool = False):
        """The evaluator, made on the first ask, or None and why the free camera cannot draw. `early`
        (open(), for a factory that starts_early: the evaluator's process) only starts it, so it opens
        beside the renderer; every other ask waits for it to be open (opened())."""
        if self.free_reason is None and self._evaluator is None:
            try:
                self._evaluator = self.evaluator_factory(self.package, self.rom)
            except Refused as refused:
                self.free_reason = str(refused)
            except Exception as error:  # noqa: BLE001 - the page shows it as the reason
                self.free_reason = f"The free camera could not start: {error}"
            if self._evaluator is not None and hasattr(self._evaluator, "prepare"):
                self._ahead = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scene-lab-ahead")
        if not early:
            self._evaluator_asked = True
            opened = getattr(self._evaluator, "opened", None)
            reason = opened() if opened is not None else None
            if reason:
                self.free_reason = f"The free camera could not start: {reason}"
                self._drop_evaluator()
        return self._evaluator, self.free_reason

    def _drop_evaluator(self) -> None:
        """Ends the evaluator (its process, when it has one) and its look-ahead thread."""
        ahead, self._ahead = self._ahead, None
        if ahead is not None:
            self._ahead_generation += 1                  # a running job stops after its frame
            ahead.shutdown(wait=True, cancel_futures=True)
        resolvers, self._resolvers = self._resolvers, None
        if resolvers is not None:
            resolvers.close()
        evaluator, self._evaluator = self._evaluator, None
        if hasattr(evaluator, "close"):                  # the evaluator's process (evaluator_child) ends
            evaluator.close()

    def _look_ahead(self, frame: int) -> None:
        """After a free-camera picture of `frame`, resolve the LOOK_AHEAD_FRAMES frames that follow
        it on the look-ahead thread (cached ones cost nothing): backward when the previous picture
        was a few frames after it (stepping back), else forward (playing, stepping, or a seek or
        the first picture, after which play goes forward). The frames next to it, not the last
        step repeated: playback that fell behind once asks for the frame due next. A drag (the
        same frame again) predicts nothing new. A job stops before its next frame once another
        free-camera request arrives (_ahead_generation), so a request never waits for more than
        the one frame being resolved, and the newest request's job runs next. With the state
        resolvers' children running, a drag hands them the frames after it too: they cost the
        server nothing, and a Play started from the dragged frame then finds its first states
        ready (without, its first pictures waited ~100 ms for a cold seek, measured 2026-10-27)."""
        step = None if self._last_free is None else frame - self._last_free
        self._last_free = frame
        if self._ahead is None or (step == 0 and self._resolvers is None):
            return
        direction = -1 if step is not None and -LOOK_AHEAD_STEP <= step < 0 else 1
        targets = [self.frames[at] for at in range(frame + direction, frame + direction * (RESOLVED_AHEAD + 1),
                                                   direction) if 0 <= at < len(self.frames)]
        if step == 0:
            if targets:
                self._resolve_ahead(targets)
            return
        if targets and not self._resolve_ahead(targets):
            self._ahead.submit(self._prepare, targets[:LOOK_AHEAD_FRAMES], self._ahead_generation)

    def _resolve_ahead(self, targets: list[int]) -> bool:
        """Hands `targets` to the state resolvers' children (state_resolver.py), started the first
        time the package is whole; False while they cannot take them (an evaluator without them, a
        growing package, children not ready yet or gone): the look-ahead thread resolves them. The
        game-code evaluator's process starts its own (evaluator_child: start_resolvers); an
        evaluator in this process (tests' doubles) is given the factory's (attach)."""
        evaluator = self._evaluator
        own = hasattr(evaluator, "start_resolvers")
        if not own and not hasattr(evaluator, "attach"):
            return False
        if not self._resolvers_tried and self.growth is None:
            self._resolvers_tried = True
            start = getattr(self.evaluator_factory, "resolvers", None)
            try:
                if own:
                    self._resolvers = evaluator.start_resolvers(targets[0], RESOLVED_AHEAD)
                else:
                    self._resolvers = start(self.package, evaluator.delivered, warm=targets[0]) if start else None
            except (OSError, ValueError, RuntimeError):  # the look-ahead thread goes on as before
                self._resolvers = None
            if self._resolvers is not None and not own:
                evaluator.attach(self._resolvers, RESOLVED_AHEAD)
        return evaluator.ahead(targets)

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

    def _free_frame(self, index: int, camera: tuple, timings: dict,
                    cuts: list | None = None) -> tuple[viewer.Frame | None, str | None]:
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
        ram = view.ram                                           # a copy without the HUD: the view stays cached
        if cuts is not None:
            ram = hud_cut.blank_ram(ram, self._hud_cut(hud_cut.ram_words(ram), task, timings, cuts))
        started = time.perf_counter()
        if self.image is not None:
            runs, data = self.image.free(ram)                    # the words the host lacks
        else:                                                    # the whole image; Project64's word order
            data = scene_package.swap_words(bytes(ram))
            runs = struct.pack("<II", 0, len(data))
        timings.update(send_bytes=len(data), pack_ms=_ms(started))
        return viewer.Frame(f"frame {index} free", tuple(task), self.outlines[index]["vi"], runs, data), None

    def picture(self, frame: int, camera=None, encoding: str = "jpeg", hud: bool = True) -> tuple[dict, bytes]:
        """Frame `frame` (a position in the frame list) as the renderer draws it: from the recorded
        camera when `camera` is None, else from that world camera, and without the game's HUD
        when `hud` is False (hud.py). Returns the header the page reads and the picture: JPEG,
        or ReadScreen's own BGR rows bottom-up ("raw")."""
        return self.finish(self.begin(frame, camera, encoding, hud))

    def begin(self, frame: int, camera=None, encoding: str = "jpeg", hud: bool = True,
              mario: bool = True) -> PendingPicture:
        """picture()'s first half, on the session's thread: everything up to the host (the package
        decode or the evaluator's view, the words the host lacks) and the request written. The host
        draws it while this thread begins the next picture; finish() collects it. `mario` False leaves
        the stage's Mario out of the redraw (camera_state.hide_mario); the recorded camera is then
        drawn again from its own pose, which redraws its picture exactly. Where the frame cannot be
        redrawn he shows, and the header's reason says so."""
        index = self._index(frame)
        if encoding not in ENCODINGS:
            raise ValueError("unknown encoding")
        if not isinstance(hud, bool):
            raise ValueError("hud is true or false")
        if not isinstance(mario, bool):
            raise ValueError("mario is true or false")
        view, camera = self._ghost_view(index, camera)
        rig_started = time.perf_counter()
        rig, camera = self._rig_view(frame, camera)
        camera, aim_reason = self.aim_blend(frame, camera)
        camera, blend_reason = self.recorded_blend(frame, camera)
        blend_reason = blend_reason or aim_reason
        redrawn = not mario and camera is None
        if redrawn:                                      # the recorded camera, drawn again from its pose
            pose = self.pose(frame)["pose"]
            camera = None if pose is None else {key: pose[key] for key in ("focus", "position", "roll", "fov")}
        free = None if camera is None else world_camera(camera)
        if free is not None and not mario:
            free = (*free, None, None)[:4] + (True,)
        if free is not None:
            self._ahead_generation += 1          # the look-ahead stops before its next frame
        started = time.perf_counter()
        timings: dict = {} if rig is None else {"rig_ms": _ms(rig_started)}   # a cinematic camera's own work
        cuts = None if hud else []               # the HUD cut's outcome (hud.Cut) for the frame sent
        host_frame, reason = (None, None) if free is None else self._free_frame(index, free, timings, cuts)
        drawn_free = host_frame is not None
        if view is not None:                 # the recorded camera when the ghost's could not be drawn
            view, reason = {"ghost": view["ghost"], "camera": camera if drawn_free else None}, reason or view["reason"]
        if rig is not None:                  # the recorded camera when the rig had none or it could not be drawn
            own = rig.pop("reason")
            reason = reason or own
        if redrawn and not drawn_free:                   # the recorded picture as it was, Mario in it
            reason = MARIO_SHOWN
        reason = reason or blend_reason
        used =None if not drawn_free else {"focus": camera["focus"], "position": camera["position"],
                                            "roll": camera.get("roll"), "fov": camera.get("fov")}
        if drawn_free:
            self._look_ahead(frame)          # resolves beside this picture's render and encode
        if host_frame is None:
            host_frame = (self._stage_frame(index, timings, cuts) if self.ghosts
                          else self._recorded_frame(index, timings, cuts))
        host_frame.ghosts = self._ghost_block(index)
        self.host.send(host_frame)
        timings["begin_ms"] = _ms(started)
        cut = cuts[-1] if cuts else None
        pending = PendingPicture(frame, encoding, "free" if drawn_free else "recorded", reason, timings, started,
                                 cut is None or cut.reason is not None, cut and cut.reason,
                                 ghosts=host_frame.ghosts is not None, view=view, rig=rig, used=used,
                                 mario=mario or not drawn_free)
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
                "hud": pending.hud, "hud_reason": pending.hud_reason, **({} if pending.mario else {"mario": False}),
                "payload_bytes": len(payload), "timings": timings, "camera_used": pending.used,
                **({"ghosts": picture.ghosts} if pending.ghosts else {}),
                **({"view": pending.view} if pending.view else {}),
                **({"rig": pending.rig} if pending.rig else {})}, payload

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
        # The recorded camera's pick only asks the evaluator (started early) for Mario: it is no free-camera use.
        evaluator, reason = (None, None) if camera is None and self._evaluator is None \
            else self._evaluator_or_reason(early=camera is None)
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

    def set_ghosts(self, entries, load_ghost, origin=None) -> dict:
        """Makes the scene's ghosts the page's whole set ([{"ref", "label"}], in order; [] removes
        them): {"shown": refs, "refused": [{"ref", "reason"}]} (stage_ghosts.StageGhosts.replace). Each
        new ghost's recording loads into the renderer once, here; every picture begun after this
        draws the set. `origin`, a frame position, is the scene frame every ghost's first frame
        stands on (default the package's first frame: an attempt scene's first frame). The first
        set also builds the renderer's ghost model and draws until its overlay is ready, so the
        picture the page asks for next shows them. ValueError for a malformed set or origin."""
        wanted = stage_ghosts.checked(entries)
        if origin is not None:
            self.ghost_origin = self._index(origin)
        if not wanted:
            self.ghosts.clear(self.host)
            return {"shown": [], "refused": []}
        problem = self._ghosts_unavailable()
        if problem:
            return {"shown": [], "refused": [{"ref": entry["ref"], "reason": problem} for entry in wanted]}
        answer = self.ghosts.replace(self.host, wanted, load_ghost)
        if self.ghosts and not self._ghosts_warm:
            self._warm_ghosts()
        return answer

    def _ghosts_unavailable(self) -> str | None:
        """Why this scene cannot draw ghosts, or None; sets the renderer up the first time."""
        if self.package.states is None:
            return stage_ghosts.NO_SCENE_STATE
        if self._ghost_problem is None:
            from sm64_events.memory.ghost_layout import GhostLayout
            from sm64_events.memory.layout import layout_for
            version = {ord("E"): "us", ord("J"): "jp"}.get(self.rom[0x3E])
            try:
                layout = GhostLayout.from_layout(layout_for(version)).to_bytes()
            except (KeyError, TypeError, ValueError):
                self._ghost_problem = "Ghosts are drawn on the US and JP practice ROMs only."
                return self._ghost_problem
            result = self.host.ghost_setup(layout, self.rom)
            self._ghost_problem = "" if not result else stage_ghosts.NO_RENDERER if result == viewer.GV_ABSENT \
                else stage_ghosts.REFUSALS.get(result, stage_ghosts.NO_RENDERER)
        return self._ghost_problem or None

    def _warm_ghosts(self) -> None:
        """Draws the stage's first shown picture with the set (pictures nobody sees, answered to
        this thread: host.call) until the renderer's ghost overlay reaches them: it sets itself up
        on its own lists, retrying a refused step a bounded number of lists later. A shown one: an
        attempt scene's first task may be one no present showed, and it has no VI registers."""
        index = next((at for at in self.frames if at >= self.ghost_origin), self.frames[0])
        frame = self._stage_frame(index, {})
        frame.ghosts = self._ghost_block(index)
        for _ in range(GHOST_WARM_PICTURES):
            if not self.host.call(frame).ghosts["pending"]:
                self._ghosts_warm = True
                return

    def _ghost_block(self, index: int) -> bytes | None:
        """This picture's ghosts (stage_ghosts.StageGhosts.block) at the game frames since the stage's
        first frame, by the tap's global_timer: lag frames and held pictures stay in step."""
        if not self.ghosts:
            return None
        frame = self._ghost_frame(index)
        return None if frame is None else self.ghosts.block(self.ghost_look, frame)

    def _ghost_frame(self, index: int) -> int | None:
        """Game frames from the ghosts' first frame to package frame `index` (stage_ghosts.ghost_frame),
        None when either has no tap clock."""
        timer = self.outlines[index]["identity"].get("global_timer")
        origin = self.outlines[self.ghost_origin]["identity"].get("global_timer")
        return None if timer is None or origin is None else stage_ghosts.ghost_frame(timer, origin)

    def _ghost_view(self, index: int, camera) -> tuple[dict | None, dict | None]:
        """A picture's camera as begin() takes it: (None, camera) for the recorded camera or a free
        one; for {"ghost": ref}, that ghost's view ({"ghost", "reason"}) and its camera at the row its
        drawing shows in this picture, in the stage's level and area (stage_ghosts.StageGhosts.view),
        None when it has none: the recorded camera, and the reason says why."""
        if not (isinstance(camera, dict) and "ghost" in camera):
            return None, camera
        ref = camera["ghost"]
        if len(camera) != 1 or not isinstance(ref, str) or not ref or len(ref) > 512:
            raise ValueError('a ghost\'s view is {"ghost": ref}, a ghost in the scene')
        frame = self._ghost_frame(index)
        if frame is None:
            return {"ghost": ref, "reason": stage_ghosts.VIEW_NO_CLOCK}, None
        identity = self.outlines[index]["identity"]
        place = (identity.get("curr_level"), identity.get("curr_area"))
        found, reason = self.ghosts.view(ref, frame, None if None in place else place)
        try:
            if found is not None:
                world_camera(found)                  # a recorded camera the evaluator cannot place
        except ValueError:
            found, reason = None, stage_ghosts.VIEW_NONE
        return {"ghost": ref, "reason": reason}, found

    def _rig_view(self, frame: int, camera) -> tuple[dict | None, dict | None]:
        """A picture's camera as begin() takes it: (None, camera) for any but a cinematic camera; for
        {"rig": {...}} (camera_rig.py), what the header says of it ({"kind", "target", "shot", "side",
        "pulled", "reason"}) and its camera at frame position `frame`, None when it has none: the
        recorded camera, and the reason says why. ValueError for a malformed rig."""
        if not (isinstance(camera, dict) and "rig" in camera):
            return None, camera
        if len(camera) != 1:
            raise ValueError('a cinematic camera is {"rig": {...}} and nothing else')
        rig = camera_rig.Rig.of(camera["rig"], self._shipped_rig_tuning())
        said = {"kind": rig.kind, "target": rig.target, "shot": rig.shot, "side": None, "pulled": None, "raised": None}
        if self.free_reason is not None:
            return {**said, "reason": self.free_reason}, None
        runner = self._runner(rig.target)
        if isinstance(runner, str):
            return {**said, "reason": runner}, None
        if frame >= len(runner):                          # a growing scene's newest frames, still being read
            return {**said, "reason": camera_rig.PATH_PENDING}, None
        shot = self.rigs.shot(rig, runner, frame, self._walls_at)
        if shot.camera is not None:
            said.update(side=shot.side, pulled=round(shot.pulled, 3), raised=round(shot.raised, 1))
        return {**said, "reason": shot.reason}, shot.camera

    def _shipped_rig_tuning(self) -> dict:
        """The registry's rows a rig request may leave out, read once a session."""
        if self._rig_tuning is None:
            from sm64_events.server.tuning_api import shipped_numbers
            self._rig_tuning = shipped_numbers("scenecamera", camera_rig.TUNING_KEYS)
        return self._rig_tuning

    def warm_rig(self, frame: int) -> None:
        """The first cinematic camera's own work, done while nobody waits (the lab calls it once Mario's
        path is read and the page is idle): his runner, the smoothed track of the shipped tuning and
        Front's windows at `frame` (the first rig picture otherwise took ~90 ms of the worker,
        measured 2026-10-27). A request with other numbers only redoes what they change."""
        self._index(frame)
        if self.free_reason is not None:
            return
        try:
            runner = self._runner("stage")
            if not isinstance(runner, str) and frame < len(runner):
                self.rigs.shot(camera_rig.Rig.of({"kind": "front"}, self._shipped_rig_tuning()), runner, frame,
                               self._walls_at)
        except Exception as error:  # noqa: BLE001 - the first cinematic picture that needs it says why
            log.warning("scene viewer: warming the cinematic cameras failed: %s", error)

    def _clock(self) -> list[float]:
        """Each frame position's time on the scene's own clock, seconds after the first picture: the
        recorded times the page plays by (describe's recorded_ms), else one game frame each."""
        times = [self.outlines[index]["present_us"] for index in self.frames]
        if None not in times and all(a <= b for a, b in zip(times, times[1:], strict=False)):
            return [(time_us - times[0]) / 1e6 for time_us in times]
        return [position / FPS for position in range(len(self.frames))]

    def _place(self, index: int) -> tuple | None:
        identity = self.outlines[index]["identity"]
        place = (identity.get("curr_level"), identity.get("curr_area"))
        return None if None in place else place

    def _runner(self, target: str):
        """The runner a rig follows (camera_rig.Runner): the stage's Mario ("stage") over the frame
        positions his path has been read for, from the first (all of them once it is: prepare_path),
        or a scene ghost over every one; kept until those frames change; a sentence when there is
        none (yet)."""
        if self.package.states is None:
            return camera_rig.NO_STATE
        if target != "stage":
            rows = self.ghosts.rows(target)
            if rows is None:
                return stage_ghosts.VIEW_GONE
            count, source = len(self.frames), (target, rows, self.ghost_origin, tuple(self.frames))
        else:
            poses = self._stage_poses()
            if not poses:                                # not read yet, or it could not be
                return self._path_problem or camera_rig.PATH_PENDING
            count = len(poses)
            source = ("stage", tuple(self.frames[:count]), self.package)
        kept = self._runners.get(target)
        if kept is not None and kept[0] == source:
            return kept[1]
        frames = self.frames[:count]
        places = [self._place(index) for index in frames]
        if target != "stage":
            poses = [None if g is None else rows.pose(g, place)
                     for g, place in zip(map(self._ghost_frame, frames), places, strict=True)]
        positions = [pose[0] if pose else [math.nan] * 3 for pose in poses]
        yaws = [pose[1] if pose else math.nan for pose in poses]
        runner = camera_rig.Runner(positions, yaws, [place if pose else None for place, pose in zip(places, poses, strict=True)],
                                   self._clock()[:count], camera_rig.NO_RUNNER if target == "stage" else camera_rig.GHOST_ELSEWHERE)
        self._runners[target] = (source, runner)
        return runner

    def _stage_poses(self) -> list[tuple | None]:
        """The stage's Mario in the frame positions read so far, from the first ((position, the game's
        s16 facing), or None where his position is not a place): all of them once prepare_path's
        reads are done, fewer while a growing scene's newest are read; [] before any is, or when his
        path could not be read."""
        self.prepare_path()
        if self._path_problem:
            return []
        poses = []
        for index in self.frames:
            if index not in self._stage_reads:
                break
            poses.append(self._stage_reads[index])
        return poses

    def prepare_path(self) -> Future:
        """Starts reading the stage Mario's place in the frames not read yet, in the path reader's
        child process (rig_path.py: about 1.2 ms a frame, never on this thread), and returns the
        future the read ends with: done at once when nothing is left to read, LookupError when his
        path cannot be read. A growing scene reads only the frames it gained. On the worker, which
        alone touches the reads; the reader's own thread only finishes the future."""
        self._take_path()
        if self._path_problem:
            failed = Future()
            failed.set_exception(LookupError(self._path_problem))
            return failed
        if self._path_reading is None:
            mario = self._mario_offset()
            missing = [index for index in self.frames if index not in self._stage_reads]
            if missing and (mario is None or self.package.states is None):
                self._stage_reads.update(dict.fromkeys(missing))       # no place to read: none
                missing = []
            if not missing:
                done = Future()
                done.set_result(None)
                return done
            if self._path_reader is None:
                try:
                    self._path_reader = self.path_reader_factory()
                except OSError as error:          # the child could not start: read here, as before
                    log.warning("scene viewer: the path reader could not start (%s); reading here", error)
                    self._path_reader = rig_path.InProcessReader()
            data = self.package.data
            data = data if isinstance(data, bytes) else data[0:len(data)]
            self._path_reading = (self._path_reader.read(data, self.rom, missing, mario), missing)
        future = self._path_reading[0]
        self._take_path()                          # a reader in this process is done already
        return future

    def _start_helpers(self) -> None:
        """The child processes an opening scene starts beside the renderer: the evaluator's, for a
        factory that starts_early (GameCodeEvaluator), and the path reader (_start_path)."""
        if getattr(self.evaluator_factory, "starts_early", False):
            self._evaluator_or_reason(early=True)
        self._start_path()

    def _start_path(self) -> None:
        """Mario's path for the cinematic cameras, read beside the opening scene (prepare_path) where
        they can draw: the free camera's evaluator and its symbol map are there."""
        map_path = getattr(self.evaluator_factory, "map_path", None)
        if self.free_reason is None and map_path is not None and Path(map_path).is_file():
            try:
                self.prepare_path()
            except Exception as error:  # noqa: BLE001 - the cameras ask again, and say why if it fails
                log.warning("scene viewer: Mario's path could not start reading: %s", error)

    def _take_path(self) -> None:
        """A finished read's places, kept per package frame; its failure, said from then on."""
        reading = self._path_reading
        if reading is None or not reading[0].done():
            return
        self._path_reading = None
        try:
            rows = reading[0].result()
        except Exception as error:  # noqa: BLE001 - the cinematic cameras say why they cannot follow him
            self._path_problem = f"Mario's path could not be read, so this shows the recorded camera ({error})."
            return
        for index, row in zip(reading[1], rows, strict=True):
            self._stage_reads[index] = None if np.isnan(row[0]) else ([float(v) for v in row[:3]], int(row[3]))
            self._stage_actions[index] = int(row[4]) if len(row) > 4 and np.isfinite(row[4]) else None

    def actions(self, target: str = "stage") -> dict:
        """Each frame position's action, for the Scene viewer's step to the next or previous action
        change: the stage's Mario's ("stage", gMarioStates.action from the frames his path has been read
        for, from the first: prepare_path) or a scene ghost's (its clip's live_action in the row its
        drawing shows). {"actions": [code or None], "names": {code: the action's name in plain words
        (memory/addresses.py action_label)}, "reason": why there are none, or None}."""
        from sm64_events.memory.addresses import action_label
        if target == "stage":
            self._take_path()
            if self._path_problem or self.package.states is None:
                return {"actions": [], "names": {}, "reason": self._path_problem or camera_rig.NO_STATE}
            codes = []
            for index in self.frames:
                if index not in self._stage_reads:            # not read yet
                    break
                codes.append(self._stage_actions.get(index))
        else:
            rows = self.ghosts.rows(target)
            if rows is None:
                return {"actions": [], "names": {}, "reason": stage_ghosts.VIEW_GONE}
            codes = [None if (ghost_frame := self._ghost_frame(index)) is None else rows.action(ghost_frame)
                     for index in self.frames]
        names = {str(code): action_label(code) for code in sorted({code for code in codes if code})}
        return {"actions": codes, "names": names, "reason": None}

    def _mario_offset(self) -> int | None:
        """gMarioStates' RDRAM offset on this ROM (US or JP), None on another."""
        from sm64_events.memory.layout import layout_for
        try:
            return layout_for({ord("E"): "us", ord("J"): "jp"}[self.rom[0x3E]]).mario_struct & 0x1FFFFFFF
        except KeyError:
            return None

    def _walls_at(self, place: tuple):
        """The level's static collision in `place` (camera_rig.static_walls), read once from the scene
        state of that place's middle frame; None without the symbol map's two addresses."""
        if place not in self._walls:
            symbols = camera_rig.map_symbols(getattr(self.evaluator_factory, "map_path", None), camera_rig.WALL_SYMBOLS)
            there = [index for index in self.frames if self._place(index) == place]
            walls = None
            if len(symbols) == len(camera_rig.WALL_SYMBOLS) and there and self.package.states is not None:
                index = there[len(there) // 2]
                try:
                    walls = camera_rig.static_walls(lambda offset, size: self.package.scene_bytes(index, [(offset, size)])[0],
                                                    symbols["sSurfacePool"], symbols["gNumStaticSurfaces"])
                except ValueError:                      # a span outside RDRAM: no walls known
                    walls = None
            self._walls[place] = walls
        return self._walls[place]

    def _stage_frame(self, index: int, timings: dict, cuts: list | None = None) -> viewer.Frame:
        """The recorded picture with the frame's scene state beside its closure (package.scene_state:
        the regions the tap recorded, closure included, in Project64's word order): where the
        in-game ghost reads the stage's camera, level and area. Without scene state, the closure.
        `cuts` as _recorded_frame's: the HUD's commands skipped (hud.py)."""
        if self.package.states is None:
            return self._recorded_frame(index, timings, cuts)
        started = time.perf_counter()
        task, _regions, ram, known = self.package.scene_state(index)
        present = np.concatenate(([False], known.view(np.uint32) != 0, [False]))
        edges = np.flatnonzero(present[1:] != present[:-1]) * 4
        runs = list(zip(edges[0::2].tolist(), edges[1::2].tolist(), strict=True))
        data = b"".join(ram[begin:end].tobytes() for begin, end in runs)
        if cuts is not None:
            blanked = self._hud_cut(hud_cut.closure_words(runs, data), task.task, timings, cuts)
            data = hud_cut.blank_closure(runs, data, blanked)
        if self.image is not None:
            self.image.recorded(runs, data)
        timings["decode_ms"] = _ms(started)
        return viewer.Frame(f"frame {index} stage", tuple(task.task), self.outlines[index]["vi"],
                            struct.pack(f"<{2 * len(runs)}I", *(edge for run in runs for edge in run)), data)

    def warm(self, frame: int) -> None:
        """Starts what the first free or cinematic camera of this scene would start, while nobody
        waits for it (the lab calls it once the page is idle after the first picture, then
        warm_ahead once it is idle again): the free camera's evaluator and one view of `frame` from
        its recorded camera (pose: ~200 ms in all, measured 2026-10-27). Nothing where the free
        camera cannot draw; a failure here is the first camera's to report. An evaluator started
        early (its process, opening beside the renderer) is warmed here too."""
        if self.free_reason is not None or self._evaluator_asked:
            return
        try:
            self.pose(frame)
        except Exception as error:  # noqa: BLE001 - the first camera that needs it says why
            log.warning("scene viewer: warming the free camera failed: %s", error)

    def warm_ahead(self, frame: int) -> None:
        """warm()'s second step: the next frames' states here (a Play from this frame asks for them
        first, and a state resolver child's first frame is cold: ~115 ms), the children for the ones
        after (_look_ahead's first start: process creation measured 0.2-1.3 s, 2026-10-27), and the
        walls of that frame's place. Once a scene; nothing before warm() started the evaluator."""
        if self._evaluator is None or not self._evaluator_asked or self._warmed_ahead:
            return
        self._warmed_ahead = True
        try:
            index = self._index(frame)
            after = [self.frames[at] for at in range(frame + 1, min(len(self.frames), frame + 1 + WARM_FRAMES
                                                                     + RESOLVED_AHEAD))]
            for following in after[:WARM_FRAMES]:
                self._evaluator.prepare(following)
            if after[WARM_FRAMES:]:
                self._resolve_ahead(after[WARM_FRAMES:])
            place = self._place(index)
            if place is not None and self.package.states is not None:
                self._walls_at(place)
        except Exception as error:  # noqa: BLE001 - the first camera that needs it says why
            log.warning("scene viewer: warming the free camera failed: %s", error)

    def aim_blend(self, frame: int, camera) -> tuple[dict | None, str | None]:
        """A camera path's camera whose keys aim at a runner (ui/components/scenepath.js): {..., "aim":
        {target: weight}} ("stage" or a scene ghost's ref, weights 0 to 1 that sum to at most 1) has its
        focus moved that share of the way to each runner's eyes on his lightly smoothed path at frame
        position `frame` (camera_rig.Track.aim, what the cinematic cameras aim at). Returns the camera
        without "aim" and None; a runner with no place here (his path not read yet, another area, a
        ghost gone) keeps the camera's own focus for its share, and the reason says why. Any other
        camera comes back as it is. ValueError for weights that are not that."""
        if not (isinstance(camera, dict) and "aim" in camera):
            return camera, None
        weights = camera["aim"]
        if not (isinstance(weights, dict) and len(weights) <= stage_ghosts.MAX_GHOSTS + 1
                and all(isinstance(target, str) and 0 < len(target) <= 512 and _number(weight) and 0 <= weight <= 1
                        for target, weight in weights.items()) and sum(weights.values()) <= 1 + 1e-6):
            raise ValueError("a camera's aim is {runner: weight}, weights 0 to 1 summing to at most 1")
        own = {key: value for key, value in camera.items() if key != "aim"}
        focus, reason = np.array(own.get("focus"), np.float64), None
        if focus.shape != (3,):
            raise ValueError("camera focus must be three finite world coordinates")
        moved = focus.copy()
        for target, weight in weights.items():
            point, missing = self._aim_point(frame, target)
            if point is None:
                reason = reason or missing
            else:
                moved += weight * (point - focus)
        if math.dist(moved, own["position"]) >= 1.0:          # never on its own place
            own["focus"] = [float(value) for value in moved]
        return own, reason

    def _aim_point(self, frame: int, target: str):
        """A runner's eyes on his lightly smoothed path at frame position `frame` (the point a cinematic
        camera aims at), or None and why."""
        runner = self._runner(target)
        if isinstance(runner, str):
            return None, runner
        if frame >= len(runner):
            return None, camera_rig.PATH_PENDING
        track = self.rigs.track(camera_rig.Rig.of({"kind": "front", "target": target}, self._shipped_rig_tuning()), runner)
        point = track.aim[frame]
        return (point, None) if np.isfinite(point).all() else (None, runner.reason)

    def recorded_blend(self, frame: int, camera) -> tuple[dict | None, str | None]:
        """A camera path's camera on its way into or out of the recorded camera (ui/components/
        scenepath.js): {"focus", "position", "roll", "fov", "recorded": weight} is drawn `weight` (0
        to 1) of the way from that camera to frame `frame`'s recorded one (pose): place and aim in
        straight lines, the roll the short way round, the lens evenly (blend_cameras). Returns the
        camera to draw and None; any other camera comes back as it is. Where the frame has no
        recorded camera to blend with, the path's own camera and the reason. ValueError for a weight
        that is not 0 to 1."""
        if not (isinstance(camera, dict) and "recorded" in camera):
            return camera, None
        weight = camera["recorded"]
        if not (_number(weight) and 0 <= weight <= 1):
            raise ValueError("a camera's recorded weight is a number from 0 to 1")
        own = {key: value for key, value in camera.items() if key != "recorded"}
        world_camera(own)
        if weight == 0:
            return own, None
        answer = self.pose(frame)
        if answer["pose"] is None:
            return own, answer["reason"]
        return blend_cameras(own, answer["pose"], float(weight)), None

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
        self._drop_evaluator()
        self._last_view = self._last_walk = None
        self.ghost_sounds.close()
        reader, self._path_reader = self._path_reader, None
        if reader is not None:
            reader.close()
        try:
            return self.host.close()
        finally:
            encoder, self.encoder = self.encoder, None
            if encoder is not None:
                encoder.close()
            if self.slots is not None:
                self.slots.close()
