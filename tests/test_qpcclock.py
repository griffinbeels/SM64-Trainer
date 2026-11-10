"""Clock epoch checked against Windows itself, without a native source or SDK."""
import ctypes
from ctypes import wintypes
import sys

import pytest

from ghost_native_fixture import frame, producer
from sm64_events.ghost.capture import NativeCapture
from sm64_events.memory.layout import US
from sm64_events.multiplayer.source_handoff import SourceHandoff


@pytest.fixture
def native_qpc_ns():
    if sys.platform != "win32":
        pytest.skip("raw Windows QueryPerformanceCounter clock witness")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    for name in ("QueryPerformanceFrequency", "QueryPerformanceCounter"):
        method = getattr(kernel, name)
        method.argtypes = [ctypes.POINTER(ctypes.c_longlong)]
        method.restype = wintypes.BOOL
    frequency = ctypes.c_longlong()
    assert kernel.QueryPerformanceFrequency(ctypes.byref(frequency)) and frequency.value > 0

    def read():
        counter = ctypes.c_longlong()
        assert kernel.QueryPerformanceCounter(ctypes.byref(counter))
        return counter.value * 1_000_000_000 // frequency.value

    return read


def test_shared_clock_is_bracketed_by_raw_windows_qpc(native_qpc_ns):
    from sm64_events.core.qpcclock import qpc_ns

    for _ in range(20):
        before = native_qpc_ns()
        value = qpc_ns()
        after = native_qpc_ns()
        assert before <= value <= after, (before, value, after)


def test_native_capture_receipts_use_the_raw_qpc_epoch(native_qpc_ns):
    receipts = []

    class Observed(SourceHandoff):
        def observe(self, *args, **kwargs):
            receipts.append(kwargs["received_ns"])
            return super().observe(*args, **kwargs)

    live = Observed(bytes(range(16)), stale_after_ns=1_000_000_000)
    source = NativeCapture(US, lambda *_: None, producer_pid=123, live_source=live)
    source._adopt_live_source(live, None, source._live_mount)
    try:
        before = native_qpc_ns()
        source._publish_live(producer(rom_layout=1), (frame(1),))
        after = native_qpc_ns()
        publication = live.take_latest(now_ns=receipts[0])
        assert publication is not None
        assert before <= publication.received_ns <= after, (before, publication.received_ns, after)
        assert live.current(publication, now_ns=after)
    finally:
        source._end_live_source()
