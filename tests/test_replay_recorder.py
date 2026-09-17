import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


class FakeAvSink:
    """Stand-in for FfmpegAvSink: records submit()/submit_audio() calls."""
    def __init__(self):
        self.frames = []
        self.audio = []
        self.started = False
        self.stopped = False
    def start(self):
        self.started = True
    def stop(self):
        self.stopped = True
    def submit(self, bgra, tag=None):
        self.frames.append(bgra)
        self.tags = getattr(self, "tags", [])
        self.tags.append(tag)
    def submit_audio(self, pcm_bytes):
        self.audio.append(pcm_bytes)


def make_recorder(tmp_path, video, audio, found=WIN, fallback=None,
                  recorder_lock_factory=None, video_sink_factory=None,
                  fallback_factory=None):
    cfg = ReplayConfig(scratch_dir=tmp_path / "buf", attach_poll_s=0.01, fps=30)
    return ReplayRecorder(
        cfg=cfg,
        window_finder=lambda title: found,
        video_factory=lambda win: video,
        audio_factory=lambda pid: audio,
        fallback_audio_factory=(
            fallback_factory or ((lambda pid: fallback) if fallback else None)),
        clock_factory=lambda: CaptureClock(anchor_qpc_100ns=0, anchor_utc=T0),
        codec="libx264",
        video_sink_factory=video_sink_factory,
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


def test_av_sink_receives_both_video_and_audio(tmp_path):
    """Single-mux architecture: when an AV sink is present, BOTH streams route
    into it (video via submit, audio via submit_audio as raw s16le bytes) and
    NO in-process SegmentWriter is created — ffmpeg owns muxing + sync."""
    video, audio = FakeVideoSource(), SystemFakeAudioSource()
    sink = FakeAvSink()
    seen = {}

    def sink_factory(cfg, on_seg, codec):
        seen["codec"] = codec
        return sink

    rec = make_recorder(tmp_path, video, audio,
                        video_sink_factory=sink_factory)
    rec.start()
    assert wait_for(lambda: video.on_frame is not None and audio.on_pcm is not None)
    assert sink.started is True
    push_frames(video, 1)
    pcm = np.zeros((1600, 2), dtype=np.int16)
    pcm[:, 0] = 1234
    audio.on_pcm(pcm)
    assert wait_for(lambda: len(sink.frames) >= 1 and len(sink.audio) >= 1)
    assert sink.audio[0] == pcm.tobytes()        # raw interleaved s16le
    assert rec._writer is None                    # no PCM-sidecar writer
    # The sink gets the machine's PICKED codec — hardcoded nvenc in the sink
    # is the flashing-mouse bug (2026-08-07).
    assert seen["codec"] == "libx264"
    rec.stop()
    assert sink.stopped is True


def test_idle_discard_defers_a_busy_file_instead_of_erroring(tmp_path, caplog):
    """A Windows sharing violation on an idle-discard unlink (ffmpeg's segment
    close, a clip cut or an indexer briefly holds the file) is a deferral, not
    an ERROR traceback — the traceback itself was reported as a bug
    (2026-08-07). The attach loop retries and deletes it once released."""
    import logging

    video, audio = FakeVideoSource(), FakeAudioSource()
    rec = make_recorder(tmp_path, video, audio,
                        video_sink_factory=lambda *args: FakeAvSink())
    rec._begin_capture(WIN)
    scratch = tmp_path / "buf"
    scratch.mkdir(parents=True, exist_ok=True)
    seg_path = scratch / "av_00_000001.ts"
    seg_path.write_bytes(b"x")
    rec._idle_since = T0                          # idle before the segment
    seg = SegmentInfo(path=seg_path, kind="video",
                      utc_start=T0 + timedelta(seconds=1),
                      utc_end=T0 + timedelta(seconds=3), size_bytes=1)
    with caplog.at_level(logging.DEBUG, logger="sm64.replay"):
        with open(seg_path, "rb"):                # an open handle denies delete
            rec._on_segment(seg)
            rec.ring.maintain()                  # still held — stays queued
            assert seg_path.exists()
            assert not [r for r in caplog.records
                        if r.levelno >= logging.ERROR], \
                "a busy file must not produce an ERROR"
        rec.ring.maintain()                      # holder gone — cleaned up
    assert not seg_path.exists()
    rec.stop()


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
    rec.stop(cleanup=False)  # inspect the final closed media before session cleanup
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
    rec.stop(cleanup=False)                       # inspect final closed partials
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


def test_fallback_audio_source_is_handed_the_window_pid(tmp_path):
    """The fallback needs the pid exactly as much as the primary does — it
    targets the endpoint hosting THAT app's session. It was handed the sample
    RATE until 2026-07-31, and both are ints, so nothing ever complained."""
    seen = []
    sysaudio = SystemFakeAudioSource()

    def fallback_factory(pid):
        seen.append(pid)
        return sysaudio

    rec = make_recorder(tmp_path, FakeVideoSource(), FailingAudioSource(),
                        fallback_factory=fallback_factory)
    rec.start()
    assert wait_for(lambda: seen)
    rec.stop()
    assert seen == [WIN.pid]


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
    rec = make_recorder(tmp_path, FakeVideoSource(), FakeAudioSource(),
                        video_sink_factory=lambda *args: FakeAvSink())
    rec.start()
    assert wait_for(lambda: rec.status()["recording"])
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
    rec.stop(cleanup=False)
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
    rec = make_recorder(tmp_path, FakeVideoSource(), FakeAudioSource(),
                        video_sink_factory=lambda *args: FakeAvSink())
    rec.start()
    assert wait_for(lambda: rec.status()["recording"])
    assert not (buf / "stale.ts").exists()
    assert not (clips / "clip_attempt_1.mp4").exists()
    assert not (clips / "clip_attempt_1.json").exists()
    rec.stop()


def _seg(tmp_path, name, start, end):
    p = tmp_path / "buf" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * 10)
    return SegmentInfo(path=p, kind="video", utc_start=start, utc_end=end,
                       size_bytes=10)


