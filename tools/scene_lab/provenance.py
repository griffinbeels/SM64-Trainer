# SPDX-License-Identifier: GPL-2.0-or-later
# Runs in the simulation program (SM64TrainerSim.exe); licenses/README.md lists its GPL-2.0-or-later code.
"""E4 probe: name each closure byte's source by the game's own segment loads.

    uv run python tools/scene_lab/provenance.py <rom.z64> <frame_dir>...

SM64 fills its RSP segments from level-script loads: LOAD_RAW (0x17),
LOAD_MIO0 (0x18) and LOAD_MIO0_TEXTURE (0x1A), each naming segment, ROM
start and ROM end. The frame's own display list states each segment's RAM
base (G_MW_SEGMENT). A segment is attributed to a load only when the decoded
load equals RAM at that base byte-for-byte, so the claim is checkable on the
viewer's machine from its own ROM. Closure bytes then fall into: ROM-backed
(a named load at an offset, still equal to RAM), the frame's gfx pool (per
frame), or raw (everything else: must travel in the package). Reports the
split per frame. Private RAM images stay in scratch; the ROM is only read.
"""
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

import numpy as np

MASK = 0x7FFFFF
GFX_POOLS = ((0x207D00, 0x214550), (0x214550, 0x220DA0))   # US/Usamune gGfxPools, measured
EXPANSION_RAM = 0x400000   # RDRAM's upper 4 MiB: a practice ROM copies its own code here at boot


def mio0(rom: bytes, at: int) -> bytes:
    if rom[at:at + 4] != b"MIO0":
        raise ValueError("not MIO0")
    size, comp, raw = struct.unpack_from(">III", rom, at + 4)
    if size > 4 << 20:
        raise ValueError("MIO0 too large")
    out = bytearray()
    bits_at, ci, ri, bit, word = at + 16, at + comp, at + raw, 0, 0
    while len(out) < size:
        if bit == 0:
            word = struct.unpack_from(">I", rom, bits_at)[0]
            bits_at += 4
            bit = 32
        bit -= 1
        if (word >> bit) & 1:
            out.append(rom[ri])
            ri += 1
        else:
            value = struct.unpack_from(">H", rom, ci)[0]
            ci += 2
            back = (value & 0xFFF) + 1
            for _ in range((value >> 12) + 3):
                out.append(out[-back])
    return bytes(out)


def segment_loads(rom: bytes) -> list[dict]:
    """Every well-formed level-script segment load in the ROM, decoded."""
    loads, seen = [], set()
    for at in range(0, len(rom) - 16, 4):
        kind = rom[at]
        if not (kind in (0x17, 0x18, 0x1A) and rom[at + 1] == 0x0C or kind in (0x00, 0x01) and rom[at + 1] == 0x10):
            continue
        segment, start, end = struct.unpack_from(">HII", rom, at + 2)
        if segment >= 0x20 or not start < end <= len(rom) or end - start > 4 << 20:
            continue
        if (segment, start, end, kind) in seen:
            continue
        try:
            data = mio0(rom, start) if kind in (0x18, 0x1A) else rom[start:end]
        except (ValueError, IndexError, struct.error):
            continue
        seen.add((segment, start, end, kind))
        loads.append({"segment": segment, "start": start, "end": end, "kind": kind, "data": data})
    # Boot-time loads (segment 2's HUD/font bank) are not level-script commands:
    # any MIO0 block in the ROM may back any segment, still verified by content.
    at = rom.find(b"MIO0")
    while at != -1:
        try:
            loads.append({"segment": None, "start": at, "end": at, "kind": 0x18, "data": mio0(rom, at)})
        except (ValueError, IndexError, struct.error):
            pass
        at = rom.find(b"MIO0", at + 4)
    return loads


