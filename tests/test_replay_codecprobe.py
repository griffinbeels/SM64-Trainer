"""Hardware selection must prove output, not merely find a codec name."""
import io
import subprocess
from types import SimpleNamespace

import av
import numpy as np
import pytest

from sm64_events.replay import codecprobe
from sm64_events.replay.config import fragment_mux_options
from sm64_events.replay.encoder import pick_video_codec
from test_replay_picture_identity import av1_encoder as av1_encoder  # real availability
from test_replay_picture_identity import encoder as encoder  # real availability only


def test_probe_witness_has_independent_pixels_and_close_unequal_ticks():
    with av.open(io.BytesIO(codecprobe.probe_input()), format="nut") as container:
        frames = list(container.decode(video=0))
    assert [f.pts * f.time_base * 90000 for f in frames] == [
        90000, 93000, 93001, 99000, 100500, 108000]
    top = []
    for frame in frames:
        assert (frame.width, frame.height) == (640, 480)
        rgb = frame.to_ndarray(format="rgb24")
        top.append(tuple(rgb[120, 320]))
        assert np.array_equal(rgb[120, 320], rgb[360, 320][::-1])
    assert top[0] == (40, 100, 220)
    assert len(set(top)) == 6


@pytest.mark.parametrize("winner", ["h264_nvenc", "h264_amf", "h264_qsv", "libx264"])
def test_selection_uses_actual_ffmpeg_and_stops_after_first_success(monkeypatch, winner):
    calls = []
    monkeypatch.setattr(codecprobe, "probe_input", lambda: b"one shared witness")

    def check(ffmpeg, codec, source):
        calls.append(codec)
        assert ffmpeg == "actual/custom/ffmpeg.exe"
        assert source == b"one shared witness"
        if codec != winner:
            raise RuntimeError("device unavailable")

    monkeypatch.setattr(codecprobe, "check_ffmpeg_codec", check)
    assert pick_video_codec("actual/custom/ffmpeg.exe") == winner
    expected = ["h264_nvenc", "h264_amf", "h264_qsv"]
    if winner in expected:
        expected = expected[:expected.index(winner) + 1]
    assert calls == expected


@pytest.mark.parametrize("failure", [ValueError("retimed"), OSError("missing"),
                                      subprocess.TimeoutExpired("ffmpeg", 8)])
def test_rejected_output_and_timeout_advance_to_next_vendor(monkeypatch, failure):
    calls = []
    monkeypatch.setattr(codecprobe, "probe_input", lambda: b"source")

    def check(_ffmpeg, codec, _source):
        calls.append(codec)
        if codec == "h264_nvenc":
            raise failure

    monkeypatch.setattr(codecprobe, "check_ffmpeg_codec", check)
    assert codecprobe.pick_ffmpeg_codec("ffmpeg") == "h264_amf"
    assert calls == ["h264_nvenc", "h264_amf"]


def test_encoder_success_without_decodable_output_is_rejected(monkeypatch):
    monkeypatch.setattr(codecprobe.subprocess, "run", lambda *a, **k:
                        SimpleNamespace(returncode=0, stdout=b"garbage", stderr=b""))
    with pytest.raises(av.error.FFmpegError):
        codecprobe.check_ffmpeg_codec("ffmpeg", "libx264", b"source")


@pytest.mark.parametrize("codec,flag", [("h264_nvenc", "-forced-idr"),
                                       ("av1_nvenc", "-forced-idr"),
                                       ("h264_amf", "-forced_idr"),
                                       ("h264_qsv", "-forced_idr")])
def test_probe_is_hidden_bounded_and_uses_vendor_idr_spelling(monkeypatch, codec, flag):
    captured = {}

    def run(args, **kwargs):
        captured.update(args=args, **kwargs)
        return SimpleNamespace(returncode=1, stderr=b"no compatible device")

    monkeypatch.setattr(codecprobe.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="no compatible device"):
        codecprobe.check_ffmpeg_codec("ffmpeg", codec, b"source")
    assert captured["args"][captured["args"].index(flag) + 1] == "1"
    assert 0 < captured["timeout"] <= 10
    assert captured["input"] == b"source"
    for key, value in codecprobe.quiet_spawn_kwargs().items():
        if key == "startupinfo":
            assert captured[key].dwFlags == value.dwFlags
        else:
            assert captured[key] == value


def test_real_encoder_preserves_startup_witness(encoder):
    # Availability was checked separately. Invalid output is a failure, NEVER
    # a skip or another fallback that would make a broken hardware path green.
    ffmpeg, codec = encoder
    codecprobe.check_ffmpeg_codec(ffmpeg, codec, codecprobe.probe_input())


def probe_args(monkeypatch, codec):
    """The exact child command line the probe would run for `codec`."""
    captured = []

    def run(args, **_kwargs):
        captured.append(list(args))
        return SimpleNamespace(returncode=1, stderr=b"no compatible device")

    monkeypatch.setattr(codecprobe.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="no compatible device"):
        codecprobe.check_ffmpeg_codec("ffmpeg", codec, b"source")
    return captured[0]


def test_the_probe_proves_a_codec_in_the_container_the_sinks_write(monkeypatch):
    """A startup witness is only worth the transport it runs through. Every
    option `fragment_mux_options()` hands the real sinks must reach the probe's
    child, from that one owner, and MPEG-TS must be nowhere near it."""
    args = probe_args(monkeypatch, "h264_nvenc")
    assert args[-3:] == ["-f", "mp4", "pipe:1"]
    for name, value in fragment_mux_options().items():
        assert args[args.index(f"-{name}") + 1] == value
    assert not any("mpegts" in str(arg) for arg in args)


def test_mpegts_hid_an_av1_gpu_that_the_sinks_own_container_shows(
        monkeypatch, av1_encoder):
    """WHY the probe moved containers, measured rather than argued.

    MPEG-TS has no usable AV1 mapping: the identical encoded pictures yield
    ZERO video streams there and decode completely in fragmented MP4. Under
    the old probe this GPU reported itself unable to encode AV1 -- a fault of
    the witness, not of the hardware. Putting MPEG-TS back is the mutation
    that proves the new container is doing the work."""
    ffmpeg, codec = av1_encoder
    source = codecprobe.probe_input()
    codecprobe.check_ffmpeg_codec(ffmpeg, codec, source)  # the shipped path

    args = probe_args(monkeypatch, codec)
    monkeypatch.undo()
    as_mpegts = [ffmpeg, *args[1:-3], "-mpegts_copyts", "1", "-f", "mpegts",
                 "pipe:1"]
    done = subprocess.run(as_mpegts, input=source, capture_output=True,
                          check=True, timeout=30,
                          **codecprobe.quiet_spawn_kwargs())
    assert done.stdout
    with av.open(io.BytesIO(done.stdout), format="mpegts") as container:
        assert len(container.streams.video) == 0


@pytest.mark.parametrize("mutation", [["-vf", "vflip"], ["-vf", "setpts=PTS+1/TB"],
                                      ["-frames:v", "5"]])
def test_real_output_oracle_rejects_flip_retime_and_loss(monkeypatch, mutation):
    import shutil
    from sm64_events.core.paths import bundled_ffmpeg
    ffmpeg = bundled_ffmpeg() or shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg required")
    real_run = subprocess.run

    def changed_run(args, **kwargs):
        return real_run([*args[:-1], *mutation, args[-1]], **kwargs)

    monkeypatch.setattr(codecprobe.subprocess, "run", changed_run)
    with pytest.raises(ValueError, match="changed|expected"):
        codecprobe.check_ffmpeg_codec(ffmpeg, "libx264", codecprobe.probe_input())
