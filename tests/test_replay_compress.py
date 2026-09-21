"""A saved replay may be re-encoded only when nothing a consumer reads moves.

Real ffmpeg over a generated clip with UNEVEN picture times (the picture
feed's shape) and an audio track. libx264 is the only candidate named here so
the file runs the same on a machine with no NVIDIA encoder.
"""
import json
import shutil
import subprocess
import threading
import time

import pytest

from sm64_events.core.paths import bundled_ffmpeg
from sm64_events.replay import compress
from sm64_events.replay.config import ARCHIVE_CODECS, CLIP_MAXRATE, video_quality_args
from sm64_events.replay.extract import frame_times_of
from test_replay_picture_identity import av1_encoder as av1_encoder  # real availability

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
    return sorted(p.name for p in clip.parent.iterdir() if ".compressed." in p.name)


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


@pytest.fixture
def saved_in_av1(av1_encoder, saved):
    """The same save-tree entry, but recorded the way an AV1 GPU records it."""
    ffmpeg, codec = av1_encoder
    av1 = saved.with_name("av1_" + saved.name)
    subprocess.run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(saved),
        "-c:v", codec, *video_quality_args(codec, "realtime", CLIP_MAXRATE),
        "-bf", "0", "-fps_mode", "passthrough", "-c:a", "copy", str(av1),
    ], check=True, capture_output=True)
    shutil.copy2(saved.with_suffix(".json"), av1.with_suffix(".json"))
    return av1


def test_a_replay_already_recorded_in_av1_is_not_encoded_again(saved_in_av1):
    """The whole point of recording in AV1: Save publishes already-small bytes
    and the compression pass never runs for that replay. A second encode at
    the same target could only stack another generation of loss."""
    def must_not_run(*_args, **_kwargs):
        raise AssertionError("re-encoded a replay that was recorded in AV1")

    block = compress.shrink(_ffmpeg(), saved_in_av1, run=must_not_run)
    assert block["state"] == "kept_original"
    assert not _leftovers(saved_in_av1)
    # Settled in the sidecar, so it is never asked again.
    written = json.loads(saved_in_av1.with_suffix(".json").read_text())["media"]
    assert written["state"] == "kept_original"


def test_the_skip_stops_applying_when_the_two_av1_targets_diverge(
        saved_in_av1, monkeypatch):
    """The skip is not "AV1 is exempt", it is "the recorder already produced
    what this pass would". Re-tune the ring away from the archive and there is
    a real encode to do again -- so the guard has to read both constants at
    the call, and this is what proves it does."""
    from sm64_events.replay import config as replay_config
    monkeypatch.setattr(replay_config, "VIDEO_AV1_CQ", 20)
    assert not compress._recorded_at_archive_quality(
        _ffmpeg(), saved_in_av1, ARCHIVE_CODECS)
    monkeypatch.setattr(replay_config, "VIDEO_AV1_CQ",
                        replay_config.ARCHIVE_AV1_CQ)
    assert compress._recorded_at_archive_quality(
        _ffmpeg(), saved_in_av1, ARCHIVE_CODECS)


def test_an_h264_recording_is_still_worth_re_encoding(saved):
    """H.264 never qualifies for the skip: the ring records at cq20 for
    quality and the archive re-encodes at cq28 for size, so the bytes really
    are there to save."""
    assert not compress._recorded_at_archive_quality(
        _ffmpeg(), saved, ARCHIVE_CODECS)


def _worker(saved, **kwargs):
    """A compressor over the fixture's save tree that only uses the CPU encoder."""
    return compress.SavedReplayCompressor(
        _ffmpeg(), saved.parents[2],
        shrink_fn=lambda ffmpeg, clip, **kw: compress.shrink(ffmpeg, clip, codecs=CPU_ONLY, **kw),
        **kwargs)


def _replay_folder(saved):
    return sorted(p.name for p in saved.parent.iterdir())


def test_a_clip_a_player_is_reading_is_not_swapped_until_it_goes_quiet(saved):
    now = [1000.0]
    worker = _worker(saved, idle_s=120, clock=lambda: now[0])
    compress.shrink(_ffmpeg(), saved, codecs=CPU_ONLY, work=worker.work)
    worker.touch(saved)
    original = compress.file_sha256(saved)

    assert worker.adopt_ready() == [] and compress.file_sha256(saved) == original
    now[0] += 121
    assert [d["state"] for d in worker.adopt_ready()] == ["compressed"]
    assert compress.file_sha256(saved) != original


def test_session_start_adopts_what_the_last_session_proved(saved):
    worker = _worker(saved, poll_s=0.05)
    compress.shrink(_ffmpeg(), saved, codecs=CPU_ONLY, work=worker.work)
    worker.touch(saved)   # last session's player is gone with that session
    worker.start()
    try:
        assert json.loads(saved.with_suffix(".json").read_text())["media"]["state"] == "compressed"
    finally:
        worker.stop()


