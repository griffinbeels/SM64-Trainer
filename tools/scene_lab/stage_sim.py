"""A stage for a course and act he never recorded, made by the game's own code in the simulation (Ghost
Handoff re-simulation). Simulation only: nothing here touches Project64 or the running game.

    stages = Stages(lab_root, cache)
    ram = stages.reset(level, area, act)        # the practice-reset state, as Usamune's own reset leaves it
    path = stages.package(level, area, act)     # that state played on with no input, as a scene package

How a reset is made, measured on his Whomp's Fortress takes (2026-11-08): a recorded scene-tap session
that boots and walks into a castle painting supplies the boot record and the frame where the painting's
warp is set (sWarpDest a course, play mode 4). frame_sim plays the session's own pads to that frame; the
painting's destination byte becomes the asked level (the one write, and it is to the simulation's copy);
the act selector runs with simulated A presses and stick pushes until the asked act is chosen; Mario
spawns; then a simulated L press (RESET_BUTTON, his takes begin with it) runs Usamune's practice reset,
and the state after its second frame is the reset. His ghosts started from that state kept Mario exact
(0 units over 351-403 frames); objects whose phase comes from before a reset (WF's sliding platforms, the
Bomps) stand elsewhere, which is why a ghost's own start (start_frame.py) wins when it has one.

Every state is cached under the cache folder by its recipe (STAGE_VERSION, session, painting frame,
level, area, act), so a second ask costs a file read.
"""
from __future__ import annotations

import hashlib
import json
import struct
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frame_sim as sim  # noqa: E402

STAGE_VERSION = 1
RESET_BUTTON = 0x0020            # L: Usamune's practice reset
ENTRY_NODE = 0x0A                # a course's painting entry
A_BUTTON = 0x8000
CASTLE = 6
PLAY_MODE_CHANGE_LEVEL = 4
SPAWN_ACTION = 0x00001924        # ACT_SPAWN_SPIN_AIRBORNE: a course entry's and a practice reset's first action
MARIO_OBJECT, CURR_LEVEL, CURR_AREA = 0x80361158, 0x8032DDF8, 0x8033BACA
LEVEL_TO_COURSE = 0x8032DD98     # gLevelToCourseNumTable: one byte per level from level 1
LEVEL_COUNT = 38
SETTLE_FRAMES = 40               # after the spawn, before the reset is pressed
MENU_FRAMES = 1500               # a warp and the act selector take ~130 frames; more means it is stuck


class StageError(RuntimeError):
    """Why no stage could be made, in a sentence the page can show."""


def lab_config() -> dict:
    return json.loads((sim.lab_root() / "lab.json").read_text(encoding="utf-8"))


class Machine:
    """frame_sim's thread 5 over one RAM image; step(pads) runs a game frame to the scene tap's moment,
    two VIs a frame (a ghost records no VI count; only Usamune's lag counter can tell)."""

    def __init__(self, ram: np.ndarray, rom: bytes, symbols: sim.Symbols, fcsr: int):
        self.cpu = sim.GameCpu(ram, rom, symbols, fcsr)
        self.symbols = symbols
        self.cpu.enter_resume(self.cpu.u32(symbols["sCurrentCmd"]))
        self.cpu.scheduler_model()

    def step(self, pads: bytes) -> None:
        cpu = self.cpu
        cpu.pads = pads
        cpu.put_u32(self.symbols["sNumVblanks"], cpu.u32(self.symbols["sNumVblanks"]) + 2)
        cpu.put_u32(sim.USAMUNE_VI_COUNT, cpu.u32(sim.USAMUNE_VI_COUNT) + 2)
        if not cpu.run_frame(sim.RESUME):
            raise StageError(f"The simulated game stopped: {cpu.violations[:2]}")
        cpu.scheduler_model()

    @property
    def ram(self) -> np.ndarray:
        return self.cpu.ram


def pads(buttons: int, x: int, y: int, template: bytes) -> bytes:
    """OSContPad[4]: port 1 as asked (errno 0), ports 2-4 as the template has them."""
    return struct.pack(">Hbb", buttons, x, y) + template[4:6] + template[6:24]


def s16(ram: np.ndarray, address: int) -> int:
    return struct.unpack_from(">h", ram, sim.phys(address))[0]


