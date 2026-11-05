"""Rewrite a Usamune slot's bytes so Mario sits where the running game has him (Ghost Handoff, simulation only).

    uv run --with unicorn==2.1.4 --with capstone python tools/scene_lab/handoff_relocate.py prove <session>
        --pair K J [--pair K J ...] [--reset-at F] [--follow 300] [--slot 3] [--out NAME]
    uv run --with unicorn==2.1.4 --with capstone python tools/scene_lab/handoff_relocate.py controls <session>
        --moved K J --same K J --wrong-level K J [--out NAME]
    uv run --with unicorn==2.1.4 --with capstone python tools/scene_lab/handoff_relocate.py address <session>
        --at F [--seed N] [--frames N] [--out NAME]
    uv run --with unicorn==2.1.4 --with capstone python tools/scene_lab/handoff_relocate.py fixed-rng <session>
        --from F --to F [--out NAME]

Griffin rejected adding code to the game: "I'm hesitant to add any code to the game... leaving Usamune functionality
untouched." So nothing here changes a routine. A practice reset moves Mario's object, and Usamune's load refuses a
slot whose gMarioObject differs. This module swaps the slot's Mario with whatever the slot holds at the live Mario's
place and rewrites every pointer to either, found by structure: the object fields, Mario's struct, the list heads
and the named globals. Usamune's gate then passes, and its load runs unmodified, inside Mario's update, with the
object loop's firstObj still naming Mario afterward.

rawData is a union the code cannot read by structure, so a pool address there is mapped and reported. An address
found anywhere unexpected stops the relocation rather than guessing. Reports go to scene-lab/handoff-sim-*.
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import handoff_slot as slots  # noqa: E402

BODY_START = 0x8033B170
POOL_START, OBJECT_BYTES, OBJECT_COUNT = 0x8033D488, 0x260, 240
POOL_END = POOL_START + OBJECT_BYTES * OBJECT_COUNT
LIST_HEADS, LIST_HEAD_COUNT, LIST_HEAD_BYTES = 0x8033CBE0, 13, 0x68   # gObjectListArray
FREE_LIST = 0x803610F0                                                # gFreeObjectList: an ObjectNode
RAW_DATA = (0x88, 0x1C8)
BEHAVIOR, BEH_PARAMS, ACTIVE_FLAGS = 0x20C, 0x188, 0x74

# Object fields that hold object pointers, by struct Object.
OBJECT_POINTERS = {0x04: "gfx.prev", 0x08: "gfx.next", 0x0C: "gfx.parent", 0x10: "gfx.children",
                   0x50: "gfx.throwMatrix", 0x60: "next", 0x64: "prev", 0x68: "parentObj", 0x6C: "prevObj",
                   0x78: "collidedObjs[0]", 0x7C: "collidedObjs[1]", 0x80: "collidedObjs[2]",
                   0x84: "collidedObjs[3]", 0x214: "platform"}
MARIO_POINTERS = {0x78: "interactObj", 0x7C: "heldObj", 0x80: "usedObj", 0x84: "riddenObj", 0x88: "marioObj"}
NAMED_GLOBALS = {0x80361158: "gMarioObject", 0x8036115C: "gLuigiObject", 0x80361160: "gCurrentObject"}


def _be_words(data: bytes | np.ndarray) -> np.ndarray:
    return np.frombuffer(data if isinstance(data, bytes) else data.tobytes(), ">u4").astype(np.uint32)


def in_pool(values: np.ndarray) -> np.ndarray:
    return (values >= POOL_START) & (values < POOL_END)


def object_index(address: int) -> int:
    index, offset = divmod(address - POOL_START, OBJECT_BYTES)
    if offset or not 0 <= index < OBJECT_COUNT:
        raise ValueError(f"{address:08x} is not an object's start")
    return index


class Ambiguous(RuntimeError):
    """A pool address sits somewhere no structure explains. Rewriting or leaving it would both be a guess."""


@dataclass
class Relocation:
    data: bytes
    moved: dict[int, int]
    pointers: collections.Counter = field(default_factory=collections.Counter)
    raw_data: list[tuple] = field(default_factory=list)      # (place, offset, behavior, value): by value alone
    trailer: list[tuple] = field(default_factory=list)       # (trailer offset, value): pool addresses left in the trailer


def permutation(moves: list[tuple[int, int]]) -> np.ndarray:
    """Each object's new place. Each pair moves one object, named by where it sits now in the slot, to a place;
    whatever is there takes the place it left."""
    place = np.arange(OBJECT_COUNT)
    for obj, target in moves:
        here, there = int(place[object_index(obj)]), object_index(target)
        if here == there:
            continue
        holders_here, holders_there = place == here, place == there
        place[holders_here], place[holders_there] = there, here
    return place


def map_pointers(values: np.ndarray, place: np.ndarray) -> np.ndarray:
    """Pool addresses to their new place, keeping the offset inside the object. Everything else is untouched."""
    out = values.copy()
    hits = in_pool(values)
    offsets = values[hits].astype(np.int64) - POOL_START
    out[hits] = (POOL_START + place[offsets // OBJECT_BYTES] * OBJECT_BYTES + offsets % OBJECT_BYTES).astype(np.uint32)
    return out


def relocate(slot: bytes, moves: list[tuple[int, int]]) -> Relocation:
    """The same slot with these objects swapped, and every pointer to them following.

    Moving Mario's place: moves = [(slot's Mario, live Mario)]. Raises Ambiguous rather than guessing."""
    place = permutation(moves)
    if len(slot) != slots.SLOT_BYTES:
        raise ValueError("not a whole slot")
    body = _be_words(slot[:slots.BODY_BYTES]).copy()
    result = Relocation(b"", {int(POOL_START + i * OBJECT_BYTES): int(POOL_START + p * OBJECT_BYTES)
                              for i, p in enumerate(place.tolist()) if i != p})
    hits = np.flatnonzero(in_pool(body))
    pool_low, pool_high = (POOL_START - BODY_START) // 4, (POOL_END - BODY_START) // 4
    for word in hits.tolist():
        address, value = BODY_START + word * 4, int(body[word])
        name = _known_name(word, pool_low, pool_high, address)
        if name is None:
            if pool_low <= word < pool_high:
                index, offset = divmod(address - POOL_START, OBJECT_BYTES)
                if RAW_DATA[0] <= offset < RAW_DATA[1]:
                    behavior = int(body[pool_low + (index * OBJECT_BYTES + BEHAVIOR) // 4])
                    result.raw_data.append((index, offset, behavior, value))
                    result.pointers["rawData"] += 1
                    continue
            raise Ambiguous(f"{address:08x} holds {value:08x}: no structure says what that word is")
        result.pointers[name] += 1
    mapped = map_pointers(body, place)
    objects = mapped[pool_low:pool_high].reshape(OBJECT_COUNT, OBJECT_BYTES // 4).copy()
    moved = objects.copy()
    moved[place] = objects
    mapped[pool_low:pool_high] = moved.reshape(-1)
    out = bytearray(mapped.astype(">u4").tobytes()) + bytearray(slot[slots.BODY_BYTES:])
    occupied = struct.unpack_from(">I", slot, slots.TRAILER_MARIO)[0]
    if occupied:
        new = int(map_pointers(np.array([occupied], np.uint32), place)[0])
        struct.pack_into(">I", out, slots.TRAILER_MARIO, new)
    trailer = _be_words(slot[slots.BODY_BYTES:len(slot) // 4 * 4])
    result.trailer = [(int(w) * 4, int(trailer[w])) for w in np.flatnonzero(in_pool(trailer)) if int(w) != 0]
    result.data = bytes(out)
    return result


def _known_name(word: int, pool_low: int, pool_high: int, address: int) -> str | None:
    if pool_low <= word < pool_high:
        return OBJECT_POINTERS.get((address - POOL_START) % OBJECT_BYTES)
    if BODY_START <= address < BODY_START + slots.sim.MARIO_BYTES:
        return MARIO_POINTERS.get(address - BODY_START)
    if LIST_HEADS <= address < LIST_HEADS + LIST_HEAD_COUNT * LIST_HEAD_BYTES:
        return OBJECT_POINTERS.get((address - LIST_HEADS) % LIST_HEAD_BYTES)
    if FREE_LIST <= address < FREE_LIST + LIST_HEAD_BYTES:
        return OBJECT_POINTERS.get(address - FREE_LIST)
    return NAMED_GLOBALS.get(address)


def slot_mario(slot: bytes) -> int:
    return struct.unpack_from(">I", slot, slots.TRAILER_MARIO)[0]


# --- the simulator: Usamune's dispatcher is real, untouched and pressed through RAM --------------
sim = slots.sim
AFTER_SITE = slots.SITE + 8
CONTROLLERS, CONTROLLERS_BYTES = 0x8033AF90, 0x80
BUTTON_DOWN, BUTTON_PRESSED = CONTROLLERS + 0x10, CONTROLLERS + 0x12
SETTINGS_ON = slots.SETTINGS + 0x10
PRESS_LOAD, PRESS_SAVE = 0x080F, 0x1000
RESET_BUTTON = bytes([0x00, 0x20])                  # L: what the recording's own reset pressed
SECOND_CAMERA_FOCUS, CHILDREN = 0x8032DF30, 0x8038BD98
WARP_LIST_IN_AREA = 0x14                            # struct Area's warpNodes
OUTSIDE_POINTERS = ("sSoundRequests", "gSecondCameraFocus", "gObjParentGraphNode")


class Press:
    """Press buttons in RAM at the call site; take them away right after. The recordings hold no press."""

    def __init__(self, cpu: slots.SlotCpu):
        self.cpu = cpu
        self.after = None
        cpu.uc.hook_add(cpu.unicorn.UC_HOOK_CODE, self._after, begin=sim.sext(AFTER_SITE), end=sim.sext(AFTER_SITE))

    def _after(self, _uc, _address, _size, _user):
        if self.after is not None:
            action, self.after = self.after, None
            action()

    def press(self, down: int, pressed: int, then=None) -> None:
        cpu = self.cpu
        kept = cpu.u16(BUTTON_DOWN), cpu.u16(BUTTON_PRESSED)
        enable = cpu.u8(SETTINGS_ON)
        cpu.put_u8(SETTINGS_ON, enable or 1)
        cpu.put_u16(BUTTON_DOWN, kept[0] | down)
        cpu.put_u16(BUTTON_PRESSED, pressed)

        def undo() -> None:
            cpu.put_u16(BUTTON_DOWN, kept[0])
            cpu.put_u16(BUTTON_PRESSED, kept[1])
            cpu.put_u8(SETTINGS_ON, enable)
            if then is not None:
                then()
        self.after = undo


@dataclass
class Trip:
    replay: slots.Replay
    press: Press
    slot: int
    k: int
    j: int
    pool_k: np.ndarray
    after: list
    frame_j: object
    pause: slots.Snapshot
    saved: bytes
    natural: np.ndarray
    tap: np.ndarray
    identity: tuple
    moved_by_reset: tuple


def ride(session: Path, k: int, j: int, follow_frames: int, slot: int, reset_at: int | None = None) -> Trip:
    """Save at K through the real dispatcher, run on, pressing L at reset_at if asked, and pause just before J."""
    replay = slots.start_replay(session)
    cpu = replay.cpu
    press = Press(cpu)
    kept: dict = {}

    def save_at_k() -> None:
        cpu.put_u8(slots.SAVE_INDEX, slot)

        def done() -> None:
            kept["natural"] = cpu.ram.copy()
            low = sim.phys(slots.slot_address(slot))
            kept["slot"] = cpu.ram[low:low + slots.SLOT_BYTES].tobytes()
        press.press(PRESS_SAVE, PRESS_SAVE, done)

    pool_k, frames_after, identity, frame_j, before_reset = None, [], None, None, None
    for frame in replay.stream:
        replay.absorb(frame)
        if frame.index == k:
            cpu.pending = save_at_k
            identity = (frame.level, frame.area)
        if frame.index > k:
            frames_after.append(frame)
        if frame.index == j:
            frame_j = frame
        if frame.index < j:
            replay.feed(frame)
            if reset_at is not None and reset_at <= frame.index < reset_at + 4:
                held = RESET_BUTTON if frame.index < reset_at + 2 else bytes(2)
                cpu.pads = held + cpu.pads[2:]
            if reset_at == frame.index:
                before_reset = cpu.u32(slots.MARIO_OBJECT)
            ok = cpu.run_frame(replay.begin)
            replay.begin = sim.RESUME
            if not ok:
                raise SystemExit(f"frame {frame.index}: {cpu.violations[:3]}")
            cpu.scheduler_model()
        if frame.index == k:
            pool_k = replay.pool.copy()
            kept["tap"] = cpu.ram.copy()
        if frame.index >= k + follow_frames + 1 and frame.index >= j:
            break
    if "slot" not in kept or not slot_mario(kept["slot"]):
        raise SystemExit(f"Usamune's dispatcher did not save at frame {k}")
    following = [frame for frame in frames_after if frame.index <= k + follow_frames]
    return Trip(replay, press, slot, k, j, pool_k, following, frame_j, slots.Snapshot.of(replay), kept["slot"],
                kept["natural"], kept["tap"], identity, (before_reset, cpu.u32(slots.MARIO_OBJECT)))


def write_slot(cpu: slots.SlotCpu, index: int, data: bytes) -> None:
    """What the Trainer's writer does between frames: a slot's bytes, nothing else."""
    low = sim.phys(slots.slot_address(index))
    cpu.ram[low:low + slots.SLOT_BYTES] = np.frombuffer(data, np.uint8)


def press_load(trip: Trip, slot_data: bytes | None, at_load=None, before_frame=None, keep_pads: bool = False) -> dict:
    """Back to the pause, the slot bytes in place, frame J with a real load press."""
    replay, cpu = trip.replay, trip.replay.cpu
    trip.pause.back_to(replay)
    if slot_data is not None:
        write_slot(cpu, trip.slot, slot_data)
    if before_frame is not None:
        before_frame(cpu)
    seen: dict = {}

    def at_site() -> None:
        cpu.put_u8(slots.LOAD_INDEX, trip.slot)
        seen["ram_before"] = cpu.ram.copy()

        def done() -> None:
            seen["ram_after"] = cpu.ram.copy()
            if at_load is not None:
                at_load(cpu)
        trip.press.press(PRESS_LOAD, 0, done)
    cpu.pending = at_site
    replay.feed(trip.frame_j)
    seen["ran"] = cpu.run_frame(sim.RESUME)
    seen["violations"] = list(cpu.violations)
    if seen["ran"]:
        cpu.scheduler_model()
    return seen


def loaded(seen: dict) -> bool:
    changed = seen["ram_after"] != seen["ram_before"]
    low = sim.phys(BODY_START)
    return bool(changed[low:low + slots.BODY_BYTES].any())


def outside_pointers(ram: np.ndarray, natural: np.ndarray, place: np.ndarray, symbols: sim.Symbols) -> dict:
    """After a load: words outside the slot naming an object, that are not what frame K's world would say."""
    words = _be_words(ram)
    truth = map_pointers(_be_words(natural), place)
    stale = in_pool(words) & (words != truth)
    for low, high in ((sim.phys(BODY_START), sim.phys(BODY_START) + slots.BODY_BYTES),
                      (sim.phys(slots.SLOT_BASE), sim.RDRAM_BYTES), *[(lo, hi) for _n, lo, hi in slots.PRESENTATION]):
        stale[low // 4:high // 4] = False
    counts: collections.Counter = collections.Counter()
    for word in np.flatnonzero(stale).tolist():
        counts[sim.label(symbols, word * 4)] += 1
    return dict(counts.most_common(30))


def _map_back(ram: np.ndarray, place: np.ndarray) -> np.ndarray:
    """A RAM image with the swaps undone, to compare with what really happened."""
    inverse = np.argsort(place)
    words = map_pointers(_be_words(ram), inverse)
    low, high = sim.phys(POOL_START) // 4, sim.phys(POOL_END) // 4
    objects = words[low:high].reshape(OBJECT_COUNT, OBJECT_BYTES // 4).copy()
    moved = objects.copy()
    moved[inverse] = objects
    words[low:high] = moved.reshape(-1)
    return words.astype(">u4").view(np.uint8)


def follow_mapped(trip: Trip, place: np.ndarray, writes: dict | None = None, stick: bool = False) -> dict:
    """Play the pads recorded after K and compare each frame, with the swapped places mapped back."""
    replay, cpu = trip.replay, trip.replay.cpu
    pool = trip.pool_k.copy()
    first: dict[str, int] = {}
    labels: collections.Counter = collections.Counter()
    farthest, same_action, last_kinds, last_names = 0.0, 0, {}, {}
    for count, frame in enumerate(trip.after):
        for begin, data in frame.level_blocks:
            pool[begin - sim.POOL_BEGIN:begin - sim.POOL_BEGIN + data.size] = data
        if writes and count in writes:
            writes[count](cpu)
        replay.feed(frame)
        if stick and count == 0:
            cpu.perturb("stick", 0x7F)
        if not cpu.run_frame(sim.RESUME):
            return {"crashed_at": frame.index, "violations": cpu.violations[:3], "frames_ok": count, "first": first}
        cpu.scheduler_model()
        away, same = slots.mario_gap(cpu, frame)
        farthest, same_action = max(farthest, away), same_action + same
        mapped = _map_back(cpu.ram, place)
        pieces = [sim.diff_runs(mapped, data, begin) for begin, data in frame.spans if data.size != sim.TAIL_BYTES]
        pieces.append(sim.diff_runs(mapped, pool, sim.POOL_BEGIN))
        where = np.concatenate(pieces)
        groups: collections.Counter = collections.Counter()
        for address in where.tolist():
            groups[sim.label(cpu.symbols, address)] += 1
        labels.update(groups)
        last_kinds = slots.summarize(groups)
        last_names = dict(groups.most_common(40))
        for kind in last_kinds:
            first.setdefault(kind, frame.index)
    return {"frames_ok": len(trip.after), "farthest_units": farthest, "same_action_frames": same_action,
            "first": {name: first[name] for name in first if name in ("Mario", "objects", "camera", "main pool", "elsewhere")},
            "last_frame": last_kinds, "end_named": last_names, "totals": dict(labels.most_common(30))}


def describe(name: str, row: dict) -> None:
    if row.get("crashed_on_load_frame"):
        print(f"{name}: CRASH on the load frame {row['violations'][:1]}")
        return
    if "crashed_at" in row:
        print(f"{name}: CRASHED at frame {row['crashed_at']} after {row['frames_ok']} frames {row['violations'][:1]}")
        return
    print(f"{name}: {row['frames_ok']} frames; Mario at most {row['farthest_units']:.3f} units; same action {row['same_action_frames']}; "
          f"first {row['first']}; last frame {row['last_frame']}")


def run_row(trip: Trip, name: str, slot_data: bytes | None, place: np.ndarray, report: dict, at_load=None, writes=None, stick=False) -> dict:
    seen = press_load(trip, slot_data, at_load)
    row: dict = {}
    if not seen["ran"]:
        row.update(crashed_on_load_frame=True, violations=seen["violations"][:2])
    else:
        cpu = trip.replay.cpu
        row["loaded"] = loaded(seen)
        row["stale_outside_slot"] = outside_pointers(seen["ram_after"], trip.natural, place, cpu.symbols)
        row.update(follow_mapped(trip, place, writes, stick))
    report[name] = row
    describe(name, row)
    return row


def twin_moves(trip: Trip, cpu: slots.SlotCpu, data: bytes) -> tuple[list, dict]:
    """Move the slot's warp objects, and the camera's second focus, where live pointers already name them. Only the
    live game and the slot are read: a slot from Griffin's own save has no copy of frame K's nodes. An object is
    found by behavior and spawn parameters; frame K's own pointers check the rule."""
    area = cpu.u32(slots.CURRENT_AREA_POINTER)
    node = cpu.u32(area + WARP_LIST_IN_AREA)
    moves, check = [], {"nodes": 0, "matched": 0, "not_unique": 0, "agrees_with_k": 0, "focus": "no object"}
    body = _be_words(data[:slots.BODY_BYTES])
    pool_low = (POOL_START - BODY_START) // 4
    objects = body[pool_low:pool_low + OBJECT_COUNT * OBJECT_BYTES // 4].reshape(OBJECT_COUNT, OBJECT_BYTES // 4)
    natural_word = lambda address: struct.unpack_from(">I", trip.natural, sim.phys(address))[0]  # noqa: E731
    while 0x80000000 <= node < 0x80800000:
        live = cpu.u32(node + 4)
        if POOL_START <= live < POOL_END:
            check["nodes"] += 1
            behavior, params = cpu.u32(live + BEHAVIOR), cpu.u32(live + BEH_PARAMS)
            twins = np.flatnonzero((objects[:, BEHAVIOR // 4] == behavior) & (objects[:, BEH_PARAMS // 4] == params)
                                   & ((objects[:, ACTIVE_FLAGS // 4] >> 16) != 0))
            if twins.size == 1:
                twin = POOL_START + int(twins[0]) * OBJECT_BYTES
                moves.append((twin, live))
                check["matched"] += 1
                check["agrees_with_k"] += int(natural_word(node + 4) == twin)
            else:
                check["not_unique"] += 1
        node = cpu.u32(node + 8)
    focus = cpu.u32(SECOND_CAMERA_FOCUS)
    if POOL_START <= focus < POOL_END:
        behavior, params = cpu.u32(focus + BEHAVIOR), cpu.u32(focus + BEH_PARAMS)
        twins = np.flatnonzero((objects[:, BEHAVIOR // 4] == behavior) & (objects[:, BEH_PARAMS // 4] == params)
                               & ((objects[:, ACTIVE_FLAGS // 4] >> 16) != 0))
        check["focus"] = f"{twins.size} candidates; behavior {behavior:08x}; K's focus {natural_word(SECOND_CAMERA_FOCUS):08x}"
        if twins.size == 1:
            moves.append((POOL_START + int(twins[0]) * OBJECT_BYTES, focus))
    return moves, check


def prove(args) -> int:
    session = sim.lab_root() / args.session
    report: dict = {"session": args.session, "follow": args.follow}
    for k, j in args.pair:
        trip = ride(session, k, j, args.follow, args.slot, args.reset_at)
        cpu = trip.replay.cpu
        live = cpu.u32(slots.MARIO_OBJECT)
        ours = slot_mario(trip.saved)
        title = f"K={k} J={j}" + (f" L at {args.reset_at}" if args.reset_at else "")
        print(f"\n{title}: slot Mario {ours:08x}, live {live:08x}, level/area {trip.identity}")
        moved = relocate(trip.saved, [(ours, live)])
        place = permutation([(ours, live)])
        wm, check = twin_moves(trip, cpu, trip.saved)     # reads the live world: before any load below
        rows: dict = {"slot_mario": f"{ours:08x}", "live_mario": f"{live:08x}", "pointers_by_structure": dict(moved.pointers),
                      "raw_data_by_value": [(i, hex(o), hex(b), hex(v)) for i, o, b, v in moved.raw_data],
                      "trailer_pool_addresses": [(hex(o), hex(v)) for o, v in moved.trailer]}
        report[title] = rows
        print(f"  pointers by structure {dict(moved.pointers)}")
        print(f"  rawData by value {rows['raw_data_by_value']}; trailer addresses {rows['trailer_pool_addresses']}")
        natural_seed = trip.natural[sim.phys(slots.RANDOM_SEED):sim.phys(slots.RANDOM_SEED) + 2].copy()
        spans = ((sim.phys(slots.RANDOM_SEED), 2), (sim.phys(slots.GLOBAL_TIMER), 4), (sim.phys(slots.AREA_UPDATE_COUNTER), 4))
        pads = (sim.phys(CONTROLLERS), CONTROLLERS_BYTES)

        def put_back(cpu, chosen, natural=trip.natural) -> None:
            for low, size in chosen:
                cpu.ram[low:low + size] = natural[low:low + size]

        run_row(trip, "relocated, Usamune's load alone", moved.data, place, rows)
        # Not achievable without code: the load frame's own seed, clocks, controllers, to see the relocation by itself.
        run_row(trip, "relocated + seed+clocks at the load instant (needs code)", moved.data, place, rows,
                lambda c: put_back(c, spans))
        run_row(trip, "relocated + seed+clocks+pads at the load instant (needs code)", moved.data, place, rows,
                lambda c: put_back(c, (*spans, pads)))
        run_row(trip, "control: flipped stick, all restored", moved.data, place, rows, lambda c: put_back(c, (*spans, pads)), stick=True)

        # The warp nodes: a data-only cure is to put the slot's warp objects where those nodes already point.
        rows["warp_rule"] = check
        print(f"  warp objects by behavior+params: {check}")
        all_moves = [(ours, live), *[(a, b) for a, b in wm if b != live and a != ours]]
        rows["all_moves"] = [(f"{a:08x}", f"{b:08x}") for a, b in all_moves]
        wider = relocate(trip.saved, all_moves)
        wider_place = permutation(all_moves)
        run_row(trip, "relocated with warp objects, Usamune's load alone", wider.data, wider_place, rows)
        run_row(trip, "relocated with warp objects + seed+clocks+pads (needs code)", wider.data, wider_place, rows,
                lambda c: put_back(c, (*spans, pads)))

        # What a Trainer write can do: between frames, some frames after the load.
        for after in args.write_after:
            def write_true(cpu, after=after) -> None:
                truth = trip.after[after - 2] if after >= 2 else None
                source = trip.tap
                if truth is None:
                    put_back(cpu, spans, source)
                    return
                for low, size in spans:
                    for begin, data in truth.spans:
                        if data.size != sim.TAIL_BYTES and begin <= low and low + size <= begin + data.size:
                            cpu.ram[low:low + size] = data[low - begin:low - begin + size]
                            break

            def write_stored(cpu, after=after, seed=natural_seed) -> None:
                low = sim.phys(slots.RANDOM_SEED)
                cpu.ram[low:low + 2] = seed
                for at in (slots.GLOBAL_TIMER, slots.AREA_UPDATE_COUNTER):
                    stored = struct.unpack_from(">I", trip.tap, sim.phys(at))[0]
                    cpu.put_u32(at, stored + max(after - 1, 0))
            run_row(trip, f"relocated + Trainer writes the stored seed and clocks {after} frames later", moved.data, place, rows,
                    writes={after - 1: write_stored})
            run_row(trip, f"relocated + Trainer writes the true seed and clocks {after} frames later (oracle)", moved.data, place, rows,
                    writes={after - 1: write_true})
    write_report(args, report)
    return 0


def controls(args) -> int:
    session = sim.lab_root() / args.session
    report: dict = {"session": args.session}
    if args.moved:
        k, j = args.moved
        trip = ride(session, k, j, args.follow, args.slot)
        cpu = trip.replay.cpu
        ours, live = slot_mario(trip.saved), cpu.u32(slots.MARIO_OBJECT)
        seen = press_load(trip, None)
        report["no relocation"] = {"loaded": loaded(seen), "ran": seen["ran"]}
        print(f"no relocation, K={k} J={j} (slot {ours:08x}, live {live:08x}): Usamune's gate {'REFUSED' if not loaded(seen) else 'LOADED'}; frame ran {seen['ran']}")
        moved = relocate(trip.saved, [(ours, live)]).data
        for name, offset, change in (("wrong area", slots.TRAILER_AREA, 1), ("wrong level", slots.TRAILER_LEVEL, 1)):
            bad = bytearray(moved)
            struct.pack_into(">h", bad, offset, struct.unpack_from(">h", bad, offset)[0] + change)
            seen = press_load(trip, bytes(bad))
            report[name] = {"loaded": loaded(seen), "ran": seen["ran"]}
            print(f"relocated, {name}: Usamune's gate {'REFUSED' if not loaded(seen) else 'LOADED'}; frame ran {seen['ran']}")
        back = relocate(moved, [(live, ours)]).data
        report["relocating there and back is the same bytes"] = back == trip.saved
        print(f"relocating there and back gives the same bytes: {back == trip.saved}")
    if args.same:
        k, j = args.same
        trip = ride(session, k, j, args.follow, args.slot)
        cpu = trip.replay.cpu
        ours, live = slot_mario(trip.saved), cpu.u32(slots.MARIO_OBJECT)
        same = relocate(trip.saved, [(ours, live)]).data
        report["Mario already matches"] = {"slot": hex(ours), "live": hex(live), "bytes_unchanged": same == trip.saved}
        print(f"Mario matches (K={k} J={j}, slot {ours:08x}, live {live:08x}): relocating leaves the bytes unchanged: {same == trip.saved}")
    if args.wrong_level:
        k, j = args.wrong_level
        trip = ride(session, k, j, args.follow, args.slot)
        cpu = trip.replay.cpu
        ours, live = slot_mario(trip.saved), cpu.u32(slots.MARIO_OBJECT)
        moved = relocate(trip.saved, [(ours, live)]).data
        seen = press_load(trip, moved)
        report["truly another level"] = {"loaded": loaded(seen), "ran": seen["ran"]}
        print(f"truly another level (K={k} J={j}, slot {trip.identity}, live level {cpu.u16(slots.CURR_LEVEL)}): "
              f"gate {'REFUSED' if not loaded(seen) else 'LOADED'}; frame ran {seen['ran']}")
    write_report(args, report)
    return 0


def address(args) -> int:
    """Does play depend on where objects sit? Move them all at a frame boundary, in the whole RAM, and watch."""
    session = sim.lab_root() / args.session
    report: dict = {"session": args.session}
    for name, chooser in (("Mario swapped with one other object", None), ("every object moved at random", args.seed)):
        replay = slots.start_replay(session)
        cpu = replay.cpu
        place = np.arange(OBJECT_COUNT)
        first: dict[str, int] = {}
        farthest, last_kinds, frames_done = 0.0, {}, 0
        pool = replay.pool
        names: collections.Counter = collections.Counter()
        for frame in replay.stream:
            replay.absorb(frame)
            if frame.index == args.at + 1:
                mario = cpu.u32(slots.MARIO_OBJECT)
                if chooser is None:
                    other = POOL_START + (object_index(mario) + 7) % OBJECT_COUNT * OBJECT_BYTES
                    place = permutation([(mario, other)])
                else:
                    order = list(range(OBJECT_COUNT))
                    random.Random(chooser).shuffle(order)
                    place = np.array(order)
                _move_everything(cpu, place)
            replay.feed(frame)
            ok = cpu.run_frame(replay.begin)
            replay.begin = sim.RESUME
            if not ok:
                report[name] = {"crashed_at": frame.index, "violations": cpu.violations[:3]}
                print(f"{name}: CRASHED at {frame.index} {cpu.violations[:2]}")
                break
            cpu.scheduler_model()
            if frame.index <= args.at:
                continue
            frames_done += 1
            away, _same = slots.mario_gap(cpu, frame)
            farthest = max(farthest, away)
            mapped = _map_back(cpu.ram, place)
            pieces = [sim.diff_runs(mapped, data, begin) for begin, data in frame.spans if data.size != sim.TAIL_BYTES]
            pieces.append(sim.diff_runs(mapped, pool, sim.POOL_BEGIN))
            groups: collections.Counter = collections.Counter()
            for address_ in np.concatenate(pieces).tolist():
                groups[sim.label(cpu.symbols, address_)] += 1
            names.update(groups)
            last_kinds = slots.summarize(groups)
            for kind in last_kinds:
                first.setdefault(kind, frame.index)
            if frames_done >= args.frames:
                break
        else:
            pass
        report.setdefault(name, {}).update(frames=frames_done, farthest_units=farthest, first=first, last_frame=last_kinds,
                                           totals=dict(names.most_common(20)), mario_now=f"{cpu.u32(slots.MARIO_OBJECT):08x}")
        print(f"{name}, at {args.at}: {frames_done} frames; Mario at most {farthest:.3f} units; first {first}; last frame {last_kinds}")
        print(f"    named: {dict(names.most_common(14))}")
    write_report(args, report)
    return 0


def _move_everything(cpu: slots.SlotCpu, place: np.ndarray) -> None:
    """Move objects at a frame boundary: every RAM word naming one follows, by value, except the display buffers."""
    words = _be_words(cpu.ram)
    mapped = map_pointers(words, place)
    low, high = sim.phys(POOL_START) // 4, sim.phys(POOL_END) // 4
    objects = mapped[low:high].reshape(OBJECT_COUNT, OBJECT_BYTES // 4).copy()
    moved = objects.copy()
    moved[place] = objects
    mapped[low:high] = moved.reshape(-1)
    cpu.ram[:] = mapped.astype(">u4").view(np.uint8)
    cpu.uc.ctl_flush_tb()


def fixed_rng(args) -> int:
    """When does Usamune's fix-RNG setting (+0x25 = 1, value at +0x82) write the seed? Watch its write sites."""
    session = sim.lab_root() / args.session
    replay = slots.start_replay(session)
    cpu = replay.cpu
    press = Press(cpu)
    sites = {0x8040A950: "the routine at 0x8040A518", 0x80405048: "the routine at 0x8040501C"}
    frame_now = [0]
    writes: list = []
    for at, name in sites.items():
        def hook(_uc, _a, _s, _u, name=name) -> None:
            writes.append((frame_now[0], name))
        cpu.uc.hook_add(cpu.unicorn.UC_HOOK_CODE, hook, begin=sim.sext(at), end=sim.sext(at))
    start_reset = sim.phys(cpu.symbols["load_mario_area"])
    resets: list = []
    cpu.uc.hook_add(cpu.unicorn.UC_HOOK_CODE, lambda *_: resets.append(frame_now[0]),
                    begin=sim.sext(cpu.symbols["unload_mario_area"]), end=sim.sext(cpu.symbols["unload_mario_area"]))
    _ = start_reset
    value = 0x1234
    report: dict = {"session": args.session}
    press_frame = args.from_frame + 20
    results: dict = {}
    saved = {}
    for frame in replay.stream:
        frame_now[0] = frame.index
        replay.absorb(frame)
        if frame.index == args.from_frame:
            cpu.put_u8(slots.SETTINGS + 0x25, 1)
            cpu.put_u16(slots.SETTINGS + 0x82, value)
        if frame.index == press_frame - 10:
            def do_save() -> None:
                cpu.put_u8(slots.SAVE_INDEX, 3)
                press.press(PRESS_SAVE, PRESS_SAVE)
            cpu.pending = do_save
        if frame.index == press_frame:
            def do_load() -> None:
                cpu.put_u8(slots.LOAD_INDEX, 3)
                saved["before"] = cpu.u16(slots.RANDOM_SEED)

                def after() -> None:
                    saved["after"] = cpu.u16(slots.RANDOM_SEED)
                press.press(PRESS_LOAD, 0, after)
            cpu.pending = do_load
        replay.feed(frame)
        ok = cpu.run_frame(replay.begin)
        replay.begin = sim.RESUME
        if not ok:
            results["stopped"] = (frame.index, cpu.violations[:2])
            break
        cpu.scheduler_model()
        if frame.index >= args.to_frame:
            break
    report.update(fix_writes=writes, seed_at_load=saved, area_reloads=sorted(set(resets)), results=results)
    print(f"fix-RNG on from {args.from_frame}, value {value:#x}. Writes of the fixed value: {writes}; area reloads at {sorted(set(resets))}")
    print(f"a real load at {press_frame}: seed before {saved.get('before', 0):#06x}, after {saved.get('after', 0):#06x}; {results}")
    write_report(args, report)
    return 0


def write_report(args, report: dict) -> None:
    out = sim.lab_root() / f"handoff-sim-{args.out or 'relocate'}" / "report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, default=str), encoding="utf-8")
    print(f"report: {out}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, handler in (("prove", prove), ("controls", controls), ("address", address), ("fixed-rng", fixed_rng)):
        command = commands.add_parser(name)
        command.add_argument("session")
        command.add_argument("--follow", type=int, default=300)
        command.add_argument("--slot", type=int, default=3)
        command.add_argument("--out")
        command.set_defaults(handler=handler)
        if name == "prove":
            command.add_argument("--pair", nargs=2, type=int, action="append", required=True, metavar=("K", "J"))
            command.add_argument("--reset-at", type=int)
            command.add_argument("--write-after", nargs="*", type=int, default=[])
        elif name == "controls":
            for flag in ("--moved", "--same", "--wrong-level"):
                command.add_argument(flag, nargs=2, type=int, metavar=("K", "J"))
        elif name == "address":
            command.add_argument("--at", type=int, required=True)
            command.add_argument("--seed", type=int, default=7)
            command.add_argument("--frames", type=int, default=600)
        else:
            command.add_argument("--from", dest="from_frame", type=int, required=True)
            command.add_argument("--to", dest="to_frame", type=int, required=True)
    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
