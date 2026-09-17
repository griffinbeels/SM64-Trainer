"""Failure diagnostics preserve layer identity and include interrupted work."""

import pytest
from types import SimpleNamespace as NS

from sm64_events.replay.gpuchannel import ChannelStopped, CLOSED
from sm64_events.replay.gpudiagnostics import CaptureTimings, native_reason


def test_error_numbers_are_decoded_in_their_own_namespace():
    assert native_reason(6) == "backend admission failed"
    assert native_reason(8) == "native request invalid"
    assert native_reason(9) == "native resources exhausted"
    assert native_reason(10) == "native admission system error"
    assert native_reason(0x10000006, channel=True) == "channel result 6"
    assert native_reason(6, channel=True) == "unknown channel reason"
    assert native_reason(0x30002710, channel=True) == "unknown channel reason"
    assert native_reason(99999) == "unknown native reason"
    assert "legacy, branch unknown" in native_reason(10010)
    for code, name in ((10016, "source image missing"),
                       (10021, "source epoch changed"), (10022, "source admission refused")):
        error = ChannelStopped(CLOSED, 0x20000000 | code)
        assert name in str(error) and str(error.reason) in str(error)
        assert not error.cancelled
    assert ChannelStopped(CLOSED, 0x20002710).cancelled


def test_interrupted_tick_and_scheduling_gap_are_separate_and_storage_is_bounded():
    now = [0.0]
    timing = CaptureTimings(lambda: now[0], wall_clock=lambda: 1000)

    def work(duration, fail=False):
        now[0] += duration
        if fail:
            raise RuntimeError("original failure")
        return "result"

    assert timing.measure("media_setup", work, 0.050) == "result"
    timing.measure("tick", work, 0.001)
    now[0] += 0.200
    with pytest.raises(RuntimeError, match="original failure"):
        timing.measure("tick", work, 0.030, True)
    summary = timing.summary()
    assert summary["media_setup"]["total_ms"] == 50
    assert summary["tick"] == dict(count=2, total_ms=31, max_ms=30,
                                   max_started_monotonic_s=.251,
                                   max_started_unix_s=1000.251)
    assert summary["between_ticks"]["max_ms"] == 200
    stages = set(timing.stages)
    assert "sink_prepare" in stages
    for _ in range(10000):
        timing.measure("tick", work, 0.001)
    assert set(timing.stages) == stages
    assert timing.summary()["tick"]["count"] == 10002


def test_nested_stages_attribute_inclusive_peak_and_keep_original_error():
    now = [0.0]
    timing = CaptureTimings(lambda: now[0], wall_clock=lambda: 100)

    def slow(*, duration):
        now[0] += duration
        raise RuntimeError("media failed")

    def tick():
        now[0] += .002
        timing.measure("adapter_pump", slow, duration=.300)

    with pytest.raises(RuntimeError, match="media failed"):
        timing.measure("tick", tick)
    summary = timing.summary()
    assert summary["tick"]["max_ms"] == 302
    assert summary["adapter_pump"]["max_ms"] == 300
    assert summary["adapter_pump"]["max_started_unix_s"] == 100.002


@pytest.mark.parametrize("broken_status", [False, True])
def test_failure_counters_are_captured_before_reconcile_and_teardown(caplog, broken_status):
    from sm64_events.replay.gpucapture_session import CaptureSession
    from sm64_events.replay.gpudemand import DemandSnapshot
    from sm64_events.replay.gpusettings import GpuSettings

    pending = [61]
    actions = []
    fault = ChannelStopped(CLOSED, 0x20000000 | 10022)

    def counters():
        actions.append("snapshot")
        if broken_status:
            raise RuntimeError("status unavailable")
        return {"pending_blocks": pending[0]}

    def reconcile():
        actions.append("reconcile")
        pending[0] = 0
        return False

    def tick():
        raise fault

    session = CaptureSession(NS(settings=GpuSettings()), NS(
        snapshot=DemandSnapshot("active"), reconcile_lifecycle=reconcile,
    ))
    session._open_channel = lambda: True
    session._open_encoder = lambda: ()
    session._first_offers = lambda: (1,)
    session._create_media = lambda *_: None
    session._continue = lambda: True
    session.tick = tick
    session.close = lambda: actions.append("close")
    session.output = NS(status=counters)
    session.adapter = NS(status=lambda: {"pending": 4, "bridges": 2})
    with caplog.at_level("INFO", logger="sm64.replay"), pytest.raises(ChannelStopped) as caught:
        session.run()
    assert caught.value is fault
    record = next(r for r in caplog.records if "failure before cleanup" in r.msg)
    captured = record.args
    assert actions == ["snapshot", "reconcile", "close"]
    assert pending[0] == 0
    assert captured["channel_reason"] == fault.reason
    assert captured["adapter"] == {"pending": 4, "bridges": 2}
    assert captured["publication"] == (
        {"unavailable": "status unavailable"} if broken_status else {"pending_blocks": 61}
    )
