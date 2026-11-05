"""The Slot Library on disk: one kept state is its exact slot bytes, packed, plus a small versioned record.

A state's id is the sha256 of its raw bytes, so saving the same bytes twice keeps one state, and two different
saves can never replace each other. Nothing here deletes a state, and a record it cannot read is reported and
left alone rather than overwritten. Layout: <root>/states/<id>.xz and <id>.json, written to a temporary name
and renamed, so a crash leaves either the old file or the new one.
"""
from __future__ import annotations

import hashlib
import json
import logging
import lzma
import os
import re
import threading
import unicodedata
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from sm64_events.slotlibrary.usamune import SEGMENT_BYTES, SLOT_BYTES, Refusal, slot_facts

log = logging.getLogger("sm64.slotlibrary")

RECORD_VERSION = 1
CAPTURED, IMPORTED, BUILT = "captured", "imported", "built"
SOURCES = frozenset({CAPTURED, IMPORTED, BUILT})
MAX_NAME = 80
XZ_MEMORY_LIMIT = 32 << 20           # a state packs with preset 6's 8 MB window; refuse files asking for more
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_FIELDS = {"id": str, "name": str, "level": int, "area": int, "mario_object": int, "fingerprint": str, "rom": str,
           "first_seen_utc": str, "source": str}


class Damaged(RuntimeError):
    """A kept state's file exists but cannot be trusted. It is left as it is."""


@dataclass(frozen=True)
class LoadContext:
    """Where the level had loaded when this state was saved. Usamune does not compare these, but the slot's
    pointers only mean something if they match. Readable only at the moment of the save."""
    current_area: int
    segments: bytes


@dataclass(frozen=True)
class KeptState:
    id: str
    name: str
    level: int
    area: int
    mario_object: int
    fingerprint: str
    rom: str
    first_seen_utc: str
    source: str
    current_area: int | None = None
    segments_hex: str | None = None

    @property
    def context(self) -> LoadContext | None:
        if self.current_area is None or self.segments_hex is None:
            return None
        return LoadContext(self.current_area, bytes.fromhex(self.segments_hex))


@dataclass(frozen=True)
class LibraryScan:
    states: tuple[KeptState, ...]
    damaged: tuple[str, ...]


def state_id(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def clean_name(name: str) -> str:
    """One line of printable text of a bounded length, whoever typed it."""
    kept = "".join(c for c in name if not unicodedata.category(c).startswith("C"))
    return kept.strip()[:MAX_NAME]


def pack(data: bytes) -> bytes:
    return lzma.compress(data, format=lzma.FORMAT_XZ, preset=6)


def unpack(packed: bytes) -> bytes:
    """Exactly one slot out of exactly one xz stream, with memory and output both bounded."""
    decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=XZ_MEMORY_LIMIT)
    try:
        data = decoder.decompress(packed, max_length=SLOT_BYTES + 1)
    except lzma.LZMAError as error:
        raise Damaged(f"the packed bytes do not unpack: {error}") from error
    if len(data) != SLOT_BYTES or not decoder.eof or decoder.unused_data:
        raise Damaged(f"the packed bytes are not exactly one slot of {SLOT_BYTES} bytes")
    return data


def valid_context(current_area: object, segments_hex: object) -> bool:
    if current_area is None and segments_hex is None:
        return True
    return (type(current_area) is int and 0 <= current_area <= 0xFFFFFFFF and isinstance(segments_hex, str)
            and re.fullmatch(rf"[0-9a-f]{{{SEGMENT_BYTES * 2}}}", segments_hex) is not None)


