"""Packet optimization must agree with independent decoded picture timestamps."""
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.replay import mediaprobe
from sm64_events.replay.mediaprobe import Packet, VideoStream


def _packet_times(packets):
    return [round(p.pts / 90000, 6) for p in packets] if packets else None


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
    decoded = mediaprobe.frame_times_of(path)
    packets = _packet_times(mediaprobe.native_packet_index(path))
    assert decoded and len(decoded) == 17
    assert max(b - a for a, b in zip(decoded, decoded[1:], strict=False)) > .4
    if bframes:
        assert packets is None, "reordered media must use decoder semantics"
    else:
        assert packets == decoded
    assert mediaprobe.frame_times_of(path, native_packets=True) == decoded


def test_tiny_held_transport_stream_matches_decoded_picture():
    path = Path(__file__).parent / "fixtures" / "replay_tiny_held.ts"
    packets = _packet_times(mediaprobe.native_packet_index(path, input_format="mpegts"))
    decoded = mediaprobe.frame_times_of(path, input_format="mpegts")
    assert packets == decoded
    assert [round(t * 90000) for t in packets] == [369919]


NATIVE = VideoStream(codec_name="h264", has_b_frames=False, time_base=Fraction(1, 90000),
                     start=0, duration=6000, frames=2, width=160, height=120,
                     rate=Fraction(30))


def _read_as(monkeypatch, stream, packets, indexed=0):
    monkeypatch.setattr(mediaprobe, "_read_packets",
                        lambda *_args: (stream, packets, indexed))


@pytest.mark.parametrize("bad", ["missing", "duplicate", "reverse", "discard", "corrupt",
                                  "codec", "timebase", "bframes", "undelivered"])
def test_untrusted_packet_evidence_falls_back_to_decoding(monkeypatch, tmp_path, bad):
    stream = NATIVE
    packets = [Packet(0, 0, True, False, False), Packet(3000, 3000, False, False, False)]
    indexed = 2
    if bad == "missing":
        packets[1] = Packet(None, 3000, False, False, False)
    elif bad == "duplicate":
        packets[1] = Packet(0, 0, False, False, False)
    elif bad == "reverse":
        packets.reverse()
    elif bad in ("discard", "corrupt"):
        packets[1] = Packet(3000, 3000, False, bad == "discard", bad == "corrupt")
    elif bad == "codec":
        stream = VideoStream(**{**vars(NATIVE), "codec_name": "hevc"})
    elif bad == "timebase":
        stream = VideoStream(**{**vars(NATIVE), "time_base": Fraction(1, 1000)})
    elif bad == "bframes":
        stream = VideoStream(**{**vars(NATIVE), "has_b_frames": True})
    elif bad == "undelivered":
        indexed = 3   # the container indexed a sample the read never reached
    decoded = []

    def decode(*_args):
        decoded.append(True)
        return [0, .033333]

    _read_as(monkeypatch, stream, packets, indexed)
    monkeypatch.setattr(mediaprobe, "_decoded_times", decode)
    assert mediaprobe.frame_times_of(tmp_path / "clip.mp4", native_packets=True) == [0, .033333]
    assert decoded == [True]


def test_trusted_packet_evidence_never_decodes(monkeypatch, tmp_path):
    _read_as(monkeypatch, NATIVE, [Packet(0, 0, True, False, False),
                                   Packet(3000, 3000, False, False, False)], 2)
    monkeypatch.setattr(mediaprobe, "_decoded_times", lambda *_args: pytest.fail("decoded"))
    assert mediaprobe.frame_times_of(tmp_path / "clip.mp4", native_packets=True) == [0, .033333]


def test_arbitrary_media_never_assumes_packet_identity(monkeypatch, tmp_path):
    monkeypatch.setattr(mediaprobe, "native_packet_index", lambda *a, **k: pytest.fail("native only"))
    monkeypatch.setattr(mediaprobe, "_decoded_times", lambda *_args: [0])
    assert mediaprobe.frame_times_of(tmp_path / "download.mp4") == [0]


@pytest.mark.parametrize("bad", [None, "visible_discard", "hidden_visible", "corrupt"])
def test_native_edit_list_only_ignores_negative_decode_preroll(monkeypatch, tmp_path, bad):
    packets = [Packet(-3000, -3000, True, True, False), Packet(0, 0, False, False, False)]
    if bad == "visible_discard":
        packets[1] = Packet(0, 0, False, True, False)
    elif bad == "hidden_visible":
        packets[0] = Packet(-3000, -3000, True, False, False)
    elif bad == "corrupt":
        packets[0] = Packet(-3000, -3000, True, True, True)
    _read_as(monkeypatch, NATIVE, packets)
    monkeypatch.setattr(mediaprobe, "_decoded_times", lambda *_args: None)
    clip = tmp_path / "clip"
    assert mediaprobe.frame_times_of(clip, native_packets=True) == (
        [-.033333, 0] if bad == "hidden_visible" else None)
    assert mediaprobe.frame_times_of(clip, native_packets=True, native_preroll=True) == (
        None if bad else [0])
