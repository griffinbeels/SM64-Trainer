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
(stage_ghosts.ghost_frame), and its picture plays the recording's sample S[k] (scene_audio: on
its pictures where the scene measured Project64's delay, else as heard). The ghost's game frame g
sounds from its tick's first engine sample, so the track
maps each recording sample to an engine position through the knots (S[k], start of tick g(k)),
linearly between them, and reads the engine's 32 kHz output there (stems_check.interp): a jump
sound lands on the picture of the jump. Placement is read every PLACE_EVERY scene frames and
held between.

Where it is made and mixed: each ghost's track is made once, on the first block the page asks
for, in the scene's track maker (TrackMaker: one below-normal child process per open scene, with
the engine as its own child), and the server serves it as blocks that tile the scene's own sound
(make_track is the one derivation; with a test's engine it runs in the server). The page's
worklet mixes those blocks on the clock that already plays the
stage, so a ghost is in sample sync with the stage by construction, and a mute or the volume
changes a gain within 64 samples. Mixing on the server instead would cost a new render for every
mute. Measured 2026-10-27 on his 655-frame HMC attempt scene with one of his own runs: 1.37 s for
the first block (the stage's camera and Mario, 110 scene-state reads: 1.0 s; the engine, 656
ticks and 51 cues: 88 ms; laying it on 1.2 M samples: 0.26 s), every later block 0.05 ms, 4.8 MB
held per ghost. The stage reads now take only the 36 bytes they need of each state (package.scene_bytes),
not the whole 8 MiB state: on his 830-frame HMC take 0.66-0.77 s a track instead of 1.32-1.40 s, the same
samples (2026-10-27). Made on a server thread, that shared Python's lock with the pictures: a Play started
while 2 or 5 tracks were being made hitched (input to picture up to 180-190 ms, 2-5 late frames); made in
the track maker it is the same samples and Play runs as without them (up to 47-53 ms, 0-2 late, 2026-10-27).
The 655-frame scene has no stems fit, so its own
sound plays as heard: 148 ms after its
picture, Project64's audio delay, while its own run's ghost lands on the picture. A scene with a
measured delay plays its sound on its pictures too (scene_audio.measured_delay), and the ghosts'
tracks follow its samples (the cache key carries the shift).
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import math
import os
import re
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
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
_BELOW_NORMAL = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
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
    """ghost_sound_host.exe (x86, the model library's audio engine). The installed app runs its own
    copy (core/paths.py bundled_lab_file) and never compiles; a checkout compiles the one recipe
    (tools/build_lab.py) into the scene lab's build cache, as viewer_host.exe is."""
    if getattr(sys, "frozen", False):
        from sm64_events.core.paths import bundled_lab_file
        path = bundled_lab_file("ghost_sound_host.exe")
        if path is None:
            raise RuntimeError("This copy of SM64 Trainer is missing ghost_sound_host.exe, the program that "
                               "makes the ghosts' sound effects. Reinstall SM64 Trainer.")
        return path
    recipes = _module("ghost_sound_build_lab", ROOT / "tools/build_lab.py")
    try:
        return recipes.cached("ghost_sound_host.exe")
    except recipes.ToolchainMissing:
        raise RuntimeError("Ghost sound needs the x86 MSVC toolchain to build its engine.") from None


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


@dataclass
class TrackInputs:
    """Everything one ghost's track is made from, as plain values a child process can take: its
    clip; the scene's viewer frames (package indices), each one's identity (global_timer,
    curr_level, curr_area) and recording sample (-1: none); the global_timer of the frame every
    ghost's first frame stands on; the recording's length in samples; the ghost volume; the ROM;
    gLakituState (None: unknown)."""
    clip: bytes
    frames: list
    identities: list
    origin: int | None
    samples: list
    total: int
    volume: float
    rom: bytes
    lakitu: int | None

    @classmethod
    def of(cls, scene, ghost, lakitu: int | None) -> TrackInputs:
        kept = ("global_timer", "curr_level", "curr_area")
        return cls(bytes(ghost.clip), [int(index) for index in scene.frames],
                   [{key: scene.outlines[index]["identity"].get(key) for key in kept} for index in scene.frames],
                   scene.outlines[scene.ghost_origin]["identity"].get("global_timer"),
                   [int(sample) for sample in scene.sound.samples], int(scene.sound.layer.total_samples),
                   float(scene.ghost_look.volume), scene.rom, lakitu)

    def plain(self) -> dict:
        """The values a request carries beside the clip and the ROM (TrackMaker.make)."""
        return {"frames": self.frames, "identities": self.identities, "origin": self.origin,
                "samples": self.samples, "total": self.total, "volume": self.volume, "lakitu": self.lakitu}


def make_track(inputs: TrackInputs, reader, engine) -> tuple[np.ndarray, dict]:
    """One ghost's track on the recording's samples ((total, 2) int16) and how it went: the clip's
    ticks planned against the stage (stage_of) and run in the engine (`engine()`, started on first
    use: EngineHost or a test's), then laid on the recording's samples (lay). `reader(index,
    wanted)`: a few bytes of a frame's scene state (package.Package.scene_bytes), None without the
    scene-state layer. The one derivation, in a TrackMaker child or in this process."""
    from sm64_events.ghost import clip as ghost_clip
    clip = ghost_clip.decode_clip(inputs.clip)
    timer = [identity.get("global_timer") for identity in inputs.identities]
    origin, samples = inputs.origin, inputs.samples
    knots_at = [(k, samples[k], timer[k] - origin) for k in range(len(inputs.frames))
                if samples[k] >= 0 and timer[k] is not None and origin is not None]
    if len(knots_at) < 2:
        return np.zeros((inputs.total, 2), np.int16), {"ticks": 0}
    first_g = max(0, min(g for _, _, g in knots_at))
    last_g = max(g for _, _, g in knots_at)
    frames = range(first_g, last_g + 2)
    started = time.perf_counter()
    stage = stage_of(inputs, knots_at, reader)
    placed = time.perf_counter()
    ticks = plan_ticks(clip, frames, stage, inputs.volume, clip.dance_tail)
    lengths, pcm = engine().render(ticks)
    rendered = time.perf_counter()
    track = lay(pcm, lengths, [(s, g - first_g) for _, s, g in knots_at if g >= first_g], inputs.total)
    return track, {"ticks": len(ticks), "cues": sum(len(cues) for _, _, cues in ticks),
                   "stage_ms": round((placed - started) * 1000, 1), "engine_ms": round((rendered - placed) * 1000, 1),
                   "lay_ms": round((time.perf_counter() - rendered) * 1000, 1)}


def stage_of(inputs: TrackInputs, knots_at: list, reader):
    """stage(g): the stage's camera position, focus, Mario, level and area at ghost frame g, read
    every PLACE_EVERY scene frames from the scene state and held between."""
    from sm64_events.memory.addresses import MARIO_POS_OFF
    from sm64_events.memory.layout import layout_for
    lakitu = inputs.lakitu if inputs.rom[0x3E] == ord("E") else None
    try:
        mario_struct = layout_for({ord("E"): "us", ord("J"): "jp"}[inputs.rom[0x3E]]).mario_struct
    except KeyError:
        mario_struct = None
    reads: list[tuple[int, tuple]] = []
    if reader is not None:
        # Only these three vectors of each state are read (package.scene_bytes): building the
        # whole 8 MiB state for them cost ~1 s a track (round 5, measured 2026-10-27).
        addresses = [None if lakitu is None else lakitu + LAKITU_POS, None if lakitu is None else lakitu + LAKITU_FOCUS,
                     None if mario_struct is None else mario_struct + MARIO_POS_OFF]
        wanted = [(address & 0x1FFFFFFF, 12) for address in addresses if address is not None]

        def vector(data):
            values = struct.unpack("<3f", data)
            return values if all(math.isfinite(value) and abs(value) < 1e6 for value in values) else None
        for position, (k, _sample, g) in enumerate(knots_at):
            if position % PLACE_EVERY and position != len(knots_at) - 1:
                continue
            found = iter(reader(inputs.frames[k], wanted))
            camera, focus, mario = (None if address is None else vector(next(found)) for address in addresses)
            reads.append((g, (camera, focus, mario)))
    identity = {g: inputs.identities[k] for k, _s, g in knots_at}
    gs = [g for g, _ in reads]

    def stage(g: int):
        place = reads[max(0, np.searchsorted(gs, g, side="right") - 1)][1] if reads else (None, None, None)
        seen = identity.get(g) or identity[min(identity, key=lambda known: abs(known - g))]
        return (*place, seen.get("curr_level"), seen.get("curr_area"))
    return stage


class TrackMaker:
    """The child process that makes a scene's ghost tracks (serve), below normal priority and
    hidden. make() hands it one ghost's inputs, and the scene's package for the stage reads, in a
    shared memory block, and takes the finished samples back from the same block; the server's
    thread only waits on the pipe meanwhile, so a track being made never shares the server's
    Python lock with the pictures. One request at a time (SceneGhostSounds.lock); the ROM goes once."""

    def __init__(self, engine: str | None = None, extra_path: tuple = ()):
        from sm64_events.core.childproc import python_child, quiet_spawn_kwargs
        argv, env = python_child("ghost_sound", "--scene-lab-ghost-sound",
                                 ["--serve", *(["--engine", engine] if engine else [])], site_packages=True)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (*map(str, extra_path), str(HERE), env.get("PYTHONPATH"))))
        spawn = quiet_spawn_kwargs()
        spawn["creationflags"] = spawn.get("creationflags", 0) | _BELOW_NORMAL
        self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, env=env, **spawn)
        self.rom = None                                # the ROM the child holds

    @property
    def pid(self) -> int:
        return self.process.pid

    def make(self, inputs: TrackInputs, package) -> tuple[np.ndarray, dict]:
        """(track, stats) as make_track gives them; `package` the scene package's bytes (or a
        package.Joined of them), None without the scene-state layer. RuntimeError with the child's
        reason, or when the child has ended."""
        from multiprocessing import shared_memory
        data = b"" if package is None else package if isinstance(package, bytes) else package[0:len(package)]
        rom = b"" if self.rom is inputs.rom else inputs.rom
        parts, at = {}, 0
        for name, size in (("package", len(data)), ("clip", len(inputs.clip)), ("rom", len(rom)),
                           ("out", inputs.total * 4)):
            parts[name], at = [at, size], at + size
        memory = shared_memory.SharedMemory(create=True, size=max(1, at))
        try:
            for name, content in (("package", data), ("clip", inputs.clip), ("rom", rom)):
                begin, size = parts[name]
                memory.buf[begin:begin + size] = content
            request = {"memory": memory.name, **parts, "inputs": inputs.plain()}
            try:
                self.process.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
                self.process.stdin.flush()
                line = self.process.stdout.readline()
            except (OSError, ValueError) as error:
                raise RuntimeError(f"the ghost track maker stopped: {error}") from None
            if not line:
                raise RuntimeError(f"the ghost track maker stopped (exit {self.process.poll()})")
            answer = json.loads(line)
            if "error" in answer:
                raise RuntimeError(answer["error"])
            self.rom = inputs.rom
            begin, size = parts["out"]
            track = np.frombuffer(memory.buf[begin:begin + size], "<i2").reshape(-1, 2).copy()
            return track, answer["stats"]
        finally:
            memory.close()
            memory.unlink()

    def close(self) -> int | None:
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            return self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            return self.process.wait(timeout=5)


