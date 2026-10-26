"""Write, read and verify a portable scene package (M4 v0/v1, experimental).

    package.py write <source> <rom.z64> <out.sm64scene> [--codec lzma|zlib|stored] [--block N] [--group N]
        [--no-state] [--checkpoint-every N]
    package.py read <package> <rom.z64> <out frames dir> [--frames 0,7] [--pattern 0xA5]
    package.py verify <package> <rom.z64> <source> [--seed N]

A scene package carries every graphics task's task closure without the ROM.
Closure bytes the viewer's own ROM reproduces travel as references to a ROM
load; only bytes no load explains travel raw. <source> is a scene-tap session
folder (tap.py) or a folder of frame inputs that carry closure.json
(savestate_frames.py + closure.py; --frames-glob picks some). `read` writes
frame inputs in the savestate_frames.py format with --pattern outside the
closure; `verify` compares every reconstructed task with its source in direct,
reverse and random order, and every scene state with tap.scene_states of the
same task. Packages and frames hold private RAM images: keep them out of the
repository.

Format v0, little-endian. A 128-byte header (magic, version, sha256 of the
ROM, RDRAM size, frame count, index position, sizes and sha256), then chunks
(independently compressed byte strings, each with the sha256 of its decoded
bytes), then the zlib index: metadata JSON and the load, chunk, block and frame
tables. A load is ROM-derived bytes named by kind (ROM slice or MIO0 block),
ROM start/end, decoded length and sha256; the reader rebuilds it from its own
ROM and refuses a mismatch. A frame record holds the OSTask words, the VI
registers of the present that showed it, the tap fields, identity extras, the
closure run table, and segments that tile the closure bytes in run order:
REF (load, offset; the load's bytes in Project64's word-swapped RDRAM order)
or RAW (one content-addressed block, shared by every frame that repeats it).
Any frame decodes directly from its record chunk, block chunks and loads; its
sha256 covers its run table and closure bytes.

Format v1 is v0 plus layers, each named with its own version in the metadata,
its tables after the frame table; a reader refuses a version or layer it does
not know. The scene-state layer (version 1, written from a tap session with
region records) carries what SM64's own renderer reads, so the evaluator can
re-run it under another camera (e2_georender.py from-package): per frame, the
level regions as they stood and the task's own regions (tap.SceneState's
layers). Its rows give each frame's state record and the sha256 of the state it
rebuilds. A checkpoint record holds the whole state as segments over the same
loads and content-addressed blocks as closures; any other record holds the
task regions XOR the previous frame's and each level delta XOR the bytes it
patches. Checkpoints fall every --checkpoint-every frames, at every level key
and wherever the task regions change shape, so a seek decodes one checkpoint
and the frames after it. Its code rows name ROM code the evaluator may execute
(the practice ROM's boot-copied code): a ROM load and the virtual address it
runs at, rebuilt from the viewer's ROM and refused on mismatch, never RAM bytes.
"""
from __future__ import annotations

import argparse
import array
import bisect
import hashlib
import json
import lzma
import os
import random
import struct
import sys
import time
import zlib
from collections import Counter, OrderedDict, deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import provenance  # noqa: E402
import tap  # noqa: E402
from savestate_frames import TASK_NAMES  # noqa: E402

MAGIC = b"SM64SCN\x00"
VERSION = 1                            # written with a layer; a closures-only package stays version 0
SCHEMAS = {0: "sm64-scene/0", 1: "sm64-scene/1"}
HEADER = struct.Struct("<8sHHI32sIIQQII32s16s")
LOAD = struct.Struct("<IIII32s")       # kind, rom_start, rom_end, length, sha256
CHUNK = struct.Struct("<QIII32s")      # offset, stored, raw, codec, sha256 of raw
BLOCK = struct.Struct("<III")          # chunk, offset, length
FRAME = struct.Struct("<IIII32s")      # chunk, offset, length, closure bytes, closure sha256
RECORD = struct.Struct("<IIIIqq16I14I12IIII")
EXTRA = struct.Struct("<II")           # address, length; bytes follow
RUN = struct.Struct("<II")             # begin, end
SEGMENT = struct.Struct("<IIII")       # kind, length, load|block, offset|0
U32 = struct.Struct("<I")
STATE = struct.Struct("<IIII32s")      # chunk, offset, length, flags, sha256 of the state it rebuilds
CODE = struct.Struct("<II")            # load, virtual address: ROM code the evaluator may execute
STATE_HEAD = struct.Struct("<IIIII")   # kind, level runs | level ops, level segments, task runs, task segments

LOAD_ROM, LOAD_MIO0 = 1, 2
SEG_REF, SEG_RAW = 1, 2
STORED, ZLIB, LZMA = 0, 1, 2
CODECS = {"stored": STORED, "zlib": ZLIB, "lzma": LZMA}
SHOWN, HAS_VI = 1, 2
NO_PRESENT = 0xFFFFFFFF
STATE_LAYER = ("scene-state", 1)
CHECKPOINT, HAS_TASK = 1, 2            # state row flags
KEY_RECORD, DELTA_RECORD = 1, 2        # state record kinds: a checkpoint, or XOR the previous frame
# Measured 2026-10-25 on a 90 s BoB session: every 32/64/128 frames packs to 7.95/7.74/7.62 MiB a
# minute with closures; a cold seek (closure + state) stays near 115 ms p95 at all three.
CHECKPOINT_EVERY = 64
RDRAM_SIZES = (4 << 20, 8 << 20)
BOOT_ROM = (0x1000, 0x101000)          # IPL3 copies 1 MiB from ROM 0x1000 to the header's entry point
WINDOW = 0x10000                       # ROM window a discovered static mapping references

MAX_PACKAGE_BYTES = 4 << 30
MAX_INDEX_BYTES = 256 << 20
MAX_CHUNK_BYTES = 64 << 20
MAX_LOAD_BYTES = 8 << 20
MAX_META_BYTES = 1 << 16
MAX_LOADS = 1 << 16
MAX_ENTRIES = 1 << 24
MAX_EXTRAS = 64
MAX_EXTRA_BYTES = 256
MAX_LAYERS = 8
MAX_STATE_RUNS = 1 << 16
MAX_LEVEL_OPS = 64
MAX_CODE_LOADS = 4
LZMA_MEMLIMIT = 64 << 20
CHUNK_TARGET = 256 << 10
CACHE_BYTES = 64 << 20


class PackageError(ValueError):
    """The package is malformed, corrupt, or does not match this ROM."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PackageError(message)


def swap_words(data: bytes) -> bytes:
    """Game (big-endian) bytes <-> Project64's word-swapped RDRAM image, whole words."""
    words = array.array("I", data[:len(data) & ~3])
    words.byteswap()
    return words.tobytes()


def rom_header_hex(rom: bytes) -> str:
    """The ROM header as the tap and savestates record it (RDRAM word order)."""
    return swap_words(rom[:0x40]).hex()


def closure_digest(runs: list, run_bytes: bytes) -> bytes:
    hasher = hashlib.sha256(b"".join(RUN.pack(*run) for run in runs))
    hasher.update(run_bytes)
    return hasher.digest()


def state_digest(level_runs: list, level: np.ndarray, task: tuple | None) -> bytes:
    """A frame's scene state: the level runs and their bytes, then its task regions or none."""
    hasher = hashlib.sha256(U32.pack(len(level_runs)) + b"".join(RUN.pack(*run) for run in level_runs))
    for begin, end in level_runs:
        hasher.update(level[begin:end])
    if task is None:
        hasher.update(b"\x00")
    else:
        hasher.update(b"\x01" + U32.pack(len(task[0])) + b"".join(RUN.pack(*run) for run in task[0]))
        hasher.update(task[1])
    return hasher.digest()


def xor(data: bytes, base: bytes) -> bytes:
    return (np.frombuffer(data, np.uint8) ^ np.frombuffer(base, np.uint8)).tobytes()


def canonical_runs(spans: list, data: bytes, rdram_size: int) -> tuple[list, bytes]:
    """A tap record's (begin, bytes) spans as ascending (begin, end) runs, its bytes in that order.
    Spans that overlap, leave RDRAM or split words are refused: their order would matter."""
    pieces, at = [], 0
    for begin, size in spans:
        pieces.append((begin, begin + size, data[at:at + size]))
        at += size
    if at != len(data):
        raise ValueError("scene state record bytes do not match its spans")
    pieces.sort(key=lambda piece: piece[0])
    previous = 0
    for begin, end, _ in pieces:
        if not (previous <= begin < end <= rdram_size and begin % 4 == 0 and end % 4 == 0):
            raise ValueError("scene state spans overlap, leave RDRAM or split words")
        previous = end
    return [(begin, end) for begin, end, _ in pieces], b"".join(piece[2] for piece in pieces)