def big_endian(rdram: bytes, begin: int, end: int) -> bytes:
    words = struct.unpack_from("<%dI" % ((end - begin) // 4), rdram, begin)
    return struct.pack(">%dI" % len(words), *words)


def boot_copied_code(rom: bytes, full_be: bytes | None) -> tuple[int, int, int] | None:
    """The practice ROM's own code image: what it copied from the cartridge into expansion RAM.

    Usamune's act selector calls it during geo processing (its print_course_number hook). A
    full big-endian RDRAM copy locates it: the 64 bytes at the expansion base occur once in the
    ROM, and the image runs while every word still equals the ROM (on Usamune v1.93u up to its
    settings at 0x80417B4C). Returns (virtual start, bytes, ROM offset), or None when there is no
    copy or it matches no single ROM window. A consumer loads these bytes from its own ROM file:
    the copy only proves they were the game's, it never supplies code.
    """
    if full_be is None or len(full_be) < EXPANSION_RAM + 64:
        return None
    anchor = full_be[EXPANSION_RAM:EXPANSION_RAM + 64]
    at = rom.find(anchor)
    if not any(anchor) or at < 0 or at % 4 or rom.find(anchor, at + 1) >= 0:
        return None
    ram = np.frombuffer(full_be, ">u4")[EXPANSION_RAM // 4:]
    image = np.frombuffer(rom, ">u4", (len(rom) - at) // 4, at)
    count = min(len(ram), len(image))
    differ = np.flatnonzero(ram[:count] != image[:count])
    return 0x80000000 | EXPANSION_RAM, 4 * (int(differ[0]) if len(differ) else count), at


def rsp_segments(rdram: bytes, dl: int) -> dict[int, int]:
    """The first value each RSP segment receives in the task (G_MW_SEGMENT)."""
    found, stack = {}, [dl & MASK]
    for _ in range(1 << 20):
        at = stack[-1]
        w0, w1 = struct.unpack_from("<II", rdram, at)
        stack[-1] += 8
        op = w0 >> 24
        if op == 0xBC and (w0 & 0xFF) == 0x06:
            found.setdefault((w0 >> 10) & 0xF, w1 & 0xFFFFFF)
        elif op == 0x06:
            target = (found.get((w1 >> 24) & 0xF, 0) + (w1 & MASK)) & MASK
            if (w0 >> 16) & 0xFF == 0:
                stack.append(target)
            else:
                stack[-1] = target
        elif op == 0xB8:
            stack.pop()
            if not stack:
                break
    return found


def attribute(frame: Path, loads: list[dict]) -> dict:
    rdram = (frame / "rdram.bin").read_bytes()
    dl = struct.unpack_from("<I", (frame / "dmem.bin").read_bytes(), 0xFF0)[0]
    closure = json.loads((frame / "closure.json").read_text(encoding="utf-8"))["ranges"]
    sources = []
    for segment, base in sorted(rsp_segments(rdram, dl).items()):
        best = None
        for load in loads:
            size = len(load["data"]) & ~3
            if load["segment"] not in (segment, None) or not size or base + size > len(rdram):
                continue
            ram = big_endian(rdram, base, base + size)
            # Cheap reject before counting: the first 64 bytes must agree.
            if ram[:64] != load["data"][:64]:
                continue
            mismatched = sum(a != b for a, b in zip(ram, load["data"], strict=False))
            if best is None or mismatched < best[0]:
                best = (mismatched, load, size)
        if best:
            mismatched, load, size = best
            sources.append({"segment": segment, "base": base, "size": size, "rom": [load["start"], load["end"]],
                            "kind": load["kind"], "mismatched_bytes": mismatched, "data": load["data"]})
    rom_backed = pool = raw = 0
    raw_regions: dict[str, int] = {}
    for item in closure:
        for granule in range(item["begin"], item["end"], 32):
            if any(lo <= granule < hi for lo, hi in GFX_POOLS):
                pool += 32
            elif any(s["base"] <= granule and granule + 32 <= s["base"] + s["size"]
                     and big_endian(rdram, granule, granule + 32) == s["data"][granule - s["base"]:granule - s["base"] + 32]
                     for s in sources):
                rom_backed += 32
            else:
                raw += 32
                key = hex(granule >> 16)
                raw_regions[key] = raw_regions.get(key, 0) + 32
    return {"frame": frame.name, "segments_attributed": [
                f"{s['segment']:#x}<-{'mio0' if s['kind'] in (0x18, 0x1A) else 'raw'}@{s['rom'][0]:#x}"
                + (f" ({s['mismatched_bytes']} bytes changed at run time)" if s["mismatched_bytes"] else "")
                for s in sources],
            "rom_backed": rom_backed, "gfx_pool": pool, "raw": raw, "raw_regions": raw_regions}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("rom", type=Path)
    parser.add_argument("frames", type=Path, nargs="+")
    args = parser.parse_args()
    loads = segment_loads(args.rom.read_bytes())
    print(f"{len(loads)} segment loads found in the ROM")
    for frame in args.frames:
        print(json.dumps(attribute(frame, loads)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
