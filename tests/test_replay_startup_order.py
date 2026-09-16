"""Prevent paired GPU demand from overlapping cold audio initialization.

No devices, GPU runtime, OS recorder lease, server or encoder are opened.
The actual recorder, paired GpuSink and PCM handoff execute; only source start
and the audio device boundary are inert. Tests synchronize with events, not
machine-speed assumptions about how long an audio import takes.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import logging
import threading
import time

import numpy as np
import pytest

from sm64_events.memory.layout import US
from sm64_events.replay.clock import CaptureClock
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.gpucapture import GpuCapture
from sm64_events.replay.recorder import ReplayRecorder
from sm64_events.replay.window import WindowInfo

WIN = WindowInfo(hwnd=123, title="isolated audio-order fixture", pid=42, visible=True)
T0 = datetime(2026, 9, 12, tzinfo=timezone.utc)
PCM = np.array([[123, -456], [789, -1234]], dtype=np.int16)


class Audio:
    mode = "process"

    def __init__(self, calls, *, blocking=False, fails=False, mode="process"):
        self.calls, self.blocking, self.fails, self.mode = calls, blocking, fails, mode
        self.entered, self.release = threading.Event(), threading.Event()
        self.on_pcm = None
        self.stopped = False

    def start(self, on_pcm):
        self.calls.append(f"audio-{self.mode}-start")
        self.on_pcm = on_pcm
        on_pcm(PCM)  # Real recorder callback -> real paired sink before media origin.
        self.entered.set()
        if self.blocking and not self.release.wait(3):
            raise AssertionError("test did not release its owned audio barrier")
        self.calls.append(f"audio-{self.mode}-finished")
        if self.fails:
            raise RuntimeError("fixture audio unavailable")

    def stop(self):
        self.stopped = True
        self.calls.append(f"audio-{self.mode}-stop")


class PairedVideo(GpuCapture):
    """Real sink/audio ownership; the demand/graphics start boundary is inert."""

    def __init__(self, calls):
        super().__init__(WIN.pid, US, nominal_rate=30)
        self.calls = calls
        self.started = threading.Event()
        self.revoked = threading.Event()
        self.demand_at_start = None
        self.starts = 0
        self.stopped = False

    def start(self, on_frame, on_stopped):
        self.calls.append("video-start")
        self.starts += 1
        self.demand_at_start = self.want_capture()
        # This inert start stands in for the worker reaching its media run.
        self._status = {**self._status,
                        "state": "recording" if self.demand_at_start else "paused"}
        self.started.set()

    def request_stop(self):
        self.revoked.set()
        super().request_stop()

    def stop(self):
        self.stopped = True
        self.calls.append("video-stop")
        super().stop()

    def finish(self):
        self.calls.append("sink-stop")
        self.end_audio(self._pcm)
        super().finish()
        self._status = {**self._status, "state": "stopped"}


class HeldLock:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def make(tmp_path, *, blocking=False, fails=False, fallback=None, raw=False):
    calls = []
    video = PairedVideo(calls)
    audio = Audio(calls, blocking=blocking, fails=fails)
    held = HeldLock()
    clock = CaptureClock(anchor_qpc_100ns=0, anchor_utc=T0)
    cfg = ReplayConfig(scratch_dir=tmp_path / "buffer", picture_feed=True)
    fallback_audio = Audio(calls, fails=fallback == "fail", mode="system")
    fallback_calls = []

    def fallback_factory(pid):
        fallback_calls.append(pid)
        return fallback_audio

    def raw_factory(*args):
        pytest.fail("paired source started the raw encoder")

    if raw:
        class RawSink:
            def start(self):
                calls.append("raw-sink-start")

            def submit_audio(self, data):
                calls.append(("raw-pcm", data))

            def stop(self):
                calls.append("raw-sink-stop")

        class RawVideo:
            starts = 0

            def set_idle_check(self, check):
                self.idle_check = check

            def start(self, on_frame, on_stopped):
                self.starts += 1
                calls.append("video-start")

            def stop(self):
                calls.append("video-stop")

        video = RawVideo()
        raw_factory = lambda *args: RawSink()

    rec = ReplayRecorder(cfg, lambda title: WIN, lambda win: video,
                         lambda pid: audio,
                         fallback_audio_factory=fallback_factory if fallback else None,
                         clock_factory=lambda: clock, codec="h264_nvenc",
                         recorder_lock_factory=lambda: held,
                         video_sink_factory=raw_factory)
    return rec, video, audio, fallback_audio, held, calls, fallback_calls, clock


@contextmanager
def starting(rec, audio):
    errors = []

    def begin():
        try:
            rec._begin_capture(WIN)
        except Exception as exc:
            logging.getLogger(__name__).exception("Owned startup fixture failed")
            errors.append(exc)

    thread = threading.Thread(target=begin, name="r32-owned-audio-start", daemon=True)
    thread.start()
    try:
        assert audio.entered.wait(3)
        yield thread, errors
    finally:
        audio.release.set()
        thread.join(3)
        assert not thread.is_alive(), "owned startup thread survived"
        rec.stop(cleanup=False)


def test_paired_demand_waits_for_cold_audio_and_keeps_clock(tmp_path, caplog):
    rec, video, audio, _, held, calls, _, clock = make(tmp_path, blocking=True)
    with caplog.at_level(logging.INFO, logger="sm64.replay"):
        with starting(rec, audio) as (thread, errors):
            assert video.starts == 0, "GPU demand overlapped blocked audio initialization"
            assert video._pcm is None  # Early audio callback was discarded.
            assert video.clock is clock and rec._clock is clock
            assert clock.anchor_utc == T0
            audio.release.set()
            thread.join(3)
            assert not thread.is_alive() and not errors
            assert video.starts == 1 and video.demand_at_start
            assert calls.index("audio-process-finished") < calls.index("video-start")
            assert rec.status()["audio_mode"] == "process"
            assert rec.status()["recording"]
            # Open the real run handoff only now; early PCM must not be replayed.
            handoff = video.begin_audio()
            assert handoff.bytes == 0 and handoff.take(time.monotonic()) is None
            before = time.time()
            audio.on_pcm(PCM)
            after = time.time()
            arrival = handoff.take(time.monotonic())
            assert arrival.data == PCM.tobytes() and before <= arrival.ends_at <= after
        finished = [r for r in caplog.records
                    if r.msg.startswith("audio initialization finished:")]
        assert len(finished) == 1
        assert finished[0].args[0:2] == (WIN.pid, "process")
        assert finished[0].args[2] >= 0
    assert audio.stopped and video.stopped and held.closed
    assert calls.index("audio-process-stop") < calls.index("sink-stop")


@pytest.mark.parametrize("fails", [False, True])
def test_stop_during_cold_audio_never_starts_gpu_or_later_fallback(tmp_path, fails):
    rec, video, audio, fallback_audio, held, calls, fallbacks, _ = make(
        tmp_path, blocking=True, fails=fails, fallback="ok")
    with starting(rec, audio) as (thread, errors):
        stopper = threading.Thread(target=lambda: rec.stop(cleanup=False),
                                   name="r32-owned-stop", daemon=True)
        stopper.start()
        try:
            assert video.revoked.wait(3)
            assert video.starts == 0
        finally:
            audio.release.set()
            thread.join(3)
            stopper.join(3)
        assert not thread.is_alive() and not stopper.is_alive() and not errors
        assert video.starts == 0 and not rec.status()["recording"]
        assert not fallbacks and not fallback_audio.stopped
    assert audio.stopped and video.stopped and held.closed
    assert "sink-stop" in calls and rec._capture_closed


@pytest.mark.parametrize("fails,fallback,mode", [
    (False, None, "process"), (True, "ok", "system"),
    (True, "fail", "none"), (True, None, "none"),
])
def test_paired_audio_outcomes_still_start_video_once(tmp_path, fails, fallback, mode):
    rec, video, audio, fallback_audio, held, calls, _, _ = make(
        tmp_path, fails=fails, fallback=fallback)
    try:
        rec._begin_capture(WIN)
        assert video.starts == 1 and video.demand_at_start
        assert rec.status()["audio_mode"] == mode and rec.status()["recording"]
        finished = [i for i, call in enumerate(calls)
                    if isinstance(call, str) and call.endswith("-finished")]
        assert max(finished) < calls.index("video-start")
    finally:
        rec.stop(cleanup=False)
    assert audio.stopped and held.closed
    if fallback:
        assert fallback_audio.stopped


@pytest.mark.parametrize("initial,during,stale_idle", [
    (False, (True,), False), (True, (False,), False),
    (False, (True, False), False), (True, (), True),
])
def test_pause_state_is_reconciled_before_paired_demand(
    tmp_path, initial, during, stale_idle
):
    rec, video, audio, _, _, _, _, _ = make(tmp_path, blocking=True)
    rec.set_session_paused(initial)
    if stale_idle:
        rec._teardown_capture()  # Actual reconnect teardown retains manual pause.
        assert rec._session_paused and not rec.is_idle()
    with starting(rec, audio) as (thread, errors):
        for paused in during:
            rec.set_session_paused(paused)
        assert video.starts == 0
        expected = not rec._session_paused
        audio.release.set()
        thread.join(3)
        assert not thread.is_alive() and not errors
        assert video.starts == 1 and video.demand_at_start == expected
        assert video.want_capture() == expected


def test_raw_sink_keeps_video_before_cold_audio_order(tmp_path):
    rec, video, audio, _, held, calls, _, _ = make(tmp_path, blocking=True, raw=True)
    with starting(rec, audio) as (thread, errors):
        assert video.starts == 1
        assert calls.index("video-start") < calls.index("audio-process-start")
        assert ("raw-pcm", PCM.tobytes()) in calls
        audio.release.set()
        thread.join(3)
        assert not thread.is_alive() and not errors
    assert held.closed and audio.stopped
    assert calls.index("audio-process-stop") < calls.index("raw-sink-stop")
