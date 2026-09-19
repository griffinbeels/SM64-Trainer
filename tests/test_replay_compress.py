"""A saved replay may be re-encoded only when nothing a consumer reads moves.

Real ffmpeg over a generated clip with UNEVEN picture times (the picture
feed's shape) and an audio track. libx264 is the only candidate named here so
the file runs the same on a machine with no NVIDIA encoder.
"""
import json
import shutil
import subprocess

import pytest

from sm64_events.core.paths import bundled_ffmpeg
from sm64_events.replay import compress
from sm64_events.replay.config import ARCHIVE_CODECS, CLIP_MAXRATE, video_quality_args
from sm64_events.replay.extract import frame_times_of

CPU_ONLY = ("libx264",)
NAME = "attempt_0042_test-course_test-star_0m02s00.mp4"


def _ffmpeg() -> str:
    ff = bundled_ffmpeg() or shutil.which("ffmpeg")
    if not ff:
        pytest.skip("ffmpeg binary not available")
    return ff


@pytest.fixture(scope="module")
def source_clip(tmp_path_factory):
    """Two seconds, 30 Hz with every 7th picture missing (so the gaps are
    uneven), near-lossless so an archive encode has something to save."""
    ff = _ffmpeg()
    clip = tmp_path_factory.mktemp("source") / NAME
    subprocess.run([
        ff, "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=640x480:rate=30:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
        "-vf", "select='mod(n,7)'", "-fps_mode", "passthrough",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "4", "-bf", "0",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(clip),
    ], check=True, capture_output=True)
    return clip


@pytest.fixture
def saved(source_clip, tmp_path):
    """A save tree entry: the clip plus the sidecar that holds its contract."""
    clip = tmp_path / "2026-09-18" / "session_1" / NAME
    clip.parent.mkdir(parents=True)
    shutil.copy2(source_clip, clip)
    times = frame_times_of(_ffmpeg(), clip)
    sidecar = {"duration_s": 2.0, "truncated": False, "encode": "picture_feed",
               "frame_times": [round(t, 6) for t in times],
               "frame_map": list(range(100, 100 + len(times)))}
    clip.with_suffix(".json").write_text(json.dumps(sidecar))
    return clip


def _leftovers(clip):
    return sorted(p.name for p in clip.parent.iterdir() if "shrink" in p.name)


def test_shrink_then_adopt_changes_the_bytes_and_nothing_a_consumer_reads(saved):
    ff = _ffmpeg()
    before = compress.fingerprint(ff, saved)
    sidecar_before = json.loads(saved.with_suffix(".json").read_text())
    gaps = {b - a for a, b in zip(compress.picture_ticks(ff, saved),
                                  compress.picture_ticks(ff, saved)[1:], strict=False)}
    assert len(gaps) > 1, "the fixture must be variable-rate or this proves nothing"

    assert compress.shrink(ff, saved, codecs=CPU_ONLY)["state"] == "ready"
    assert compress.file_sha256(saved) == before.sha256, "step 1 never touches the clip"
    done = compress.adopt(saved)

    after = compress.fingerprint(ff, saved)
    assert done["state"] == "compressed" and after.sha256 == done["sha256"]
    assert after.bytes < before.bytes
    assert (after.pictures, after.picture_times_sha256, after.audio_sha256) == (
        before.pictures, before.picture_times_sha256, before.audio_sha256)
    assert abs(after.end_ticks - before.end_ticks) <= 1
    sidecar_after = json.loads(saved.with_suffix(".json").read_text())
    assert {k: sidecar_after[k] for k in sidecar_before} == sidecar_before
    assert sidecar_after["media"]["original"]["sha256"] == before.sha256
    assert _leftovers(saved) == []


def test_an_encode_that_retimes_pictures_is_refused_and_the_clip_survives(saved):
    ff = _ffmpeg()
    before = compress.file_sha256(saved)

    def onto_a_fixed_grid(args, **kwargs):
        args = ["cfr" if a == "passthrough" else a for a in args]
        at = args.index("-fps_mode")
        return subprocess.run([*args[:at], "-r", "30", *args[at:]], **kwargs)

    kept = compress.shrink(ff, saved, codecs=CPU_ONLY, run=onto_a_fixed_grid)

    assert kept["state"] == "kept_original", kept
    assert compress.file_sha256(saved) == before
    assert compress.adopt(saved) is None
    assert json.loads(saved.with_suffix(".json").read_text())["media"]["state"] == "kept_original"
    assert _leftovers(saved) == []


