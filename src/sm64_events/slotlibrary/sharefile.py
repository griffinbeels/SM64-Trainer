"""One kept state as a file a friend can open (.sm64state): magic, version, record, packed bytes, hash.

The file comes from someone else, so nothing in it is believed: every length is bounded before it is read,
the packed bytes unpack under a memory limit into exactly one slot, the hash must match, and the record must
agree with what the slot's own bytes say. The fingerprint must be one this player's Usamune has, or, with the
game running, the record must name the other build this Trainer translates from (usamune.ROM_NAMES): a US state
may go into a JP player's library.

This proves the file is a well-formed slot for the same Usamune build. It does not prove the game state inside
is harmless: a crafted body could still crash the game when loaded.
"""
from __future__ import annotations

import json
import struct
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from sm64_events.slotlibrary.store import (
    IMPORTED,
    Damaged,
    KeptState,
    LoadContext,
    SlotLibrary,
    clean_name,
    pack,
    state_id,
    unpack,
    valid_context,
    write_atomic,
)
from sm64_events.slotlibrary.usamune import SLOT_BYTES, Refusal, build_of_rom, slot_facts

SUFFIX = ".sm64state"
MAGIC = b"SM64STATE\n"
FILE_VERSION = 1
_HEADER = struct.Struct(">HII32s")   # version, record length, packed length, sha256 of the raw slot
MAX_RECORD_BYTES = 4096
MAX_PACKED_BYTES = SLOT_BYTES + 1024  # xz stores what it cannot shrink, with a small overhead
MAX_FILE_BYTES = len(MAGIC) + _HEADER.size + MAX_RECORD_BYTES + MAX_PACKED_BYTES


@dataclass(frozen=True)
class SharedState:
    data: bytes
    name: str
    fingerprint: str
    rom: str
    context: LoadContext | None


def export_state(library: SlotLibrary, identity: str, path: Path) -> Path:
    """Refuses to replace a file that exists: an earlier export may be the only copy under that name."""
    path = Path(path)
    if path.suffix != SUFFIX:
        raise Refusal(f"a shared state's file name ends in {SUFFIX}")
    if path.exists():
        raise Refusal(f"{path} already exists")
    state = library.get(identity)
    record = json.dumps({"name": state.name, "level": state.level, "area": state.area,
                         "mario_object": state.mario_object, "fingerprint": state.fingerprint, "rom": state.rom,
                         "current_area": state.current_area, "segments_hex": state.segments_hex}).encode("utf-8")
    packed = pack(library.read_bytes(identity))
    if len(record) > MAX_RECORD_BYTES or len(packed) > MAX_PACKED_BYTES:
        raise Refusal("this state does not fit the share format's limits")
    header = _HEADER.pack(FILE_VERSION, len(record), len(packed), bytes.fromhex(identity))
    write_atomic(path, MAGIC + header + record + packed)
    return path


def _record_of(raw: bytes) -> dict:
    try:
        record = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError) as error:
        raise Refusal("this file's record cannot be read") from error
    if not isinstance(record, dict):
        raise Refusal("this file's record cannot be read")
    return record


def _framed_parts(blob: bytes) -> tuple[bytes, bytes, bytes]:
    """The record, the packed bytes and the declared hash, with every length checked against the limits."""
    if len(blob) > MAX_FILE_BYTES:
        raise Refusal(f"this file is larger than any shared state ({MAX_FILE_BYTES} bytes)")
    start = len(MAGIC) + _HEADER.size
    if not blob.startswith(MAGIC) or len(blob) < start:
        raise Refusal("this is not a shared save state")
    version, record_size, packed_size, digest = _HEADER.unpack_from(blob, len(MAGIC))
    if version != FILE_VERSION:
        raise Refusal(f"this file is format {version}; this Trainer reads format {FILE_VERSION}")
    if record_size > MAX_RECORD_BYTES or packed_size > MAX_PACKED_BYTES:
        raise Refusal("this file declares more than a shared state can hold")
    if len(blob) != start + record_size + packed_size:
        raise Refusal("this file's length does not match its header")
    return blob[start:start + record_size], blob[start + record_size:], digest


def parse_shared(blob: bytes, accepted_fingerprints: Collection[str], own_build: str | None = None) -> SharedState:
    """own_build: the build the accepted fingerprints belong to, when a game is running. A file whose record names
    the OTHER known build may carry its own fingerprint: that build's states are translated when placed."""
    raw_record, packed, digest = _framed_parts(blob)
    record = _record_of(raw_record)
    try:
        data = unpack(packed)
    except Damaged as error:
        raise Refusal(str(error)) from error
    if bytes.fromhex(state_id(data)) != digest:
        raise Refusal("this file is damaged: its bytes do not match its hash")
    facts = slot_facts(data)
    if (record.get("level"), record.get("area"), record.get("mario_object")) != (facts.level, facts.area, facts.mario_object):
        raise Refusal("this file's record disagrees with the slot inside it")
    fingerprint, rom = record.get("fingerprint"), record.get("rom")
    other_build = own_build is not None and isinstance(rom, str) and build_of_rom(rom) not in (None, own_build)
    if not isinstance(fingerprint, str) or not (fingerprint in accepted_fingerprints or other_build):
        raise Refusal("this state came from a different Usamune build")
    current_area, segments_hex = record.get("current_area"), record.get("segments_hex")
    if not valid_context(current_area, segments_hex):
        raise Refusal("this file's load addresses are malformed")
    context = LoadContext(current_area, bytes.fromhex(segments_hex)) if current_area is not None else None
    name = record.get("name")
    return SharedState(data, clean_name(name) if isinstance(name, str) else "", fingerprint,
                       clean_name(rom) if isinstance(rom, str) else "", context)


def import_state(library: SlotLibrary, path: Path, accepted_fingerprints: Collection[str],
                 own_build: str | None = None) -> tuple[KeptState, bool]:
    """Into the library only. Returns the state and whether it is new; a state already kept keeps its name."""
    path = Path(path)
    if not path.is_file():
        raise Refusal(f"{path} is not a file")
    with path.open("rb") as file:
        blob = file.read(MAX_FILE_BYTES + 1)
    shared = parse_shared(blob, accepted_fingerprints, own_build)
    return library.add(shared.data, source=IMPORTED, fingerprint=shared.fingerprint, rom=shared.rom,
                       name=shared.name, context=shared.context)
