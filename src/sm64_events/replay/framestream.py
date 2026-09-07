"""THE FRAME STREAM: the shared-memory ring the capture layer writes and the
recorder reads (round 32 item 95).

One named mapping, `NAME`, session-local; one named auto-reset event the
plugin sets after each published slot. Both sides compute the mapping's
size from the constants below, so whoever opens it first creates it and the
other side joins the same object -- the plugin at InitiateGFX, the tracker
at boot, in either order.

Layout (every field little-endian; offsets are the contract, mirrored by
``plugin/gfxwrap/stream.h`` and pinned by ``tests/test_framestream_layout.py``
against the offsets the C side prints):

    header (HEADER_BYTES)
      0   magic[8] = "SM64GFX1"        52  write_seq   (last completed slot)
      8   version                      56  dropped     (presents with no free slot)
      12  header_bytes                 60  wrapped_version (PLUGIN_INFO.Version)
      16  plugin_pid                   64  plugin_version  (GFXWRAP_VERSION)
      20  alive     (++ per UpdateScreen)  68  lists   (++ per ProcessDList)
      24  status    (STATUS_* bits)    128 wrapped_name[128]
      28  width  32 height  36 format  --- tracker -> plugin ---
      40  slot_count                   256 want_frames
      44  slot_bytes                   260 rdram_bytes
      48  slots_offset                 264 table_count
                                       268 tracker_alive
                                       272 table[16] of {rdram_offset, length}
    slot (SLOT_BYTES, SLOT_COUNT of them from slots_offset)
      0   seq        4   kind (1 = picture)
      8   list_qpc   16  present_qpc   (QPC ticks; perf_counter's clock)
      24  vi_origin  28  width  32 height  36 stride
      40  table_count  44  lists_since
      48  lengths[16]  116 seq_end
      128 table bytes: 16 x TABLE_ENTRY_BYTES
      4096 pixels: stride x height, FORMAT_BGR8_BOTTOM_UP (glReadPixels order)

The writer fills a slot, then `seq_end`, then the header's `write_seq`,
each behind a memory barrier; a reader copies the slot and re-checks that
`seq == seq_end == the seq it started on` -- a torn slot (the ring wrapped
under the copy) is skipped and counted, never delivered.
"""
from __future__ import annotations

import ctypes
import mmap
import struct
from dataclasses import dataclass

import numpy as np

NAME = "sm64_trainer_gfx_v1"
EVENT_NAME = NAME + "_frame"
MAGIC = b"SM64GFX1"
VERSION = 1

HEADER_BYTES = 4096
SLOT_COUNT = 6                           # ~200 ms of pictures at 30/s; only touched pages cost memory
MAX_WIDTH = 3840
MAX_HEIGHT = 2160
BYTES_PER_PIXEL = 3
TABLE_ENTRIES = 16
TABLE_ENTRY_BYTES = 128                  # Mario's block is 76 bytes; a stick/button struct 32
SLOT_META_BYTES = 4096
SLOT_BYTES = SLOT_META_BYTES + MAX_WIDTH * MAX_HEIGHT * BYTES_PER_PIXEL
TOTAL_BYTES = HEADER_BYTES + SLOT_COUNT * SLOT_BYTES

H_MAGIC, H_VERSION, H_HEADER_BYTES, H_PLUGIN_PID, H_ALIVE, H_STATUS = 0, 8, 12, 16, 20, 24
H_WIDTH, H_HEIGHT, H_FORMAT, H_SLOT_COUNT, H_SLOT_BYTES, H_SLOTS_OFFSET = 28, 32, 36, 40, 44, 48
H_WRITE_SEQ, H_DROPPED, H_WRAPPED_VERSION, H_PLUGIN_VERSION, H_LISTS = 52, 56, 60, 64, 68
H_WRAPPED_NAME = 128
H_WRAPPED_NAME_BYTES = 128
H_WANT_FRAMES, H_RDRAM_BYTES, H_TABLE_COUNT, H_TRACKER_ALIVE = 256, 260, 264, 268
H_TABLE = 272

STATUS_WRAPPED_LOADED = 1
STATUS_GL_CONTEXT = 2
STATUS_ROM_OPEN = 4
STATUS_FRAME_TOO_LARGE = 8
STATUS_INITIATED = 16
STATUS_READSCREEN = 32         # pictures come through the wrapped plugin's own ReadScreen
FORMAT_BGR8_BOTTOM_UP = 1
KIND_PICTURE = 1

S_SEQ, S_KIND, S_LIST_QPC, S_PRESENT_QPC, S_VI_ORIGIN = 0, 4, 8, 16, 24
S_WIDTH, S_HEIGHT, S_STRIDE, S_TABLE_COUNT, S_LISTS_SINCE = 28, 32, 36, 40, 44
S_LENGTHS, S_SEQ_END, S_TABLE, S_PIXELS = 48, 116, 128, 4096

