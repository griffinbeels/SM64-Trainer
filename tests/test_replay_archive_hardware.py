"""Archive vendors share the replay proof, never assume matching quality scales."""
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.replay import compress, config
from test_replay_compress import saved as saved, source_clip as source_clip


VENDOR_CODECS = ("av1_amf", "h264_amf", "av1_qsv", "h264_qsv")


@pytest.fixture(autouse=True)
def forget_witnesses():
    witness = compress._archive_hardware_reason
    witness.cache_clear()
    yield
    witness.cache_clear()


def test_nvidia_preference_and_policy_are_preserved():
    assert config.ARCHIVE_CODECS[:2] == ("av1_nvenc", "h264_nvenc")
    assert config.ARCHIVE_CODECS[-1] == "libx264"
    assert set(VENDOR_CODECS) <= set(config.ARCHIVE_CODECS)
    assert config.video_quality_args("h264_nvenc", "archive", "60M") == [
        "-preset", "p7", "-tune", "hq", "-profile:v", "high", "-rc", "vbr",
        "-cq", str(config.ARCHIVE_H264_CQ), "-b:v", "0", "-maxrate", "60M", "-bufsize", "60M"]
    assert config.video_quality_args("av1_nvenc", "archive", "60M") == [
        "-preset", "p7", "-tune", "hq", "-rc", "vbr", "-cq", str(config.ARCHIVE_AV1_CQ),
        "-b:v", "0", "-maxrate", "60M", "-bufsize", "60M"]


@pytest.mark.parametrize("backend,expected", [(None, True), ("nvenc", True),
                                              ("windows", False), ("unknown", False)])
def test_av1_format_never_implies_another_backends_nvenc_quality(monkeypatch, backend, expected):
    monkeypatch.setattr(compress.mediaprobe, "probe",
                        lambda _clip: SimpleNamespace(video=SimpleNamespace(codec_name="av1")))
    monkeypatch.setattr(config, "VIDEO_AV1_CQ", config.ARCHIVE_AV1_CQ)
    assert compress._recorded_at_archive_quality(
        None, config.ARCHIVE_CODECS, encoder_backend=backend) is expected


def test_saved_backend_provenance_reaches_the_skip_decision(saved, monkeypatch):
    import json

    sidecar = saved.with_suffix(".json")
    meta = json.loads(sidecar.read_text())
    sidecar.write_text(json.dumps({**meta, "encoder_backend": "windows"}))
    seen = []

    def skip(_saved, _codecs, *, encoder_backend):
        seen.append(encoder_backend)
        return True

    monkeypatch.setattr(compress, "_recorded_at_archive_quality", skip)
    assert compress.shrink("ffmpeg", saved)["state"] == "kept_original"
    assert seen == ["windows"]


@pytest.mark.parametrize("clean,copy,expected", [
    ("windows", {}, "windows"),
    ("windows", {"encoder_backend": None}, "windows"),
    ("windows", {"encoder_backend": "nvenc"}, "nvenc"),
    ("nvenc", {"encoder_backend": "windows"}, "windows"),
    (None, {}, None),
])
def test_ghost_copy_uses_its_explicit_backend_or_the_clean_sessions(
        saved, monkeypatch, clean, copy, expected):
    import json

    sidecar = saved.with_suffix(".json")
    meta = json.loads(sidecar.read_text())
    sidecar.write_text(json.dumps({**meta, "encoder_backend": clean, "ghost_copy": copy}))
    shutil.copy2(saved, compress.ghost_copy_of(saved))
    seen = []

    def skip(_saved, _codecs, *, encoder_backend):
        seen.append(encoder_backend)
        return True

    monkeypatch.setattr(compress, "_recorded_at_archive_quality", skip)
    assert compress.shrink_ghost_copy("ffmpeg", saved)["state"] == "kept_original"
    assert seen == [expected]


@pytest.mark.parametrize("codec,constant,flag", [
    ("h264_amf", "ARCHIVE_AMF_H264_QP", "-qp_p"),
    ("av1_amf", "ARCHIVE_AMF_AV1_QP", "-qp_p"),
    ("h264_qsv", "ARCHIVE_QSV_H264_ICQ", "-global_quality"),
    ("av1_qsv", "ARCHIVE_QSV_AV1_QUALITY", "-global_quality"),
])
def test_archive_vendor_quality_reads_its_own_target_at_call(monkeypatch, codec, constant, flag):
    before_ring = config.video_quality_args(codec, "realtime", "30M")
    monkeypatch.setattr(config, constant, 27)
    args = config.video_quality_args(codec, "archive", "60M")
    assert args[args.index(flag) + 1] == "27"
    assert config.video_quality_args(codec, "realtime", "30M") == before_ring
    assert "-cq" not in args and "-crf" not in args


