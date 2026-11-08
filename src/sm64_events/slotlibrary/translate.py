"""A save state made by one Usamune build, rewritten to be the same moment in the other build: data only.

Griffin's words (2026-11-08): "I'm not sure if JP <-> US, or US <-> JP works, but we shouldn't make the
assumption that it doesn't (or that there's no way to translate between the two)." Both builds copy the same
bytes into a slot (slotlibrary/usamune.py), so only pointer values differ, and each is mapped by what it points at:

- game memory a symbol names: by that symbol, through build_shifts.tsv (STROOP's maps; tools/import_stroop_maps.py)
- a behaviour script: by its name (memory/behaviours.py), at the running game's own behaviour segment
- the main pool: through the level's atlas (slotlibrary/atlas.py), else by the paired pool block
- Usamune's own data: by the difference between the two builds' settings bases
- a segmented address: only where structure says one sits (object rawData and gPlayerSpawnInfos; scanning every
  value as one moved Mario 235 units in the study)
- the game's own memory layout (the segment table, the main pool's bounds and free space, the display list heap):
  taken from the running game, because a load restores the slot but never the pool

An object whose behaviour the running build lacks cannot run there, so it is removed the way the game removes
one (`spawn_object.c::unload_object`): taken off its list and put on the free list, inactive and not drawn. The
report names it, and the running world's own object at that spawn point when the atlas holds one.

Evidence, simulation only (2026-11-08 study): translated slots load through each build's own Usamune load, both
ways, and play on with Mario 0.000 units off for 300 frames in the castle grounds, BoB and JRB; his own BoB moment
in a cold JP game matched his recording, Mario and all 240 objects. A pointer that maps to nothing is reported as
unmapped, never guessed; the write path refuses a state with any.
"""
from __future__ import annotations

import bisect
import struct
from collections import Counter
from dataclasses import dataclass
from functools import cache
from importlib import resources

import numpy as np

from sm64_events.memory import behaviours
from sm64_events.memory.addresses import OBJECT_BEHAVIOR, OBJECT_COUNT, OBJECT_HOME_POS, OBJECT_SIZE
from sm64_events.slotlibrary.atlas import SEGMENTS, Atlas, World, pool_blocks
from sm64_events.slotlibrary.relocate import (
    FREE_LIST_IN_BODY,
    LIST_HEAD_BYTES,
    LIST_HEAD_COUNT,
    LIST_HEADS_IN_BODY,
    OBJECT_ACTIVE_FLAGS,
    POOL_BYTES,
    POOL_IN_BODY,
    RAW_DATA,
)
from sm64_events.slotlibrary.usamune import BODY_BYTES, SLOT_BYTES, Build, GameMemory, Refusal, live_facts, slot_facts

ADDRESSES = (0x80000400, 0x80800000)
USAMUNE_MEMORY = (0x80400000, 0x80424000)
ABOVE_USAMUNE = 0x80500000
BEHAVIOUR_SEGMENT, SCRIPT_SPAN = 0x13, 0x10000
# Where the slot body holds the game's own memory layout (STROOP maps; the same offsets in both builds).
SEGMENT_TABLE_IN_BODY = 0x290                                   # sSegmentTable
POOL_START_IN_BODY, POOL_END_IN_BODY = 0x314, 0x318             # sPoolStart, sPoolEnd
HEAD_L_IN_BODY, HEAD_R_IN_BODY = 0x31C, 0x320                   # sPoolListHeadL, sPoolListHeadR
LIVE_FIELDS = ((SEGMENT_TABLE_IN_BODY, SEGMENTS * 4), (0x310, 4), (POOL_START_IN_BODY, 4), (POOL_END_IN_BODY, 4),
               (HEAD_L_IN_BODY, 4), (HEAD_R_IN_BODY, 4), (0x1218, 4))   # + sPoolFreeSpace, gDisplayListHeap
