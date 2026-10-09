import hashlib
import struct
import zlib

import pytest

from ghost_native_fixture import frame, producer
from sm64_events.ghost.clip import encode_clip, frame_from_bytes
from sm64_events.community import codec


def native(count=3):
    return encode_clip(tuple(frame_from_bytes(frame(index)) for index in range(1, count + 1)), producer(), clock_kind=1,
                       clip_id="22222222-2222-4222-8222-222222222222")


def older(version):
    raw = native()
    _, record, sample, sample_version = codec.FORMATS[f"SM64GHO{version}".encode()]
    header = bytearray(raw[:160])
    header[:8] = f"SM64GHO{version}".encode()
    struct.pack_into("<3I", header, 8, version, 160, record)
    struct.pack_into("<I", header, 28, sample_version)
    blocks = []
    for index in range(3):
        block = bytearray(raw[160 + index * 1284:160 + index * 1284 + record])
        struct.pack_into("<2I", block, 0, record, version)
        struct.pack_into("<2I", block, 16, sample, sample_version)
        blocks.append(block)
    return bytes(header + b"".join(blocks))


@pytest.mark.parametrize("version", [4, 5, 6, 7])
def test_every_native_format_round_trips_exact_original_bytes(version):
    raw = native() if version == 7 else older(version)
    encoded = codec.encode(raw)
    assert codec.decode(encoded, raw_sha256=hashlib.sha256(raw).hexdigest()) == raw


def test_block_boundary_pad_known_and_unknown_are_lossless():
    raw = encode_clip(tuple(frame_from_bytes(frame(index, pad=(0, 0, 0) if index % 2 else None))
                            for index in range(1, 258)), producer(), clock_kind=1)
    encoded = codec.encode(raw)
    assert codec.decode(encoded) == raw
    assert struct.unpack_from("<I", encoded, 180)[0] == 256


@pytest.mark.parametrize("mutation", ["trailing", "extra_stream", "count", "bomb", "hash", "raw_limit"])
def test_transport_refuses_ambiguous_or_unbounded_content(mutation):
    encoded = codec.encode(native())
    kwargs = {}
    if mutation == "trailing":
        encoded += b"trailing"
    elif mutation == "extra_stream":
        count, size = codec.BLOCK.unpack_from(encoded, 180)
        extra = zlib.compress(b"other")
        encoded = encoded[:180] + codec.BLOCK.pack(count, size + len(extra)) + encoded[188:] + extra
    elif mutation == "count":
        encoded = encoded[:180] + codec.BLOCK.pack(2, len(encoded) - 188) + encoded[188:]
    elif mutation == "bomb":
        bomb = zlib.compress(b"x" * (4 * 1284))
        encoded = encoded[:180] + codec.BLOCK.pack(3, len(bomb)) + bomb
    elif mutation == "hash":
        kwargs["raw_sha256"] = "0" * 64
    else:
        kwargs["max_raw"] = 160
    with pytest.raises(ValueError):
        codec.decode(encoded, **kwargs)


def test_wrong_sample_version_and_infinite_pose_fail():
    from sm64_events.ghost.native_layout import POSE_AT
    for offset, format_, value in [(160 + 20, "<I", 1268), (160 + POSE_AT, "<f", float("inf"))]:
        bad = bytearray(native())
        struct.pack_into(format_, bad, offset, value)
        with pytest.raises(ValueError):
            codec.encode(bytes(bad))
