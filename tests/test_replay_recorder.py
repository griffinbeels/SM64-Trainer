import time
from datetime import datetime, timedelta, timezone

import numpy as np

from sm64_events.replay.clock import CaptureClock
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.recorder import ReplayRecorder
from sm64_events.replay.ring import SegmentInfo
from sm64_events.replay.window import WindowInfo

T0 = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
WIN = WindowInfo(hwnd=123, title="Project64 Version 1.6", pid=42, visible=True)


class FakeVideoSource:
    def __init__(self):
        self.on_frame = None
        self.stopped = False
        self.idle_check = None      # captured from set_idle_check
    def set_idle_check(self, fn):
        self.idle_check = fn
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


class _FakeLock:
    """Stand-in for the machine-wide recorder lock handle."""
    def __init__(self):
        self.closed = False
    def close(self):
        self.closed = True


def make_recorder(tmp_path, video, audio, found=WIN, fallback=None,
                  recorder_lock_factory=None):
    cfg = ReplayConfig(scratch_dir=tmp_path / "buf", attach_poll_s=0.01, fps=30)
    return ReplayRecorder(
        cfg=cfg,
        window_finder=lambda title: found,
        video_factory=lambda win: video,
        audio_factory=lambda pid: audio,
        fallback_audio_factory=(lambda rate: fallback) if fallback else None,
        clock_factory=lambda: CaptureClock(anchor_qpc_100ns=0, anchor_utc=T0),
        codec="libx264",
        # default: always-acquire fake so capture tests are deterministic and
        # never touch the real lock; override per-test to simulate contention.
        recorder_lock_factory=recorder_lock_factory or (lambda: _FakeLock()))


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


def test_viewer_only_when_another_instance_holds_recorder_lock(tmp_path):
    """Single-recorder guard: if the machine-wide lock can't be acquired
    (another instance is recording), this one finds the window but starts NO
    capture — preventing the redundant double-capture that lagged the machine."""
    video, audio = FakeVideoSource(), FakeAudioSource()
    rec = make_recorder(tmp_path, video, audio,
                        recorder_lock_factory=lambda: None)  # lock unavailable
    rec.start()
    assert wait_for(lambda: rec.status()["window_found"] is True)
    time.sleep(0.1)                              # several attach cycles
    assert video.on_frame is None                # capture never started
    assert rec.status()["recording"] is False
    rec.stop()


def test_recorder_releases_lock_on_teardown(tmp_path):
    """The held lock is released on teardown so another instance can take
    over recording."""
    video, audio = FakeVideoSource(), FakeAudioSource()
    held = _FakeLock()
    rec = make_recorder(tmp_path, video, audio, recorder_lock_factory=lambda: held)
    rec.start()
    assert wait_for(lambda: video.on_frame is not None)   # captured -> lock taken
    rec.stop()
    assert held.closed is True                            # released on teardown


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


def _seg(tmp_path, name, start, end):
    p = tmp_path / name
    p.write_bytes(b"x" * 10)
    return SegmentInfo(path=p, kind="video", utc_start=start, utc_end=end,
                       size_bytes=10)


def test_idle_gating_discards_segments_keeps_straddlers(tmp_path):
    """The discard contract (live-reported bug 2026-06-12: pausing the sink
    left a hole at the clip start). While idle, only segments born ENTIRELY
    inside the idle window are dropped; straddlers carry the last active
    footage / the anchor lead-up and must be kept. Resume is instant."""
    cfg = ReplayConfig(scratch_dir=tmp_path / "buf")
    rec = ReplayRecorder(cfg=cfg, window_finder=lambda t: None,
                         video_factory=None, audio_factory=None)
    assert rec.idle_after_s == 5.0          # default pads 3+2
    rec.set_idle_after(1.0)
    assert rec.idle_after_s == 3.0          # floor prevents thrash

    rec._recording = True
    rec.idle_after_s = 0.05                 # fast for the test
    now = datetime.now(timezone.utc)

    rec._maybe_idle_pause()                 # input is recent -> stays active
    assert rec.status()["idle"] is False
    rec._on_segment(_seg(tmp_path, "a.ts", now - timedelta(seconds=4),
                         now - timedelta(seconds=2)))
    assert rec.ring.total_bytes == 10       # active: retained

    rec._last_player_active = time.monotonic() - 1.0
    rec._maybe_idle_pause()
    assert rec.status()["idle"] is True
    since = rec._idle_since
    straddler = _seg(tmp_path, "b.ts", since - timedelta(seconds=1),
                     since + timedelta(seconds=1))
    rec._on_segment(straddler)
    assert rec.ring.total_bytes == 20       # born before idle: kept
    inside = _seg(tmp_path, "c.ts", since + timedelta(seconds=1),
                  since + timedelta(seconds=3))
    rec._on_segment(inside)
    assert rec.ring.total_bytes == 20       # born inside idle: dropped
    assert not inside.path.exists()         # disk freed, not just unlisted

    rec.set_player_active()                 # first input -> instant resume
    assert rec.status()["idle"] is False
    rec._on_segment(_seg(tmp_path, "d.ts", now + timedelta(seconds=5),
                         now + timedelta(seconds=7)))
    assert rec.ring.total_bytes == 30       # post-resume: retained again


def test_recorder_injects_idle_check_tracking_idle_state(tmp_path):
    """The capture source throttles its grab rate while idle: the recorder
    must hand it a live 'am I idle?' callback that flips with the idle gate
    (AFK auto-idle AND manual pause both count)."""
    video, audio = FakeVideoSource(), FakeAudioSource()
    rec = make_recorder(tmp_path, video, audio)
    rec.start()
    assert wait_for(lambda: video.idle_check is not None)
    assert video.idle_check() is False           # fresh capture: active
    rec.set_session_paused(True)
    assert video.idle_check() is True             # pause -> source trickles
    rec.set_session_paused(False)
    assert video.idle_check() is False            # resume -> full rate
    rec.stop()


def test_session_pause_forces_idle_and_outranks_input(tmp_path):
    """Manual pause (POST /api/pause): forces the idle-discard state, and
    stray input pings must NOT resume it; unpausing resumes immediately
    and refreshes the activity clock so auto-idle doesn't re-trigger."""
    cfg = ReplayConfig(scratch_dir=tmp_path / "buf")
    rec = ReplayRecorder(cfg=cfg, window_finder=lambda t: None,
                         video_factory=None, audio_factory=None)
    rec._recording = True

    rec.set_session_paused(True)
    assert rec.status()["idle"] is True
    rec.set_player_active()                 # input must not resume a pause
    assert rec.status()["idle"] is True

    rec.set_session_paused(False)
    assert rec.status()["idle"] is False
    rec._maybe_idle_pause()                 # clock refreshed on unpause
    assert rec.status()["idle"] is False
