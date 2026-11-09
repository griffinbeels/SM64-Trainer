# SPDX-License-Identifier: GPL-2.0-or-later
# Runs in the simulation program (SM64TrainerSim.exe); licenses/README.md lists its GPL-2.0-or-later code.
"""Boot a Usamune ROM (US v1.93u or JP v1.93j) from its cartridge image inside Unicorn, with no recording (lab only).

    uv run --with unicorn==2.1.4 --with capstone python tools/scene_lab/cold_boot.py fetch-maps
    uv run --with unicorn==2.1.4 --with capstone python tools/scene_lab/cold_boot.py boot --version jp [--level 9]

Usamune's own entry is reproduced: its image is copied to 0x80400000, and IPL3's first MiB of game code to the entry
point, where the cartridge's own entry code says. Then the game's threads run in order until each would block: thread 1
until it starts thread 3, thread 3 until it waits for its first interrupt, thread 4 (audio) until it waits for its first
VI, thread 5 (the game) until its first frame is sent. From there frame_sim's GameCpu runs thread 5 one frame per step,
as it does for a recording. The device registers the game pokes are plain zeroed memory; the libultra calls that talk
to devices are answered here. There is no EEPROM: save file A is marked as existing instead, so the castle grounds start
Mario standing rather than in the intro.

Measured (2026-11-08): both builds reach the castle grounds at frame 174 under the same pads, Mario at 8034b888 (US)
and 8034a518 (JP); a warp to BoB gives 8034ffc8 and 8034ec58, the places Griffin's own BoB entries give, and BoB's
main pool is block for block the one in his recording. Under identical pads the two builds stay identical in the
castle grounds (600 frames), BoB and JRB (300); WF's course entry takes one frame longer on US.

Inputs, read only: the ROMs (lab.json "rom" for US; the JP ROM is found beside it by its header) and STROOP's maps
(lab refs/Mapping<V>.map; `fetch-maps` downloads the pinned commit). Nothing here touches a running game.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frame_sim as sim  # noqa: E402

STROOP_COMMIT = "6771197ad0f50718d3d4a13387e01d1e6d9c0d1a"
STROOP_URL = "https://raw.githubusercontent.com/SM64-TAS-ABC/STROOP/{commit}/STROOP/Mappings/Mapping{v}.map"
USAMUNE_HEADER = {"us": b"SM64 USAMUNE v1.93u", "jp": b"SM64 USAMUNE v1.93j"}
USAMUNE_IMAGE = sim.USAMUNE_IMAGE
# Usamune's own call into its save/load dispatcher sits in try_print_debug_mario_level_info: a jal into its image
# (frame_sim.SimBuild.site). Its routines and settings, read from each build's code (US 0x8040392C/0x80411CF0, JP 0x80403930/0x80411D84).
USAMUNE = {"us": {"save": 0x8040392C, "load": 0x80411CF0, "settings": 0x80417B4C},
           "jp": {"save": 0x80403930, "load": 0x80411D84, "settings": 0x80417C14}}
SLOT_BASE, SLOT_BYTES, BODY_BYTES = 0x80500000, 0x26460, 0x26320
EXTRA_STUBS = ("osInitialize", "osCreateThread", "osStartThread", "osSetThreadPri", "osCreateViManager", "osCreatePiManager",
               "osViSetMode", "osViSetEvent", "osSetEventMesg", "osContInit", "osSetTime", "osViBlack",
               "osViSetSpecialFeatures", "osAiSetFrequency")
BUTTON_A, BUTTON_START = 0x8000, 0x1000
CASTLE_GROUNDS = 16


@dataclass(frozen=True)
class Rom:
    version: str
    path: Path
    data: bytes
    image_rom: int        # where Usamune's expansion-RAM image sits in the cartridge
    image_bytes: int      # how much its entry code copies


def read_entry_dma(data: bytes) -> tuple[int, int]:
    """Usamune's entry code: `lui v1, hi; addiu v1, lo` for the cartridge address, then the same for the length - 1."""
    words = struct.unpack(">8I", data[0x1000:0x1020])
    pairs = []
    for hi, lo in ((words[3], words[4]), (words[6], words[7])):
        if hi >> 16 != 0x3C03 or lo >> 16 != 0x2463:
            raise SystemExit("this cartridge's entry is not Usamune's PI copy")
        pairs.append(((hi & 0xFFFF) << 16) + (lo & 0xFFFF) - (0x10000 if lo & 0x8000 else 0))
    cart, length = pairs
    return cart - 0x10000000, length + 1


