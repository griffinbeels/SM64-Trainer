"""Source rediscovery and ROM retirement without live emulator/GPU resources."""

from dataclasses import replace
from types import SimpleNamespace as NS
import threading

import pytest

from sm64_events.memory.layout import US
from sm64_events.replay import capturecontrol as C
from sm64_events.replay import gpucapture as G
from sm64_events.replay import gpudemand as D
from sm64_events.replay import pluginsource as P
from sm64_events.replay import sourcefactory as S
from sm64_events.replay.gpucapture_session import CaptureSession
from sm64_events.replay.gpuretry import RetryGate
from sm64_events.replay.gpusettings import GpuSettings
from test_gpudemand import Fixture, eventual


def status(**changes):
    return replace(C.CaptureStatus(77, 4, C.PASSIVE, 0, 0, 0, C.CAP_GPU,
                                   True, 55, 1, "fixture"), **changes)


class ReadOnlyControl:
    def __init__(self, values):
        self.values, self.reads = values, 0

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def status(self):
        self.reads += 1
        value = self.values[0]
        if isinstance(value, Exception):
            raise value
        return value

    def acquire(self, *args, **kwargs):
        pytest.fail("read-only discovery must never acquire a capture lease")


@pytest.mark.parametrize("error", [FileNotFoundError("absent"),
                                   BlockingIOError("busy"), OSError("transient")])
def test_known_gpu_transient_read_never_opens_raw_source(monkeypatch, error):
    control = ReadOnlyControl([status()])
    factory = S.SourceFactory(US, NS(fps=60), lambda *a, **k: pytest.fail("desktop"))
    factory.retry_gate = RetryGate(control_factory=control)
    identity = factory.retry_gate.observe(status())
    assert factory.retry_gate.identity_for(77) == identity
    assert factory.retry_gate.identity_for(78) is None
    control.values[0] = error
    monkeypatch.setattr(S, "discover", lambda *a, **k: G.discover(
        *a, control_factory=control, **k))
    source = factory.create(NS(pid=77))
    assert isinstance(source, G.GpuCapture) and source.producer_identity == identity
    waits = []

    def wait(period):
        waits.append(period)
        control.values[0] = status()

    owner = NS(want_capture=lambda: True, wait=wait, report_wait=lambda r: None)
    assert factory.retry_gate.wait(owner, identity)
    assert waits == [0.5] and control.reads == 3


def test_cached_gpu_does_not_lease_to_a_reused_pid_or_changed_producer():
    control = ReadOnlyControl([status(producer_created_lo=56)])
    gate = RetryGate(control_factory=control)
    identity = gate.observe(status())
    owner = NS(want_capture=lambda: True, wait=lambda _: pytest.fail("wait"),
               report_wait=lambda _: None)
    assert not gate.wait(owner, identity)
    # A verified successor using a legacy plugin can be selected normally.
    control.values[0] = status(producer_created_lo=56, capabilities=C.CAP_PASSIVE)
    assert G.discover(77, US, nominal_rate=30, retry_gate=gate,
                      control_factory=control) is None


def test_late_gpu_arrival_reuses_desktop_watcher_without_legacy_pixels(monkeypatch):
    control = ReadOnlyControl([FileNotFoundError("plugin has not initialized")])
    desktop = NS(start=lambda frame, stop: None, stop=lambda: None,
                 status=lambda: {"grabs": 0})
    factory = S.SourceFactory(US, NS(fps=60), lambda *a, **k: desktop)
    monkeypatch.setattr(S, "discover", lambda *a, **k: G.discover(
        *a, control_factory=control, **k))
    monkeypatch.setattr(P, "LAYER_WATCH_S", 0.01)
    source = factory.create(NS(pid=77))
    assert isinstance(source, P.DesktopUntilLayerPresents)
    stopped = threading.Event()
    try:
        source.start(lambda *a: None, stopped.set)
        assert not stopped.wait(0.03)
        control.values[0] = status()
        assert stopped.wait(0.5) and source.upgraded
        assert isinstance(factory.create(NS(pid=77)), G.GpuCapture)
    finally:
        source.stop()


