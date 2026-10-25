"""E2a probe: re-run SM64's OWN scene-graph renderer offline, under a moved camera.

    uv run --with unicorn python tools/scene_lab/e2_georender.py regen <frame_dir> <out_dir>
        --rom <z64> --map <MappingUS.map> [--orbit YAW,PITCH,DIST ...] [--focus-height H]
    uv run --with unicorn python tools/scene_lab/e2_georender.py from-tap <session>
        --rom <z64> --map <MappingUS.map> [--every N] [--limit N] [--out report.json]

<frame_dir> is the savestate frame input (savestate_frames.py) of the gfx pool
the RAM image last rendered (gGfxPool). The game's geo_process_root, its
graph-node callbacks and everything they call execute in a sandboxed N64
address space: Unicorn's MIPS64 core (QEMU, GPLv2; lab only, never a project
dependency) as an R4000 in 32-bit kernel mode with FR=0 and the game's own
FCR31, 8 MiB RDRAM at physical 0 and a private stack page outside it. Nothing
else is mapped, so any MMIO access, exception or unmapped fetch stops the run.
Every executed block must be byte-identical to the given ROM (main and engine
segments at their load addresses, anything else found verbatim in the ROM);
the exception vectors are refused outright. Every RDRAM read and write is
recorded per byte (first touch: read or write) by decoding each load and store
before it executes: Unicorn 2.1.4's MIPS memory hooks raise a spurious
reserved-instruction exception on a delay-slot access, so they are not used.

regen first proves the vehicle: it poisons the captured pool's geo output and
regenerates it in place with the source camera, compares every byte the game
wrote (main list and pool allocations; never-written padding keeps stale pool
memory and is counted, not compared), then repeats the run relocated to the
pool's free middle and compares command by command with pool references
replaced by the bytes they point at. The closure proof re-runs with every byte
outside the recorded read set and executed code poisoned; the output must not
move. Each --orbit re-runs from the same RAM with gLakituState.pos and focus
moved around Mario (yaw relative to the source camera, degrees), splices the
new geo output between the captured prologue and HUD, and writes a frame input
replay.py can render. report.json carries the comparisons, the read and write
sets by region, the persistent writes (side effects), what a recording would
carry (provenance.py's ROM attribution) and per-object culling decisions.

from-tap is the E2-live proof: for each task of a scene-tap session it rebuilds
RDRAM from the ROM's own images plus what the session captured (closure, the
scene state spans, the main pool's key and deltas), poisons everything else,
regenerates the task's geo output in place and compares every byte the game
wrote with the captured list. It also lists every byte the renderer read that
the session did not capture, and checks the rebuild against the tap's full
RDRAM copy of task 0. RAM images are private: outputs belong in scratch only.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import struct
import sys
import tempfile
from pathlib import Path

import numpy as np
from unicorn import (UC_ARCH_MIPS, UC_HOOK_BLOCK, UC_HOOK_CODE, UC_HOOK_INTR, UC_MODE_BIG_ENDIAN,
                     UC_MODE_MIPS64, Uc, UcError)
from unicorn import mips_const as mips

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sm64_events.memory.layout import layout_for  # noqa: E402
from tap import GFX_POOL_BYTES  # noqa: E402

RDRAM_BYTES = 8 << 20
SCRATCH_PHYS = 0x01000000          # private page outside RDRAM: stack and return sentinel
SCRATCH_BYTES = 1 << 20
SENTINEL = 0x81000000
STACK_TOP = 0x810FFF00
STATUS_CU1 = 0x20000000            # kernel, EXL/ERL/IE clear, FR=0 (libultra threads never set FR)

# Every game address comes from the decomp's linker map for the ROM's version
# (STROOP MappingUS.map, --map), filled in by load_map and cross-checked
# against memory/layout.py wherever a layout row names the same global.
SYMBOLS = ("geo_process_root", "geo_process_object", "geo_process_held_object", "obj_is_in_view",
           "gGlobalTimer", "gCurrentArea", "D_8032CE74", "D_8032CE78", "gFBSetColor",
           "gAreaUpdateCounter", "gCurrLevelNum", "gDisplayListHead", "gGfxPoolEnd", "gGfxPool",
           "gMarioStates", "sSegmentTable", "gDisplayListHeap", "gLakituState", "gObjectPool",
           "gGfxPools", "sFloorAlignMatrix")
LAYOUT_ROWS_FOR = {"gGlobalTimer": "global_timer", "gCurrentArea": "current_area_pointer",
                   "gAreaUpdateCounter": "area_update_counter", "gCurrLevelNum": "curr_level",
                   "gMarioStates": "mario_struct", "gObjectPool": "object_pool"}
US: dict[str, int] = {}
CODE_SEGMENTS: list[tuple[str, int, int, int]] = []   # ROM-loaded images: (name, start, size, ROM offset)
REGIONS: list[tuple[str, int, int]] = []              # read/write-set classification: (name, start, end)
OBJECT_BYTES, OBJECT_COUNT = 0x260, 240
LAKITU_FOCUS, LAKITU_POS = 0x80, 0x8C
MARIO_POS = 0x3C
OBJ_BEHAVIOR = 0x20C


def be_from_pj64(data: bytes) -> bytes:
    """Project64 keeps RDRAM as little-endian words; the N64 sees big-endian bytes."""
    return np.frombuffer(data, "<u4").astype(">u4").tobytes()


def pj64_from_be(data: bytes) -> bytes:
    return np.frombuffer(data, ">u4").astype("<u4").tobytes()


def phys(address: int) -> int:
    return address & 0x1FFFFFFF


def sext(address: int) -> int:
    address &= 0xFFFFFFFF
    return address | 0xFFFFFFFF00000000 if address & 0x80000000 else address


GPR = [getattr(mips, f"UC_MIPS_REG_{n}") for n in range(32)]
# MIPS III loads and stores: opcode -> (write, bytes, kind); "left"/"right" are the unaligned halves.
ACCESS = {0x20: (0, 1, ""), 0x21: (0, 2, ""), 0x22: (0, 4, "left"), 0x23: (0, 4, ""), 0x24: (0, 1, ""),
          0x25: (0, 2, ""), 0x26: (0, 4, "right"), 0x27: (0, 4, ""), 0x30: (0, 4, ""), 0x31: (0, 4, ""),
          0x34: (0, 8, ""), 0x35: (0, 8, ""), 0x37: (0, 8, ""), 0x1A: (0, 8, "left"), 0x1B: (0, 8, "right"),
          0x28: (1, 1, ""), 0x29: (1, 2, ""), 0x2A: (1, 4, "left"), 0x2B: (1, 4, ""), 0x2C: (1, 8, "left"),
          0x2D: (1, 8, "right"), 0x2E: (1, 4, "right"), 0x38: (1, 4, ""), 0x39: (1, 4, ""), 0x3C: (1, 8, ""),
          0x3D: (1, 8, ""), 0x3F: (1, 8, "")}


def decode_access(word: int) -> tuple | None:
    entry = ACCESS.get(word >> 26)
    if entry is None:
        return None
    offset = word & 0xFFFF
    return entry[0], (word >> 21) & 31, offset - 0x10000 if offset & 0x8000 else offset, entry[1], entry[2]


class Sandbox:
    """One N64 CPU context over a private RDRAM copy, instrumented per byte."""

    def __init__(self, ram_be: bytes, rom: bytes, fcr31: int, trace_access: bool = True):
        self.rom = rom
        self.uc = uc = Uc(UC_ARCH_MIPS, UC_MODE_MIPS64 | UC_MODE_BIG_ENDIAN)
        uc.ctl_set_cpu_model(mips.UC_CPU_MIPS64_R4000)
        uc.mem_map(0, RDRAM_BYTES)
        uc.mem_write(0, ram_be)
        uc.mem_map(SCRATCH_PHYS, SCRATCH_BYTES)
        uc.reg_write(mips.UC_MIPS_REG_CP0_STATUS, STATUS_CU1)
        uc.reg_write(mips.UC_MIPS_REG_FCSR, fcr31)
        self.first = bytearray(RDRAM_BYTES)     # 0 untouched, 1 first read, 2 first written
        self.read = bytearray(RDRAM_BYTES)
        self.wrote = bytearray(RDRAM_BYTES)
        self.code = bytearray(RDRAM_BYTES)
        self.verified: dict[int, str] = {}
        self.loaded: list[tuple[str, int, int, int]] = []
        self.violations: list[str] = []
        self.instructions = 0
        self.stack_accesses = 0
        self.culling: list[tuple[int, int]] = []
        self.decoded: dict[int, tuple | None] = {}
        uc.hook_add(UC_HOOK_BLOCK, self._on_block)
        if trace_access:
            uc.hook_add(UC_HOOK_CODE, self._on_instruction)
        uc.hook_add(UC_HOOK_INTR, self._on_exception)
        self._pending_view = 0
        uc.hook_add(UC_HOOK_CODE, self._on_view_entry, begin=sext(US["obj_is_in_view"]),
                    end=sext(US["obj_is_in_view"]))
        returned = self._call_return(US["geo_process_object"], US["geo_process_held_object"], US["obj_is_in_view"])
        uc.hook_add(UC_HOOK_CODE, self._on_view_return, begin=sext(returned), end=sext(returned))

    # --- memory helpers (virtual KSEG0 addresses) ---
    def u32(self, address: int) -> int:
        return struct.unpack(">I", self.uc.mem_read(phys(address), 4))[0]

    def put_u32(self, address: int, value: int) -> None:
        self.uc.mem_write(phys(address), struct.pack(">I", value & 0xFFFFFFFF))

    def f32s(self, address: int, count: int) -> list[float]:
        return list(struct.unpack(f">{count}f", self.uc.mem_read(phys(address), 4 * count)))

    def put_f32s(self, address: int, values) -> None:
        self.uc.mem_write(phys(address), struct.pack(f">{len(values)}f", *values))

    def ram(self) -> bytes:
        return bytes(self.uc.mem_read(0, RDRAM_BYTES))

    def _call_return(self, start: int, end: int, callee: int) -> int:
        jal = 0x0C000000 | ((callee & 0x0FFFFFFF) >> 2)
        sites = [address for address in range(start, end, 4) if self.u32(address) == jal]
        if len(sites) != 1:
            raise RuntimeError(f"expected one call of {callee:08x}, found {len(sites)}")
        return sites[0] + 8

    # --- hooks ---
    def _on_block(self, uc, address, size, _):
        address &= 0xFFFFFFFF
        if address == SENTINEL:
            return
        self.instructions += size // 4
        if address not in self.verified:
            self.verified[address] = self._origin(address, size)
            if self.verified[address] is None:
                self.violations.append(f"block {address:08x}+{size} is not ROM code")
                uc.emu_stop()
                return
        start = phys(address)
        self.code[start:start + size] = b"\x01" * size

    def _origin(self, address: int, size: int) -> str | None:
        if address < 0x80000400 or phys(address) + size > RDRAM_BYTES:
            return None  # exception vectors (or beyond RDRAM) never run here
        code = bytes(self.uc.mem_read(phys(address), size))
        for name, start, length, rom_offset in (*CODE_SEGMENTS, *self.loaded):
            if start <= address and address + size <= start + length:
                at = rom_offset + address - start
                return name if self.rom[at:at + size] == code else None
        # Code outside the fixed images (a segment the level loaded raw): find a unique 64-byte
        # window in the ROM, then take the whole identical run around it as one loaded image.
        start = phys(address) & ~3
        window = bytes(self.uc.mem_read(start, 64))
        found = self.rom.find(window)
        if found < 0 or self.rom.find(window, found + 1) >= 0:
            return None
        ram = self.uc.mem_read(0, RDRAM_BYTES)
        lo, hi, delta = start, start + 64, found - start
        while lo >= 4 and lo + delta >= 4 and ram[lo - 4:lo] == self.rom[lo - 4 + delta:lo + delta]:
            lo -= 4
        while hi + 4 <= RDRAM_BYTES and hi + delta + 4 <= len(self.rom) and \
                ram[hi:hi + 4] == self.rom[hi + delta:hi + delta + 4]:
            hi += 4
        name = f"rom {lo + delta:06x}-{hi + delta:06x} at {0x80000000 | lo:08x}"
        self.loaded.append((name, 0x80000000 | lo, hi - lo, lo + delta))
        return name if lo <= phys(address) and phys(address) + size <= hi else None

    def _on_instruction(self, uc, address, size, _):
        """Decode loads and stores before they execute (Unicorn 2.1.4's MIPS memory hooks raise
        a spurious reserved-instruction exception on a delay-slot access, so they are not used)."""
        access = self.decoded.get(address, False)
        if access is False:
            access = self.decoded[address] = decode_access(struct.unpack(">I", uc.mem_read(phys(address), 4))[0])
        if access is None:
            return
        write, base, offset, size, kind = access
        target = phys((uc.reg_read(GPR[base]) + offset) & 0xFFFFFFFF)
        if kind == "left":
            size -= target & (size - 1)
        elif kind == "right":
            size, target = (target & (size - 1)) + 1, target & ~(size - 1)
        if target >= RDRAM_BYTES:
            self.stack_accesses += 1
            return
        mark = 2 if write else 1
        first = self.first
        for at in range(target, target + size):
            if not first[at]:
                first[at] = mark
        (self.wrote if write else self.read)[target:target + size] = b"\x01" * size

    def _on_exception(self, uc, number, _):
        pc = uc.reg_read(mips.UC_MIPS_REG_PC) & 0xFFFFFFFF
        cause = uc.reg_read(mips.UC_MIPS_REG_CP0_STATUS)
        self.violations.append(f"exception {number} at pc {pc:08x} (status {cause:08x})")
        uc.emu_stop()

    def _on_view_entry(self, uc, address, size, _):
        self._pending_view = uc.reg_read(mips.UC_MIPS_REG_A0) & 0xFFFFFFFF

    def _on_view_return(self, uc, address, size, _):
        self.culling.append((self._pending_view, uc.reg_read(mips.UC_MIPS_REG_V0) & 0xFFFFFFFF))

    # --- call ---
    def call(self, function: int, *args: int, budget: int = 200_000_000) -> None:
        uc = self.uc
        for register, value in zip((mips.UC_MIPS_REG_A0, mips.UC_MIPS_REG_A1, mips.UC_MIPS_REG_A2,
                                    mips.UC_MIPS_REG_A3)[:len(args)], args, strict=True):
            uc.reg_write(register, sext(value))
        uc.reg_write(mips.UC_MIPS_REG_SP, sext(STACK_TOP))
        uc.reg_write(mips.UC_MIPS_REG_RA, sext(SENTINEL))
        try:
            uc.emu_start(sext(function), sext(SENTINEL), count=budget)
        except UcError as error:
            self.violations.append(f"unicorn: {error}")
        pc = uc.reg_read(mips.UC_MIPS_REG_PC) & 0xFFFFFFFF
        if pc != SENTINEL and not self.violations:
            self.violations.append(f"stopped at {pc:08x} before returning")


# --- display lists (F3D, big-endian bytes) ---
def segment_table(ram: bytes) -> list[int]:
    at = phys(US["sSegmentTable"])
    return [phys(value) for value in struct.unpack_from(">32I", ram, at)]


def walk(ram: bytes, start: int, count: int | None, pool: tuple[int, int], segments: list[int],
         written: np.ndarray | None = None) -> list[tuple]:
    """Canonical command stream: pool references become the bytes they point at; with a
    written mask, bytes the game never wrote (struct padding: stale pool memory) read '--'."""

    def payload(target: int, size: int) -> str:
        if written is None:
            return ram[target:target + size].hex()
        return "".join(f"{ram[at]:02x}" if written[at] else "--" for at in range(target, target + size))
    out: list[tuple] = []

    def resolve(w1: int) -> int:
        return (segments[(w1 >> 24) & 0xF] + (w1 & 0xFFFFFF)) & 0x7FFFFF

    def run(address: int, count: int | None, depth: int) -> None:
        index = 0
        while count is None or index < count:
            w0, w1 = struct.unpack_from(">II", ram, address)
            op = w0 >> 24
            target = resolve(w1)
            in_pool = op in (0x01, 0x03, 0x04, 0x06, 0xFD) and pool[0] <= target < pool[1]
            if in_pool and op == 0x06:
                out.append((depth, address, w0, "pool-dl"))
                run(target, None, depth + 1)
                if (w0 >> 16) & 0xFF:
                    return
            elif in_pool:
                size = 64 if op == 0x01 else (w0 & 0xFFFF if op != 0xFD else 0)
                out.append((depth, address, w0, "pool", payload(target, size)))
            else:
                out.append((depth, address, w0, w1))
            if op == 0xB8 and count is None:
                return
            address += 8
            index += 1
            if index > 1 << 16:
                raise RuntimeError("display list did not end")

    run(start, count, 0)
    return out


def first_divergence(a: list[tuple], b: list[tuple]) -> dict | None:
    for index, (left, right) in enumerate(zip(a, b, strict=False)):
        if left[2:] != right[2:]:
            return {"index": index, "regenerated": [f"{v:08x}" if isinstance(v, int) else v for v in left],
                    "captured": [f"{v:08x}" if isinstance(v, int) else v for v in right]}
    if len(a) != len(b):
        return {"index": min(len(a), len(b)), "length": [len(a), len(b)]}
    return None


def find_geo_start(ram: bytes, main: int, size: int, top: int) -> tuple[int, int]:
    """geo_process_root's first commands: G_MOVEMEM viewport then G_MTX LOAD of the identity,
    the matrix allocated right below the viewport."""
    for address in range(main, main + size - 8, 8):
        w0, w1, m0, m1 = struct.unpack_from(">IIII", ram, address)
        if w0 == 0x03800010 and m0 == 0x01020040 and m1 == w1 - 64:
            return address, top - (w1 + 16)
    raise RuntimeError("geo_process_root output not found in the captured main list")


# --- symbolization ---
def load_map(path: Path, version: str = "us") -> list[tuple[int, str]]:
    """Fill US, CODE_SEGMENTS and REGIONS from the decomp's linker map (read as
    data); returns every (address, symbol) for labels."""
    pattern = re.compile(r"^\s+0x00000000([0-9a-f]{8})\s+([A-Za-z_]\w*)\s*$")
    section = re.compile(r"^\.(main|engine)(\.noload)?\s+0x([0-9a-f]+)\s+0x([0-9a-f]+)\s+load address 0x([0-9a-f]+)")
    by_address, by_name, sections = {}, {}, {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if match := pattern.match(line):
            by_address[int(match[1], 16)] = match[2]
            by_name.setdefault(match[2], int(match[1], 16))
        elif match := section.match(line):
            sections[match[1] + (match[2] or "")] = (int(match[3], 16) & 0xFFFFFFFF, int(match[4], 16),
                                                     int(match[5], 16))
    missing = [name for name in SYMBOLS if name not in by_name] + \
              [name for name in ("main", "main.noload", "engine", "engine.noload") if name not in sections]
    if missing:
        raise SystemExit(f"{path}: the map names none of {missing}")
    US.update({name: by_name[name] for name in SYMBOLS})
    layout = layout_for(version)
    for symbol, field in LAYOUT_ROWS_FOR.items():
        if layout.value(field) not in (None, US[symbol]):
            raise SystemExit(f"{path}: {symbol} {US[symbol]:#x} disagrees with layout.{field}")
    CODE_SEGMENTS[:] = [(name, *sections[name]) for name in ("main", "engine")]
    REGIONS[:] = [(f"{name} image" if not name.endswith("noload") else f"{name.split('.')[0]} bss",
                   sections[name][0], sections[name][0] + sections[name][1])
                  for name in ("main", "main.noload", "engine", "engine.noload")]
    return sorted(by_address.items())


def label(address: int, symbols: list[tuple[int, str]], heap: tuple[int, int] | None) -> str:
    virtual = 0x80000000 | address
    pool = US["gObjectPool"]
    if pool <= virtual < pool + OBJECT_BYTES * OBJECT_COUNT:
        slot, offset = divmod(virtual - pool, OBJECT_BYTES)
        return f"gObjectPool[{slot}]+0x{offset:03x}"
    if heap and heap[0] <= virtual < heap[1]:
        return "display-list heap (freed main-pool space)"
    best = None
    for start, name in symbols:
        if start > virtual:
            break
        if start >= 0x80000000:
            best = (start, name)
    if best and virtual - best[0] < 0x1000 and any(lo <= virtual < hi for _, lo, hi in REGIONS):
        return f"{best[1]}+0x{virtual - best[0]:x}"
    return region_of(virtual)


def region_of(virtual: int) -> str:
    for name, lo, hi in REGIONS:
        if lo <= virtual < hi:
            return name
    pool = US["gObjectPool"]
    if pool <= virtual < pool + OBJECT_BYTES * OBJECT_COUNT:
        return "object pool"
    if US["gGfxPools"] <= virtual < US["gGfxPools"] + 2 * (GFX_POOL_BYTES + 0x50):
        return "gfx pools"
    return "other RDRAM (heap / segments)"


def ranges(mask: np.ndarray) -> list[tuple[int, int]]:
    edges = np.flatnonzero(np.diff(np.concatenate(([0], mask.astype(np.int8), [0]))))
    return [(int(edges[i]), int(edges[i + 1])) for i in range(0, len(edges), 2)]


# --- the experiment ---
class Frame:
    def __init__(self, directory: Path):
        self.dir = directory
        self.meta = json.loads((directory / "frame.json").read_text(encoding="utf-8"))
        self.ram = be_from_pj64((directory / "rdram.bin").read_bytes())
        self.dmem = (directory / "dmem.bin").read_bytes()

    def word(self, address: int) -> int:
        return struct.unpack_from(">I", self.ram, phys(address))[0]


def run_geo(frame: Frame, rom: bytes, fcr31: int, head: int, end: int, poison: list[tuple[int, int]],
            camera: tuple[list[float], list[float]] | None, ram: bytes | None = None) -> tuple[Sandbox, dict]:
    sandbox = Sandbox(ram or frame.ram, rom, fcr31)
    for lo, hi in poison:
        sandbox.uc.mem_write(phys(lo), b"\xA5" * (hi - lo))
    if camera:
        lakitu = US["gLakituState"]
        sandbox.put_f32s(lakitu + LAKITU_FOCUS, camera[0])
        sandbox.put_f32s(lakitu + LAKITU_POS, camera[1])
    sandbox.put_u32(US["gDisplayListHead"], head)
    sandbox.put_u32(US["gGfxPoolEnd"], end)
    root = frame.word(frame.word(US["gCurrentArea"]) + 4)
    args = (root, frame.word(US["D_8032CE74"]), frame.word(US["D_8032CE78"]), frame.word(US["gFBSetColor"]))
    sandbox.call(US["geo_process_root"], *args)
    origins: dict[str, int] = {}
    for origin in sandbox.verified.values():
        origins[str(origin)] = origins.get(str(origin), 0) + 1
    result = {"args": [f"{value:08x}" for value in args], "violations": sandbox.violations,
              "level": frame.word(US["gCurrLevelNum"]) >> 16, "code_blocks_by_rom_origin": origins,
              "instructions": sandbox.instructions, "head": sandbox.u32(US["gDisplayListHead"]),
              "end": sandbox.u32(US["gGfxPoolEnd"])}
    return sandbox, result


def access_report(sandbox: Sandbox, before: bytes, symbols, output: list[tuple[int, int]]) -> dict:
    after = sandbox.ram()
    first = np.frombuffer(bytes(sandbox.first), np.uint8)
    code = np.frombuffer(bytes(sandbox.code), np.uint8).astype(bool)
    inputs = first == 1
    heap_at = struct.unpack_from(">I", after, phys(US["gDisplayListHeap"]))[0]
    heap_total = struct.unpack_from(">i", after, phys(heap_at))[0] if heap_at else 0
    heap = (heap_at - 16, heap_at + 16 + max(heap_total, 0)) if heap_at else None
    out_mask = np.zeros(RDRAM_BYTES, bool)
    for lo, hi in output:
        out_mask[phys(lo):phys(hi)] = True
    changed = (np.frombuffer(after, np.uint8) != np.frombuffer(before, np.uint8)) & ~out_mask
    written = np.frombuffer(bytes(sandbox.wrote), np.uint8).astype(bool) & ~out_mask
    side_effects = []
    for lo, hi in ranges(written):
        delta = int(changed[lo:hi].sum())
        side_effects.append({"range": f"{0x80000000 | lo:08x}-{0x80000000 | hi:08x}", "bytes": hi - lo,
                             "changed": delta, "label": label(lo, symbols, heap)})
    input_ranges = [{"range": f"{0x80000000 | lo:08x}-{0x80000000 | hi:08x}", "bytes": hi - lo,
                     "label": label(lo, symbols, heap)} for lo, hi in ranges(inputs)]
    return {"code_bytes": int(code.sum()), **input_summary(inputs, before, sandbox.rom),
            "written_bytes_outside_output": int(written.sum()), "changed_bytes_outside_output": int(changed.sum()),
            "display_list_heap": [f"{heap[0]:08x}", f"{heap[1]:08x}"] if heap else None,
            "side_effects": side_effects, "inputs": input_ranges}


def input_summary(inputs: np.ndarray, before: bytes, rom: bytes) -> dict:
    """Bytes read before any write, by region; ROM-identical bytes inside the code images are
    immutable, everything else is game state the run consumed."""
    by_region: dict[str, int] = {}
    for lo, hi in ranges(inputs):
        name = region_of(0x80000000 | lo)
        by_region[name] = by_region.get(name, 0) + hi - lo
    rom_identical = 0
    for _, start, length, rom_offset in CODE_SEGMENTS:
        lo, hi = phys(start), phys(start) + length
        same = np.frombuffer(before[lo:hi], np.uint8) == np.frombuffer(rom[rom_offset:rom_offset + length], np.uint8)
        rom_identical += int((inputs[lo:hi] & same).sum())
    pool = phys(US["gObjectPool"])
    return {"input_bytes": int(inputs.sum()), "input_bytes_rom_identical_in_code_images": rom_identical,
            "input_bytes_object_pool": int(inputs[pool:pool + OBJECT_BYTES * OBJECT_COUNT].sum()),
            "input_bytes_by_region": by_region}


def state_split(frame: "Frame", rom: bytes, inputs: np.ndarray) -> dict:
    """Split a read set into what the ROM already holds and what a recording must carry.
    ROM-backed: code-image bytes equal to the ROM, and segment bytes equal to the level
    script's decoded load at the frame's RSP segment base (provenance.py, E4)."""
    import provenance
    backed = np.zeros(RDRAM_BYTES, bool)
    ram = np.frombuffer(frame.ram, np.uint8)
    for _, start, length, rom_offset in CODE_SEGMENTS:
        lo = phys(start)
        backed[lo:lo + length] = ram[lo:lo + length] == np.frombuffer(rom[rom_offset:rom_offset + length], np.uint8)
    loads = provenance.segment_loads(rom)
    dl = struct.unpack_from("<I", frame.dmem, 0xFF0)[0]
    attributed = []
    for segment, base in sorted(provenance.rsp_segments(pj64_from_be(frame.ram), dl).items()):
        best = None
        for load in loads:
            size = len(load["data"]) & ~3
            if load["segment"] not in (segment, None) or not size or base + size > RDRAM_BYTES:
                continue
            if frame.ram[base:base + 64] != load["data"][:64]:
                continue
            same = ram[base:base + size] == np.frombuffer(load["data"][:size], np.uint8)
            if best is None or (~same).sum() < (~best[1]).sum():
                best = (load, same)
        if best:
            backed[base:base + len(best[1])] |= best[1]
            attributed.append(f"{segment:#x}@{base:06x}<-rom {best[0]['start']:#x}")
    pool = phys(US["gObjectPool"])
    objects = np.zeros(RDRAM_BYTES, bool)
    objects[pool:pool + OBJECT_BYTES * OBJECT_COUNT] = True
    carried = inputs & ~backed
    by_region = {name: int(carried[phys(lo):phys(hi)].sum()) for name, lo, hi in REGIONS}
    by_region["of which object pool"] = int((carried & objects).sum())
    by_region["heap and segments"] = int(carried.sum()) - sum(int(carried[phys(lo):phys(hi)].sum())
                                                              for _, lo, hi in REGIONS)
    return {"segments_attributed": attributed, "rom_backed": int((inputs & backed).sum()),
            "carried": int(carried.sum()), "carried_by_region": by_region}


def culling_report(sandbox: Sandbox, ram: bytes, symbols) -> dict[int, dict]:
    names = dict(symbols)
    segments = segment_table(ram)
    report = {}
    for node, visible in sandbox.culling:
        behavior = struct.unpack_from(">I", ram, phys(node + OBJ_BEHAVIOR))[0]
        segmented = None
        if segments[0x13] and phys(behavior) >= segments[0x13]:
            segmented = 0x13000000 + phys(behavior) - segments[0x13]
        pool = US["gObjectPool"]
        slot = (node - pool) // OBJECT_BYTES if pool <= node < pool + OBJECT_BYTES * OBJECT_COUNT else None
        report[node] = {"slot": slot, "behavior": names.get(segmented, f"{behavior:08x}"), "visible": bool(visible)}
    return report


def write_frame(out: Path, template: Path, ram: bytes, data_ptr: int, data_size: int, note: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "rdram.bin").write_bytes(pj64_from_be(ram))
    dmem = bytearray((template / "dmem.bin").read_bytes())
    struct.pack_into("<2I", dmem, 0xFF0, phys(data_ptr), data_size)
    (out / "dmem.bin").write_bytes(bytes(dmem))
    meta = json.loads((template / "frame.json").read_text(encoding="utf-8"))
    meta["task"]["data_ptr"] = data_ptr
    meta["task"]["data_size"] = data_size
    meta["source"]["regenerated"] = note
    (out / "frame.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


def orbit_camera(focus: list[float], source_pos: list[float], yaw: float, pitch: float, dist: float):
    base = math.atan2(source_pos[0] - focus[0], source_pos[2] - focus[2])
    angle = base + math.radians(yaw)
    p = math.radians(pitch)
    pos = [focus[0] + dist * math.cos(p) * math.sin(angle), focus[1] + dist * math.sin(p),
           focus[2] + dist * math.cos(p) * math.cos(angle)]
    return focus, pos


class Capture:
    """The savestate frame, its pool and where geo_process_root's output sits in it."""

    def __init__(self, args):
        self.frame = frame = Frame(args.frame)
        self.rom = args.rom.read_bytes()
        if be_from_pj64(bytes.fromhex(frame.meta["rom_header"])) != self.rom[:0x40]:
            raise SystemExit("the frame's ROM header does not match --rom")
        self.symbols = load_map(args.map)
        self.fcr31 = args.fcr31
        self.pool = frame.word(US["gGfxPool"])
        self.top = self.pool + GFX_POOL_BYTES
        task = int(frame.meta["source"]["task_address"], 16)
        if task != self.top:
            raise SystemExit(f"frame task {task:08x} is not gGfxPool's ({self.top:08x}): RAM rendered the other pool")
        self.main, self.main_size = frame.meta["task"]["data_ptr"], frame.meta["task"]["data_size"]
        if self.main != self.pool or frame.word(US["gDisplayListHead"]) != self.pool + self.main_size:
            raise SystemExit("the game thread is not parked after this pool's render")
        self.low_water = frame.word(US["gGfxPoolEnd"])
        geo_start, self.pre = find_geo_start(frame.ram, phys(self.main), self.main_size, phys(self.top))
        self.geo_start = geo_start | 0x80000000
        self.alloc_top = self.top - self.pre
        # Free middle of the pool: relocated output goes here, below nothing the capture uses.
        self.scratch_head = (self.pool + self.main_size + 15) & ~7

    def run(self, head: int, end: int, poison=(), camera=None, ram=None) -> tuple[Sandbox, dict]:
        return run_geo(self.frame, self.rom, self.fcr31, head, end, list(poison), camera, ram)

    def canonical(self, ram: bytes, start: int, commands: int, written: np.ndarray) -> list[tuple]:
        return walk(ram, phys(start), commands, (phys(self.pool), phys(self.top)), segment_table(self.frame.ram),
                    written)


