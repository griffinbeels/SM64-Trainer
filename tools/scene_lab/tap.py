"""Arm, read and replay scene-tap sessions (plugin/gfxwrap/scene_tap.cpp).

    tap.py summary <session>
    tap.py frames <session> <out> [--pattern 0xA5] [--limit N] [--every N] [--pair origin|next]
    tap.py offline <lab_root> <frame_dir>... [--seconds N] [--no-scene-state]

`arming_text` is the arming both modes read (the live spool's lease starts
from it, live_spool.lease_text); its identity reads (gGlobalTimer, level, area)
come from memory/layout.py so the plugin still knows no game address. Since
2026-11-06 Project64's wrapper records only through the lease: the one-shot
arming file (armed.txt at the lab root, consumed when a ROM opens) exists in
the offline host's build alone, so `tap.py arm` is gone. `frames` turns
each recorded task into a frame input (savestate_frames.py format) whose
RDRAM holds ONLY the captured closure, every other byte set to --pattern,
paired with the presented picture that followed it (ref.bgr); --every N keeps
only every N-th frame that has a reference picture. The arming also
names the scene state spans (layout.py's scene_* and main_pool_* rows, plus the
act selector's save_buffer_* and usamune_state_* where the layout has them) the tap
copies with each task, so SM64's own renderer can re-run offline (E2-live:
tools/scene_lab/e2_georender.py from-tap; `scene_states` rebuilds each task's
RDRAM from a session), and the audio spans (layout.py's audio_* and sp_task_*
rows) plus SP DMEM it copies at every VI, so the RSP's audio task can re-run
offline (M4 stems: stems_probe.py from-tap; `vi_states` rebuilds each VI's
state). `offline` runs the REAL producer (scene_tap.cpp, through
tap_offline_host.cpp) over frame inputs and writes a session exactly as the
live tap would, under <lab_root> instead of %LOCALAPPDATA%. Sessions and
frames are private RAM images: keep them out of the repository.

The live spool (live_spool.py) writes the same records into rolling segments:
`read_segment` reads one from its first byte (a SegmentReader takes it in runs
as it grows), stops at a torn tail (the open segment is still being written),
and rebuilds the writer's packed records
(REC_PACKED: RDRAM pieces equal to the segment's reference image left out,
then XPRESS_HUFF through the Windows Compression API) through an Unpacker. REC_BOOT (Boot) is the live capture's one
copy of the expansion RAM, in its session's boot.bin. A header's flags carry
WRITER_KEY on the keys the writer starts each segment with.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import mmap
import struct
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from sm64_events.core.childproc import quiet_spawn_kwargs  # noqa: E402
from sm64_events.memory.layout import layout_for  # noqa: E402

MAGIC = 0x31544C53
REC_PAD, REC_TASK, REC_PRESENT, REC_FULL, REC_REGIONS, REC_VI, REC_BOOT, REC_PACKED = 0, 1, 2, 3, 4, 5, 6, 7
HEADER = struct.Struct("<IIIIqII")      # magic, type, bytes, sequence, qpc, records lost so far, flags
WRITER_KEY = 1                          # header flag: a live-spool key the writer wrote from its own shadow
PACKED = struct.Struct("<8I")           # inner type, inner body bytes, codec, stream bytes, payload bytes, pieces, dedupe, 0
CODEC_STORED, CODEC_XPRESS_HUFF = 0, 2    # 1 was format 1's ntdll XPRESS: no reader decodes it now
COMPRESS_ALGORITHM_XPRESS_HUFF = 4      # compressapi.h
SPOOL_BLOCK = 256                       # a packed record's pieces end at every multiple of this RDRAM address
REGION_KINDS = ("task", "level_key", "level_delta")
REGION_FIELDS = ("task_index", "kind", "count", "data_bytes", "copy_ns", "scan_ns", "block_bytes", "reserved")
VI_KINDS = ("vi_key", "vi_delta")
VI_FIELDS = ("vi_index", "kind", "count", "data_bytes", "total_ns", "scan_ns", "block_bytes", "tasks")
DMEM_BEGIN, DMEM_BYTES = 0x04000000, 0x1000      # vi_dmem: SP DMEM recorded at its physical address
# The spans the scene renderer reads (layout.py rows, [start, end)): copied
# whole with every task, and the main pool as a level region (key + deltas).
SCENE_SPANS = (("scene_data_start", "scene_data_end"), ("scene_bss_start", "scene_bss_end"),
               ("scene_engine_bss_start", "scene_engine_bss_end"))
SCENE_LEVEL_SPANS = (("main_pool_start", "main_pool_end"),)
# Copied whole with every task too, each only where this version's layout names
# it: the act selector's coin score (gSaveBuffer) and Usamune's settings and
# counters its act-select hook prints. A version without them loses those menus.
# The last three: what a Usamune slot of a recorded frame needs and the scene spans stop short of (moment_slot.py).
SCENE_OPTIONAL_SPANS = (("save_buffer_start", "save_buffer_end"), ("usamune_state_start", "usamune_state_end"),
                        ("slot_tail_start", "slot_tail_end"), ("power_meter_hud_start", "power_meter_hud_end"),
                        ("random_seed_start", "random_seed_end"))
# Copied at every VI with SP DMEM (M4 stems): the audio task the game just dispatched,
# whole, before the RSP runs it, and at the next VI what the RSP wrote.
AUDIO_SPANS = (("audio_heap_start", "audio_heap_end"), ("audio_globals_start", "audio_globals_end"),
               ("sp_task_queue_start", "sp_task_queue_end"), ("sp_task_messages_start", "sp_task_messages_end"),
               ("sp_task_pointers_start", "sp_task_pointers_end"))
# SM64's gfx pool (GFX_POOL_SIZE: 6400 commands, every version) starts at a task's data pointer.
# The update allocates from its top before geo_process_root draws: obj_orient_graph's floor-aligned
# matrices, which the render reads through throwMatrix and then lets go of (E2-live). The tap copies
# this tail of the task's own pool; the evaluator reports a task whose update allocated more.
GFX_POOL_BYTES = 6400 * 8
POOL_TAIL_BYTES = 4096
HOST_SOURCES = (ROOT / "tools/scene_lab/tap_offline_host.cpp", ROOT / "plugin/gfxwrap/scene_tap.cpp",
                ROOT / "plugin/gfxwrap/scene_spool.cpp", ROOT / "plugin/gfxwrap/scene_tap.h",
                ROOT / "plugin/gfxwrap/scene_spool.h", ROOT / "plugin/gfxwrap/scene_closure.h",
                ROOT / "plugin/gfxwrap/zilmar.h")
TASK_FIELDS = ("refusals", "commands", "walk_ns", "copy_ns", "vi_origin", "runs", "extras",
               "closure_bytes", "last_cimg", "color_images", "task_index", "reserved")
VI_NAMES = ("status", "origin", "width", "intr", "v_current", "burst", "v_sync", "h_sync",
            "leap", "h_start", "v_start", "v_burst", "x_scale", "y_scale")
IDENTITY_FIELDS = {"global_timer": 4, "curr_level": 2, "curr_area": 2}   # name: bytes


def lab_root() -> Path:
    from sm64_events.core.paths import scene_lab_root
    return scene_lab_root()


@dataclass
class Task:
    sequence: int
    qpc: int
    fields: dict
    task: tuple
    runs: list
    run_bytes: bytes
    extras: list = field(default_factory=list)   # (address, bytes)


@dataclass
class Regions:
    sequence: int
    qpc: int
    fields: dict
    kind: str           # REGION_KINDS
    segments: tuple     # the task's 16 RSP segment bases
    spans: list         # (physical begin, bytes)
    data: bytes         # the spans' bytes, in order, as Project64 stores them


@dataclass
class ViRegions:
    sequence: int
    qpc: int
    fields: dict
    kind: str           # VI_KINDS
    spans: list         # (physical begin, bytes); DMEM_BEGIN and up is SP DMEM
    data: bytes         # the spans' bytes, in order, as Project64 stores them


@dataclass
class Present:
    sequence: int
    qpc: int
    vi: dict
    width: int
    height: int
    read_ns: int
    pixels: bytes


@dataclass
class Boot:
    """A live capture's one copy when it starts (the lease's boot_region): the
    expansion RAM a practice ROM copied its own code into."""
    sequence: int
    begin: int
    data: bytes


def _walk(data, torn_ok: bool):
    """(offset, kind, size, sequence, qpc, flags) of each whole record. A live segment may
    end mid-write: torn_ok stops at the first record that is not whole instead of refusing."""
    at = 0
    while at + 32 <= len(data):
        magic, kind, size, sequence, qpc, _lost, flags = HEADER.unpack_from(data, at)
        if magic != MAGIC or size < 32 or size % 32 or at + size > len(data):
            if torn_ok:
                return
            raise ValueError(f"corrupt tap record at byte {at}")
        yield at, kind, size, sequence, qpc, flags
        at += size


def read_records(path: Path):
    """Stream tap.bin's records. The file is mapped, not read whole: a session
    with reference pictures runs to gigabytes, and only the caller's records stay."""
    if not path.stat().st_size:
        return
    with path.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
        for at, kind, size, sequence, qpc, _flags in _walk(data, torn_ok=False):
            with memoryview(data)[at + 32:at + size] as body:
                record = _parse_record(kind, sequence, qpc, body)
            if record is not None:
                yield record


