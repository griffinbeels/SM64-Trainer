"""Is a start someone else made safe to run, and is a save state built from it safe to write? (Ghost Handoff)

A downloaded start becomes game state in the simulation and, through the Save states row, in a Usamune slot of
his real game. The game follows pointers in that state: an object's behaviour pointer and behaviour command
pointer name the script the behaviour interpreter runs (and a script can call native code), its graph node is
drawn through node functions, and some globals are function pointers. A crafted start could aim any of them
at bytes laid out as commands, so before anything from another machine is simulated or written, every word
of it that is a pointer must point where the game's own state points, for this ROM build:

    behaviour        0, or the start of one of the ROM's behaviour scripts (the map's bhv* symbols, placed at
                     the reference's segment 0x13 base)
    command, stack   0, or inside those behaviour scripts (word aligned)
    graph node       0, or a node the reference's own objects or gLoadedGraphNodes table name
    collision        0, or a collision table one of the reference's objects uses
    list links       an object of the pool (at its start) or one of gObjectListArray's heads
    any other word   never into code (the game's, the menus', Usamune's image) unless the reference holds the
                     same word at the same address (a function pointer the game itself keeps); never into a
                     stack, a frame buffer or Usamune's slots
The reference is a practice reset made locally from his own ROM (stage_sim.py), so the checks need nothing
from the start's maker. A downloaded start's main-pool changes are never applied: the main pool holds ROM-made
scripts and models, so a downloaded start runs over the reference's own pool (Mario and every object stay exact
that way, measured 2026-11-08), and only its task regions are checked and used.

    reasons = check_state(ram, reference)       # [] or the plain sentences, first ones first
    reasons = check_slot(slot_bytes, reference) # the same rules over a slot's body before it is written
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

import frame_sim as sim

OBJECT_BYTES, OBJECT_COUNT = sim.OBJECT_BYTES, sim.OBJECT_COUNT
LIST_COUNT, NODE_BYTES = 13, 0x68
FIELD = {"next": 0x60, "prev": 0x64, "graph": 0x14, "behavior": 0x20C, "command": 0x208, "stack": 0x1D0,
         "stack_end": 0x1F0, "collision": 0x218}
USAMUNE_IMAGE_BEGIN = 0x400000             # where Usamune's entry code copies its image (cold_boot.USAMUNE_IMAGE)
# Not the exception vectors: 0x80000000 is also the float -0.0, which matrices and speeds hold all the time.
# The US build's; never_ranges() reads each build's own from its map (JP's stacks sit 0x400 higher).
NEVER = ((0x1FF200, 0x207D00), (0x38F800, 0x400000), (0x500000, 0x800000))
#        thread stacks          frame buffers       Usamune's slots and above


def never_ranges(symbols) -> tuple[tuple[int, int], ...]:
    """Physical [begin, end) a start's word may never point into: the thread stacks (gUnusedThread2Stack up to
    gGfxPools), the frame buffers (gFrameBuffer0 up to Usamune's image), Usamune's slots and above. A map without
    those names (a test's) keeps the US build's."""
    names = ("gUnusedThread2Stack", "gGfxPools", "gFrameBuffer0")
    if not all(name in getattr(symbols, "by_name", {}) for name in names):
        return NEVER
    return ((sim.phys(symbols["gUnusedThread2Stack"]), sim.phys(symbols["gGfxPools"])),
            (sim.phys(symbols["gFrameBuffer0"]), USAMUNE_IMAGE_BEGIN), (0x500000, 0x800000))
BEHAVIOR_TAIL = 0x400                       # past the last bhv* symbol, for its own script


def usamune_image(version: str) -> tuple[int, int]:
    """Physical [begin, end) of Usamune's code and data below its state block, which count as code here. The
    end is that build's state block (memory/layout.py's usamune_state_start; where that row is not verified
    yet, JP, its own definition: usamune_stop - 1): US 0x417B4C, JP 0x417C14."""
    from sm64_events.memory.layout import layout_for  # noqa: PLC0415
    layout = layout_for(version)
    start = layout.usamune_state_start
    if start is None and layout.usamune_stop is not None:
        start = layout.usamune_stop - 1
    if start is None:
        raise ValueError(f"The {version.upper()} layout does not place Usamune's state block.")
    return USAMUNE_IMAGE_BEGIN, sim.phys(start)


def code_ranges(map_path: Path, version: str = "us") -> list[tuple[int, int]]:
    """Physical [begin, end) of every .text section the map names, merged, plus Usamune's image."""
    text = Path(map_path).read_text(encoding="utf-8", errors="replace")
    spans = sorted((int(a, 16), int(a, 16) + int(n, 16)) for name, a, n in
                   re.findall(r"^ (\.text\S*)\s+0x0000000080([0-9a-f]{6})\s+0x([0-9a-f]+)\s", text, re.M) if int(n, 16))
    merged: list[list[int]] = []
    for low, high in spans:
        if merged and low <= merged[-1][1] + 0x10:
            merged[-1][1] = max(merged[-1][1], high)
        else:
            merged.append([low, high])
    # The menus' and the head's code load into the main pool only while those screens run; in a level the same
    # addresses hold the level's own data, so they are not code here (the pool itself is never taken from a start).
    pool = (sim.POOL_BEGIN, sim.POOL_BEGIN + sim.POOL_BYTES)
    return [tuple(span) for span in merged if not (pool[0] <= span[0] and span[1] <= pool[1])] + [usamune_image(version)]


def behavior_offsets(map_path: Path) -> list[int]:
    text = Path(map_path).read_text(encoding="utf-8", errors="replace")
    return sorted(int(a, 16) for a in re.findall(r"^\s+0x0000000013([0-9a-f]{6})\s+bhv\w+\s*$", text, re.M))


def _words(ram: np.ndarray, low: int, high: int) -> np.ndarray:
    return np.frombuffer(ram[low:high].tobytes(), ">u4").astype(np.int64)


def _u32(ram: np.ndarray, virtual: int) -> int:
    return struct.unpack_from(">I", ram, sim.phys(virtual))[0]


@dataclass
class Reference:
    """What the game's own state points at, from a practice reset made locally from his ROM."""
    ram: np.ndarray
    symbols: sim.Symbols
    code: list
    behaviors: set = field(default_factory=set)
    scripts: tuple = (0, 0)
    graphs: set = field(default_factory=set)
    collisions: set = field(default_factory=set)
    heads: set = field(default_factory=set)

    @classmethod
    def of(cls, ram: np.ndarray, symbols: sim.Symbols, map_path: Path, version: str = "us") -> Reference:
        reference = cls(ram, symbols, code_ranges(map_path, version))
        base = _u32(ram, symbols["sSegmentTable"] + 0x13 * 4) | 0x80000000
        offsets = behavior_offsets(map_path)
        reference.behaviors = {base + offset for offset in offsets}
        reference.scripts = (base, base + offsets[-1] + BEHAVIOR_TAIL)
        pool = sim.phys(symbols["gObjectPool"])
        objects = _words(ram, pool, pool + OBJECT_BYTES * OBJECT_COUNT).reshape(OBJECT_COUNT, OBJECT_BYTES // 4)
        reference.graphs = set(objects[:, FIELD["graph"] // 4].tolist()) | {0}
        reference.collisions = set(objects[:, FIELD["collision"] // 4].tolist()) | {0}
        table = _u32(ram, symbols["gLoadedGraphNodes"])
        if table:
            reference.graphs |= set(_words(ram, sim.phys(table), sim.phys(table) + 0x400).tolist())
        lists = symbols["gObjectListArray"]
        reference.heads = {lists + i * NODE_BYTES for i in range(16)}
        return reference

    def in_code(self, value: int) -> bool:
        at = value & 0x1FFFFFFF
        return any(low <= at < high for low, high in self.code)


def _is_pointer(value: int) -> bool:
    return 0x80000000 <= value < 0x80800000


def live_slots(objects: np.ndarray, heads: list[int], reference: Reference) -> tuple[list[int], list[str]]:
    """The pool slots the object lists reach (the objects the game runs), walking each list from its head with
    a bound; a link that leaves the pool or a list that never closes is a reason."""
    pool = reference.symbols["gObjectPool"]
    pool_end = pool + OBJECT_BYTES * OBJECT_COUNT
    found, reasons = [], []
    for head, first in enumerate(heads):
        node, steps = first, 0
        while node != reference.symbols["gObjectListArray"] + head * NODE_BYTES:
            if not (pool <= node < pool_end and (node - pool) % OBJECT_BYTES == 0) or steps > OBJECT_COUNT:
                reasons.append(f"Object list {head} leaves the object pool or never closes.")
                break
            slot = (node - pool) // OBJECT_BYTES
            if slot in found:
                reasons.append(f"Object list {head} visits an object twice.")
                break
            found.append(slot)
            node, steps = int(objects[slot, FIELD["next"] // 4]), steps + 1
    return found, reasons


def check_objects(objects: np.ndarray, heads: list[int], reference: Reference) -> list[str]:
    """Each object the lists reach (rows of u32 words, big-endian values), by the rules above."""
    pool = reference.symbols["gObjectPool"]
    pool_end = pool + OBJECT_BYTES * OBJECT_COUNT
    low, high = reference.scripts
    slots, reasons = live_slots(objects, heads, reference)
    for slot in slots:
        row = objects[slot].tolist()
        behavior, command = row[FIELD["behavior"] // 4], row[FIELD["command"] // 4]
        if behavior not in reference.behaviors and behavior != 0:
            reasons.append(f"Object {slot} names a behaviour this game does not have.")
        if _is_pointer(command) and not (low <= command < high and command % 4 == 0):
            reasons.append(f"Object {slot}'s behaviour runs from outside the game's behaviour scripts.")
        for value in row[FIELD["stack"] // 4:FIELD["stack_end"] // 4]:
            if value and _is_pointer(value) and not (low <= value < high):
                reasons.append(f"Object {slot}'s behaviour would return outside the game's behaviour scripts.")
                break
        if row[FIELD["graph"] // 4] not in reference.graphs:
            reasons.append(f"Object {slot} would be drawn with a model this level does not load.")
        if row[FIELD["collision"] // 4] not in reference.collisions:
            reasons.append(f"Object {slot} uses collision this level does not load.")
        for name in ("next", "prev"):
            link = row[FIELD[name] // 4]
            if link and link not in reference.heads and not (pool <= link < pool_end and (link - pool) % OBJECT_BYTES == 0):
                reasons.append(f"Object {slot}'s list link points outside the object pool.")
    return reasons


def check_words(words: np.ndarray, begin: int, reference: Reference) -> list[str]:
    """Any word of a region (physical begin, big-endian u32 values) that points into code, a stack, a frame
    buffer or Usamune's slots, where the reference holds another word at that address."""
    trusted = _words(reference.ram, begin, begin + words.size * 4)
    reasons = []
    for index in np.flatnonzero((words != trusted) & (words >= 0x80000000) & (words < 0x80800000)).tolist():
        value = int(words[index])
        at = value & 0x1FFFFFFF
        if reference.in_code(value) or any(low <= at < high for low, high in never_ranges(reference.symbols)):
            name = reference.symbols.name_of(0x80000000 | (begin + index * 4))
            reasons.append(f"The start points {name} at the game's code or another protected place.")
    return reasons


def check_state(ram: np.ndarray, reference: Reference, spans: list[tuple[int, int]]) -> list[str]:
    """A whole simulated start: every object, and every word of the regions it brought (spans)."""
    pool = sim.phys(reference.symbols["gObjectPool"])
    objects = _words(ram, pool, pool + OBJECT_BYTES * OBJECT_COUNT).reshape(OBJECT_COUNT, OBJECT_BYTES // 4)
    lists = sim.phys(reference.symbols["gObjectListArray"])
    heads = [int(w) for w in _words(ram, lists, lists + LIST_COUNT * NODE_BYTES).reshape(LIST_COUNT, NODE_BYTES // 4)[:, FIELD["next"] // 4]]
    reasons = check_objects(objects, heads, reference)
    for begin, size in spans:
        reasons += check_words(_words(ram, begin, begin + size), begin, reference)
    return reasons


def check_slot(slot: bytes, reference: Reference, body_bytes: int) -> list[str]:
    """A save state's body before it goes into a slot of his game: the same rules over the bytes Usamune copies."""
    begin = sim.phys(reference.symbols["gMarioStates"])
    body = np.frombuffer(slot[:body_bytes], ">u4").astype(np.int64)
    pool = sim.phys(reference.symbols["gObjectPool"]) - begin
    objects = body[pool // 4:(pool + OBJECT_BYTES * OBJECT_COUNT) // 4].reshape(OBJECT_COUNT, OBJECT_BYTES // 4)
    lists = sim.phys(reference.symbols["gObjectListArray"]) - begin
    heads = [int(w) for w in body[lists // 4:(lists + LIST_COUNT * NODE_BYTES) // 4].reshape(LIST_COUNT, NODE_BYTES // 4)[:, FIELD["next"] // 4]]
    return check_objects(objects, heads, reference) + check_words(body, begin, reference)
