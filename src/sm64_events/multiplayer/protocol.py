"""Bounded experimental live poses; no transport, admission or import side effects.

The C layout lives in ghost_live_protocol.h. A transport must first bind its
authenticated peer and room assignment to expected_incarnation. Matching a
packet nonce alone never grants membership or certifies a player's performance.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct

from sm64_events.ghost.clip import ClipError, Frame, frame_from_bytes
from sm64_events.ghost import native_layout as L

MAGIC, VERSION, MAX_PEERS = b"SMLV", 0, 3
HEADER = struct.Struct("<4s5I16s3Q")
CORE_BYTES = L.SHADOW_AT - L.RECORD_PREFIX_BYTES
PACKET_BYTES = HEADER.size + CORE_BYTES + L.HELD_BYTES + L.PAD_BYTES
VALID_FIELDS = L.VALID_REQUIRED | L.VALID_HELD | L.VALID_PAD
_U64_MAX = (1 << 64) - 1


class PoseError(ValueError):
    """A packet cannot establish a supported live source occurrence."""


@dataclass(frozen=True)
class LivePose:
    participant_incarnation: bytes
    source_generation: int
    elapsed_ns: int
    continuity: int
    rom_layout: int
    rom_generation: int
    frame: Frame


def _incarnation(value: bytes) -> bytes:
    if not isinstance(value, bytes) or len(value) != 16 or not any(value):
        raise PoseError("Live participant incarnation must be a nonzero 16-byte identity.")
    return value


def _bounded_bytes(value: bytes, size: int, name: str) -> bytes:
    """Check byte count before copying, including non-byte memoryview formats."""
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise PoseError(f"Live {name} has the wrong size.")
    try:
        view = memoryview(value)
    except (TypeError, ValueError) as error:
        raise PoseError(f"Live {name} is not a readable buffer.") from error
    if view.nbytes != size:
        raise PoseError(f"Live {name} has the wrong size.")
    return view.tobytes()


def _integer(value: int, name: str, maximum: int, *, minimum: int = 1) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise PoseError(f"Live {name} is outside its field.")
    return value


def _metadata(incarnation, generation, elapsed_ns, continuity, rom_layout, rom_generation):
    _incarnation(incarnation)
    _integer(generation, "source generation", _U64_MAX)
    _integer(elapsed_ns, "capture time", _U64_MAX, minimum=0)
    _integer(continuity, "presentation continuity", _U64_MAX)
    _integer(rom_generation, "ROM generation", (1 << 32) - 1)
    if type(rom_layout) is not int or rom_layout not in (L.ROM_US, L.ROM_JP):
        raise PoseError("Live source cartridge is unsupported.")


def encode_pose(raw_record: bytes, *, participant_incarnation: bytes,
                source_generation: int, elapsed_ns: int, continuity: int,
                rom_layout: int, rom_generation: int) -> bytes:
    """Extract source facts; omit cosmetic blocks without inventing unknown pad."""
    _metadata(participant_incarnation, source_generation, elapsed_ns, continuity,
              rom_layout, rom_generation)
    raw_record = _bounded_bytes(raw_record, L.RECORD_BYTES, "source record")
    try:
        frame = frame_from_bytes(raw_record)
    except (ClipError, TypeError, struct.error) as error:
        raise PoseError("Live source record is invalid.") from error
    core = bytearray(frame.raw[L.RECORD_PREFIX_BYTES:L.SHADOW_AT])
    struct.pack_into("<I", core, 12, frame.valid_fields & VALID_FIELDS)
    return (HEADER.pack(MAGIC, VERSION, PACKET_BYTES, rom_layout, frame.configuration,
                        rom_generation, participant_incarnation, source_generation,
                        elapsed_ns, continuity)
            + core + frame.raw[L.HELD_AT:L.HELD_AT + L.HELD_BYTES]
            + frame.raw[L.PAD_AT:L.PAD_AT + L.PAD_BYTES])


def decode_pose(packet: bytes, *, expected_incarnation: bytes) -> LivePose:
    """Require the original datagram length, before any SDK receive truncation."""
    expected = _incarnation(expected_incarnation)
    packet = _bounded_bytes(packet, PACKET_BYTES, "pose datagram")
    magic, version, size, rom, config, rom_generation, incarnation, generation, elapsed, continuity = HEADER.unpack_from(packet)
    if (magic, version, size) != (MAGIC, VERSION, PACKET_BYTES):
        raise PoseError("Live pose schema is unsupported.")
    _metadata(incarnation, generation, elapsed, continuity, rom, rom_generation)
    if incarnation != expected:
        raise PoseError("Live pose belongs to a different participant incarnation.")
    _integer(config, "configuration", (1 << 32) - 1)
    core = packet[HEADER.size:HEADER.size + CORE_BYTES]
    if struct.unpack_from("<I", core, 12)[0] & ~VALID_FIELDS:
        raise PoseError("Live pose declares unsupported optional fields.")
    record = bytearray(L.RECORD_BYTES)
    struct.pack_into("<4I", record, 0, L.RECORD_BYTES, L.CLIP_VERSION, config, 0)
    record[L.RECORD_PREFIX_BYTES:L.SHADOW_AT] = core
    at = HEADER.size + CORE_BYTES
    record[L.HELD_AT:L.HELD_AT + L.HELD_BYTES] = packet[at:at + L.HELD_BYTES]
    record[L.PAD_AT:L.PAD_AT + L.PAD_BYTES] = packet[at + L.HELD_BYTES:]
    try:
        frame = frame_from_bytes(bytes(record))
    except ClipError as error:
        raise PoseError("Live pose contains invalid native evidence.") from error
    return LivePose(incarnation, generation, elapsed, continuity, rom, rom_generation, frame)
