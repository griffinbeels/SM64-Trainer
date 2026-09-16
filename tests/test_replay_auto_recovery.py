"""Recover from injected downstream failures without live capture or devices.

Real recorder/pump/lease-owner threads; scratch is pytest-owned. Factories replace
only OS, audio device, and encoder boundaries. No server or Project64 is opened.
"""
from dataclasses import replace
import threading
import time
from types import SimpleNamespace as NS

import pytest

from sm64_events.memory.layout import US
from sm64_events.replay._system_audio import AudioPump
from sm64_events.replay.audio import DeafStreamWatchdog, ProcessAudioSource, SystemAudioSource
from sm64_events.replay.gpucapture import GpuCapture, GpuCleanupError
from sm64_events.replay.gpudemand import DemandSnapshot
from sm64_events.replay.gpuretry import RetryGate
from test_gpu_retry import producer
from test_replay_recorder import FakeAudioSource, FakeAvSink, FakeVideoSource, WIN, make_recorder, wait_for


def recorder(tmp_path, *, video=None, audio=None, sink=None):
    return make_recorder(tmp_path, video or FakeVideoSource(), audio or FakeAudioSource(),
                         video_sink_factory=lambda *args: sink or FakeAvSink())


def test_transient_gpu_failures_keep_retrying_same_producer_with_capped_backoff():
    now = [0.0]
    gate = RetryGate(clock=lambda: now[0], cooldown=1, max_cooldown=4)
    key = gate.observe(producer())
    for delay in (1, 2, 4, 4, 4, 4):
        gate.failed(key, "transient helper failure", productive=True)
        assert gate.observe(producer()) == key
        assert gate.blocked(key)
        now[0] += delay - 0.01
        assert gate.blocked(key), "capability refresh must not bypass the delay"
        now[0] += 0.01
        assert gate.blocked(key) is None
    gate.failed(key, "native_capture_ended:3:9")
    now[0] += 10000
    gate.observe(producer(rom_open=False))
    key = gate.observe(producer(generation=9))
    assert "fully close and reopen" in gate.blocked(key)


def test_attach_discovery_recovers_without_restarting_recorder(tmp_path):
    rec = recorder(tmp_path)
    attempts = []

    def find(title):
        attempts.append(time.monotonic())
        if len(attempts) == 1:
            raise OSError("window enumeration transient")
        return WIN

    rec._window_finder = find
    rec.start()
    thread = rec._thread
    try:
        assert wait_for(lambda: rec.status()["recovery"]["error"] is not None)
        assert wait_for(lambda: rec.status()["recording"])
        assert rec._thread is thread and thread.is_alive()
        assert attempts[1] - attempts[0] >= 0.9
        assert rec.status()["recovery"]["error"] is None
    finally:
        rec.stop()


def test_maintenance_failure_does_not_replace_healthy_capture(tmp_path):
    rec = recorder(tmp_path)
    rec.start()
    try:
        assert wait_for(lambda: rec._recording)
        source, sink = rec._video_source, rec._video_sink
        original = rec.ring.maintain
        calls = []

        def maintain():
            calls.append(True)
            if len(calls) == 1:
                raise OSError("transient storage scan")
            original()

        rec.ring.maintain = maintain
        rec._next_storage_maintenance = 0
        assert wait_for(lambda: len(calls) >= 2)
        assert rec._video_source is source and rec._video_sink is sink
        assert rec._thread.is_alive() and rec._recording
    finally:
        rec.stop()


def test_cleanup_retry_blocks_new_capture_until_same_owner_closes(tmp_path):
    calls = []
    released = threading.Event()

    class Video(FakeVideoSource):
        def start(self, on_frame, on_stopped):
            assert not calls or released.is_set()
            calls.append(self)
            self.ended = on_stopped

        def stop(self):
            if self is calls[0] and not released.is_set():
                raise RuntimeError("fixture retains video custody")
            super().stop()

    rec = recorder(tmp_path)
    rec._video_factory = lambda win: Video()
    rec.start()
    try:
        assert wait_for(lambda: len(calls) == 1 and rec._recording)
        held = rec._rec_lock
        calls[0].ended()
        assert wait_for(lambda: rec._capture_cleanup_failed)
        assert not held.closed and len(calls) == 1
        assert rec.status()["recovery"]["state"] == "cleanup_pending"
        assert not rec.cleanup_scratch()
        released.set()
        assert wait_for(lambda: len(calls) == 2 and rec._recording)
        assert held.closed and calls[0].stopped
        assert not rec._capture_cleanup_failed
    finally:
        released.set()
        rec.stop()