def test_idle_gating_discards_segments_keeps_straddlers(tmp_path):
    """The discard contract (live-reported bug 2026-06-12: pausing the sink
    left a hole at the clip start). While idle, only segments born ENTIRELY
    inside the idle window are dropped; straddlers carry the last active
    footage / the anchor lead-up and must be kept. Resume is instant."""
    rec = make_recorder(tmp_path, FakeVideoSource(), FakeAudioSource(),
                        video_sink_factory=lambda *args: FakeAvSink())
    rec._begin_capture(WIN)
    assert rec.idle_after_s == 5.0          # default pads 3+2
    overhead = rec.ring.total_bytes        # ownership marker is accounted too
    rec.set_idle_after(1.0)
    assert rec.idle_after_s == 3.0          # floor prevents thrash

    rec._recording = True
    rec.idle_after_s = 0.05                 # fast for the test
    now = datetime.now(timezone.utc)

    rec._maybe_idle_pause()                 # input is recent -> stays active
    assert rec.status()["idle"] is False
    rec._on_segment(_seg(tmp_path, "a.ts", now - timedelta(seconds=4),
                         now - timedelta(seconds=2)))
    assert rec.ring.total_bytes == overhead + 10  # active: retained

    rec._last_player_active = time.monotonic() - 1.0
    rec._maybe_idle_pause()
    assert rec.status()["idle"] is True
    since = rec._idle_since
    straddler = _seg(tmp_path, "b.ts", since - timedelta(seconds=1),
                     since + timedelta(seconds=1))
    rec._on_segment(straddler)
    assert rec.ring.total_bytes == overhead + 20  # born before idle: kept
    inside = _seg(tmp_path, "c.ts", since + timedelta(seconds=1),
                  since + timedelta(seconds=3))
    rec._on_segment(inside)
    assert rec.ring.total_bytes == overhead + 20  # born inside idle: dropped
    assert not inside.path.exists()         # disk freed, not just unlisted

    rec.set_player_active()                 # first input -> instant resume
    assert rec.status()["idle"] is False
    rec._on_segment(_seg(tmp_path, "d.ts", now + timedelta(seconds=5),
                         now + timedelta(seconds=7)))
    assert rec.ring.total_bytes == overhead + 30  # post-resume: retained again
    rec.stop()


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


