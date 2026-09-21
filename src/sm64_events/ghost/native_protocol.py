"""Fixed-width leased Ghost IPC, mirrored by plugin/gfxwrap/ghost_ipc.h.

Every page access requires the named mutex. Only the native worker touches IPC;
the graphics thread uses the separate bounded native queue.
"""
from __future__ import annotations

import ctypes as C
from dataclasses import dataclass
from pathlib import Path

MAGIC = b"SM64GIP1"
VERSION = 1
PREFIX = "Local\\sm64_trainer_ghost_v1_"
SLOTS, RECORD_BYTES, RING_OFFSET = 512, 192, 4096
PAGE_BYTES = RING_OFFSET + SLOTS * RECORD_BYTES
U32, U64, I32 = C.c_uint32, C.c_uint64, C.c_int32


class Request(C.LittleEndianStructure):
    _pack_ = 4
    _fields_ = [
        ("owner_pid", U32), ("owner_created_lo", U32), ("owner_created_hi", U32),
        ("generation", U32), ("token", U64), ("revision", U64),
        ("heartbeat", U32), ("capture_enabled", U32), ("play_enabled", U32),
        ("mode", U32), ("seek_igt", I32), ("nudge", I32), ("opacity", C.c_float),
        ("stop_at_star", U32), ("rom_generation", U32), ("reserved", U32),
        ("model_id", U64), ("clip_id", C.c_ubyte * 16),
        ("layout", C.c_ubyte * 64), ("rom_path", C.c_uint16 * 520),
        ("clip_path", C.c_uint16 * 520), ("clip_sha256", C.c_ubyte * 32),
        ("star_action_count", U32), ("star_actions", U32 * 8),
        ("tint_enabled", U32), ("hue_degrees", U32),
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
        ("displayed", U32), ("reserved", C.c_ubyte * 116),
    ]


assert C.sizeof(Request) == 2316 and C.sizeof(Status) == 256
assert Request.layout.offset == 96 and Status.consumed.offset == 96


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
    if len(units) >= 520:
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
