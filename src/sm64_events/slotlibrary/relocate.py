"""Move a slot's Mario to the object place the running game gives him, by rewriting slot data only.

A practice reset reloads the area without clearing the object pool, so Mario's object lands in a place that
depends on play history. Usamune's load refuses a slot whose gMarioObject differs from the running game's.
Griffin's rule (2026-11-05): no code in the game, Usamune untouched. So the slot's own bytes change instead:
Mario swaps places with whatever the slot holds where the running game keeps him, and every pointer to either
follows. Usamune's own load then runs unmodified, inside Mario's update, and the object loop's pointer still
names Mario afterward.

Pointers are found by structure: object fields, Mario's struct, the object list heads, the free list and the
three object globals after gMarioObject. rawData is a union nothing here can read by structure; a pool address
there is mapped by value and reported. A pool address anywhere else refuses the relocation: guessing could
leave the game holding a pointer to the wrong object.

Two more moves keep pointers outside the slot honest after a load: the area's warp nodes and gSecondCameraFocus
name objects of the running world. Each named object's twin in the slot (same behavior, same spawn parameters,
active, and the only one) moves into that place. Evidence, simulation only (2026-11-05,
tools/scene_lab/handoff_relocate.py): the load runs and Mario follows the recording on nine pairs; the twin rule
matched all five warp nodes on every pair and agreed with frame K's own nodes.
"""
from __future__ import annotations

import struct
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from sm64_events.memory.addresses import (
    MARIO_HELD_OBJ_OFF,
    MARIO_INTERACT_OBJ_OFF,
    MARIO_USED_OBJ_OFF,
    OBJECT_BEHAVIOR,
    OBJECT_COUNT,
    OBJECT_SIZE,
)
from sm64_events.slotlibrary.usamune import (
    BODY_BYTES,
    SLOT_BYTES,
    TRAILER_MARIO,
    US,
    Build,
    GameMemory,
    Refusal,
)

POOL_BYTES = OBJECT_SIZE * OBJECT_COUNT

# Where the slot body (a copy of the game from gMarioStates on) holds the globals that name objects. These are
# offsets into Usamune's slot format, the same in both builds like the trailer's (usamune.py).
POOL_IN_BODY = US.object_pool - US.body_start                            # gObjectPool
LIST_HEADS_IN_BODY, LIST_HEAD_COUNT, LIST_HEAD_BYTES = 0x1A70, 13, 0x68   # gObjectListArray (STROOP map)
FREE_LIST_IN_BODY = 0x25F80                                              # gFreeObjectList: one ObjectNode
MARIO_OBJECT_IN_BODY = US.mario_object - US.body_start
OBJECT_GLOBALS_IN_BODY = {MARIO_OBJECT_IN_BODY: "gMarioObject",          # gLuigiObject and gCurrentObject
                          MARIO_OBJECT_IN_BODY + 4: "gLuigiObject",       # follow it in the map
                          MARIO_OBJECT_IN_BODY + 8: "gCurrentObject"}
MARIO_RIDDEN_OBJ_OFF, MARIO_MARIO_OBJ_OFF = 0x84, 0x88
MARIO_STRUCT_BYTES = 0xC8

# struct Object fields that hold object pointers (header.gfx is a GraphNodeObject, then ObjectNode next/prev).
OBJECT_POINTERS = {0x04: "gfx.prev", 0x08: "gfx.next", 0x0C: "gfx.parent", 0x10: "gfx.children",
                   0x50: "gfx.throwMatrix", 0x60: "next", 0x64: "prev", 0x68: "parentObj", 0x6C: "prevObj",
                   0x78: "collidedObjs[0]", 0x7C: "collidedObjs[1]", 0x80: "collidedObjs[2]",
                   0x84: "collidedObjs[3]", 0x214: "platform"}
MARIO_POINTERS = {MARIO_INTERACT_OBJ_OFF: "interactObj", MARIO_HELD_OBJ_OFF: "heldObj",
                  MARIO_USED_OBJ_OFF: "usedObj", MARIO_RIDDEN_OBJ_OFF: "riddenObj", MARIO_MARIO_OBJ_OFF: "marioObj"}
