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
    IPC_SLOTS as SLOTS, IPC_VERSION as VERSION, IPC_CLIP_BYTES, IPC_REQUEST_BYTES,
    LABEL_BYTES, LAYOUT_BYTES, RECORD_BYTES,
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
                ("clip_path", C.c_uint16 * PATH_CHARS), ("label", C.c_char * LABEL_BYTES)]


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
        ("volume", C.c_float), ("sound_fade", U32),
        ("names_enabled", U32), ("inputs_enabled", U32), ("input_mode", U32),
        ("name_background", U32), ("name_background_opacity", C.c_float), ("hud_inputs_enabled", U32),
        ("record_ghosts", U32),
        # The ghost path: path_clip indexes `clips` (the fastest playing ghost).
        ("path_enabled", U32), ("path_buttons", U32), ("path_source", U32), ("path_clip", U32),
        ("path_ahead_ms", U32), ("path_width", C.c_float), ("path_opacity", C.c_float),
        ("path_spacing_ms", U32),
        ("prepare_generation", U64), ("activate_generation", U64), ("activate_start", U64),
        ("cancel_preparation", U32),
    ]


# The last list's hook cost after its query count and time (gs_hook_cost order).
HOOK_STAGES = tuple((f"hook_{name}", U32) for name in (
    "list", "list_micros", "flush_micros", "open_micros", "batch_micros", "validate_micros", "close_micros",
    "finalize_micros", "state_micros", "copy_micros", "draw_micros", "draw_max_micros",
    "restore_micros", "hud_micros", "hud_draws", "layer_passes", "query_micros", "error_reads",
    "ghosts"))


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
        ("hook_gl_queries", U32), ("hook_micros", U32), *HOOK_STAGES,
        ("prepared_generation", U64), ("active_generation", U64),
    ]


assert C.sizeof(Origin) == 40 and C.sizeof(Clip) == IPC_CLIP_BYTES and C.sizeof(Request) == IPC_REQUEST_BYTES
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


def label_bytes(label: str) -> bytes:
    """A display credit, bounded UTF-8; malformed commands never partly apply."""
    if not isinstance(label, str) or any(ord(char) < 32 or ord(char) == 127 for char in label):
        raise ValueError("ghost label must be text without control characters")
    try:
        encoded = label.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("ghost label must be valid UTF-8") from error
    if len(encoded) >= LABEL_BYTES:
        raise ValueError("ghost label exceeds the native channel limit")
    return encoded


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