def test_the_sidecars_frame_times_are_the_contract_not_the_files_own(saved):
    sidecar = saved.with_suffix(".json")
    meta = json.loads(sidecar.read_text())
    meta["frame_times"][5] = round(meta["frame_times"][5] + 0.001, 6)
    sidecar.write_text(json.dumps(meta))

    kept = compress.shrink(_ffmpeg(), saved, codecs=CPU_ONLY)

    assert kept["state"] == "kept_original"
    assert any("sidecar" in reason for reason in kept["reasons"]), kept


def test_a_compressed_replay_is_never_encoded_a_second_time(saved):
    ff = _ffmpeg()
    compress.shrink(ff, saved, codecs=CPU_ONLY)
    first = compress.adopt(saved)

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("a second generation of loss on a saved replay")

    assert compress.shrink(ff, saved, codecs=CPU_ONLY, run=must_not_run) == first


@pytest.mark.parametrize("codec", ARCHIVE_CODECS)
def test_every_archive_candidate_runs_without_picture_reordering(codec, tmp_path):
    """Reordered H.264 passed ffprobe and lost a clip's last 11 pictures in
    Chromium (2026-09-18). The flag is structural, the quality is a registry row."""
    args = compress.encode_args("ffmpeg", tmp_path / "a.mp4", tmp_path / "b", codec, 900)
    assert args[args.index("-bf") + 1] == "0"
    assert args[args.index("-fps_mode") + 1] == "passthrough"
    assert args[args.index("-c:a") + 1] == "copy"
    assert video_quality_args(codec, "archive", CLIP_MAXRATE), codec


def test_a_clip_a_player_is_reading_is_not_swapped_until_it_goes_quiet(saved):
    ff = _ffmpeg()
    compress.shrink(ff, saved, codecs=CPU_ONLY)
    now = [1000.0]
    worker = compress.SavedReplayCompressor(ff, saved.parents[2], idle_s=120,
                                            clock=lambda: now[0])
    worker.touch(saved)
    original = compress.file_sha256(saved)

    assert worker.adopt_ready() == [] and compress.file_sha256(saved) == original
    now[0] += 121
    assert [d["state"] for d in worker.adopt_ready()] == ["compressed"]
    assert compress.file_sha256(saved) != original


def test_session_start_adopts_what_the_last_session_proved(saved):
    ff = _ffmpeg()
    compress.shrink(ff, saved, codecs=CPU_ONLY)
    worker = compress.SavedReplayCompressor(ff, saved.parents[2], poll_s=0.05)
    worker.touch(saved)   # last session's player is gone with that session
    worker.start()
    try:
        assert json.loads(saved.with_suffix(".json").read_text())["media"]["state"] == "compressed"
    finally:
        worker.stop()


def test_a_save_queues_its_clip_and_a_request_for_it_marks_it_in_use(tmp_path):
    from test_replay_service import attempt, make_service

    class Recording:
        def __init__(self):
            self.queued, self.touched = [], []

        def enqueue(self, path):
            self.queued.append(path)

        def touch(self, path):
            self.touched.append(path)

    svc = make_service(tmp_path, [attempt()])
    svc.compressor = Recording()

    path = svc.save(42)["path"]
    served = svc.saved_clip_path(42)

    assert [str(p) for p in svc.compressor.queued] == [path]
    assert svc.compressor.touched == [served] and str(served) == path


def test_a_crash_between_the_two_writes_is_settled_by_the_files_own_digest(saved):
    sidecar = saved.with_suffix(".json")
    meta = json.loads(sidecar.read_text())
    digest = compress.file_sha256(saved)

    sidecar.write_text(json.dumps({**meta, "media": {"state": "adopting", "sha256": digest}}))
    compress.settle(saved)
    assert json.loads(sidecar.read_text())["media"]["state"] == "compressed"

    sidecar.write_text(json.dumps({**meta, "media": {"state": "adopting", "sha256": "0" * 64}}))
    compress.settle(saved)
    assert "media" not in json.loads(sidecar.read_text())
