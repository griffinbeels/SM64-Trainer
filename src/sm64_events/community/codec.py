"""GHC1: lossless shuffled native records with bounded single-stream blocks."""
import hashlib
import math
import struct
import zlib

from sm64_events.ghost.clip import decode_clip
from sm64_events.ghost.native_layout import (
    CAMERA_AT, CAMERA_FOV_AT, HEADER_BYTES, MAX_RECORDS, POSE_AT,
    ROOT_KIND_AT, ROOT_MATRIX_AT, VALID_CAMERA, VALID_POSE,
)

from .claims import REGIONS

MAX_RAW = 16 << 20
MAX_ENCODED = 2 << 20
BLOCK_RECORDS = 256
ENVELOPE = struct.Struct("<4s4I")
BLOCK = struct.Struct("<2I")
FORMATS = {b"SM64GHO4": (4, 652, 636, 5), b"SM64GHO5": (5, 1268, 1252, 6),
           b"SM64GHO6": (6, 1276, 1260, 7), b"SM64GHO7": (7, 1284, 1268, 8)}


def _header(raw, *, max_raw=MAX_RAW):
    if not isinstance(raw, bytes) or not HEADER_BYTES <= len(raw) <= max_raw:
        raise ValueError("Native clip exceeds the community byte limit.")
    fmt = FORMATS.get(raw[:8])
    version, header, record, count, rom, sample = struct.unpack_from("<6I", raw, 8)
    if (fmt is None or (version, header, record, sample) != (fmt[0], HEADER_BYTES, fmt[1], fmt[3])
            or rom not in REGIONS or not 1 <= count <= MAX_RECORDS or len(raw) != header + record * count):
        raise ValueError("Unsupported native community clip header.")
    return header, record, count


def validate_native(raw, *, max_raw=MAX_RAW):
    """Use the native reader plus broad safe numeric bounds for public bytes."""
    result = _header(raw, max_raw=max_raw)
    clip = decode_clip(raw)
    for frame in clip.frames:
        for flag, offset in ((VALID_POSE, POSE_AT), (VALID_CAMERA, CAMERA_AT)):
            if frame.valid_fields & flag:
                values = struct.unpack_from("<6f", frame.raw, offset)
                if any(not math.isfinite(v) or abs(v) > 1e7 for v in values):
                    raise ValueError("Native component exceeds community bounds.")
        if frame.valid_fields & VALID_CAMERA and not 0 < struct.unpack_from("<f", frame.raw, CAMERA_FOV_AT)[0] < 180:
            raise ValueError("Native camera field of view is invalid.")
        if frame.valid_fields & VALID_POSE and struct.unpack_from("<I", frame.raw, ROOT_KIND_AT)[0] == 2:
            if any(abs(v) > 1e7 for v in struct.unpack_from("<16f", frame.raw, ROOT_MATRIX_AT)):
                raise ValueError("Native root matrix exceeds community bounds.")
    return result


def encode(raw, *, max_raw=MAX_RAW, max_encoded=MAX_ENCODED):
    header, record, count = validate_native(raw, max_raw=max_raw)
    output = bytearray(ENVELOPE.pack(b"GHC1", len(raw), header, record, count))
    output.extend(raw[:header])
    for start in range(0, count, BLOCK_RECORDS):
        records = min(BLOCK_RECORDS, count - start)
        block = raw[header + start * record:header + (start + records) * record]
        shuffled = b"".join(block[offset::record] for offset in range(record))
        compressed = zlib.compress(shuffled, 6)
        if len(output) + BLOCK.size + len(compressed) > max_encoded:
            raise ValueError("Compressed ghost exceeds the community byte limit.")
        output.extend(BLOCK.pack(records, len(compressed)))
        output.extend(compressed)
    return bytes(output)


def decode(encoded, *, raw_sha256=None, max_raw=MAX_RAW, max_encoded=MAX_ENCODED):
    if not isinstance(encoded, bytes) or not ENVELOPE.size + HEADER_BYTES < len(encoded) <= max_encoded:
        raise ValueError("Invalid compressed ghost size.")
    magic, raw_size, header, record, count = ENVELOPE.unpack_from(encoded)
    if (magic != b"GHC1" or header != HEADER_BYTES or record not in {fmt[1] for fmt in FORMATS.values()}
            or not 1 <= count <= MAX_RECORDS or raw_size != header + record * count or raw_size > max_raw):
        raise ValueError("Invalid compressed ghost envelope.")
    output = bytearray(encoded[ENVELOPE.size:ENVELOPE.size + header])
    position, restored = ENVELOPE.size + header, 0
    while restored < count:
        if position + BLOCK.size > len(encoded):
            raise ValueError("Compressed ghost block is truncated.")
        records, size = BLOCK.unpack_from(encoded, position)
        position += BLOCK.size
        if records != min(BLOCK_RECORDS, count - restored) or not 1 <= size <= max_encoded or position + size > len(encoded):
            raise ValueError("Invalid compressed ghost block size.")
        payload = encoded[position:position + size]
        position += size
        inflater = zlib.decompressobj()
        expected = records * record
        try:
            shuffled = inflater.decompress(payload, expected + 1)
        except zlib.error:
            raise ValueError("Invalid compressed ghost stream.") from None
        if len(shuffled) != expected or not inflater.eof or inflater.unused_data or inflater.unconsumed_tail:
            raise ValueError("Compressed ghost stream exceeds or disagrees with its block.")
        block = bytearray(expected)
        for offset in range(record):
            block[offset::record] = shuffled[offset * records:(offset + 1) * records]
        output.extend(block)
        restored += records
    if position != len(encoded):
        raise ValueError("Compressed ghost has trailing data.")
    raw = bytes(output)
    if raw_sha256 is not None and hashlib.sha256(raw).hexdigest() != raw_sha256:
        raise ValueError("Native ghost content hash does not match.")
    if validate_native(raw, max_raw=max_raw) != (header, record, count):
        raise ValueError("Native header disagrees with the transport envelope.")
    return raw