def test_failed_source_demand_does_not_leave_idle_half_changed(tmp_path, caplog):
    video = FakeVideoSource()
    def unavailable():
        raise OSError("frame mapping closed")
    video.refresh_demand = unavailable
    rec = make_recorder(tmp_path, video, FakeAudioSource())
    rec._video_source = video
    rec._set_idle(True)
    rec._idle_dropped = 5
    rec.set_player_active()
    assert not rec.is_idle()
    assert rec._idle_since is None
    assert rec._idle_dropped == 0
    assert "replay frame demand notification failed" in caplog.text


def test_startup_reconciles_unpause_before_source_publication(tmp_path):
    class StartingPaused(FakeVideoSource):
        def start(self, on_frame, on_stopped):
            super().start(on_frame, on_stopped)
            self.demand = not self.idle_check()
            assert not self.demand
            rec.set_session_paused(False)  # not published yet: notification has no target
        def refresh_demand(self):
            self.demand = not self.idle_check()

    video = StartingPaused()
    sink = FakeAvSink()
    rec = make_recorder(tmp_path, video, FakeAudioSource(),
                        video_sink_factory=lambda *args, **kwargs: sink)
    rec.set_session_paused(True)
    try:
        rec._begin_capture(WIN)
        assert not rec.is_idle()
        assert video.demand
    finally:
        rec._teardown_capture()


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
def test_the_picture_ledger_rides_the_capture_path(tmp_path):
    """Item 40: every grab passes the picture ledger; identical grabs of one
    presented picture land ONE row, a changed picture lands the next. And
    item 38: the sink is fed ONE frame per row -- the three identical grabs
    reach it once, and every write lands in the ledger's feed log.

    A desktop grab names no game frame (the frame clock that used to guess
    one was deleted 2026-09-05), so every row is filed by time alone."""
    video, audio = FakeVideoSource(), SystemFakeAudioSource()
    sink = FakeAvSink()
    rec = make_recorder(tmp_path, video, audio,
                        video_sink_factory=lambda cfg, on_seg, codec: sink)
    rec.start()
    assert wait_for(lambda: video.on_frame is not None)
    same = np.zeros((480, 640, 4), dtype=np.uint8)
    for tick in range(3):                    # the same zeros picture, thrice
        video.on_frame(same, int(tick / 30 * 1e7))
    changed = np.full((480, 640, 4), 200, dtype=np.uint8)
    video.on_frame(changed, int(3 / 30 * 1e7))
    assert wait_for(lambda: len(getattr(sink, "tags", [])) >= 2)
    rows = rec.ledger.rows_between(0.0, 1e12)
    assert [row["frame"] for row in rows] == [None, None]
    assert rows[1]["ts"] - rows[0]["ts"] > 0
    time.sleep(0.05)
    assert len(sink.frames) == 2, "one fed frame per distinct picture"
    assert [tag[1] for tag in sink.tags] == [row["ts"] for row in rows]
    # The sink's feed callback is the ledger's feed log.
    sink.on_fed(sink.tags[0], rows[0]["ts"] + 0.004)
    assert rec.ledger.feeds_between(0.0, 1e12) == [
        {"at": rows[0]["ts"] + 0.004, "ts": rows[0]["ts"],
         "run_id": None, "pts": None, "repeat": False}]
    rec.stop()


def test_clean_stop_closes_encoder_before_removing_owned_scratch(tmp_path):
    held = _FakeLock()
    source_path = tmp_path / "buf" / "tail.ts"

    class ClosingSink(FakeAvSink):
        def stop(self):
            assert not held.closed
            source_path.write_bytes(b"final segment")
            super().stop()

    sink = ClosingSink()
    rec = make_recorder(tmp_path, FakeVideoSource(), FakeAudioSource(),
                        recorder_lock_factory=lambda: held,
                        video_sink_factory=lambda *args: sink)
    rec._begin_capture(WIN)
    saved = tmp_path / "saved.mp4"
    saved.write_bytes(b"saved footage")
    rec.stop()
    assert sink.stopped and held.closed
    assert not source_path.exists()
    assert rec.ring.total_bytes == 0
    assert saved.read_bytes() == b"saved footage"