def write_atomic(path: Path, blob: bytes) -> None:
    temporary = path.with_name(f".tmp-{os.getpid()}-{threading.get_ident()}-{path.name}")
    try:
        temporary.write_bytes(blob)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class SlotLibrary:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._states = self.root / "states"
        self._lock = threading.Lock()

    def _blob(self, identity: str) -> Path:
        return self._states / f"{identity}.xz"

    def _record(self, identity: str) -> Path:
        return self._states / f"{identity}.json"

    @staticmethod
    def _checked_id(identity: str) -> str:
        if not _HEX_64.fullmatch(identity):
            raise Refusal(f"{identity!r} is not a state id")
        return identity

    def add(self, data: bytes, *, source: str, fingerprint: str, rom: str, name: str = "",
            context: LoadContext | None = None) -> tuple[KeptState, bool]:
        """Keep these bytes unless they are already kept. Returns the state and whether it is new."""
        facts = slot_facts(data)
        if source not in SOURCES:
            raise ValueError(f"unknown source {source!r}")
        if not _HEX_64.fullmatch(fingerprint):
            raise ValueError("a fingerprint is a sha256 in hex")
        identity = state_id(data)
        with self._lock:
            self._states.mkdir(parents=True, exist_ok=True)
            known = self._record(identity).exists()
            if not self._blob_holds(identity, data):
                write_atomic(self._blob(identity), pack(data))
                if not self._blob_holds(identity, data):
                    raise Damaged(f"state {identity} did not read back from disk")
            if known:
                return self.get(identity), False
            state = KeptState(identity, clean_name(name), facts.level, facts.area, facts.mario_object, fingerprint,
                              clean_name(rom), datetime.now(UTC).isoformat(timespec="seconds"), source,
                              context.current_area if context else None, context.segments.hex() if context else None)
            self._write_record(state)
            return state, True

    def _blob_holds(self, identity: str, data: bytes) -> bool:
        try:
            return unpack(self._blob(identity).read_bytes()) == data
        except FileNotFoundError:
            return False
        except Damaged:
            log.warning("slot library: state %s had a damaged packed file; replacing it", identity)
            return False

    def _write_record(self, state: KeptState) -> None:
        record = {"version": RECORD_VERSION, **asdict(state)}
        write_atomic(self._record(state.id), json.dumps(record, indent=1).encode("utf-8"))

    def _read_record(self, path: Path) -> KeptState:
        try:
            record = json.loads(path.read_bytes())
        except (OSError, ValueError, RecursionError) as error:
            raise Damaged(f"{path.name}: {error}") from error
        if not isinstance(record, dict) or record.get("version") != RECORD_VERSION:
            raise Damaged(f"{path.name}: not a version {RECORD_VERSION} record")
        fields = {k: record.get(k) for k in _FIELDS}
        if not all(type(fields[k]) is kind for k, kind in _FIELDS.items()) or fields["id"] != path.stem:
            raise Damaged(f"{path.name}: its fields are not a kept state")
        if not valid_context(record.get("current_area"), record.get("segments_hex")):
            raise Damaged(f"{path.name}: its load context is malformed")
        return KeptState(**fields, current_area=record.get("current_area"), segments_hex=record.get("segments_hex"))

    def has(self, identity: str) -> bool:
        return self._record(self._checked_id(identity)).exists()

    def get(self, identity: str) -> KeptState:
        path = self._record(self._checked_id(identity))
        if not path.exists():
            raise Refusal(f"no kept state {identity}")
        return self._read_record(path)

    def read_bytes(self, identity: str) -> bytes:
        """The exact slot bytes, or Damaged: its hash is its name, so a changed byte cannot pass."""
        self._checked_id(identity)
        try:
            data = unpack(self._blob(identity).read_bytes())
        except FileNotFoundError as error:
            raise Damaged(f"state {identity} has no packed file") from error
        if state_id(data) != identity:
            raise Damaged(f"state {identity} does not match its own hash")
        return data

    def scan(self) -> LibraryScan:
        """Every readable state, oldest first, and the name of every record that could not be read."""
        states, damaged = [], []
        for path in sorted(self._states.glob("*.json")) if self._states.exists() else []:
            if path.name.startswith(".tmp-"):
                continue
            try:
                states.append(self._read_record(path))
            except Damaged as error:
                log.warning("slot library: %s", error)
                damaged.append(path.stem)
        states.sort(key=lambda state: (state.first_seen_utc, state.id))
        return LibraryScan(tuple(states), tuple(damaged))

    def rename(self, identity: str, name: str) -> KeptState:
        with self._lock:
            renamed = replace(self.get(identity), name=clean_name(name))
            self._write_record(renamed)
            return renamed

    def resolve(self, prefix: str) -> str:
        """A full id from the start of one, for a person typing at a command line."""
        hits = [path.stem for path in self._states.glob("*.json")
                if path.stem.startswith(prefix)] if prefix and self._states.exists() else []
        if len(hits) != 1:
            raise Refusal(f"{prefix!r} matches {len(hits)} kept states")
        return hits[0]