#: every named constant above, for the C side's parity test
LAYOUT = {name: value for name, value in globals().items()
          if name.isupper() and isinstance(value, int) and not name.startswith("_")}

_U32 = struct.Struct("<I")
_I64 = struct.Struct("<q")
_EVENT_ALL_ACCESS = 0x1F0003
_WAIT_OBJECT_0 = 0


@dataclass(frozen=True)
class Header:
    version: int
    plugin_pid: int
    alive: int
    status: int
    width: int
    height: int
    format: int
    write_seq: int
    dropped: int
    wrapped_version: int
    plugin_version: int
    lists: int
    wrapped_name: str
    want_frames: int
    table_count: int
    tracker_alive: int

    @property
    def initiated(self) -> bool:
        return bool(self.status & STATUS_INITIATED)


@dataclass(frozen=True)
class Slot:
    seq: int
    list_qpc: int
    present_qpc: int
    vi_origin: int
    width: int
    height: int
    stride: int
    lists_since: int
    table: tuple          # bytes per entry, b"" where the plugin copied nothing
    pixels: np.ndarray    # (height, width, 3) uint8, bottom-up rows as stored


def stride_of(width: int) -> int:
    """glReadPixels packs BGR rows to GL_PACK_ALIGNMENT 4."""
    return (width * BYTES_PER_PIXEL + 3) & ~3


