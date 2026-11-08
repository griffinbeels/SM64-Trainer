"""Usamune v1.93's nine save-state slots in the running game, and the one checked way to write them.

Griffin decided the Trainer writes to game memory for Ghost Handoff (2026-11-04). Every write goes through
`write_slot_bytes`, inside one slot's range. Slot n lives at SLOT_BASE + n * SLOT_BYTES; the body is copied
from gMarioStates, the trailer starts with gMarioObject (the occupied word, 0 when empty), level and area.
Usamune's load refuses a slot unless those three match the running game. The occupied word is never patched
alone to get past that: in the simulation a patched word crashes the game on the load frame (2026-11-05).
`relocate.to_live_place` moves Mario inside the slot's data instead, and every writer runs it.

Two builds, one slot format. Usamune v1.93u and v1.93j copy the same 0x26320 bytes from gMarioStates, which JP
keeps 0x1370 lower; every one of the 203 symbols STROOP's maps name inside that span sits at the same offset in
both (2026-11-08). So the offsets below are the same for both builds, and a `Build` holds only the absolute
addresses, from that version's layout. A slot made by one build loads in the other only through
`translate.for_build`, which rewrites the pointer values.
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from functools import cache
from typing import Protocol

from sm64_events.memory.layout import VERSIONS, LayoutIncomplete, layout_for
from sm64_events.memory.version_probe import USAMUNE_NAME_PREFIX, normalise_header, version_from_header

# The cartridge name each build's header carries, as a kept state's `rom` records it. A state from any other
# Usamune names no build here, and only the build that made it may take it.
ROM_NAMES = {"us": "SM64 USAMUNE v1.93u", "jp": "SM64 USAMUNE v1.93j"}


@dataclass(frozen=True)
class Build:
    """One Usamune build's absolute addresses for its slots: the layout fields named in `_LAYOUT_FIELDS`."""
    version: str
    save: int                    # Usamune's save routine; its first FINGERPRINT_BYTES identify the build
    load: int                    # Usamune's load routine, the fingerprint's other half
    slots_start: int
    body_start: int              # gMarioStates: a slot's body is a copy of BODY_BYTES from here
    object_pool: int
    mario_object: int
    curr_level: int
    curr_area: int
    current_area_pointer: int
    segment_table: int
    second_camera_focus: int

    @property
    def rom(self) -> str:
        return ROM_NAMES[self.version]


_LAYOUT_FIELDS = {"save": "usamune_slot_save", "load": "usamune_slot_load", "slots_start": "usamune_slots_start",
                  "body_start": "mario_struct", "object_pool": "object_pool", "mario_object": "mario_object",
                  "curr_level": "curr_level", "curr_area": "curr_area",
                  "current_area_pointer": "current_area_pointer", "segment_table": "segment_table",
                  "second_camera_focus": "second_camera_focus"}


@cache
def build_for(version: str) -> Build:
    """Raises LayoutIncomplete until every address a slot needs is verified for this version."""
    layout = layout_for(version)
    absent = [name for name in _LAYOUT_FIELDS.values() if getattr(layout, name) is None]
    if absent:
        raise LayoutIncomplete(f"the {version.upper()} layout has no {', '.join(absent)}")
    return Build(version, **{key: getattr(layout, name) for key, name in _LAYOUT_FIELDS.items()})


def build_of_rom(rom: str) -> str | None:
    """The version whose cartridge name this is, or None for a Usamune this Trainer has no slot addresses for."""
    return next((version for version, name in ROM_NAMES.items() if name == rom), None)


