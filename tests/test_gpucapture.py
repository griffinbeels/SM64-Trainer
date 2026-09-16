"""Recorder entry/lifetime contracts; no live capture, audio device or GPU."""

from dataclasses import replace
from types import SimpleNamespace as NS
import threading
import time

import numpy as np
import pytest

from sm64_events.memory.layout import US as US_LAYOUT
from sm64_events.replay.gpucapture import GpuCapture, GpuCleanupError, discover
from sm64_events.replay.gpucapture_session import CaptureSession
from sm64_events.replay.gpusettings import GpuSettings
from sm64_events.replay.gpudemand import DemandSnapshot
from sm64_events.replay.capturecontrol import CAP_GPU, PASSIVE
from test_replay_recorder import (
    FakeAudioSource,
    FakeVideoSource,
    FakeAvSink,
    make_recorder,
    WIN,
)


def wait(predicate):
    deadline = time.monotonic() + 2
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(0.002)


def test_recorder_selects_one_gpu_sink_and_drains_audio_source_before_close(tmp_path):
    calls = []

    class Sink(FakeAvSink):
        def start(self):
            calls.append("sink-start")

        def stop(self):
            calls.append("sink-stop")

        def submit_audio(self, data):
            calls.append(("pcm", data))

    sink = Sink()

    class Video(FakeVideoSource):
        frame_source = "plugin"

        def create_sink(self, cfg, clock, ledger, publish):
            calls.append("bind")
            assert callable(publish) and ledger is not None and clock is not None
            return sink

        def start(self, on_frame, on_stopped):
            calls.append("video-start")

        def request_stop(self):
            calls.append("revoke")

        def stop(self):
            calls.append("video-stop")

    class Audio(FakeAudioSource):
        def stop(self):
            calls.append("audio-stop")
            self.on_pcm(np.array([[123, -456]], dtype=np.int16))

    def forbidden(*args):
        pytest.fail("paired GPU source must not start a raw-image encoder")

    rec = make_recorder(tmp_path, Video(), Audio(), video_sink_factory=forbidden)
    try:
        rec._begin_capture(WIN)
        assert rec.fragments.enabled and rec._writer is None
        rec._teardown_capture()
        assert calls[:3] == ["bind", "sink-start", "video-start"]
        assert (
            calls.index("revoke")
            < calls.index("video-stop")
            < calls.index("audio-stop")
        )
        assert calls[-2:] == [
            ("pcm", np.array([[123, -456]], dtype=np.int16).tobytes()),
            "sink-stop",
        ]
        assert rec._capture_closed
    finally:
        rec.stop()


def test_unproved_helper_disposal_blocks_scratch_cleanup_and_reuse(tmp_path):
    made = []

    class BadSession:
        def __init__(self, owner, demand):
            made.append(self)

        def run(self):
            raise GpuCleanupError("private GPU helper job still owns resources")

    video = GpuCapture(
        WIN.pid,
        US_LAYOUT,
        nominal_rate=30,
        demand_factory=FakeDemand,
        session_factory=BadSession,
    )
    rec = make_recorder(tmp_path, video, FakeAudioSource())
    try:
        rec._begin_capture(WIN)
        wait(video._done.is_set)
        rec._teardown_capture()
        assert not rec._capture_closed and rec._capture_cleanup_failed
        assert not rec.cleanup_scratch()
        rec._begin_capture(WIN)
        assert len(made) == 1 and not rec._recording
        with pytest.raises(GpuCleanupError, match="still owns resources"):
            video.finish()
    finally:
        rec.stop(cleanup=False)


class FakeDemand:
    instances = []

    def __init__(self, *args):
        self.stop_event = threading.Event()
        self.closed = False
        self.snapshot = DemandSnapshot("active")
        self.instances.append(self)

    def start(self):
        pass

    def request_stop(self, reason="stopped", *, expected=False):
        self.stop_event.set()

    def close(self, timeout):
        self.request_stop()
        self.closed = True


