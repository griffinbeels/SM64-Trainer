"""The beta24 no-channel failure must leave footage recording usable.

Run the actual session, source owner and factory with only OS channel/control
boundaries replaced. No renderer, game, installed app or encoder is acquired.
"""
from dataclasses import replace
from functools import partial
from types import SimpleNamespace as NS
import threading

import pytest

from sm64_events.memory.layout import US
from sm64_events.replay import sourcefactory
from sm64_events.replay.capturecontrol import CAP_GPU, PASSIVE
from sm64_events.replay.gpucapture import GpuCapture, GpuCleanupError, discover
from sm64_events.replay.gpucapture_session import CaptureSession
from sm64_events.replay.gpudemand import DemandSnapshot
from sm64_events.replay.gpuretry import RetryGate
from sm64_events.replay.gpusettings import GpuSettings
from test_replay_recorder import FakeVideoSource, wait_for


class Control:
    status_value = NS(producer_pid=42, producer_created_lo=7, producer_created_hi=0,
                      generation=1, build_id="beta24-source", state=PASSIVE,
                      capabilities=CAP_GPU, rom_open=True)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def status(self):
        return self.status_value

    def close(self):
        pass


class Demand:
    def __init__(self, *_):
        self.identity = NS(nonce=bytes(16), control_generation=1, producer_pid=42, producer_birth=7)
        self.snapshot = DemandSnapshot("preparing", capture_build="beta24-source")
        self.closed = False

    def start(self):
        pass

    def request_stop(self, *_args, **_kwargs):
        pass

    def reconcile_lifecycle(self):
        return self.snapshot.lifecycle

    def close(self, timeout):
        self.closed = True


def setup_factory(monkeypatch):
    cameras = []
    factory = sourcefactory.SourceFactory(US, NS(fps=30),
        lambda win, fps: cameras.append(win) or NS(stop=lambda: None))
    factory.retry_gate = RetryGate(control_factory=Control)
    monkeypatch.setattr(sourcefactory, "discover", partial(discover, control_factory=Control,
                                                        adapter_probe=lambda: None))
    return factory, cameras


def run_failed_start(factory, *, scenario="deadline"):
    owner = factory.create(NS(pid=42))
    assert isinstance(owner, GpuCapture)
    demands = []

    def demand_factory(*args):
        demand = Demand(*args)
        demands.append(demand)
        return demand

    def session_factory(owner, demand):
        def missing_channel(**_identity):
            if scenario == "other channel error":
                raise OSError("channel access refused")
            raise FileNotFoundError("no published picture channel")
        session = CaptureSession(owner, demand, channel_factory=missing_channel)
        if scenario == "native deadline":
            demand.snapshot = replace(demand.snapshot, state="fault", reason="capture_setup_deadline")
        elif scenario == "native delivery deadline":
            demand.snapshot = replace(demand.snapshot, state="fault", reason="native_capture_ended:3:10012",
                                      backend_reason=10012)
        elif scenario == "lifecycle":
            demand.snapshot = replace(demand.snapshot, state="fault", reason="producer_closed", lifecycle=True)
        elif scenario == "cancelled":
            demand.snapshot = replace(demand.snapshot, cancelled=True)
        elif scenario == "stopped during failure":
            demand.reconcile_lifecycle = lambda: owner.request_stop() or False
        elif scenario == "cleanup":
            session.close = lambda: (_ for _ in ()).throw(OSError("owned channel cannot close"))
        elif scenario == "encoder":
            session._open_channel = lambda: True
            session._open_encoder = lambda: (_ for _ in ()).throw(TimeoutError("GPU recording startup deadline"))
        return session

    owner.settings = replace(GpuSettings(), startup_s=0.02)
    owner._demand_factory, owner._session_factory = demand_factory, session_factory
    # Never sleep through cleanup retries: preserve the unresolved owner below.
    owner.CLEANUP_DELAYS = ()
    owner.create_sink(NS(picture_feed=True), object(), object(), lambda *args: None)
    stopped = threading.Event()
    owner.start(lambda *args: pytest.fail("GPU cannot supply raw frames"), stopped.set)
    assert stopped.wait(3), owner.status()
    if scenario == "cleanup":
        with pytest.raises(GpuCleanupError):
            owner.finish()
    else:
        owner.finish()
    return owner, demands


@pytest.mark.parametrize("scenario", ["deadline", "native deadline", "native delivery deadline"])
def test_no_channel_deadline_keeps_desktop_footage_and_original_failure(monkeypatch, scenario):
    factory, cameras = setup_factory(monkeypatch)
    owner, demands = run_failed_start(factory, scenario=scenario)
    assert len(demands) == 1 and demands[0].closed and owner.cleanup_error is None
    assert owner.error and ("deadline" in owner.error or "10012" in owner.error)
    identity = factory.retry_gate.identity_for(42)
    assert factory.retry_gate.unsupported_for(identity) is None
    assert factory.retry_gate.fallback_for(identity)
    first = factory.diagnostics()["first_failure"]
    assert first["stage"] == "channel" and first["codec"] is None and first["startup"] is None
    expected_reason = 10012 if scenario == "native delivery deadline" else 0
    assert first["native_reason"] == expected_reason
    assert "deadline" in first["reason"] or "10012" in first["reason"]
    for _ in range(3):
        camera = factory.create(NS(pid=42))
        assert isinstance(camera, sourcefactory.DesktopAfterGpuStartup)
        assert camera.frame_source == "desktop" and camera.gpu_fallback
        assert camera.gpu_fallback == camera.frame_source_note
        assert not hasattr(camera, "gpu_unsupported")
    assert len(cameras) == 3 and len(demands) == 1
    assert factory.diagnostics()["first_failure"] == first


