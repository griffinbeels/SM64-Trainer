"""Optional native capture timings; independent of the frame/pad stream ABI.

The 2,864-byte mapping is specified in plugin/gfxwrap/profile.h. Only request
and heartbeat belong to this reader. The plugin owns every other byte, with
a sequence lock around each UpdateScreen. Histograms are cumulative within
one profiling session. Durations are CPU wall time (including driver waits),
not GPU execution time. VI intervals are CALL intervals, not display FPS.
"""
from __future__ import annotations

import hashlib
import math
import mmap
import struct
import threading

MAGIC = b"SM64PRF1"
VERSION = 1
SUFFIX = "_profile_v1"
SIZE = 2864
STAGES = ("update_screen", "wrapped_update_screen", "capture", "gl_setup",
          "gl_read_pixels", "gl_restore", "wrapped_read_screen", "copy_and_free",
          "publish", "vi_call_interval")
_U32 = struct.Struct("<I")
_METRIC = struct.Struct("<35Q")


def _percentile_bound(buckets: tuple, count: int, quantile: float) -> float | None:
    if not count:
        return None
    target, seen = math.ceil(count * quantile), 0
    for index, value in enumerate(buckets):
        seen += value
        if seen >= target:
            return (2 ** index) / 1000 if index < 31 else None
    return None


def decode_snapshot(raw: bytes, pid: int, generation: int) -> dict | None:
    """Reject absent/old/torn writers. Percentiles are bucket upper bounds."""
    if len(raw) != SIZE or raw[:8] != MAGIC:
        return None
    version, writer, sequence, captured = struct.unpack_from("<4I", raw, 8)
    frequency, = struct.unpack_from("<q", raw, 24)
    producer_qpc, = struct.unpack_from("<q", raw, 40)
    if (version != VERSION or writer != pid or sequence % 2
            or captured != generation or not generation or frequency <= 0 or producer_qpc <= 0):
        return None
    metrics = {}
    for index, name in enumerate(STAGES):
        count, total, maximum, *buckets = _METRIC.unpack_from(raw, 64 + index * _METRIC.size)
        if sum(buckets) != count:
            return None
        metrics[name] = {
            "count": count, "total_ms": total * 1000 / frequency,
            "mean_ms": total * 1000 / frequency / count if count else None,
            "max_ms": maximum * 1000 / frequency if count else None,
            "p50_upper_ms": _percentile_bound(buckets, count, .50),
            "p95_upper_ms": _percentile_bound(buckets, count, .95),
            "p99_upper_ms": _percentile_bound(buckets, count, .99),
            "histogram_counts": buckets,
        }
    return {"version": version, "plugin_pid": writer, "generation": captured,
            "producer_instance": producer_qpc,
            "clock": "cpu_wall", "percentiles": "log2_us_bucket_upper_bounds",
            "metrics": metrics}


class GraphicsProfile:
    """One recorder owner controls the separate lease; never changes capture demand.

    A stale heartbeat disables native measurement within three seconds. Native
    sessions also have a hard five-minute cap, independent of server liveness.
    Missing older DLL support is explicit None, never a zero-duration result.
    """

    def __init__(self, name: str):
        self._name = name
        self._map = None
        self._generation = 0
        self._lock = threading.Lock()
        self._closed = False

    def refresh(self, session_id: str | None, stop_event: threading.Event | None = None) -> None:
        with self._lock:
            if self._closed:
                return
            if stop_event is not None and stop_event.is_set():
                session_id = None
            if session_id is None:
                if self._map is not None:
                    _U32.pack_into(self._map, 32, 0)
                return
            if self._map is None:
                self._map = mmap.mmap(-1, SIZE, tagname=self._name + SUFFIX)
            self._generation = int.from_bytes(
                hashlib.sha256(session_id.encode()).digest()[:4], "little") or 1
            heartbeat, = _U32.unpack_from(self._map, 36)
            _U32.pack_into(self._map, 36, (heartbeat + 1) & 0xFFFFFFFF or 1)
            _U32.pack_into(self._map, 32, self._generation)

    def snapshot(self, pid: int) -> dict | None:
        with self._lock:
            if self._map is None:
                return None
            # Bounded retries: a hung GPU/driver keeps the writer odd, so no
            # statistics can be certified while that hook remains in flight.
            for _ in range(3):
                before, = _U32.unpack_from(self._map, 16)
                if before % 2:
                    continue
                raw = self._map[:]
                after, = _U32.unpack_from(self._map, 16)
                if before == after:
                    return decode_snapshot(raw, pid, self._generation)
            return None

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._map is not None:
                _U32.pack_into(self._map, 32, 0)
                self._map.close()
                self._map = None