def _part(memory, span) -> bytes:
    begin, size = span
    return bytes(memory.buf[begin:begin + size])


def serve(argv=None) -> int:
    """The track maker child (TrackMaker): one JSON request a line, one answer a line, EOF exits."""
    import argparse
    from multiprocessing import shared_memory
    parser = argparse.ArgumentParser(description="the scene viewer's ghost track maker")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--engine", help="module:callable starting a stand-in engine host (tests)")
    args = parser.parse_args(argv)
    factory = EngineHost
    if args.engine:
        module, _, name = args.engine.partition(":")
        factory = getattr(importlib.import_module(module), name)
    untracked = {"track": False} if sys.version_info >= (3, 13) else {}   # as picture_encoder.serve
    rom, host, sink = None, None, sys.stdout.buffer

    def engine():
        nonlocal host
        if host is None:
            host = factory(rom)
        return host
    for line in sys.stdin.buffer:
        try:
            request = json.loads(line)
            memory = shared_memory.SharedMemory(name=request["memory"], **untracked)
            try:
                if request["rom"][1]:
                    rom = _part(memory, request["rom"])
                reader = None
                if request["package"][1]:
                    import package as scene_package
                    reader = scene_package.Package(_part(memory, request["package"]), rom).scene_bytes
                inputs = TrackInputs(clip=_part(memory, request["clip"]), rom=rom, **request["inputs"])
                track, stats = make_track(inputs, reader, engine)
                begin, size = request["out"]
                if track.nbytes != size:
                    raise ValueError("the track is not the recording's length")
                memory.buf[begin:begin + size] = np.ascontiguousarray(track, "<i2").tobytes()
            finally:
                memory.close()
            answer = {"stats": stats}
        except Exception as error:  # noqa: BLE001 - that ghost plays silent; the server logs why
            answer = {"error": f"{type(error).__name__}: {error}"[:500]}
        sink.write((json.dumps(answer) + "\n").encode("utf-8"))
        sink.flush()
    if host is not None:
        host.close()
    return 0