class Demand:
    def __init__(self, *args):
        self.snapshot = DemandSnapshot("active")
        self.closed = False

    def start(self):
        pass

    def request_stop(self, *args, **kwargs):
        pass

    def close(self, timeout):
        self.closed = True


def test_gpu_late_cleanup_stays_on_media_thread_and_keeps_native_failure_class():
    sessions, closers, demands = [], [], []
    closed = threading.Event()

    class Session:
        error = "native_capture_ended:3:9"

        def __init__(self, owner, demand):
            sessions.append(self)
            demands.append(demand)
            self.thread = threading.current_thread()

        def run(self):
            raise GpuCleanupError("publication worker still running")

        def close(self):
            assert threading.current_thread() is self.thread
            assert not demands[0].closed
            closers.append(time.monotonic())
            if len(closers) == 1:
                raise GpuCleanupError("same publication owner still running")
            closed.set()

    gate = RetryGate(cooldown=0)
    key = gate.observe(producer())
    owner = GpuCapture(42, US, nominal_rate=30, demand_factory=Demand,
                       session_factory=Session, retry_gate=gate, producer_identity=key)
    # The real wait gate's Win32 control boundary is irrelevant to disposal.
    gate.wait = lambda *args: True
    owner.create_sink(NS(picture_feed=True), None, None, None)
    started = time.monotonic()
    owner.start(lambda *args: pytest.fail("raw pixel callback"), lambda: None)
    try:
        assert wait_for(lambda: owner.cleanup_error is not None)
        assert owner.status()["state"] == "retiring"
        assert not owner.capture_retired() and len(sessions) == 1
        owner.request_stop()  # Stop/wake cannot bypass the cleanup backoff.
        assert closed.wait(5)
        assert owner._done.wait(2)
        owner.finish()
        assert closers[0] - started >= 0.9
        assert closers[1] - closers[0] >= 1.9
        assert owner.cleanup_error is None and demands[0].closed
        assert "fully close and reopen" in gate.blocked(key)
    finally:
        owner.finish()


def test_audio_pump_failure_is_visible_and_safely_joinable():
    entered = threading.Event()

    def broken(data):
        entered.set()
        raise OSError("sink delivery failed")

    pump = AudioPump(48000, broken)
    try:
        pump.feed(b"\x01\x00\x02\x00", 0)
        assert entered.wait(1)
        assert wait_for(lambda: not pump._thread.is_alive())
        with pytest.raises(RuntimeError, match="sink delivery failed"):
            pump.check_health()
        before = pump._q.qsize()
        pump.feed(b"\x03\x00\x04\x00", 0)
        assert pump._q.qsize() == before
    finally:
        pump.stop(timeout=1)


class PumpAudio:
    mode = "process"

    def __init__(self):
        self.pump = None
        self.stopped = False

    def start(self, on_pcm):
        self.pump = AudioPump(48000, on_pcm)

    def check_health(self):
        self.pump.check_health()

    def stop(self):
        self.pump.stop(timeout=1)
        self.stopped = True


def test_dead_audio_reopens_after_backoff_without_replacing_video(tmp_path):
    class Sink(FakeAvSink):
        def submit_audio(self, data):
            if not self.audio:
                self.audio.append(b"failed")
                raise OSError("one failed PCM block")
            super().submit_audio(data)

    sink, made = Sink(), []
    rec = recorder(tmp_path, sink=sink)

    def audio(pid):
        assert not made or made[-1].stopped
        made.append(PumpAudio())
        return made[-1]

    rec._audio_factory = audio
    rec._begin_capture(WIN)
    source, clock = rec._video_source, rec._clock
    try:
        made[0].pump.feed(b"\x01\x00\x02\x00", 0)
        assert wait_for(lambda: not made[0].pump._thread.is_alive())
        rec._maybe_recover_audio(WIN)
        assert made[0].stopped and len(made) == 1
        assert rec.status()["audio_mode"] == "none"
        assert "one failed PCM" in rec.status()["audio_health"]["error"]
        rec._maybe_recover_audio(WIN)
        assert len(made) == 1, "failure must wait before constructing replacement"
        rec._audio_retry_at = 0
        rec._maybe_recover_audio(WIN)
        assert len(made) == 2 and rec._video_source is source and rec._clock is clock
        made[1].pump.feed(b"\x03\x00\x04\x00", 0)
        assert wait_for(lambda: len(sink.audio) == 2)
        assert sink.audio[-1] == b"\x03\x00\x04\x00"
        assert rec.status()["audio_health"]["error"] is None
    finally:
        rec.stop()