class FrameStream:
    """Open (or create) the mapping and the event by name."""

    def __init__(self, name: str = NAME):
        self.name = name
        self._map = mmap.mmap(-1, TOTAL_BYTES, tagname=name)
        self._view = np.frombuffer(self._map, dtype=np.uint8)
        if bytes(self._map[H_MAGIC:H_MAGIC + 8]) != MAGIC:
            self._initialise()
        elif self._u32(H_VERSION) != VERSION:
            raise RuntimeError(f"frame stream {name}: version {self._u32(H_VERSION)}, "
                               f"this build speaks {VERSION}")
        kernel32 = ctypes.windll.kernel32
        # HANDLEs are pointer-sized: without restype/argtypes ctypes would
        # squeeze them through a C int (fresh-context review, 2026-09-05).
        kernel32.CreateEventW.restype = ctypes.c_void_p
        kernel32.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                          ctypes.c_wchar_p]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.SetEvent.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        self._event = kernel32.CreateEventW(None, False, False, name + "_frame")
        if not self._event:
            self._map.close()
            raise OSError(ctypes.get_last_error(), f"frame stream {name}: no event")
        self._wait_for = kernel32.WaitForSingleObject
        self._set_event = kernel32.SetEvent
        self._close_handle = kernel32.CloseHandle

    # -- raw access ------------------------------------------------------
    def _u32(self, offset: int) -> int:
        return _U32.unpack_from(self._map, offset)[0]

    def _put_u32(self, offset: int, value: int) -> None:
        _U32.pack_into(self._map, offset, value & 0xFFFFFFFF)

    def _i64(self, offset: int) -> int:
        return _I64.unpack_from(self._map, offset)[0]

    def _initialise(self) -> None:
        """The geometry both sides agree on; written by whoever comes first."""
        self._map[H_MAGIC:H_MAGIC + 8] = MAGIC
        self._put_u32(H_VERSION, VERSION)
        self._put_u32(H_HEADER_BYTES, HEADER_BYTES)
        self._put_u32(H_SLOT_COUNT, SLOT_COUNT)
        self._put_u32(H_SLOT_BYTES, SLOT_BYTES)
        self._put_u32(H_SLOTS_OFFSET, HEADER_BYTES)

    @staticmethod
    def slot_offset(seq: int) -> int:
        return HEADER_BYTES + ((seq - 1) % SLOT_COUNT) * SLOT_BYTES

    # -- the header --------------------------------------------------------
    def header(self) -> Header:
        raw_name = bytes(self._map[H_WRAPPED_NAME:H_WRAPPED_NAME + H_WRAPPED_NAME_BYTES])
        return Header(
            version=self._u32(H_VERSION), plugin_pid=self._u32(H_PLUGIN_PID),
            alive=self._u32(H_ALIVE), status=self._u32(H_STATUS),
            width=self._u32(H_WIDTH), height=self._u32(H_HEIGHT),
            format=self._u32(H_FORMAT), write_seq=self._u32(H_WRITE_SEQ),
            dropped=self._u32(H_DROPPED),
            wrapped_version=self._u32(H_WRAPPED_VERSION),
            plugin_version=self._u32(H_PLUGIN_VERSION), lists=self._u32(H_LISTS),
            wrapped_name=raw_name.split(b"\0", 1)[0].decode("utf-8", "replace"),
            want_frames=self._u32(H_WANT_FRAMES),
            table_count=self._u32(H_TABLE_COUNT),
            tracker_alive=self._u32(H_TRACKER_ALIVE))

    def set_table(self, entries: list, rdram_bytes: int) -> None:
        """What the plugin copies out of RDRAM at every ProcessDList:
        `entries` = [(rdram_offset, length)], word-aligned, each at most
        TABLE_ENTRY_BYTES. The count goes in LAST so the plugin never reads
        a half-written table as a whole one."""
        if len(entries) > TABLE_ENTRIES:
            raise ValueError(f"at most {TABLE_ENTRIES} table entries")
        self._put_u32(H_TABLE_COUNT, 0)
        for index, (offset, length) in enumerate(entries):
            if offset % 4 or length % 4 or length > TABLE_ENTRY_BYTES or length <= 0:
                raise ValueError(f"table entry {index}: offset {offset:#x} length {length} "
                                 "must be word-aligned and at most "
                                 f"{TABLE_ENTRY_BYTES} bytes")
            self._put_u32(H_TABLE + index * 8, offset)
            self._put_u32(H_TABLE + index * 8 + 4, length)
        self._put_u32(H_RDRAM_BYTES, rdram_bytes)
        self._put_u32(H_TABLE_COUNT, len(entries))

    def table(self) -> list:
        count = min(self._u32(H_TABLE_COUNT), TABLE_ENTRIES)
        return [(self._u32(H_TABLE + index * 8), self._u32(H_TABLE + index * 8 + 4))
                for index in range(count)]

    def set_want_frames(self, on: bool) -> None:
        self._put_u32(H_WANT_FRAMES, 1 if on else 0)

    def touch(self) -> None:
        """Renew the reader's lease; gfxwrap v2 expires demand after 3s.

        Only the recorder owner writes this. Setting want_frames alone is
        insufficient: a crashed reader can leave that bit set in PJ64's map.
        """
        self._put_u32(H_TRACKER_ALIVE, self._u32(H_TRACKER_ALIVE) + 1)

    def alive_since(self, previous_alive: int) -> bool:
        return self._u32(H_ALIVE) != previous_alive

    def plugin_process_alive(self) -> bool:
        """Is the process that wrote `plugin_pid` still running? A crash
        leaves the header's bits behind; the process is the truth."""
        pid = self._u32(H_PLUGIN_PID)
        if not pid:
            return False
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION: waiting on the
        # handle needs the first, and nothing here needs more than the second
        handle = kernel32.OpenProcess(0x100000 | 0x1000, False, pid)
        if not handle:
            return False
        try:
            return self._wait_for(handle, 0) == 0x102             # WAIT_TIMEOUT: still running
        finally:
            self._close_handle(handle)

    # -- the slots ---------------------------------------------------------
    def wait(self, timeout_s: float) -> bool:
        """True when the plugin signalled a new slot within the timeout."""
        return self._wait_for(self._event, int(max(timeout_s, 0) * 1000)) == _WAIT_OBJECT_0

    def read_new(self, after_seq: int) -> tuple:
        """Every slot with seq in (after_seq, write_seq], oldest first, and
        the number skipped as torn or already overwritten."""
        write_seq = self._u32(H_WRITE_SEQ)
        if write_seq < after_seq:
            # The plugin restarted on a recreated mapping (seq back to 1):
            # follow it rather than wait forever for a seq it will never
            # reach again.
            after_seq = 0
        slots, skipped = [], 0
        first = max(after_seq + 1, write_seq - SLOT_COUNT + 1)
        skipped += max(0, first - (after_seq + 1))     # overwritten before we looked
        for seq in range(first, write_seq + 1):
            slot = self._read_slot(seq)
            if slot is None:
                skipped += 1
            else:
                slots.append(slot)
        return slots, skipped

    def _read_slot(self, seq: int) -> Slot | None:
        base = self.slot_offset(seq)
        if self._u32(base + S_SEQ) != seq or self._u32(base + S_SEQ_END) != seq:
            return None
        width, height = self._u32(base + S_WIDTH), self._u32(base + S_HEIGHT)
        stride = self._u32(base + S_STRIDE)
        if (width == 0 or height == 0 or stride < width * BYTES_PER_PIXEL
                or stride * height > SLOT_BYTES - SLOT_META_BYTES):
            return None
        lengths = [self._u32(base + S_LENGTHS + index * 4) for index in range(TABLE_ENTRIES)]
        table = tuple(
            bytes(self._map[base + S_TABLE + index * TABLE_ENTRY_BYTES:
                            base + S_TABLE + index * TABLE_ENTRY_BYTES
                            + min(lengths[index], TABLE_ENTRY_BYTES)])
            for index in range(TABLE_ENTRIES))
        # Copy every field before the final sequence check. Reading timing or
        # exactness afterwards could pair old pixels/table bytes with metadata
        # from a slot the producer had just overwritten.
        list_qpc = self._i64(base + S_LIST_QPC)
        present_qpc = self._i64(base + S_PRESENT_QPC)
        vi_origin = self._u32(base + S_VI_ORIGIN)
        lists_since = self._u32(base + S_LISTS_SINCE)
        pixels = self._view[base + S_PIXELS:base + S_PIXELS + stride * height]
        pixels = pixels.reshape(height, stride)[:, :width * BYTES_PER_PIXEL]
        pixels = pixels.reshape(height, width, BYTES_PER_PIXEL).copy()
        if self._u32(base + S_SEQ) != seq or self._u32(base + S_SEQ_END) != seq:
            return None                                  # torn under the copy
        return Slot(seq=seq, list_qpc=list_qpc, present_qpc=present_qpc,
                    vi_origin=vi_origin, width=width,
                    height=height, stride=stride,
                    lists_since=lists_since,
                    table=table, pixels=pixels)

    # -- the writer's side, in Python: the test double for the plugin ------
    def publish(self, pixels_bgr_bottom_up: np.ndarray, table: list,
                list_qpc: int = 0, present_qpc: int = 0, vi_origin: int = 0,
                lists_since: int = 1, torn: bool = False) -> int:
        """Write the next slot the way the C side does and signal it; returns
        its seq. `torn=True` leaves seq_end stale (a reader must skip it)."""
        height, width = pixels_bgr_bottom_up.shape[:2]
        stride = stride_of(width)
        seq = self._u32(H_WRITE_SEQ) + 1
        base = self.slot_offset(seq)
        self._put_u32(base + S_SEQ, seq)
        self._put_u32(base + S_SEQ_END, 0)
        self._put_u32(base + S_KIND, KIND_PICTURE)
        _I64.pack_into(self._map, base + S_LIST_QPC, list_qpc)
        _I64.pack_into(self._map, base + S_PRESENT_QPC, present_qpc)
        self._put_u32(base + S_VI_ORIGIN, vi_origin)
        self._put_u32(base + S_WIDTH, width)
        self._put_u32(base + S_HEIGHT, height)
        self._put_u32(base + S_STRIDE, stride)
        self._put_u32(base + S_TABLE_COUNT, len(table))
        self._put_u32(base + S_LISTS_SINCE, lists_since)
        for index in range(TABLE_ENTRIES):
            chunk = table[index] if index < len(table) else b""
            self._put_u32(base + S_LENGTHS + index * 4, len(chunk))
            at = base + S_TABLE + index * TABLE_ENTRY_BYTES
            self._map[at:at + len(chunk)] = chunk
        rows = np.zeros((height, stride), dtype=np.uint8)
        rows[:, :width * BYTES_PER_PIXEL] = pixels_bgr_bottom_up.reshape(height, -1)
        self._view[base + S_PIXELS:base + S_PIXELS + stride * height] = rows.reshape(-1)
        if not torn:
            self._put_u32(base + S_SEQ_END, seq)
        self._put_u32(H_WRITE_SEQ, seq)
        self._put_u32(H_WIDTH, width)
        self._put_u32(H_HEIGHT, height)
        self._put_u32(H_FORMAT, FORMAT_BGR8_BOTTOM_UP)
        self._put_u32(H_ALIVE, self._u32(H_ALIVE) + 1)
        self._set_event(self._event)
        return seq

    def set_plugin_fields(self, status: int, wrapped_name: str = "",
                          plugin_pid: int = 0, plugin_version: int = 1,
                          dropped: int | None = None, alive: int | None = None) -> None:
        """The plugin's own header fields, for a Python stand-in."""
        self._put_u32(H_STATUS, status)
        self._put_u32(H_PLUGIN_PID, plugin_pid)
        self._put_u32(H_PLUGIN_VERSION, plugin_version)
        if dropped is not None:
            self._put_u32(H_DROPPED, dropped)
        if alive is not None:
            self._put_u32(H_ALIVE, alive)
        raw = wrapped_name.encode("utf-8")[:H_WRAPPED_NAME_BYTES - 1]
        self._map[H_WRAPPED_NAME:H_WRAPPED_NAME + H_WRAPPED_NAME_BYTES] = raw.ljust(
            H_WRAPPED_NAME_BYTES, b"\0")

    def close(self) -> None:
        self._view = None
        try:
            self._map.close()
        finally:
            self._close_handle(self._event)