def source_camera(capture: Capture, report: dict, out: Path) -> tuple[Sandbox, np.ndarray, dict] | None:
    """In place over poisoned output, then relocated: both must reproduce the capture."""
    frame = capture.frame
    poison = [(capture.geo_start, capture.pool + capture.main_size), (capture.low_water, capture.alloc_top)]
    sandbox, run = capture.run(capture.geo_start, capture.alloc_top, poison)
    report["source_camera"] = run
    if run["violations"]:
        return None
    head, end = run["head"], run["end"]
    # Bytes the game never writes (Vp padding, ...) hold whatever the pool held before: compare
    # what the run wrote, keep the capture's bytes elsewhere, and count the unwritten ones.
    wrote = np.frombuffer(bytes(sandbox.wrote), np.uint8).astype(bool)
    captured = np.frombuffer(frame.ram, np.uint8)
    regenerated = np.where(wrote, np.frombuffer(sandbox.ram(), np.uint8), captured).tobytes()
    outputs = {"main list": (phys(capture.geo_start), phys(head)), "allocations": (phys(end), phys(capture.alloc_top))}
    differing = {name: int((np.frombuffer(regenerated[lo:hi], np.uint8) != captured[lo:hi]).sum())
                 for name, (lo, hi) in outputs.items()}
    commands = (head - capture.geo_start) // 8
    # Relocated: same run with output in the pool's free middle; only pool addresses differ.
    elsewhere, moved = capture.run(capture.scratch_head, capture.low_water)
    captured_stream = capture.canonical(frame.ram, capture.geo_start, commands, wrote)
    relocated = first_divergence(
        capture.canonical(elsewhere.ram(), capture.scratch_head, (moved["head"] - capture.scratch_head) // 8,
                          np.frombuffer(bytes(elsewhere.wrote), np.uint8).astype(bool)), captured_stream)
    in_place = first_divergence(capture.canonical(regenerated, capture.geo_start, commands, wrote), captured_stream)
    run.update({"head": f"{head:08x}", "end": f"{end:08x}", "commands": commands,
                "canonical_commands_with_sublists": len(captured_stream),
                "allocated": capture.alloc_top - end, "differing_written_bytes": differing,
                "unwritten_output_bytes": {name: int((~wrote[lo:hi]).sum()) for name, (lo, hi) in outputs.items()},
                "in_place_canonical_divergence": in_place,
                "relocated_canonical_divergence": relocated, "relocated_violations": moved["violations"]})
    report["access"] = access_report(sandbox, frame.ram, capture.symbols, poison)
    write_frame(out / "source", frame.dir, regenerated, capture.main, capture.main_size,
                {"camera": "source", "identical": not any(differing.values())})
    print(f"source camera: {commands} main-list commands ({len(captured_stream)} with pool sub-lists), "
          f"{capture.alloc_top - end} bytes allocated, differing written bytes {differing}, "
          f"{run['instructions']} instructions; canonical divergence in place {in_place}, "
          f"relocated {relocated} {moved['violations']}")
    return sandbox, wrote, outputs