def test_initial_audio_unavailable_later_recovers_on_same_video(tmp_path):
    rec = recorder(tmp_path)
    calls = []

    def audio(pid):
        calls.append(pid)
        if len(calls) == 1:
            raise OSError("audio service starting")
        return FakeAudioSource()

    rec._audio_factory = audio
    rec._begin_capture(WIN)
    source = rec._video_source
    try:
        assert rec._recording and rec._audio_source is None
        rec._maybe_recover_audio(WIN)
        assert calls == [WIN.pid]
        rec._audio_retry_at = 0
        rec._maybe_recover_audio(WIN)
        assert calls == [WIN.pid, WIN.pid]
        assert rec._video_source is source and rec._audio_source is not None
    finally:
        rec.stop()


def test_audio_recovery_cannot_overlap_unclosed_audio_owner(tmp_path):
    allowed, made = [False], []

    class Audio(FakeAudioSource):
        def check_health(self):
            raise RuntimeError("consumer failed")

        def stop(self):
            if not allowed[0]:
                raise RuntimeError("audio still owns callback")

    rec = recorder(tmp_path)

    def factory(pid):
        assert not made or allowed[0]
        made.append(Audio())
        return made[-1]

    rec._audio_factory = factory
    rec._begin_capture(WIN)
    held = rec._rec_lock
    try:
        with pytest.raises(RuntimeError, match="still owns callback"):
            rec._maybe_recover_audio(WIN)
        assert rec._audio_source is made[0] and not held.closed
        assert rec._capture_cleanup_failed and not rec.cleanup_scratch()
        rec._begin_capture(WIN)
        assert len(made) == 1
        allowed[0] = True
        rec._teardown_capture()
        rec._begin_capture(WIN)
        assert len(made) == 2 and rec._recording
    finally:
        allowed[0] = True
        rec.stop()


def test_unjoined_attach_worker_is_retained_and_cannot_be_started_twice(tmp_path):
    rec = recorder(tmp_path)
    pending = NS(join=lambda timeout: None, is_alive=lambda: True)
    rec._thread = pending
    rec.stop(cleanup=False)
    assert rec._thread is pending and rec._stopping
    rec.start()
    assert rec._thread is pending and rec._stopping
    assert rec.status()["recovery"]["error"] == "replay attach worker still owns cleanup"


def test_audio_repeated_early_failures_and_attach_faults_cap_at_30s(tmp_path):
    rec = recorder(tmp_path)
    for delay in (1, 2, 4, 8, 16, 30, 30):
        assert rec._recovery_delay("discovery failed") == delay
        now = time.monotonic()
        rec._schedule_audio_retry("delivery failed")
        assert delay <= rec._audio_retry_at - now < delay + 0.2


@pytest.mark.parametrize("kind", ["process", "system"])
def test_audio_source_health_detects_dead_underlying_device_worker(kind):
    source = ProcessAudioSource(42) if kind == "process" else SystemAudioSource(pid=42)
    source._pump = source._watchdog = NS(check_health=lambda: None)
    if kind == "process":
        source._tap = NS(_thread=NS(is_alive=lambda: False))
    else:
        source._stream = NS(is_active=lambda: False)
    with pytest.raises(RuntimeError, match="not running"):
        source.check_health()


def test_watchdog_unexpected_failure_is_reported():
    def broken():
        raise OSError("watchdog probe failed")

    dog = DeafStreamWatchdog(42, broken, lambda: None, "fixture", check_every_s=0.001)
    dog.start()
    try:
        assert wait_for(lambda: not dog._thread.is_alive())
        with pytest.raises(RuntimeError, match="watchdog probe failed"):
            dog.check_health()
    finally:
        dog.stop()


def test_repeated_process_reopen_replaces_both_sources_and_ignores_old_callback(tmp_path):
    current, videos, audios = [None], [], []
    rec = recorder(tmp_path)
    rec._window_finder = lambda title: current[0]

    class Video(FakeVideoSource):
        def start(self, on_frame, on_stopped):
            self.ended = on_stopped

    def video(win):
        if videos:
            assert videos[-1].stopped
        videos.append(Video())
        return videos[-1]

    def audio(pid):
        audios.append(pid)
        return FakeAudioSource()

    rec._video_factory, rec._audio_factory = video, audio
    rec.start()  # Server-before-producer with repeated close/reopen.
    try:
        for i in range(3):
            current[0] = replace(WIN, pid=WIN.pid + i, hwnd=WIN.hwnd + i)
            assert wait_for(lambda expected=i + 1: len(videos) == expected and rec._recording)
            if i:
                videos[i - 1].ended()
                assert not rec._window_lost.is_set()
            current[0] = None
            assert wait_for(lambda: not rec._recording and videos[-1].stopped)
        assert audios == [WIN.pid, WIN.pid + 1, WIN.pid + 2]
    finally:
        rec.stop()
