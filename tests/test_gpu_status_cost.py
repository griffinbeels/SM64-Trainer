"""Ordinary reporting reuses helper health; disposal always obtains fresh proof."""
from types import SimpleNamespace as NS

import pytest

from sm64_events.replay.gpucapture import GpuCapture
from test_channelencoder import adapter


def instrument(control):
    original = control.status
    samples = []
    disposal = dict(done=False, disposal=None)

    def measured():
        result = {**original(), **disposal}
        samples.append(result)
        return result

    control.status = measured
    return samples, disposal


def report_fixture(a):
    owner = object.__new__(GpuCapture)
    session = NS(
        adapter=a, timings=None, state="recording",
        demand=NS(identity=NS(producer_pid=1, producer_birth=2,
                              control_generation=3, token=4)),
        channel=NS(header=NS(epoch=5)), output=None,
        media=NS(mux=NS(video_count=1), order=NS(pending_bytes=0),
                 pcm=NS(bytes=0), delivered=1),
        handoff=NS(bytes=0), frontier=1000,
    )
    return owner, session


def test_pump_and_real_report_query_helper_once():
    a, _, control, _, _ = adapter()
    calls, _ = instrument(control)
    owner, session = report_fixture(a)
    state = a.pump(now=0.01)
    GpuCapture.report(owner, session)
    assert len(calls) == 1
    assert owner._status["opened"] == state["opened"] is True
    assert owner._status["helper_disposed"] is False


def test_reporting_rebuilds_custody_after_heartbeat_without_resampling_helper():
    a, _, control, q, selection = adapter()
    # Reach retained native pixels through actual selection/packet delivery.
    from test_channelencoder import first, receipt
    first(a, a.channel)
    control.deliver(keys=[receipt()])
    a.pump(now=0.02)
    selection.certificate = 1000.1
    a.pump(now=0.03)
    calls, _ = instrument(control)
    a.pump(now=0.04)
    before = a.status(refresh=False)
    assert before["pending"] == 0
    assert a.heartbeat(1000.1, now=0.05) is not None
    owner, session = report_fixture(a)
    GpuCapture.report(owner, session)
    assert len(calls) == 1
    assert owner._status["pending"] == 1
    assert owner._status["operation"] == "Repeat"


def test_disposal_proof_is_fresh_even_after_cached_reporting():
    a, _, control, _, _ = adapter()
    calls, disposal = instrument(control)
    a.pump(now=0.01)
    disposal.update(done=True, disposal=dict(empty=True, handle_closed=True,
                                            active_processes=0))
    assert a.status(refresh=False)["helper_disposed"] is False
    assert a.status()["helper_disposed"] is True
    assert len(calls) == 2
    disposal["disposal"] = dict(empty=False, handle_closed=False, active_processes=1)
    assert a.status()["helper_disposed"] is False
    assert len(calls) == 3


def test_reporting_uses_each_new_pump_sample_instead_of_open_time_state():
    a, _, control, _, _ = adapter()
    calls, disposal = instrument(control)
    owner, session = report_fixture(a)
    a.pump(now=0.01)
    GpuCapture.report(owner, session)
    assert owner._status["helper_disposed"] is False
    disposal.update(done=True, disposal=dict(empty=True, handle_closed=True,
                                            active_processes=0))
    a.pump(now=0.02)
    GpuCapture.report(owner, session)
    assert owner._status["helper_disposed"] is True
    assert len(calls) == 2


def test_new_helper_fault_is_detected_next_pump_despite_reporting_cache():
    a, _, control, _, _ = adapter()
    calls, _ = instrument(control)
    a.pump(now=0.01)
    control.fault = "helper broke after previous tick"
    with pytest.raises(RuntimeError, match="helper broke"):
        a.pump(now=0.02)
    assert len(calls) == 2
    assert a.fault == control.fault