@pytest.mark.parametrize("scenario", ["lifecycle", "cancelled", "stopped during failure",
                                     "other channel error", "encoder", "cleanup"])
def test_unqualified_failures_and_unproved_cleanup_do_not_select_fallback(monkeypatch, scenario):
    factory, cameras = setup_factory(monkeypatch)
    owner, demands = run_failed_start(factory, scenario=scenario)
    assert demands[0].closed
    assert factory.retry_gate.fallback_for(factory.retry_gate.identity_for(42)) is None
    assert cameras == []
    assert isinstance(factory.create(NS(pid=42)), GpuCapture)
    assert bool(owner.cleanup_error) == (scenario == "cleanup")


@pytest.mark.parametrize("successor", ["generation", "birth", "rom reopen"])
def test_fallback_does_not_prevent_a_fresh_native_attempt(monkeypatch, successor):
    factory, _ = setup_factory(monkeypatch)
    run_failed_start(factory)
    old = factory.retry_gate.identity_for(42)
    status = NS(**vars(Control.status_value))
    if successor == "rom reopen":
        status.rom_open = False
        factory.retry_gate.observe(status)
        status.rom_open = True
    elif successor == "generation":
        status.generation += 1
    else:
        status.producer_created_lo += 1
    monkeypatch.setattr(Control, "status_value", status)
    assert isinstance(factory.create(NS(pid=42)), GpuCapture)
    assert factory.retry_gate.fallback_for(old) is None
    assert factory.diagnostics()["fallback_reason"] is None


def test_late_retired_producer_and_native_exhaustion_cannot_authorize_desktop(monkeypatch):
    factory, _ = setup_factory(monkeypatch)
    factory.create(NS(pid=42))
    gate = factory.retry_gate
    identity = gate.identity_for(42)
    gate.failed(identity, "native_capture_ended:3:9")
    gate.fallback(identity, "not safe")
    assert gate.fallback_for(identity) is None
    successor = NS(**vars(Control.status_value))
    successor.generation += 1
    new_identity = gate.observe(successor)
    gate.fallback(identity, "late old failure")
    assert gate.fallback_for(new_identity) is None


@pytest.mark.parametrize("successor", ["generation", "birth", "rom reopen"])
def test_started_fallback_passively_hands_over_on_qualified_successor(monkeypatch, successor):
    factory, _ = setup_factory(monkeypatch)
    run_failed_start(factory)
    desktop = FakeVideoSource()
    factory.desktop_factory = lambda win, fps: desktop
    camera = factory.create(NS(pid=42))
    monkeypatch.setattr(sourcefactory, "discover", lambda *args, **kwargs:
                        pytest.fail("a passive fallback watch must not construct a GPU capture"))
    monkeypatch.setattr("sm64_events.replay.pluginsource.LAYER_WATCH_S", 0.01)
    stopped = threading.Event()
    try:
        camera.start(lambda *args: None, stopped.set)
        assert not stopped.wait(0.04), "same failed producer must keep desktop footage"
        status = NS(**vars(Control.status_value))
        monkeypatch.setattr(Control, "status_value", status)
        if successor == "rom reopen":
            status.rom_open, status.capabilities = False, 0
            assert wait_for(lambda: factory.retry_gate._saw_closed)
            assert not stopped.is_set()
            status.rom_open, status.capabilities = True, CAP_GPU
        elif successor == "generation":
            status.generation += 1
        else:
            status.producer_created_lo += 1
        assert stopped.wait(2) and camera.upgraded
    finally:
        camera.stop()
    assert desktop.stopped and camera._thread is None


def test_passive_fallback_watch_retains_failed_control_close(monkeypatch):
    factory, _ = setup_factory(monkeypatch)
    run_failed_start(factory)
    closed = [False]

    class BadClose(Control):
        def close(self):
            if not closed[0]:
                raise OSError("passive mapping close failed")

    factory.retry_gate.control_factory = BadClose
    camera = factory.create(NS(pid=42))
    with pytest.raises(OSError, match="passive mapping"):
        camera._successor_ready()
    retained = camera._pending_control
    assert isinstance(retained, BadClose)
    with pytest.raises(OSError, match="passive mapping"):
        camera.stop()
    assert camera._pending_control is retained
    closed[0] = True
    camera.stop()
    assert camera._pending_control is None