def read_segment(data: bytes, rdram_size: int):
    """(offset, header flags, record) for a live-spool segment's whole records, from its
    first byte (plugin/gfxwrap/scene_spool.cpp): packed records come back as the
    producer's own, rebuilt against the segment's reference image."""
    yield from SegmentReader(rdram_size).feed(data)


class SegmentReader:
    """A live-spool segment read as it grows: feed() each run of bytes appended since the
    last call; a record not yet whole waits for the next feed. Consume each feed fully."""

    def __init__(self, rdram_size: int):
        self.unpacker = Unpacker(rdram_size)
        self.offset = 0                  # file bytes taken as whole records
        self.pending = b""               # bytes after them: a record still being written

    @property
    def read(self) -> int:
        """File bytes fed so far: where the next feed starts."""
        return self.offset + len(self.pending)

    def feed(self, data: bytes):
        buffer = self.pending + data if self.pending else data
        consumed = 0
        for at, kind, size, sequence, qpc, flags in _walk(buffer, torn_ok=True):
            body = memoryview(buffer)[at + 32:at + size]
            if kind == REC_PACKED:
                kind, body = self.unpacker.unpack(body)
            record = _parse_record(kind, sequence, qpc, body)
            consumed = at + size
            if record is not None:
                yield self.offset + at, flags, record
        self.pending = bytes(buffer[consumed:])
        self.offset += consumed


