"""Ghost sound in the Scene viewer (M4 round 5, item 4, lab only): every ghost added to a scene
plays its own recorded sound effects, never music, on the stage's own sound clock.

    sounds = SceneGhostSounds(scene_inputs)          # one per open scene (scene_session.py)
    data, media_type = sounds.block(ghost, index)     # one block of that ghost's track, a .wav
    sounds.close()

Griffin (round 5, item 4): "For music, we should use the music of the base ghost; for sound
effects, we should play the sound effects back for each of the ghosts independently, right?"
The stage plays its own recording (and its Music / Sound effects split); each ghost adds a track
of its own, which the page mixes under the viewer's volume and Mute with a mute per ghost.

A ghost sounds by the in-game ghost's rules (docs/ghost-native-runtime.md, "Ghost sound playback"):
- Events: the play_sound requests its recording carries per record (ghost/clip.py Frame.sounds,
  VALID_SOUND), each record's at its own game frame. Capture keeps only Mario's own requests, so
  no ghost ever plays music.
- Samples: SM64's own audio engine, the in-game ghost's (renderer/model/audio.h, the staged and
  patched libsm64 copy, under ghost_audio::known_audio's ROM check), in ghost_sound_host.cpp: one
  engine per ghost, one tick per game frame.
- Volume: the engine's camera falloff and pan from the ghost's root under the stage's recorded
  camera (gLakituState, as cameraToObject places Mario's own sounds), times min(1, NEAR / d) from
  the stage's Mario, times the player's ghost volume (ghost/settings.py); hidden (another level
  or area, a gap, past its recording) is silent, a star ghost's dance tail fades over
  STAR_FADE_FRAMES, as ghost_viewer.cpp choose shows it.

Timing: scene frame k shows ghost frame g(k) = global_timer(k) - global_timer(origin)
(stage_ghosts.ghost_frame), and its picture appears at the recording's sample S[k] (scene_audio,
"as heard"). The ghost's game frame g sounds from its tick's first engine sample, so the track
maps each recording sample to an engine position through the knots (S[k], start of tick g(k)),
linearly between them, and reads the engine's 32 kHz output there (stems_check.interp): a jump
sound lands on the picture of the jump. Placement is read every PLACE_EVERY scene frames and
held between.

Where it is mixed: the engine is native (one process per open scene), so the server renders each
ghost's track once, on the first block the page asks for, and serves it as blocks that tile the
scene's own sound; the page's worklet mixes those blocks on the clock that already plays the
stage, so a ghost is in sample sync with the stage by construction, and a mute or the volume
changes a gain within 64 samples. Mixing on the server instead would cost a new render for every
mute. Measured 2026-10-27 on his 655-frame HMC attempt scene with one of his own runs: 1.37 s for
the first block (the stage's camera and Mario, 110 scene-state reads: 1.0 s; the engine, 656
ticks and 51 cues: 88 ms; laying it on 1.2 M samples: 0.26 s), every later block 0.05 ms, 4.8 MB
held per ghost. The stage's own sound is "as heard" (scene_audio.SOUND_SHIFT_MS 0): on that scene
it lands 148 ms after its picture, Project64's audio delay; a ghost's lands on its picture.
"""
from __future__ import annotations

import hashlib
import importlib.util
import logging
import math
import os
import re
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import scene_audio  # noqa: E402
import stems_check as sc  # noqa: E402

NATIVE = ROOT / "plugin/gfxwrap"
SOURCES = (HERE / "ghost_sound_host.cpp", NATIVE / "ghost_audio.cpp", NATIVE / "ghost_audio.h",
           NATIVE / "ghost_assets.cpp", NATIVE / "ghost_assets.h", NATIVE / "ghost_tuning.h",
           NATIVE / "ghost_playback.h", ROOT / "renderer/model/audio.h")
