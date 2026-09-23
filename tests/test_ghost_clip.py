import hashlib
import struct

import pytest

from ghost_native_fixture import frame, producer
from sm64_events.ghost.clip import (
    STAR_CLOCK, SEGMENT_CLOCK, ClipError, OlderPoseFormat, decode_clip, encode_clip,
    frame_from_bytes, read_clip, write_clip,
)


def encoded(rows, kind=STAR_CLOCK):
    return encode_clip(rows, producer(), clock_kind=kind)


def matrix_frame():
    raw = bytearray(frame())
    struct.pack_into("<I", raw, 128, 2)
    struct.pack_into("<16f", raw, 156, .25, 0, 0, 0, 0, .2, .15, 0,
                     0, -.15, .2, 0, 100, 200, -300, 1)
    return bytes(raw)


def test_effective_root_preserves_matrix_and_requires_its_recorded_camera():
    raw = matrix_frame()
    assert decode_clip(encoded((raw,))).frames[0].raw == raw
    for offset, value in ((128, 3), (168, 1), (216, 0), (156, 0x7fc00000)):
        invalid = bytearray(raw)
        struct.pack_into("<I", invalid, offset, value)
        with pytest.raises(ClipError):
            frame_from_bytes(invalid)
    invalid = bytearray(frame(valid=27))
    struct.pack_into("<I", invalid, 128, 2)
    invalid[156:220] = raw[156:220]
    with pytest.raises(ClipError, match="camera"):
        frame_from_bytes(invalid)
    invalid = bytearray(frame())
    invalid[156:220] = raw[156:220]
    with pytest.raises(ClipError, match="TRS"):
        frame_from_bytes(invalid)


@pytest.mark.parametrize("magic", [b"SM64GHO1", b"SM64GHO2", b"SM64GHO3"])
def test_old_pose_format_has_typed_rerecord_reason_and_preserves_file(tmp_path, magic):
    path = tmp_path / "old.ghost"
    original = magic + bytes(400)
    path.write_bytes(original)
    with pytest.raises(OlderPoseFormat, match="Record a new attempt"):
        read_clip(path)
    assert path.read_bytes() == original


def test_old_archive_row_has_typed_reason_only_with_its_explicit_schema():
    raw = bytearray(260)
    struct.pack_into("<2I", raw, 0, 260, 2)
    struct.pack_into("<2I", raw, 16, 244, 3)
    with pytest.raises(OlderPoseFormat, match="Record a new attempt"):
        frame_from_bytes(raw)
    raw[20] = 0
    with pytest.raises(ClipError) as error:
        frame_from_bytes(raw)
    assert not isinstance(error.value, OlderPoseFormat)
    # GC4/GCLIP3 rows (no shadow or sound) are older too.
    raw = bytearray(324)
    struct.pack_into("<2I", raw, 0, 324, 3)
    struct.pack_into("<2I", raw, 16, 308, 4)
    with pytest.raises(OlderPoseFormat, match="Record a new attempt"):
        frame_from_bytes(raw)


def test_shadow_and_sound_round_trip_with_their_own_validity():
    rows = (frame(1, shadow=True, sounds=(0x24228080, 0x30008081)), frame(2, sounds=()),
            frame(3, valid=29), frame(4, sounds=tuple(range(1, 9)), overflow=True))
    clip = decode_clip(encoded(rows))
    assert tuple(item.raw for item in clip.frames) == rows
    first, second, refused, flooded = clip.frames
    assert first.has_shadow and first.playable and first.sounds == (0x24228080, 0x30008081)
    assert not second.has_shadow and second.has_sound and second.sounds == ()
    assert not refused.has_sound and refused.sounds == ()
    assert flooded.sound_overflow and len(flooded.sounds) == 8


@pytest.mark.parametrize("mutate", [
    lambda raw: struct.pack_into("<I", raw, 128, 1),               # shadow without executed root
    lambda raw: struct.pack_into("<f", raw, 324 + 60, 0.5),        # non-affine shadow matrix
    lambda raw: struct.pack_into("<f", raw, 324, float("nan")),    # nonfinite shadow matrix
    lambda raw: raw.__setitem__(324 + 163, 1),                     # reserved shadow byte
])
def test_invalid_recorded_shadow_refuses(mutate):
    raw = bytearray(frame(shadow=True))
    mutate(raw)
    with pytest.raises(ClipError):
        frame_from_bytes(bytes(raw))