def find_rom(version: str, config: dict | None = None) -> Rom:
    config = config or json.loads((sim.lab_root() / "lab.json").read_text(encoding="utf-8"))
    us = Path(config["rom"])
    candidates = [us] if version == "us" else sorted(p for p in us.parent.iterdir() if p.suffix.lower() == ".z64")
    for path in candidates:
        data = path.read_bytes()
        if data[0x20:0x20 + len(USAMUNE_HEADER[version])] == USAMUNE_HEADER[version]:
            image_rom, image_bytes = read_entry_dma(data)
            return Rom(version, path, data, image_rom, image_bytes)
    raise SystemExit(f"no Usamune {version.upper()} ROM found beside {us}")


def map_path(version: str) -> Path:
    return sim.lab_root() / "refs" / f"Mapping{version.upper()}.map"


def fetch_maps() -> None:
    for version in ("us", "jp"):
        target = map_path(version)
        if target.exists():
            print(f"{target} is there ({hashlib.sha256(target.read_bytes()).hexdigest()[:16]})")
            continue
        url = STROOP_URL.format(commit=STROOP_COMMIT, v=version.upper())
        with urllib.request.urlopen(url, timeout=60) as response:   # noqa: S310 - a fixed https URL at a pinned commit
            data = response.read()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        print(f"{target}: {len(data)} bytes from STROOP @ {STROOP_COMMIT[:12]}")


jal_site_after = sim.jal_site_after


class BootCpu(sim.GameCpu):
    """GameCpu over a cold RDRAM, with the device and thread calls a boot needs. Its SimBuild is read off the
    cartridge's own code (IPL3's copy of the main segment), for this CPU alone: frame_sim's module is never
    changed, so a process can hold both builds at once (the atlas does)."""

    def __init__(self, rom: Rom, symbols: sim.Symbols):
        self.version = rom.version
        ram = np.zeros(sim.RDRAM_BYTES, np.uint8)
        ram[0x246000:0x346000] = np.frombuffer(rom.data, np.uint8, 0x100000, 0x1000)       # IPL3's copy of the game's code
        ram[0x400000:0x400000 + rom.image_bytes] = np.frombuffer(rom.data, np.uint8, rom.image_bytes, rom.image_rom)
        for address, value in ((0x80000300, 1), (0x80000308, 0xB0000000), (0x80000318, sim.RDRAM_BYTES)):
            struct.pack_into(">I", ram, sim.phys(address), value)   # osTvType NTSC, osRomBase, osMemSize
        self.threads: dict[int, dict] = {}
        self.started: list[int] = []
        self.stop_rule = None
        super().__init__(ram, rom.data, symbols, 0x01000800, extra_stubs=EXTRA_STUBS)
        uc = self.uc
        uc.mem_map(0x04000000, 0x00900000)     # SP, DP, MI, VI, AI, PI, RI, SI registers: zeros, writes kept
        uc.mem_map(0x1FC00000, 0x1000)         # PIF RAM
        uc.mem_map(0x10000000, 0x00800000)     # the cartridge as the PI bus sees it
        uc.mem_write(0x10000000, rom.data[:0x800000])
        self._replace("osCreateThread", self._create_thread)
        self._replace("osStartThread", self._start_thread)
        self._replace("osContInit", self._cont_init)
        self._replace("osRecvMesg", self._recv_or_stop)
        self._replace("wait_for_audio_frames", self._audio_frames_here)

    def _replace(self, name: str, handler) -> None:
        at = sim.sext(self.symbols[name])
        self.uc.hook_add(self.unicorn.UC_HOOK_CODE, self._stub(name + "*", handler), begin=at, end=at)

    def _create_thread(self) -> int:
        self.threads[self.arg(0)] = {"id": self.arg(1), "entry": self.arg(2), "arg": self.arg(3), "sp": self.arg(4)}
        return 0

    def _start_thread(self) -> int:
        thread = self.arg(0)
        self.started.append(thread)
        if self.stop_rule and self.stop_rule("start", self.threads.get(thread, {}).get("id")):
            self.uc.emu_stop()
        return 0

    def _cont_init(self) -> int:
        self.ram[sim.phys(self.arg(1))] = 1                                          # one controller, in port 1
        struct.pack_into(">HBB", self.ram, sim.phys(self.arg(2)), 0x0005, 0, 0)      # CONT_TYPE_NORMAL
        return 0

    def _recv_or_stop(self) -> int:
        ra = self.uc.reg_read(self.mips.UC_MIPS_REG_RA) & 0xFFFFFFFF
        if self.stop_rule and self.stop_rule("recv", ra):
            self.uc.emu_stop()
            return 0
        return self._recv()

    def _audio_frames_here(self) -> int:
        self.put_u32(self.symbols["gAudioFrameCount"], self.arg(0))
        return 0

    def run_thread(self, pc: int, sp: int, a0: int, rule) -> None:
        self.stop_rule = rule
        self.uc.reg_write(self.mips.UC_MIPS_REG_SP, sim.sext(sp))
        self.uc.reg_write(self.mips.UC_MIPS_REG_A0, sim.sext(a0))
        self.uc.reg_write(self.mips.UC_MIPS_REG_RA, sim.sext(sim.NEVER))
        self.armed = False
        self.uc.emu_start(sim.sext(pc), sim.sext(sim.NEVER), timeout=sim.BUDGET_US)
        self.stop_rule = None
        if self.violations:
            raise SystemExit(f"{self.version} boot at {pc:08x}: {self.violations[:3]}")