ENGINE_RATE = 32000            # SA_RATE
GAME_FPS = 30
NEAR = 400.0                   # GP_SOUND_NEAR: full gain this near the stage's Mario
STAR_FADE_FRAMES = 15          # GP_STAR_FADE_FRAMES: a star ghost's dance tail fades this long
PLACE_EVERY = 6                # scene frames between reads of the stage's camera and Mario
TICK = struct.Struct("<4fI")   # ghost_sound_host.cpp Tick: x, y, z, gain, cues
LAKITU_FOCUS, LAKITU_POS = 0x80, 0x8C          # struct LakituState (camera_state.py)
TRACK_PREFIX = "ghost:"
NO_TRACK = "This ghost is not in the scene."
NO_SOUND = "This ghost's sound could not be made."
log = logging.getLogger("sm64.scene_lab")


def track_name(ref: str) -> str:
    return TRACK_PREFIX + ref


# ---------------------------------------------------------------- the engine host

def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build() -> Path:
    """ghost_sound_host.exe (x86, the model library's audio engine), built once per input hash
    into the scene lab's build cache, as viewer_host.exe is."""
    compiler = _module("ghost_sound_build_plugin", ROOT / "tools/build_plugin.py")
    model = _module("ghost_sound_build_model", ROOT / "tools/build_model.py")
    digest = hashlib.sha256(model.component_hash().encode() + b"".join(path.read_bytes() for path in SOURCES))
    cache = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "SM64Trainer/build-cache"
    work = cache / "scene-lab" / f"ghost-sound-{digest.hexdigest()[:16]}"
    target = work / "ghost_sound_host.exe"
    if target.is_file():
        return target
    vcvars = compiler.find_vcvars32()
    if vcvars is None:
        raise RuntimeError("Ghost sound needs the x86 MSVC toolchain to build its engine.")
    work.mkdir(parents=True, exist_ok=True)
    library = model.build_model(work / "model", cache=cache / "model", vcvars32=vcvars)
    source = work / "model/source/src"
    compiler._cl(vcvars, ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17", f"/I{NATIVE}",
                          f"/I{ROOT / 'renderer/model'}", f"/I{source}", f"/I{source / 'decomp/include'}",
                          str(SOURCES[0]), str(NATIVE / "ghost_audio.cpp"), str(NATIVE / "ghost_assets.cpp"),
                          str(library), f"/Fe:{target}", f"/Fo{work}\\", "/link", "winmm.lib", "bcrypt.lib",
                          *compiler.LIBS], work)
    return target


class EngineHost:
    """One ghost_sound_host.exe (hidden) and its pipes: render() runs one ghost's ticks."""

    def __init__(self, rom: bytes, command: list[str] | None = None):
        from sm64_events.core.childproc import quiet_spawn_kwargs
        self.process = subprocess.Popen(command or [str(build())], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, **quiet_spawn_kwargs())
        self.lock = threading.Lock()
        self.process.stdin.write(b"ROM1" + struct.pack("<I", len(rom)) + bytes(rom))
        self.process.stdin.flush()

    def render(self, ticks: list[tuple]) -> tuple[np.ndarray, np.ndarray]:
        """(engine frames per tick, int16 stereo PCM at ENGINE_RATE) for ticks of
        ((x, y, z) camera-relative, gain, [sound bits])."""
        cues = [bits for _, _, sounds in ticks for bits in sounds]
        body = b"".join(TICK.pack(*position, gain, len(sounds)) for position, gain, sounds in ticks)
        with self.lock:
            stdin, stdout = self.process.stdin, self.process.stdout
            stdin.write(b"GSR1" + struct.pack("<II", len(ticks), len(cues)) + body
                        + struct.pack(f"<{len(cues)}I", *cues))
            stdin.flush()
            head = stdout.read(8)
            if len(head) < 8:
                raise RuntimeError(f"the ghost sound engine stopped (exit {self.process.poll()})")
            status, frames = struct.unpack("<iI", head)
            lengths = np.frombuffer(stdout.read(4 * len(ticks)), "<u4").astype(np.int64)
            pcm = np.frombuffer(stdout.read(4 * frames), "<i2").reshape(-1, 2)
        if status != 0 or len(lengths) != len(ticks) or len(pcm) != frames:
            raise RuntimeError(f"the ghost sound engine refused the ghost ({status})")
        return lengths, pcm

    def close(self) -> int | None:
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            return self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            return self.process.wait(timeout=5)


