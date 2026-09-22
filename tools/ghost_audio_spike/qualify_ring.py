"""Offline ROM/map evidence for a future read-only sound-request capture gate.

Does not attach to a process. Printed addresses are candidates, not approved
production memory reads. No ROM bytes or disassembly are committed by this tool.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct

from run import normalize

SYMBOLS = (
    "play_sound", "process_sound_request", "process_all_sound_requests",
    "update_game_sound", "thread4_sound", "sSoundRequests",
    "sNumProcessedSoundRequests", "sSoundRequestCount", "gMarioObject",
)


def decode(word: int) -> str:
    """Small transparent MIPS decoder for this leaf function, not an emulator."""
    op, rs, rt, rd = word >> 26, (word >> 21) & 31, (word >> 16) & 31, (word >> 11) & 31
    immediate = word & 0xffff
    signed = immediate - 0x10000 if immediate & 0x8000 else immediate
    if op == 15:
        return f"lui r{rt}, 0x{immediate:04x}"
    if op in (9, 13):
        return f"{'addiu' if op == 9 else 'ori'} r{rt}, r{rs}, {signed if op == 9 else immediate}"
    if op in (35, 36, 40, 43):
        return f"{ {35: 'lw', 36: 'lbu', 40: 'sb', 43: 'sw'}[op]} r{rt}, {signed}(r{rs})"
    if op == 0 and word & 63 == 0:
        return f"sll r{rd}, r{rt}, {(word >> 6) & 31}"
    if op == 0 and word & 63 == 33:
        return f"addu r{rd}, r{rs}, r{rt}"
    if op == 0 and word & 63 == 8:
        return f"jr r{rs}"
    return f"unhandled 0x{word:08x}"


def inspect(map_path: Path, rom_path: Path) -> dict:
    mapping = map_path.read_text(encoding="utf-8")
    rows = re.findall(r"^\s*0x([0-9a-f]{16})\s+(\S+)\s*$", mapping, re.MULTILINE)
    symbols = {name: int(address, 16) for address, name in rows if name in SYMBOLS}
    missing = set(SYMBOLS) - symbols.keys()
    if missing:
        raise ValueError(f"Map lacks expected symbols: {sorted(missing)}")
    section = re.search(r"^\.main\s+0x([0-9a-f]+)\s+0x([0-9a-f]+) load address 0x([0-9a-f]+)",
                        mapping, re.MULTILINE)
    if section is None:
        raise ValueError("Map lacks explicit .main ROM mapping")
    ram, length, offset = (int(group, 16) for group in section.groups())
    start, end = symbols["play_sound"], symbols["process_sound_request"]
    if not ram <= start < end <= ram + length:
        raise ValueError("play_sound does not lie within mapped .main")
    rom = normalize(rom_path)
    data = rom[offset + start - ram:offset + end - ram]
    words = struct.unpack(">12I", data) if len(data) == 48 else ()
    layout_matches = bool(words and
        tuple(word & 0xffff0000 for word in words[:5]) ==
        (0x3c060000, 0x24c60000, 0x90c20000, 0x3c0f0000, 0x25ef0000) and
        words[2] == 0x90c20000 and
        words[5:] == (0x000270c0, 0x01cf1821, 0x24580001, 0xac640000,
                      0xac650004, 0x03e00008, 0xa0d80000))
    # Confirm the actual instructions' address construction agrees with the map.
    if layout_matches:
        signed = lambda value: value - 0x10000 if value & 0x8000 else value
        layout_matches = (
            ((words[0] & 0xffff) << 16) + signed(words[1] & 0xffff) == symbols["sSoundRequestCount"] and
            ((words[3] & 0xffff) << 16) + signed(words[4] & 0xffff) == symbols["sSoundRequests"])
    return {
        "status": "offline candidates only; no live capture verification",
        "map_sha256": hashlib.sha256(map_path.read_bytes()).hexdigest(),
        "rom_sha256": hashlib.sha256(rom).hexdigest(),
        "symbols": {key: f"0x{value:08x}" for key, value in symbols.items()},
        "play_sound_bytes": len(data),
        "play_sound_sha256": hashlib.sha256(data).hexdigest(),
        "producer_layout_matches_map": layout_matches,
        "play_sound_disassembly": [
            {"address": f"0x{start + index:08x}", "word": f"{word:08x}", "instruction": decode(word)}
            for index in range(0, len(data), 4)
            for word in (struct.unpack_from(">I", data, index)[0],)
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map", type=Path, required=True)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    options = parser.parse_args()
    result = inspect(options.map, options.rom)
    options.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