_capi = threading.local()             # one Compression API decompressor per thread (a handle is not shared)


def _decompressor():
    """This thread's XPRESS_HUFF decompressor (cabinet.dll's documented Compression API)."""
    handle = getattr(_capi, "handle", None)
    if handle is None:
        import ctypes
        from ctypes import wintypes
        cabinet = ctypes.WinDLL("cabinet", use_last_error=True)
        cabinet.CreateDecompressor.argtypes = (wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
        cabinet.CreateDecompressor.restype = wintypes.BOOL
        cabinet.Decompress.argtypes = (ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_void_p,
                                       ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t))
        cabinet.Decompress.restype = wintypes.BOOL
        handle = ctypes.c_void_p()
        if not cabinet.CreateDecompressor(COMPRESS_ALGORITHM_XPRESS_HUFF, None, ctypes.byref(handle)):
            raise OSError(ctypes.get_last_error(), "the Compression API has no XPRESS_HUFF decompressor")
        _capi.handle, _capi.cabinet = handle, cabinet
    return _capi.cabinet, handle


def unpack_payload(codec: int, payload: bytes, size: int) -> bytes:
    """A REC_PACKED payload back into its `size`-byte stream (scene_spool.cpp's codecs)."""
    if codec == CODEC_STORED and len(payload) == size:
        return payload
    if codec == CODEC_XPRESS_HUFF:
        import ctypes
        cabinet, handle = _decompressor()
        out, got = ctypes.create_string_buffer(size), ctypes.c_size_t()
        if not cabinet.Decompress(handle, payload, len(payload), out, size, ctypes.byref(got)) or got.value != size:
            raise ValueError(f"packed record does not decompress (error {ctypes.get_last_error()})")
        return out.raw
    raise ValueError(f"packed record: codec {codec} is not one this reader knows (format 1's XPRESS was 1), "
                     "or its payload is short")