# ---------------------------------------------------------------- where a ghost sounds from

def camera_relative(position, focus, world) -> tuple[float, float, float] | None:
    """`world` in the stage camera's space as SM64's mtxf_lookat builds it (roll 0): +x right,
    +y up, -z ahead, the space cameraToObject hands the engine for Mario's own sounds. None for a
    camera looking straight down or up, or on its focus."""
    dx, dz = focus[0] - position[0], focus[2] - position[2]
    along = math.hypot(dx, dz)
    z = [focus[i] - position[i] for i in range(3)]
    length = math.sqrt(sum(value * value for value in z))
    if along == 0 or length == 0:
        return None
    z = [-value / length for value in z]
    y0 = (0.0, 1.0, 0.0)
    x = [y0[1] * z[2] - y0[2] * z[1], y0[2] * z[0] - y0[0] * z[2], y0[0] * z[1] - y0[1] * z[0]]
    length = math.sqrt(sum(value * value for value in x))
    x = [value / length for value in x]
    y = [z[1] * x[2] - z[2] * x[1], z[2] * x[0] - z[0] * x[2], z[0] * x[1] - z[1] * x[0]]
    offset = [world[i] - position[i] for i in range(3)]
    return tuple(sum(axis[i] * offset[i] for i in range(3)) for axis in (x, y, z))


def distance_gain(stage_mario, ghost) -> float:
    """min(1, NEAR / d) from the stage's Mario (ghost_playback.cpp sound_gain); 1 when unknown."""
    if stage_mario is None:
        return 1.0
    apart = math.dist(stage_mario, ghost)
    return NEAR / apart if apart > NEAR else 1.0


# ---------------------------------------------------------------- one ghost's ticks and track

def ghost_rows(clip) -> tuple[int, int, dict]:
    """(first coordinate, last coordinate, coordinate: frame) of a decoded clip, as ghost_viewer.cpp
    choose indexes it (the clip's canonical frames)."""
    rows = clip.canonical_frames()
    return min(rows), max(rows), rows


def root_of(frame) -> tuple[float, float, float] | None:
    from sm64_events.ghost.native_layout import POSE_AT, VALID_POSE
    if not frame.valid_fields & VALID_POSE:
        return None
    return struct.unpack_from("<3f", frame.raw, POSE_AT)


def plan_ticks(clip, ghost_frames: range, stage, volume: float, dance_tail: bool) -> list[tuple]:
    """The engine's ticks for ghost frames `ghost_frames` (game frames since the stage's first,
    each a tick): its cues, where it sounds from and its gain. `stage(g)` gives the stage at ghost
    frame g: (camera position, focus, Mario, level, area), any of them None when unknown."""
    first, last, rows = ghost_rows(clip)
    ticks, root = [], None
    for g in ghost_frames:
        target = first + max(g, 0)
        fade = 1.0
        if target > last:
            past = target - last
            fade = (STAR_FADE_FRAMES - past) / STAR_FADE_FRAMES if dance_tail and past < STAR_FADE_FRAMES else 0.0
            target = last
        frame = rows.get(target)
        camera, focus, mario, level, area = stage(g)
        root = (root_of(frame) if frame is not None else None) or root
        shown = frame is not None and g >= 0 and fade > 0 and (level is None or (frame.level, frame.area) == (level, area))
        relative = camera_relative(camera, focus, root) if shown and root and camera and focus else None
        gain = volume * fade * distance_gain(mario, root) if shown and root else 0.0
        cues = list(frame.sounds) if shown and g >= 0 and fade == 1.0 and frame.has_sound else []
        ticks.append((relative or (0.0, 0.0, 0.0), gain, cues))
    return ticks