class ColdGame:
    """A booted build: thread 5 one frame per step(); Usamune's save and load at its own call site."""

    def __init__(self, version: str, config: dict | None = None):
        rom = find_rom(version, config)
        if not map_path(version).exists():
            raise SystemExit(f"no {map_path(version)}: run `cold_boot.py fetch-maps`")
        self.version = version
        self.rom = rom
        self.u = USAMUNE[version]
        cpu = self.cpu = BootCpu(rom, sim.Symbols(map_path(version)))
        self.resume = cpu.resume
        within = lambda name, address: cpu.symbols[name] <= address < cpu.symbols[name] + 0x400  # noqa: E731
        thread = lambda ident: next(t for t in cpu.threads.values() if t["id"] == ident)  # noqa: E731
        cpu.run_thread(USAMUNE_IMAGE, 0x80000400, 0, lambda kind, x: kind == "start" and x == 1)
        idle = thread(1)
        cpu.run_thread(idle["entry"], idle["sp"], idle["arg"], lambda kind, x: kind == "start" and x == 3)
        main = thread(3)
        cpu.run_thread(main["entry"], main["sp"], main["arg"],
                       lambda kind, x: kind == "recv" and within("thread3_main", x) and len(cpu.started) >= 4)
        sound = thread(4)
        cpu.run_thread(sound["entry"], sound["sp"], sound["arg"], lambda kind, x: kind == "recv" and within("thread4_sound", x))
        game = thread(5)
        if cpu.symbols.next_after(cpu.symbols["gThread5Stack"]) != game["sp"]:    # where SimBuild puts its frames
            raise SystemExit(f"{version}: thread 5's stack does not end where the map's next symbol begins")
        first = jal_site_after(cpu.ram, cpu.symbols["rendering_init"], cpu.symbols["send_display_list"])
        self._first = False
        cpu.uc.hook_add(cpu.unicorn.UC_HOOK_CODE, self._first_frame, begin=sim.sext(first), end=sim.sext(first))
        cpu.run_thread(game["entry"], game["sp"], game["arg"], None)
        if not self._first:
            raise SystemExit(f"{version}: thread 5 never sent its first frame")
        self.begin = first
        cpu.scheduler_model()
        sim.complete_symbols(cpu.symbols, cpu.ram)        # the engine segment is in RAM now
        self.frames = 0
        self.site = self._find_site()
        self.pending = None
        cpu.uc.hook_add(cpu.unicorn.UC_HOOK_CODE, self._on_site, begin=sim.sext(self.site), end=sim.sext(self.site))
        self.body = (cpu.symbols["gMarioStates"], cpu.symbols["gMarioStates"] + BODY_BYTES)

    def _first_frame(self, uc, *_):
        if not self._first:
            self._first = True
            uc.emu_stop()

    def _find_site(self) -> int:
        if self.cpu.build.site is None:
            raise SystemExit("no call into Usamune in try_print_debug_mario_level_info")
        return self.cpu.build.site

    def _on_site(self, uc, *_):
        if self.pending is not None:
            uc.emu_stop()

    def _pc(self) -> int:
        return self.cpu.uc.reg_read(self.cpu.mips.UC_MIPS_REG_PC) & 0xFFFFFFFF

    def step(self, buttons: int = 0, stick: tuple[int, int] = (0, 0), pads: bytes | None = None) -> bool:
        """One frame. `pads` (24 bytes, as gControllerPads holds them) overrides buttons and stick."""
        cpu = self.cpu
        if pads is None:
            raw = bytearray(24)
            struct.pack_into(">Hbb", raw, 0, buttons, stick[0], stick[1])
            pads = bytes(raw)
        cpu.pads = pads
        cpu.armed = self.begin != self.resume
        had = self.pending is not None
        try:
            cpu.uc.emu_start(sim.sext(self.begin), sim.sext(sim.NEVER), timeout=sim.BUDGET_US)
            if self.pending is not None and self._pc() == self.site:
                action, self.pending = self.pending, None
                action()
                cpu.uc.emu_start(sim.sext(self.site), sim.sext(sim.NEVER), timeout=sim.BUDGET_US)
        except cpu.unicorn.UcError as error:
            cpu.violations.append(f"unicorn {error} at {self._pc():08x} in {cpu.symbols.name_of(self._pc())}")
            return False
        self.begin = self.resume
        if had and self.pending is not None:
            self.pending = None
            cpu.violations.append("this frame never reached Usamune's call site")
        if self._pc() != self.resume:
            cpu.violations.append(f"stopped at {self._pc():08x} in {cpu.symbols.name_of(self._pc())}")
            return False
        if cpu.flush_due:
            cpu.uc.ctl_flush_tb()
            cpu.flush_due = False
        cpu.scheduler_model()
        self.frames += 1
        return not cpu.violations

    def call(self, routine: int) -> None:
        cpu = self.cpu
        context = cpu.uc.context_save()
        cpu.uc.reg_write(cpu.mips.UC_MIPS_REG_RA, sim.sext(sim.NEVER))
        cpu.uc.emu_start(sim.sext(routine), sim.sext(sim.NEVER), timeout=sim.BUDGET_US)
        if self._pc() != sim.NEVER:
            cpu.violations.append(f"routine {routine:08x} stopped at {self._pc():08x}")
        cpu.uc.context_restore(context)

    def save(self, index: int) -> bytes:
        """Usamune's own save into slot `index`. Call it only inside a pending action, at Usamune's call site."""
        self.cpu.ram[sim.phys(self.u["settings"] + 0x119)] = index
        self.call(self.u["save"])
        low = sim.phys(SLOT_BASE + index * SLOT_BYTES)
        return self.cpu.ram[low:low + SLOT_BYTES].tobytes()

    def load(self, index: int) -> None:
        self.cpu.ram[sim.phys(self.u["settings"] + 0x118)] = index
        self.call(self.u["load"])

    def snapshot(self):
        return self.cpu.ram.copy(), self.cpu.uc.context_save(), self.begin, self.frames

    def restore(self, snap) -> None:
        ram, context, begin, frames = snap
        self.cpu.ram[:] = ram
        self.cpu.uc.context_restore(context)
        self.cpu.uc.ctl_flush_tb()
        self.begin, self.frames = begin, frames
        self.cpu.violations.clear()
        self.pending = None

    def u16(self, name: str) -> int:
        return struct.unpack_from(">H", self.cpu.ram, sim.phys(self.cpu.symbols[name]))[0]

    def u32(self, name: str) -> int:
        return self.cpu.u32(self.cpu.symbols[name])

    def mario_pos(self) -> np.ndarray:
        return np.array(struct.unpack_from(">3f", self.cpu.ram, sim.phys(self.cpu.symbols["gMarioStates"]) + 0x3C))

    def mario_place(self) -> int:
        return self.u32("gMarioObject")

    def in_level(self, level: int) -> bool:
        return self.u16("gCurrLevelNum") == level and self.mario_place() != 0


