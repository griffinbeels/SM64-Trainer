"""Bounded versioned framing. Metadata is strict JSON; payload is a compressed
native picture -- H264 or AV1, whichever the recording GPU encodes."""

import json
import struct

HEADER = struct.Struct("<4sHHII")
MAGIC = b"GPE1"
VERSION = 1
REQUEST = 1
REPLY = 2


def uint(value, name, positive=False):
    if type(value) is not int or not int(positive) <= value < (1 << 64):
        raise ValueError(name + " is not uint64")
    return value


def exact(mapping, keys):
    if type(mapping) is not dict or set(mapping) != set(keys):
        raise ValueError("unexpected metadata fields")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def metadata_bytes(value, limit):
    data = json.dumps(
        value, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")
    if not 2 <= len(data) <= limit:
        raise ValueError("metadata bound exceeded")
    return data


def canonical_command(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def frame(kind, metadata, payload, *, max_json, max_packet):
    data = metadata_bytes(metadata, max_json)
    if (
        type(payload) is not bytes
        or len(payload) > max_packet
        or (kind == REQUEST and payload)
    ):
        raise ValueError("payload bound/type")
    return HEADER.pack(MAGIC, VERSION, kind, len(data), len(payload)) + data + payload


def _read_exact(stream, size):
    data = bytearray()
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            raise EOFError("truncated helper frame")
        data.extend(chunk)
    return bytes(data)


def read_frame(stream, kind, *, max_json, max_packet):
    magic, version, got, json_bytes, payload_bytes = HEADER.unpack(
        _read_exact(stream, HEADER.size)
    )
    if (
        (magic, version, got) != (MAGIC, VERSION, kind)
        or not 2 <= json_bytes <= max_json
        or payload_bytes > max_packet
        or (kind == REQUEST and payload_bytes)
    ):
        raise ValueError("invalid helper frame header")
    raw = _read_exact(stream, json_bytes)
    metadata = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_pairs,
        parse_constant=lambda x: (_ for _ in ()).throw(ValueError("nonfinite JSON")),
    )
    if type(metadata) is not dict:
        raise ValueError("metadata object required")
    return metadata, _read_exact(stream, payload_bytes)


def write_all(stream, data):
    offset = 0
    while offset < len(data):
        count = stream.write(data[offset:])
        if count is None or count <= 0:
            raise OSError("helper pipe write failed")
        offset += count
    stream.flush()