def lay(pcm: np.ndarray, lengths: np.ndarray, knots: list[tuple[int, float]], total: int) -> np.ndarray:
    """The engine's output on the recording's samples: recording sample S of knot (S, t) hears the
    engine at the start of tick t (fractional t between ticks, linear between knots); outside the
    knots, or past the engine's output, silence. Returns (total, 2) int16."""
    track = np.zeros((total, 2), np.int16)
    if len(knots) < 2 or not len(pcm):
        return track
    xs = np.array([s for s, _ in knots], np.float64)
    ts = np.array([t for _, t in knots], np.float64)
    starts = np.concatenate([[0], np.cumsum(lengths)]).astype(np.float64)
    lo, hi = max(0, int(math.ceil(xs[0]))), min(total, int(math.floor(xs[-1])) + 1)
    for at in range(lo, hi, 1 << 18):
        samples = np.arange(at, min(hi, at + (1 << 18)), dtype=np.float64)
        t = np.interp(samples, xs, ts)
        whole = np.clip(np.floor(t).astype(np.int64), 0, len(lengths) - 1)
        positions = starts[whole] + (t - whole) * lengths[whole]
        inside = (t >= 0) & (positions <= len(pcm) - 1)
        if inside.any():
            values = sc.interp(pcm.astype(np.float32), positions[inside])
            track[at:at + len(samples)][inside] = np.clip(np.rint(values), -32768, 32767).astype(np.int16)
    return track


def wav_file(pcm: np.ndarray, rate: int) -> bytes:
    """A block as the scene's stored-layer blocks are served (scene_audio's .wav)."""
    return scene_audio._wav(np.ascontiguousarray(pcm, "<i2").tobytes(), rate)


# ---------------------------------------------------------------- one open scene's ghosts

_MAP_SYMBOL = re.compile(r"^\s+0x00000000([0-9a-f]{8})\s+gLakituState\s*$")


def lakitu_address(map_path: Path | None) -> int | None:
    """gLakituState from the lab's symbol map (US), or None without one."""
    try:
        for line in Path(map_path).read_text(encoding="utf-8", errors="replace").splitlines():
            if match := _MAP_SYMBOL.match(line):
                return int(match[1], 16)
    except (OSError, TypeError):
        return None
    return None