def boot_into_castle(version: str, config: dict | None = None) -> tuple[ColdGame, int]:
    """Boot, mark save file A as existing, then press Start and A now and then until the castle grounds hold Mario."""
    game = ColdGame(version, config)
    flags = game.cpu.symbols["gSaveBuffer"] + 8          # files[0][0].flags; with no EEPROM this is all of it
    game.cpu.put_u32(flags, game.cpu.u32(flags) | 1)     # SAVE_FLAG_FILE_EXISTS
    for frame in range(2000):
        phase = frame % 40
        if not game.step(BUTTON_START if phase == 0 else BUTTON_A if phase == 20 else 0):
            raise SystemExit(f"{version} frame {frame}: {game.cpu.violations[:3]}")
        if game.in_level(CASTLE_GROUNDS):
            return game, frame
    raise SystemExit(f"{version}: never reached the castle grounds")


def warp_to(game: ColdGame, level: int, area: int = 1, node: int = 0x0A, limit: int = 900) -> int:
    """Change level the way a painting does: sWarpDest written inside Mario's update, after warp_area has run and before
    play_mode_normal checks for a level change; A at the act selector picks the act it offers. Returns the frames."""
    syms = game.cpu.symbols
    if level == CASTLE_GROUNDS and game.in_level(level):
        return 0

    def write():
        struct.pack_into(">BBBBI", game.cpu.ram, sim.phys(syms["sWarpDest"]), 1, level, area, node, 0)
    game.pending = write
    for frames in range(1, limit):
        buttons = BUTTON_A if frames > 30 and frames % 20 == 0 and not game.mario_place() else 0
        if not game.step(buttons):
            raise SystemExit(f"{game.version}: {game.cpu.violations[:3]}")
        if game.in_level(level) and frames > 2:
            arrived(game)
            return frames
    raise SystemExit(f"{game.version}: the warp to level {level} never arrived")


