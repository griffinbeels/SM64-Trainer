"""A synthetic scene-tap session for the attempt scene tests: what the live tap records, per task.

    session = write_session(folder, rom, places)   # places: (global_timer, level, area) per task

Each task carries the identity extras the arming tool names (gGlobalTimer, the level and area
numbers), its task regions, a level delta, and a present; a level key opens the session and
another lands wherever the level or area changes, as a level load writes one. The ROM and the
region layout are test_scene_state_package.py's, so no private RAM image or real ROM is involved.
"""
from __future__ import annotations

import importlib.util
import random
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("scene_state_synthetic", ROOT / "tests/test_scene_state_package.py")
state = importlib.util.module_from_spec(spec)
spec.loader.exec_module(state)
pk = state.pk
sys.modules.setdefault("scene_state_synthetic", state)

RDRAM = state.RDRAM
LEVEL = state.LEVEL
TIMER, LEVEL_NUM, AREA_NUM = 0x32D5D4, 0x32DDF8, 0x33BACA     # the US identity addresses (layout.py)
IDENTITY = f"global_timer:{TIMER:x}:4,curr_level:{LEVEL_NUM:x}:2,curr_area:{AREA_NUM:x}:2"
rom = state._rom


def _task(task_index: int, image: bytes) -> bytes:
    data_ptr = 0x208000 if task_index % 2 == 0 else 0x214000
    runs = [(data_ptr, data_ptr + 0x20)]
    extras = [(TIMER, 4), (LEVEL_NUM & ~3, 4), (AREA_NUM & ~3, 4)]
    fields = (0, 1, 1000, 2000, 0, 1, len(extras), 0x20, 0x100000, 1, task_index, 0)
    task = (1, 0, 0x80246000, 208, 0x80247000, 4096, 0x80248000, 2048, 0x200000, 1024,
            0x210000, 0x220000, data_ptr, 8, 0, 0)
    return (struct.pack("<12I", *fields) + struct.pack("<16I", *task)
            + b"".join(struct.pack("<II", *run) for run in runs)
            + b"".join(struct.pack("<II", address, size) for address, size in extras)
            + b"".join(image[b:e] for b, e in runs)
            + b"".join(image[address:address + size] for address, size in extras))


def _record(kind: int, sequence: int, body: bytes, qpc: int) -> bytes:
    size = (32 + len(body) + 31) & ~31
    return struct.pack("<IIIIq8x", pk.tap.MAGIC, kind, size, sequence, qpc) + body + bytes(size - 32 - len(body))


def write_session(folder: Path, rom_bytes: bytes, places: list[tuple[int, int, int]],
                  name: str = "tap-20261026-120000") -> Path:
    """A session of len(places) tasks at 30 per second of QPC time (10 MHz)."""
    rng = random.Random(11)
    ram = bytearray(RDRAM)
    ram[0x330000:0x332000] = rng.randbytes(0x2000)
    ram[LEVEL[0] + 0x8000:sum(LEVEL)] = rng.randbytes(0x8000)
    base = LEVEL[0]
    ram[base:base + state.ROM_LEVEL[1]] = rom_bytes[state.ROM_LEVEL[0]:sum(state.ROM_LEVEL)]
    for data_ptr in (0x208000, 0x214000):
        ram[data_ptr:data_ptr + 8] = struct.pack(">II", 0xB8000000, 0)
    records, sequence = [], 0
    frame_qpc = 10_000_000 // 30

    def add(kind: int, body: bytes, index: int) -> None:
        nonlocal sequence
        records.append(_record(kind, sequence, body, index * frame_qpc + sequence))
        sequence += 1

    add(pk.tap.REC_REGIONS, state._regions(0, 1, base, [LEVEL], pk.swap_words(bytes(ram))), 0)
    previous = None
    for index, (timer, level, area) in enumerate(places):
        ram[0x330000 + 64 * (index % 128):0x330000 + 64 * (index % 128) + 8] = rng.randbytes(8)
        block = LEVEL[0] + 256 * rng.randrange(0x80, 0x100)
        ram[block:block + 4] = rng.randbytes(4)
        ram[TIMER:TIMER + 4] = struct.pack(">I", timer)
        ram[LEVEL_NUM:LEVEL_NUM + 2] = struct.pack(">H", level)
        ram[AREA_NUM:AREA_NUM + 2] = struct.pack(">H", area)
        swapped = pk.swap_words(bytes(ram))
        add(pk.tap.REC_TASK, _task(index, swapped), index)
        add(pk.tap.REC_REGIONS, state._regions(index, 0, base, [(0x330000, 0x2000)], swapped), index)
        loaded = previous is not None and (level, area) != previous
        add(pk.tap.REC_REGIONS, state._regions(index, 1 if loaded else 2, base,
                                               [LEVEL] if loaded else [(block & ~0xFF, 256)], swapped), index)
        add(pk.tap.REC_PRESENT, struct.pack("<14I", *[index] * 14) + struct.pack("<4I", 0, 0, 0, 1), index)
        previous = (level, area)
    session = folder / name
    session.mkdir(parents=True)
    (session / "tap.bin").write_bytes(b"".join(records))
    (session / "session.txt").write_text(
        f"schema=scene-lab-tap/1\nwrapper_build=test\nqpc_frequency=10000000\nrdram_size={RDRAM}\n"
        f"rom_header={pk.rom_header_hex(rom_bytes)}\nregion=330000:8192\nlevel_region={LEVEL[0]:x}:{LEVEL[1]}\n",
        encoding="ascii")
    (session / "armed.txt").write_text(f"# identity={IDENTITY}\n", encoding="ascii")
    return session
