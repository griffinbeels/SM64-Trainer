"""READ-ONLY probe: WHICH specific door, pole or enemy did Mario just touch?

The recorder can already say "Open a door (#2) in Castle Inside" -- a kind plus
a count. What it cannot say is WHICH door. His ruling, 2026-08-05: the count is
a property of the thing rather than its name -- "this specific door that
happens to be the 5th one you open" -- so the identity has to come out of the
GAME'S OWN OBJECT.

This project does not guess an offset, so the probe does not test a hypothesis
about where that identity lives. It DUMPS the whole object Mario is
interacting with, every time the interaction changes, and the report finds the
identity by DIFFERENCING what it caught:

  * an offset whose value moves between two consecutive frames is VOLATILE
    (a timer, an animation counter) and can never be an identity;
  * an offset holding ONE value across every capture of a behaviour names the
    KIND, not the instance (the behaviour pointer is the known example);
  * an offset taking SEVERAL values that each RECUR is the INSTANCE -- door A,
    door B, door A again reads as [X, Y, X], and that is the shape to hunt;
  * one that recurs ACROSS an epoch boundary (savestate load, level reload)
    survives the thing he actually practices with.

Which gMarioState offsets even hold an object is not assumed either: every
word of the struct's first 0xC0 bytes is checked each frame for a value that
lands on an object-pool SLOT BOUNDARY, so the pointer fields announce
themselves. The names in POINTER_HINTS are decomp's and are reading aids, not
claims -- nothing here is promoted to addresses.py until this gate passes.

Attaches read-only via ReadProcessMemory. Takes no instance lock and no
recorder lock, so it is safe to run beside the live server while playing.

Usage:
    uv run python tools/probe_objects.py     # play; CTRL+C to stop
    uv run python tools/probe_objects.py --report

What to do while it runs, in this order -- each step answers one question:
  1. open ONE door, walk back, open the SAME door again     (does it recur?)
  2. open a DIFFERENT door                                  (is it distinct?)
  3. load a savestate, open the first door a third time     (does it survive?)
  4. grab a pole or tree, stomp a goomba, pick up a bob-omb (other kinds)
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import time
from collections import defaultdict
from pathlib import Path

from sm64_events.memory import addresses as A
from sm64_events.memory.objects import pool_slot, slot_address
from sm64_events.memory.pj64 import Pj64Memory

CAPTURE_PATH = Path("data/object_probe.jsonl")

# How much of gMarioState to scan for object pointers. Wide on purpose: a
# Surface pointer (wall/ceil/floor) or a plain integer simply fails the
# pool-slot test, so scanning costs nothing and an UNEXPECTED pointer field
# gets discovered instead of assumed away.
MARIO_SCAN_BYTES = 0xC0
POINTER_HINTS = {
    0x78: "interactObj?",
    0x7C: "heldObj?",
    0x80: "usedObj?",
    0x84: "riddenObj?",
    0x88: "marioObj?",
}
MARIO_POS = 0x3C  # Vec3f, decomp; a hint like the above -- the report shows it
                  # so a wrong guess here is visible rather than load-bearing.

# The second read that separates a timer from an identity. Two game frames is
# enough for anything animated to move and short enough that the object is
# still the one Mario touched.
VOLATILITY_GAP_FRAMES = 2

ACTION_NAMES = {value: name for name, value in vars(A).items()
                if name.startswith("ACT_") and isinstance(value, int)}

WORD_COUNT = A.OBJECT_SIZE // 4
SHOWN = 10  # how many captures / candidates one report line carries


# ---------------------------------------------------------------- pure core

def words(blob: str) -> list[int]:
    """The 32-bit words of a captured object dump, in N64 order."""
    raw = bytes.fromhex(blob)
    return [int.from_bytes(raw[at:at + 4], "big") for at in range(0, len(raw), 4)]


def volatile_offsets(captures: list[dict]) -> set[int]:
    """Offsets that MOVED within a single capture's two-frame window."""
    moved: set[int] = set()
    for capture in captures:
        if not capture.get("obj_next"):
            continue
        at_touch, later = words(capture["obj"]), words(capture["obj_next"])
        moved.update(index * 4 for index, (first, second)
                     in enumerate(zip(at_touch, later)) if first != second)
    return moved