def u32(ram: np.ndarray, address: int) -> int:
    return struct.unpack_from(">I", ram, sim.phys(address))[0]


@dataclass(frozen=True)
class Painting:
    session: str
    frame: int                   # the session's frame index where the painting's warp is set


def find_painting(session: Path, symbols: sim.Symbols) -> int | None:
    """The first frame of a recorded session in the castle with a course warp set (play mode 4)."""
    _ram, stream = sim.boot_state(session)
    bss = sim.phys(sim.SCENE_BSS)
    warp = sim.phys(symbols["sWarpDest"]) - bss
    mode = sim.phys(symbols["sCurrPlayMode"]) - bss
    for frame in stream:
        if frame.level != CASTLE:
            continue
        span = next(data for begin, data in frame.spans if begin == bss)
        if span[warp] == 1 and struct.unpack_from(">h", span, mode)[0] == PLAY_MODE_CHANGE_LEVEL:
            return frame.index
    return None


class Stages:
    def __init__(self, lab_root: Path, cache: Path, *, rom_path: Path | None = None, symbols: sim.Symbols | None = None):
        config = lab_config()
        self.lab_root, self.cache = Path(lab_root), Path(cache)
        self.rom_path = Path(rom_path or config["rom"])
        self.rom = self.rom_path.read_bytes()
        self.symbols = symbols or sim.Symbols(Path(config["map"]))
        self.lock = threading.Lock()
        self._painting: Painting | None = None
        self._boot: tuple[np.ndarray, int] | None = None

    # ---------------------------------------------------------------- the castle and its painting
    def sessions(self) -> list[Path]:
        return sorted((path for path in self.lab_root.glob("tap-*") if (path / "tap.bin").is_file()),
                      key=lambda path: path.name)

    def painting(self) -> Painting:
        """The recorded session and frame a stage starts from; found once, remembered in the cache."""
        if self._painting is not None:
            return self._painting
        note = self.cache / "painting.json"
        if note.is_file():
            known = json.loads(note.read_text(encoding="utf-8"))
            if (self.lab_root / known["session"] / "tap.bin").is_file():
                self._painting = Painting(known["session"], known["frame"])
                return self._painting
        for session in self.sessions():
            frame = find_painting(session, self.symbols)
            if frame is not None:
                self._painting = Painting(session.name, frame)
                self.cache.mkdir(parents=True, exist_ok=True)
                note.write_text(json.dumps({"session": session.name, "frame": frame}), encoding="utf-8")
                return self._painting
        raise StageError("No recorded scene session walks into a castle painting, so no new stage can be made.")

    def boot(self) -> tuple[np.ndarray, int]:
        """A recorded session's boot record and thread 5's FCSR: the RAM a start lies over (code, threads,
        everything a scene's regions do not hold). The painting session's, else the first one recorded.
        Read once."""
        if self._boot is None:
            try:
                session = self.lab_root / self.painting().session
            except StageError:
                sessions = self.sessions()
                if not sessions:
                    raise StageError("No recorded scene session holds a boot record, so no ghost can be "
                                     "re-simulated here.") from None
                session = sessions[0]
            ram, _stream = sim.boot_state(session)
            fcsr = struct.unpack_from(">I", ram, sim.phys(self.symbols["gGameLoopThread"]) + sim.THREAD_FPCSR)[0]
            self._boot = (ram, fcsr)
        return self._boot

    def _castle(self) -> tuple[np.ndarray, bytes]:
        painting = self.painting()
        path = self.cache / f"castle-v{STAGE_VERSION}-{painting.session}-{painting.frame}.npz"
        if path.is_file():
            data = np.load(path)
            return data["ram"], data["pads"].tobytes()
        import handoff_slot  # noqa: PLC0415 - its replay is the boot session played closed
        replay = handoff_slot.start_replay(self.lab_root / painting.session)
        for frame in replay.stream:
            replay.absorb(frame)
            replay.step(frame)
            if frame.index == painting.frame:
                ram, template = replay.cpu.ram.copy(), replay.cpu.pads
                self.cache.mkdir(parents=True, exist_ok=True)
                np.savez_compressed(path, ram=ram, pads=np.frombuffer(template, np.uint8))
                return ram, template
        raise StageError("The painting session ended before its painting.")

    # ---------------------------------------------------------------- course and act
    def level_of_course(self, course: int) -> int:
        ram, _fcsr = self.boot()
        table = ram[sim.phys(LEVEL_TO_COURSE):sim.phys(LEVEL_TO_COURSE) + LEVEL_COUNT]
        for level, value in enumerate(table.tolist(), start=1):
            if value == course:
                return level
        raise StageError(f"No level belongs to course {course}.")

    def key(self, level: int, area: int, act: int) -> str:
        painting = self.painting()
        return f"v{STAGE_VERSION}-{painting.session}-{painting.frame}-{level}-{area}-{act}"

    def reset(self, level: int, area: int, act: int) -> np.ndarray:
        """The practice-reset state of that level, area and act (cached)."""
        with self.lock:
            path = self.cache / f"reset-{self.key(level, area, act)}.npz"
            if path.is_file():
                return np.load(path)["ram"]
            ram = self._make_reset(level, area, act)
            np.savez_compressed(path, ram=ram)
            return ram

    def reset_digest(self, level: int, area: int, act: int) -> str:
        """The reset's main pool, hashed: what a start file's pool delta was made against."""
        ram = self.reset(level, area, act)
        return hashlib.sha256(ram[sim.POOL_BEGIN:sim.POOL_BEGIN + sim.POOL_BYTES].tobytes()).hexdigest()

    def _make_reset(self, level: int, area: int, act: int) -> np.ndarray:
        castle, template = self._castle()
        _base, fcsr = self.boot()
        machine = Machine(castle.copy(), self.rom, self.symbols, fcsr)
        struct.pack_into(">BBBB", machine.ram, sim.phys(self.symbols["sWarpDest"]), 1, level, area, ENTRY_NODE)
        neutral = pads(0, 0, 0, template)
        visible, selected = sim.phys(self.symbols["sVisibleStars"]), sim.phys(self.symbols["sSelectedActIndex"])
        spawned = None
        for n in range(MENU_FRAMES):
            ram = machine.ram
            here = s16(ram, CURR_LEVEL) == level and s16(ram, CURR_AREA) == area
            if here and u32(ram, MARIO_OBJECT) and u32(ram, self.symbols["gMarioStates"] + 0x0C) == SPAWN_ACTION:
                spawned = n
                break
            step = neutral
            if s16(ram, CURR_LEVEL) == level and not u32(ram, MARIO_OBJECT) and ram[visible] and n % 16 == 0:
                step = pads(0, 80, 0, template) if ram[selected] < act - 1 else pads(A_BUTTON, 0, 0, template)
            machine.step(step)
        if spawned is None:
            raise StageError(f"The simulated game never reached level {level} area {area} act {act}.")
        if machine.ram[sim.phys(self.symbols["gCurrActNum"]) + 1] != act:
            raise StageError(f"The act selector chose act {machine.ram[sim.phys(self.symbols['gCurrActNum']) + 1]}, not {act}.")
        for _ in range(SETTLE_FRAMES):
            machine.step(neutral)
        before = u32(machine.ram, MARIO_OBJECT)
        machine.step(pads(RESET_BUTTON, 0, 0, template))
        machine.step(pads(RESET_BUTTON, 0, 0, template))
        if u32(machine.ram, self.symbols["gMarioStates"] + 0x0C) != SPAWN_ACTION or u32(machine.ram, MARIO_OBJECT) == before:
            raise StageError("Usamune's practice reset did not run in the simulated game.")
        return machine.ram.copy()

    # ---------------------------------------------------------------- as a scene
    def package(self, level: int, area: int, act: int, frames: int = 150) -> Path:
        """That reset played on with no input, as a scene package the viewer opens (cached)."""
        import sim_scene  # noqa: PLC0415
        out = self.cache / f"stage-{self.key(level, area, act)}-{frames}.sm64scene"
        if out.is_file():
            return out
        start = self.reset(level, area, act)
        _base, fcsr = self.boot()
        castle_pads = self._castle()[1]
        machine = Machine(start.copy(), self.rom, self.symbols, fcsr)
        writer = sim_scene.SimScene(out, self.cache / f"work-{time.monotonic_ns()}", self.rom_path, self.symbols)
        try:
            for _ in range(frames):
                machine.step(pads(0, 0, 0, castle_pads))
                writer.add(machine.ram)
            writer.finish()
        except BaseException:
            writer.abandon()
            raise
        return out