RAW_DATA = (0x88, 0x1C8)
OBJECT_BEH_PARAMS, OBJECT_ACTIVE_FLAGS = 0x188, 0x74
AREA_WARP_NODES = 0x14                     # struct Area: the ObjectWarpNode list
WARP_NODE_OBJECT, WARP_NODE_NEXT = 4, 8    # struct ObjectWarpNode {WarpNode node; Object *object; next}
WARP_NODE_LIMIT = 64


class Ambiguous(Refusal):
    """A pool address sits where no structure says what it is. Nothing was written."""


@dataclass
class Relocated:
    data: bytes
    moved: dict[int, int] = field(default_factory=dict)       # object address -> its new address
    raw_data: list[tuple[int, int, int]] = field(default_factory=list)   # (place, offset, behavior): by value only
    pointers: Counter = field(default_factory=Counter)                    # how many of each kind were found


def _words(data: bytes) -> np.ndarray:
    return np.frombuffer(data, ">u4").astype(np.uint32)


def in_pool(values: np.ndarray, pool: int) -> np.ndarray:
    return (values >= pool) & (values < pool + POOL_BYTES)


def object_index(address: int, pool: int) -> int:
    index, offset = divmod(address - pool, OBJECT_SIZE)
    if offset or not 0 <= index < OBJECT_COUNT:
        raise ValueError(f"{address:08x} is not an object's start")
    return index


def permutation(moves: list[tuple[int, int]], pool: int) -> np.ndarray:
    """Each object's new place. A move names an object by where the slot holds it, and the place it goes;
    whatever is there takes the place it left."""
    place = np.arange(OBJECT_COUNT)
    for obj, target in moves:
        here, there = int(place[object_index(obj, pool)]), object_index(target, pool)
        if here != there:
            holders_here, holders_there = place == here, place == there
            place[holders_here], place[holders_there] = there, here
    return place


