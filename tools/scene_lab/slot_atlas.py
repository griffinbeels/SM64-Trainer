"""Build the atlas for one level and area from cold boots of both Usamune ROMs (simulation only; no recording).

    uv run --with unicorn==2.1.4 --with capstone python tools/scene_lab/slot_atlas.py build --level 9 --area 1 --out FILE

Each build boots from its own cartridge (cold_boot.py), warps to the place the way a painting does, and runs
ENTRY_FRAMES more frames standing still; then the two RAMs give the atlas (slotlibrary/atlas.py). The US ROM is
lab.json's "rom", and the JP ROM is found beside it by its header; in the installed app both come from its ROM
store and bundled maps (core/lab_inputs.py::tool_config). Prints one JSON line: where it wrote the atlas,
or {"refused": reason}. The Trainer runs this as a hidden child when a save state crosses builds
(slotlibrary/atlas.py::child_builder).
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cold_boot  # noqa: E402
from sm64_events.memory import behaviours  # noqa: E402
from sm64_events.memory.addresses import OBJECT_BEHAVIOR, OBJECT_COUNT, OBJECT_HOME_POS, OBJECT_SIZE  # noqa: E402
from sm64_events.slotlibrary import atlas as A  # noqa: E402
from sm64_events.slotlibrary.relocate import OBJECT_ACTIVE_FLAGS  # noqa: E402
from sm64_events.slotlibrary.store import write_atomic  # noqa: E402

ENTRY_FRAMES = 10                  # after the warp arrives: the moment the 2026-11-08 study learned its atlases at
DEFAULT_NODE = 0x0A                # the entry node a painting uses
AREA_BYTES, AREA_WARP_NODES = 0x3C, 0x14    # struct Area, and its ObjectWarpNode list (decomp include/types.h)
sim = cold_boot.sim


def world_of(game: cold_boot.ColdGame) -> A.World:
    cpu, version = game.cpu, game.version
    word = cpu.u32
    pool = (game.u32("sPoolStart"), game.u32("sPoolEnd"))
    blocks = A.pool_blocks(word, *pool, game.u32("sPoolListHeadL"), game.u32("sPoolListHeadR"))
    table = cpu.symbols["sSegmentTable"]
    segments = tuple(word(table + 4 * i) for i in range(A.SEGMENTS))
    scripts = segments[0x13] | 0x80000000
    objects = []
    for index in range(OBJECT_COUNT):
        at = cpu.symbols["gObjectPool"] + index * OBJECT_SIZE
        if struct.unpack_from(">h", cpu.ram, sim.phys(at + OBJECT_ACTIVE_FLAGS))[0] == 0:
            continue
        name = behaviours.symbol_of(version, word(at + OBJECT_BEHAVIOR), scripts)
        home = struct.unpack_from(">3f", cpu.ram, sim.phys(at + OBJECT_HOME_POS))
        objects.append((index, name, *(round(float(v), 3) for v in home)))
    return A.World(version, pool, blocks, segments, cold_boot.USAMUNE[version]["settings"], tuple(objects))


def entry_node(game: cold_boot.ColdGame, area: int) -> int:
    """A warp node the loaded level gives this area: 0x0A when it has one, else its lowest entry."""
    node, ids = game.cpu.u32(game.cpu.symbols["gAreaData"] + area * AREA_BYTES + AREA_WARP_NODES), []
    while 0x80000000 < node < 0x80800000 and len(ids) < 64:
        ids.append(int(game.cpu.ram[sim.phys(node)]))
        node = game.cpu.u32(node + 8)
    entries = [i for i in ids if i < 0xF0]                    # 0xF0 and up are the level's exits and deaths
    if not entries:
        raise SystemExit(f"area {area} of this level has no warp node to arrive by")
    return DEFAULT_NODE if DEFAULT_NODE in entries else min(entries)


def warp_into_area(game: cold_boot.ColdGame, level: int, area: int, node: int, limit: int = 900) -> int:
    """Like cold_boot.warp_to, but done only once the level has been left and this area holds Mario: it works
    from inside the same level too."""
    def write():
        struct.pack_into(">BBBBI", game.cpu.ram, sim.phys(game.cpu.symbols["sWarpDest"]), 1, level, area, node, 0)
    game.pending = write
    left = False
    for frames in range(1, limit):
        buttons = cold_boot.BUTTON_A if frames > 30 and frames % 20 == 0 and not game.mario_place() else 0
        if not game.step(buttons):
            raise SystemExit(f"{game.version}: {game.cpu.violations[:3]}")
        left = left or not game.mario_place()
        if left and game.in_level(level) and game.u16("gCurrAreaIndex") == area:
            cold_boot.arrived(game)
            return frames
    raise SystemExit(f"{game.version}: the warp to level {level} area {area} never arrived")


def standing_in(version: str, level: int, area: int, config=None) -> cold_boot.ColdGame:
    game, _frame = cold_boot.boot_into_castle(version, config)
    cold_boot.warp_to(game, level, 1)
    if area != 1:
        warp_into_area(game, level, area, entry_node(game, area))
    for _ in range(ENTRY_FRAMES):
        if not game.step():
            raise SystemExit(f"{version}: {game.cpu.violations[:3]}")
    if not game.in_level(level) or game.u16("gCurrAreaIndex") != area:
        raise SystemExit(f"the {version.upper()} cold boot could not reach level {level} area {area}")
    return game


SECONDS: dict[str, float] = {}     # what the last build spent, by step: printed with its answer


def build(level: int, area: int, config=None) -> A.Atlas:
    games = {}
    for version in ("us", "jp"):
        started = time.perf_counter()
        games[version] = standing_in(version, level, area, config)
        SECONDS[f"boot {version}"] = round(time.perf_counter() - started, 1)
    started = time.perf_counter()
    worlds = {version: world_of(game) for version, game in games.items()}
    rams = {version: game.cpu.ram for version, game in games.items()}
    bodies = {version: (game.body[0], cold_boot.BODY_BYTES) for version, game in games.items()}
    pairs = {source: A.learn_pairs(rams[source], rams[target], worlds[source], worlds[target], bodies[source],
                                   bodies[target])
             for source, target in (("us", "jp"), ("jp", "us"))}
    SECONDS["learn"] = round(time.perf_counter() - started, 1)
    return A.Atlas(level, area, worlds, pairs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("build")
    make.add_argument("--level", type=int, required=True)
    make.add_argument("--area", type=int, required=True)
    make.add_argument("--out", type=Path, required=True)
    make.add_argument("--lab-config", type=Path, help="the lab.json naming the ROMs (default: core/lab_inputs.py's)")
    args = parser.parse_args()
    try:
        from sm64_events.core.lab_inputs import tool_config  # noqa: PLC0415
        atlas = build(args.level, args.area, tool_config(args.lab_config))
    except SystemExit as stopped:
        print(json.dumps({"refused": str(stopped)}))
        return 2
    write_atomic(args.out, A.to_json(atlas))
    print(json.dumps({"atlas": str(args.out), "pairs": {v: len(p) for v, p in atlas.pairs.items()},
                      "blocks": {v: len(w.blocks) for v, w in atlas.worlds.items()}, "seconds": SECONDS}))
    return 0


if __name__ == "__main__":
    import sim_boundary  # a simulation process: no (L)GPL-3.0 library from here on
    sim_boundary.enter()
    raise SystemExit(main())
