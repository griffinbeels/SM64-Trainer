# SPDX-License-Identifier: GPL-2.0-or-later
# Runs in the simulation program (SM64TrainerSim.exe); licenses/README.md lists its GPL-2.0-or-later code.
"""Turn a Project64 1.6 savestate into scene-lab frame inputs (E0 probe, private data).

A savestate holds the whole emulated machine at one VI: RDRAM, DMEM and the VI
registers. SM64 keeps its last two graphics tasks in the two gfx pools, so one
savestate yields up to two complete display lists to walk and render offline,
with no emulator and no live game. Outputs go to a scratch folder the caller
names; RAM images are private and never belong in the repository.

Frame input directory (shared by closure_probe and replay_host):
  rdram.bin   RDRAM exactly as Project64 holds it: little-endian u32 words
              (GFX_INFO.MemoryBswaped), RDRAM size from the savestate.
  dmem.bin    4 KiB SP DMEM with the OSTask at 0xFC0 as osSpTaskLoad leaves it
              (pointers physical except ucode_boot).
  frame.json  VI registers, ROM header bytes, the chosen task and provenance.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from sm64_events.memory.layout import layout_for  # noqa: E402

SAVE_ID = 0x23D8A6C8
# Project64 1.6 Machine_SaveState order: id, rdram size, rom header, timer, pc,
# GPR, FPR, CP0, FPCR, HI, LO, RDRAM/SP/DPC/MI/VI/AI/PI/RI/SI registers, TLB, PIF.
_HEADER_OFFSET = 8
_VI_OFFSET = 1000
VI_NAMES = ("status", "origin", "width", "intr", "v_current", "burst", "v_sync", "h_sync",
            "leap", "h_start", "v_start", "v_burst", "x_scale", "y_scale")
TASK_NAMES = ("type", "flags", "ucode_boot", "ucode_boot_size", "ucode", "ucode_size",
              "ucode_data", "ucode_data_size", "dram_stack", "dram_stack_size", "output_buff",
              "output_buff_size", "data_ptr", "data_size", "yield_data_ptr", "yield_data_size")
# osSpTaskLoad converts these to physical; ucode_boot stays virtual (IMEM boot DMA).
_PHYSICAL = {"ucode", "ucode_data", "dram_stack", "output_buff", "output_buff_size",
             "data_ptr", "yield_data_ptr"}
M_GFXTASK = 1


def read_savestate(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) != 1 or members[0].file_size > 64 << 20:
            raise ValueError("unexpected savestate archive")
        data = archive.read(members[0])
    save_id, rdram_size = struct.unpack_from("<II", data, 0)
    if save_id != SAVE_ID or rdram_size not in (4 << 20, 8 << 20):
        raise ValueError("not a Project64 1.6 savestate")
    head = len(data) - 0x2000 - rdram_size
    if head != 1884:
        raise ValueError(f"unexpected savestate header length {head}")
    return {
        "rom_header": data[_HEADER_OFFSET:_HEADER_OFFSET + 0x40],
        "vi": dict(zip(VI_NAMES, struct.unpack_from("<14I", data, _VI_OFFSET), strict=True)),
        "rdram": data[head:head + rdram_size],
        "dmem": data[head + rdram_size:head + rdram_size + 0x1000],
    }


def graphics_tasks(rdram: bytes) -> list[tuple[int, dict]]:
    """Every OSTask-shaped M_GFXTASK in RDRAM (SM64 keeps one per gfx pool)."""
    words = memoryview(rdram).cast("I")
    found = []
    for index in range(0, len(words) - 16):
        if words[index] == M_GFXTASK and words[index + 5] == 0x1000 and words[index + 7] == 0x800:
            task = dict(zip(TASK_NAMES, words[index:index + 16], strict=True))
            if task["data_ptr"] >> 24 == 0x80 and 0 < task["data_size"] < (1 << 20):
                found.append((0x80000000 | index * 4, task))
    return found


def dmem_for(task: dict, template: bytes) -> bytes:
    dmem = bytearray(template)
    values = [task[name] & 0x1FFFFFFF if name in _PHYSICAL and task[name] else task[name]
              for name in TASK_NAMES]
    struct.pack_into("<16I", dmem, 0xFC0, *values)
    return bytes(dmem)


def write_frames(state_path: Path, out: Path, timer_address: int | None) -> list[Path]:
    state = read_savestate(state_path)
    tasks = graphics_tasks(state["rdram"])
    if not tasks:
        raise ValueError("no graphics task found in RDRAM")
    timer = (struct.unpack_from("<I", state["rdram"], timer_address & 0x7FFFFF)[0]
             if timer_address else None)
    written = []
    for address, task in tasks:
        frame = out / f"{state_path.name.split('.')[0]}-task{address:08x}"
        frame.mkdir(parents=True, exist_ok=True)
        (frame / "rdram.bin").write_bytes(state["rdram"])
        (frame / "dmem.bin").write_bytes(dmem_for(task, state["dmem"]))
        (frame / "frame.json").write_text(json.dumps({
            "schema": "scene-lab-frame/1",
            "source": {"kind": "pj64-1.6-savestate", "name": state_path.name,
                       "task_address": f"{address:08x}", "global_timer": timer},
            "vi": state["vi"],
            "rom_header": state["rom_header"].hex(),
            "task": task,
        }, indent=2) + "\n", encoding="utf-8")
        written.append(frame)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("savestate", type=Path)
    parser.add_argument("out", type=Path, help="private scratch folder for frame inputs")
    parser.add_argument("--timer", type=lambda value: int(value, 0), default=layout_for("us").global_timer,
                        help="gGlobalTimer address for provenance (US default)")
    args = parser.parse_args()
    for frame in write_frames(args.savestate, args.out, args.timer):
        meta = json.loads((frame / "frame.json").read_text(encoding="utf-8"))
        print(frame.name, "data_ptr=%08x size=%d timer=%s" % (
            meta["task"]["data_ptr"], meta["task"]["data_size"], meta["source"]["global_timer"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