def test_explicit_pause_retires_one_request_and_resume_creates_fresh_request():
    instances = []
    idle = [False]

    class Session:
        def __init__(self, owner, demand):
            self.owner, self.demand = owner, demand

        def run(self):
            instances.append(self.demand)
            assert self.demand.stop_event.wait(2)

    owner = GpuCapture(
        42,
        US_LAYOUT,
        nominal_rate=30,
        demand_factory=FakeDemand,
        session_factory=Session,
    )
    owner.create_sink(NS(picture_feed=True), object(), object(), lambda *a: None)
    owner.set_pause_check(lambda: idle[0])
    stopped = threading.Event()
    try:
        owner.start(lambda *a: pytest.fail("raw frame callback"), stopped.set)
        wait(lambda: len(instances) == 1)
        idle[0] = True
        owner.refresh_demand()
        wait(lambda: instances[0].closed)
        assert not stopped.is_set()
        idle[0] = False
        owner.refresh_demand()
        wait(lambda: len(instances) == 2)
        assert instances[0] is not instances[1]
    finally:
        owner.finish()
    assert stopped.is_set() and all(d.closed for d in instances)


def test_detached_audio_handoff_cannot_append_after_final_drain():
    owner = GpuCapture(42, US_LAYOUT, nominal_rate=30)
    handoff = owner.begin_audio()
    owner.submit_audio(b"ABCD")
    owner.end_audio(handoff)
    owner.submit_audio(b"EFGH")
    assert handoff.take(time.monotonic()).data == b"ABCD"
    assert handoff.drained()
    assert not handoff.submit(b"IJKL", time.time(), now=time.monotonic())
    assert handoff.drained()


def test_session_respects_dataclass_demand_and_lease_fault():
    owner = NS(settings=GpuSettings(), want_capture=lambda: True)
    demand = NS(snapshot=DemandSnapshot("preparing"))
    session = CaptureSession(owner, demand)
    assert session._continue()
    demand.snapshot = DemandSnapshot("fault", "lease expired")
    with pytest.raises(RuntimeError, match="lease expired"):
        session._continue()
    demand.snapshot = DemandSnapshot("stopped")
    assert not session._continue()


def test_archive_failure_still_closes_channel_and_owned_helper():
    actions = []

    def bad_finish(*args):
        actions.append("archive")
        raise OSError("archive close failed")

    owner = NS(settings=GpuSettings(), end_audio=lambda h: actions.append("audio"))
    demand = NS(request_stop=lambda reason: actions.append("revoke"))
    session = CaptureSession(owner, demand)
    session.archive = NS(finish=bad_finish)
    session.channel = NS(close=lambda: actions.append("channel"))
    session._finish_helper = lambda: actions.append("helper")
    with pytest.raises(OSError, match="archive close"):
        session.close()
    assert actions == ["revoke", "audio", "channel", "helper", "archive"]


def test_unavailable_gpu_worker_is_not_reported_as_recording(tmp_path):
    video = GpuCapture(WIN.pid, US_LAYOUT, nominal_rate=30)
    rec = make_recorder(tmp_path, video, FakeAudioSource())
    rec._video_source = video
    rec._video_sink = video.create_sink(NS(picture_feed=True), None, None, None)
    rec._recording = True  # recorder owns a worker which is waiting at its retry gate
    video.report_wait("native resources exhausted")
    assert rec.status()["recording"] is False
    assert rec.perf_gauges()["recording"] is False
    assert rec.status()["publication_error"] == "native resources exhausted"
    video._status = dict(kind="gpu", state="preparing")
    assert not rec.status()["recording"]
    video._status = dict(kind="gpu", state="recording")
    assert rec.status()["recording"] and rec.perf_gauges()["recording"]
    video.error = "lost capture"
    assert not rec.status()["recording"]


def test_unfinished_publication_still_disposes_channel_and_helper():
    from sm64_events.replay.gpupublication import PublicationBusyError

    actions = []

    def blocked(*args, **kwargs):
        actions.append("publication")
        raise PublicationBusyError("writer owns archive")

    owner = NS(settings=GpuSettings(), end_audio=lambda h: actions.append("audio"))
    session = CaptureSession(owner, NS(request_stop=lambda reason: actions.append("revoke")))
    session.output = NS(finish=blocked, status=lambda: {"pending_bytes": 1})
    session.channel = NS(close=lambda: actions.append("channel"))
    session._finish_helper = lambda: actions.append("helper")
    with pytest.raises(PublicationBusyError):
        session.close()
    assert actions == ["revoke", "audio", "channel", "helper", "publication"]


