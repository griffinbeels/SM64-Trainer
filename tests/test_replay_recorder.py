import time
from datetime import datetime, timezone

import numpy as np

from sm64_events.replay.clock import CaptureClock
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.recorder import ReplayRecorder
from sm64_events.replay.window import WindowInfo

T0 = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
WIN = WindowInfo(hwnd=123, title="Project64 Version 1.6", pid=42, visible=True)


class FakeVideoSource:
    def __init__(self):
        self.on_frame = None
        self.stopped = False
    def start(self, on_frame, on_stopped):
        self.on_frame = on_frame
    def stop(self):
        self.stopped = True


class FakeAudioSource:
    mode = "process"
    def __init__(self):
        self.on_pcm = None
    def start(self, on_pcm):
        self.on_pcm = on_pcm
    def stop(self):
        pass


class FailingAudioSource:
    mode = "process"
    def start(self, on_pcm):
        raise RuntimeError("proc-tap unavailable")
    def stop(self):
        pass


class SystemFakeAudioSource(FakeAudioSource):
    mode = "system"


def make_recorder(tmp_path, video, audio, found=WIN, fallback=None):
    cfg = ReplayConfig(scratch_dir=tmp_path / "buf", attach_poll_s=0.01, fps=30)
    return ReplayRecorder(
        cfg=cfg,
        window_finder=lambda title: found,
        video_factory=lambda win: video,
        audio_factory=lambda pid: audio,
        fallback_audio_factory=(lambda rate: fallback) if fallback else None,
        clock_factory=lambda: CaptureClock(anchor_qpc_100ns=0, anchor_utc=T0),
        codec="libx264")


def push_frames(video, n, start_index=0, fps=30):
    arr = np.zeros((480, 640, 4), dtype=np.uint8)
    for i in range(start_index, start_index + n):
        video.on_frame(arr, int(i / fps * 1e7))  # qpc 100ns ticks for frame i


