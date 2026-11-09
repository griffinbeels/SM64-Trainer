"""A stage for a course and act he never recorded, made by the game's own code in the simulation (Ghost
Handoff re-simulation). Simulation only: nothing here touches Project64 or the running game.

    stages = Stages(lab_root, cache)
    ram = stages.reset(level, area, act)        # the practice-reset state, as Usamune's own reset leaves it
    path = stages.package(level, area, act)     # that state played on with no input, as a scene package

A reset is made from the ROM alone, with no recorded session (cold_boot.py boots the cartridge in the
simulation), so every machine with the same ROM makes the same bytes: that is what lets a start file's
main-pool delta, and start_check's reference, mean the same thing on another player's trainer. The recipe
(STAGE_VERSION 2, measured 2026-11-08 on US and JP):
  1. cold_boot.boot_into_castle: save file A exists, Mario stands in the castle grounds (~3 s, once).
  2. That course's six star flags are set in the simulation's own save, so the act selector offers every act.
  3. The painting warp (sWarpDest, written at Usamune's call site), then the act selector: MENU_WAIT frames,
     one stick push per wanted act after the first, A until Mario spawns; gCurrActNum must read the act. A level
     without an act selector (courses 16-24, the Bowser arenas) spawns him straight away, its own way
     (SPAWN_ACTIONS), and names no act.
  4. sTransitionTimer back at 0 (cold_boot.arrived), settle, SETTLE_FRAMES with no input.
  5. USAMUNE_RESET_WORD into Usamune's state block, then L for two frames: Usamune's practice reset, which must
     spawn him the way the level's entry did, in the same level. The state after it is the reset. US and JP
     both fire; ~2-4 s per reset after the boot. All 90 acts of courses 1-15, the nine courses without acts and
     the three arenas make one (2026-11-08); the castle's own levels do not (scene_warm.py says why).
His recorded scenes re-run the same over the cold boot's RAM as over his session's boot record (two
scenes, every frame, Mario 0.000 units, the same single differing word), so the cold boot is also what a
start lies over (boot()).

Every reset is cached under the cache folder by its recipe (STAGE_VERSION, build, ROM, level, area, act),
so a second ask costs a file read. Both cartridges simulate (2026-11-09): each build's own frame facts are
read off its code (frame_sim.SimBuild), so a JP lab rebuilds a ghost on the JP cartridge, whichever cartridge
recorded it (the same seven levels' ghosts part, or never part, at the same record on both).
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frame_sim as sim  # noqa: E402

STAGE_VERSION = 2                # the reset's recipe: a change makes every reset (and start-file base) anew
PACKAGE_VERSION = 2              # a stage scene's own form: 2 shows every frame at the game's 30 fps
RESET_BUTTON = 0x0020            # L: Usamune's practice reset
ENTRY_NODE = 0x0A                # a course's painting entry
A_BUTTON = 0x8000
SPAWN_ACTION = 0x00001924        # ACT_SPAWN_SPIN_AIRBORNE: a course entry's and a practice reset's first action
# A level without a painting spawns Mario its own way, and Usamune's reset does the same (measured 2026-11-08):
# ACT_SPAWN_NO_SPIN_AIRBORNE in the Bowser courses, the slide, the cap courses and Wing Mario over the Rainbow,
# ACT_FLYING in the Tower of the Wing Cap, ACT_WATER_IDLE in the aquarium.
SPAWN_ACTIONS = (SPAWN_ACTION, 0x00001932, 0x10880899, 0x380022C0)
LAST_ACT_COURSE = 15             # courses 1-15 have an act selector; the rest (and the arenas) enter straight in
COLD_FCSR = 0x01000800           # thread 5's FCSR: what every recorded boot record holds, and what cold_boot runs with
# Usamune's state block's first word as his game holds it (2026-11-08). With it zero, L does nothing; it is the
# only word of the block a reset needs (a bisection of the block against his game).
USAMUNE_RESET_WORD = 0x01030300
COURSE_STARS = 0x0C              # files[0][0].courseStars in gSaveBuffer, one byte per course from course 1
ALL_ACTS = 0x3F                  # stars 1-6: the act selector offers every act
# RAM by the symbol map's names (memory/layout.py owns addresses; the lab reads the map's): Mario's object,
# the level and area, and gLevelToCourseNumTable (one byte per level from level 1).
MARIO_OBJECT, CURR_LEVEL, CURR_AREA, LEVEL_TO_COURSE = "gMarioObject", "gCurrLevelNum", "gCurrAreaIndex", "gLevelToCourseNumTable"
LEVEL_COUNT = 38
MENU_WAIT, MENU_PERIOD = 30, 16  # frames in the act selector before the first input, then between inputs
SETTLE_FRAMES = 40               # after the spawn, before the reset is pressed
MENU_FRAMES = 1500               # a warp and the act selector take 50-130 frames; more means it is stuck
NTSC_LAN1 = 2                    # osViModeTable's entry SM64 runs (OS_VI_NTSC_LAN1)
VI_MODE_BYTES = 0x50             # sizeof(OSViMode): type, 9 common registers, 2 fields of 5
# SM64 turns gamma off and the dither filter on (libultra's osViSetSpecialFeatures, which also clears the
# anti-alias mode bits): the mode's ctrl 0x311E becomes 0x13016, the status his recordings show.
VI_GAMMA, VI_ANTIALIAS, VI_DITHER_FILTER = 0x0008, 0x0300, 0x10000


SLOTS_UNKNOWN = "This Trainer does not know where this cartridge keeps its save states, so it cannot make them."


class StageError(RuntimeError):
    """Why no stage could be made, in a sentence the page can show."""


def lab_config() -> dict:
    return json.loads((sim.lab_root() / "lab.json").read_text(encoding="utf-8"))


class Machine:
    """frame_sim's thread 5 over one RAM image; step(pads) runs a game frame to the scene tap's moment,
    two VIs a frame (a ghost records no VI count; only Usamune's lag counter can tell). Either build: the
    cartridge's own frame facts come from its code (frame_sim.SimBuild)."""

    def __init__(self, ram: np.ndarray, rom: bytes, symbols: sim.Symbols, fcsr: int, cpu_class=None):
        self.cpu = (cpu_class or sim.GameCpu)(ram, rom, symbols, fcsr)
        self.symbols = symbols
        self.cpu.enter_resume(self.cpu.u32(symbols["sCurrentCmd"]))
        self.cpu.scheduler_model()

    def step(self, pads: bytes) -> None:
        cpu = self.cpu
        cpu.pads = pads
        cpu.put_u32(self.symbols["sNumVblanks"], cpu.u32(self.symbols["sNumVblanks"]) + 2)
        counter = cpu.build.usamune_vi_count
        cpu.put_u32(counter, cpu.u32(counter) + 2)
        if not cpu.run_frame():
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


def _keep(path: Path, ram: np.ndarray) -> None:
    """A cached RAM image, whole or not at all: the server's background warm and a build can make the same one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{path.stem}.{os.getpid()}-{threading.get_ident()}.partial")
    with partial.open("wb") as handle:
        np.savez_compressed(handle, ram=ram)
    try:
        partial.replace(path)
    except PermissionError:                     # the other one wrote it and is reading it now: the same bytes
        partial.unlink(missing_ok=True)
        if not path.is_file():
            raise


def menu_input(frames_in_menu: int, pushes: int, act: int) -> tuple[int, tuple[int, int], int]:
    """The act selector's input on its n-th frame: (buttons, stick, pushes so far). Nothing for MENU_WAIT
    frames, then every MENU_PERIOD frames one stick push right until act - 1 were made, then A."""
    if frames_in_menu < MENU_WAIT or (frames_in_menu - MENU_WAIT) % MENU_PERIOD:
        return 0, (0, 0), pushes
    if pushes < act - 1:
        return 0, (80, 0), pushes + 1
    return A_BUTTON, (0, 0), pushes


def vi_of_mode(ram: np.ndarray, symbols: sim.Symbols) -> dict:
    """The VI registers a simulated frame is shown with, from the ROM's own NTSC mode (osViModeTable, as the
    cold boot's RAM holds it) and SM64's special features; the origin is field 0's offset into the first
    frame buffer (sim_scene.SimScene moves it to the buffer each frame shows). Equal to his recording's
    first picture of play, register for register (2026-11-08, US and JP the same)."""
    at = sim.phys(symbols["osViModeTable"]) + NTSC_LAN1 * VI_MODE_BYTES
    entry = ram[at:at + VI_MODE_BYTES].tobytes()
    ctrl, width, burst, v_sync, h_sync, leap, h_start, x_scale, v_current = struct.unpack_from(">9I", entry, 4)
    origin, y_scale, v_start, v_burst, v_intr = struct.unpack_from(">5I", entry, 40)
    return {"status": (ctrl & ~(VI_GAMMA | VI_ANTIALIAS)) | VI_DITHER_FILTER,
            "origin": sim.phys(symbols["gFrameBuffer0"]) + origin, "width": width, "intr": v_intr,
            "v_current": v_current, "burst": burst, "v_sync": v_sync, "h_sync": h_sync, "leap": leap,
            "h_start": h_start, "v_start": v_start, "v_burst": v_burst, "x_scale": x_scale, "y_scale": y_scale}


class Stages:
    def __init__(self, lab_root: Path, cache: Path, *, rom_path: Path | None = None, symbols: sim.Symbols | None = None,
                 map_path: Path | None = None):
        config = lab_config() if rom_path is None or symbols is None or map_path is None else {}
        self.map_path = Path(map_path or config["map"])
        self.lab_root, self.cache = Path(lab_root), Path(cache)
        self.rom_path = Path(rom_path or config["rom"])
        self.rom = self.rom_path.read_bytes()
        self.symbols = symbols or sim.Symbols(self.map_path)
        self.lock = threading.Lock()
        self._game = None                   # cold_boot.ColdGame, booted once
        self._castle_snap = None            # its snapshot in the castle grounds, at the resume point
        self._boot: np.ndarray | None = None
        self._references: dict = {}

    # ---------------------------------------------------------------- the cold boot
    def region(self) -> str | None:
        """The cartridge this lab simulates, from its ROM header ("us" | "jp"; None: neither)."""
        from sm64_events.memory.version_probe import version_from_header  # noqa: PLC0415
        return version_from_header(self.rom[:0x40])

    def version(self) -> str:
        version = self.region()
        if version is None:
            raise StageError("The lab's ROM is not a Usamune US or JP cartridge, so no stage can be made.")
        return version

    def _cold(self):
        """The cold boot in the castle grounds: (game, its snapshot). Booted once (~3 s), then restored."""
        if self._game is None:
            import cold_boot  # noqa: PLC0415
            try:
                game, _frames = cold_boot.boot_into_castle(self.version(), {"rom": str(self.rom_path)})
            except SystemExit as stopped:                    # cold_boot's own sentences
                raise StageError(f"The simulated cartridge did not boot: {stopped}") from None
            self._game, self._castle_snap = game, game.snapshot()
        return self._game, self._castle_snap

    def boot(self) -> tuple[np.ndarray, int]:
        """The RAM a start lies over (code, threads, everything a scene's regions do not hold): the cold boot's
        castle grounds, cached on disk by build and ROM; and thread 5's FCSR."""
        if self._boot is None:
            path = self.cache / f"boot-{self._recipe()}.npz"
            if path.is_file():
                self._boot = np.load(path)["ram"]
            else:
                _game, snap = self._cold()
                self._boot = snap[0].copy()
                _keep(path, self._boot)
        return self._boot, COLD_FCSR

    def vi(self) -> dict:
        return vi_of_mode(self.boot()[0], self.symbols)

    def simulates(self) -> None:
        """StageError unless this cartridge's scenes can be simulated: Usamune US or JP. Each build's own frame
        facts are read off its code (frame_sim.SimBuild), and the names STROOP's JP map lacks off the same code
        (frame_sim.complete_symbols)."""
        self.version()

    def machine(self, ram: np.ndarray):
        """A machine that steps this build's thread 5 from `ram`."""
        self.simulates()
        return Machine(ram, self.rom, self.symbols, COLD_FCSR)

    # ---------------------------------------------------------------- course and act
    def level_of_course(self, course: int) -> int:
        ram, _fcsr = self.boot()
        table = ram[sim.phys(self.symbols[LEVEL_TO_COURSE]):sim.phys(self.symbols[LEVEL_TO_COURSE]) + LEVEL_COUNT]
        for level, value in enumerate(table.tolist(), start=1):
            if value == course:
                return level
        raise StageError(f"No level belongs to course {course}.")

    def course_of_level(self, level: int) -> int:
        ram, _fcsr = self.boot()
        if not 1 <= level <= LEVEL_COUNT:
            raise StageError(f"There is no level {level}.")
        return int(ram[sim.phys(self.symbols[LEVEL_TO_COURSE]) + level - 1])

    def _recipe(self) -> str:
        return f"v{STAGE_VERSION}-{self.version()}-{hashlib.sha256(self.rom).hexdigest()[:12]}"

    def key(self, level: int, area: int, act: int) -> str:
        return f"{self._recipe()}-{level}-{area}-{act}"

    def reference(self, level: int, area: int, act: int):
        """What a start someone else made is checked against: this machine's own reset of that place
        (start_check.Reference), made from his ROM."""
        import start_check  # noqa: PLC0415
        key = (level, area, act)
        if key not in self._references:
            self._references[key] = start_check.Reference.of(self.reset(level, area, act), self.symbols, self.map_path,
                                                             self.version())
        return self._references[key]

    def reference_for_slot(self, slot: bytes):
        """The reference for a slot's own level and area (its trailer) and act (its body)."""
        from sm64_events.slotlibrary.usamune import TRAILER_AREA, TRAILER_LEVEL  # noqa: PLC0415
        level, area = struct.unpack_from(">hh", slot, TRAILER_LEVEL)[0], struct.unpack_from(">h", slot, TRAILER_AREA)[0]
        act_at = sim.phys(self.symbols["gCurrActNum"]) - sim.phys(self.symbols["gMarioStates"])
        act = struct.unpack_from(">h", slot, act_at)[0] or 1
        return self.reference(level, area, act)

    def fingerprint(self) -> str:
        """The Usamune build this lab runs, as its slots name it (slotlibrary/usamune.py)."""
        from sm64_events.memory.layout import LayoutIncomplete  # noqa: PLC0415
        from sm64_events.slotlibrary import usamune  # noqa: PLC0415
        ram = self.boot()[0]

        def read(address: int, size: int) -> bytes:
            return ram[sim.phys(address):sim.phys(address) + size].tobytes()
        try:
            build = usamune.build_for(self.version())
        except LayoutIncomplete:                          # memory/layout.py names no slot addresses for this build
            raise StageError(SLOTS_UNKNOWN) from None
        return usamune.routine_fingerprint(read, build)

    def reset(self, level: int, area: int, act: int) -> np.ndarray:
        """The practice-reset state of that level, area and act (cached)."""
        with self.lock:
            path = self.cache / f"reset-{self.key(level, area, act)}.npz"
            if path.is_file():
                return np.load(path)["ram"]
            ram = self._make_reset(level, area, act)
            _keep(path, ram)
            return ram

    def reset_digest(self, level: int, area: int, act: int) -> str:
        """The reset's main pool, hashed: what a start file's pool delta was made against."""
        ram = self.reset(level, area, act)
        return hashlib.sha256(ram[sim.POOL_BEGIN:sim.POOL_BEGIN + sim.POOL_BYTES].tobytes()).hexdigest()

    def _make_reset(self, level: int, area: int, act: int) -> np.ndarray:
        """The recipe in the module docstring, on the cold boot restored to the castle grounds."""
        import cold_boot  # noqa: PLC0415
        course = self.course_of_level(level)
        game, snap = self._cold()
        game.restore(snap)
        syms, ram = self.symbols, game.cpu.ram
        if 1 <= course <= 25:
            ram[sim.phys(syms["gSaveBuffer"]) + COURSE_STARS + course - 1] = ALL_ACTS
        warp = sim.phys(syms["sWarpDest"])
        game.pending = lambda: struct.pack_into(">BBBBI", game.cpu.ram, warp, 1, level, area, ENTRY_NODE, 0)
        in_menu = pushes = 0
        for _ in range(MENU_FRAMES):
            if s16(ram, syms[CURR_LEVEL]) == level and s16(ram, syms[CURR_AREA]) == area and u32(ram, syms[MARIO_OBJECT]):
                entry = u32(ram, syms["gMarioStates"] + 0x0C)    # how this level's entry spawns him: the reset's too
                break
            buttons, stick = 0, (0, 0)
            if s16(ram, syms[CURR_LEVEL]) == level and not u32(ram, syms[MARIO_OBJECT]):
                buttons, stick, pushes = menu_input(in_menu, pushes, act)
                in_menu += 1
            if not game.step(buttons, stick):
                raise StageError(f"The simulated game stopped on its way to level {level}: {game.cpu.violations[:2]}")
        else:
            raise StageError(f"The simulated game never reached level {level} area {area} act {act}.")
        if 1 <= course <= LAST_ACT_COURSE and ram[sim.phys(syms["gCurrActNum"]) + 1] != act:   # an act selector chose
            raise StageError(f"The act selector chose act {ram[sim.phys(syms['gCurrActNum']) + 1]}, not {act}.")
        try:
            cold_boot.arrived(game)
            cold_boot.settle(game)
        except SystemExit as stopped:
            raise StageError(f"The simulated game did not settle in level {level}: {stopped}") from None
        for _ in range(SETTLE_FRAMES):
            game.step(0)
        struct.pack_into(">I", ram, sim.phys(cold_boot.USAMUNE[self.version()]["settings"]), USAMUNE_RESET_WORD)
        game.cpu.put_u32(syms["sTransitionTimer"], 0)
        before = u32(ram, syms[MARIO_OBJECT])
        game.step(RESET_BUTTON)
        game.step(RESET_BUTTON)
        if u32(ram, syms["gMarioStates"] + 0x0C) != entry or u32(ram, syms[MARIO_OBJECT]) == before \
                or s16(ram, syms[CURR_LEVEL]) != level or game.cpu.violations:
            raise StageError("Usamune's practice reset did not run in the simulated game.")
        return ram.copy()

    # ---------------------------------------------------------------- as a scene
    def package(self, level: int, area: int, act: int, frames: int = 150, out: Path | None = None) -> Path:
        """That reset played on with no input, as a scene package the viewer opens (cached; `out`: written there
        instead, uncached)."""
        import sim_scene  # noqa: PLC0415
        cached = out is None
        out = out or self.cache / f"stage-{self.key(level, area, act)}-p{PACKAGE_VERSION}-{frames}.sm64scene"
        if cached and out.is_file():
            return out
        start = self.reset(level, area, act)
        template = start[sim.phys(self.symbols["gControllerPads"]):sim.phys(self.symbols["gControllerPads"]) + 24].tobytes()
        machine = self.machine(start.copy())
        writer = sim_scene.SimScene(out, self.cache / f"work-{time.monotonic_ns()}", self.rom_path, self.symbols, self.vi(),
                                    self.version(), seeds=self.cache)
        try:
            for _ in range(frames):
                machine.step(pads(0, 0, 0, template))
                writer.add(machine.ram)
            writer.finish()
        except BaseException:
            writer.abandon()
            raise
        return out