def test_pending_save_can_defer_cleanup_and_retry_after_stop(tmp_path):
    rec = make_recorder(tmp_path, FakeVideoSource(), FakeAudioSource(),
                        video_sink_factory=lambda *args: FakeAvSink())
    rec._begin_capture(WIN)
    source = _seg(tmp_path, "pending.ts", T0, T0 + timedelta(seconds=2))
    rec.ring.add(source)
    rec.stop(cleanup=False)
    assert source.path.exists()
    assert rec.cleanup_scratch()
    assert not source.path.exists() and rec.ring.coverage("video") is None


def test_shutdown_cleanup_preserves_live_lease(tmp_path):
    rec = make_recorder(tmp_path, FakeVideoSource(), FakeAudioSource(),
                        video_sink_factory=lambda *args: FakeAvSink())
    rec._begin_capture(WIN)
    source = _seg(tmp_path, "reading.ts", T0, T0 + timedelta(seconds=2))
    rec.ring.add(source)
    archive = rec._cfg.scratch_dir / rec._ledger_name
    assert archive.exists()
    with rec.ring.pin("video", source.utc_start, source.utc_end):
        rec.stop()
        assert source.path.read_bytes() == b"x" * 10
        assert archive.exists()  # projection after ffmpeg still needs this evidence
    assert rec.cleanup_scratch()
    assert not source.path.exists() and not archive.exists()


def test_viewer_stop_does_not_modify_any_scratch_bytes(tmp_path):
    scratch = tmp_path / "buf"
    scratch.mkdir()
    paths = [scratch / ".session-owner", scratch / "active.ts"]
    for path in paths:
        path.write_bytes(b"foreign owner")
    rec = make_recorder(tmp_path, FakeVideoSource(), FakeAudioSource(),
                        recorder_lock_factory=lambda: None)
    rec._begin_capture(WIN)
    rec.stop()
    assert not rec.cleanup_scratch()
    assert all(path.read_bytes() == b"foreign owner" for path in paths)


def test_detached_old_owner_cannot_clean_new_owner_lifetime(tmp_path):
    rec = make_recorder(tmp_path, FakeVideoSource(), FakeAudioSource(),
                        video_sink_factory=lambda *args: FakeAvSink())
    rec._begin_capture(WIN)
    rec._teardown_capture()  # emulator disconnect releases recorder lock, not session
    assert rec._scratch.owns()
    rec._scratch.marker.write_text("different owner", encoding="utf-8")
    current = _seg(tmp_path, "other.ts", T0, T0 + timedelta(seconds=2))
    rec.stop()
    assert current.path.exists()
    assert rec._scratch.marker.read_text() == "different owner"


def test_disconnect_pause_and_reconnect_preserve_unsaved_session(tmp_path):
    rec = make_recorder(tmp_path, FakeVideoSource(), FakeAudioSource(),
                        video_sink_factory=lambda *args: FakeAvSink())
    rec._begin_capture(WIN)
    source = _seg(tmp_path, "retained.ts", T0, T0 + timedelta(seconds=2))
    rec.ring.add(source)
    rec.set_session_paused(True)
    rec.set_session_paused(False)
    rec._teardown_capture()
    assert source.path.exists()
    rec._begin_capture(WIN)
    assert source.path.exists() and rec.ring.coverage("video") is not None
    rec.stop()
    assert not source.path.exists()