def test_closing_the_app_swaps_what_is_proven_even_if_it_was_just_watched(saved):
    """His first clean close left both files in the folder: he had watched the
    clips inside the two quiet minutes, then closed (2026-09-19)."""
    worker = _worker(saved, idle_s=120)
    compress.shrink(_ffmpeg(), saved, codecs=CPU_ONLY, work=worker.work)
    worker.touch(saved)
    assert worker.adopt_ready() == []

    worker.stop()

    assert json.loads(saved.with_suffix(".json").read_text())["media"]["state"] == "compressed"
    assert list(worker.work.iterdir()) == []


def test_a_save_closed_on_at_once_is_shrunk_next_start_and_his_folder_stays_clean(saved):
    """"I opened the replay, saved it, and then immediately closed the app" --
    and found a 0 KB temp file beside his replay (2026-09-19). The note that a
    job is owed lives in the hidden work folder, never beside the clip."""
    his_folder = _replay_folder(saved)
    closing = threading.Event()

    def killed_by_the_close(ffmpeg, clip, **kw):
        closing.set()
        return compress.shrink(ffmpeg, clip, codecs=CPU_ONLY, **{**kw, "cancel": closing})

    first = compress.SavedReplayCompressor(_ffmpeg(), saved.parents[2], poll_s=0.05,
                                           shrink_fn=killed_by_the_close)
    first.start()
    first.enqueue(saved)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not first.outcomes:
        time.sleep(0.05)
    first.stop()

    assert [o["state"] for o in first.outcomes] == ["interrupted"]
    assert "media" not in json.loads(saved.with_suffix(".json").read_text())
    assert _replay_folder(saved) == his_folder
    assert [p.name for p in first.work.iterdir()] == [saved.name + compress.JOB_SUFFIX]

    second = _worker(saved, poll_s=0.05, idle_s=0)
    second.start()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not second.outcomes:
        time.sleep(0.1)
    second.stop()

    assert json.loads(saved.with_suffix(".json").read_text())["media"]["state"] == "compressed"
    assert _replay_folder(saved) == his_folder
    assert list(second.work.iterdir()) == []


def test_the_progress_list_follows_a_job_from_waiting_to_done(saved):
    """What the close warning and the recording panel poll: which replay, what
    is happening to it, and one bar that only ever moves forward."""
    worker = _worker(saved, poll_s=0.05, idle_s=0)
    assert worker.status() == {"active": False, "jobs": []}
    worker.enqueue(saved, attempt_id=42, label="Test Course: Test Star", time_text="11.16")
    first = worker.status()
    assert first["active"] is True
    assert first["jobs"][0]["stage"] == "waiting" and first["jobs"][0]["fraction"] is None

    seen = []
    worker.start()
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            row = worker.status()["jobs"][0]
            seen.append((row["stage"], row["fraction"]))
            if row["stage"] in ("done", "kept"):
                break
            time.sleep(0.02)
    finally:
        worker.stop()

    stages = [stage for stage, _ in seen]
    assert stages[-1] == "done", seen[-5:]
    assert {"compressing", "checking"} <= set(stages), sorted(set(stages))
    fractions = [f for _, f in seen if f is not None]
    assert fractions == sorted(fractions) and fractions[-1] == 1.0
    assert any(0.0 < f < 1.0 for f in fractions), "the bar never showed work in progress"
    last = worker.status()
    assert last["active"] is False
    assert last["jobs"][0]["label"] == "Test Course: Test Star"
    assert last["jobs"][0]["attempt_id"] == 42
    assert 0 < last["jobs"][0]["to_bytes"] < last["jobs"][0]["from_bytes"]


def test_a_proven_replay_someone_is_watching_is_not_a_reason_to_warn_at_close(saved):
    worker = _worker(saved, poll_s=0.05, idle_s=3600)
    worker.touch(saved)
    worker.enqueue(saved)
    worker.start()
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and worker.status()["jobs"][0]["stage"] != "in_use":
            time.sleep(0.05)
        waiting_on_him = worker.status()
    finally:
        worker.stop()

    assert waiting_on_him["jobs"][0]["stage"] == "in_use"
    assert waiting_on_him["active"] is False     # its work is finished; close swaps it
    assert worker.status()["jobs"][0]["stage"] == "done"


def test_a_save_queues_its_clip_and_a_request_for_it_marks_it_in_use(tmp_path):
    from test_replay_service import attempt, make_service

    class Recording:
        def __init__(self):
            self.queued, self.touched = [], []

        def enqueue(self, path, **named):
            self.queued.append(path)
            self.named = named

        def touch(self, path):
            self.touched.append(path)

    svc = make_service(tmp_path, [attempt()])
    svc.compressor = Recording()

    path = svc.save(42)["path"]
    served = svc.saved_clip_path(42)

    assert [str(p) for p in svc.compressor.queued] == [path]
    # The progress list names the replay the way its filename does.
    assert svc.compressor.named["attempt_id"] == 42
    assert set(svc.compressor.named) == {"attempt_id", "label", "time_text"}
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