def merge_runs(runs: list) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for begin, end in sorted(runs):
        if merged and begin <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((begin, end))
    return merged


def as_spans(runs: list) -> list[tuple[int, int]]:
    """(begin, end) runs as the tap's (begin, bytes) spans."""
    return [(begin, end - begin) for begin, end in runs]


def known_bytes(scene: "tap.SceneState", runs: list) -> bytes:
    """The level bytes under these runs, zero where the state holds none (the XOR base)."""
    return b"".join(np.where(scene.level_known[begin:end], scene.level[begin:end], np.uint8(0)).tobytes()
                    for begin, end in runs)


def mio0_end(rom: bytes, at: int) -> int:
    """One past the last ROM byte the MIO0 block at `at` consumes."""
    size, comp, raw = struct.unpack_from(">III", rom, at + 4)
    bits_at, ci, ri, made = at + 16, at + comp, at + raw, 0
    while made < size:
        word = struct.unpack_from(">I", rom, bits_at)[0]
        bits_at += 4
        for bit in range(31, -1, -1):
            if made >= size:
                break
            if (word >> bit) & 1:
                ri += 1
                made += 1
            else:
                made += (struct.unpack_from(">H", rom, ci)[0] >> 12) + 3
                ci += 2
    return max(bits_at, ci, ri)


# ---------------------------------------------------------------- sources

@dataclass
class Capture:
    """One graphics task as its source recorded it, or as a package rebuilds it."""
    task_sequence: int
    task: tuple
    runs: list
    run_bytes: bytes
    fields: tuple = (0,) * len(tap.TASK_FIELDS)
    extras: list = field(default_factory=list)          # (address, bytes)
    vi: tuple | None = None
    shown: bool = False
    present_sequence: int | None = None
    task_time_us: int = -1
    present_time_us: int = -1


@dataclass
class Source:
    kind: str
    rdram_size: int
    rom_header: str
    identity: list
    build: str | None
    captures: object = None                             # iterator of Capture
    refused: int = 0
    states: "TapStates | None" = None                   # the session's scene state records, when kept


class TapStates:
    """A tap session's scene state records, handed to the writer task by task as they stream by.

    Ownership mirrors tap.scene_states: a level record belongs to the newest task before it
    (records ahead of the first task to the first), a task record to the newest task when it
    names that task. A refused task's level records carry into the next packaged task; its own
    task record is dropped. Also keeps the tap's full RDRAM copy ahead of the first task, which
    only locates the practice ROM's code (provenance.boot_copied_code) and never travels."""

    def __init__(self):
        self.level: deque = deque()                     # (owner task sequence, or -1, Regions)
        self.task: dict[int, tap.Regions] = {}
        self.newest: tuple[int, int] | None = None      # (sequence, task_index)
        self.full: bytes | None = None

    def watch(self, records):
        for record in records:
            if isinstance(record, tap.Task):
                self.newest = (record.sequence, record.fields["task_index"])
            elif isinstance(record, tap.Regions):
                if record.kind != "task":
                    self.level.append((self.newest[0] if self.newest else -1, record))
                elif self.newest and record.fields["task_index"] == self.newest[1]:
                    self.task[self.newest[0]] = record
            elif isinstance(record, tuple) and record[0] == "full" and self.newest is None and self.full is None:
                self.full = record[2]
            yield record

    def take(self, sequence: int) -> tuple:
        """(the task's own regions or None, the level records due by it) for a packaged task."""
        ops = []
        while self.level and self.level[0][0] <= sequence:
            ops.append(self.level.popleft()[1])
        for stale in [key for key in self.task if key < sequence]:
            del self.task[stale]
        return self.task.pop(sequence, None), ops


def has_state(session: Path) -> bool:
    """The session's arming named scene state spans (tap.py arm without --no-scene-state)."""
    text = (session / "session.txt").read_text(encoding="ascii")
    return any(line.split("=")[0] in ("region", "task_region", "level_region") for line in text.splitlines())


