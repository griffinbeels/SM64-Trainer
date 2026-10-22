"""Identity-bound bootstrap/reader budgets; clocks and control pages are inert."""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from sm64_events.replay import capturecontrol as C
from sm64_events.replay.gpucapture_session import CaptureSession
from sm64_events.replay.gpudemand import DemandFailure
from sm64_events.replay.gpusettings import GpuSettings
from test_gpudemand import Fixture, Request


def setup(monkeypatch, schedule):
    f, clock = Fixture(), NS(now=0.0)
    request = Request(f)
    f.change(ack_token=request.identity.token, state=C.PREPARING)
    d = f.demand(clock=lambda: clock.now, publication_grace=.25)
    d._publish(identity=request.identity, state="preparing")
    rows, status = iter(schedule), f.status
    def observe():
        answer = status()
        if answer.state == C.ACTIVE:
            d.stop_event.set()  # End the synchronous fixture after observing success.
        return answer
    def advance(_timeout):
        if d.stop_event.is_set():
            return True
        row = next(rows, None)
        assert row is not None, "startup exceeded the scheduled phase budget"
        clock.now, changes, notification = row
        f.change(**changes)
        if notification:
            identity = request.identity if notification == "current" else replace(request.identity, token=999)
            assert d.note_channel_opened(identity) is (notification == "current")
        return False
    monkeypatch.setattr(f, "status", observe)
    monkeypatch.setattr(d.stop_event, "wait", advance)
    return f, clock, request, d


def test_a_real_channel_open_starts_its_own_prepare_budget(monkeypatch):
    f, _, request, d = setup(monkeypatch, [
        (4.0, {}, "current"), (5.1, {}, None), (6.0, {"state": C.ACTIVE}, None),
    ])
    d._monitor(f, request)
    assert d.snapshot.state == "active"


def test_duplicate_channel_notifications_cannot_extend_preparation(monkeypatch):
    f, _, request, d = setup(monkeypatch, [
        (4.0, {}, "current"), (8.0, {}, "current"), (9.26, {}, None),
    ])
    with pytest.raises(DemandFailure, match="capture_setup_deadline"):
        d._monitor(f, request)


def test_a_stale_channel_cannot_extend_bootstrap(monkeypatch):
    f, _, request, d = setup(monkeypatch, [
        (4.0, {}, "stale"), (5.26, {}, None),
    ])
    with pytest.raises(DemandFailure, match="capture_setup_deadline"):
        d._monitor(f, request)


def test_native_verdict_has_bounded_publication_grace(monkeypatch):
    f, _, request, d = setup(monkeypatch, [
        (5.1, {"state": C.UNAVAILABLE, "reason": 10031}, None),
    ])
    with pytest.raises(DemandFailure, match="native_capture_ended:3:10031"):
        d._monitor(f, request)
    assert d.snapshot.backend_reason == 10031


def test_channel_notification_during_the_last_control_read_wins(monkeypatch):
    f, clock, request, d = setup(monkeypatch, [
        (5.26, {}, None), (6.0, {"state": C.ACTIVE}, None),
    ])
    status = f.status
    def observe():
        if clock.now == 5.26:
            assert d.note_channel_opened(request.identity)
        return status()
    monkeypatch.setattr(f, "status", observe)
    d._monitor(f, request)
    assert d.snapshot.state == "active"


def test_a_committed_startup_timeout_refuses_a_late_notification(monkeypatch):
    f, _, request, d = setup(monkeypatch, [(5.26, {}, None)])
    with pytest.raises(DemandFailure, match="capture_setup_deadline"):
        d._monitor(f, request)
    assert not d.note_channel_opened(request.identity)


def test_early_channel_at_clock_zero_keeps_its_original_phase_budget(monkeypatch):
    f, clock, request, d = setup(monkeypatch, [])
    assert d.note_channel_opened(request.identity)
    clock.now = 5.26  # Notification can precede entry into _monitor.
    with pytest.raises(DemandFailure, match="capture_setup_deadline"):
        d._monitor(f, request)


def test_stopped_or_failed_demand_cannot_accept_a_late_channel(monkeypatch):
    _, _, request, d = setup(monkeypatch, [])
    d._publish(state="fault")
    assert not d.note_channel_opened(request.identity)
    d._publish(state="preparing")
    d.request_stop()
    assert not d.note_channel_opened(request.identity)


@pytest.mark.parametrize("retire", [False, True])
def test_actual_session_notifies_only_the_current_channel_owner(monkeypatch, retire):
    _, clock, request, d = setup(monkeypatch, [])
    clock.now = 4.0
    owner = NS(settings=GpuSettings(), want_capture=lambda: True)
    channel = NS(result_event=None)
    def open_channel(**identity):
        assert identity["generation"] == request.identity.control_generation
        assert identity["producer_pid"] == request.identity.producer_pid
        if retire:
            d._publish(state="fault")
        return channel  # Replaces only the validated OS channel constructor.
    session = CaptureSession(owner, d, channel_factory=open_channel)
    assert session._open_channel() is (not retire)
    assert session.channel is channel  # Owner retains cleanup even if it retired.