def test_finished_publication_failure_cannot_hide_unproved_helper_exit():
    from sm64_events.replay.gpupublication import PublicationError

    actions = []

    def unproved():
        actions.append("helper")
        raise RuntimeError("helper still owns resources")

    def publication(*args, **kwargs):
        actions.append("publication")
        raise PublicationError("disk failed")

    owner = NS(settings=GpuSettings(), end_audio=lambda h: None)
    session = CaptureSession(owner, NS(request_stop=lambda reason: None))
    session._finish_helper = unproved
    session.output = NS(finish=publication, status=lambda: {"error": "disk failed"})
    with pytest.raises(RuntimeError, match="helper still owns resources"):
        session.close()
    assert actions == ["helper", "publication"]


@pytest.mark.parametrize("has_media", [False, True])
def test_partial_media_setup_aborts_owned_mux_before_archive_and_helper(has_media):
    actions = []
    owner = NS(settings=GpuSettings(), end_audio=lambda h: actions.append("audio"))
    demand = NS(request_stop=lambda reason: actions.append("revoke"))
    session = CaptureSession(owner, demand)
    session.mux = NS(abort=lambda: actions.append("mux"))
    if has_media:
        session.media = NS(
            closed=False,
            fault="partial setup",
            abort=lambda reason: actions.append("media"),
        )
    session.archive = NS(finish=lambda reason: actions.append("archive"))
    session._finish_helper = lambda: actions.append("helper")
    session.close()
    assert actions == [
        "revoke",
        "audio",
        "helper",
        "media" if has_media else "mux",
        "archive",
    ]


def test_helper_done_is_insufficient_without_empty_closed_job():
    owner = NS(settings=replace(GpuSettings(), close_s=0.02))
    session = CaptureSession(owner, object())
    session.controller = NS(
        stop=lambda reason: None,
        take_result=lambda: None,
        status=lambda: {
            "fault": None,
            "done": True,
            "disposal": {"empty": False, "handle_closed": False, "active_processes": 1},
        },
    )
    with pytest.raises(RuntimeError, match="disposal is unproved"):
        session._finish_helper()


def test_discovery_is_read_only_and_requires_matching_capable_producer():
    status = NS(producer_pid=42, state=PASSIVE, capabilities=CAP_GPU)

    class Control:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def status(self):
            return status

        def acquire(self):
            pytest.fail("discovery must not activate capture")

    assert isinstance(
        discover(42, US_LAYOUT, nominal_rate=30, control_factory=Control), GpuCapture
    )
    assert discover(43, US_LAYOUT, nominal_rate=30, control_factory=Control) is None
    status.capabilities = 1
    assert discover(42, US_LAYOUT, nominal_rate=30, control_factory=Control) is None


def test_the_tick_wait_is_not_quantized_to_the_windows_timer():
    """Event.wait(0.004) sleeps 15.5 ms on this machine; three of those per
    picture drained the eight native slots on any short stall (round 48).
    The tick waiter must honour a 4 ms period and wake at once on set() or
    on a watched native event."""
    import ctypes
    import statistics
    from time import perf_counter
    from sm64_events.replay.tickwait import TickWaiter

    waiter = TickWaiter()
    if not waiter.high_resolution:
        pytest.skip("no high-resolution waitable timer on this host")
    try:
        samples = []
        for _ in range(40):
            started = perf_counter()
            waiter.wait(0.004)
            samples.append(perf_counter() - started)
        assert statistics.median(samples) < 0.008, samples
        # set() from another thread ends a long wait immediately.
        threading.Timer(0.01, waiter.set).start()
        started = perf_counter()
        waiter.wait(1.0)
        assert perf_counter() - started < 0.2
        # A watched native auto-reset event ends the wait the same way.
        k = ctypes.windll.kernel32
        k.CreateEventW.restype = ctypes.c_void_p
        native = k.CreateEventW(None, False, False, None)
        waiter.watch(native)
        threading.Timer(0.01, lambda: k.SetEvent(ctypes.c_void_p(native))).start()
        started = perf_counter()
        waiter.wait(1.0)
        assert perf_counter() - started < 0.2
        waiter.unwatch(native)
        k.CloseHandle(ctypes.c_void_p(native))
    finally:
        waiter.close()


def test_the_shipped_gpu_settings_fit_the_native_request_caps():
    """Coherence, not contents: whatever the shipped budgets are, the request
    page the wrapper admits must accept them. On 2026-09-16 a pending budget
    one MiB over slots x packet_bytes refused every live request and no test
    noticed, because none packed the shipped settings."""
    from sm64_events.replay.gpurequest import validate_limits
    validate_limits(GpuSettings().request())