class SceneGhostSounds:
    """The ghost tracks of one open scene, each made once on the first block asked of it (a track
    per ghost ref, origin and scene length) and kept while the scene is open.

    `scene`: the session's package, ROM bytes, viewer frames (package indices), outlines, its
    SceneSound (the recording's samples per viewer frame), the ghost origin, the ghosts shown
    (stage_ghosts.Shown) and their look (its volume: the player's ghost volume). Tracks are made in
    a TrackMaker child (`maker_factory()` when given); with `host_factory(rom)` (a test's engine)
    they are made in this process instead, and also when the child cannot start."""

    def __init__(self, host_factory=None, map_path: Path | None = None, maker_factory=None):
        self.host_factory, self.map_path, self.maker_factory = host_factory, map_path, maker_factory
        self.host: EngineHost | None = None
        self.maker: TrackMaker | None = None
        self.tracks: dict[tuple, np.ndarray] = {}
        self.stats: dict[str, dict] = {}
        self.lock = threading.Lock()                 # one track at a time; tracks are immutable once made
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
        key = (ghost.ref, scene.ghost_origin, layer.total_samples, getattr(scene.sound, "shift_ms", 0.0),
               round(scene.ghost_look.volume, 4))
        with self.lock:
            found = self.tracks.get(key)
            if found is None:
                started = time.perf_counter()
                found, self.stats[ghost.ref] = self._make(scene, ghost)
                self.tracks[key] = found
                self.stats[ghost.ref]["ms"] = round((time.perf_counter() - started) * 1000, 1)
            return found

    def _make(self, scene, ghost) -> tuple[np.ndarray, dict]:
        inputs = TrackInputs.of(scene, ghost, lakitu_address(self.map_path))
        package = scene.package
        maker = self._maker()
        if maker is not None:
            try:
                return maker.make(inputs, None if package.states is None else package.data)
            except RuntimeError:
                if maker.process.poll() is not None:     # it ended: the next track starts another
                    self.maker = None
                raise
        reader = None
        if package.states is not None:
            import package as scene_package
            if self.twin is None or self.twin.data is not package.data:
                self.twin = scene_package.Package(package.data, package.rom, checked=package.index)
            reader = self.twin.scene_bytes        # a package of this object's own: the pictures' cursor stays

        def engine():
            if self.host is None:
                self.host = (self.host_factory or EngineHost)(inputs.rom)
            return self.host
        return make_track(inputs, reader, engine)

    def _maker(self) -> TrackMaker | None:
        """The track maker child (started on the first track), None to make tracks in this process:
        a test's engine (host_factory), or a child that could not start."""
        if self.host_factory is not None:
            return None
        if self.maker is None:
            try:
                self.maker = (self.maker_factory or TrackMaker)()
            except OSError as error:
                log.warning("scene viewer: the ghost track maker could not start (%s); tracks are made here", error)
                self.host_factory = EngineHost
                return None
        return self.maker

    def close(self) -> None:
        host, self.host = self.host, None
        if host is not None:
            host.close()
        maker, self.maker = self.maker, None
        if maker is not None:
            maker.close()
        self.tracks.clear()


if __name__ == "__main__":
    raise SystemExit(serve())