def test_startup_and_shutdown_honor_pending_source_protection(tmp_path):
    scratch = tmp_path / "buf"
    scratch.mkdir()
    pending, ordinary = scratch / "pending.mp4", scratch / "old.ts"
    pending.write_bytes(b"explicit save source")
    ordinary.write_bytes(b"expired")
    rec = make_recorder(tmp_path, FakeVideoSource(), FakeAudioSource(),
                        video_sink_factory=lambda *args: FakeAvSink())
    rec.scratch_protection = lambda: [pending]
    rec._begin_capture(WIN)
    assert pending.exists() and not ordinary.exists()
    rec.stop()
    assert pending.read_bytes() == b"explicit save source"
    rec.scratch_protection = None
    assert rec.cleanup_scratch() and not pending.exists()


def test_failed_encoder_close_keeps_scratch_for_recovery(tmp_path):
    class FailedClose(FakeAvSink):
        def stop(self):
            raise OSError("encoder still draining")

    rec = make_recorder(tmp_path, FakeVideoSource(), FakeAudioSource(),
                        video_sink_factory=lambda *args: FailedClose())
    rec._begin_capture(WIN)
    source = _seg(tmp_path, "tail.ts", T0, T0 + timedelta(seconds=2))
    rec.stop()
    assert source.path.exists()
    assert not rec.cleanup_scratch()


def test_free_floor_stops_encoder_with_owned_session_and_resumes_when_recovered(tmp_path):
    free = [1000]
    held = _FakeLock()
    sinks = []

    def factory(*args):
        sink = FakeAvSink()
        sinks.append(sink)
        return sink

    rec = make_recorder(tmp_path, FakeVideoSource(), FakeAudioSource(),
                        video_sink_factory=factory, recorder_lock_factory=lambda: held)
    rec.ring._disk_margin = 100
    rec.ring._free_bytes_fn = lambda: free[0]
    rec.start()
    try:
        assert wait_for(lambda: rec.status()["recording"])
        free[0] = 0
        rec.ring.maintain()
        assert wait_for(lambda: sinks[0].stopped and not rec.status()["recording"])
        assert not held.closed and rec._scratch.owns()
        assert rec.status()["storage_pressure"]
        free[0] = 1000
        assert wait_for(lambda: len(sinks) == 2 and rec.status()["recording"])
        assert not rec.status()["storage_pressure"]
    finally:
        rec.stop()


def test_restart_after_low_space_stop_rechecks_disk_before_capture(tmp_path):
    free = [0]
    rec = make_recorder(tmp_path, FakeVideoSource(), FakeAudioSource(),
                        video_sink_factory=lambda *args: FakeAvSink())
    rec.ring._disk_margin = 100
    rec.ring._free_bytes_fn = lambda: free[0]
    rec.start()
    assert wait_for(lambda: rec.ring.storage_pressure)
    rec.stop()
    free[0] = 1000
    rec.start()
    try:
        assert wait_for(lambda: rec.status()["recording"])
    finally:
        rec.stop()


def test_session_rotation_preserves_http_reader_and_never_releases_owner_lock(tmp_path):
    held = _FakeLock()
    sinks = []

    def factory(*args):
        sink = FakeAvSink()
        sinks.append(sink)
        return sink

    rec = make_recorder(tmp_path, FakeVideoSource(), FakeAudioSource(),
                        recorder_lock_factory=lambda: held, video_sink_factory=factory)
    rec._begin_capture(WIN)
    source = _seg(tmp_path, "previous.ts", T0, T0 + timedelta(seconds=2))
    rec.ring.add(source)
    previous_archive = rec._cfg.scratch_dir / rec._ledger_name
    media = rec._cfg.scratch_dir / "clips" / "reading.mp4"
    media.parent.mkdir()
    media.write_bytes(b"http response bytes")
    with rec.ring.pin_temp("http"):
        rec.ring.register_temp("http", [media], source.utc_start, source.utc_end)
        assert rec.reset_session_scratch()
        assert not held.closed and sinks[0].stopped and sinks[1].started
        assert rec.status()["recording"]
        assert media.read_bytes() == b"http response bytes"
        assert not source.path.exists() and not previous_archive.exists()
        assert rec._cfg.scratch_dir.joinpath(rec._ledger_name).exists()
    assert not media.exists()
    rec.stop()


