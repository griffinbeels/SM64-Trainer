"""QPC <-> UTC bridge — THE contract between event timestamps and the buffer.

WGC stamps every frame with Direct3D11CaptureFrame.SystemRelativeTime: QPC
time scaled to 100 ns ticks since boot. qpc_100ns() reproduces that exact
timebase (QueryPerformanceCounter / QueryPerformanceFrequency * 1e7), so one
(qpc, utc) pair captured at recorder start maps any frame timestamp to UTC
and any event UTC into the stream. Capture the pair ONCE per recording run;
re-anchoring mid-run would shear A/V against event timestamps."""
import ctypes
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


def _qpc_frequency() -> int:
    freq = ctypes.c_int64()
    ctypes.windll.kernel32.QueryPerformanceFrequency(ctypes.byref(freq))
    return freq.value


_FREQ = _qpc_frequency()


def qpc_100ns() -> int:
    count = ctypes.c_int64()
    ctypes.windll.kernel32.QueryPerformanceCounter(ctypes.byref(count))
    return count.value * 10_000_000 // _FREQ


@dataclass(frozen=True)
class CaptureClock:
    anchor_qpc_100ns: int
    anchor_utc: datetime  # tz-aware UTC

    @classmethod
    def now(cls) -> "CaptureClock":
        return cls(qpc_100ns(), datetime.now(timezone.utc))

    def utc_of(self, ts_100ns: int) -> datetime:
        return self.anchor_utc + timedelta(
            microseconds=(ts_100ns - self.anchor_qpc_100ns) // 10)

    def seconds_since_anchor(self, ts_100ns: int) -> float:
        return (ts_100ns - self.anchor_qpc_100ns) / 1e7

    def utc_to_seconds(self, t: datetime) -> float:
        return (t - self.anchor_utc).total_seconds()
