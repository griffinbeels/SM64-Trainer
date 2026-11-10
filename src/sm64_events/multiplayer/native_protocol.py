"""Fixed local live-receiver ABI; distinct from captured poses and SDK IPC.

The current native capture lease supplies LocalOwner. Network values never
select a process, local model, cartridge, pointer or file. All clocks here are
same-machine QPC scaled to nanoseconds, not a remote capture-age estimate.
"""
from __future__ import annotations

import ctypes as C
from dataclasses import dataclass
import math

from sm64_events.multiplayer.protocol import PACKET_BYTES

PREFIX = "Local\\sm64_trainer_live_v1_"
MAGIC = int.from_bytes(b"SMLI", "little")
VERSION, PAGE_BYTES, REQUEST_OFFSET, UPDATE_BYTES = 2, 4096, 256, 1692
STALE_AFTER_NS = 250_000_000
LABEL_BYTES, MAX_PEERS = 64, 3


class LocalOwner(C.LittleEndianStructure):
    _pack_ = 4
    _fields_ = [(name, C.c_uint32) for name in (
        "pid", "created_lo", "created_hi", "generation", "rom_generation", "configuration")
    ] + [("token", C.c_uint64), ("model_id", C.c_uint64)]


class Controls(C.LittleEndianStructure):
    _pack_ = 4
    _fields_ = [(name, C.c_float) for name in (
        "opacity", "proximity_distance", "name_background_opacity")
    ] + [(name, C.c_uint32) for name in (
        "tint_enabled", "proximity_enabled", "occlusion_fade", "names_enabled", "name_background",
        "record_ghosts", "inputs_enabled", "input_mode", "hud_inputs_enabled")]


class Peer(C.LittleEndianStructure):
    _pack_ = 4
    _fields_ = [("incarnation", C.c_ubyte * 16), ("member", C.c_uint32),
        ("present", C.c_uint32), ("hue_degrees", C.c_uint32), ("label", C.c_char * LABEL_BYTES),
        ("received_ns", C.c_uint64), ("packet", C.c_ubyte * PACKET_BYTES)]


class Update(C.LittleEndianStructure):
    _pack_ = 4
    _fields_ = [("owner", LocalOwner), ("room", C.c_ubyte * 16), ("revision", C.c_uint64),
        ("enabled", C.c_uint32), ("hidden", C.c_uint32), ("controls", Controls), ("peers", Peer * MAX_PEERS)]


class Header(C.LittleEndianStructure):
    _pack_ = 4
    _fields_ = [(name, C.c_uint32) for name in (
        "magic", "version", "bytes", "request_bytes", "producer_pid", "producer_created_lo",
        "producer_created_hi", "generation", "state", "reason")
    ] + [("applied_revision", C.c_uint64), ("receipt_room", C.c_ubyte * 16),
        ("receipt_owner", LocalOwner)] + [(name, C.c_uint32) for name in (
            "presentation_reason", "eligible_mask", "selected_mask", "displayed_mask")
        ] + [(name, C.c_uint64) for name in ("updates", "refusals", "drops", "presentations")
        ] + [(name, C.c_uint32) for name in ("gl_major", "gl_minor", "ineligible_mask")
        ] + [("reserved", C.c_uint32 * 23)]


PRESENTATION_REASONS = ("waiting", "hidden", "different_scene", "unsupported", "ineligible",
    "setup_pending", "gateway_refused", "compositor_refused", "ready", "displayed")


assert C.sizeof(LocalOwner) == 40 and C.sizeof(Controls) == 48
assert C.sizeof(Peer) == 524 and Peer.packet.offset == 100
assert C.sizeof(Update) == UPDATE_BYTES and Update.peers.offset == 120
assert C.sizeof(Header) == REQUEST_OFFSET


@dataclass(frozen=True)
class Appearance:
    opacity: float = 1.0
    proximity_distance: float = 300.0
    name_background_opacity: float = .6
    tint_enabled: bool = True
    proximity_enabled: bool = True
    occlusion_fade: bool = True
    names_enabled: bool = True
    name_background: int = 0
    record_ghosts: bool = False
    inputs_enabled: bool = False
    input_mode: int = 0
    hud_inputs_enabled: bool = False

    def native(self) -> Controls:
        value = Controls()
        for name, low, high in (("opacity", 0, 1), ("proximity_distance", 0, 400),
                                ("name_background_opacity", 0, 1)):
            number = getattr(self, name)
            if type(number) not in (int, float) or not math.isfinite(number) or not low <= number <= high:
                raise ValueError("Live ghost appearance is invalid.")
            setattr(value, name, number)
        for name in ("tint_enabled", "proximity_enabled", "occlusion_fade", "names_enabled",
                     "record_ghosts", "inputs_enabled", "hud_inputs_enabled"):
            if type(getattr(self, name)) is not bool:
                raise ValueError("Live ghost switch is invalid.")
            setattr(value, name, int(getattr(self, name)))
        for name, maximum in (("name_background", 1), ("input_mode", 1)):
            number = getattr(self, name)
            if type(number) is not int or not 0 <= number <= maximum:
                raise ValueError("Live ghost appearance choice is invalid.")
            setattr(value, name, number)
        return value
