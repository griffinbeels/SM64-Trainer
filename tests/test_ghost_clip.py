import hashlib
import struct

import pytest

from ghost_native_fixture import frame, producer
from sm64_events.ghost.clip import ClipError, decode_clip, encode_clip, frame_from_bytes, read_clip, write_clip


def test_portable_offsets_and_lossless_refusal_rows(tmp_path):
    rows = (frame(1), frame(2, valid=13), frame(4))
    path = tmp_path / "attempt.ghost"
    manifest = write_clip(path, rows, producer(), clip_id="112233445566778899aabbccddeeff00")
    raw = path.read_bytes()
    assert raw[:8] == b"SM64GHO1"
    assert struct.unpack_from("<8I", raw, 8) == (1, 128, 192, 3, 1, 2, 4, 5)
    assert raw[40:56].hex() == manifest["clip_id"]
    assert struct.unpack_from("<Qqd", raw, 56) == (30, 1000, 1800000000.0)
    assert struct.unpack_from("<2Q2I2i", raw, 80) == (1, 4, 101, 104, 24, 1)
    assert raw[128:] == b"".join(rows)
    assert manifest["invalid_records"] == 1 and manifest["missing_occurrences"] == 1
    assert manifest["sha256"] == hashlib.sha256(raw).hexdigest()
    assert tuple(f.raw for f in read_clip(path, manifest["sha256"]).frames) == rows
    with pytest.raises(ClipError, match="hash"):
        read_clip(path, "0" * 64)


def test_first_repeated_igt_remains_refused_and_clock_invalid_rows_survive():
    rows = (frame(1), frame(2, igt=2, valid=13), frame(3, igt=2),
            frame(4, igt=0, timer=0, valid=0), frame(5, igt=3))
    clip = decode_clip(encode_clip(rows, producer()))
    assert len(clip.frames) == 5
    assert clip.canonical_frames()[2].sequence == 2
    assert not clip.canonical_frames()[2].playable
    assert 0 not in clip.canonical_frames()


@pytest.mark.parametrize("rows,reason", [
    ((frame(1), frame(2, epoch=6)), "epoch"),
    ((frame(2), frame(1)), "ordered"),
    ((frame(1), frame(2, qpc=999)), "ordered"),
    ((frame(1), frame(2, timer=99)), "clock restarts"),
    ((frame(1, igt=8), frame(2, igt=1)), "clock restarts"),
    ((frame(1), frame(2, area=2)), "level or area"),
    ((frame(1, valid=13),), "no supported"),
    ((frame(1, valid=0), frame(2)), "endpoints"),
])
def test_unplayable_or_ambiguous_file_refused(rows, reason):
    with pytest.raises(ClipError, match=reason):
        encode_clip(rows, producer())


@pytest.mark.parametrize("offset,value", [(8, 2), (12, 124), (16, 176), (20, 262145),
                                        (28, 1), (112, 1), (104, 25)])
def test_corrupted_header_is_not_accepted(offset, value):
    data = bytearray(encode_clip((frame(),), producer()))
    struct.pack_into("<I", data, offset, value)
    with pytest.raises(ClipError):
        decode_clip(data)


def test_truncation_trailing_data_and_nonfinite_pose():
    data = encode_clip((frame(),), producer())
    for broken in (data[:100], data[:-1], data + b"\0"):
        with pytest.raises(ClipError):
            decode_clip(broken)
    raw = bytearray(frame())
    struct.pack_into("<f", raw, 76, float("nan"))
    with pytest.raises(ClipError, match="nonfinite"):
        frame_from_bytes(raw)
    raw = bytearray(frame(valid=13))
    raw[76] = 1
    with pytest.raises(ClipError, match="retains"):
        frame_from_bytes(raw)