def prove_closure(capture: Capture, sandbox: Sandbox, wrote: np.ndarray, outputs: dict) -> dict:
    """Poison every RDRAM byte the run neither executed nor read before writing it. If the
    recorded read set is complete, the output cannot move."""
    captured = np.frombuffer(capture.frame.ram, np.uint8)
    keep = (np.frombuffer(bytes(sandbox.first), np.uint8) == 1) | np.frombuffer(bytes(sandbox.code), np.uint8).astype(bool)
    for _, start, length, _ in sandbox.loaded:   # code-origin checks read the whole ROM image
        keep[phys(start):phys(start) + length] = True
    proof, run = capture.run(capture.geo_start, capture.alloc_top,
                             ram=np.where(keep, captured, np.uint8(0x5A)).tobytes())
    proof_wrote = np.frombuffer(bytes(proof.wrote), np.uint8).astype(bool)
    proof_out = np.frombuffer(proof.ram(), np.uint8)
    first_out = np.frombuffer(sandbox.ram(), np.uint8)
    result = {"kept_bytes": int(keep.sum()), "violations": run["violations"],
              "same_written_set": all(np.array_equal(proof_wrote[lo:hi], wrote[lo:hi]) for lo, hi in outputs.values()),
              "same_output_as_unpoisoned_run": all(np.array_equal(proof_out[lo:hi][wrote[lo:hi]], first_out[lo:hi][wrote[lo:hi]])
                                                   for lo, hi in outputs.values())}
    print(f"closure proof: kept {result['kept_bytes']} bytes, poisoned the rest: {result}")
    return result


