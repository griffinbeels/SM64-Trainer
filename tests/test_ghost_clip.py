import hashlib
import struct

import pytest

from ghost_native_fixture import frame, producer
from sm64_events.ghost.clip import (
    STAR_CLOCK, SEGMENT_CLOCK, ClipError, decode_clip, encode_clip,
    frame_from_bytes, read_clip, write_clip,
)


def encoded(rows, kind=STAR_CLOCK):
    return encode_clip(rows, producer(), clock_kind=kind)


def test_portable_offsets_and_lossless_refusal_rows(tmp_path):
    rows = (frame(1), frame(2, valid=29), frame(4))
    path = tmp_path / "attempt.ghost"
    manifest = write_clip(path, rows, producer(), clock_kind=STAR_CLOCK,
                          clip_id="112233445566778899aabbccddeeff00")
    raw = path.read_bytes()
    assert raw[:8] == b"SM64GHO2"
    assert struct.unpack_from("<8I", raw, 8) == (2, 160, 260, 3, 1, 3, 4, 5)
    assert raw[40:56].hex() == manifest["clip_id"]
    assert struct.unpack_from("<Qqd", raw, 56) == (30, 1000, 1800000000.0)
    assert struct.unpack_from("<2Q2I2i", raw, 80) == (1, 4, 101, 104, 24, 1)
    assert struct.unpack_from("<4I3Q2I", raw, 112) == (1, 101, 1, 0, 1, 1, 9, 0, 0)
    assert raw[160:] == b"".join(rows)
    assert manifest["invalid_records"] == 1 and manifest["missing_occurrences"] == 1
    assert manifest["origin_elapsed"] == 1 and manifest["origin_generation"] == 1
    assert manifest["sha256"] == hashlib.sha256(raw).hexdigest()
    assert tuple(f.raw for f in read_clip(path, manifest["sha256"]).frames) == rows
    with pytest.raises(ClipError, match="hash"):
        read_clip(path, "0" * 64)


def test_first_exact_coordinate_keeps_refused_pose_and_per_row_scene():
    rows = (frame(1), frame(2, igt=2, elapsed=2, valid=29), frame(3, igt=2, elapsed=2),
            frame(4, igt=0, elapsed=3, area=2), frame(5, igt=1, elapsed=4, level=22))
    clip = decode_clip(encoded(rows))
    assert len(clip.frames) == 5
    assert clip.canonical_frames()[2].sequence == 2
    assert not clip.canonical_frames()[2].playable
    assert clip.canonical_frames()[3].area == 2
    assert clip.canonical_frames()[4].level == 22


@pytest.mark.parametrize("world_status", range(12))
def test_recorded_world_refusal_preserves_usable_pose_and_diagnostic_bytes(world_status):
    rows = (frame(1, world=world_status), frame(2, world=world_status, valid=29),
            frame(3, world=world_status, igt=2, elapsed=2))
    clip = decode_clip(encoded(rows))
    assert tuple(item.raw for item in clip.frames) == rows
    assert all(item.world_status == world_status for item in clip.frames)
    assert clip.frames[0].playable
    assert clip.canonical_frames()[2].sequence == 2
    assert not clip.canonical_frames()[2].playable


def test_segment_origin_is_zero_and_raw_restarts_do_not_rebase_it():
    rows = (frame(1, igt=90, elapsed=100), frame(2, igt=0, elapsed=100, area=2),
            frame(4, igt=4, elapsed=104, area=2))
    clip = decode_clip(encoded(rows, SEGMENT_CLOCK))
    assert list(clip.canonical_frames()) == [0, 4]
    assert clip.canonical_frames()[0].sequence == 1
    assert clip.origin_raw == 90


@pytest.mark.parametrize("generation", [0, 4])
def test_star_closing_reset_is_retained_but_new_generation_is_never_indexed(generation):
    rows = (frame(1, igt=98, elapsed=500, origin=generation),
            frame(2, igt=99, elapsed=501, origin=generation),
            frame(3, igt=0, elapsed=501, origin=generation+1),
            frame(4, igt=1, elapsed=502, origin=generation+1))
    clip = decode_clip(encoded(rows))
    assert len(clip.frames) == 4 and list(clip.canonical_frames()) == [98, 99]
    assert decode_clip(encoded(rows, SEGMENT_CLOCK)).canonical_frames()[2].sequence == 4


INVALID_ROWS = [
    ((frame(1), frame(2, epoch=6)), "epoch"),
    ((frame(2), frame(1)), "ordered"),
    ((frame(1), frame(2, qpc=999)), "ordered"),
    ((frame(1), frame(2, timer=99)), "backward"),
    ((frame(1, elapsed=8), frame(2, elapsed=1)), "backward"),
    ((frame(1), frame(2, continuity=10)), "continuity"),
    ((frame(1, valid=29),), "no supported"),
    ((frame(1, world=11, valid=29),), "no supported"),
    ((frame(1), frame(2, valid=0)), "elapsed clock"),
    ((frame(1), frame(2, clock_valid=1)), "elapsed clock"),
    ((frame(1, elapsed=0), frame(2, elapsed=2**64-1)), "coordinate"),
]


@pytest.mark.parametrize("rows,reason", INVALID_ROWS)
def test_unplayable_or_ambiguous_file_refused(rows, reason):
    with pytest.raises(ClipError, match=reason):
        encoded(rows)


BAD_HEADER_WORDS = [(8, 1), (12, 128), (16, 192), (20, 240001), (28, 2),
                    (104, 25), (112, 3), (116, 102), (120, 2), (124, 1),
                    (128, 2), (136, 2), (144, 10), (152, 1), (156, 1)]


@pytest.mark.parametrize("offset,value", BAD_HEADER_WORDS)
def test_corrupted_header_is_not_accepted(offset, value):
    data = bytearray(encoded((frame(),)))
    struct.pack_into("<I", data, offset, value)
    with pytest.raises(ClipError):
        decode_clip(data)


BAD_CLOCK_WORDS = [(196, 13), (200, 1024), (204, 7), (208, 2),
                   (212, 1), (220, 1), (236, 0), (252, 0)]


@pytest.mark.parametrize("offset,value", BAD_CLOCK_WORDS)
def test_clock_schema_cannot_claim_or_retain_inconsistent_evidence(offset, value):
    raw = bytearray(frame())
    struct.pack_into("<I", raw, offset, value)
    with pytest.raises(ClipError):
        frame_from_bytes(raw)


def test_truncation_trailing_data_nonfinite_and_invalid_components():
    data = encoded((frame(),))
    for broken in (data[:100], data[:-1], data + b"\0"):
        with pytest.raises(ClipError):
            decode_clip(broken)
    raw = bytearray(frame())
    struct.pack_into("<f", raw, 76, float("nan"))
    with pytest.raises(ClipError, match="nonfinite"):
        frame_from_bytes(raw)
    raw = bytearray(frame(valid=29))
    raw[76] = 1
    with pytest.raises(ClipError, match="retains"):
        frame_from_bytes(raw)
    raw = bytearray(frame(valid=15))
    raw[192] = 1
    with pytest.raises(ClipError, match="warp"):
        frame_from_bytes(raw)


def test_progress_must_match_raw_and_have_no_refusal():
    assert frame_from_bytes(frame(clock_valid=7, banked=30)).clock.progress_frames == 31
    with pytest.raises(ClipError, match="progress"):
        frame_from_bytes(frame(clock_valid=7, banked=30, refusal=1))