SPAWN_INFOS_IN_BODY, SPAWN_INFOS_BYTES = 0x340, 0x20             # gPlayerSpawnInfos
GFX_FLAGS, GFX_THROW_MATRIX, OBJECT_NEXT, OBJECT_PREV, OBJECT_PREV_OBJ = 0x02, 0x50, 0x60, 0x64, 0x6C
GRAPH_LINKS = frozenset({0x04, 0x08, 0x0C, 0x10})               # the scene graph keeps an unloaded object too
NOT_DRAWN = ~0x0005 & 0xFFFF                                    # GRAPH_RENDER_ACTIVE and GRAPH_RENDER_BILLBOARD off
SAME_SPOT = 1.0                                                 # units: a spawn point is the object's identity
UNMAPPED = frozenset({"behaviour script, unmapped", "main pool, between blocks", "main pool, no block in the target",
                      "static, unnamed", "segmented, unmapped"})


@cache
def _shifts(source: str) -> tuple[list[int], list[int]]:
    starts, deltas = [], []
    table = resources.files("sm64_events.data") / "build_shifts.tsv"   # package data, frozen or not
    for line in table.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            version, start, delta, _symbol = line.split("\t")
            if version == source:
                starts.append(int(start, 16))
                deltas.append(int(delta, 16))
    if not starts:
        raise Refusal(f"no shift table for {source.upper()}")
    return starts, deltas


@cache
def _scripts(version: str) -> tuple[list[tuple[int, str]], dict[str, int]]:
    """This build's behaviour scripts: (offset in segment 0x13, name) sorted, and name -> offset."""
    table = {name: offset & 0xFFFFFF for name, offset in behaviours.offsets(version).items()}
    return sorted((offset, name) for name, offset in table.items()), table


