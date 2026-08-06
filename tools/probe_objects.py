"""READ-ONLY probe: WHICH specific door, pole or enemy did Mario just touch?

The recorder can already say "Open a door (#2) in Castle Inside" -- a kind plus
a count. What it could not say is WHICH door. His ruling, 2026-08-05: the count
is a property of the thing rather than its name -- "this specific door that
happens to be the 5th one you open".

ANSWERED 2026-08-05 over one ordinary session: the identity is the object's
SPAWN POINT (addresses.py::OBJECT_HOME_POS carries the evidence), and this tool
now LISTS the distinct things he touched rather than hunting for the field that
names them. It still dumps whole objects, because the differencing is what
would show the key failing for a kind nobody has tried yet:

  * an offset that moved between two frames of one interaction is VOLATILE and
    cannot name anything -- judged inside a behaviour, never across all of
    them, or Mario's own object marks POSITION volatile and throws the answer
    away (that bug hid this result for a round);
  * an offset that stays put for one thing and differs between two of them
    draws the same line the spawn point does, and the report names those;
  * an object the GAME spawned mid-play has no spawn point at all (Mario, a
    star popping out), so those collapse into one row and the report says so.

Which gMarioState offsets even hold an object is discovered, not assumed: every
word of the struct's first 0xC0 bytes is checked each frame for a value landing
on an object-pool SLOT BOUNDARY, so the pointer fields announce themselves. The
names in POINTER_HINTS are decomp's and are reading aids, not claims.

Attaches read-only via ReadProcessMemory. Takes no instance lock and no
recorder lock, so it is safe to run beside the live server while playing.

Usage:
    uv run python tools/probe_objects.py     # play; CTRL+C to stop
    uv run python tools/probe_objects.py --report

To re-check the key against a kind it has not met, play so that it sees the
same thing twice with an area reload in between -- the report's verdict counts
exactly that, because a name that outlives a pool rebuild is the whole claim.
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


def home_of(capture: dict) -> tuple[float, float, float]:
    """The spawn point of the object this capture caught -- its IDENTITY."""
    raw = bytes.fromhex(capture["obj"])[A.OBJECT_HOME_POS:A.OBJECT_HOME_POS + 12]
    return struct.unpack(">fff", raw)


def entities(captures: list[dict]) -> list[dict]:
    """The DISTINCT things these captures touched, most-touched first.

    One row per real object, keyed on where it spawned. Addresses.py carries
    the evidence for why that key and not the pool slot or the live position.
    """
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for capture in captures:
        grouped[home_of(capture)].append(capture)
    rows = [{
        "home": home,
        "captures": mine,
        "slots": sorted({capture["slot"] for capture in mine}),
        "actions": sorted({capture["action_name"] for capture in mine}),
        "epochs": sorted({capture["epoch"] for capture in mine}),
    } for home, mine in grouped.items()]
    rows.sort(key=lambda row: (-len(row["captures"]), row["home"]))
    return rows


def corroborating_offsets(captures: list[dict], volatile: set[int]) -> list[int]:
    """Offsets that split these captures EXACTLY the way the spawn point does.

    The spawn point is three words wide, so a single-word field that draws the
    same line is worth knowing about -- and an offset that draws a DIFFERENT
    line is how we would find out the key had stopped working for a new kind.
    """
    truth = {}
    for capture in captures:
        truth.setdefault(home_of(capture), []).append(capture)
    if len(truth) < 2:
        return []
    matching = []
    for offset in range(0, A.OBJECT_SIZE, 4):
        if offset in volatile:
            continue
        per_entity = [{words(capture["obj"])[offset // 4] for capture in mine}
                      for mine in truth.values()]
        if any(len(values) != 1 for values in per_entity):
            continue  # moved while the object stayed the same
        seen = [next(iter(values)) for values in per_entity]
        if len(set(seen)) == len(seen):
            matching.append(offset)
    return matching


def analyse(captures: list[dict]) -> dict:
    groups = []
    for key in sorted({group_key(capture) for capture in captures}):
        mine = [capture for capture in captures if group_key(capture) == key]
        # PER GROUP, never across all captures: Mario's own object moves every
        # frame, so one global mask marks POSITION volatile and throws away the
        # only offset that turned out to name a door (2026-08-05, his session).
        volatile = volatile_offsets(mine)
        groups.append({
            "level": key[0], "area": key[1], "behaviour": key[2],
            "captures": mine,
            "actions": sorted({capture["action_name"] for capture in mine}),
            "fields": sorted({capture["field"] for capture in mine}),
            "epochs": len({capture["epoch"] for capture in mine}),
            "volatile": len(volatile),
            "entities": entities(mine),
            "corroborating": corroborating_offsets(mine, volatile),
        })
    return {
        "captures": len(captures),
        "unusable": sum(1 for capture in captures if not capture["obj_next"]),
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
            # A reload REBUILDS the pool, so the same slot two frames later
            # holds whatever moved in -- scored naively that reads as the object
            # having MOVED, and it is how a door's own position looked volatile
            # and got discarded (2026-08-05, his session). An empty second read
            # contributes nothing instead of contributing a lie.
            reloaded = ((record["level"], record["area"]) != place
                        or frame < record["frame"])
            if frame >= due or reloaded:
                record["obj_next"] = "" if reloaded else mem.read_block(
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

    print(f"\n{found['captures']} captures, {found['unusable']} of them with no "
          "second read (the game reloaded inside the window)")
    print("gMarioState offsets that held an object pointer: " + ", ".join(
        f"{offset:#04x} {POINTER_HINTS.get(offset, '')}".strip()
        for offset in found["pointer_fields"]))

    for group in found["groups"]:
        print(f"\n=== level {group['level']} area {group['area']}  "
              f"behaviour {group['behaviour']:#010x}  "
              f"{len(group['entities'])} distinct thing(s), "
              f"{len(group['captures'])} interaction(s): "
              + ", ".join(group["actions"]))
        for entity in group["entities"]:
            home = ", ".join(f"{axis:.0f}" for axis in entity["home"])
            print(f"    spawned at ({home})   touched {len(entity['captures'])}x"
                  f"   pool slots {entity['slots']}"
                  f"   {', '.join(entity['actions'])}")
            if entity["home"] == (0.0, 0.0, 0.0):
                # A level script writes a spawn point; something the GAME spawns
                # mid-play (a star popping out, Mario himself) never gets one, so
                # this key cannot tell two of those apart and says so out loud.
                print("      ^ no spawn point: the game made this one at runtime,"
                      " so several of them would collapse into this row")
        if group["corroborating"]:
            print("    single words that draw the same line: " + ", ".join(
                f"+{offset:#05x}" for offset in group["corroborating"][:SHOWN]))

    renamed = sum(1 for group in found["groups"] for entity in group["entities"]
                  if len(entity["slots"]) > 1)
    print(f"\nVERDICT: {renamed} thing(s) kept one name while the game moved them"
          " to a different pool slot -- which is what the slot count could not do."
          if renamed else
          "\nVERDICT: nothing here outlived a pool rebuild, so this session cannot"
          " tell an identity from a slot. Reload an area and touch the same thing"
          " again.")
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