@pytest.mark.parametrize("mutate", [
    lambda raw: raw.__setitem__(616, 9),                           # more than eight events
    lambda raw: raw.__setitem__(617, 1),                           # overflow before eight
    lambda raw: raw.__setitem__(617, 2),                           # unknown flag
    lambda raw: struct.pack_into("<I", raw, 620 + 4 * 3, 5),       # bits past the count
])
def test_invalid_sound_events_refuse(mutate):
    raw = bytearray(frame(sounds=(1, 2)))
    mutate(raw)
    with pytest.raises(ClipError, match="sound"):
        frame_from_bytes(bytes(raw))


@pytest.mark.parametrize("at, name", [(324 + 4, "shadow"), (620, "sound")])
def test_refused_optional_evidence_keeps_no_bytes(at, name):
    raw = bytearray(frame())
    raw[at] = 1
    with pytest.raises(ClipError, match=name):
        frame_from_bytes(bytes(raw))


def test_portable_offsets_and_lossless_refusal_rows(tmp_path):
    rows = (frame(1), frame(2, valid=29), frame(4))
    path = tmp_path / "attempt.ghost"
    manifest = write_clip(path, rows, producer(), clock_kind=STAR_CLOCK,
                          clip_id="112233445566778899aabbccddeeff00")
    raw = path.read_bytes()
    assert raw[:8] == b"SM64GHO4"
    assert struct.unpack_from("<8I", raw, 8) == (4, 160, 652, 3, 1, 5, 4, 5)
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
                    (104, 25), (112, 3), (116, 102), (120, 2), (124, 2),
                    (128, 2), (136, 2), (144, 10), (152, 1), (156, 1)]


@pytest.mark.parametrize("offset,value", BAD_HEADER_WORDS)
def test_corrupted_header_is_not_accepted(offset, value):
    data = bytearray(encoded((frame(),)))
    struct.pack_into("<I", data, offset, value)
    with pytest.raises(ClipError):
        decode_clip(data)


def test_a_dance_tail_is_one_known_header_flag_on_either_clock():
    for kind in (STAR_CLOCK, SEGMENT_CLOCK):  # the 100-coin star runs on its exit segment's clock
        assert decode_clip(encode_clip((frame(),), producer(), clock_kind=kind, dance_tail=True)).dance_tail
    assert not decode_clip(encoded((frame(),))).dance_tail
    data = bytearray(encoded((frame(),)))
    struct.pack_into("<I", data, 124, 3)
    with pytest.raises(ClipError):
        decode_clip(data)


BAD_CLOCK_WORDS = [(260, 13), (264, 1024), (268, 7), (272, 2),
                   (276, 1), (284, 1), (300, 0), (316, 0)]


@pytest.mark.parametrize("offset,value", BAD_CLOCK_WORDS)
def test_clock_schema_cannot_claim_or_retain_inconsistent_evidence(offset, value):
    raw = bytearray(frame())
    struct.pack_into("<I", raw, offset, value)
    with pytest.raises(ClipError):
        frame_from_bytes(raw)


def test_refused_pose_keeps_only_its_named_reason():
    refused = frame_from_bytes(frame(valid=29, pose_refusal=19))
    assert not refused.playable and refused.refusal_name == "not_drawn" and not refused.carried
    carried = frame_from_bytes(frame(pose_refusal=19))
    assert carried.playable and carried.carried
    assert frame_from_bytes(frame()).refusal == 0 and not frame_from_bytes(frame()).carried
    with pytest.raises(ClipError, match="refusal is unknown"):
        frame_from_bytes(frame(valid=29, pose_refusal=24))
    raw = bytearray(frame(valid=29, pose_refusal=3))
    raw[76] = 1
    with pytest.raises(ClipError, match="retains"):
        frame_from_bytes(bytes(raw))


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
    raw[256] = 1
    with pytest.raises(ClipError, match="warp"):
        frame_from_bytes(raw)


def test_progress_must_match_raw_and_have_no_refusal():
    assert frame_from_bytes(frame(clock_valid=7, banked=30)).clock.progress_frames == 31
    with pytest.raises(ClipError, match="progress"):
        frame_from_bytes(frame(clock_valid=7, banked=30, refusal=1))