@pytest.mark.parametrize("codec", VENDOR_CODECS)
def test_archive_witness_is_bounded_hidden_and_uses_the_actual_binary(monkeypatch, codec):
    calls = []

    def unavailable(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=1, stderr=b"device lacks this codec")

    monkeypatch.setattr(compress, "run_child", unavailable)
    quality = tuple(config.video_quality_args(codec, "archive", "60M"))
    assert "device lacks this codec" in compress._archive_hardware_reason("actual-ffmpeg", codec, quality)
    assert "device lacks this codec" in compress._archive_hardware_reason("actual-ffmpeg", codec, quality)
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == "actual-ffmpeg" and args[-3:] == ["-f", "mp4", "pipe:1"]
    assert args[args.index("-bf") + 1] == "0"
    assert args[args.index("-fps_mode") + 1] == "passthrough"
    assert kwargs["input"] and 0 < kwargs["timeout"] <= 10
    assert kwargs["creationflags"] & quiet_spawn_kwargs().get("creationflags", 0) == (
        quiet_spawn_kwargs().get("creationflags", 0))
    assert compress._archive_hardware_reason("actual-ffmpeg", codec, (*quality, "changed"))
    assert len(calls) == 2, "a changed quality target earns a fresh witness"


@pytest.mark.parametrize("mutation", [[], ["-vf", "vflip"], ["-vf", "setpts=PTS+1/TB"],
                                       ["-frames:v", "5"], ["-bf", "2"]])
def test_archive_witness_independently_rejects_broken_pixels_ticks_count_and_reordering(
        monkeypatch, mutation):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg required")
    original = compress.run_child

    def changed(args, **kwargs):
        # Exercise the shipped witness with a deterministic CPU encoder. This
        # proves the oracle, not availability or quality of any vendor GPU.
        return original([*args[:-1], *mutation, args[-1]], **kwargs)

    monkeypatch.setattr(compress, "run_child", changed)
    reason = compress._archive_hardware_reason(
        ffmpeg, "libx264", ("-preset", "ultrafast", "-crf", "8"))
    assert (reason is None) == (not mutation), reason


def test_unavailable_vendor_routes_fall_back_and_still_pass_the_full_clip_proof(saved, monkeypatch):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg required")
    queried = []

    def absent(_ffmpeg, codec, _quality):
        queried.append(codec)
        return "actual driver rejected codec"

    monkeypatch.setattr(compress, "_archive_hardware_reason", absent)
    original = compress.file_sha256(saved)
    result = compress.shrink(ffmpeg, saved, codecs=(*VENDOR_CODECS, "libx264"))
    assert queried == list(VENDOR_CODECS)
    assert result["state"] == "ready" and result["codec"] == "libx264"
    assert result["pictures"] == len(compress.picture_ticks(saved))
    assert result["picture_times_sha256"] == result["original"]["picture_times_sha256"]
    assert result["audio_sha256"] == result["original"]["audio_sha256"]
    assert compress.file_sha256(saved) == original


@pytest.mark.skipif(os.environ.get("SM64_TEST_GPU_ARCHIVE") != "1",
                    reason="real GPU archive qualification requires a serialized probe slot")
@pytest.mark.parametrize("codec", VENDOR_CODECS)
def test_real_archive_vendor_is_interchangeable(saved, codec):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg required")
    # Availability is a separate trivial open. A subsequently broken witness
    # or replay proof is a failure, never another availability skip.
    opened = subprocess.run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=size=640x480",
        "-frames:v", "1", "-c:v", codec, "-f", "null", "-"],
        capture_output=True, timeout=15, check=False, **quiet_spawn_kwargs())
    if opened.returncode:
        pytest.skip(f"{codec} is unavailable on this hardware/driver (exit {opened.returncode})")
    reason = compress._archive_hardware_reason(
        ffmpeg, codec, tuple(config.video_quality_args(codec, "archive", "60M")))
    assert reason is None, reason
    result = compress.shrink(ffmpeg, saved, codecs=(codec,))
    assert result["state"] == "ready", result
    assert result["codec"] == codec
    assert result["ssim"] >= compress.MIN_SSIM
    assert result["bytes"] <= result["original"]["bytes"] * (1 - compress.MIN_SAVING)
    assert result["picture_times_sha256"] == result["original"]["picture_times_sha256"]
    assert result["audio_sha256"] == result["original"]["audio_sha256"]