class Unpacker:
    """A live-spool segment's packed records back into the producer's records.

    The reference image follows the writer's: zero at the segment's start, then every
    closure's runs and task record's regions as they decode, in record order. A packed
    record's RDRAM bytes come in pieces cut at every 256-byte RDRAM boundary; a cleared
    bit means the piece equals the reference image before this record."""

    def __init__(self, rdram_size: int):
        self.ref = np.zeros(rdram_size, np.uint8)

    def unpack(self, body) -> tuple[int, bytes]:
        inner, inner_bytes, codec, stream_bytes, payload_bytes, pieces, dedupe, _ = PACKED.unpack_from(body, 0)
        stream = unpack_payload(codec, bytes(body[32:32 + payload_bytes]), stream_bytes)
        if not dedupe:
            return inner, stream
        bitmap = ((pieces + 7) // 8 + 3) & ~3
        rest = stream[bitmap:]
        if inner == REC_TASK:
            runs, extras, content = struct.unpack_from("<3I", rest, 20)
            prefix = 112 + 8 * runs + 8 * extras
            table = np.frombuffer(rest, "<u4", 2 * runs, 112).reshape(-1, 2).astype(np.int64)
            begins, sizes = table[:, 0], table[:, 1] - table[:, 0]
        elif inner == REC_REGIONS:
            count, content = struct.unpack_from("<2I", rest, 8)
            prefix = 96 + 8 * count
            table = np.frombuffer(rest, "<u4", 2 * count, 96).reshape(-1, 2).astype(np.int64)
            begins, sizes = table[:, 0], table[:, 1]
        else:
            raise ValueError(f"packed record: type {inner} carries no RDRAM pieces")
        if int(sizes.sum()) != content or (len(begins) and int((begins + sizes).max()) > len(self.ref)):
            raise ValueError("packed record: its spans and its bytes disagree")
        first, last = begins // SPOOL_BLOCK, (begins + sizes - 1) // SPOOL_BLOCK
        counts = np.where(sizes > 0, last - first + 1, 0)
        if int(counts.sum()) != pieces:
            raise ValueError("packed record: its piece count does not match its spans")
        span = np.repeat(np.arange(len(begins)), counts)
        block = np.repeat(first, counts) + np.arange(pieces) - np.repeat(np.cumsum(counts) - counts, counts)
        lows = np.maximum(begins[span], block * SPOOL_BLOCK)
        highs = np.minimum((begins + sizes)[span], (block + 1) * SPOOL_BLOCK)
        bits = np.unpackbits(np.frombuffer(stream, np.uint8, (pieces + 7) // 8), bitorder="little")[:pieces]
        stored = np.repeat(bits.astype(bool), highs - lows)
        spans = list(zip(begins.tolist(), sizes.tolist(), strict=True))
        rebuilt = np.concatenate([self.ref[begin:begin + size] for begin, size in spans] or [np.zeros(0, np.uint8)])
        kept = int(stored.sum())
        rebuilt[stored] = np.frombuffer(rest, np.uint8, kept, prefix)
        offset = 0
        for begin, size in spans:
            self.ref[begin:begin + size] = rebuilt[offset:offset + size]
            offset += size
        body = rest[:prefix] + rebuilt.tobytes() + rest[prefix + kept:]
        if len(body) != inner_bytes:
            raise ValueError("packed record: rebuilt body has the wrong length")
        return inner, body


def _parse_record(kind: int, sequence: int, qpc: int, body: memoryview):
    """One record as owned objects (copies), so the mapping can close behind it."""
    if kind == REC_TASK:
        fields = dict(zip(TASK_FIELDS, struct.unpack_from("<12I", body, 0), strict=True))
        task = struct.unpack_from("<16I", body, 48)
        offset = 112
        runs = [struct.unpack_from("<II", body, offset + 8 * i) for i in range(fields["runs"])]
        offset += 8 * fields["runs"]
        extra_spec = [struct.unpack_from("<II", body, offset + 8 * i) for i in range(fields["extras"])]
        offset += 8 * fields["extras"]
        run_bytes = bytes(body[offset:offset + fields["closure_bytes"]])
        offset += fields["closure_bytes"]
        extras = []
        for address, length in extra_spec:
            padded = (length + 3) & ~3
            extras.append((address & ~3, bytes(body[offset:offset + padded])))
            offset += padded
        return Task(sequence, qpc, fields, task, runs, run_bytes, extras)
    if kind == REC_PRESENT:
        vi = dict(zip(VI_NAMES, struct.unpack_from("<14I", body, 0), strict=True))
        width, height, read_ns, _format = struct.unpack_from("<4I", body, 56)
        return Present(sequence, qpc, vi, width, height, read_ns, bytes(body[72:72 + width * height * 3]))
    if kind == REC_FULL:
        (rdram_size,) = struct.unpack_from("<I", body, 0)
        return ("full", sequence, bytes(body[16:16 + rdram_size]))
    if kind == REC_REGIONS:
        fields = dict(zip(REGION_FIELDS, struct.unpack_from("<8I", body, 0), strict=True))
        segments = struct.unpack_from("<16I", body, 32)
        spans = [struct.unpack_from("<II", body, 96 + 8 * i) for i in range(fields["count"])]
        at = 96 + 8 * fields["count"]
        return Regions(sequence, qpc, fields, REGION_KINDS[fields["kind"]], segments, spans,
                       bytes(body[at:at + fields["data_bytes"]]))
    if kind == REC_VI:
        fields = dict(zip(VI_FIELDS, struct.unpack_from("<8I", body, 0), strict=True))
        if fields["kind"] >= len(VI_KINDS) or 32 + 8 * fields["count"] + fields["data_bytes"] > len(body):
            raise ValueError(f"corrupt VI record {sequence}")
        spans = [struct.unpack_from("<II", body, 32 + 8 * i) for i in range(fields["count"])]
        if sum(size for _, size in spans) != fields["data_bytes"]:
            raise ValueError(f"VI record {sequence}: its spans and its data disagree")
        at = 32 + 8 * fields["count"]
        return ViRegions(sequence, qpc, fields, VI_KINDS[fields["kind"]], spans,
                         bytes(body[at:at + fields["data_bytes"]]))
    if kind == REC_BOOT:
        begin, size = struct.unpack_from("<II", body, 0)
        return Boot(sequence, begin, bytes(body[32:32 + size]))
    return None


def session_header(session: Path) -> dict:
    """session.txt as key=value strings (rdram_size, rom_header, qpc_frequency...)."""
    header = {}
    for line in (session / "session.txt").read_text(encoding="ascii").splitlines():
        key, _, value = line.partition("=")
        header[key] = value
    return header


def identity_spec(session: Path) -> list[tuple[str, int, int]]:
    """The identity extras the arming tool named: (name, physical address, bytes). A live
    session keeps its lease's lines in lease-lines.txt, a one-shot session its armed.txt."""
    identity = []
    lines = session / "lease-lines.txt"
    for line in (lines if lines.is_file() else session / "armed.txt").read_text(encoding="ascii").splitlines():
        if line.startswith("# identity="):
            for item in filter(None, line.removeprefix("# identity=").split(",")):
                name, address, size = item.split(":")
                identity.append((name, int(address, 16), int(size)))
    return identity


def paired(records, pair: str = "next"):
    """Every task once, in record order, with the present that showed it or None.

    With frame buffer emulation off (Project64's configuration) the renderer
    presents its newest list at the next new VI origin, whatever that origin
    names, so "next" pairs a picture with the newest task. Measured on the
    first live session (2026-10-24): "next" matched 217 of 219 sampled
    pictures pixel for pixel, "origin" 25 (SM64 shows the previous task's
    buffer, so origin pairing is one frame late). "origin" pairs with the
    newest task drawn into the image VI scans, for a renderer that emulates
    frame buffers. Tasks a later picture overtook were never shown.
    """
    pending: list[Task] = []
    for record in records:
        if isinstance(record, Task):
            pending.append(record)
            continue
        if not isinstance(record, Present) or not pending:
            continue
        shown = pending if pair == "next" else [
            task for task in pending if abs(record.vi["origin"] - task.fields["last_cimg"]) < 0x1000]
        if not shown:
            continue
        task = shown[-1]
        for earlier in pending:
            if earlier.sequence < task.sequence:
                yield earlier, None
        yield task, record
        pending = [later for later in pending if later.sequence > task.sequence]
    for task in pending:
        yield task, None


def word_value(extras: list, address: int, size: int) -> int | None:
    """Read a big-endian game value from word-swapped extra bytes."""
    for base, data in extras:
        if base <= address and address + size <= base + len(data):
            raw = bytearray()
            for byte in range(address, address + size):
                index = (byte ^ 3) - base
                raw.append(data[index])
            return int.from_bytes(raw, "big")
    return None


def scene_state_lines(layout) -> list[str]:
    """Arming lines for the scene renderer's state (E2-live), or none when this
    version's layout lacks the spans."""
    lines = []
    for kind, spans in (("region", SCENE_SPANS), ("level_region", SCENE_LEVEL_SPANS)):
        for start_field, end_field in spans:
            start, end = layout.value(start_field), layout.value(end_field)
            if start is None or end is None:
                return []
            lines.append(f"{kind}={start & 0x1FFFFFFF:x}:{end - start}")
    for start_field, end_field in SCENE_OPTIONAL_SPANS:
        start, end = layout.value(start_field), layout.value(end_field)
        if start is not None and end is not None:
            lines.append(f"region={start & 0x1FFFFFFF:x}:{end - start}")
    lines.append(f"task_region={GFX_POOL_BYTES - POOL_TAIL_BYTES:x}:{POOL_TAIL_BYTES}")
    return lines


def audio_state_lines(layout) -> list[str]:
    """Arming lines for the audio task's state at every VI (M4 stems), or none when
    this version's layout lacks any of the spans."""
    lines = []
    for start_field, end_field in AUDIO_SPANS:
        start, end = layout.value(start_field), layout.value(end_field)
        if start is None or end is None:
            return []
        lines.append(f"vi_region={start & 0x1FFFFFFF:x}:{end - start}")
    return [*lines, "vi_dmem=1"]


def arming_text(args) -> str:
    layout = layout_for(args.version)
    lines = [f"seconds={args.seconds}", f"reference={0 if args.no_reference else 1}",
             f"reference_every={args.reference_every}", f"full_every={args.full_every}"]
    names = []
    for name, size in IDENTITY_FIELDS.items():
        address = getattr(layout, name)
        if address:
            lines.append(f"extra={address & 0x1FFFFFFF:x}:{size}")
            names.append(f"{name}:{address & 0x1FFFFFFF:x}:{size}")
    lines.append("# identity=" + ",".join(names))   # the tap ignores comment lines
    if not args.no_scene_state:
        lines += scene_state_lines(layout)
    if not getattr(args, "no_audio_state", False):
        lines += audio_state_lines(layout)
    return "\n".join(lines) + "\n"


class SceneState:
    """The RDRAM one task's scene renderer read, rebuilt from a session.

    The level regions accumulate across tasks (a key replaces them, a delta
    patches them); a task's own regions and its closure lie on top. Bytes stay
    in Project64's word-swapped order; `known` marks every byte the session
    captured, and everything else reads `pattern`."""

    def __init__(self, rdram_size: int):
        self.level = np.zeros(rdram_size, np.uint8)
        self.level_known = np.zeros(rdram_size, bool)

    def apply_level(self, regions: Regions) -> None:
        if regions.kind == "level_key":
            self.level_known[:] = False
        at = 0
        for begin, size in regions.spans:
            self.level[begin:begin + size] = np.frombuffer(regions.data, np.uint8, size, at)
            self.level_known[begin:begin + size] = True
            at += size

    @staticmethod
    def _layers(task: Task, regions: Regions | None) -> list:
        """What lies over the level regions, in order: the task's regions, then its closure."""
        layers = [] if regions is None else [(regions.spans, regions.data)]
        layers.append(([(begin, end - begin) for begin, end in task.runs], task.run_bytes))
        return layers

    def task_ram(self, task: Task, regions: Regions | None, pattern: int = 0x5A):
        ram = np.where(self.level_known, self.level, np.uint8(pattern))
        known = self.level_known.copy()
        for spans, data in self._layers(task, regions):
            at = 0
            for begin, size in spans:
                ram[begin:begin + size] = np.frombuffer(data, np.uint8, size, at)
                known[begin:begin + size] = True
                at += size
        return ram, known

    def task_bytes(self, task: Task, regions: Regions | None, wanted, pattern: int = 0x5A) -> list[bytes]:
        """task_ram's RDRAM at each (offset, size) of `wanted` only, without building all of it:
        a few words of a task's state cost well under a millisecond instead of two 8 MiB passes
        (ghost_sound.py reads the stage's camera and Mario every few frames)."""
        layers = []
        for spans, data in self._layers(task, regions):
            begins = np.array([begin for begin, _size in spans], np.int64)
            sizes = np.array([size for _begin, size in spans], np.int64)
            layers.append((begins, sizes, np.cumsum(sizes) - sizes, data))
        pieces = []
        for offset, size in wanted:
            if not (0 <= offset and 0 <= size and offset + size <= self.level.size):
                raise ValueError("a span outside RDRAM")
            piece = np.where(self.level_known[offset:offset + size], self.level[offset:offset + size], np.uint8(pattern))
            for begins, sizes, ats, data in layers:
                for at in np.flatnonzero((begins < offset + size) & (begins + sizes > offset)).tolist():
                    lo, hi = max(int(begins[at]), offset), min(int(begins[at] + sizes[at]), offset + size)
                    piece[lo - offset:hi - offset] = np.frombuffer(data, np.uint8, hi - lo, int(ats[at]) + lo - int(begins[at]))
            pieces.append(piece.tobytes())
        return pieces


def scene_states(records, rdram_size: int):
    """(task, its task regions or None, RDRAM, known mask) for every task, in
    order, with the level regions as they stood at that task."""
    state = SceneState(rdram_size)
    pending: list = []
    for record in records:
        if isinstance(record, Task):
            if pending:
                yield (pending[0], pending[1], *state.task_ram(pending[0], pending[1]))
            pending = [record, None]
        elif isinstance(record, Regions):
            if record.kind != "task":
                state.apply_level(record)
            elif pending and record.fields["task_index"] == pending[0].fields["task_index"]:
                pending[1] = record
    if pending:
        yield (pending[0], pending[1], *state.task_ram(pending[0], pending[1]))


class ViState:
    """RDRAM and SP DMEM as the VI regions last recorded them, in Project64's
    word order: a key rewrites every span, a delta patches its blocks."""

    def __init__(self, rdram_size: int):
        self.ram = np.zeros(rdram_size, np.uint8)
        self.known = np.zeros(rdram_size, bool)
        self.dmem = np.zeros(DMEM_BYTES, np.uint8)
        self.dmem_known = False

    def apply(self, record: ViRegions) -> None:
        at = 0
        for begin, size in record.spans:
            chunk = np.frombuffer(record.data, np.uint8, size, at)
            if begin >= DMEM_BEGIN:
                if begin - DMEM_BEGIN + size > DMEM_BYTES:
                    raise ValueError(f"VI record {record.sequence}: a span leaves SP DMEM")
                self.dmem[begin - DMEM_BEGIN:begin - DMEM_BEGIN + size] = chunk
                self.dmem_known = True
            else:
                if begin + size > len(self.ram):
                    raise ValueError(f"VI record {record.sequence}: a span leaves RDRAM")
                self.ram[begin:begin + size] = chunk
                self.known[begin:begin + size] = True
            at += size


def vi_states(records, rdram_size: int):
    """(VI record, the state after it) for every VI record, in order. The state is
    one object updated in place: copy what must outlive the next record."""
    state = ViState(rdram_size)
    for record in records:
        if isinstance(record, ViRegions):
            state.apply(record)
            yield record, state


def vi_summary(vis: list[dict], calls: int | None) -> dict:
    """VI records by kind, the indexes no record carries (lost), bytes and cost."""
    if not vis:
        return {"records": 0}
    indexes = [v["vi_index"] for v in vis]
    deltas = [v["data_bytes"] for v in vis if v["kind"] == 1]
    costs = sorted(v["total_ns"] / 1000 for v in vis)

    def pct(p):
        return costs[min(len(costs) - 1, int(p * (len(costs) - 1) + 0.5))]
    span = (calls if calls is not None else max(indexes) + 1)
    return {"records": len(vis), "keys": sum(v["kind"] == 0 for v in vis), "deltas": len(deltas),
            "lost": span - len(set(indexes)), "bytes": sum(v["data_bytes"] for v in vis),
            "delta_kib_p50": sorted(deltas)[len(deltas) // 2] / 1024 if deltas else 0,
            "us_p50": pct(.5), "us_p99": pct(.99), "us_max": costs[-1]}


def summary(args) -> int:
    session = Path(args.session)
    tasks = presents = refused = 0
    walk, copy, closure, reads = [], [], [], []
    region = {kind: {"bytes": [], "us": [], "scan_us": []} for kind in REGION_KINDS}
    vis = []
    for record in read_records(session / "tap.bin"):
        if isinstance(record, ViRegions):
            vis.append(record.fields)
        elif isinstance(record, Regions):
            region[record.kind]["bytes"].append(record.fields["data_bytes"])
            region[record.kind]["us"].append(record.fields["copy_ns"] / 1000)
            region[record.kind]["scan_us"].append(record.fields["scan_ns"] / 1000)
        elif isinstance(record, Task):
            tasks += 1
            refused += bool(record.fields["refusals"])
            walk.append(record.fields["walk_ns"] / 1000)
            copy.append(record.fields["copy_ns"] / 1000)
            closure.append(record.fields["closure_bytes"])
        elif isinstance(record, Present):
            presents += 1
            reads.append(record.read_ns / 1000)

    def pct(values, p):
        values = sorted(values)
        return values[min(len(values) - 1, int(p * (len(values) - 1) + 0.5))] if values else 0

    print(json.dumps({
        "tasks": tasks, "presents": presents, "refused": refused,
        "walk_us": {"p50": pct(walk, .5), "p99": pct(walk, .99), "max": max(walk, default=0)},
        "copy_us": {"p50": pct(copy, .5), "p99": pct(copy, .99), "max": max(copy, default=0)},
        "closure_kib": {"p50": pct(closure, .5) / 1024, "max": max(closure, default=0) / 1024},
        "reference_read_us": {"p50": pct(reads, .5), "p99": pct(reads, .99)},
        "regions": {kind: {"records": len(v["bytes"]), "kib_p50": pct(v["bytes"], .5) / 1024,
                           "kib_max": max(v["bytes"], default=0) / 1024, "us_p50": pct(v["us"], .5),
                           "us_p99": pct(v["us"], .99), "scan_us_p50": pct(v["scan_us"], .5)}
                    for kind, v in region.items() if v["bytes"]},
        "vi": vi_summary(vis, summary_calls(session)),
        "summary": (session / "summary.txt").read_text(encoding="ascii") if (session / "summary.txt").is_file() else None,
    }, indent=2))
    return 0


def summary_calls(session: Path) -> int | None:
    """The wrapper's count of VIs it tried to copy (summary.txt vi_calls), when it wrote one."""
    path = session / "summary.txt"
    if not path.is_file():
        return None
    for line in path.read_text(encoding="ascii").splitlines():
        if line.startswith("vi_calls="):
            return int(line.partition("=")[2])
    return None


def frames(args) -> int:
    session, out = Path(args.session), Path(args.out)
    header = session_header(session)
    rdram_size = int(header["rdram_size"])
    pattern = bytes([args.pattern]) * rdram_size
    identity = identity_spec(session)
    written = referenced = 0
    for task, record in paired(read_records(session / "tap.bin"), args.pair):
        if record is None:
            continue
        if args.every:
            if not record.pixels:
                continue
            referenced += 1
            if (referenced - 1) % args.every:
                continue
        rdram = bytearray(pattern)
        at = 0
        for begin, end in task.runs:
            rdram[begin:end] = task.run_bytes[at:at + end - begin]
            at += end - begin
        frame = out / f"{written:05d}-task{task.fields['task_index']:05d}"
        frame.mkdir(parents=True, exist_ok=True)
        (frame / "rdram.bin").write_bytes(rdram)
        dmem = bytearray(0x1000)
        struct.pack_into("<16I", dmem, 0xFC0, *task.task)
        (frame / "dmem.bin").write_bytes(dmem)
        if record.pixels:
            (frame / "ref.bgr").write_bytes(record.pixels)
        (frame / "frame.json").write_text(json.dumps({
            "schema": "scene-lab-frame/1",
            "source": {"kind": "scene-tap", "session": session.name, "task_sequence": task.sequence,
                       "present_sequence": record.sequence, "pattern": args.pattern},
            "identity": {name: word_value(task.extras, address, size) for name, address, size in identity},
            "vi": record.vi,
            "rom_header": header.get("rom_header", ""),
            "task": dict(zip(("type", "flags", "ucode_boot", "ucode_boot_size", "ucode", "ucode_size",
                              "ucode_data", "ucode_data_size", "dram_stack", "dram_stack_size",
                              "output_buff", "output_buff_size", "data_ptr", "data_size",
                              "yield_data_ptr", "yield_data_size"), task.task, strict=True)),
            "tap": task.fields,
            "reference": {"width": record.width, "height": record.height, "format": "bgr24-bottom-up"},
        }, indent=2) + "\n", encoding="utf-8")
        written += 1
        if args.limit and written >= args.limit:
            break
    print(f"{written} frames -> {out}")
    return 0


def build_offline_host() -> Path:
    """tap_offline_host.exe (x86, the wrapper's compiler flags, SCENE_TAP_ONE_SHOT). The installed
    app runs its own copy (core/paths.py bundled_lab_file) and never compiles; a checkout compiles
    the one recipe (tools/build_lab.py) into the scene lab's build cache, keyed by
    the build id it carries."""
    if getattr(sys, "frozen", False):
        from sm64_events.core.paths import bundled_lab_file
        path = bundled_lab_file("tap_offline_host.exe")
        if path is None:
            raise RuntimeError("This copy of SM64 Trainer is missing tap_offline_host.exe, the program that "
                               "loads saved scenes. Reinstall SM64 Trainer.")
        return path
    spec = importlib.util.spec_from_file_location("scene_lab_build", ROOT / "tools/build_lab.py")
    recipes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recipes)
    return recipes.cached("tap_offline_host.exe")


def _frame_argument(frame: str) -> str:
    """A frame folder for tap_offline_host, keeping a vi: prefix (one UpdateScreen) intact."""
    prefix = "vi:" if frame.startswith("vi:") else ""
    return prefix + str(Path(frame.removeprefix(prefix)).resolve())


def offline(args) -> int:
    """Arm under <lab_root>, feed the frames through the real producer, print the session."""
    root = Path(args.lab_root).resolve()
    lab = root / "SM64Trainer" / "scene-lab"
    lab.mkdir(parents=True, exist_ok=True)
    before = set(lab.glob("tap-*"))
    (lab / "armed.txt").write_text(arming_text(args), encoding="ascii")
    result = subprocess.run([str(build_offline_host()), str(root), *map(_frame_argument, args.frames)],
                            capture_output=True, text=True, check=False, timeout=600, **quiet_spawn_kwargs())
    if result.returncode != 0:
        raise SystemExit(f"tap_offline_host failed ({result.returncode}): {result.stdout}{result.stderr}")
    sessions = sorted(set(lab.glob("tap-*")) - before)
    if len(sessions) != 1 or not (sessions[0] / "tap.bin").is_file():
        raise SystemExit(f"expected one new session under {lab}, found {sessions}")
    print(sessions[0])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("summary")
    p.add_argument("session")
    p.set_defaults(run=summary)
    p = sub.add_parser("frames")
    p.add_argument("session")
    p.add_argument("out")
    p.add_argument("--pattern", type=lambda value: int(value, 0), default=0xA5)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--every", type=int, default=0, help="keep every N-th frame with a reference picture")
    p.add_argument("--pair", choices=("origin", "next"), default="next")
    p.set_defaults(run=frames)
    p = sub.add_parser("offline")
    p.add_argument("lab_root", help="stands in for %%LOCALAPPDATA%%; use a fresh folder per run")
    p.add_argument("frames", nargs="+")
    p.add_argument("--seconds", type=int, default=600)
    p.add_argument("--version", choices=("us", "jp"), default="us")
    p.add_argument("--no-scene-state", action="store_true")
    p.add_argument("--no-audio-state", action="store_true")
    p.set_defaults(run=offline, no_reference=True, reference_every=1, full_every=0)
    args = parser.parse_args()
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
