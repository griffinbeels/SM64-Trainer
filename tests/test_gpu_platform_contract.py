"""Native dispatch must refuse mixed/unknown policies before touching a GPU."""
import importlib.util
from pathlib import Path
import subprocess
import io
import re

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]


def test_native_platform_contract(tmp_path):
    spec = importlib.util.spec_from_file_location("platform_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    source = ROOT / "plugin/gfxwrap"
    exe = tmp_path / "platform_fault.exe"
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    build._cl(build.find_vcvars64(), flags + ["/std:c++17", "/EHsc", "/WX",
        f"/I{source / 'vendor'}", str(source / "gpu_platform_fault_host.cpp"),
        str(source / "gpu_platform_encoder.cpp"), str(source / "gpu_mf_encoder.cpp"),
        str(source / "gpu_bridge_encoder.cpp"), f"/Fe:{exe}", f"/Fo{tmp_path}\\",
        "/link", *build.ENCODER_LIBS], tmp_path)
    result = subprocess.run([str(exe)], cwd=tmp_path, capture_output=True, text=True,
        timeout=10, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    assert "refuse before device access" in result.stdout
    # Inspect the produced loader contract, not the linker command string:
    # Windows N must still load the NVIDIA helper without Media Foundation.
    import pefile
    helper = build.build_encoder_helper(tmp_path)
    with pefile.PE(str(helper)) as image:
        ordinary = {entry.dll.lower() for entry in image.DIRECTORY_ENTRY_IMPORT}
        delayed = {entry.dll.lower() for entry in image.DIRECTORY_ENTRY_DELAY_IMPORT}
    assert b"mfplat.dll" not in ordinary
    assert b"mfplat.dll" in delayed


@pytest.fixture(scope="module")
def mf_output_host(tmp_path_factory):
    """CPU transform drives the production submit/identity/input-release path."""
    out = tmp_path_factory.mktemp("mf-output-contract")
    spec = importlib.util.spec_from_file_location("mf_output_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    source = ROOT / "plugin/gfxwrap"
    target = out / "mf-output.exe"
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    build._cl(build.find_vcvars64(), flags + ["/std:c++17", "/EHsc", "/WX",
        "/DGPU_MF_TEST_HOST", str(source / "gpu_mf_output_host.cpp"),
        str(source / "gpu_mf_encoder.cpp"), f"/Fe:{target}", f"/Fo{out}\\"], out)
    return target


def assert_mux_preserves_picture(codec, pictures, source_packet):
    """Both native boundaries must survive the real shared mux and decoder."""
    import av
    import numpy as np
    from sm64_events.replay.media import MediaRun
    from sm64_events.replay.packetmux import EncodedPicture, NativeFormat, PacketFragmentMux

    output = io.BytesIO()
    mux = PacketFragmentMux(output, NativeFormat(codec, 320, 240, 30), MediaRun("mf-output-contract", 1000.0),
        audio_rate=48000, audio_bitrate=160000, packet_limit=1 << 20, pcm_limit=384000)
    try:
        for picture in pictures:
            mux.write_video(EncodedPicture(*picture))
        mux.close()
    finally:
        if not mux.closed:
            mux.abort()
    with av.open(io.BytesIO(output.getvalue())) as media:
        frames = list(media.decode(video=0))
    assert len(frames) == len(pictures)
    assert [int(frame.pts * frame.time_base * 90000) for frame in frames] == [picture[1] for picture in pictures]
    with av.open(io.BytesIO(source_packet), format="obu" if codec == "av1" else "h264") as media:
        reference = next(media.decode(video=0)).to_ndarray(format="yuv420p")
    assert all(np.array_equal(frame.to_ndarray(format="yuv420p"), reference) for frame in frames)


@pytest.mark.parametrize("mode", [
    "event", "inband", "inband_no_type", "caller", "missing_sps", "missing_pps", "advertised_differs",
    "changed_size", "changed_codec", "changed_color", "wrong_pts", "wrong_duration",
    "incomplete", "event_flood", "error_event", "held_input", "malformed_header",
    "missing_configuration", "overflow",
])
def test_mf_output_events_configuration_and_exact_identity(mf_output_host, tmp_path, mode):
    from test_packetmux import fixture_packets

    packet = fixture_packets()[0]
    starts = list(re.finditer(b"\x00\x00(?:\x00)?\x01", packet.data))
    units = [(packet.data[start.end()] & 31,
              packet.data[start.start():starts[i + 1].start() if i + 1 < len(starts) else len(packet.data)])
             for i, start in enumerate(starts)]
    header = b"".join(data for kind, data in units if kind in (7, 8))
    if mode == "advertised_differs":
        # A valid alternative level on the advertised type. Real AMD supplies
        # provisional type headers which differ from its first actual packet.
        advertised = bytearray(header)
        start = next(re.finditer(b"\x00\x00(?:\x00)?\x01", header)).end()
        advertised[start + 3] = 42 if advertised[start + 3] != 42 else 40
        header = bytes(advertised)
    keep = ({7} if mode == "missing_pps" else {8} if mode == "missing_sps"
            else {7, 8} if mode in ("inband", "inband_no_type", "advertised_differs") else set())
    payload = b"".join(data for kind, data in units if kind not in (7, 8) or kind in keep)
    header_path, payload_path, result_path = [tmp_path / name for name in ("configuration.h264", "packet.h264", "delivered.h264")]
    header_path.write_bytes(header)
    payload_path.write_bytes(payload)
    result = subprocess.run([str(mf_output_host), mode, str(header_path), str(payload_path), str(result_path)],
        cwd=tmp_path, capture_output=True, text=True, timeout=5, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    assert "positive media-type bitrate hint; native bitrate fields stay zero" in result.stdout
    success = mode in {"event", "inband", "inband_no_type", "caller", "missing_sps", "missing_pps", "advertised_differs"}
    if not success:
        assert "accepted=0" in result.stdout and "delivered=0" in result.stdout
        assert result_path.read_bytes() == b""
        if mode == "event_flood":
            assert "outputs=32" in result.stdout
        return
    assert "packet occurrence=41 pts=3002 duration=86998 flags=1" in result.stdout
    count = 2 if mode == "advertised_differs" else 1
    assert (f"inputs={count} outputs={3 * count} type_sets={count} forced={count} delivered={count} "
            f"submitted={count} completed={count} closed=1") in result.stdout
    delivered = result_path.read_bytes()
    # Normalization retains every existing NAL exactly once. This independent
    # parser checks both missing-single-header and already-in-band cases.
    nals = [nal for nal in re.split(b"\x00\x00\x00?\x01", delivered) if nal]
    originals = [nal for nal in re.split(b"\x00\x00\x00?\x01", packet.data) if nal]
    assert sorted(nals) == sorted(originals * count)
    if mode in ("inband", "inband_no_type"):
        assert delivered == payload

    if count == 2:
        first_size = int(re.search(r"packet occurrence=41 .*bytes=(\d+)", result.stdout)[1])
        pictures = [(41, 3002, 86998, True, delivered[:first_size]),
                    (42, 90000, 9000, True, delivered[first_size:])]
    else:
        pictures = [(41, 3002, 86998, True, delivered)]
    assert_mux_preserves_picture("h264", pictures, packet.data)


@pytest.fixture(scope="module")
def av1_packet():
    """Independent software encoder; never borrows an installed GPU or driver."""
    from fractions import Fraction
    import av
    import numpy as np

    if "libsvtav1" not in av.codecs_available:
        pytest.skip("PyAV build lacks the isolated CPU AV1 fixture encoder")
    encoder = av.CodecContext.create("libsvtav1", "w")
    encoder.width, encoder.height, encoder.pix_fmt = 320, 240, "yuv420p"
    encoder.time_base, encoder.framerate = Fraction(1, 30), Fraction(30, 1)
    encoder.thread_count = 1
    encoder.options = {"preset": "11", "crf": "20", "svtav1-params": "lp=1:lookahead=0"}
    encoder.open()
    ys, xs = np.indices((240, 320))
    pixels = np.stack([(xs // 40 * 31 + 17) & 255, (ys // 30 * 25 + 33) & 255,
                       (xs // 40 * 17 + ys // 30 * 7 + 70) & 255], axis=-1).astype(np.uint8)
    frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
    frame.pts = 0
    packets = [*encoder.encode(frame), *encoder.encode(None)]
    assert len(packets) == 1
    return bytes(packets[0])


def av1_obus(data):
    """Independent fixture framing; retain the original OBU bytes unchanged."""
    at, result = 0, []
    while at < len(data):
        first = at
        header = data[at]
        at += 1 + bool(header & 4)
        assert header & 2
        size, shift = 0, 0
        while True:
            octet = data[at]
            at += 1
            size |= (octet & 127) << shift
            if not octet & 128:
                break
            shift += 7
            assert shift <= 49
        at += size
        assert at <= len(data)
        result.append(((header >> 3) & 15, data[first:at]))
    return result


@pytest.mark.parametrize("mode", ["av1_event", "av1_inband", "av1_inband_no_type",
    "av1_record", "av1_missing_configuration", "av1_bad_leb", "av1_bad_packet", "av1_overflow"])
def test_mf_av1_configuration_is_bounded_and_self_describing(mf_output_host, av1_packet, tmp_path, mode):
    units = av1_obus(av1_packet)
    header = b"".join(data for kind, data in units if kind == 1)
    payload = (av1_packet if mode in {"av1_inband", "av1_inband_no_type"}
               else b"".join(data for kind, data in units if kind != 1))
    if mode == "av1_record":
        header = b"\x81\x00\x0c\x00" + header  # Different representation; no speculative extraction.
    if mode == "av1_bad_leb":
        header = b"\x0a\x80"  # Unterminated payload length.
    if mode == "av1_bad_packet":
        payload = b"\x32\xff\xff\xff\xff\xff\xff\xff\xff"  # Eight continuing length bytes.
    header_path, payload_path, result_path = [tmp_path / name for name in ("configuration.obu", "packet.obu", "delivered.obu")]
    header_path.write_bytes(header)
    payload_path.write_bytes(payload)
    result = subprocess.run([str(mf_output_host), mode, str(header_path), str(payload_path), str(result_path)],
        cwd=tmp_path, capture_output=True, text=True, timeout=5, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    success = mode in {"av1_event", "av1_inband", "av1_inband_no_type"}
    if not success:
        assert "accepted=0" in result.stdout and "delivered=0" in result.stdout
        assert "operation=sequence_header" in result.stderr
        assert result_path.read_bytes() == b""
        if mode == "av1_record":
            assert "error=8" in result.stdout and "inputs=0" in result.stdout
        return
    assert "packet occurrence=41 pts=3002 duration=86998 flags=1" in result.stdout
    assert "inputs=1 outputs=3 type_sets=1 forced=1 delivered=1 submitted=1 completed=1 closed=1" in result.stdout
    delivered = result_path.read_bytes()
    assert av1_obus(delivered) == units

    assert_mux_preserves_picture("av1", [(41, 3002, 86998, True, delivered)], av1_packet)