@pytest.mark.parametrize("changes, lifecycle", [
    ({"producer_pid": 78}, True),
    ({"rom_open": False}, True),
    ({"generation": 5}, True),
    ({"producer_created_lo": 56}, True),
    ({"state": C.CLOSED}, True),
    ({"state": C.UNAVAILABLE, "reason": C.FRESH_REQUEST}, True),
    ({"state": C.UNAVAILABLE, "reason": 6}, False),
    ({"capabilities": 0}, False),
    ({"ack_token": 999}, False),
])
def test_typed_lifecycle_ends_session_but_real_failure_still_raises(changes, lifecycle):
    fixture = Fixture()
    demand = fixture.demand().start()
    try:
        assert demand.ready.wait(1)
        fixture.change(ack_token=123, state=C.ACTIVE)
        eventual(lambda: demand.snapshot.state, lambda value: value == "active")
        fixture.change(**changes)
        assert demand.done.wait(1)
        assert demand.snapshot.lifecycle is lifecycle
        session = CaptureSession(NS(settings=GpuSettings(), want_capture=lambda: True), demand)
        if lifecycle:
            assert not session._continue()
        else:
            with pytest.raises(RuntimeError, match=demand.snapshot.reason):
                session._continue()
    finally:
        demand.close()


def test_cleanup_failure_overrides_normal_rom_retirement(monkeypatch):
    fixture = Fixture()
    demand = fixture.demand().start()
    assert demand.ready.wait(1)

    def close_failure():
        raise OSError("request handle cleanup failed")

    monkeypatch.setattr(fixture.request, "close", close_failure)
    fixture.change(rom_open=False)
    assert demand.done.wait(1)
    assert not demand.snapshot.lifecycle and demand.snapshot.cleanup_error
    with pytest.raises(D.DemandFailure, match="cleanup failed"):
        demand.close()


def test_rapid_rom_restarts_do_not_spend_failure_budget_and_reconnect():
    fixture = Fixture()
    control = ReadOnlyControl([status()])
    gate = RetryGate(control_factory=control)
    identity = gate.observe(status())
    # Leave one genuine failure in the budget: normal retirement must neither
    # add a failure nor erase the previous failure while the close poll is missed.
    gate.cooldown = 0
    gate.failed(identity, "one genuine initialization failure")
    made = []

    class LifecycleSession(CaptureSession):
        def run(self):
            assert self.demand.ready.wait(1)
            fixture.change(ack_token=123, state=C.ACTIVE)
            eventual(lambda: self.demand.snapshot.state, lambda state: state == "active")
            fixture.change(state=C.UNAVAILABLE, reason=C.FRESH_REQUEST)
            assert self.demand.done.wait(1)
            assert not self._continue()
            made.append(self)

    for _ in range(4):
        fixture.status_value = status()
        owner = G.GpuCapture(
            77, US, nominal_rate=30, retry_gate=gate, producer_identity=identity,
            demand_factory=lambda *args: fixture.demand(), session_factory=LifecycleSession,
        )
        owner.create_sink(NS(picture_feed=True), object(), object(), lambda *a: None)
        stopped = threading.Event()
        owner.start(lambda *a: pytest.fail("raw frame"), stopped.set)
        try:
            assert stopped.wait(2)
            assert owner.error is None and gate.blocked(identity) is None
        finally:
            owner.finish()
    assert fixture.requests == 4 and len(made) == 4
    # The one genuine failure survived four retirements: a second one now
    # exhausts the budget (a cooldown must exist for the gate to say so).
    gate.cooldown = 10
    gate.failed(identity, "second genuine failure")
    assert "repeated failures" in gate.blocked(identity)