def map_pointers(values: np.ndarray, place: np.ndarray, pool: int) -> np.ndarray:
    """Pool addresses to their new place, keeping the offset inside the object."""
    out = values.copy()
    hits = in_pool(values, pool)
    offsets = values[hits].astype(np.int64) - pool
    out[hits] = (pool + place[offsets // OBJECT_SIZE] * OBJECT_SIZE + offsets % OBJECT_SIZE).astype(np.uint32)
    return out


def _structure(word: int) -> str | None:
    """What a body word is, by where it sits, or None. Offsets only, so the same in both builds."""
    at = word * 4
    if POOL_IN_BODY <= at < POOL_IN_BODY + POOL_BYTES:
        return OBJECT_POINTERS.get((at - POOL_IN_BODY) % OBJECT_SIZE)
    if at < MARIO_STRUCT_BYTES:
        return MARIO_POINTERS.get(at)
    if LIST_HEADS_IN_BODY <= at < LIST_HEADS_IN_BODY + LIST_HEAD_COUNT * LIST_HEAD_BYTES:
        return OBJECT_POINTERS.get((at - LIST_HEADS_IN_BODY) % LIST_HEAD_BYTES)
    if FREE_LIST_IN_BODY <= at < FREE_LIST_IN_BODY + LIST_HEAD_BYTES:
        return OBJECT_POINTERS.get(at - FREE_LIST_IN_BODY)
    return OBJECT_GLOBALS_IN_BODY.get(at)


def relocate(data: bytes, moves: list[tuple[int, int]], build: Build) -> Relocated:
    """The same slot of this build with these objects moved and every pointer to them following. Raises Ambiguous."""
    if len(data) != SLOT_BYTES:
        raise Refusal(f"a slot is {SLOT_BYTES} bytes, this one {len(data)}")
    pool = build.object_pool
    place = permutation(moves, pool)
    body = _words(data[:BODY_BYTES])
    pool_low = POOL_IN_BODY // 4
    found = Relocated(b"")
    for word in np.flatnonzero(in_pool(body, pool)).tolist():
        name = _structure(word)
        if name is not None:
            found.pointers[name] += 1
            continue
        offset = (word - pool_low) * 4 % OBJECT_SIZE
        index = (word - pool_low) * 4 // OBJECT_SIZE
        if pool_low <= word < pool_low + POOL_BYTES // 4 and RAW_DATA[0] <= offset < RAW_DATA[1]:
            behavior = int(body[pool_low + (index * OBJECT_SIZE + OBJECT_BEHAVIOR) // 4])
            found.raw_data.append((index, offset, behavior))
            found.pointers["rawData"] += 1
            continue
        raise Ambiguous(f"{build.body_start + word * 4:08x} holds {int(body[word]):08x}, and nothing says what that word is")
    mapped = map_pointers(body, place, pool)
    objects = mapped[pool_low:pool_low + POOL_BYTES // 4].reshape(OBJECT_COUNT, OBJECT_SIZE // 4).copy()
    moved = objects.copy()
    moved[place] = objects
    mapped[pool_low:pool_low + POOL_BYTES // 4] = moved.reshape(-1)
    out = bytearray(mapped.astype(">u4").tobytes()) + bytearray(data[BODY_BYTES:])
    occupied = struct.unpack_from(">I", data, TRAILER_MARIO)[0]
    if occupied:
        struct.pack_into(">I", out, TRAILER_MARIO, int(map_pointers(np.array([occupied], np.uint32), place, pool)[0]))
    found.data = bytes(out)
    found.moved = {pool + i * OBJECT_SIZE: pool + int(p) * OBJECT_SIZE
                   for i, p in enumerate(place.tolist()) if i != p}
    return found


def _word(memory: GameMemory, address: int) -> int:
    return int.from_bytes(memory.read(address, 4), "big")


def _twin(objects: np.ndarray, memory: GameMemory, live: int, pool: int) -> int | None:
    """The slot's only active object with this live object's behavior and spawn parameters."""
    behavior, params = _word(memory, live + OBJECT_BEHAVIOR), _word(memory, live + OBJECT_BEH_PARAMS)
    twins = np.flatnonzero((objects[:, OBJECT_BEHAVIOR // 4] == behavior)
                           & (objects[:, OBJECT_BEH_PARAMS // 4] == params)
                           & ((objects[:, OBJECT_ACTIVE_FLAGS // 4] >> 16) != 0))
    return pool + int(twins[0]) * OBJECT_SIZE if twins.size == 1 else None


def live_named_objects(memory: GameMemory, build: Build) -> list[int]:
    """Objects the running game names from outside a slot: each warp node's object, and the camera's second focus."""
    named = []
    area = _word(memory, build.current_area_pointer)
    node = _word(memory, area + AREA_WARP_NODES) if area else 0
    for _ in range(WARP_NODE_LIMIT):
        if not 0x80000000 < node < 0x80800000:
            break
        named.append(_word(memory, node + WARP_NODE_OBJECT))
        node = _word(memory, node + WARP_NODE_NEXT)
    named.append(_word(memory, build.second_camera_focus))
    pool = build.object_pool
    return [address for address in named if pool <= address < pool + POOL_BYTES and (address - pool) % OBJECT_SIZE == 0]


def to_live_place(memory: GameMemory, data: bytes, build: Build) -> bytes:
    """What the write path calls: the slot's bytes, made for the running build, with Mario where the running game
    has him. A slot whose Mario already matches, or an empty one, comes back unchanged. Raises Ambiguous."""
    if len(data) != SLOT_BYTES:
        raise Refusal(f"a slot is {SLOT_BYTES} bytes, this one {len(data)}")
    ours = struct.unpack_from(">I", data, TRAILER_MARIO)[0]
    live = _word(memory, build.mario_object)
    if ours == 0 or live == 0 or ours == live:
        return data
    pool = build.object_pool
    if not (pool <= live < pool + POOL_BYTES and (live - pool) % OBJECT_SIZE == 0):
        raise Refusal(f"the game's gMarioObject {live:08x} is not an object")
    object_index(ours, pool)
    return relocate(data, planned_moves(memory, data, ours, live, build), build).data


def planned_moves(memory: GameMemory, data: bytes, ours: int, live: int, build: Build) -> list[tuple[int, int]]:
    """Mario to the live place first, then each live-named object's twin into that object's place."""
    pool_low = POOL_IN_BODY // 4
    objects = _words(data[:BODY_BYTES])[pool_low:pool_low + POOL_BYTES // 4].reshape(OBJECT_COUNT, OBJECT_SIZE // 4)
    moves = [(ours, live)]
    for named in live_named_objects(memory, build):
        twin = _twin(objects, memory, named, build.object_pool)
        if twin is not None and named != live and twin != ours and all(target != named for _o, target in moves):
            moves.append((twin, named))
    return moves
