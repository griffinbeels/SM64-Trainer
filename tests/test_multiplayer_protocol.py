"""The experimental live boundary preserves facts and refuses original bad bytes."""
import struct
from pathlib import Path

import pytest

from ghost_native_fixture import frame
from sm64_events.ghost.clip import decode_clip, frame_from_bytes
from sm64_events.ghost import native_layout as L
from sm64_events.multiplayer.protocol import PoseError, decode_pose, encode_pose

INCARNATION = bytes(range(16))
META = dict(participant_incarnation=INCARNATION, source_generation=11,
            elapsed_ns=33_333_333, continuity=7, rom_layout=1, rom_generation=3)


def decode(packet):
    return decode_pose(packet, expected_incarnation=INCARNATION)


def test_independent_wire_layout_retains_source_facts_and_omits_cosmetics():
    source = frame(17, shadow=True, sounds=[0x14000001],
                   held=(0x13001714, 1, 2), pad=(-70, 23, 0x8000))
    core = bytearray(source[16:324])
    struct.pack_into("<I", core, 12, 31 | 256 | 512)
    expected = (struct.pack("<4s5I16s3Q", b"SMLV", 0, 424, 1, 4, 3,
                            INCARNATION, 11, 33_333_333, 7)
                + core + source[1232:1284])
    packet = encode_pose(source, **META)
    assert packet == expected
    live = decode(packet)
    assert (live.source_generation, live.elapsed_ns, live.continuity, live.rom_layout,
            live.rom_generation) == (11, 33_333_333, 7, 1, 3)
    original = frame_from_bytes(source)
    assert (live.frame.sequence, live.frame.epoch, live.frame.qpc, live.frame.clock,
            live.frame.pad, live.frame.held_behaviour) == (
        original.sequence, original.epoch, original.qpc, original.clock,
        original.pad, original.held_behaviour)
    assert not live.frame.has_shadow and not live.frame.has_sound
    assert live.frame.raw[324:1232] == bytes(908)


def test_actual_recordings_preserve_unknown_inputs_and_held_objects():
    fixtures = Path(__file__).parent / "fixtures"
    recordings = (
        "attempt_2549_whomps-fortress_shoot-into-the-wild-blue_0m10s66.ghost",
        "attempt_4015_bob-omb-battlefield_behind-chain-chomps-gate_0m13s63.ghost",
    )
    held = 0
    for name in recordings:
        clip = decode_clip((fixtures / name).read_bytes())
        for original in clip.frames:
            live = decode(encode_pose(original.raw, **META)).frame
            assert live.raw[16:28] == original.raw[16:28]
            assert live.raw[32:324] == original.raw[32:324]
            assert live.raw[1232:] == original.raw[1232:]
            assert live.pad == original.pad
            assert live.clock == original.clock
            held += bool(live.held_behaviour)
    assert held > 0, "the authentic fixture must exercise a held object"


@pytest.mark.parametrize("pad", [None, (0, 0, 0), (-128, 127, 0x2001)])
def test_optional_pad_is_never_invented(pad):
    assert decode(encode_pose(frame(pad=pad), **META)).frame.pad == pad


def test_original_byte_size_is_checked_for_typed_and_strided_buffers():
    packet = encode_pose(frame(), **META)
    # len=106, nbytes=424: valid typed buffer. len=424, nbytes=848: oversized.
    assert decode(memoryview(packet).cast("I")).frame.sequence == 1
    with pytest.raises(PoseError, match="size"):
        decode(memoryview(packet * 2).cast("H"))
    interleaved = bytearray(848)
    interleaved[::2] = packet
    assert decode(memoryview(interleaved)[::2]).frame.sequence == 1
    source = frame()
    assert encode_pose(memoryview(source).cast("I"), **META) == packet
    with pytest.raises(PoseError, match="size"):
        encode_pose(memoryview(source * 2).cast("H"), **META)
    unreadable = memoryview(packet)
    unreadable.release()
    with pytest.raises(PoseError, match="readable"):
        decode(unreadable)


@pytest.mark.parametrize("packet", [b"", bytes(423), bytes(425), bytes(4240), "x" * 424],
                         ids=["empty", "short", "suffix", "oversized", "text"])
def test_original_wrong_size_is_refused(packet):
    with pytest.raises(PoseError, match="size"):
        decode(packet)


@pytest.mark.parametrize(("offset", "format", "value"), [
    (0, "4s", b"BAD!"), (4, "I", 1), (8, "I", 423), (12, "I", 0),
    (16, "I", 0), (20, "I", 0), (40, "Q", 0), (56, "Q", 0),
    (76, "I", 31 | 32),  # omitted shadow cannot be declared present
    (64 + L.POSE_AT - 16, "f", float("nan")),
    (64 + L.CLOCK_AT - 16 + 12, "I", 12345),  # clock contradicts raw IGT
])
def test_untrusted_header_and_native_evidence_are_validated(offset, format, value):
    packet = bytearray(encode_pose(frame(), **META))
    struct.pack_into("<" + format, packet, offset, value)
    with pytest.raises(PoseError):
        decode(packet)


def test_packet_never_grants_a_new_participant_identity():
    packet = bytearray(encode_pose(frame(), **META))
    packet[24:40] = b"a" * 16
    with pytest.raises(PoseError, match="different participant"):
        decode(packet)
    packet[24:40] = bytes(16)
    with pytest.raises(PoseError, match="identity"):
        decode(packet)


@pytest.mark.parametrize(("key", "value"), [
    ("source_generation", True), ("source_generation", 2**64), ("elapsed_ns", -1),
    ("continuity", 0), ("rom_layout", True), ("rom_generation", 2**32),
])
def test_local_metadata_cannot_wrap_or_confuse_types(key, value):
    with pytest.raises(PoseError):
        encode_pose(frame(), **{**META, key: value})