def moved_camera(capture: Capture, spec: str, focus_height: float, source_culling: dict, out: Path):
    """Re-run from the same RAM with gLakituState moved; splice the new geo output between the
    captured prologue and HUD and write a frame input (not rendered here)."""
    frame = capture.frame
    lakitu = US["gLakituState"]
    source_pos = list(struct.unpack_from(">3f", frame.ram, phys(lakitu + LAKITU_POS)))
    mario = list(struct.unpack_from(">3f", frame.ram, phys(US["gMarioStates"] + MARIO_POS)))
    yaw, pitch, dist = (float(part) for part in spec.split(","))
    camera = orbit_camera([mario[0], mario[1] + focus_height, mario[2]], source_pos, yaw, pitch, dist)
    moved, run = capture.run(capture.scratch_head, capture.low_water, camera=camera)
    entry = {"yaw": yaw, "pitch": pitch, "dist": dist, "focus": camera[0], "pos": camera[1],
             "violations": run["violations"], "instructions": run["instructions"]}
    if run["violations"]:
        return entry, None
    ram = bytearray(moved.ram())
    spliced = (frame.ram[phys(capture.main):phys(capture.geo_start)]
               + ram[phys(capture.scratch_head):phys(run["head"])]
               + frame.ram[phys(capture.geo_end):phys(capture.main) + capture.main_size])
    at = (run["head"] + 15) & ~7
    if phys(at) + len(spliced) > phys(run["end"]):
        entry["violations"].append("no pool room for the spliced list")
        return entry, moved
    ram[phys(at):phys(at) + len(spliced)] = spliced
    culling = culling_report(moved, frame.ram, capture.symbols)
    entry.update({"commands": (run["head"] - capture.scratch_head) // 8, "allocated": capture.low_water - run["end"],
                  "visible": sum(value["visible"] for value in culling.values()),
                  "newly_visible": [value for node, value in culling.items()
                                    if value["visible"] and not source_culling.get(node, {}).get("visible")],
                  "newly_hidden": [value for node, value in culling.items()
                                   if not value["visible"] and source_culling.get(node, {}).get("visible")],
                  "frame": f"orbit_{int(yaw)}_{int(pitch)}_{int(dist)}"})
    write_frame(out / entry["frame"], frame.dir, bytes(ram), at, len(spliced),
                {"camera": "orbit", "yaw": yaw, "pitch": pitch, "dist": dist})
    return entry, moved


