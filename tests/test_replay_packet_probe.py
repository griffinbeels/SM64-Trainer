"""Packet optimization must agree with independent decoded picture timestamps."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.replay import extract


@pytest.mark.parametrize("bframes", [0, 2])
def test_real_vfr_packets_preserve_decoded_pts_or_fall_back(tmp_path, bframes):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg unavailable")
    path = tmp_path / "vfr.mp4"
    subprocess.run([
        ffmpeg, "-v", "error", "-f", "lavfi", "-i",
        "testsrc2=size=160x120:rate=30:duration=1", "-vf",
        "select='not(between(n,5,17))'", "-fps_mode", "vfr",
        "-c:v", "libx264", "-bf", str(bframes),
        "-video_track_timescale", "90000", str(path),
    ], check=True, capture_output=True, timeout=20, **quiet_spawn_kwargs())
    decoded = extract.frame_times_of(ffmpeg, path)
    packets = extract._native_packet_times(extract.ffprobe_beside(ffmpeg), path, None)
    assert decoded and len(decoded) == 17
    assert max(b - a for a, b in zip(decoded, decoded[1:], strict=False)) > .4
    if bframes:
        assert packets is None, "reordered media must use decoder semantics"
    else:
        assert packets == decoded
    assert extract.frame_times_of(ffmpeg, path, native_packets=True) == decoded


def test_tiny_held_transport_stream_matches_decoded_picture():
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg unavailable")
    path = Path(__file__).parent / "fixtures" / "replay_tiny_held.ts"
    packets = extract._native_packet_times(extract.ffprobe_beside(ffmpeg), path, "mpegts")
    decoded = extract.frame_times_of(ffmpeg, path, input_format="mpegts")
    assert packets == decoded
    assert [round(t * 90000) for t in packets] == [369919]


@pytest.mark.parametrize("bad", ["missing", "duplicate", "reverse", "discard", "corrupt",
                                  "codec", "timebase", "bframes", "stderr"])
def test_untrusted_packet_evidence_falls_back_to_decoding(monkeypatch, tmp_path, bad):
    stream = {"codec_name": "h264", "has_b_frames": 0, "time_base": "1/90000"}
    packets = [{"pts": 0, "dts": 0, "flags": "K_"},
               {"pts": 3000, "dts": 3000, "flags": "__"}]
    if bad == "missing":
        packets[1].pop("pts")
    elif bad == "duplicate":
        packets[1].update(pts=0, dts=0)
    elif bad == "reverse":
        packets.reverse()
    elif bad in ("discard", "corrupt"):
        packets[1]["flags"] = "D" if bad == "discard" else "C"
    elif bad == "codec":
        stream["codec_name"] = "hevc"
    elif bad == "timebase":
        stream["time_base"] = "1/1000"
    elif bad == "bframes":
        stream["has_b_frames"] = 2
    calls = []

    def probe(args, **kwargs):
        calls.append(args)
        packet_probe = "json" in args
        return subprocess.CompletedProcess(args, 0,
            json.dumps({"streams": [stream], "packets": packets}) if packet_probe else "0.000000\n0.033333\n",
            "damaged stream" if packet_probe and bad == "stderr" else "")

    monkeypatch.setattr(extract, "ffprobe_beside", lambda _: "ffprobe")
    monkeypatch.setattr(extract.subprocess, "run", probe)
    assert extract.frame_times_of("ffmpeg", tmp_path / "clip.mp4", native_packets=True) == [0, .033333]
    assert len(calls) == 2


def test_arbitrary_media_never_assumes_packet_identity(monkeypatch, tmp_path):
    monkeypatch.setattr(extract, "_native_packet_times", lambda *args: pytest.fail("native only"))
    monkeypatch.setattr(extract, "ffprobe_beside", lambda _: "ffprobe")
    monkeypatch.setattr(extract.subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args, 0, "0.000000\n", ""))
    assert extract.frame_times_of("ffmpeg", tmp_path / "download.mp4") == [0]
