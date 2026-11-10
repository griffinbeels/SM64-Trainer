"""Native live decoder and real pose/scene conversion; no GPU or live services.

The native fixture compiles only when these tests run. The root integration
owner serializes that MSVC lease. The source/golden test is pure Python.
"""
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys

import pytest

from ghost_native_fixture import frame
from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.ghost.clip import decode_clip, frame_from_bytes
from sm64_events.multiplayer.protocol import decode_pose, encode_pose

ROOT = Path(__file__).resolve().parents[1]
INCARNATION = bytes(range(16))
META = dict(participant_incarnation=INCARNATION, source_generation=11,
            elapsed_ns=33_333_333, continuity=7, rom_layout=1, rom_generation=3)
RECORDINGS = (
    "attempt_2549_whomps-fortress_shoot-into-the-wild-blue_0m10s66.ghost",
    "attempt_4015_bob-omb-battlefield_behind-chain-chomps-gate_0m13s63.ghost",
)


def recorded_inputs():
    """Actual captured QPC/frequency provides elapsed; old pads remain unknown."""
    packets, expected = [], []
    for index, name in enumerate(RECORDINGS):
        clip = decode_clip((ROOT / "tests/fixtures" / name).read_bytes())
        origin = clip.frames[0].qpc
        for original in clip.frames:
            packet = encode_pose(original.raw, **{**META, "source_generation": 11 + index,
                "continuity": 7 + index, "rom_layout": clip.rom_layout,
                "elapsed_ns": (original.qpc - origin) * 1_000_000_000 // clip.qpc_frequency})
            sample = bytearray(original.raw[16:])
            # Independent fixed-layout witness: retain core308 and held+pad52;
            # optional cosmetic blocks occupy sample308..1215 and are absent.
            struct.pack_into("<I", sample, 12, original.valid_fields & (31 | 256 | 512))
            sample[308:1216] = bytes(908)
            packets.append(packet)
            expected.append(packet[:64] + sample)
    return packets, expected


def test_source_wire_goldens_preserve_actual_fixture_evidence():
    packets, expected = recorded_inputs()
    assert len(packets) == 830
    assert all(len(packet) == 424 for packet in packets)
    assert all(packet[202:204] == bytes(2) for packet in packets)  # gc_pose alignment padding
    frames = [decode_pose(packet, expected_incarnation=INCARNATION).frame for packet in packets]
    assert sum(bool(original.held_behaviour) for original in frames) == 85
    assert all(original.pad is None for original in frames)
    assert all(len(value) == 64 + 1268 for value in expected)
    raw = frame()
    independent = (struct.pack("<4s5I16s3Q", b"SMLV", 0, 424, 1, 4, 3, INCARNATION,
                               11, 33_333_333, 7) + raw[16:324] + raw[1232:1284])
    assert encode_pose(raw, **META) == independent


@pytest.fixture(scope="module")
def native(tmp_path_factory):
    if sys.platform != "win32":
        pytest.skip("MSVC native live codec witness")
    work = tmp_path_factory.mktemp("multiplayer_native")
    spec = importlib.util.spec_from_file_location("multiplayer_build", ROOT / "tools/build_plugin.py")
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    vcvars = compiler.find_vcvars32()
    assert vcvars, "x86 MSVC is required for the native live decoder witness"
    target = work / "ghost_live_codec_host.exe"
    compiler._cl(vcvars, ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17", "/Gy",
        f"/I{ROOT / 'plugin/gfxwrap'}", f"/I{ROOT / 'renderer/model'}",
        str(ROOT / "plugin/gfxwrap/ghost_live_codec.cpp"), str(ROOT / "plugin/gfxwrap/ghost_pose.cpp"),
        str(ROOT / "tests/native/ghost_live_codec_host.cpp"),
        f"/Fe:{target}", f"/Fo{work}\\", "/link", "/OPT:REF",
        "/MANIFEST:EMBED", "/MANIFESTUAC:level='asInvoker'", *compiler.LIBS], work)
    return target


def run_native(native, *args):
    result = subprocess.run([str(native), *map(str, args)], cwd=native.parent, capture_output=True,
                            text=True, timeout=20, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_native_checks_original_length_before_any_copy(native):
    assert run_native(native, "arguments")["arguments"] is True


def test_native_matches_all_authentic_wire_values(native, tmp_path):
    packets, expected = recorded_inputs()
    source, output = tmp_path / "captured-packets.bin", tmp_path / "checked-values.bin"
    source.write_bytes(b"".join(packets))
    report = run_native(native, "batch", source, output)
    assert report == {"decoded": 830, "known_pad": 0, "held": 85, "value_bytes": 1332}
    assert output.read_bytes() == b"".join(expected)


@pytest.mark.parametrize("pad", [None, (0, 0, 0), (-128, 127, 0x2001)])
def test_native_preserves_unknown_neutral_and_known_pad(native, tmp_path, pad):
    packet = encode_pose(frame(pad=pad), **META)
    source, output = tmp_path / "packet.bin", tmp_path / "value.bin"
    source.write_bytes(packet)
    assert run_native(native, "decode", source, output)["result"] == "ok"
    value = output.read_bytes()
    assert value == packet[:64] + decode_pose(packet, expected_incarnation=INCARNATION).frame.raw[16:]


@pytest.mark.parametrize("data", [b"", bytes(423), bytes(425), bytes(848)])
def test_native_refuses_original_datagram_size_and_clears_prior_value(native, tmp_path, data):
    source = tmp_path / "invalid.bin"
    source.write_bytes(data)
    report = run_native(native, "decode", source)
    assert report["result"] == "bad_size" and report["cleared"] is True and report["model_evaluations"] == 0


@pytest.mark.parametrize(("mutations", "reason"), [
    ([(0, "4s", b"BAD!")], "bad_header"), ([(4, "I", 1)], "bad_header"),
    ([(8, "I", 423)], "bad_header"), ([(12, "I", 0)], "bad_header"),
    ([(12, "I", 3)], "bad_header"), ([(16, "I", 0)], "bad_header"),
    ([(20, "I", 0)], "bad_header"), ([(24, "16s", bytes(16))], "bad_header"),
    ([(24, "16s", b"a" * 16)], "wrong_incarnation"), ([(40, "Q", 0)], "bad_header"),
    ([(56, "Q", 0)], "bad_header"), ([(64, "I", 1267)], "bad_sample"),
    ([(68, "I", 7)], "bad_sample"), ([(72, "I", 0)], "bad_sample"),
    ([(76, "I", 31 | 512 | 32)], "bad_sample"), ([(80, "Q", 0)], "bad_sample"),
    ([(88, "Q", 0)], "bad_sample"), ([(96, "q", -1)], "bad_sample"),
    ([(112, "h", 3)], "bad_sample"), ([(114, "h", 9)], "bad_sample"),
    ([(116, "I", 14)], "bad_sample"), ([(120, "I", 0)], "bad_sample"),
    ([(124, "f", float("nan"))], "bad_pose"), ([(124, "f", 10_000_001)], "bad_pose"),
    ([(136, "f", 0)], "bad_pose"), ([(136, "f", 101)], "bad_pose"),
    ([(154, "h", 209)], "bad_pose"), ([(156, "h", -1)], "bad_pose"),
    ([(160, "H", 2)], "bad_pose"), ([(164, "I", 0)], "bad_pose"),
    ([(176, "I", 3)], "bad_pose"), ([(192, "H", 1024)], "bad_pose"),
    ([(194, "B", 4)], "bad_pose"), ([(195, "B", 9)], "bad_pose"),
    ([(196, "B", 6)], "bad_pose"), ([(197, "B", 2)], "bad_pose"),
    ([(198, "B", 6)], "bad_pose"), ([(199, "B", 4)], "bad_pose"),
    ([(200, "B", 2)], "bad_pose"), ([(201, "B", 24)], "bad_pose"),
    ([(202, "B", 1)], "bad_pose"),
    ([(204, "f", 1)], "bad_pose"), ([(268, "f", float("inf"))], "bad_camera"),
    ([(268, "6f", (0, 0, 0, 0, 0, 0))], "bad_camera"),
    ([(296, "f", 0)], "bad_camera"), ([(296, "f", 180)], "bad_camera"),
    ([(300, "h", 0)], "bad_camera"), ([(302, "h", 100)], "bad_camera"),
    ([(308, "I", 13)], "bad_clock"), ([(312, "I", 1024)], "bad_clock"),
    ([(316, "I", 7)], "bad_clock"), ([(320, "I", 2)], "bad_clock"),
    ([(324, "Q", 1)], "bad_clock"), ([(348, "Q", 0)], "bad_clock"),
    ([(364, "Q", 0)], "bad_clock"),
    ([(308, "I", 7), (312, "I", 0), (324, "Q", 2**64 - 1)], "bad_clock"),
    ([(372, "I", 0x13003174)], "bad_held"),
    ([(416, "h", -129)], "bad_pad"), ([(418, "h", 128)], "bad_pad"),
    ([(420, "H", 0x40)], "bad_pad"), ([(422, "H", 1)], "bad_pad"),
    ([(76, "I", 31), (420, "H", 0x8000)], "bad_pad"),
])
def test_native_refuses_malformed_values_before_model_evaluation(native, tmp_path, mutations, reason):
    packet = bytearray(encode_pose(frame(), **META))
    for offset, format, value in mutations:
        struct.pack_into("<" + format, packet, offset, *(value if isinstance(value, tuple) else (value,)))
    source = tmp_path / "invalid.bin"
    source.write_bytes(packet)
    report = run_native(native, "decode", source)
    assert report["result"] == reason and report["cleared"] is True and report["model_evaluations"] == 0


@pytest.mark.parametrize(("offset", "format", "value"), [
    (372, "I", 0x12003174), (376, "h", 2), (378, "h", -1), (382, "H", 256),
    (384, "f", float("nan")), (396, "f", 0), (396, "f", 101),
    (408, "h", 1), (414, "H", 2),
])
def test_native_bounds_held_identity_animation_and_transform(native, tmp_path, offset, format, value):
    packet = bytearray(encode_pose(frame(held=(0x13003174, 0, 2)), **META))
    struct.pack_into("<" + format, packet, offset, value)
    source = tmp_path / "bad-held.bin"
    source.write_bytes(packet)
    report = run_native(native, "decode", source)
    assert report["result"] == "bad_held" and report["cleared"] is True and report["model_evaluations"] == 0


@pytest.mark.parametrize(("index", "value"), [(0, float("nan")), (3, 0.5), (15, 0), (12, 1_000_000_000)])
def test_native_bounds_an_authentic_modelview_matrix(native, tmp_path, index, value):
    clip = decode_clip((ROOT / "tests/fixtures" / RECORDINGS[0]).read_bytes())
    packet = bytearray(encode_pose(clip.frames[0].raw, **META))
    struct.pack_into("<f", packet, 204 + index * 4, value)
    source = tmp_path / "bad-matrix.bin"
    source.write_bytes(packet)
    report = run_native(native, "decode", source)
    assert report["result"] == "bad_pose" and report["cleared"] is True


def test_native_held_animation_bounds_use_the_source_cartridge(native, tmp_path):
    packet = bytearray(encode_pose(frame(held=(0x13003154, 0, 2)), **{**META, "rom_layout": 2}))
    source = tmp_path / "jp-held.bin"
    source.write_bytes(packet)
    assert run_native(native, "decode", source)["result"] == "ok"
    struct.pack_into("<I", packet, 12, 1)  # the same offset does not name Bob-omb on US
    source.write_bytes(packet)
    assert run_native(native, "decode", source)["result"] == "bad_held"


def test_native_preserves_a_hidden_mario_without_evaluating_an_animation(native, tmp_path):
    packet = bytearray(encode_pose(frame(), **META))
    struct.pack_into("<hh", packet, 154, -1, -1)
    struct.pack_into("<H", packet, 160, 0)
    packet[200] = 1
    source, output = tmp_path / "hidden.bin", tmp_path / "value.bin"
    source.write_bytes(packet)
    report = run_native(native, "decode", source, output)
    assert report["result"] == "ok" and report["model_evaluations"] == 0
    assert output.read_bytes() == packet[:64] + decode_pose(packet, expected_incarnation=INCARNATION).frame.raw[16:]


def test_native_preserves_refused_pose_and_raw_warp_without_reconstruction(native, tmp_path):
    original = frame(valid=29, pose_refusal=7, pad=None)
    raw = bytearray(original)
    struct.pack_into("<Hh", raw, 16 + 240, 65535, -32768)
    packet = encode_pose(bytes(raw), **META)
    source, output = tmp_path / "refused.bin", tmp_path / "value.bin"
    source.write_bytes(packet)
    assert run_native(native, "decode", source, output)["result"] == "ok"
    assert output.read_bytes()[64:] == frame_from_bytes(raw).raw[16:]


@pytest.mark.parametrize("count", [1, 3])
def test_one_and_three_decoded_live_values_use_actual_pose_and_scene_math(native, tmp_path, count):
    clip = decode_clip((ROOT / "tests/fixtures" / RECORDINGS[0]).read_bytes())
    original = clip.frames[0]
    packets = [encode_pose(original.raw, **{**META,
        "participant_incarnation": INCARNATION[:-1] + bytes([15 + seat])}) for seat in range(3)]
    source = tmp_path / "three-admitted-peers.bin"
    source.write_bytes(b"".join(packets))
    report = run_native(native, f"pose{count}", source)
    assert report["peers"] == count and report["evaluations"] == count + 1 + (3 if count == 3 else 0)
    assert report["scene"] is True