@pytest.mark.parametrize("native_reason, benign", [(C.FRESH_REQUEST, True), (6, False)])
def test_channel_closes_before_control_poll_only_typed_lifecycle_is_benign(
    native_reason, benign
):
    entered, release = threading.Event(), threading.Event()

    class BlockedControl(Fixture):
        block = False

        def status(self):
            if self.block:
                entered.set()
                assert release.wait(1)
            return super().status()

    fixture = BlockedControl()
    demand = fixture.demand().start()
    try:
        assert demand.ready.wait(1)
        fixture.change(ack_token=123, state=C.ACTIVE)
        eventual(lambda: demand.snapshot.state, lambda state: state == "active")
        fixture.block = True
        assert entered.wait(1)
        fixture.change(state=C.UNAVAILABLE, reason=native_reason)
        reconcile = demand.reconcile_lifecycle
        reconciled = []

        def allow_control_poll():
            # The real supervisor has not consumed the new control state when
            # the media operation fails. It can publish only after this point.
            assert demand.snapshot.state == "active"
            release.set()
            result = reconcile()
            reconciled.append(result)
            return result

        demand.reconcile_lifecycle = allow_control_poll
        owner = NS(settings=GpuSettings(), want_capture=lambda: True,
                   end_audio=lambda h: None)
        session = CaptureSession(owner, demand)

        def closed_channel():
            raise RuntimeError("channel has already closed")

        session._open_channel = closed_channel
        if benign:
            session.run()
        else:
            with pytest.raises(RuntimeError, match="channel has already closed"):
                session.run()
        assert reconciled == [benign]
        # Preserve the error for suffix-abort even when retirement is normal.
        assert session.error == (
            "channel has already closed" if benign else
            "channel has already closed; demand=fault:native_capture_ended:3:6"
        )
        demand.close()
        assert demand.snapshot.lifecycle is benign
        # Every lease API read still occurred on its independent supervisor.
        assert len({tid for _, tid in fixture.calls}) == 1
    finally:
        release.set()
        demand.close()


def test_lifecycle_reconciliation_is_bounded_and_requires_positive_evidence():
    demand = Fixture().demand()
    timeouts = []
    demand._ended = NS(wait=lambda timeout: timeouts.append(timeout))
    assert not demand.reconcile_lifecycle()
    assert timeouts == [0.25]
    with pytest.raises(ValueError, match="250 ms"):
        demand.reconcile_lifecycle(0.251)
    demand._publish(state="fault", reason="rom_closed", lifecycle=False)
    # The text alone is not evidence: only a typed supervisor outcome qualifies.
    assert not demand.reconcile_lifecycle(0)


def test_cleanup_failure_after_lifecycle_publication_still_spends_retry_budget():
    fixture = Fixture()
    release, entered = threading.Event(), threading.Event()
    control = ReadOnlyControl([status()])
    gate = RetryGate(control_factory=control, cooldown=10)
    gate.wait = lambda owner, identity: True   # the budget, not the delay, is under test
    identity = gate.observe(status())
    gate.failed(identity, "first actual failure")

    class FailingCleanup(CaptureSession):
        def run(self):
            assert self.demand.ready.wait(1)

            def bad_close():
                entered.set()
                assert release.wait(1)
                raise OSError("cleanup did not complete")

            fixture.request.close = bad_close
            fixture.change(rom_open=False)
            assert entered.wait(1)
            assert self.demand.reconcile_lifecycle()
            assert not self._continue()
            release.set()

    owner = G.GpuCapture(
        77, US, nominal_rate=30, retry_gate=gate, producer_identity=identity,
        demand_factory=lambda *args: fixture.demand(), session_factory=FailingCleanup,
    )
    owner.create_sink(NS(picture_feed=True), object(), object(), lambda *a: None)
    stopped = threading.Event()
    owner.start(lambda *a: pytest.fail("raw frame"), stopped.set)
    try:
        assert stopped.wait(2)
        assert isinstance(owner.cleanup_error, G.GpuCleanupError)
        assert "repeated failures" in gate.blocked(identity)
        with pytest.raises(G.GpuCleanupError, match="cleanup pending"):
            owner.finish()
    finally:
        release.set()


@pytest.fixture(autouse=True)
def _short_retire_delays(monkeypatch):
    """The bounded cleanup retry (round 48) waits 1 s then 2 s between the
    retained owner's close attempts in production; these lifecycle scenarios
    assert on the same ordering within their own second."""
    monkeypatch.setattr(D.GpuDemand, "RETIRE_DELAYS", (0.05, 0.1))
    monkeypatch.setattr(G.GpuCapture, "CLEANUP_DELAYS", (0.05, 0.1, 0.2))