def test_session_rotation_refuses_a_viewer_or_replaced_owner(tmp_path):
    rec = make_recorder(tmp_path, FakeVideoSource(), FakeAudioSource(),
                        video_sink_factory=lambda *args: FakeAvSink())
    assert not rec.reset_session_scratch()
    rec._begin_capture(WIN)
    rec._teardown_capture()
    rec._scratch.marker.write_text("replacement", encoding="utf-8")
    media = _seg(tmp_path, "replacement.ts", T0, T0 + timedelta(seconds=2))
    assert not rec.reset_session_scratch()
    assert media.path.exists() and rec._scratch.marker.read_text() == "replacement"


def test_detached_lease_release_cannot_delete_replacement_owners_media(tmp_path):
    rec = make_recorder(tmp_path, FakeVideoSource(), FakeAudioSource(),
                        video_sink_factory=lambda *args: FakeAvSink())
    rec._begin_capture(WIN)
    path = rec._cfg.scratch_dir / "clip.mp4"
    path.write_bytes(b"previous owner")
    with rec.ring.pin_temp("http"):
        rec.ring.register_temp("http", [path], T0, T0 + timedelta(seconds=2))
        rec.ring.forget_temp("http", delete=True)
        rec._teardown_capture()
        rec._scratch.marker.write_text("replacement", encoding="utf-8")
        path.write_bytes(b"replacement owner's bytes")
    rec.ring.set_limits(None, 0)
    rec.ring.maintain()
    rec.stop()
    assert path.read_bytes() == b"replacement owner's bytes"


def test_pending_unlink_cannot_resume_after_machine_ownership_released(tmp_path, monkeypatch):
    rec = make_recorder(tmp_path, FakeVideoSource(), FakeAudioSource(),
                        video_sink_factory=lambda *args: FakeAvSink())
    rec._begin_capture(WIN)
    source = _seg(tmp_path, "busy.ts", T0, T0 + timedelta(seconds=2))
    unlink = Path.unlink

    def busy(path, *args, **kwargs):
        if path == source.path:
            raise PermissionError("sharing violation")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", busy)
    rec.ring.discard(source)
    assert source.path.exists()
    rec._teardown_capture()
    monkeypatch.setattr(Path, "unlink", unlink)
    # Even before a replacement publishes its token, the old instance no
    # longer owns the machine lock and must perform no deletions.
    source.path.write_bytes(b"new owner preparing scratch")
    rec.ring.maintain()
    assert source.path.read_bytes() == b"new owner preparing scratch"


def test_idle_arriving_tail_is_retained_until_source_lease_finishes(tmp_path):
    rec = make_recorder(tmp_path, FakeVideoSource(), FakeAudioSource(),
                        video_sink_factory=lambda *args: FakeAvSink())
    rec._begin_capture(WIN)
    rec._idle_since = T0
    with rec.ring.pin("video", T0, T0 + timedelta(seconds=2)):
        tail = _seg(tmp_path, "tail.ts", T0, T0 + timedelta(seconds=2))
        rec._on_segment(tail)
        assert tail.path.read_bytes() == b"x" * 10
        assert rec.ring.covering("video", T0, tail.utc_end) == [tail]
    assert not tail.path.exists()
    rec.stop()


def test_capture_waits_for_a_practice_rom_and_stops_when_one_leaves(tmp_path):
    """His ruling, 2026-09-16: a real run on another ROM records nothing --
    no video source, no audio. main.py gates on the poller's practice ROM."""
    video, audio = FakeVideoSource(), FakeAudioSource()
    rec = make_recorder(tmp_path, video, audio)
    practice = [False]
    rec.set_capture_gate(lambda: practice[0])
    rec.start()
    try:
        time.sleep(0.1)
        assert video.on_frame is None and rec.status()["capture_gated"] is True
        assert rec.status()["recording"] is False
        practice[0] = True
        assert wait_for(lambda: video.on_frame is not None)
        assert wait_for(lambda: rec.status()["capture_gated"] is False)
        practice[0] = False
        assert wait_for(lambda: video.stopped)
        assert wait_for(lambda: rec.status()["recording"] is False)
    finally:
        rec.stop()
