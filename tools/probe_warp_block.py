"""READ-ONLY probe: what does the game know at the ENTRANCE TOUCH frame?

The question this answers is the one the held emit exists because of. Today
`warp_entered` is withheld until the level byte moves -- 77 frames, 2.6 s --
because the touch was believed unable to name its own destination. That belief
came from reading decomp, not from watching RAM, and the practice-log row
arriving 2.9 s after the portal is what it costs.

Two candidates, both inside the `level_update.c` FORCE_BSS block already pinned
in addresses.py (sWarpDest 0x8033B248, sDelayedWarpOp 0x8033B252, timer
0x8033B254, sTimerRunning 0x8033B25E as the block's last member):

  * sSourceWarpNodeId -- derived 0x8033B256 from that declaration order, and
    set by `level_trigger_warp()` on the TOUCH frame. If it changes at the
    touch and differs per entrance, the touch can be published immediately.
  * sWarpDest.levelNum -- 0x8033B249, written by `initiate_warp()` when the
    delayed warp fires (~20 frames in), NOT at the touch. If it lands well
    before the level byte moves, that alone is most of the latency.

Attaches read-only via ReadProcessMemory. Takes no instance lock and no
recorder lock, so it is safe to run beside the live server while playing.

Usage: with PJ64 + Usamune running, `uv run python tools/probe_warp_block.py`,
then walk into any course entrance. Prints one frame-by-frame trace per touch.
"""
import sys
import time

from sm64_events.memory.addresses import (
    CURR_AREA, CURR_LEVEL, GLOBAL_TIMER, MARIO_ACTION, WARP_ENTRY_ACTIONS,
)
from sm64_events.memory.pj64 import Pj64Memory

WARP_DEST = 0x8033B248          # struct WarpDest: type, levelNum, areaIdx, nodeId, s32 arg
DELAYED_WARP_OP = 0x8033B252
DELAYED_WARP_TIMER = 0x8033B254
SOURCE_WARP_NODE_ID = 0x8033B256  # DERIVED -- this probe is its live gate
DELAYED_WARP_ARG = 0x8033B258

TRACE_FRAMES = 200


def sample(mem) -> dict:
    return {
        "timer": mem.read_u32(GLOBAL_TIMER),
        "action": mem.read_u32(MARIO_ACTION),
        "level": mem.read_s16(CURR_LEVEL),
        "area": mem.read_s16(CURR_AREA),
        "dest_type": mem.read_u8(WARP_DEST),
        "dest_level": mem.read_u8(WARP_DEST + 1),
        "dest_area": mem.read_u8(WARP_DEST + 2),
        "dest_node": mem.read_u8(WARP_DEST + 3),
        "op": mem.read_s16(DELAYED_WARP_OP),
        "op_timer": mem.read_s16(DELAYED_WARP_TIMER),
        "src_node": mem.read_s16(SOURCE_WARP_NODE_ID),
        "arg": mem.read_u32(DELAYED_WARP_ARG),
    }


def report(trace: list[dict]) -> None:
    touch = trace[0]
    print()
    print(f"=== TOUCH at frame {touch['timer']} "
          f"level {touch['level']} area {touch['area']} "
          f"action 0x{touch['action']:08X} ===")
    header = ("  d_frame  level  area  op  op_t  src_node  "
              "dest(type/level/area/node)   arg")
    print(header)
    prev_row = None
    for row in trace:
        key = (row["level"], row["area"], row["op"], row["op_timer"],
               row["src_node"], row["dest_type"], row["dest_level"],
               row["dest_area"], row["dest_node"], row["arg"])
        if key == prev_row:
            continue
        prev_row = key
        print(f"  {row['timer'] - touch['timer']:>7}  "
              f"{row['level']:>5}  {row['area']:>4}  "
              f"{row['op']:>2}  {row['op_timer']:>4}  "
              f"{row['src_node']:>8}  "
              f"{row['dest_type']:>4}/{row['dest_level']:>5}/"
              f"{row['dest_area']:>4}/{row['dest_node']:>4}   "
              f"0x{row['arg']:08X}")

    def first_frame(predicate) -> int | None:
        for row in trace:
            if predicate(row):
                return row["timer"] - touch["timer"]
        return None

    landed = first_frame(lambda r: r["level"] != touch["level"])
    dest_ready = first_frame(lambda r: r["dest_level"] == (
        trace[-1]["level"] if landed is not None else -1))
    print(f"  -> level byte moved at  +{landed} frames"
          if landed is not None else "  -> level byte never moved in window")
    print(f"  -> sWarpDest.levelNum matched the destination at  +{dest_ready} frames"
          if dest_ready is not None else
          "  -> sWarpDest.levelNum never matched the destination")
    print(f"  -> sSourceWarpNodeId at the touch frame: {touch['src_node']}")
    print()
    sys.stdout.flush()


def main() -> int:
    mem = Pj64Memory()
    if not mem.attach():
        print("Could not attach -- is PJ64 running with the ROM loaded?")
        return 1
    print("Attached read-only. Walk into any course entrance (painting, portal,")
    print("pipe or hole). CTRL+C to stop.\n")
    prev = sample(mem)
    trace: list[dict] = []
    while True:
        time.sleep(1 / 120)
        curr = sample(mem)
        if curr["timer"] == prev["timer"]:
            continue
        if trace:
            trace.append(curr)
            if len(trace) >= TRACE_FRAMES or (
                    curr["level"] != trace[0]["level"] and len(trace) > 5):
                report(trace)
                trace = []
        elif (curr["action"] in WARP_ENTRY_ACTIONS
              and prev["action"] not in WARP_ENTRY_ACTIONS):
            trace = [curr]
        prev = curr


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nstopped")
