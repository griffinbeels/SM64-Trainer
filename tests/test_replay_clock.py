from datetime import datetime, timedelta, timezone

from sm64_events.replay.clock import CaptureClock, qpc_100ns

T0 = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)


def test_utc_of_maps_ticks_forward():
    clk = CaptureClock(anchor_qpc_100ns=10_000_000, anchor_utc=T0)
    # 1.5 s after the anchor in 100 ns ticks
    assert clk.utc_of(10_000_000 + 15_000_000) == T0 + timedelta(seconds=1.5)


def test_seconds_since_anchor_and_utc_roundtrip():
    clk = CaptureClock(anchor_qpc_100ns=0, anchor_utc=T0)
    assert clk.seconds_since_anchor(30_000_000) == 3.0
    assert clk.utc_to_seconds(T0 + timedelta(seconds=3)) == 3.0
    assert clk.utc_of(30_000_000) == T0 + timedelta(seconds=3)


def test_qpc_100ns_is_positive_and_monotonic():
    a = qpc_100ns()
    b = qpc_100ns()
    assert a > 0 and b >= a


def test_now_constructor_uses_current_clocks():
    clk = CaptureClock.now()
    assert clk.anchor_utc.tzinfo is timezone.utc
    assert clk.anchor_qpc_100ns > 0