def behaviour_of(capture: dict) -> int:
    return words(capture["obj"])[A.OBJECT_BEHAVIOR // 4]


def group_key(capture: dict) -> tuple[int, int, int]:
    return capture["level"], capture["area"], behaviour_of(capture)


def identity_candidates(captures: list[dict], volatile: set[int]) -> list[dict]:
    """Offsets that could NAME one of these objects, best first.

    An identity must vary between instances and repeat when the same instance
    comes back. Sorting by that second property first is deliberate: an offset
    that recurs across an EPOCH survives the savestate loads he practices
    with, which is the property a label has to have to be worth writing down.
    """
    per_offset: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for capture in captures:
        for index, value in enumerate(words(capture["obj"])):
            offset = index * 4
            if offset not in volatile:
                per_offset[offset].append((value, capture["epoch"]))

    found = []
    for offset, seen in per_offset.items():
        values = [value for value, _ in seen]
        distinct = set(values)
        if len(distinct) < 2:
            continue  # constant across every capture: that is the KIND
        repeats = {value for value in distinct if values.count(value) > 1}
        if not repeats:
            continue  # never the same twice: a counter, or one-shot noise
        cross_epoch = {value for value in repeats
                       if len({epoch for got, epoch in seen if got == value}) > 1}
        found.append({
            "offset": offset,
            "distinct": len(distinct),
            "values": values,
            "repeats": len(repeats),
            "cross_epoch": len(cross_epoch),
        })
    found.sort(key=lambda row: (-row["cross_epoch"], -row["repeats"],
                                row["distinct"], row["offset"]))
    return found


def analyse(captures: list[dict]) -> dict:
    volatile = volatile_offsets(captures)
    groups = []
    for key in sorted({group_key(capture) for capture in captures}):
        mine = [capture for capture in captures if group_key(capture) == key]
        groups.append({
            "level": key[0], "area": key[1], "behaviour": key[2],
            "captures": mine,
            "actions": sorted({capture["action_name"] for capture in mine}),
            "fields": sorted({capture["field"] for capture in mine}),
            "epochs": len({capture["epoch"] for capture in mine}),
            "candidates": identity_candidates(mine, volatile),
        })
    return {
        "captures": len(captures),
        "volatile": len(volatile),
        "pointer_fields": sorted({capture["field"] for capture in captures}),
        "groups": groups,
    }


def annotate(value: int) -> str:
    """What a 32-bit word might BE, for reading the report by eye."""
    notes = []
    located = pool_slot(value)
    if located is not None:
        notes.append(f"obj slot {located[0]}+{located[1]:#x}")
    elif 0x80000000 <= value < 0x80800000:
        notes.append("ram ptr")
    as_float = struct.unpack(">f", value.to_bytes(4, "big"))[0]
    if as_float == as_float and 0.5 < abs(as_float) < 1e6:
        notes.append(f"f32 {as_float:.1f}")
    return "  ".join(notes)


# ------------------------------------------------------------------ capture

def is_object_pointer(value: int) -> bool:
    located = pool_slot(value)
    return located is not None and located[1] == 0


def sample_mario(mem) -> tuple[bytes, dict[int, int]]:
    blob = mem.read_block(A.MARIO_STRUCT, MARIO_SCAN_BYTES)
    pointers = {}
    for offset in range(0, MARIO_SCAN_BYTES, 4):
        value = int.from_bytes(blob[offset:offset + 4], "big")
        if is_object_pointer(value):
            pointers[offset] = value
    return blob, pointers


def capture_record(mem, blob, offset, pointer, frame, epoch) -> dict:
    slot, _ = pool_slot(pointer)
    position = struct.unpack(">fff", blob[MARIO_POS:MARIO_POS + 12])
    action = int.from_bytes(blob[0x0C:0x10], "big")
    return {
        "frame": frame,
        "epoch": epoch,
        "level": mem.read_s16(A.CURR_LEVEL),
        "area": mem.read_s16(A.CURR_AREA),
        "action": action,
        "action_name": ACTION_NAMES.get(action, f"{action:#010x}"),
        "field": offset,
        "field_hint": POINTER_HINTS.get(offset, ""),
        "slot": slot,
        "mario_pos": [round(axis, 1) for axis in position],
        "obj": mem.read_block(slot_address(slot), A.OBJECT_SIZE).hex(),
        "obj_next": "",
    }


def watch(out_path: Path) -> int:
    mem = Pj64Memory()
    if not mem.attach():
        print("Could not attach -- is PJ64 running with the ROM loaded?")
        return 1
    out_path.parent.mkdir(parents=True, exist_ok=True)
    handle = out_path.open("a", encoding="utf-8")
    print(f"Attached read-only; writing {out_path}")
    print("Play. Open the SAME door twice, then a different one, then reload a")
    print("savestate and open the first again. CTRL+C to stop.\n")

    previous_frame = -1
    previous_pointers: dict[int, int] = {}
    previous_place = None
    epoch = 0
    pending: list[tuple[int, int, dict]] = []  # (due frame, slot, record)
    caught = 0

    while True:
        time.sleep(1 / 120)
        frame = mem.read_u32(A.GLOBAL_TIMER)
        if frame == previous_frame:
            continue
        blob, pointers = sample_mario(mem)
        place = (mem.read_s16(A.CURR_LEVEL), mem.read_s16(A.CURR_AREA))
        if frame < previous_frame or (previous_place and place != previous_place):
            epoch += 1
            previous_pointers = {}
        previous_frame, previous_place = frame, place

        for due, slot, record in list(pending):
            if frame >= due:
                record["obj_next"] = mem.read_block(
                    slot_address(slot), A.OBJECT_SIZE).hex()
                handle.write(json.dumps(record) + "\n")
                handle.flush()
                pending.remove((due, slot, record))
                caught += 1
                print(f"  [{caught:3d}] frame {record['frame']}  "
                      f"{record['action_name']:<28} "
                      f"+{record['field']:#04x} {record['field_hint']:<13} "
                      f"slot {record['slot']:3d}  "
                      f"bhv {behaviour_of(record):#010x}")
                sys.stdout.flush()

        for offset, pointer in pointers.items():
            if previous_pointers.get(offset) == pointer:
                continue
            record = capture_record(mem, blob, offset, pointer, frame, epoch)
            pending.append((frame + VOLATILITY_GAP_FRAMES, record["slot"], record))
        previous_pointers = pointers


# ------------------------------------------------------------------- report

def report(path: Path) -> int:
    if not path.exists():
        print(f"No captures at {path} -- run the probe and play first.")
        return 1
    captures = [json.loads(line) for line in path.read_text().splitlines() if line]
    if not captures:
        print(f"{path} is empty -- nothing was caught.")
        return 1
    found = analyse(captures)

    print(f"\n{found['captures']} captures, {found['volatile']} volatile offsets "
          f"of {WORD_COUNT * 4} bytes")
    print("gMarioState offsets that held an object pointer: " + ", ".join(
        f"{offset:#04x} {POINTER_HINTS.get(offset, '')}".strip()
        for offset in found["pointer_fields"]))

    for group in found["groups"]:
        print(f"\n=== level {group['level']} area {group['area']}  "
              f"behaviour {group['behaviour']:#010x}  "
              f"{len(group['captures'])} captures over {group['epochs']} epoch(s)")
        print("    actions: " + ", ".join(group["actions"]))
        # The value columns below are in this order, so the legend is what makes
        # [X, Y, X] readable as "the same door, a different one, that one again".
        print("    captures: " + "  ".join(
            f"frame {capture['frame']} e{capture['epoch']}"
            for capture in group["captures"][:SHOWN]))
        if not group["candidates"]:
            print("    NO identity candidate: every stable offset is either"
                  " constant (the kind) or never seen twice.")
            continue
        print(f"    {len(group['candidates'])} identity candidate(s), best first"
              " -- read the value column for [X, Y, X]:")
        for row in group["candidates"][:SHOWN]:
            values = " ".join(f"{value:08x}" for value in row["values"][:SHOWN])
            if len(row["values"]) > SHOWN:
                values += " ..."
            print(f"      +{row['offset']:#05x}  {row['distinct']} distinct, "
                  f"{row['repeats']} recur, {row['cross_epoch']} across a reload"
                  f"   {values}")
            print(f"              {annotate(row['values'][0])}")

    survivors = sum(1 for group in found["groups"]
                    for row in group["candidates"] if row["cross_epoch"])
    if survivors:
        print(f"\nVERDICT: {survivors} candidate offset(s) named the same object "
              "again after a reload.")
    else:
        print("\nVERDICT: nothing recurred across a reload -- either no reload "
              "happened during capture, or no offset survives one.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true",
                        help="analyse what was captured instead of capturing")
    parser.add_argument("--path", type=Path, default=CAPTURE_PATH)
    args = parser.parse_args()
    return report(args.path) if args.report else watch(args.path)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nstopped -- now run: uv run python tools/probe_objects.py --report")
