"""E2a probe: re-run SM64's OWN scene-graph renderer offline, under a moved camera.

    uv run --with unicorn python tools/scene_lab/e2_georender.py regen <frame_dir> <out_dir>
        --rom <z64> [--map <MappingUS.map>] [--orbit YAW,PITCH,DIST ...] [--focus-height H]

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
carry (provenance.py's ROM attribution) and per-object culling decisions. RAM
images are private: outputs belong in scratch only.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import struct
from pathlib import Path

import numpy as np
from unicorn import (UC_ARCH_MIPS, UC_HOOK_BLOCK, UC_HOOK_CODE, UC_HOOK_INTR, UC_MODE_BIG_ENDIAN,
                     UC_MODE_MIPS64, Uc, UcError)
from unicorn import mips_const as mips

RDRAM_BYTES = 8 << 20
SCRATCH_PHYS = 0x01000000          # private page outside RDRAM: stack and return sentinel
SCRATCH_BYTES = 1 << 20
SENTINEL = 0x81000000
STACK_TOP = 0x810FFF00
GFX_POOL_BYTES = 6400 * 8
STATUS_CU1 = 0x20000000            # kernel, EXL/ERL/IE clear, FR=0 (libultra threads never set FR)

# SM64 US addresses: n64decomp/sm64 US build map (STROOP MappingUS.map).
US = {
    "geo_process_root": 0x8027E130, "geo_process_object": 0x8027D6FC, "geo_process_held_object": 0x8027DA84,
    "obj_is_in_view": 0x8027D518, "gGlobalTimer": 0x8032D5D4, "gCurrentArea": 0x8032DDCC,
    "D_8032CE74": 0x8032DDD4, "D_8032CE78": 0x8032DDD8, "gFBSetColor": 0x8032DDE0,
    "gAreaUpdateCounter": 0x8032DF08, "gDisplayListHead": 0x8033B06C, "gGfxPoolEnd": 0x8033B070,
    "gGfxPool": 0x8033B074, "gMarioStates": 0x8033B170, "sSegmentTable": 0x8033B400,
    "gDisplayListHeap": 0x8033C388, "gLakituState": 0x8033C698, "gObjectPool": 0x8033D488,
}
# ROM-loaded code images (US layout): (virtual start, size, ROM offset).
CODE_SEGMENTS = (("main", 0x80246000, 0xF4580, 0x1000), ("engine", 0x80378800, 0x13490, 0xF5580))
REGIONS = (  # read/write-set classification, US layout
    ("main image", 0x80246000, 0x8033A580), ("main bss", 0x8033A580, 0x80367460),
    ("engine image", 0x80378800, 0x8038BC90), ("engine bss", 0x8038BC90, 0x8038EEF0),
)
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
def load_map(path: Path | None) -> list[tuple[int, str]]:
    if not path:
        return []
    pattern = re.compile(r"^\s+0x00000000([0-9a-f]{8})\s+([A-Za-z_]\w*)\s*$")
    symbols = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = pattern.match(line)
        if match:
            symbols[int(match[1], 16)] = match[2]
    return sorted(symbols.items())


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
    if 0x80207D00 <= virtual < 0x80207D00 + 2 * (GFX_POOL_BYTES + 0x50):
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
              "level": frame.word(0x8032DDF8) >> 16, "code_blocks_by_rom_origin": origins,
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("regen")
    run.add_argument("frame", type=Path)
    run.add_argument("out", type=Path)
    run.add_argument("--rom", type=Path, required=True)
    run.add_argument("--map", type=Path, help="US linker map (STROOP MappingUS.map) to label addresses")
    run.add_argument("--orbit", action="append", default=[], help="YAW,PITCH,DIST around Mario")
    run.add_argument("--focus-height", type=float, default=120.0)
    run.add_argument("--fcr31", type=lambda value: int(value, 0), default=0x01000800,
                     help="FPU control (libultra's FS|EV, as the savestates hold)")
    run.set_defaults(handler=regen)
    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
