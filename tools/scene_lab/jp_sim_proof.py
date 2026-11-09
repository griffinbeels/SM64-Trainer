# SPDX-License-Identifier: GPL-2.0-or-later
# Runs in the simulation program (SM64TrainerSim.exe); licenses/README.md lists its GPL-2.0-or-later code.
"""JP re-simulation, proved on the CPU (P15). Nothing here touches Project64, a running game or his lab folder.

    uv run --with unicorn==2.1.4 python tools/scene_lab/jp_sim_proof.py <command> --roms <his roms folder> --cache DIR
        builds                      both cold boots: each SimBuild, the stack check, the JP map's missing names
        equivalence --version V     stage_sim.Machine against the cold boot's own thread 5, frame by frame
            [--level L --area A --act N --frames F] [--death] [--mutate resume|vsync_sp|script_slot|usamune_vi_count]
        warm --version V            every stage the server warms (scene_warm.rest): time and disk
        corpus --version V --clips DIR [--out FILE]   every padded ghost from that cartridge's simulated reset
        package --version V         a stage scene through the real producer, read back: its spans are the build's
        savestate --version V --clip FILE              a ghost re-simulated, a save state from its scene, loaded

Run it with LOCALAPPDATA pointed at a scratch folder holding refs/Mapping{US,JP}.map copies (cold_boot reads the
maps from the lab root's refs): the producer's host then builds into that folder too, never into his.

Equivalence is the proof that the synthetic resume (SimBuild's frames, written by enter_resume) is the game's own
thread 5: the cold boot carries every register from the real boot, the Machine rebuilds only the stack frames
SimBuild names, and both take the same pads (and the same two VIs a frame). Outside thread 5's stack their RAM must
match byte for byte, through a death and the level load after it. A mutated SimBuild must make it fail.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import struct
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cold_boot  # noqa: E402
import frame_sim as sim  # noqa: E402
import stage_sim  # noqa: E402

from sm64_events.memory.addresses import MARIO_HEALTH_OFF  # noqa: E402

HEADER = {"us": b"SM64 USAMUNE v1.93u", "jp": b"SM64 USAMUNE v1.93j"}
MUTATIONS = {"resume": -4, "vsync_sp": 8, "script_slot": 4, "usamune_vi_count": 4}


def rom_of(roms: Path, version: str) -> Path:
    for path in sorted(roms.iterdir()):
        if path.suffix.lower() == ".z64" and path.read_bytes()[0x20:0x20 + len(HEADER[version])] == HEADER[version]:
            return path
    raise SystemExit(f"no Usamune {version.upper()} ROM in {roms}")


def stages_for(args, version: str) -> stage_sim.Stages:
    map_path = cold_boot.map_path(version)
    return stage_sim.Stages(args.cache, args.cache / version, rom_path=rom_of(args.roms, version),
                            symbols=sim.Symbols(map_path), map_path=map_path)


def pattern_pads(template: bytes, frames: int) -> list[bytes]:
    """Stick swings with a jump now and then: enough to move Mario, the camera and the objects he touches."""
    return [stage_sim.pads(0x8000 if i % 37 == 5 else 0, 60 if (i // 20) % 2 else -40, 70, template) for i in range(frames)]


def builds(args) -> int:
    report = {}
    for version in ("us", "jp"):
        game = cold_boot.ColdGame(version, {"rom": str(rom_of(args.roms, version))})
        build = game.cpu.build
        mapped = sim.Symbols(cold_boot.map_path(version))
        read = {}
        for name in ("sCurrentCmd", "gRandomSeed16"):
            fresh = sim.Symbols(cold_boot.map_path(version))
            fresh.by_name.pop(name, None)
            sim.complete_symbols(fresh, game.cpu.ram)
            read[name] = {"read off the code": f"{fresh.by_name.get(name, 0):08x}",
                          "the map": f"{mapped.by_name[name]:08x}" if name in mapped.by_name else None}
        report[version] = {**{field.name: (f"{value:08x}" if isinstance(value, int) and value > 0xFFFF else value)
                              for field in dataclasses.fields(build) for value in [getattr(build, field.name)]},
                           "names": read}
    print(json.dumps(report, indent=1))
    return 0


def _differing(a: np.ndarray, b: np.ndarray, stack: tuple[int, int], symbols) -> tuple[int, list[str]]:
    mask = a != b
    mask[stack[0]:stack[1]] = False
    where = np.flatnonzero(mask)
    return int(where.size), sorted({sim.label(symbols, int(at)) for at in where[:4000]})[:12]


def equivalence(args) -> int:
    stages = stages_for(args, args.version)
    began = time.perf_counter()
    reset = stages._make_reset(args.level, args.area, args.act)        # leaves the cold game standing at the reset
    game, symbols = stages._game, stages.symbols
    if not np.array_equal(game.cpu.ram, reset):
        raise SystemExit("the cold game is not where the reset left it")
    build = game.cpu.build
    if args.mutate:
        build = dataclasses.replace(build, **{args.mutate: getattr(build, args.mutate) + MUTATIONS[args.mutate]})
        sim.BUDGET_US = 5_000_000
    machine = stage_sim.Machine(reset.copy(), stages.rom, symbols, stage_sim.COLD_FCSR,
                                cpu_class=lambda *a: sim.GameCpu(*a, build=build))
    template = reset[sim.phys(symbols["gControllerPads"]):sim.phys(symbols["gControllerPads"]) + 24].tobytes()
    stack = (sim.phys(symbols["gThread5Stack"]), sim.phys(symbols.next_after(symbols["gThread5Stack"])))
    health = symbols["gMarioStates"] + MARIO_HEALTH_OFF
    level0 = stage_sim.s16(reset, symbols["gCurrLevelNum"])
    result = {"version": args.version, "stage": [args.level, args.area, args.act], "mutated": args.mutate,
              "death": args.death, "reset_seconds": round(time.perf_counter() - began, 1)}
    levels, first = [level0], None
    pads = pattern_pads(template, args.frames) if not args.death else [stage_sim.pads(0, 0, 0, template)] * args.frames
    for index, pad in enumerate(pads):
        if args.death and index == 5:                                 # the same write into both: Mario's health gone
            for ram in (game.cpu.ram, machine.ram):
                struct.pack_into(">h", ram, sim.phys(health), 0x00FF)
        for cpu in (game.cpu,):
            cpu.put_u32(symbols["sNumVblanks"], cpu.u32(symbols["sNumVblanks"]) + 2)
            cpu.put_u32(cpu.build.usamune_vi_count, cpu.u32(cpu.build.usamune_vi_count) + 2)
        ok = game.step(pads=pad)
        try:
            machine.step(pad)
            stepped = True
        except stage_sim.StageError as stopped:
            stepped, result["machine_stopped"] = False, str(stopped)[:160]
        level = stage_sim.s16(game.cpu.ram, symbols["gCurrLevelNum"])
        if level != levels[-1]:
            levels.append(level)
        count, where = _differing(game.cpu.ram, machine.ram, stack, symbols)
        if not ok or not stepped or count:
            first = {"frame": index, "cold_ok": ok, "machine_ok": stepped, "bytes": count, "where": where,
                     "violations": game.cpu.violations[:2]}
            break
    result.update(frames_equal=args.frames if first is None else first["frame"], first_difference=first,
                  levels_passed=levels, verdict="green" if first is None else "red")
    print(json.dumps(result, indent=1))
    return 0


def warm(args) -> int:
    from sm64_events.server.scene_warm import rest
    stages = stages_for(args, args.version)
    began, made, refused = time.perf_counter(), 0, []
    for level, area, act in rest():
        try:
            stages.reset(level, area, act)
            made += 1
        except stage_sim.StageError as error:
            refused.append({"stage": [level, area, act], "reason": str(error)})
    seconds = time.perf_counter() - began
    files = [path for path in (args.cache / args.version).rglob("*") if path.is_file()]
    print(json.dumps({"version": args.version, "stages": len(rest()), "made": made, "refused": refused,
                      "seconds": round(seconds, 1), "seconds_per_stage": round(seconds / max(len(rest()), 1), 2),
                      "files": len(files), "bytes": sum(path.stat().st_size for path in files)}, indent=1))
    return 0


def corpus(args) -> int:
    from ghost_resim import REGION_OF_LAYOUT, pads_of, pose_of, sim_mario

    from sm64_events.ghost.clip import decode_clip
    stages = stages_for(args, args.version)
    symbols = stages.symbols
    rows = []
    for path in sorted(args.clips.rglob("*.ghost")):
        try:
            clip = decode_clip(path.read_bytes())
        except Exception as error:  # noqa: BLE001 - a clip this Trainer cannot read is counted, not fatal
            rows.append({"clip": path.name[:16], "undecodable": type(error).__name__})
            continue
        if any(record.pad is None for record in clip.frames):
            rows.append({"clip": path.name[:16], "unpadded": True})
            continue
        first = clip.frames[0]
        row = {"clip": path.name[:16], "recorded_on": REGION_OF_LAYOUT.get(clip.rom_layout), "level": first.level,
               "area": first.area, "records": len(clip.frames)}
        try:
            start = stages.reset(first.level, first.area, 1).copy()
        except stage_sim.StageError as error:
            rows.append({**row, "no_reset": str(error)[:100]})
            continue
        struct.pack_into(">I", start, sim.phys(symbols["gGlobalTimer"]), first.global_timer)
        struct.pack_into(">H", start, sim.phys(symbols["gAreaUpdateCounter"]), first.area_update_counter)
        template = start[sim.phys(symbols["gControllerPads"]):sim.phys(symbols["gControllerPads"]) + 24].tobytes()
        machine = stage_sim.Machine(start, stages.rom, symbols, stage_sim.COLD_FCSR)
        parting, farthest, compared = None, 0.0, 0
        try:
            for index, record in enumerate(clip.frames[1:], start=1):
                machine.step(pads_of(record, template))
                pose = pose_of(record)
                if pose is None:
                    continue
                compared += 1
                position, action = sim_mario(machine.ram, symbols)
                farthest = max(farthest, float(np.linalg.norm(position - pose[0])))
                if parting is None and (action != pose[1] or not np.array_equal(position, pose[0])):
                    parting = index
        except stage_sim.StageError as error:
            row["stopped"] = str(error)[:100]
        rows.append({**row, "poses": compared, "parting": parting, "farthest": round(farthest, 3)})
    summary = {"simulated_on": args.version, "clips": len(rows), "rows": rows}
    text = json.dumps(summary, indent=0)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items() if key != "rows"}))
    return 0


def package(args) -> int:
    import package as packages

    from sm64_events.memory.layout import layout_for
    stages = stages_for(args, args.version)
    out = args.cache / f"proof-{args.version}.sm64scene"
    out.unlink(missing_ok=True)
    stages.package(args.level, args.area, args.act, frames=args.frames, out=out)
    pkg = packages.open_package(out, stages.rom_path)
    layout = layout_for(args.version)
    wanted = {sim.phys(layout.value(f"{row}_start")) for row in ("scene_data", "scene_bss", "scene_engine_bss")}
    begins = set()
    for index in range(len(pkg)):
        _fields, _extras, spans, _level, _known = pkg.scene_regions(index)
        begins |= {begin for begin, _data in spans}
    identity = {name: address for name, address, _size in pkg.identity}
    expected = {name: sim.phys(getattr(layout, name)) for name in ("global_timer", "curr_level", "curr_area")}
    print(json.dumps({"version": args.version, "frames": len(pkg), "scene_spans_found": sorted(hex(b) for b in wanted & begins),
                      "scene_spans_missing": sorted(hex(b) for b in wanted - begins),
                      "identity_is_this_builds": identity == expected, "identity": {k: hex(v) for k, v in identity.items()},
                      "package_bytes": out.stat().st_size}, indent=1))
    return 0


def savestate(args) -> int:
    import ghost_resim
    import moment_slot
    import package as packages

    from sm64_events.ghost.clip import decode_clip
    stages = stages_for(args, args.version)
    clip_bytes = args.clip.read_bytes()
    scenes = ghost_resim.GhostScenes(args.cache, args.cache / "scenes", scenes_dir=None, starts_dir=None, stages=stages)
    began = time.perf_counter()
    path, info = scenes.build(clip_bytes)
    pkg = packages.open_package(path, stages.rom_path)
    frame = min(len(pkg) - 2, args.frame)
    built = moment_slot.build(moment_slot.PackageMoments(pkg), stages.rom, stages.map_path, frame, 3, symbols=stages.symbols)
    answer = ghost_resim.check_slot_load(path, frame, built.data, decode_clip(clip_bytes), stages, info["first_record"])
    print(json.dumps({"version": args.version, "start": info["start"], "parting_frame": info["parting_frame"],
                      "simulated_on": info["simulated_on"], "recorded_on": info["recorded_on"],
                      "frame": frame, "slot_level_area": [built.level, built.area], "mario_object": f"{built.mario_object:08x}",
                      "checked": built.checked, "load_check": answer, "seconds": round(time.perf_counter() - began, 1)},
                     indent=1))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("builds", "equivalence", "warm", "corpus", "package", "savestate"))
    parser.add_argument("--roms", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--version", choices=("us", "jp"), default="jp")
    parser.add_argument("--level", type=int, default=24)
    parser.add_argument("--area", type=int, default=1)
    parser.add_argument("--act", type=int, default=1)
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--frame", type=int, default=120)
    parser.add_argument("--death", action="store_true")
    parser.add_argument("--mutate", choices=tuple(MUTATIONS))
    parser.add_argument("--clips", type=Path)
    parser.add_argument("--clip", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    if not cold_boot.map_path("us").is_file() or not cold_boot.map_path("jp").is_file():
        raise SystemExit(f"copy both STROOP maps into {cold_boot.map_path('us').parent} first")
    return {"builds": builds, "equivalence": equivalence, "warm": warm, "corpus": corpus, "package": package,
            "savestate": savestate}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