def tap_source(session: Path, pair: str, states: bool = False) -> Source:
    header = tap.session_header(session)
    frequency = int(header.get("qpc_frequency") or 0) or 1
    source = Source("scene-tap", int(header["rdram_size"]), header.get("rom_header", ""),
                    tap.identity_spec(session), header.get("wrapper_build"))
    source.states = TapStates() if states and has_state(session) else None

    def captures():
        origin = None
        records = tap.read_records(session / "tap.bin")
        for task, present in tap.paired(source.states.watch(records) if source.states else records, pair):
            origin = task.qpc if origin is None else origin
            if task.fields["refusals"]:
                source.refused += 1
                continue
            yield Capture(
                task.sequence, tuple(task.task), [tuple(run) for run in task.runs], task.run_bytes,
                tuple(task.fields[name] for name in tap.TASK_FIELDS),
                [(address, data) for address, data in task.extras],
                tuple(present.vi[name] for name in tap.VI_NAMES) if present else None,
                present is not None, present.sequence if present else None,
                (task.qpc - origin) * 1_000_000 // frequency,
                (present.qpc - origin) * 1_000_000 // frequency if present else -1)

    source.captures = captures()
    return source


def frames_source(folder: Path, pattern: str) -> Source:
    dirs = sorted(path for path in folder.glob(pattern) if (path / "closure.json").is_file())
    if not dirs:
        raise SystemExit(f"no frame inputs with closure.json match {pattern} in {folder}")
    metas = [json.loads((path / "frame.json").read_text(encoding="utf-8")) for path in dirs]
    if len({meta["rom_header"] for meta in metas}) != 1:
        raise SystemExit("these frame inputs come from more than one ROM; pick one with --frames-glob")
    source = Source("frames", (dirs[0] / "rdram.bin").stat().st_size, metas[0]["rom_header"], [], None)

    def captures():
        for sequence, (path, meta) in enumerate(zip(dirs, metas, strict=True)):
            closure = json.loads((path / "closure.json").read_text(encoding="utf-8"))
            if closure["refusals"]:
                source.refused += 1
                continue
            rdram = (path / "rdram.bin").read_bytes()
            runs = [(item["begin"], item["end"]) for item in closure["ranges"]]
            task = struct.unpack_from("<16I", (path / "dmem.bin").read_bytes(), 0xFC0)
            fields = dict.fromkeys(tap.TASK_FIELDS, 0) | {
                "runs": len(runs), "closure_bytes": sum(end - begin for begin, end in runs),
                "vi_origin": meta["vi"]["origin"]}
            yield Capture(sequence, task, runs, b"".join(rdram[begin:end] for begin, end in runs),
                          tuple(fields[name] for name in tap.TASK_FIELDS), [],
                          tuple(meta["vi"][name] for name in tap.VI_NAMES), True, sequence)

    source.captures = captures()
    return source


def load_source(path: Path, pair: str = "next", frames_glob: str = "*", states: bool = False) -> Source:
    return tap_source(path, pair, states) if (path / "tap.bin").is_file() else frames_source(path, frames_glob)


def check_capture(capture: Capture, rdram_size: int) -> None:
    """Writer input contract: what the reader will later insist on."""
    previous = 0
    for begin, end in capture.runs:
        if not (previous <= begin < end <= rdram_size and begin % 4 == 0 and end % 4 == 0):
            raise ValueError(f"task {capture.task_sequence}: closure runs are not ascending aligned ranges")
        previous = end
    if len(capture.run_bytes) != sum(end - begin for begin, end in capture.runs) or len(capture.task) != 16:
        raise ValueError(f"task {capture.task_sequence}: closure bytes do not match its run table")
    for address, data in capture.extras:
        if address % 4 or len(data) % 4 or len(data) > MAX_EXTRA_BYTES or address + len(data) > rdram_size:
            raise ValueError(f"task {capture.task_sequence}: identity extra out of range")


# ---------------------------------------------------------------- writer

@dataclass(eq=False)
class Candidate:
    """ROM-derived bytes the writer may reference (game byte order)."""
    kind: int
    rom_start: int
    rom_end: int
    data: bytes
    _image: bytes | None = None

    @property
    def image(self) -> bytes:
        if self._image is None:
            self._image = swap_words(self.data)
        return self._image


class Attributor:
    """Names each closure byte by a ROM load at a RAM base, or leaves it raw.

    Mappings come from the game's own loads: each RSP segment base the task
    sets (G_MW_SEGMENT) backed by the level-script load or MIO0 block that best
    matches the closure there (provenance.py), the IPL3 boot copy of the main
    executable, and static ROM windows discovered by content for raw bytes
    outside the gfx pools (Usamune's expansion RAM payload). Every reference is
    checked byte for byte, so a wrong guess only costs size, never fidelity.
    """

    def __init__(self, rom: bytes, rdram_size: int):
        self.rom, self.rdram_size = rom, rdram_size
        self.by_segment: dict[int, list[Candidate]] = {}
        anywhere: dict[int, Candidate] = {}
        slices: dict[tuple[int, int], Candidate] = {}
        for load in provenance.segment_loads(rom):
            if load["kind"] in (0x18, 0x1A):
                anywhere.setdefault(load["start"], Candidate(LOAD_MIO0, load["start"], 0, load["data"]))
            elif load["segment"] is not None:
                key = (load["start"], load["end"])
                candidate = slices.setdefault(key, Candidate(LOAD_ROM, *key, load["data"]))
                self.by_segment.setdefault(load["segment"], []).append(candidate)
        self.anywhere = list(anywhere.values())
        entry = struct.unpack_from(">I", rom, 8)[0] & 0x1FFFFFFF
        start, end = BOOT_ROM[0], min(BOOT_ROM[1], len(rom), BOOT_ROM[0] + rdram_size - entry)
        self.static: list[tuple[int, Candidate]] = [(entry, Candidate(LOAD_ROM, start, end, rom[start:end]))]
        self.windows: set[tuple[int, int, int]] = set()
        self.choices: dict[tuple[int, int], Candidate] = {}
        self.misses: set[bytes] = set()
        self.loads: dict[Candidate, int] = {}
        self.sparse = bytearray(b"\xa5") * rdram_size
        self.runs: list = []
        self.ends: list = []

    def attribute(self, capture: Capture, segments: dict[int, int] | None = None,
                  discover: bool = True) -> list[list]:
        """Pieces tiling the closure in run order: ["ref", cand, offset, length, ram_end] | ["raw", begin, end].
        State spans hold no display list: they pass the RSP segment bases the tap recorded, and
        look for new ROM windows only where new content arrives (a level key)."""
        self._place(capture)
        segments = self._segments(capture.task) if segments is None else segments
        for _ in range(8):
            mappings = self._segment_mappings(segments) + self.static
            pieces = self._pieces(mappings)
            if not discover or not self._discover(pieces):
                break
        return pieces

    def _place(self, capture: Capture) -> None:
        for begin, end in self.runs:
            self.sparse[begin:end] = b"\xa5" * (end - begin)
        at = 0
        for begin, end in capture.runs:
            self.sparse[begin:end] = capture.run_bytes[at:at + end - begin]
            at += end - begin
        self.runs, self.ends = capture.runs, [end for _, end in capture.runs]

    def _segments(self, task: tuple) -> dict[int, int]:
        try:
            return provenance.rsp_segments(self.sparse, task[12])
        except (struct.error, IndexError):
            return {}

    def _segment_mappings(self, segments: dict[int, int]) -> list[tuple[int, Candidate]]:
        mappings = []
        for segment, base in sorted(segments.items()):
            if base % 4 or base >= self.rdram_size:
                continue
            chosen = self.choices.get((segment, base))
            if chosen is not None:
                matched, total = self._matched(chosen, base)
                chosen = chosen if total and matched * 2 >= total else None
            chosen = chosen or self._choose(segment, base)
            if chosen is not None:
                self.choices[(segment, base)] = chosen
                mappings.append((base, chosen))
        return mappings

    def _choose(self, segment: int, base: int) -> Candidate | None:
        best, best_matched = None, 0
        for candidate in self.by_segment.get(segment, []) + self.anywhere:
            if base + len(candidate.image) > self.rdram_size or not self._matched(candidate, base, 256)[0]:
                continue
            matched = self._matched(candidate, base)[0]
            if matched > best_matched:
                best, best_matched = candidate, matched
        return best

    def _overlap(self, lo: int, hi: int):
        for begin, end in self.runs[bisect.bisect_right(self.ends, lo):]:
            if begin >= hi:
                return
            yield max(begin, lo), min(end, hi)

    def _matched(self, candidate: Candidate, base: int, limit: int = 0) -> tuple[int, int]:
        """Closure bytes in the candidate's span that equal it, of those compared."""
        image, matched, total = candidate.image, 0, 0
        for lo, hi in self._overlap(base, base + len(image)):
            if not limit and self.sparse[lo:hi] == image[lo - base:hi - base]:
                matched, total = matched + hi - lo, total + hi - lo
                continue
            for at in range(lo, hi, 32):
                size = min(32, hi - at)
                total += size
                matched += size if self.sparse[at:at + size] == image[at - base:at - base + size] else 0
                if limit and total >= limit:
                    return matched, total
        return matched, total

    def _equal(self, at: int, size: int, mapping: tuple[int, Candidate]) -> bool:
        base, candidate = mapping
        offset = at - base
        return (0 <= offset and offset + size <= len(candidate.image)
                and self.sparse[at:at + size] == candidate.image[offset:offset + size])

    def _pieces(self, mappings: list) -> list[list]:
        pieces: list[list] = []
        for begin, end in self.runs:
            last = None
            for at in range(begin, end, 32):
                size = min(32, end - at)
                if last is None or not self._equal(at, size, last):
                    last = next((mapping for mapping in mappings if self._equal(at, size, mapping)), None)
                _append(pieces, at, size, last)
        return pieces

    def _discover(self, pieces: list[list]) -> bool:
        """Map raw bytes outside the gfx pools that occur exactly once in the ROM."""
        found = False
        for piece in pieces:
            if piece[0] != "raw":
                continue
            for at in range(piece[1], piece[2] - 31, 256):
                if any(lo <= at < hi for lo, hi in provenance.GFX_POOLS):
                    break
                mapping = self._probe(at)
                key = mapping and (mapping[0], mapping[1].rom_start, mapping[1].rom_end)
                if key and key not in self.windows:
                    self.windows.add(key)
                    self.static.append(mapping)
                    found = True
                    break
        return found

    def _probe(self, at: int) -> tuple[int, Candidate] | None:
        probe = swap_words(bytes(self.sparse[at:at + 32]))
        if probe in self.misses or len(set(probe)) < 8:
            return None
        hit = self.rom.find(probe)
        if hit < 0 or hit % 4 or self.rom.find(probe, hit + 1) >= 0:
            self.misses.add(probe)
            return None
        delta = hit - at
        start = max(hit & ~(WINDOW - 1), delta)
        end = min(start + WINDOW, len(self.rom), self.rdram_size + delta)
        return start - delta, Candidate(LOAD_ROM, start, end, self.rom[start:end])

    def load_index(self, candidate: Candidate) -> int:
        if candidate not in self.loads:
            if candidate.kind == LOAD_MIO0:
                candidate.rom_end = mio0_end(self.rom, candidate.rom_start)
            self.loads[candidate] = len(self.loads)
        return self.loads[candidate]

    def load_table(self) -> list[tuple]:
        return [(c.kind, c.rom_start, c.rom_end, len(c.data), hashlib.sha256(c.data).digest()) for c in self.loads]


def _append(pieces: list[list], at: int, size: int, mapping: tuple[int, Candidate] | None) -> None:
    tail = pieces[-1] if pieces else None
    if mapping is not None:
        base, candidate = mapping
        if tail and tail[0] == "ref" and tail[1] is candidate and tail[2] + tail[3] == at - base and tail[4] == at:
            tail[3] += size
            tail[4] += size
        else:
            pieces.append(["ref", candidate, at - base, size, at + size])
    elif tail and tail[0] == "raw" and tail[2] == at:
        tail[2] += size
    else:
        pieces.append(["raw", at, at + size])


def compress(codec: int, raw: bytes) -> bytes:
    if codec == ZLIB:
        return zlib.compress(raw, 9)
    if codec == LZMA:
        return lzma.compress(raw, format=lzma.FORMAT_XZ, check=lzma.CHECK_NONE, filters=[
            {"id": lzma.FILTER_LZMA2, "preset": 9 | lzma.PRESET_EXTREME, "dict_size": 1 << 20}])
    return raw


class ChunkWriter:
    def __init__(self, handle, codec: int, offset: int):
        self.handle, self.codec, self.offset = handle, codec, offset
        self.entries: list[tuple] = []
        self.stored = Counter()

    def add(self, raw: bytes, kind: str) -> int:
        stored = compress(self.codec, raw)
        self.handle.write(stored)
        self.entries.append((self.offset, len(stored), len(raw), self.codec, hashlib.sha256(raw).digest()))
        self.offset += len(stored)
        self.stored[kind] += len(stored)
        return len(self.entries) - 1


class Slices:
    """Byte strings appended into shared chunks: content-addressed blocks or frame records."""

    def __init__(self, chunks: ChunkWriter, kind: str, target: int, dedupe: bool):
        self.chunks, self.kind, self.target, self.dedupe = chunks, kind, target, dedupe
        self.ids: dict[bytes, int] = {}
        self.entries: list[list] = []
        self.pending = bytearray()
        self.waiting: list[int] = []

    def add(self, data: bytes, *extra) -> int:
        digest = hashlib.sha256(data).digest() if self.dedupe else None
        if digest in self.ids:
            return self.ids[digest]
        index = len(self.entries)
        self.entries.append([None, len(self.pending), len(data), *extra])
        if digest:
            self.ids[digest] = index
        self.pending += data
        self.waiting.append(index)
        if len(self.pending) >= self.target:
            self.flush()
        return index

    def flush(self) -> None:
        if self.pending:
            chunk = self.chunks.add(bytes(self.pending), self.kind)
            for index in self.waiting:
                self.entries[index][0] = chunk
            self.pending, self.waiting = bytearray(), []


def encode_record(capture: Capture, segments: list[tuple]) -> bytes:
    flags = (SHOWN if capture.shown else 0) | (HAS_VI if capture.vi is not None else 0)
    present = NO_PRESENT if capture.present_sequence is None else capture.present_sequence
    parts = [RECORD.pack(capture.task_sequence, present, flags, 0, capture.task_time_us, capture.present_time_us,
                         *capture.task, *(capture.vi or (0,) * 14), *capture.fields,
                         len(capture.extras), len(capture.runs), len(segments))]
    for address, data in capture.extras:
        parts += [EXTRA.pack(address, len(data)), data]
    parts += [RUN.pack(*run) for run in capture.runs]
    parts += [SEGMENT.pack(*segment) for segment in segments]
    return b"".join(parts)


def encode_segments(attributor: Attributor, pieces: list[list], blocks: Slices, block_bytes: int,
                    stats: Counter) -> list[tuple]:
    segments = []
    for piece in pieces:
        if piece[0] == "ref":
            segments.append((SEG_REF, piece[3], attributor.load_index(piece[1]), piece[2]))
            stats["ref_bytes"] += piece[3]
            continue
        at = piece[1]
        while at < piece[2]:
            end = min(piece[2], (at // block_bytes + 1) * block_bytes)
            before = len(blocks.entries)
            segments.append((SEG_RAW, end - at, blocks.add(bytes(attributor.sparse[at:end])), 0))
            stats["raw_bytes"] += end - at
            stats["raw_unique_bytes"] += (end - at) * (len(blocks.entries) - before)
            at = end
    return segments


class StateWriter:
    """The scene-state layer, written frame by frame beside the closures (format v1).

    It replays the tap's region records through tap.SceneState, so every frame's state is
    exactly what tap.scene_states rebuilds. A checkpoint tiles the level runs and the task
    regions with segments (ROM references, else content-addressed blocks shared with the
    closures and every other checkpoint: an unchanged level block is stored once); any other
    frame XORs its task regions with the previous frame's and each level delta with the bytes
    it patches. A frame's level records are complete once the next task has arrived, so the
    writer runs one frame behind the captures."""

    def __init__(self, store: TapStates, attributor: Attributor, chunks: ChunkWriter, blocks: Slices,
                 block_bytes: int, rdram_size: int, every: int, group: int):
        self.store, self.attributor, self.blocks, self.block_bytes = store, attributor, blocks, block_bytes
        self.rdram_size, self.every, self.group = rdram_size, every, group
        self.records = Slices(chunks, "state", 1 << 62, dedupe=False)
        self.scene = tap.SceneState(rdram_size)
        self.level_runs: list[tuple[int, int]] = []
        self.segments: dict[int, int] = {}
        self.previous: tuple | None = None              # the previous frame's task regions (runs, bytes)
        self.waiting: list[Capture] = []
        self.stats = Counter()

    def add(self, capture: Capture) -> None:
        self.waiting.append(capture)
        if len(self.waiting) > 1:
            self._frame(self.waiting.pop(0))

    def finish(self) -> None:
        for capture in self.waiting:
            self._frame(capture)
        self.waiting = []
        self.records.flush()

    def _frame(self, capture: Capture) -> None:
        regions, ops = self.store.take(capture.task_sequence)
        for record in [*ops, regions]:
            if record is not None and any(record.segments):
                self.segments = {index: base for index, base in enumerate(record.segments) if base}
        task = None if regions is None else canonical_runs(regions.spans, regions.data, self.rdram_size)
        key = any(op.kind == "level_key" for op in ops)
        shaped = task is None or (self.previous is not None and _sizes(task[0]) == _sizes(self.previous[0]))
        checkpoint = self.stats["frames"] % self.every == 0 or key or not shaped
        if checkpoint:
            for op in ops:
                self._apply(op)
            record = self._checkpoint(capture, task, discover=key or not self.stats["frames"])
        else:
            record = self._delta(ops, task)
        self.records.add(record, (CHECKPOINT if checkpoint else 0) | (HAS_TASK if task is not None else 0),
                         state_digest(self.level_runs, self.scene.level, task))
        self.previous = task
        self.stats["frames"] += 1
        self.stats["checkpoints"] += checkpoint
        self.stats["record_bytes"] += len(record)
        if self.stats["frames"] % self.group == 0:
            self.records.flush()

    def _apply(self, op: tap.Regions) -> tuple[list, bytes]:
        runs, data = canonical_runs(op.spans, op.data, self.rdram_size)
        self.level_runs = merge_runs(runs if op.kind == "level_key" else self.level_runs + runs)
        self.scene.apply_level(op)
        return runs, data

    def _tile(self, capture: Capture, runs: list, data: bytes, segments: dict, discover: bool) -> list[tuple]:
        if not runs:
            return []
        part = Capture(capture.task_sequence, capture.task, runs, data)
        pieces = self.attributor.attribute(part, segments, discover)
        return encode_segments(self.attributor, pieces, self.blocks, self.block_bytes, self.stats)

    def _checkpoint(self, capture: Capture, task: tuple | None, discover: bool) -> bytes:
        level = b"".join(self.scene.level[begin:end].tobytes() for begin, end in self.level_runs)
        level_segments = self._tile(capture, self.level_runs, level, self.segments, discover)
        task_runs, task_bytes = task or ([], b"")
        task_segments = self._tile(capture, task_runs, task_bytes, {}, False)
        return b"".join([STATE_HEAD.pack(KEY_RECORD, len(self.level_runs), len(level_segments), len(task_runs),
                                         len(task_segments)),
                         *(RUN.pack(*run) for run in self.level_runs), *(SEGMENT.pack(*s) for s in level_segments),
                         *(RUN.pack(*run) for run in task_runs), *(SEGMENT.pack(*s) for s in task_segments)])

    def _delta(self, ops: list, task: tuple | None) -> bytes:
        parts = [STATE_HEAD.pack(DELTA_RECORD, len(ops), 0, 0 if task is None else len(task[0]), 0)]
        for op in ops:
            runs, data = canonical_runs(op.spans, op.data, self.rdram_size)
            parts += [U32.pack(len(runs)), *(RUN.pack(*run) for run in runs), xor(data, known_bytes(self.scene, runs))]
            self.stats["level_delta_bytes"] += len(data)
            self._apply(op)
        if task is not None:
            parts += [*(RUN.pack(*run) for run in task[0]), xor(task[1], self.previous[1])]
        return b"".join(parts)


def _sizes(runs: list) -> list[int]:
    return [end - begin for begin, end in runs]


def code_load(rom: bytes, full: bytes | None) -> tuple[Candidate, int] | None:
    """The practice ROM's boot-copied code as a ROM load and the virtual address it runs at,
    located by the tap's full RDRAM copy (which never travels)."""
    window = provenance.boot_copied_code(rom, swap_words(full)) if full is not None else None
    if window is None:
        return None
    start, length, offset = window
    return Candidate(LOAD_ROM, offset, offset + length, rom[offset:offset + length]), start


def encode_index(meta: dict, loads: list, chunks: list, blocks: list, frames: list, *layers: tuple) -> bytes:
    """The index; each layer appends its (struct, rows) tables in its metadata order."""
    text = json.dumps(meta, sort_keys=True, separators=(",", ":")).encode("utf-8")
    parts = [U32.pack(len(text)), text]
    for fmt, rows in ((LOAD, loads), (CHUNK, chunks), (BLOCK, blocks), (FRAME, frames), *layers):
        parts.append(U32.pack(len(rows)))
        parts += [fmt.pack(*row) for row in rows]
    return b"".join(parts)


def pack_header(rom_sha: bytes, rdram_size: int, frame_count: int, index_offset: int, stored: bytes,
                index_raw: bytes, version: int = 0) -> bytes:
    return HEADER.pack(MAGIC, version, HEADER.size, 0, rom_sha, rdram_size, frame_count, index_offset,
                       index_offset + len(stored), len(stored), len(index_raw),
                       hashlib.sha256(index_raw).digest(), bytes(16))


def assemble(rom_sha: bytes, rdram_size: int, frame_count: int, body: bytes, index_raw: bytes,
             version: int = 0) -> bytes:
    """Header + body + zlib index: a whole file (tests re-assemble tampered indexes)."""
    stored = zlib.compress(index_raw, 9)
    return pack_header(rom_sha, rdram_size, frame_count, HEADER.size + len(body), stored, index_raw,
                       version) + body + stored


def write_package(source: Source, rom: bytes, out: Path, codec: int = LZMA, block_bytes: int = 256,
                  group: int = 16, checkpoint_every: int = CHECKPOINT_EVERY) -> dict:
    """Version 1 with the scene-state layer when the source carries its records, else version 0."""
    if source.rom_header != rom_header_hex(rom):
        raise SystemExit("the source was recorded from a different ROM (header mismatch)")
    if source.rdram_size not in RDRAM_SIZES or block_bytes % 32 or not 0 < group <= 4096 or checkpoint_every < 1:
        raise SystemExit("unsupported RDRAM size, block size, group or checkpoint spacing")
    started = time.perf_counter()
    attributor = Attributor(rom, source.rdram_size)
    stats = Counter()
    partial = out.with_name(out.name + ".partial")
    with partial.open("wb") as handle:
        handle.write(bytes(HEADER.size))
        chunks = ChunkWriter(handle, codec, HEADER.size)
        blocks = Slices(chunks, "blocks", CHUNK_TARGET, dedupe=True)
        records = Slices(chunks, "records", 1 << 62, dedupe=False)
        states = source.states and StateWriter(source.states, attributor, chunks, blocks, block_bytes,
                                               source.rdram_size, checkpoint_every, group)
        for capture in source.captures:
            check_capture(capture, source.rdram_size)
            segments = encode_segments(attributor, attributor.attribute(capture), blocks, block_bytes, stats)
            records.add(encode_record(capture, segments), len(capture.run_bytes),
                        closure_digest(capture.runs, capture.run_bytes))
            if states:
                states.add(capture)
            stats["frames"] += 1
            stats["shown"] += capture.shown
            stats["closure_bytes"] += len(capture.run_bytes)
            if stats["frames"] % group == 0:
                records.flush()
        layers, layer_meta = [], []
        if states:
            states.finish()
            code = code_load(rom, source.states.full)
            source.states.full = None
            codes = [(attributor.load_index(code[0]), code[1])] if code else []
            layers = [(STATE, [tuple(entry) for entry in states.records.entries]), (CODE, codes)]
            layer_meta = [{"name": STATE_LAYER[0], "version": STATE_LAYER[1], "checkpoint_every": checkpoint_every,
                           "group": group}]
            stats.update({f"state_{name}": value for name, value in states.stats.items()})
            stats.update(chunk_state=chunks.stored["state"], code_loads=len(codes))
        blocks.flush()
        records.flush()
        loads = attributor.load_table()
        stats.update(refused=source.refused, chunk_blocks=chunks.stored["blocks"],
                     chunk_records=chunks.stored["records"], loads=len(loads),
                     discovered_windows=len(attributor.static) - 1)
        version = VERSION if states else 0
        meta = {"schema": SCHEMAS[version], "source": source.kind, "wrapper_build": source.build,
                "identity": [list(item) for item in source.identity], "block_bytes": block_bytes,
                "group": group, "stats": dict(stats)} | ({"layers": layer_meta} if version else {})
        index_raw = encode_index(meta, loads, chunks.entries, [tuple(e) for e in blocks.entries],
                                 [tuple(e) for e in records.entries], *layers)
        stored = zlib.compress(index_raw, 9)
        handle.write(stored)
        handle.seek(0)
        handle.write(pack_header(hashlib.sha256(rom).digest(), source.rdram_size, stats["frames"], chunks.offset,
                                 stored, index_raw, version))
    os.replace(partial, out)
    stats.update(package_bytes=out.stat().st_size, index_raw_bytes=len(index_raw))
    return dict(stats) | {"write_seconds": round(time.perf_counter() - started, 3)}


# ---------------------------------------------------------------- reader

@dataclass
class Header:
    magic: bytes
    version: int
    header_size: int
    flags: int
    rom_sha: bytes
    rdram_size: int
    frame_count: int
    index_offset: int
    file_size: int
    index_stored: int
    index_raw: int
    index_sha: bytes
    reserved: bytes


@dataclass
class Index:
    meta: dict
    loads: list
    chunks: list
    blocks: list
    frames: list
    layers: dict = field(default_factory=dict)         # name: its tables


class _Cursor:
    def __init__(self, data: bytes, what: str):
        self.data, self.at, self.what = data, 0, what

    def take(self, size: int) -> bytes:
        _require(0 <= size <= len(self.data) - self.at, f"truncated {self.what}")
        self.at += size
        return self.data[self.at - size:self.at]

    def unpack(self, fmt: struct.Struct) -> tuple:
        return fmt.unpack(self.take(fmt.size))

    def rows(self, fmt: struct.Struct, count: int) -> list[tuple]:
        _require(count * fmt.size <= len(self.data) - self.at, f"truncated {self.what}")
        rows = [fmt.unpack_from(self.data, self.at + i * fmt.size) for i in range(count)]
        self.at += count * fmt.size
        return rows

    def table(self, fmt: struct.Struct, limit: int, name: str) -> list[tuple]:
        (count,) = self.unpack(U32)
        _require(count <= limit, f"too many {name}")
        return self.rows(fmt, count)

    def done(self) -> bool:
        return self.at == len(self.data)


def decompress(codec: int, stored: bytes, raw: int) -> bytes:
    """Decode exactly `raw` bytes, refusing more, less, or a damaged stream."""
    _require(codec in CODECS.values() and 0 <= raw <= max(MAX_CHUNK_BYTES, MAX_INDEX_BYTES), "unknown codec or size")
    try:
        if codec == STORED:
            data, done = bytes(stored), True
        elif codec == ZLIB:
            engine = zlib.decompressobj()
            data = engine.decompress(stored, raw)
            done = not engine.decompress(engine.unconsumed_tail, 1) and engine.eof and not engine.unused_data
        else:
            engine = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=LZMA_MEMLIMIT)
            data = engine.decompress(stored, max_length=raw)
            done = (engine.eof or not engine.decompress(b"", max_length=1)) and engine.eof and not engine.unused_data
    except (zlib.error, lzma.LZMAError) as error:
        raise PackageError(f"compressed data is damaged: {error}") from error
    _require(done and len(data) == raw, "decoded size does not match its claim")
    return data


def read_header(data: bytes) -> Header:
    _require(len(data) >= HEADER.size, "shorter than a package header")
    header = Header(*HEADER.unpack_from(data))
    _require(header.magic == MAGIC, "not a scene package")
    _require(header.version in SCHEMAS,
             f"unsupported package version {header.version} (this reader knows {sorted(SCHEMAS)})")
    _require(header.header_size == HEADER.size and header.flags == 0 and header.reserved == bytes(16),
             "malformed package header")
    _require(header.rdram_size in RDRAM_SIZES, "unsupported RDRAM size")
    _require(header.file_size == len(data), "package size does not match its header (truncated?)")
    _require(HEADER.size <= header.index_offset and header.index_offset + header.index_stored == len(data),
             "index outside the package")
    _require(0 < header.index_raw <= MAX_INDEX_BYTES and header.frame_count <= MAX_ENTRIES, "index too large")
    return header


def parse_index(raw: bytes, version: int = 0) -> Index:
    cursor = _Cursor(raw, "index")
    (length,) = cursor.unpack(U32)
    _require(length <= MAX_META_BYTES, "package metadata too large")
    try:
        meta = json.loads(cursor.take(length).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise PackageError("package metadata is not JSON") from error
    _require(isinstance(meta, dict), "unknown package metadata")
    index = Index(meta, cursor.table(LOAD, MAX_LOADS, "loads"), cursor.table(CHUNK, MAX_ENTRIES, "chunks"),
                  cursor.table(BLOCK, MAX_ENTRIES, "blocks"), cursor.table(FRAME, MAX_ENTRIES, "frames"))
    layers = meta.get("layers", [])
    _require(isinstance(layers, list) and len(layers) <= MAX_LAYERS and (version or "layers" not in meta),
             "malformed package layers")
    for layer in layers:
        name, number = (layer.get("name"), layer.get("version")) if isinstance(layer, dict) else (None, None)
        _require((name, number) == STATE_LAYER and name not in index.layers,
                 f"unknown package layer {str(name)[:40]!r} version {str(number)[:12]} "
                 f"(this reader knows {STATE_LAYER[0]} {STATE_LAYER[1]})")
        index.layers[name] = (cursor.table(STATE, MAX_ENTRIES, "scene states"),
                              cursor.table(CODE, MAX_CODE_LOADS, "code loads"))
    _require(cursor.done(), "trailing bytes in the index")
    return index


def _identity_ok(item, rdram_size: int) -> bool:
    return (isinstance(item, list) and len(item) == 3 and isinstance(item[0], str) and len(item[0]) <= 32
            and all(type(value) is int for value in item[1:]) and 1 <= item[2] <= 8
            and 0 <= item[1] <= rdram_size - item[2])


def check_index(index: Index, header: Header, rom_size: int) -> None:
    meta = index.meta
    _require(isinstance(meta, dict) and meta.get("schema") == SCHEMAS[header.version], "unknown package metadata")
    identity = meta.get("identity", [])
    _require(isinstance(identity, list) and len(identity) <= 16
             and all(_identity_ok(item, header.rdram_size) for item in identity), "malformed identity fields")
    _require(len(index.frames) == header.frame_count, "frame count does not match the header")
    for kind, start, end, length, _sha in index.loads:
        _require(kind in (LOAD_ROM, LOAD_MIO0) and 0 <= start < end <= rom_size and 0 < length <= MAX_LOAD_BYTES
                 and (kind != LOAD_ROM or length == end - start), "malformed load entry")
    previous_end = HEADER.size
    for offset, stored, raw, codec, _sha in index.chunks:
        _require(codec in CODECS.values() and 0 < stored and 0 < raw <= MAX_CHUNK_BYTES, "malformed chunk entry")
        _require(offset >= previous_end and offset + stored <= header.index_offset,
                 "chunk outside the package or overlapping another")
        previous_end = offset + stored
    _require(all(length % 4 == 0 for _, _, length in index.blocks), "block length is not whole words")
    _require(all(0 < closure <= header.rdram_size for _, _, _, closure, _ in index.frames),
             "frame closure larger than RDRAM")
    if STATE_LAYER[0] in index.layers:
        _check_state_layer(index, header)
    _check_slices(index)


def _check_state_layer(index: Index, header: Header) -> None:
    states, codes = index.layers[STATE_LAYER[0]]
    _require(len(states) == len(index.frames), "scene-state rows do not match the frames")
    _require(all(flags & ~(CHECKPOINT | HAS_TASK) == 0 for _, _, _, flags, _ in states)
             and (not states or states[0][3] & CHECKPOINT), "malformed scene-state rows")
    for load, address in codes:
        _require(load < len(index.loads) and index.loads[load][0] == LOAD_ROM and address % 4 == 0
                 and 0x80000000 <= address < 0xA0000000
                 and (address & 0x1FFFFFFF) + index.loads[load][3] <= header.rdram_size, "malformed code load")


def _check_slices(index: Index) -> None:
    spans: dict[int, list[tuple[int, int]]] = {}
    states = index.layers.get(STATE_LAYER[0], ([], []))[0]
    for chunk, offset, length in [row[:3] for table in (index.blocks, index.frames, states) for row in table]:
        _require(chunk < len(index.chunks) and 0 < length and offset + length <= index.chunks[chunk][2],
                 "block, frame or state record outside its chunk")
        spans.setdefault(chunk, []).append((offset, offset + length))
    for items in spans.values():
        items.sort()
        _require(all(a[1] <= b[0] for a, b in zip(items, items[1:], strict=False)), "overlapping entries in a chunk")


@dataclass
class Record:
    head: tuple
    extras: list
    runs: list
    segments: list


def parse_record(raw: bytes, rdram_size: int, closure_bytes: int) -> Record:
    cursor = _Cursor(raw, "frame record")
    head = cursor.unpack(RECORD)
    extras_count, run_count, segment_count = head[-3:]
    _require(head[2] & ~(SHOWN | HAS_VI) == 0 and head[3] == 0, "unknown frame record flags")
    _require(extras_count <= MAX_EXTRAS and run_count <= rdram_size // 4 and segment_count <= rdram_size // 4,
             "frame record counts out of range")
    extras = []
    for _ in range(extras_count):
        address, length = cursor.unpack(EXTRA)
        _require(address % 4 == 0 and length % 4 == 0 and length <= MAX_EXTRA_BYTES
                 and address + length <= rdram_size, "identity extra out of range")
        extras.append((address, cursor.take(length)))
    runs = cursor.rows(RUN, run_count)
    segments = cursor.rows(SEGMENT, segment_count)
    _require(cursor.done(), "trailing bytes in a frame record")
    _check_runs(runs, rdram_size, "closure runs")
    _check_segments(segments)
    total = sum(end - begin for begin, end in runs)
    _require(total == closure_bytes and sum(segment[1] for segment in segments) == total,
             "frame record does not tile its closure")
    return Record(head, extras, runs, segments)


def _check_runs(runs: list, rdram_size: int, what: str) -> None:
    previous = 0
    for begin, end in runs:
        _require(previous <= begin < end <= rdram_size and begin % 4 == 0 and end % 4 == 0,
                 f"{what} overlap, are unordered or leave RDRAM")
        previous = end


def _check_segments(segments: list) -> None:
    for kind, length, _which, offset in segments:
        _require(kind in (SEG_REF, SEG_RAW) and 0 < length and length % 4 == 0 and offset % 4 == 0,
                 "malformed closure segment")


class Package:
    """A scene package bound to the viewer's ROM; any frame decodes directly, and with the
    scene-state layer any frame's state by a seek from its checkpoint."""

    def __init__(self, data: bytes, rom: bytes, cache_bytes: int = CACHE_BYTES):
        _require(len(data) <= MAX_PACKAGE_BYTES, "package too large")
        self.data, self.rom = data, rom
        self.header = read_header(data)
        _require(hashlib.sha256(rom).digest() == self.header.rom_sha,
                 "this package was made from a different ROM")
        start = self.header.index_offset
        raw = decompress(ZLIB, data[start:start + self.header.index_stored], self.header.index_raw)
        _require(hashlib.sha256(raw).digest() == self.header.index_sha, "package index is corrupt")
        self.index = parse_index(raw, self.header.version)
        check_index(self.index, self.header, len(rom))
        self.identity = [tuple(item) for item in self.index.meta.get("identity", [])]
        self.cache_bytes, self._cached = cache_bytes, 0
        self._chunks: OrderedDict[int, bytes] = OrderedDict()
        self._loads: dict[int, bytes] = {}
        layer = self.index.layers.get(STATE_LAYER[0])
        self.states = SceneStates(self, *layer) if layer else None

    def __len__(self) -> int:
        return len(self.index.frames)

    def _chunk(self, index: int) -> bytes:
        if index in self._chunks:
            self._chunks.move_to_end(index)
            return self._chunks[index]
        offset, stored, raw, codec, sha = self.index.chunks[index]
        data = decompress(codec, self.data[offset:offset + stored], raw)
        _require(hashlib.sha256(data).digest() == sha, f"chunk {index} is corrupt")
        self._chunks[index] = data
        self._cached += len(data)
        while self._cached > self.cache_bytes and len(self._chunks) > 1:
            self._cached -= len(self._chunks.popitem(last=False)[1])
        return data

    def _load(self, index: int) -> bytes:
        """The load's bytes from this ROM, in RDRAM word order, checked against its sha256."""
        if index not in self._loads:
            kind, start, end, length, sha = self.index.loads[index]
            try:
                data = self.rom[start:end] if kind == LOAD_ROM else provenance.mio0(self.rom, start)
            except (ValueError, IndexError, struct.error) as error:
                raise PackageError(f"load {index} does not decode from this ROM") from error
            _require(len(data) == length and hashlib.sha256(data).digest() == sha,
                     f"load {index} does not match this ROM")
            self._loads[index] = swap_words(data)
        return self._loads[index]

    def _piece(self, kind: int, length: int, which: int, offset: int) -> bytes:
        if kind == SEG_REF:
            _require(which < len(self.index.loads), "reference to a missing load")
            image = self._load(which)
            _require(offset + length <= len(image), "reference past the end of its load")
            return image[offset:offset + length]
        _require(which < len(self.index.blocks) and offset == 0 and self.index.blocks[which][2] == length,
                 "reference to a missing or differently sized block")
        chunk, at, size = self.index.blocks[which]
        return self._chunk(chunk)[at:at + size]

    def tile(self, total: int, segments: list) -> bytes:
        """The bytes segments tile (each checked against its load or block): exactly `total` of them."""
        _check_segments(segments)
        _require(sum(segment[1] for segment in segments) == total, "segments do not tile their runs")
        return b"".join(self._piece(*segment) for segment in segments)

    def _record(self, index: int) -> Record:
        _require(0 <= index < len(self), "no such frame")
        chunk, offset, length, closure_bytes, _digest = self.index.frames[index]
        return parse_record(self._chunk(chunk)[offset:offset + length], self.header.rdram_size, closure_bytes)

    def fields(self, index: int) -> dict:
        """The frame's tap fields (task_index, ...) without rebuilding its closure."""
        return dict(zip(tap.TASK_FIELDS, self._record(index).head[36:48], strict=True))

    def outline(self, index: int) -> dict:
        """What a viewer lists for a frame without rebuilding its closure: the VI registers of the
        present that showed it (None when none did), and its identity values (global_timer, ...)."""
        record = self._record(index)
        return {"vi": tuple(record.head[22:36]) if record.head[2] & HAS_VI else None,
                "identity": {name: tap.word_value(record.extras, address, size)
                             for name, address, size in self.identity}}

    def frame(self, index: int) -> Capture:
        record = self._record(index)
        run_bytes = b"".join(self._piece(*segment) for segment in record.segments)
        _require(closure_digest(record.runs, run_bytes) == self.index.frames[index][4],
                 f"frame {index} does not reconstruct")
        head = record.head
        return Capture(head[0], tuple(head[6:22]), record.runs, run_bytes, tuple(head[36:48]), record.extras,
                       tuple(head[22:36]) if head[2] & HAS_VI else None, bool(head[2] & SHOWN),
                       None if head[1] == NO_PRESENT else head[1], head[4], head[5])

    def scene_state(self, index: int) -> tuple:
        """(task, its task regions or None, RDRAM, known mask), as tap.scene_states yields them
        for the same task: the closure over the scene state, the rest the 0x5A pattern."""
        _require(self.states is not None, "this package carries no scene state")
        capture = self.frame(index)
        regions, scene = self.states.state(index)
        task = tap.Task(capture.task_sequence, 0, dict(zip(tap.TASK_FIELDS, capture.fields, strict=True)),
                        capture.task, capture.runs, capture.run_bytes, capture.extras)
        return (task, regions, *scene.task_ram(task, regions))


class SceneStates:
    """The scene-state layer of a package: one cursor over tap.SceneState.

    A seek starts at the checkpoint at or before the frame (or carries on forward from the
    frame it last rebuilt, when that lies between) and applies the frames after it; the result
    must match the frame's recorded sha256. Records are bounded before anything is allocated."""

    def __init__(self, package: Package, rows: list, codes: list):
        self.package, self.rows, self.codes = package, rows, codes
        self.rdram_size = package.header.rdram_size
        self.scene: tap.SceneState | None = None
        self.level_runs: list[tuple[int, int]] = []
        self.task: tuple | None = None
        self.at = -1

    def code_windows(self) -> list[tuple[int, int, int]]:
        """(virtual start, bytes, ROM offset) of each ROM code window the evaluator may execute,
        each rebuilt from this ROM and checked against its sha256 (refused on mismatch)."""
        windows = []
        for load, address in self.codes:
            self.package._load(load)
            _kind, start, _end, length, _sha = self.package.index.loads[load]
            windows.append((address, length, start))
        return windows

    def state(self, index: int) -> tuple:
        """(the frame's task regions as a tap.Regions or None, the cursor's tap.SceneState). The
        state object is the cursor's own: use it before the next seek."""
        _require(0 <= index < len(self.rows), "no such frame")
        start = index
        while not self.rows[start][3] & CHECKPOINT:
            start -= 1
        first = self.at + 1 if start <= self.at <= index else start
        self.at = -1                                    # a refused record leaves no half-applied cursor
        for frame in range(first, index + 1):
            self._decode(frame)
        _require(state_digest(self.level_runs, self.scene.level, self.task) == self.rows[index][4],
                 f"state {index} does not reconstruct")
        self.at = index
        regions = None if self.task is None else tap.Regions(index, 0, {}, "task", (), as_spans(self.task[0]),
                                                             self.task[1])
        return regions, self.scene

    def _runs(self, cursor: _Cursor, count: int) -> list[tuple[int, int]]:
        _require(count <= MAX_STATE_RUNS, "scene-state record counts out of range")
        runs = cursor.rows(RUN, count)
        _check_runs(runs, self.rdram_size, "scene-state runs")
        return runs

    def _decode(self, frame: int) -> None:
        chunk, offset, length, flags, _digest = self.rows[frame]
        cursor = _Cursor(self.package._chunk(chunk)[offset:offset + length], "scene-state record")
        kind, a, b, c, d = cursor.unpack(STATE_HEAD)
        checkpoint = bool(flags & CHECKPOINT)
        _require(kind == (KEY_RECORD if checkpoint else DELTA_RECORD) and (flags & HAS_TASK or c == 0)
                 and (checkpoint or b == d == 0), "scene-state record does not match its row")
        _require(b <= self.rdram_size // 4 and d <= self.rdram_size // 4 and (checkpoint or a <= MAX_LEVEL_OPS),
                 "scene-state record counts out of range")
        if checkpoint:
            level_runs = self._runs(cursor, a)
            level_segments = cursor.rows(SEGMENT, b)
            task_runs = self._runs(cursor, c)
            task_segments = cursor.rows(SEGMENT, d)
            _require(cursor.done(), "trailing bytes in a scene-state record")
            level = self.package.tile(sum(_sizes(level_runs)), level_segments)
            task = self.package.tile(sum(_sizes(task_runs)), task_segments)
            self.scene = tap.SceneState(self.rdram_size)
            self.scene.apply_level(tap.Regions(frame, 0, {}, "level_key", (), as_spans(level_runs), level))
            self.level_runs = level_runs
            self.task = (task_runs, task) if flags & HAS_TASK else None
            return
        for _ in range(a):
            (count,) = cursor.unpack(U32)
            runs = self._runs(cursor, count)
            data = xor(cursor.take(sum(_sizes(runs))), known_bytes(self.scene, runs))
            self.scene.apply_level(tap.Regions(frame, 0, {}, "level_delta", (), as_spans(runs), data))
            self.level_runs = merge_runs(self.level_runs + runs)
        if flags & HAS_TASK:
            runs = self._runs(cursor, c)
            _require(self.task is not None and _sizes(runs) == _sizes(self.task[0]),
                     "scene-state record has no previous task regions of its shape")
            self.task = (runs, xor(cursor.take(sum(_sizes(runs))), self.task[1]))
        else:
            self.task = None
        _require(cursor.done(), "trailing bytes in a scene-state record")


def open_package(path: Path, rom_path: Path) -> Package:
    _require(path.stat().st_size <= MAX_PACKAGE_BYTES, "package too large")
    return Package(path.read_bytes(), rom_path.read_bytes())


# ---------------------------------------------------------------- commands

def write_frame(out: Path, index: int, capture: Capture, package: Package, name: str, pattern: int) -> Path:
    """One frame input in the savestate_frames.py format: RDRAM holds only the closure."""
    rdram = bytearray([pattern]) * package.header.rdram_size
    at = 0
    for begin, end in capture.runs:
        rdram[begin:end] = capture.run_bytes[at:at + end - begin]
        at += end - begin
    fields = dict(zip(tap.TASK_FIELDS, capture.fields, strict=True))
    frame = out / f"{index:05d}-task{fields['task_index']:05d}"
    frame.mkdir(parents=True, exist_ok=True)
    (frame / "rdram.bin").write_bytes(rdram)
    dmem = bytearray(0x1000)
    struct.pack_into("<16I", dmem, 0xFC0, *capture.task)
    (frame / "dmem.bin").write_bytes(dmem)
    (frame / "frame.json").write_text(json.dumps({
        "schema": "scene-lab-frame/1",
        "source": {"kind": "scene-package", "package": name, "frame": index,
                   "task_sequence": capture.task_sequence, "present_sequence": capture.present_sequence,
                   "shown": capture.shown, "task_time_us": capture.task_time_us,
                   "present_time_us": capture.present_time_us, "pattern": pattern},
        "identity": {item[0]: tap.word_value(capture.extras, item[1], item[2]) for item in package.identity},
        "vi": dict(zip(tap.VI_NAMES, capture.vi, strict=True)) if capture.vi else None,
        "rom_header": rom_header_hex(package.rom),
        "task": dict(zip(TASK_NAMES, capture.task, strict=True)),
        "tap": fields,
    }, indent=2) + "\n", encoding="utf-8")
    return frame


def read(args) -> int:
    started = time.perf_counter()
    package = open_package(Path(args.package), Path(args.rom))
    wanted = [int(item) for item in args.frames.split(",")] if args.frames else range(len(package))
    written = skipped = 0
    for index in wanted:
        capture = package.frame(index)
        if not args.frames and capture.vi is None:
            skipped += 1
            continue
        write_frame(Path(args.out), index, capture, package, Path(args.package).name, args.pattern)
        written += 1
    print(json.dumps({"frames_written": written, "unshown_skipped": skipped,
                      "seconds": round(time.perf_counter() - started, 3), "out": str(args.out)}))
    return 0


def verify(args) -> int:
    path, rom = Path(args.package), Path(args.rom).read_bytes()
    data = path.read_bytes()
    source = load_source(Path(args.source), args.pair, args.frames_glob)
    expected = list(source.captures)
    started = time.perf_counter()
    package = Package(data, rom)
    open_ms = (time.perf_counter() - started) * 1000
    if len(package) != len(expected):
        raise SystemExit(f"package holds {len(package)} frames, source {len(expected)}")
    order = list(range(len(expected)))
    shuffled = order[:]
    random.Random(args.seed).shuffle(shuffled)
    orders = {}
    for name, sequence in (("direct", order), ("reverse", order[::-1]), ("random", shuffled)):
        package = Package(data, rom)          # fresh caches: no frame depends on visit order
        started = time.perf_counter()
        mismatched = [index for index in sequence if package.frame(index) != expected[index]]
        orders[name] = {"mismatched": mismatched[:8], "mismatch_count": len(mismatched),
                        "seconds": round(time.perf_counter() - started, 3)}
    stats = package.index.meta.get("stats", {})
    closure = sum(len(capture.run_bytes) for capture in expected)
    frames = max(len(expected), 1)
    report = {
        "frames": len(expected), "closure_bytes_compared": closure, "orders": orders,
        "open_ms": round(open_ms, 1), "package_bytes": len(data),
        "per_task": {"closure_bytes": closure // frames, "packaged_bytes": len(data) // frames},
        "ref_bytes": stats.get("ref_bytes"), "raw_bytes": stats.get("raw_bytes"),
        "raw_unique_bytes": stats.get("raw_unique_bytes"),
        "compression_ratio": round(closure / len(data), 1),
    }
    if package.states is not None:
        report["state_orders"] = verify_states(data, rom, Path(args.source),
                                               [capture.task_sequence for capture in expected], args.seed)
        orders |= {f"state {name}": result for name, result in report["state_orders"].items()}
    print(json.dumps(report, indent=2))
    return 0 if all(not result["mismatch_count"] for result in orders.values()) else 1


def state_fingerprint(regions, ram: np.ndarray, known: np.ndarray) -> bytes:
    hasher = hashlib.sha256(b"\x00" if regions is None else b"\x01")
    hasher.update(ram)
    hasher.update(known)
    return hasher.digest()


def verify_states(data: bytes, rom: bytes, session: Path, sequences: list[int], seed: int) -> dict:
    """Every packaged frame's (task regions?, RDRAM, known mask) against tap.scene_states of the
    same task, the owner's own rebuild from the session, in direct, reverse and random order."""
    wanted = {sequence: index for index, sequence in enumerate(sequences)}
    expected: list[bytes | None] = [None] * len(sequences)
    rdram_size = int(tap.session_header(session)["rdram_size"])
    for task, regions, ram, known in tap.scene_states(tap.read_records(session / "tap.bin"), rdram_size):
        if task.sequence in wanted:
            expected[wanted[task.sequence]] = state_fingerprint(regions, ram, known)
    order = list(range(len(sequences)))
    shuffled = order[:]
    random.Random(seed).shuffle(shuffled)
    results = {}
    for name, sequence in (("direct", order), ("reverse", order[::-1]), ("random", shuffled)):
        package = Package(data, rom)
        started = time.perf_counter()
        mismatched = [index for index in sequence
                      if state_fingerprint(*package.scene_state(index)[1:]) != expected[index]]
        results[name] = {"mismatched": mismatched[:8], "mismatch_count": len(mismatched),
                         "seconds": round(time.perf_counter() - started, 3)}
    return results


def write(args) -> int:
    source = load_source(Path(args.source), args.pair, args.frames_glob, states=not args.no_state)
    stats = write_package(source, Path(args.rom).read_bytes(), Path(args.out), CODECS[args.codec],
                          args.block, args.group, args.checkpoint_every)
    print(json.dumps(stats, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("write")
    p.add_argument("source")
    p.add_argument("rom")
    p.add_argument("out")
    p.add_argument("--codec", choices=tuple(CODECS), default="lzma")
    p.add_argument("--block", type=int, default=256, help="raw block boundary in RAM bytes (multiple of 32)")
    p.add_argument("--group", type=int, default=16, help="frame records per chunk")
    p.add_argument("--no-state", action="store_true",
                   help="closures only (version 0): leave out the session's scene state")
    p.add_argument("--checkpoint-every", type=int, default=CHECKPOINT_EVERY,
                   help="frames between scene-state checkpoints (a seek decodes at most this many)")
    p.set_defaults(run=write)
    p = sub.add_parser("read")
    p.add_argument("package")
    p.add_argument("rom")
    p.add_argument("out")
    p.add_argument("--frames", default="", help="comma-separated frame indexes (default: every shown frame)")
    p.add_argument("--pattern", type=lambda value: int(value, 0), default=0xA5)
    p.set_defaults(run=read)
    p = sub.add_parser("verify")
    p.add_argument("package")
    p.add_argument("rom")
    p.add_argument("source")
    p.add_argument("--seed", type=int, default=0)
    p.set_defaults(run=verify)
    for p in (sub.choices["write"], sub.choices["verify"]):
        p.add_argument("--pair", choices=("origin", "next"), default="next")
        p.add_argument("--frames-glob", default="*", help="frame-input folders to take from a frames source")
    args = parser.parse_args()
    try:
        return args.run(args)
    except PackageError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