def _words(data: bytes) -> np.ndarray:
    return np.frombuffer(data[:len(data) // 4 * 4], ">u4")


def _word_at(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


@dataclass(frozen=True)
class Removed:
    index: int                   # its place in the object pool
    behaviour: str               # the script the running build lacks
    instead: str | None          # the running world's own object at that spawn point, if the atlas holds one


@dataclass(frozen=True)
class Translation:
    data: bytes
    source: str
    target: str
    counts: Counter
    removed: tuple[Removed, ...] = ()
    unmapped: tuple[tuple[int, int, str], ...] = ()   # (slot offset, value, kind): pointers with no place there

    def words(self) -> str:
        """What the page says about it, in plain words. The first sentence is short on purpose: the Save states row
        shows one line (round 9) and the rest on hover."""
        source, target = self.source.upper(), self.target.upper()
        said = [f"The Trainer changed it from the {source} version to your {target} game."]
        for gone in self.removed:
            instead = (f"Your {target} game has {_a(gone.instead)} there instead."
                       if gone.instead else "Nothing is there instead.")
            said.append(f"It left out {_a(gone.behaviour)}, which the {target} game does not have. {instead}")
        return " ".join(said)


def _a(behaviour: str) -> str:
    """An object's name in a sentence: a proper noun stands bare, a common noun takes its article
    (memory/behaviours.py's case convention)."""
    name = behaviours.display_name(behaviour)
    if name[:1].isupper():
        return name
    return f"{'an' if name[:1] in 'aeiou' else 'a'} {name}"


class Translator:
    """One slot's words from the source build to the target build, each counted by how it was mapped."""

    def __init__(self, atlas: Atlas, source: str, target: str, source_segments: tuple[int, ...],
                 target_segments: tuple[int, ...]):
        self.source, self.target = source, target
        self.shift_starts, self.shift_deltas = _shifts(source)
        self.pairs = atlas.pairs[source]
        self.pair_keys = sorted(self.pairs)
        source_world, target_world = atlas.worlds[source], atlas.worlds[target]
        self.blocks, self.target_blocks = source_world.blocks, target_world.blocks
        self.block_starts = [start for _side, start, _size in self.blocks]
        self.pool = source_world.span
        self.usamune_shift = target_world.usamune - source_world.usamune
        self.source_segments, self.target_segments = source_segments, target_segments
        self.source_scripts = source_segments[BEHAVIOUR_SEGMENT] | 0x80000000
        self.target_scripts = target_segments[BEHAVIOUR_SEGMENT] | 0x80000000
        self.scripts, _ = _scripts(source)
        self.script_starts = [offset for offset, _name in self.scripts]
        _, self.target_script = _scripts(target)
        self.counts: Counter = Counter()
        self.unmapped: list[tuple[int, int, str]] = []

    def script_name(self, value: int) -> str | None:
        """The source script a pointer lies in, or None outside the behaviour segment."""
        if not self.source_scripts <= value < self.source_scripts + SCRIPT_SPAN:
            return None
        i = bisect.bisect_right(self.script_starts, value - self.source_scripts) - 1
        return self.scripts[i][1] if i >= 0 else None

    def _block_of(self, value: int) -> int | None:
        i = bisect.bisect_right(self.block_starts, value) - 1
        if i >= 0 and self.blocks[i][1] <= value < self.blocks[i][1] + self.blocks[i][2]:
            return i
        return None

    def word(self, value: int) -> tuple[int, str]:
        if not ADDRESSES[0] <= value < ADDRESSES[1]:
            return value, "not an address"
        if self.source_scripts <= value < self.source_scripts + SCRIPT_SPAN:
            i = bisect.bisect_right(self.script_starts, value - self.source_scripts) - 1
            if i >= 0 and self.scripts[i][1] in self.target_script:
                start, name = self.scripts[i]
                return self.target_scripts + self.target_script[name] + value - self.source_scripts - start, \
                    "behaviour script"
            return value, "behaviour script, unmapped"
        if self.pool[0] <= value < self.pool[1]:
            return self._main_pool(value)
        if USAMUNE_MEMORY[0] <= value < USAMUNE_MEMORY[1]:
            return value + self.usamune_shift, "Usamune's own memory"
        if value >= ABOVE_USAMUNE:
            return value, "expansion memory above Usamune"
        i = bisect.bisect_right(self.shift_starts, value) - 1
        if i < 0:
            return value, "static, unnamed"
        return value + self.shift_deltas[i], "static, by symbol"

    def _main_pool(self, value: int) -> tuple[int, str]:
        if value in self.pairs:
            return self.pairs[value], "main pool, learned"
        i = self._block_of(value)
        k = bisect.bisect_right(self.pair_keys, value) - 1
        if i is not None and k >= 0:
            below = self.pair_keys[k]
            if self.blocks[i][1] <= below and value - below < 0x400:
                return value + self.pairs[below] - below, "main pool, near a learned address"
        if i is None:
            return value, "main pool, between blocks"
        if i >= len(self.target_blocks):
            return value, "main pool, no block in the target"
        side, start, size = self.blocks[i]
        other_side, other_start, other_size = self.target_blocks[i]
        kind = "main pool, same-size block" if (side, size) == (other_side, other_size) else "main pool, resized block"
        return other_start + value - start, kind

    def segmented(self, value: int) -> tuple[int, str] | None:
        """A segmented address (segment number in the top byte) into a pool segment both games loaded, or None."""
        segment, offset = value >> 24, value & 0xFFFFFF
        if not 1 <= segment < SEGMENTS or not self.source_segments[segment] or not self.target_segments[segment]:
            return None
        virtual = 0x80000000 | (self.source_segments[segment] + offset)
        if not self.pool[0] <= virtual < self.pool[1]:
            return None
        new, kind = self.word(virtual)
        if kind in UNMAPPED:
            return value, "segmented, unmapped"
        return (segment << 24) | ((new - (0x80000000 | self.target_segments[segment])) & 0xFFFFFF), "segmented"

    def translate(self, slot: bytes, live_fields: dict[int, bytes], segmented_at) -> bytes:
        """live_fields: slot offset -> the running game's bytes there; segmented_at(offset): may a segmented
        address sit at this slot offset."""
        out = bytearray(slot)
        skip = set()
        for offset, value in live_fields.items():
            out[offset:offset + len(value)] = value
            skip.update(range(offset // 4, (offset + len(value) + 3) // 4))
            self.counts["the running game's own layout"] += len(value) // 4
        for i, value in enumerate(_words(slot).tolist()):
            if i in skip:
                continue
            if ADDRESSES[0] <= value < ADDRESSES[1]:
                new, kind = self.word(value)
            elif segmented_at(i * 4) and (found := self.segmented(value)) is not None:
                new, kind = found
            else:
                continue
            self.counts[kind] += 1
            if kind in UNMAPPED:
                self.unmapped.append((i * 4, value, kind))
            if new != value:
                struct.pack_into(">I", out, i * 4, new)
        return bytes(out)


def segmented_at(offset: int) -> bool:
    """Where structure says a segmented address may sit: an object's rawData, or gPlayerSpawnInfos."""
    if POOL_IN_BODY <= offset < POOL_IN_BODY + POOL_BYTES:
        return RAW_DATA[0] <= (offset - POOL_IN_BODY) % OBJECT_SIZE < RAW_DATA[1]
    return SPAWN_INFOS_IN_BODY <= offset < SPAWN_INFOS_IN_BODY + SPAWN_INFOS_BYTES


def _segments(data: bytes) -> tuple[int, ...]:
    return struct.unpack_from(f">{SEGMENTS}I", data, SEGMENT_TABLE_IN_BODY)


def _instead(world: World, home: tuple[float, float, float]) -> str | None:
    for _index, name, x, y, z in world.objects:
        if max(abs(x - home[0]), abs(y - home[1]), abs(z - home[2])) <= SAME_SPOT:
            return name
    return None


def remove_objects(data: bytes, original: bytes, translator: Translator, target: Build,
                   target_world: World) -> tuple[bytes, tuple[Removed, ...]]:
    """Every active object whose script the target lacks, unloaded in the data as the game would unload it.
    Refuses when anything but the scene graph and its list neighbours points at one: they would name a free object."""
    out = bytearray(data)
    removed = []
    for index in range(OBJECT_COUNT):
        at = POOL_IN_BODY + index * OBJECT_SIZE
        if struct.unpack_from(">h", original, at + OBJECT_ACTIVE_FLAGS)[0] == 0:
            continue
        name = translator.script_name(_word_at(original, at + OBJECT_BEHAVIOR))
        if name is None or name in translator.target_script:
            continue
        address = target.object_pool + index * OBJECT_SIZE
        _refuse_if_named(out, address, at, name)
        following, before = _word_at(out, at + OBJECT_NEXT), _word_at(out, at + OBJECT_PREV)
        struct.pack_into(">I", out, _body_offset(target, before) + OBJECT_NEXT, following)
        struct.pack_into(">I", out, _body_offset(target, following) + OBJECT_PREV, before)
        free = FREE_LIST_IN_BODY + OBJECT_NEXT
        struct.pack_into(">I", out, at + OBJECT_NEXT, _word_at(out, free))
        struct.pack_into(">I", out, free, address)
        struct.pack_into(">h", out, at + OBJECT_ACTIVE_FLAGS, 0)
        struct.pack_into(">I", out, at + OBJECT_PREV_OBJ, 0)
        struct.pack_into(">I", out, at + GFX_THROW_MATRIX, 0)
        struct.pack_into(">H", out, at + GFX_FLAGS, struct.unpack_from(">H", out, at + GFX_FLAGS)[0] & NOT_DRAWN)
        home = struct.unpack_from(">3f", original, at + OBJECT_HOME_POS)
        removed.append(Removed(index, name, _instead(target_world, home)))
    return bytes(out), tuple(removed)


def _link_kind(offset: int) -> str | None:
    """'graph' or 'list' for a body word that is a scene-graph or object-list link, else None."""
    if POOL_IN_BODY <= offset < POOL_IN_BODY + POOL_BYTES:
        field = (offset - POOL_IN_BODY) % OBJECT_SIZE
        return "graph" if field in GRAPH_LINKS else "list" if field in (OBJECT_NEXT, OBJECT_PREV) else None
    if LIST_HEADS_IN_BODY <= offset < LIST_HEADS_IN_BODY + LIST_HEAD_COUNT * LIST_HEAD_BYTES:
        return "list" if (offset - LIST_HEADS_IN_BODY) % LIST_HEAD_BYTES in (OBJECT_NEXT, OBJECT_PREV) else None
    return "list" if offset in (FREE_LIST_IN_BODY + OBJECT_NEXT, FREE_LIST_IN_BODY + OBJECT_PREV) else None


def _refuse_if_named(data: bytearray, address: int, at: int, name: str) -> None:
    """Only the object itself, its list neighbours and the scene graph may point at an object being left out."""
    words = _words(bytes(data[:BODY_BYTES]))
    hits = np.flatnonzero((words >= address) & (words < address + OBJECT_SIZE)) * 4
    for offset in hits.tolist():
        if not (at <= offset < at + OBJECT_SIZE or _link_kind(offset) is not None):
            raise Refusal(f"the level holds on to its {behaviours.display_name(name)}, so it cannot be left out")


def _body_offset(target: Build, node: int) -> int:
    offset = node - target.body_start
    if not 0 <= offset < BODY_BYTES:
        raise Refusal("an object's list leaves the save state")
    return offset


def for_build(memory: GameMemory, live: Build, data: bytes, source: str, atlas: Atlas) -> Translation:
    """The slot's bytes, made by `source`, as the running build's: what every writer calls when the builds differ.
    The running game must stand in the slot's level and area, laid out as the atlas says. Mario's place is
    relocate.to_live_place's job afterwards, as for any slot."""
    if len(data) != SLOT_BYTES:
        raise Refusal(f"a slot is {SLOT_BYTES} bytes, this one {len(data)}")
    if source == live.version or source not in atlas.worlds or live.version not in atlas.worlds:
        raise Refusal(f"there is no translation from {source.upper()} to {live.version.upper()}")
    facts, here = slot_facts(data), live_facts(memory, live)
    if (facts.level, facts.area) != (atlas.level, atlas.area):
        raise Refusal("the map between the two game versions is for another place")
    if (here.level, here.area) != (facts.level, facts.area) or here.mario_object == 0:
        raise Refusal("enter the level and area of this save state first")
    source_world, target_world = atlas.worlds[source], atlas.worlds[live.version]
    if (_word_at(data, POOL_START_IN_BODY), _word_at(data, POOL_END_IN_BODY)) != source_world.pool:
        raise Refusal(f"this {source.upper()} save state's level loaded to other places than the map expects")
    _check_live_pool(memory, live, target_world)
    target_segments = struct.unpack(f">{SEGMENTS}I", memory.read(live.body_start + SEGMENT_TABLE_IN_BODY,
                                                                   SEGMENTS * 4))
    translator = Translator(atlas, source, live.version, _segments(data), target_segments)
    live_fields = {offset: memory.read(live.body_start + offset, size) for offset, size in LIVE_FIELDS}
    out = translator.translate(data, live_fields, segmented_at)
    out, removed = remove_objects(out, data, translator, live, target_world)
    gone = {POOL_IN_BODY + r.index * OBJECT_SIZE for r in removed}
    unmapped = tuple(u for u in translator.unmapped if not any(at <= u[0] < at + OBJECT_SIZE for at in gone))
    return Translation(out, source, live.version, translator.counts, removed, unmapped)


def _check_live_pool(memory: GameMemory, live: Build, world: World) -> None:
    def word(address: int) -> int:
        return int.from_bytes(memory.read(address, 4), "big")
    start, end = word(live.body_start + POOL_START_IN_BODY), word(live.body_start + POOL_END_IN_BODY)
    if (start, end) != world.pool:
        raise Refusal(f"your {live.version.upper()} game loaded this level to other places than the map expects")
    blocks = pool_blocks(word, start, end, word(live.body_start + HEAD_L_IN_BODY), word(live.body_start + HEAD_R_IN_BODY))
    for side in ("L", "R"):
        running = [block for block in blocks if block[0] == side]
        expected = [block for block in world.blocks if block[0] == side]
        if running[:len(expected)] != expected:
            raise Refusal(f"your {live.version.upper()} game loaded this level to other places than the map expects")