class SceneGhostSounds:
    """The ghost tracks of one open scene, each rendered once on the first block asked of it
    (a track per ghost ref, origin and scene length) and kept while the scene is open.

    `scene`: the session's package, ROM bytes, viewer frames (package indices), outlines, its
    SceneSound (the recording's samples per viewer frame), the ghost origin, the ghosts shown
    (stage_ghosts.Shown) and their look (its volume: the player's ghost volume). `host_factory(rom)`
    starts the engine host (EngineHost unless a test gives its own)."""

    def __init__(self, host_factory=EngineHost, map_path: Path | None = None):
        self.host_factory, self.map_path = host_factory, map_path
        self.host: EngineHost | None = None
        self.tracks: dict[tuple, np.ndarray] = {}
        self.stats: dict[str, dict] = {}
        self.lock = threading.Lock()                 # one render at a time; tracks are immutable once made
        self.twin = None                             # a Package of the scene's own, for its camera reads

    def block(self, scene, ref: str, index: int) -> tuple[bytes, str]:
        """Block `index` of ghost `ref`'s track as a .wav on the scene's sound blocks; LookupError
        for a ghost not in the scene or no such block."""
        sound = scene.sound
        if sound is None:
            raise LookupError("This recording has no sound.")
        layer = sound.layer
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(layer.blocks):
            raise LookupError("This recording's sound has no such block.")
        ghost = next((shown for shown in scene.ghosts.shown if shown.ref == ref), None)
        if ghost is None:
            raise LookupError(NO_TRACK)
        try:
            track = self.track(scene, ghost)
        except (RuntimeError, OSError, ValueError) as error:    # that ghost plays silent; the log says why
            log.warning("scene viewer: ghost %r has no sound: %s", ref, error)
            raise LookupError(NO_SOUND) from None
        at = index * layer.block_samples
        return wav_file(track[at:at + layer.block_samples], layer.rate), "audio/wav"

    def track(self, scene, ghost) -> np.ndarray:
        layer = scene.sound.layer
        key = (ghost.ref, scene.ghost_origin, layer.total_samples, round(scene.ghost_look.volume, 4))
        with self.lock:
            found = self.tracks.get(key)
            if found is None:
                started = time.perf_counter()
                found = self.tracks[key] = self._render(scene, ghost)
                self.stats[ghost.ref]["ms"] = round((time.perf_counter() - started) * 1000, 1)
            return found

    def _render(self, scene, ghost) -> np.ndarray:
        from sm64_events.ghost import clip as ghost_clip
        clip = ghost_clip.decode_clip(ghost.clip)
        timer = [scene.outlines[index]["identity"].get("global_timer") for index in scene.frames]
        origin = scene.outlines[scene.ghost_origin]["identity"].get("global_timer")
        samples = scene.sound.samples
        knots_at = [(k, samples[k], timer[k] - origin) for k in range(len(scene.frames))
                    if samples[k] >= 0 and timer[k] is not None and origin is not None]
        layer = scene.sound.layer
        if len(knots_at) < 2:
            self.stats[ghost.ref] = {"ticks": 0}
            return np.zeros((layer.total_samples, 2), np.int16)
        first_g = max(0, min(g for _, _, g in knots_at))
        last_g = max(g for _, _, g in knots_at)
        frames = range(first_g, last_g + 2)
        started = time.perf_counter()
        stage = self._stage(scene, knots_at)
        placed = time.perf_counter()
        ticks = plan_ticks(clip, frames, stage, scene.ghost_look.volume, clip.dance_tail)
        if self.host is None:
            self.host = self.host_factory(scene.rom)
        lengths, pcm = self.host.render(ticks)
        rendered = time.perf_counter()
        track = lay(pcm, lengths, [(s, g - first_g) for _, s, g in knots_at if g >= first_g], layer.total_samples)
        self.stats[ghost.ref] = {"ticks": len(ticks), "cues": sum(len(cues) for _, _, cues in ticks),
                                 "stage_ms": round((placed - started) * 1000, 1),
                                 "engine_ms": round((rendered - placed) * 1000, 1),
                                 "lay_ms": round((time.perf_counter() - rendered) * 1000, 1)}
        return track

    def _stage(self, scene, knots_at: list):
        """stage(g): the stage's camera position, focus, Mario, level and area at ghost frame g,
        read every PLACE_EVERY scene frames from the scene state (a package of this object's own,
        so the picture worker's cursor is never moved) and held between."""
        from sm64_events.memory.addresses import MARIO_POS_OFF
        from sm64_events.memory.layout import layout_for
        package = scene.package
        lakitu = lakitu_address(self.map_path) if scene.rom[0x3E] == ord("E") else None
        try:
            mario_struct = layout_for({ord("E"): "us", ord("J"): "jp"}[scene.rom[0x3E]]).mario_struct
        except KeyError:
            mario_struct = None
        reads: list[tuple[int, tuple]] = []
        if package.states is not None:
            import package as scene_package
            if self.twin is None or self.twin.data is not package.data:
                self.twin = scene_package.Package(package.data, package.rom, checked=package.index)

            def vector(ram, address):
                if address is None:
                    return None
                at = address & 0x1FFFFFFF
                values = struct.unpack_from("<3f", ram, at)
                return values if all(math.isfinite(value) and abs(value) < 1e6 for value in values) else None
            for position, (k, _sample, g) in enumerate(knots_at):
                if position % PLACE_EVERY and position != len(knots_at) - 1:
                    continue
                _task, _regions, ram, _known = self.twin.scene_state(scene.frames[k])
                camera = vector(ram, None if lakitu is None else lakitu + LAKITU_POS)
                focus = vector(ram, None if lakitu is None else lakitu + LAKITU_FOCUS)
                mario = vector(ram, None if mario_struct is None else mario_struct + MARIO_POS_OFF)
                reads.append((g, (camera, focus, mario)))
        identity = {g: scene.outlines[scene.frames[k]]["identity"] for k, _s, g in knots_at}
        gs = [g for g, _ in reads]

        def stage(g: int):
            place = reads[max(0, np.searchsorted(gs, g, side="right") - 1)][1] if reads else (None, None, None)
            seen = identity.get(g) or identity[min(identity, key=lambda known: abs(known - g))]
            return (*place, seen.get("curr_level"), seen.get("curr_area"))
        return stage

    def close(self) -> None:
        host, self.host = self.host, None
        if host is not None:
            host.close()
        self.tracks.clear()