US = build_for("us")
FINGERPRINT_BYTES = 0x400            # how much of each routine identifies this Usamune build
SLOT_BASE, SLOT_BYTES, SLOT_COUNT = US.slots_start, 0x26460, 9
BODY_BYTES = 0x26320                 # Usamune copies this much game state into each slot
TRAILER_MARIO = BODY_BYTES           # the occupied word: gMarioObject, 0 when empty
TRAILER_LEVEL, TRAILER_AREA = BODY_BYTES + 4, BODY_BYTES + 6
OS_MEM_SIZE, EXPANSION_BYTES = 0x80000318, 0x800000
SEGMENT_BYTES = 64
SEGMENT_OF_GFX_POOL = 1              # alternates every frame, so it cannot identify a level
LEVEL_RANGE, AREA_RANGE = range(1, 39), range(1, 9)
# Where any build keeps the body, for checking a slot's own bytes before anyone says which build made them.
_BODIES = tuple(layout_for(version).mario_struct for version in VERSIONS if layout_for(version).mario_struct)


class Refusal(RuntimeError):
    """This cannot be done safely. Nothing was written."""


class WriteFailed(RuntimeError):
    """A write did not read back. The occupied word was cleared, so the slot reads as empty."""


class MarioMoved(WriteFailed):
    """Mario's place in the game changed while the slot was written. The occupied word stays clear: the slot reads
    as empty, and Usamune cannot load bytes made for the old place."""


class GameMemory(Protocol):
    """The running game, in the N64's byte order and addresses."""

    def read(self, address: int, size: int) -> bytes: ...
    def write(self, address: int, data: bytes) -> None: ...
    def cartridge_header(self) -> bytes | None: ...


@dataclass(frozen=True)
class SlotFacts:
    """The three trailer fields Usamune's load gate compares with the running game."""
    mario_object: int
    level: int
    area: int


def slot_address(index: int) -> int:
    if not 0 <= index < SLOT_COUNT:
        raise ValueError(f"Usamune has slots 0 to {SLOT_COUNT - 1}, not {index}")
    return SLOT_BASE + index * SLOT_BYTES


def slot_range(index: int) -> tuple[int, int]:
    try:
        low = slot_address(index)
    except ValueError as error:
        raise Refusal(str(error)) from error
    return low, low + SLOT_BYTES


def routine_fingerprint(read, build: Build) -> str:
    """Usamune's save and load code, hashed: another Usamune build has another slot format."""
    digest = hashlib.sha256()
    for start in (build.save, build.load):
        digest.update(read(start, FINGERPRINT_BYTES))
    return digest.hexdigest()


def check_cartridge(memory: GameMemory) -> Build:
    """The running Usamune's build, or Refusal."""
    raw = memory.cartridge_header()
    header = normalise_header(raw) if raw else None
    if header is None:
        raise Refusal("no cartridge image found: is a ROM running?")
    version = version_from_header(header) if USAMUNE_NAME_PREFIX in header else None
    if version is None:
        raise Refusal("the running ROM is not Usamune")
    try:
        build = build_for(version)
    except LayoutIncomplete as incomplete:
        raise Refusal(f"this Trainer has not checked Usamune {version.upper()}'s save-state addresses yet "
                      f"({incomplete})") from incomplete
    if int.from_bytes(memory.read(OS_MEM_SIZE, 4), "big") != EXPANSION_BYTES:
        raise Refusal("the game runs without the 8 MB of RAM Usamune's slots live in")
    return build


def rom_identity(memory: GameMemory) -> str:
    """The cartridge's own name, for a person to read; `build_of_rom` turns it into a build."""
    raw = memory.cartridge_header()
    header = normalise_header(raw) if raw else None
    return header[0x20:0x34].decode("ascii", "replace").strip("\0 ") if header else ""


def is_occupied(data: bytes) -> bool:
    return data[TRAILER_MARIO:TRAILER_MARIO + 4] != bytes(4)