def regen(args) -> int:
    capture = Capture(args)
    frame = capture.frame
    report = {"frame": frame.dir.name, "pool": f"{capture.pool:08x}", "global_timer": frame.word(US["gGlobalTimer"]),
              "area_update_counter": frame.word(US["gAreaUpdateCounter"]) >> 16,
              "geo_start": f"{capture.geo_start:08x}", "allocations_before_geo": capture.pre,
              "fcr31": f"{capture.fcr31:08x}"}
    args.out.mkdir(parents=True, exist_ok=True)
    source = source_camera(capture, report, args.out)
    if source is None:
        print(json.dumps(report, indent=2))
        return 1
    sandbox, wrote, outputs = source
    capture.geo_end = capture.geo_start + report["source_camera"]["commands"] * 8
    report["closure_proof"] = prove_closure(capture, sandbox, wrote, outputs)
    source_culling = culling_report(sandbox, frame.ram, capture.symbols)
    report["objects"] = {"processed": len(source_culling),
                         "visible": sum(entry["visible"] for entry in source_culling.values())}
    inputs = np.frombuffer(bytes(sandbox.first), np.uint8) == 1
    union_inputs, union_code = inputs.copy(), np.frombuffer(bytes(sandbox.code), np.uint8).astype(bool)
    report["orbits"] = []
    for spec in args.orbit:
        entry, moved = moved_camera(capture, spec, args.focus_height, source_culling, args.out)
        if moved is not None:
            union_inputs |= np.frombuffer(bytes(moved.first), np.uint8) == 1
            union_code |= np.frombuffer(bytes(moved.code), np.uint8).astype(bool)
        report["orbits"].append(entry)
        print(f"orbit {spec}: {entry.get('commands')} commands, visible {entry.get('visible')} "
              f"(+{len(entry.get('newly_visible', []))} -{len(entry.get('newly_hidden', []))}) "
              f"{entry['violations']} -> {entry.get('frame')}")
    report["union_over_cameras"] = {"code_bytes": int(union_code.sum()), **input_summary(union_inputs, frame.ram, capture.rom)}
    report["state_split"] = {"source_camera": state_split(frame, capture.rom, inputs),
                             "union_over_cameras": state_split(frame, capture.rom, union_inputs)}
    for name in ("source_camera", "union_over_cameras"):
        split = dict(report["state_split"][name])
        split.pop("segments_attributed")
        print(f"read set ({name}): {split}")
    (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if not any(report["source_camera"]["differing_written_bytes"].values()) else 2


class TapFrame(Frame):
    """One tap-session task as a frame: RDRAM rebuilt from what the session holds."""

    def __init__(self, session: Path, task, ram_be: bytes):
        self.dir = session
        self.ram = ram_be
        self.dmem = b""
        self.meta = {"task": {"data_ptr": task.task[12] | 0x80000000, "data_size": task.task[13]}}
        self.task_words = task.task


def graphics_not_captured(frame: TapFrame, ram: bytearray, known: np.ndarray, spliced: bytes, at: int) -> list:
    """Bytes the moved-camera task makes the RENDERER read that the session lacks: the real
    closure walker (closure_probe.cpp, scene_closure.h) over the spliced list, read by read
    (its trace), not by its 32-byte granules, which round into unwritten pool memory."""
    import closure
    with tempfile.TemporaryDirectory(prefix="e2-orbit-") as folder:
        folder = Path(folder)
        (folder / "rdram.bin").write_bytes(pj64_from_be(bytes(ram)))
        dmem = bytearray(0x1000)
        struct.pack_into("<16I", dmem, 0xFC0, *frame.task_words)
        struct.pack_into("<2I", dmem, 0xFF0, phys(at), len(spliced))
        (folder / "dmem.bin").write_bytes(bytes(dmem))
        os.environ["SCENE_LAB_TRACE"] = str(folder / "reads.txt")
        try:
            closure.probe(closure.build(), folder, 1)
        finally:
            del os.environ["SCENE_LAB_TRACE"]
        reads = (folder / "reads.txt").read_text(encoding="ascii").split()
    missing = np.zeros(RDRAM_BYTES, bool)
    for begin, size in zip(reads[1::3], reads[2::3], strict=True):
        begin, size = int(begin, 16), int(size)
        missing[begin:begin + size] = ~known[begin:begin + size]
    return ranges(missing)


def check_orbits(frame: TapFrame, rom: bytes, fcr31: int, known: np.ndarray, orbits: list[str],
                 scratch_head: int, low_water: int, geo: tuple[int, int], sink=None) -> list[dict]:
    """A moved camera from the same captured bytes: no output to compare against, so the
    question is coverage: does the game's renderer read anything the tap did not capture, and
    does the list it makes send the RSP anywhere the session holds no bytes?"""
    source_pos = list(struct.unpack_from(">3f", frame.ram, phys(US["gLakituState"] + LAKITU_POS)))
    mario = list(struct.unpack_from(">3f", frame.ram, phys(US["gMarioStates"] + MARIO_POS)))
    checked = []
    for spec in orbits:
        yaw, pitch, dist = (float(part) for part in spec.split(","))
        camera = orbit_camera([mario[0], mario[1] + 120.0, mario[2]], source_pos, yaw, pitch, dist)
        moved, run = run_geo(frame, rom, fcr31, scratch_head, low_water, [], camera)
        outside = (np.frombuffer(bytes(moved.first), np.uint8) == 1) & ~known
        entry = {"orbit": spec, "violations": run["violations"], "commands": (run["head"] - scratch_head) // 8,
                 "objects_visible": sum(1 for _, visible in moved.culling if visible),
                 "read_bytes_not_captured": int(outside.sum())}
        if not run["violations"]:
            main, size = frame.meta["task"]["data_ptr"], frame.meta["task"]["data_size"]
            ram = bytearray(moved.ram())
            spliced = (frame.ram[phys(main):phys(geo[0])] + ram[phys(scratch_head):phys(run["head"])]
                       + frame.ram[phys(geo[1]):phys(main) + size])
            at = (run["head"] + 15) & ~7
            if phys(at) + len(spliced) <= phys(run["end"]):
                ram[phys(at):phys(at) + len(spliced)] = spliced
                # The run's own output, slack included: a buffer a callback allocated and did not
                # fill holds stale pool memory in the live game too, and the walker's peek past an
                # ENDDL reads it (never used). Plus the peek past the spliced list's end.
                made = known.copy()
                made[phys(scratch_head):phys(run["head"])] = True
                made[phys(run["end"]):phys(low_water)] = True
                made[phys(at):phys(at) + len(spliced) + 8] = True
                missing = graphics_not_captured(frame, ram, made, spliced, at)
                entry["graphics_bytes_not_captured"] = sum(hi - lo for lo, hi in missing)
                entry["graphics_not_captured"] = [f"{0x80000000 | lo:08x}-{0x80000000 | hi:08x}"
                                                  for lo, hi in missing[:8]]
                if sink:
                    entry["frame"] = sink(f"orbit_{int(yaw)}_{int(pitch)}_{int(dist)}", bytes(ram), at, len(spliced))
            else:
                entry["violations"] = ["no pool room for the spliced list"]
        checked.append(entry)
    return checked


THROW_MATCH_UNITS = 64.0   # a floor-aligned matrix sits where its object was before this frame's move


def world_matrix(ram: bytes, address: int) -> np.ndarray | None:
    """A float Mat4 that can place an object: affine, rows of plausible length."""
    m = np.array(struct.unpack_from(">16f", ram, phys(address))).reshape(4, 4)
    if not np.isfinite(m).all() or m[3, 3] != 1.0 or m[:3, 3].any():
        return None
    lengths = np.linalg.norm(m[:3, :3], axis=1)
    return m if ((lengths > 0.5) & (lengths < 2.0)).all() else None


def restore_throw_matrices(frame: TapFrame, known: np.ndarray, update_span: tuple[int, int]) -> list[tuple]:
    """Put back the throwMatrix pointers the original render consumed.

    The update can draw an object through a world matrix: obj_orient_graph's floor-aligned Mat4,
    allocated in the gfx pool before geo_process_root (bob-ombs and other walkers on object_step),
    or sFloorAlignMatrix (Mario on a slide). geo_process_object reads it, then sets throwMatrix to
    NULL, so the capture holds the matrix but not the pointer. Each captured candidate goes to the
    active object it places (nearest within THROW_MATCH_UNITS); the byte comparison then judges.
    """
    candidates = [(at, "update allocation") for at in range(update_span[0], update_span[1] - 63, 8)
                  if known[phys(at):phys(at) + 64].all()]
    candidates += [(US["sFloorAlignMatrix"] + 64 * k, f"sFloorAlignMatrix[{k}]") for k in (0, 1)]
    objects = []
    for slot in range(OBJECT_COUNT):
        obj = US["gObjectPool"] + slot * OBJECT_BYTES
        if struct.unpack_from(">h", frame.ram, phys(obj + 0x74))[0] & 1:
            objects.append((obj, np.array(struct.unpack_from(">3f", frame.ram, phys(obj + 0x20)))))
    restored, taken = [], set()
    for address, source in candidates:
        matrix = world_matrix(frame.ram, address) if known[phys(address):phys(address) + 64].all() else None
        if matrix is None:
            continue
        near = [(float(np.linalg.norm(pos - matrix[3, :3])), obj) for obj, pos in objects if obj not in taken]
        distance, obj = min(near, default=(None, None))
        if obj is not None and distance <= THROW_MATCH_UNITS:
            taken.add(obj)
            restored.append((obj, address, source))
    return restored


def with_throw_matrices(frame: TapFrame, pointers: list[tuple]) -> TapFrame:
    ram = bytearray(frame.ram)
    for obj, address, _ in pointers:
        struct.pack_into(">I", ram, phys(obj + 0x50), address)
    return TapFrame(frame.dir, type("Task", (), {"task": frame.task_words})(), bytes(ram))


def regenerate(frame: TapFrame, rom: bytes, fcr31: int, known: np.ndarray, layout: dict) -> dict:
    """One in-place regeneration of the task's geo output, compared with the capture."""
    geo_start, top, pre, low_water = layout["geo_start"], layout["top"], layout["pre"], layout["low_water"]
    main, size = frame.meta["task"]["data_ptr"], frame.meta["task"]["data_size"]
    sandbox, run = run_geo(frame, rom, fcr31, geo_start, top - pre, [(geo_start, main + size), (low_water, top - pre)],
                           None)
    outside = (np.frombuffer(bytes(sandbox.first), np.uint8) == 1) & ~known
    result = {"sandbox": sandbox, "run": run, "outside": outside, "differing": {}}
    if run["violations"]:
        return result
    wrote = np.frombuffer(bytes(sandbox.wrote), np.uint8).astype(bool)
    out, captured = np.frombuffer(sandbox.ram(), np.uint8), np.frombuffer(frame.ram, np.uint8)
    outputs = {"main list": (phys(geo_start), phys(run["head"])), "allocations": (phys(run["end"]), phys(top - pre))}
    result.update({
        "wrote": wrote, "out": out, "captured": captured,
        "differing": {name: int((wrote[lo:hi] & known[lo:hi] & (out[lo:hi] != captured[lo:hi])).sum())
                      for name, (lo, hi) in outputs.items()},
        # Output the RSP never reads (a culled part's matrix) is not in the closure, and closure
        # bytes the game never writes (Vp padding) are stale pool memory: counted, not compared.
        "written_but_not_captured": {name: int((wrote[lo:hi] & ~known[lo:hi]).sum())
                                     for name, (lo, hi) in outputs.items()},
        "captured_but_not_written": {name: int((~wrote[lo:hi] & known[lo:hi]).sum())
                                     for name, (lo, hi) in outputs.items()}})
    return result


def check_task(frame: TapFrame, rom: bytes, fcr31: int, known: np.ndarray, symbols,
               orbits: list[str] = (), sink=None) -> dict:
    """Regenerate one captured task's geo output in place and compare every byte the game wrote
    with what the tap captured; list every byte the renderer read that the tap did not capture."""
    main, size = frame.meta["task"]["data_ptr"], frame.meta["task"]["data_size"]
    pool = frame.word(US["gGfxPool"])
    entry = {"global_timer": frame.word(US["gGlobalTimer"]), "pool": f"{pool:08x}", "data_ptr": f"{main:08x}"}
    if pool != main or frame.word(US["gDisplayListHead"]) != main + size:
        return {**entry, "status": "not parked after this pool's render"}
    top, low_water = pool + GFX_POOL_BYTES, frame.word(US["gGfxPoolEnd"])
    try:
        geo_start, pre = find_geo_start(frame.ram, phys(main), size, phys(top))
    except RuntimeError as error:
        return {**entry, "status": str(error)}
    layout = {"geo_start": geo_start | 0x80000000, "top": top, "pre": pre, "low_water": low_water}
    entry["update_allocations"] = {"bytes": pre, "captured": int(known[phys(top - pre):phys(top)].sum())}
    result = regenerate(frame, rom, fcr31, known, layout)
    # The source capture decides each throwMatrix the render consumed: keep a proposed pointer
    # only when it brings the regenerated list closer to the captured one.
    chosen = []
    for proposal in restore_throw_matrices(frame, known, (top - pre, top)):
        if not result["run"]["violations"] and not any(result["differing"].values()):
            break
        trial_frame = with_throw_matrices(frame, chosen + [proposal])
        trial = regenerate(trial_frame, rom, fcr31, known, layout)
        if not trial["run"]["violations"] and sum(trial["differing"].values()) < sum(result["differing"].values()):
            chosen.append(proposal)
            result = trial
    frame = with_throw_matrices(frame, chosen) if chosen else frame
    entry["throw_matrices_restored"] = [
        {"slot": (obj - US["gObjectPool"]) // OBJECT_BYTES, "from": f"{address:08x} {source}"}
        for obj, address, source in chosen]
    run, outside = result["run"], result["outside"]
    entry.update({"level": run["level"], "instructions": run["instructions"], "violations": run["violations"],
                  "read_bytes_not_captured": int(outside.sum()),
                  "reads_not_captured": [{"range": f"{0x80000000 | lo:08x}-{0x80000000 | hi:08x}",
                                          "label": label(lo, symbols, None)} for lo, hi in ranges(outside)][:20]})
    if run["violations"]:
        return {**entry, "status": "sandbox violation"}
    geo_start, commands = layout["geo_start"], (run["head"] - layout["geo_start"]) // 8
    entry.update({"commands": commands, "differing_written_bytes": result["differing"],
                  "written_but_not_captured": result["written_but_not_captured"],
                  "captured_but_not_written": result["captured_but_not_written"]})
    if any(result["differing"].values()):
        segments = segment_table(frame.ram)
        regenerated = np.where(result["wrote"], result["out"], result["captured"]).tobytes()
        entry["first_divergence"] = first_divergence(
            walk(regenerated, phys(geo_start), commands, (phys(pool), phys(top)), segments, result["wrote"]),
            walk(frame.ram, phys(geo_start), commands, (phys(pool), phys(top)), segments, result["wrote"]))
    identical = not any(result["differing"].values()) and not outside.any()
    if orbits:
        entry["orbits"] = check_orbits(frame, rom, fcr31, known, orbits, (main + size + 15) & ~7, low_water,
                                       (geo_start, run["head"]), sink)
        identical = identical and all(not o["violations"] and not o["read_bytes_not_captured"]
                                      and not o.get("graphics_bytes_not_captured") for o in entry["orbits"])
    if sink:
        entry["frame"] = sink("captured", frame.ram, main, size)
    return {**entry, "status": "identical" if identical else "divergent"}


def rom_images(rom: bytes) -> tuple[np.ndarray, np.ndarray]:
    """The ROM's own code images (code, constants, .data's initial values) at their load addresses."""
    image, known = np.zeros(RDRAM_BYTES, np.uint8), np.zeros(RDRAM_BYTES, bool)
    for _, start, length, rom_offset in CODE_SEGMENTS:
        image[phys(start):phys(start) + length] = np.frombuffer(rom[rom_offset:rom_offset + length], np.uint8)
        known[phys(start):phys(start) + length] = True
    return image, known


def frame_sink(args, header: dict, vi: dict, task):
    """Writes one task's frame inputs replay.py renders: what the session holds, the rest poison."""
    def sink(name: str, ram: bytes, data_ptr: int, data_size: int) -> str:
        folder = args.frames / f"task{task.fields['task_index']:05d}-{name}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "rdram.bin").write_bytes(pj64_from_be(ram))
        dmem = bytearray(0x1000)
        struct.pack_into("<16I", dmem, 0xFC0, *task.task)
        struct.pack_into("<2I", dmem, 0xFF0, phys(data_ptr), data_size)
        (folder / "dmem.bin").write_bytes(bytes(dmem))
        (folder / "frame.json").write_text(json.dumps({
            "schema": "scene-lab-frame/1", "vi": vi, "rom_header": header["rom_header"],
            "source": {"kind": "e2-from-tap", "session": args.session.name, "task_sequence": task.sequence,
                       "camera": name}}, indent=2) + "\n", encoding="utf-8")
        return folder.name
    return sink


def session_vi(tap, args) -> dict | None:
    """VI registers for written frames: the session's first picture, else --vi-frame's."""
    if not args.frames:
        return None
    vi = next((r.vi for r in tap.read_records(args.session / "tap.bin") if isinstance(r, tap.Present)), None)
    if vi is None and args.vi_frame:
        vi = json.loads((args.vi_frame / "frame.json").read_text(encoding="utf-8"))["vi"]
    if vi is None:
        raise SystemExit("--frames needs VI registers: the session has no picture; pass --vi-frame")
    return vi


def from_tap(args) -> int:
    """E2-live: every captured task regenerated from the session's bytes and the ROM alone."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import tap
    symbols = load_map(args.map)
    rom = args.rom.read_bytes()
    header = tap.session_header(args.session)
    if be_from_pj64(bytes.fromhex(header["rom_header"])) != rom[:0x40]:
        raise SystemExit("the session's ROM header does not match --rom")
    image, image_known = rom_images(rom)
    vi = session_vi(tap, args)
    fulls: dict[int, bytes] = {}

    def keep_fulls(records):
        for record in records:
            if isinstance(record, tuple) and record[0] == "full":
                fulls[record[1]] = record[2]
            yield record

    results = []
    states = tap.scene_states(keep_fulls(tap.read_records(args.session / "tap.bin")), int(header["rdram_size"]))
    for index, (task, regions, ram, known) in enumerate(states):
        if index % args.every:
            continue
        if args.limit and len(results) >= args.limit:
            break
        entry = {"task_index": task.fields["task_index"], "has_regions": regions is not None}
        full = fulls.pop(task.sequence - 1, None)
        if full is not None:   # the tap's full RDRAM copy, taken at the same instant
            entry["captured_bytes_differing_from_full_copy"] = int(
                (known & (ram != np.frombuffer(full, np.uint8, known.size))).sum())
        ram_be = np.frombuffer(be_from_pj64(ram.tobytes()), np.uint8)   # captured spans are whole words
        ram_be = np.where(image_known & ~known, image, ram_be)
        sink = frame_sink(args, header, vi, task) if args.frames else None
        entry.update(check_task(TapFrame(args.session, task, ram_be.tobytes()), rom, args.fcr31,
                                known | image_known, symbols, args.orbit, sink))
        results.append(entry)
        print(json.dumps({k: v for k, v in entry.items() if k != "reads_not_captured"}))
    statuses: dict[str, int] = {}
    for entry in results:
        statuses[entry["status"]] = statuses.get(entry["status"], 0) + 1
    summary = {"session": str(args.session), "tasks_checked": len(results), "statuses": statuses}
    print(json.dumps(summary))
    if args.out:
        args.out.write_text(json.dumps({"summary": summary, "tasks": results}, indent=2) + "\n", encoding="utf-8")
    return 0 if results and statuses.get("identical") == len(results) else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("regen")
    run.add_argument("frame", type=Path)
    run.add_argument("out", type=Path)
    run.add_argument("--rom", type=Path, required=True)
    run.add_argument("--map", type=Path, required=True,
                     help="the decomp's linker map for this version (STROOP MappingUS.map): every address")
    run.add_argument("--orbit", action="append", default=[], help="YAW,PITCH,DIST around Mario")
    run.add_argument("--focus-height", type=float, default=120.0)
    run.add_argument("--fcr31", type=lambda value: int(value, 0), default=0x01000800,
                     help="FPU control (libultra's FS|EV, as the savestates hold)")
    run.set_defaults(handler=regen)
    live = commands.add_parser("from-tap")
    live.add_argument("session", type=Path, help="a scene-tap session folder (tap.py arm, or tap.py offline)")
    live.add_argument("--rom", type=Path, required=True)
    live.add_argument("--map", type=Path, required=True)
    live.add_argument("--every", type=int, default=1, help="check every n-th task")
    live.add_argument("--limit", type=int, default=0)
    live.add_argument("--out", type=Path, help="write the per-task report here (private scratch)")
    live.add_argument("--frames", type=Path, help="write each checked task (captured and orbits) as frame "
                      "inputs replay.py renders: private scratch only")
    live.add_argument("--vi-frame", type=Path, help="VI registers from this frame input when the session "
                      "holds no picture (an offline session)")
    live.add_argument("--orbit", action="append", default=[],
                      help="YAW,PITCH,DIST: also re-run each task from a moved camera and require it "
                           "to read only captured bytes")
    live.add_argument("--fcr31", type=lambda value: int(value, 0), default=0x01000800)
    live.set_defaults(handler=from_tap)
    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
