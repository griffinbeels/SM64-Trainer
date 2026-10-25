"""Write, read and verify a portable scene package (M4 v0, experimental).

    package.py write <source> <rom.z64> <out.sm64scene> [--codec lzma|zlib|stored] [--block N] [--group N]
    package.py read <package> <rom.z64> <out frames dir> [--frames 0,7] [--pattern 0xA5]
    package.py verify <package> <rom.z64> <source> [--seed N]

A scene package carries every graphics task's task closure without the ROM.
Closure bytes the viewer's own ROM reproduces travel as references to a ROM
load; only bytes no load explains travel raw. <source> is a scene-tap session
folder (tap.py) or a folder of frame inputs that carry closure.json
(savestate_frames.py + closure.py; --frames-glob picks some). `read` writes
frame inputs in the savestate_frames.py format with --pattern outside the
closure; `verify` compares every reconstructed task with its source in direct,
reverse and random order. Packages and frames hold private RAM images: keep
them out of the repository.

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
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import provenance  # noqa: E402
import tap  # noqa: E402
from savestate_frames import TASK_NAMES  # noqa: E402

MAGIC = b"SM64SCN\x00"
VERSION = 0
SCHEMA = "sm64-scene/0"
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

LOAD_ROM, LOAD_MIO0 = 1, 2
SEG_REF, SEG_RAW = 1, 2
STORED, ZLIB, LZMA = 0, 1, 2
CODECS = {"stored": STORED, "zlib": ZLIB, "lzma": LZMA}
SHOWN, HAS_VI = 1, 2
NO_PRESENT = 0xFFFFFFFF
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
LZMA_MEMLIMIT = 64 << 20
CHUNK_TARGET = 256 << 10


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


def tap_source(session: Path, pair: str) -> Source:
    header = tap.session_header(session)
    frequency = int(header.get("qpc_frequency") or 0) or 1
    source = Source("scene-tap", int(header["rdram_size"]), header.get("rom_header", ""),
                    tap.identity_spec(session), header.get("wrapper_build"))

    def captures():
        origin = None
        for task, present in tap.paired(tap.read_records(session / "tap.bin"), pair):
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


def load_source(path: Path, pair: str = "next", frames_glob: str = "*") -> Source:
    return tap_source(path, pair) if (path / "tap.bin").is_file() else frames_source(path, frames_glob)


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

    def attribute(self, capture: Capture) -> list[list]:
        """Pieces tiling the closure in run order: ["ref", cand, offset, length, ram_end] | ["raw", begin, end]."""
        self._place(capture)
        segments = self._segments(capture.task)
        for _ in range(8):
            mappings = self._segment_mappings(segments) + self.static
            pieces = self._pieces(mappings)
            if not self._discover(pieces):
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


def encode_index(meta: dict, loads: list, chunks: list, blocks: list, frames: list) -> bytes:
    text = json.dumps(meta, sort_keys=True, separators=(",", ":")).encode("utf-8")
    parts = [U32.pack(len(text)), text]
    for fmt, rows in ((LOAD, loads), (CHUNK, chunks), (BLOCK, blocks), (FRAME, frames)):
        parts.append(U32.pack(len(rows)))
        parts += [fmt.pack(*row) for row in rows]
    return b"".join(parts)


def pack_header(rom_sha: bytes, rdram_size: int, frame_count: int, index_offset: int, stored: bytes,
                index_raw: bytes) -> bytes:
    return HEADER.pack(MAGIC, VERSION, HEADER.size, 0, rom_sha, rdram_size, frame_count, index_offset,
                       index_offset + len(stored), len(stored), len(index_raw),
                       hashlib.sha256(index_raw).digest(), bytes(16))


def assemble(rom_sha: bytes, rdram_size: int, frame_count: int, body: bytes, index_raw: bytes) -> bytes:
    """Header + body + zlib index: a whole file (tests re-assemble tampered indexes)."""
    stored = zlib.compress(index_raw, 9)
    return pack_header(rom_sha, rdram_size, frame_count, HEADER.size + len(body), stored, index_raw) + body + stored


def write_package(source: Source, rom: bytes, out: Path, codec: int = LZMA, block_bytes: int = 256,
                  group: int = 16) -> dict:
    if source.rom_header != rom_header_hex(rom):
        raise SystemExit("the source was recorded from a different ROM (header mismatch)")
    if source.rdram_size not in RDRAM_SIZES or block_bytes % 32 or not 0 < group <= 4096:
        raise SystemExit("unsupported RDRAM size, block size or group")
    started = time.perf_counter()
    attributor = Attributor(rom, source.rdram_size)
    stats = Counter()
    partial = out.with_name(out.name + ".partial")
    with partial.open("wb") as handle:
        handle.write(bytes(HEADER.size))
        chunks = ChunkWriter(handle, codec, HEADER.size)
        blocks = Slices(chunks, "blocks", CHUNK_TARGET, dedupe=True)
        records = Slices(chunks, "records", 1 << 62, dedupe=False)
        for capture in source.captures:
            check_capture(capture, source.rdram_size)
            segments = encode_segments(attributor, attributor.attribute(capture), blocks, block_bytes, stats)
            records.add(encode_record(capture, segments), len(capture.run_bytes),
                        closure_digest(capture.runs, capture.run_bytes))
            stats["frames"] += 1
            stats["shown"] += capture.shown
            stats["closure_bytes"] += len(capture.run_bytes)
            if stats["frames"] % group == 0:
                records.flush()
        blocks.flush()
        records.flush()
        loads = attributor.load_table()
        stats.update(refused=source.refused, chunk_blocks=chunks.stored["blocks"],
                     chunk_records=chunks.stored["records"], loads=len(loads),
                     discovered_windows=len(attributor.static) - 1)
        meta = {"schema": SCHEMA, "source": source.kind, "wrapper_build": source.build,
                "identity": [list(item) for item in source.identity], "block_bytes": block_bytes,
                "group": group, "stats": dict(stats)}
        index_raw = encode_index(meta, loads, chunks.entries, [tuple(e) for e in blocks.entries],
                                 [tuple(e) for e in records.entries])
        stored = zlib.compress(index_raw, 9)
        handle.write(stored)
        handle.seek(0)
        handle.write(pack_header(hashlib.sha256(rom).digest(), source.rdram_size, stats["frames"], chunks.offset,
                                 stored, index_raw))
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
    _require(header.version == VERSION, f"unsupported package version {header.version}")
    _require(header.header_size == HEADER.size and header.flags == 0 and header.reserved == bytes(16),
             "malformed package header")
    _require(header.rdram_size in RDRAM_SIZES, "unsupported RDRAM size")
    _require(header.file_size == len(data), "package size does not match its header (truncated?)")
    _require(HEADER.size <= header.index_offset and header.index_offset + header.index_stored == len(data),
             "index outside the package")
    _require(0 < header.index_raw <= MAX_INDEX_BYTES and header.frame_count <= MAX_ENTRIES, "index too large")
    return header


def parse_index(raw: bytes) -> Index:
    cursor = _Cursor(raw, "index")
    (length,) = cursor.unpack(U32)
    _require(length <= MAX_META_BYTES, "package metadata too large")
    try:
        meta = json.loads(cursor.take(length).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise PackageError("package metadata is not JSON") from error
    index = Index(meta, cursor.table(LOAD, MAX_LOADS, "loads"), cursor.table(CHUNK, MAX_ENTRIES, "chunks"),
                  cursor.table(BLOCK, MAX_ENTRIES, "blocks"), cursor.table(FRAME, MAX_ENTRIES, "frames"))
    _require(cursor.done(), "trailing bytes in the index")
    return index


def _identity_ok(item, rdram_size: int) -> bool:
    return (isinstance(item, list) and len(item) == 3 and isinstance(item[0], str) and len(item[0]) <= 32
            and all(type(value) is int for value in item[1:]) and 1 <= item[2] <= 8
            and 0 <= item[1] <= rdram_size - item[2])


def check_index(index: Index, header: Header, rom_size: int) -> None:
    meta = index.meta
    _require(isinstance(meta, dict) and meta.get("schema") == SCHEMA, "unknown package metadata")
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
    _check_slices(index)


def _check_slices(index: Index) -> None:
    spans: dict[int, list[tuple[int, int]]] = {}
    for chunk, offset, length in [row[:3] for row in index.blocks] + [row[:3] for row in index.frames]:
        _require(chunk < len(index.chunks) and 0 < length and offset + length <= index.chunks[chunk][2],
                 "block or frame record outside its chunk")
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
    previous = 0
    for begin, end in runs:
        _require(previous <= begin < end <= rdram_size and begin % 4 == 0 and end % 4 == 0,
                 "closure runs overlap, are unordered or leave RDRAM")
        previous = end
    for kind, length, _which, offset in segments:
        _require(kind in (SEG_REF, SEG_RAW) and 0 < length and length % 4 == 0 and offset % 4 == 0,
                 "malformed closure segment")
    total = sum(end - begin for begin, end in runs)
    _require(total == closure_bytes and sum(segment[1] for segment in segments) == total,
             "frame record does not tile its closure")
    return Record(head, extras, runs, segments)


class Package:
    """A scene package bound to the viewer's ROM; any frame decodes directly."""

    def __init__(self, data: bytes, rom: bytes, cache_chunks: int = 8):
        _require(len(data) <= MAX_PACKAGE_BYTES, "package too large")
        self.data, self.rom = data, rom
        self.header = read_header(data)
        _require(hashlib.sha256(rom).digest() == self.header.rom_sha,
                 "this package was made from a different ROM")
        start = self.header.index_offset
        raw = decompress(ZLIB, data[start:start + self.header.index_stored], self.header.index_raw)
        _require(hashlib.sha256(raw).digest() == self.header.index_sha, "package index is corrupt")
        self.index = parse_index(raw)
        check_index(self.index, self.header, len(rom))
        self.identity = [tuple(item) for item in self.index.meta.get("identity", [])]
        self.cache_chunks = cache_chunks
        self._chunks: OrderedDict[int, bytes] = OrderedDict()
        self._loads: dict[int, bytes] = {}

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
        while len(self._chunks) > self.cache_chunks:
            self._chunks.popitem(last=False)
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

    def frame(self, index: int) -> Capture:
        _require(0 <= index < len(self), "no such frame")
        chunk, offset, length, closure_bytes, digest = self.index.frames[index]
        record = parse_record(self._chunk(chunk)[offset:offset + length], self.header.rdram_size, closure_bytes)
        run_bytes = b"".join(self._piece(*segment) for segment in record.segments)
        _require(closure_digest(record.runs, run_bytes) == digest, f"frame {index} does not reconstruct")
        head = record.head
        return Capture(head[0], tuple(head[6:22]), record.runs, run_bytes, tuple(head[36:48]), record.extras,
                       tuple(head[22:36]) if head[2] & HAS_VI else None, bool(head[2] & SHOWN),
                       None if head[1] == NO_PRESENT else head[1], head[4], head[5])


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
    print(json.dumps({
        "frames": len(expected), "closure_bytes_compared": closure, "orders": orders,
        "open_ms": round(open_ms, 1), "package_bytes": len(data),
        "per_task": {"closure_bytes": closure // frames, "packaged_bytes": len(data) // frames},
        "ref_bytes": stats.get("ref_bytes"), "raw_bytes": stats.get("raw_bytes"),
        "raw_unique_bytes": stats.get("raw_unique_bytes"),
        "compression_ratio": round(closure / len(data), 1),
    }, indent=2))
    return 0 if all(not result["mismatch_count"] for result in orders.values()) else 1


def write(args) -> int:
    source = load_source(Path(args.source), args.pair, args.frames_glob)
    stats = write_package(source, Path(args.rom).read_bytes(), Path(args.out), CODECS[args.codec],
                          args.block, args.group)
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