def slot_facts(data: bytes) -> SlotFacts:
    """What a slot's bytes say about itself. Refuses bytes that cannot be one occupied slot of either build."""
    if len(data) != SLOT_BYTES:
        raise Refusal(f"a slot is {SLOT_BYTES} bytes, this one {len(data)}")
    mario = struct.unpack_from(">I", data, TRAILER_MARIO)[0]
    level = struct.unpack_from(">h", data, TRAILER_LEVEL)[0]
    area = struct.unpack_from(">h", data, TRAILER_AREA)[0]
    if mario == 0:
        raise Refusal("this slot is empty: its occupied word is zero")
    if not any(body <= mario < body + BODY_BYTES for body in _BODIES) or mario % 4:
        raise Refusal(f"gMarioObject {mario:08x} is not inside the object pool a slot copies")
    if level not in LEVEL_RANGE or area not in AREA_RANGE:
        raise Refusal(f"level {level} area {area} is not a place in the game")
    return SlotFacts(mario, level, area)


def live_facts(memory: GameMemory, build: Build) -> SlotFacts:
    """The same three fields in the running game. Mario 0 means no level is loaded."""
    return SlotFacts(int.from_bytes(memory.read(build.mario_object, 4), "big"),
                     struct.unpack(">h", memory.read(build.curr_level, 2))[0],
                     struct.unpack(">h", memory.read(build.curr_area, 2))[0])


def same_segments(live: bytes, recorded: bytes) -> bool:
    keep = [at for at in range(0, SEGMENT_BYTES, 4) if at != SEGMENT_OF_GFX_POOL * 4]
    return all(live[at:at + 4] == recorded[at:at + 4] for at in keep)


def write_slot_bytes(memory: GameMemory, build: Build, index: int, data: bytes, *, mario_place: int | None) -> None:
    """Occupied word cleared, then everything else, then the occupied word; all inside the slot.
    A load pressed during the write finds an empty slot and not half of one.

    mario_place: the game's gMarioObject the bytes were made for. It is read again right before the occupied word
    goes in, and a change leaves the slot empty (MarioMoved). None only for putting a backup back as it was."""
    low, high = slot_range(index)
    occupied = low + TRAILER_MARIO
    if len(data) != high - low:
        raise Refusal("a slot image of the wrong size")
    memory.write(occupied, bytes(4))
    memory.write(low, data[:TRAILER_MARIO])
    memory.write(occupied + 4, data[TRAILER_MARIO + 4:])
    if mario_place is not None and live_facts(memory, build).mario_object != mario_place:
        raise MarioMoved(f"slot {index}: Mario's place changed during the write; the slot is empty")
    memory.write(occupied, data[TRAILER_MARIO:TRAILER_MARIO + 4])
    if memory.read(low, high - low) != data:
        memory.write(occupied, bytes(4))
        raise WriteFailed(f"slot {index} did not read back; it is now empty")


class Project64Game:
    """GameMemory over the attached Project64: it stores RDRAM as little-endian words."""

    def __init__(self, reader):
        self.reader = reader

    @staticmethod
    def _swap(data: bytes) -> bytes:
        if len(data) % 4:
            raise ValueError("whole words only")
        words = struct.unpack(f"<{len(data) // 4}I", data)
        return struct.pack(f">{len(words)}I", *words)

    @staticmethod
    def _word_span(address: int, size: int) -> tuple[int, int]:
        offset = address - 0x80000000
        low = offset & ~3
        return low, ((offset + size + 3) & ~3) - low

    def read(self, address: int, size: int) -> bytes:
        low, whole = self._word_span(address, size)
        data = self._swap(self.reader._read_raw(low, whole))
        start = address - 0x80000000 - low
        return data[start:start + size]

    def write(self, address: int, data: bytes) -> None:
        low, whole = self._word_span(address, len(data))
        words = bytearray(self.read(low + 0x80000000, whole))
        start = address - 0x80000000 - low
        words[start:start + len(data)] = data
        host = self._swap(bytes(words))
        self.reader.write_rdram(low, host)

    def cartridge_header(self) -> bytes | None:
        return self.reader.rom_header()


def attach(pid: int | None) -> Project64Game:
    from sm64_events.memory.pj64 import Pj64Memory
    reader = Pj64Memory()
    if not reader.attach(pid):
        raise Refusal("no Project64 with a running game found")
    return Project64Game(reader)
