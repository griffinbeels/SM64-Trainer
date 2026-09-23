"""Fixed-width leased Ghost IPC, mirrored by plugin/gfxwrap/ghost_ipc.h.

Every page access requires the named mutex. Only the native worker touches IPC;
the graphics thread uses the separate bounded native queue.
"""
from __future__ import annotations

import ctypes as C
from dataclasses import dataclass
from pathlib import Path

from sm64_events.ghost.native_layout import (  # sizes and limits: native_layout.py
    GHOSTS, IPC_MAGIC as MAGIC, IPC_PATH_CHARS as PATH_CHARS, IPC_PREFIX, IPC_RING_AT as RING_OFFSET,
    IPC_SLOTS as SLOTS, IPC_VERSION as VERSION, LAYOUT_BYTES, RECORD_BYTES,
)

PREFIX = IPC_PREFIX  # the named mapping, mutex and event, plus the producer pid
PAGE_BYTES = RING_OFFSET + SLOTS * RECORD_BYTES
U32, U64, I32 = C.c_uint32, C.c_uint64, C.c_int32


class Origin(C.LittleEndianStructure):
    _pack_ = 4
    _fields_ = [("kind", U32), ("configuration", U32), ("epoch", U32),
                ("global_timer", U32), ("utc", C.c_double),
                ("session_id", U64), ("arm_id", U64)]


class Clip(C.LittleEndianStructure):
    """One displayed recording; hue is per ghost, tint_enabled stays global."""
    _pack_ = 4
    _fields_ = [("clip_id", C.c_ubyte * 16), ("clip_sha256", C.c_ubyte * 32),
                ("hue_degrees", U32), ("reserved", U32),
                ("clip_path", C.c_uint16 * PATH_CHARS)]


class Request(C.LittleEndianStructure):
    _pack_ = 4
    _fields_ = [
        ("owner_pid", U32), ("owner_created_lo", U32), ("owner_created_hi", U32),
        ("generation", U32), ("token", U64), ("revision", U64),
        ("heartbeat", U32), ("capture_enabled", U32), ("play_enabled", U32),
        ("mode", U32), ("seek_igt", U32), ("nudge", I32), ("opacity", C.c_float),
        ("stop_at_star", U32), ("rom_generation", U32), ("owner_suspended", U32),
        ("model_id", U64),
        ("layout", C.c_ubyte * LAYOUT_BYTES), ("rom_path", C.c_uint16 * PATH_CHARS),
        ("star_action_count", U32), ("star_actions", U32 * 8),
        ("tint_enabled", U32),
        ("proximity_enabled", U32), ("proximity_distance", C.c_float),
        ("origin", Origin), ("clip_count", U32), ("reserved1", U32),
        ("clips", Clip * GHOSTS),
        ("shadow_enabled", U32), ("occlusion_fade", U32),
        ("volume", C.c_float), ("reserved3", U32),
    ]


class Status(C.LittleEndianStructure):
    _pack_ = 4
    _fields_ = [
        ("magic", C.c_char * 8), ("version", U32), ("bytes", U32),
        ("producer_pid", U32), ("producer_created_lo", U32),
        ("producer_created_hi", U32), ("generation", U32),
        ("rom_generation", U32), ("rom_open", U32), ("state", U32), ("reason", U32),
        ("configuration", U32), ("epoch", U32), ("record_bytes", U32),
        ("capabilities", U32), ("qpc_frequency", U64), ("qpc_anchor", C.c_int64),
        ("utc_anchor", C.c_double), ("produced", U64), ("consumed", U64),
        ("dropped", U64), ("ack_revision", U64), ("ack_token", U64),
        ("playback_state", U32), ("playback_reason", U32),
        ("displayed", U32), ("clips_requested", U32), ("clips_prepared", U32),
        ("clips_visible", U32), ("particles_dropped", U32),
        ("hook_gl_queries", U32), ("hook_micros", U32), ("reserved", C.c_ubyte * 92),
    ]


assert C.sizeof(Origin) == 40 and C.sizeof(Clip) == 1096 and C.sizeof(Request) == 18956
assert C.sizeof(Status) == 256 and 256 + C.sizeof(Request) <= RING_OFFSET
assert Request.layout.offset == 80 and Status.consumed.offset == 96


@dataclass(frozen=True)
class Producer:
    pid: int
    created: int
    generation: int
    rom_generation: int
    configuration: int
    epoch: int
    qpc_frequency: int
    qpc_anchor: int
    utc_anchor: float

    @classmethod
    def from_status(cls, status: Status) -> Producer:
        return cls(status.producer_pid,
                   status.producer_created_lo | (status.producer_created_hi << 32),
                   status.generation, status.rom_generation, status.configuration,
                   status.epoch, status.qpc_frequency, status.qpc_anchor, status.utc_anchor)


def path_units(path: str | Path) -> tuple[int, ...]:
    """Explicit UTF16 avoids host wchar width and never truncates a path."""
    text = str(Path(path).resolve())
    if "\0" in text:
        raise ValueError("ghost path contains a null character")
    encoded = text.encode("utf-16-le")
    units = tuple(int.from_bytes(encoded[i:i + 2], "little")
                  for i in range(0, len(encoded), 2))
    if len(units) >= PATH_CHARS:
        raise ValueError("ghost path exceeds the native channel limit")
    return units


def checked_status(raw: bytes, pid: int) -> Status:
    if len(raw) != C.sizeof(Status):
        raise ValueError("incomplete ghost status")
    status = Status.from_buffer_copy(raw)
    if (status.magic != MAGIC or status.version != VERSION or status.bytes != PAGE_BYTES
            or status.producer_pid != pid or not status.generation
            or status.record_bytes != RECORD_BYTES or not status.qpc_frequency
            or status.consumed > status.produced
            or status.produced - status.consumed > SLOTS):
        raise ValueError("unsupported or inconsistent ghost channel")
    return status
