"""Capture lifetime through real recorder/activity boundaries, without PJ64."""
import threading
from types import SimpleNamespace as NS

import pytest

from sm64_events.memory.addresses import PASSIVE_ACTIONS
from sm64_events.memory.layout import US
from sm64_events.replay.activity import ActivityTap
from sm64_events.replay.gpucapture import GpuCapture, GpuCleanupError
from sm64_events.replay.gpuretry import RetryGate
from test_gpucapture import FakeDemand, wait
from test_gpu_retry import producer
from test_replay_recorder import FakeAudioSource, WIN, make_recorder


def test_automatic_idle_keeps_run_and_reset_before_movement_preserves_demand(tmp_path):
    sessions = []

    class Session:
        def __init__(self, owner, demand):
            self.demand = demand

        def run(self):
            sessions.append(self.demand)
            assert self.demand.stop_event.wait(3)

    video = GpuCapture(WIN.pid, US, nominal_rate=30, demand_factory=FakeDemand,
                       session_factory=Session)
    rec = make_recorder(tmp_path, video, FakeAudioSource())
    try:
        rec._begin_capture(WIN)
        wait(lambda: len(sessions) == 1)
        original = sessions[0]
        for _ in range(3):
            rec._last_player_active -= rec.idle_after_s + 60
            rec._maybe_idle_pause()
            assert rec.is_idle() and video.want_capture()
            assert not rec.can_collect(), "retained lead-in is not disposable GC time"
            assert rec._fragment_idle_window() is not None
            assert not original.stop_event.is_set()
            # Reset into the passive fade-in: no stick movement is required.
            previous = NS(global_timer=1000, igt_overall=300, curr_level=1)
            current = NS(global_timer=1001, igt_overall=0, curr_level=1,
                         mario_action=next(iter(PASSIVE_ACTIONS)))
            assert ActivityTap(rec).process(previous, current) == []
            assert not rec.is_idle() and rec._fragment_idle_window() is None
            assert video._demand is original and video.want_capture()
        assert video.status()["sessions_started"] == 1
        # Explicit pause must still work when inactivity has already set idle.
        rec._set_idle(True)
        rec.set_session_paused(True)
        wait(lambda: original.closed and video.capture_retired())
        assert not video.want_capture() and rec.can_collect()
        assert rec._fragment_idle_window() is None
        rec.set_player_active()
        assert not video.want_capture(), "input cannot override explicit pause"
        rec.set_session_paused(False)
        wait(lambda: len(sessions) == 2)
        assert video.want_capture() and sessions[1] is not original
    finally:
        rec.stop()
    assert all(s.closed for s in sessions) and rec._capture_closed


@pytest.mark.parametrize("productive,cleanup", [(False, True), (True, True), (True, False)])
def test_a_runtime_fault_waits_out_the_cooldown_however_much_media_it_produced(productive, cleanup):
    """docs/replay-gpu-runtime.md: a short productive prefix or capability
    refresh does not waive the delay. Proved cleanup decides only what the
    failure reports."""
    now = [0.0]
    gate = RetryGate(clock=lambda: now[0], cooldown=10)
    key = gate.observe(producer())
    closed = threading.Event()

    class Demand(FakeDemand):
        def close(self, timeout):
            super().close(timeout)
            closed.set()
            if not cleanup:
                raise RuntimeError("lease owner still live")

    class Session:
        def __init__(self, owner, demand):
            self.owner = owner

        def run(self):
            identity = NS(producer_pid=42, producer_birth=1, control_generation=1, token=1)
            self.owner.report(NS(adapter=NS(status=lambda **kw: {}), demand=NS(identity=identity),
                timings=NS(summary=lambda: {}), output=None,
                state="recording", channel=NS(header=NS(epoch=1)),
                media=NS(mux=NS(video_count=int(productive)), order=NS(pending_bytes=0),
                         pcm=NS(bytes=0), delivered=int(productive)),
                handoff=NS(bytes=0), frontier=1))
            raise RuntimeError("runtime fault")

    owner = GpuCapture(42, US, nominal_rate=30, demand_factory=Demand,
                       session_factory=Session, retry_gate=gate, producer_identity=key)
    # A fake control page only; the real retry budget still executes.
    gate.control_factory = lambda: NS()  # wait is independently covered below
    gate.wait = lambda owner, identity: True
    owner._run()
    assert closed.is_set() and owner.error
    assert isinstance(owner.cleanup_error, GpuCleanupError) == (not cleanup)
    assert gate.blocked(key) and "repeated failures" not in gate.blocked(key)
    now[0] = 9.99
    assert gate.blocked(key), "packets written before the fault cannot shorten the cooldown"
    now[0] = 10
    assert gate.blocked(key) is None
    # The second failure doubles the cooldown (tests/test_gpu_retry.py owns
    # the back-off contract) and names the repetition.
    gate.failed(key, "second fault")
    now[0] = 25
    assert "repeated failures" in gate.blocked(key)


def test_startup_gc_finishes_before_any_replay_activation(monkeypatch):
    from sm64_events.server.app import _start_app_replay
    from sm64_events.replay import _gcwatch

    calls = []
    recorder = NS(can_collect=lambda: False)
    def arm(**kwargs):
        assert kwargs["is_idle"] is recorder.can_collect
        calls.append("full-collection")
    monkeypatch.setattr(_gcwatch, "arm", arm)
    _start_app_replay(NS(recorder=recorder, lifecycle_start=lambda: calls.append("capture")))
    assert calls == ["full-collection", "capture"]


def test_gc_permission_rejects_unproved_source_and_recorder_cleanup(tmp_path):
    owner = GpuCapture(42, US, nominal_rate=30)
    owner._status = {"state": "stopped"}
    assert owner.capture_retired()
    owner.cleanup_error = GpuCleanupError("helper still owns resources")
    assert not owner.capture_retired()
    rec = make_recorder(tmp_path, owner, FakeAudioSource())
    assert rec.can_collect()
    rec._capture_closed = False
    assert not rec.can_collect()
    rec._capture_closed = True
    rec._capture_retained = {"audio": object()}
    assert not rec.can_collect()
    rec._capture_retained.clear()
    assert rec.can_collect()
    rec._recording = True
    rec.fragments.enabled = True
    rec._idle = True
    assert not rec.can_collect(), "legacy fragmented idle still retains lead-in"
    rec.set_session_paused(True)
    assert rec.can_collect()