def arrived(game: ColdGame) -> None:
    """What a real course entry leaves behind that this warp does not: sTransitionTimer back at 0. Left at -1,
    Usamune's L practice reset never fires (handoff-boundary's bisection against a game where L works, 2026-11-08)."""
    struct.pack_into(">h", game.cpu.ram, sim.phys(game.cpu.symbols["sTransitionTimer"]), 0)


def settle(game: ColdGame, limit: int = 600) -> int:
    """Stand still until Mario's own update reaches Usamune's call site again (a course entry holds it a while)."""
    for waited in range(1, limit):
        snap = game.snapshot()
        game.pending = lambda: None
        ok = game.step()
        game.cpu.violations.clear()
        if ok and waited > 20:
            game.restore(snap)
            return waited
    raise SystemExit(f"{game.version}: Mario's update never reached Usamune's call site")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("fetch-maps")
    boot = commands.add_parser("boot")
    boot.add_argument("--version", choices=("us", "jp"), required=True)
    boot.add_argument("--level", type=int, default=CASTLE_GROUNDS)
    args = parser.parse_args()
    if args.command == "fetch-maps":
        fetch_maps()
        return 0
    game, frame = boot_into_castle(args.version)
    frame += warp_to(game, args.level)
    print(json.dumps({"version": args.version, "level": args.level, "frame": frame, "mario": f"{game.mario_place():08x}",
                      "pos": [round(float(x), 1) for x in game.mario_pos()]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