def wait_for(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_recorder_attaches_and_produces_segments(tmp_path):
    video, audio = FakeVideoSource(), FakeAudioSource()
    rec = make_recorder(tmp_path, video, audio)
    rec.start()
    assert wait_for(lambda: video.on_frame is not None)
    push_frames(video, 70)                      # > one 2 s segment
    audio.on_pcm(np.zeros((48000, 2), dtype=np.int16))
    assert wait_for(lambda: rec.ring.coverage("video") is not None)
    rec.stop()
    st = rec.status()
    assert st["recording"] is False and st["window_found"] is True
    assert st["audio_mode"] == "process"
    assert st["encoder"] == "libx264"
    cov = rec.ring.coverage("video")
    assert cov[0] == T0
    assert video.stopped is True


def test_cfr_fill_duplicates_dropped_frames(tmp_path):
    video, audio = FakeVideoSource(), FakeAudioSource()
    rec = make_recorder(tmp_path, video, audio)
    rec.start()
    assert wait_for(lambda: video.on_frame is not None)
    push_frames(video, 10)                       # indices 0..9
    push_frames(video, 80, start_index=40)       # delivery gap: 10..39 filled
    rec.stop()                                   # stop() closes partials
    cov = rec.ring.coverage("video")
    # 120 contiguous indices = 4 s despite the 1 s delivery gap
    assert (cov[1] - cov[0]).total_seconds() == 4.0


def test_audio_start_failure_falls_back_to_system(tmp_path):
    video = FakeVideoSource()
    sysaudio = SystemFakeAudioSource()
    rec = make_recorder(tmp_path, video, FailingAudioSource(), fallback=sysaudio)
    rec.start()
    assert wait_for(lambda: video.on_frame is not None)
    assert wait_for(lambda: rec.status()["audio_mode"] == "system")
    rec.stop()


def test_audio_total_failure_records_video_only(tmp_path):
    video = FakeVideoSource()
    rec = make_recorder(tmp_path, video, FailingAudioSource(), fallback=None)
    rec.start()
    assert wait_for(lambda: video.on_frame is not None)
    assert rec.status()["audio_mode"] == "none"
    push_frames(video, 70)
    assert wait_for(lambda: rec.ring.coverage("video") is not None)
    rec.stop()


def test_no_window_reports_not_recording(tmp_path):
    rec = make_recorder(tmp_path, FakeVideoSource(), FakeAudioSource(), found=None)
    rec.start()
    time.sleep(0.05)
    st = rec.status()
    assert st["recording"] is False and st["window_found"] is False
    rec.stop()


def test_startup_wipes_scratch(tmp_path):
    buf = tmp_path / "buf"
    buf.mkdir(parents=True)
    (buf / "stale.ts").write_bytes(b"junk")
    rec = make_recorder(tmp_path, FakeVideoSource(), FakeAudioSource(), found=None)
    rec.start()
    assert not (buf / "stale.ts").exists()
    rec.stop()


def test_begin_capture_failure_still_stops_video_source(tmp_path):
    """C1: if something raises after video.start() but before _begin_capture
    completes (e.g. fallback factory constructor blows up), teardown must still
    be able to reach the already-running video source and stop it.
    Before the fix _video_source was only assigned at the END of the function,
    so an exception in between left the WGC session running forever."""
    video = FakeVideoSource()

    class ExplodingFallbackFactory:
        def __call__(self, rate):
            raise RuntimeError("fallback factory exploded")

    cfg = ReplayConfig(scratch_dir=tmp_path / "buf2", attach_poll_s=0.01)
    rec = ReplayRecorder(
        cfg=cfg,
        window_finder=lambda title: WIN,
        video_factory=lambda win: video,
        audio_factory=lambda pid: FailingAudioSource(),
        fallback_audio_factory=ExplodingFallbackFactory(),
        clock_factory=lambda: CaptureClock(anchor_qpc_100ns=0, anchor_utc=T0),
        codec="libx264")
    rec.start()
    assert wait_for(lambda: video.on_frame is not None)
    rec.stop()
    assert video.stopped is True      # the leak: before the fix this stayed False


def test_long_gap_becomes_coverage_hole_not_giant_fill(tmp_path):
    """I2: a ~10-minute delivery gap must NOT cause ~18000 fill encodes.
    The recorder caps fill at one segment's worth; beyond that it hands the
    writer the real target index and gap-rotation converts the silence into an
    honest coverage hole.  Total buffer size stays tiny; two coverage islands
    are written instead of one giant frozen-video block."""
    video, audio = FakeVideoSource(), FakeAudioSource()
    rec = make_recorder(tmp_path, video, audio)
    rec.start()
    assert wait_for(lambda: video.on_frame is not None)
    push_frames(video, 60)                          # indices 0..59 (2 s)
    push_frames(video, 60, start_index=18060)       # ~10 min later (index 18060)
    rec.stop()
    cov = rec.ring.coverage("video")
    # coverage span reflects true wall-clock of the late frames
    assert cov is not None
    assert (cov[1] - cov[0]).total_seconds() > 600
    # but total encoded footage is tiny — NOT 10 minutes of duplicates
    assert rec.ring.total_bytes < 5 * 1024 * 1024
    segs = rec.ring.covering("video", T0, cov[1])
    assert len(segs) == 2


def test_restart_after_stop_records_again(tmp_path):
    """R1 regression: stop() sets _stopping; start() must reset it or the
    recorder is silently dead on restart (recording never goes True)."""
    video, audio = FakeVideoSource(), FakeAudioSource()
    rec = make_recorder(tmp_path, video, audio)
    rec.start()
    assert wait_for(lambda: rec.status()["recording"])
    rec.stop()
    assert rec.status()["recording"] is False
    rec.start()
    assert wait_for(lambda: rec.status()["recording"])
    rec.stop()


def test_startup_wipe_is_recursive_clips_cache_dies_with_buffer(tmp_path):
    """Final-review fix: a file-only wipe left clips/ alive, so view() served
    stale clips against an empty ring after a restart."""
    buf = tmp_path / "buf"
    clips = buf / "clips"
    clips.mkdir(parents=True)
    (buf / "stale.ts").write_bytes(b"junk")
    (clips / "clip_attempt_1.mp4").write_bytes(b"stale clip")
    (clips / "clip_attempt_1.json").write_text("{}")
    rec = make_recorder(tmp_path, FakeVideoSource(), FakeAudioSource(), found=None)
    rec.start()
    assert not (buf / "stale.ts").exists()
    assert not (clips / "clip_attempt_1.mp4").exists()
    assert not (clips / "clip_attempt_1.json").exists()
    rec.stop()
