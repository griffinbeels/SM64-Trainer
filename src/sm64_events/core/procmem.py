"""Process memory + GC observability — the evidence layer for leak hunting.

WHY THIS EXISTS: a long-running session can grow RAM unboundedly with no way
to tell a true leak from OS file-cache pressure, because nothing sampled the
process. Two structural risks make a leak plausible and a monitor mandatory:
- replay/_gcwatch.py raises the gen-2 GC threshold to ~manual (cyclic garbage
  that reaches gen-2 is reclaimed only when something explicitly collects);
- the capture hot path allocates ~8 MB frame buffers at a high rate.
This module samples RSS (Windows working set), GC generation state, and the
scratch-buffer size, surfaces them on /health, and logs them on a cadence so
the NEXT incident leaves a trail instead of a mystery.

No third-party deps: RSS comes from psapi via ctypes (the codebase's existing
Windows idiom). On non-Windows / probe failure, rss_bytes() returns 0 and the
rest of the surface still works — the monitor degrades, never crashes."""
import ctypes
import gc
import logging
import os
from ctypes import wintypes
from pathlib import Path

log = logging.getLogger("sm64.procmem")

_GiB = 1024 ** 3


class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]


def _bind_gpmi():
    """Bind psapi!GetProcessMemoryInfo with explicit argtypes — WITHOUT them
    ctypes defaults pointer args to c_int and truncates them to 32 bits on
    64-bit Python, so byref() writes nowhere and the call silently no-ops
    (the bug that made an earlier probe always read 0)."""
    try:
        fn = ctypes.windll.psapi.GetProcessMemoryInfo
        fn.argtypes = [wintypes.HANDLE,
                       ctypes.POINTER(_PROCESS_MEMORY_COUNTERS), wintypes.DWORD]
        fn.restype = wintypes.BOOL
        getcur = ctypes.windll.kernel32.GetCurrentProcess
        getcur.restype = wintypes.HANDLE
        return fn, getcur
    except Exception:  # non-Windows or missing psapi
        return None, None


_GPMI, _GETCUR = _bind_gpmi()


def rss_bytes() -> int:
    """Current process working set (resident set) in bytes, or 0 if the
    platform can't report it."""
    if _GPMI is None:
        return 0
    counters = _PROCESS_MEMORY_COUNTERS()
    counters.cb = ctypes.sizeof(counters)
    if _GPMI(_GETCUR(), ctypes.byref(counters), counters.cb):
        return int(counters.WorkingSetSize)
    return 0


def dir_size_bytes(path: Path) -> int:
    """Sum of regular-file sizes directly under `path` (one level — the
    scratch buffer is flat). Tolerant of files vanishing mid-scan (the
    recorder deletes discarded segments concurrently)."""
    total = 0
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_file():
                        total += entry.stat().st_size
                except OSError:
                    continue  # raced with an eviction unlink
    except (FileNotFoundError, NotADirectoryError):
        return 0
    return total


def gc_summary() -> dict:
    """Cheap GC state (no full heap walk). `counts` are the per-generation
    allocation counters; a gen-2 count pinned near 0 with a huge threshold is
    the _gcwatch fingerprint (gen-2 effectively never auto-collects)."""
    counts = gc.get_count()
    threshold = gc.get_threshold()
    return {"counts": list(counts), "threshold": list(threshold),
            "frozen": gc.get_freeze_count()}


def sample(scratch_dir: Path | None = None, *, count_objects: bool = False) -> dict:
    """One observability snapshot. `count_objects` walks the whole heap
    (len(gc.get_objects())) — the truest 'is the object graph growing' signal
    but O(heap); the periodic monitor sets it, on-demand /health does not."""
    snap = {"rss_bytes": rss_bytes(), "gc": gc_summary()}
    if count_objects:
        snap["objects"] = len(gc.get_objects())
    if scratch_dir is not None:
        snap["scratch_bytes"] = dir_size_bytes(scratch_dir)
    return snap


def assess_growth(baseline_rss: int, current_rss: int, *,
                  warn_ratio: float = 2.0,
                  warn_floor_bytes: int = 2 * _GiB) -> str | None:
    """Pure leak-alarm decision (unit-tested). Warn only when BOTH the
    process has at least doubled vs its post-startup baseline AND it now
    exceeds an absolute floor — so a tiny baseline doubling to still-tiny
    doesn't cry wolf, and a genuinely large working set does. Returns the
    warning text or None."""
    if baseline_rss <= 0 or current_rss <= 0:
        return None
    if current_rss >= warn_floor_bytes and current_rss >= baseline_rss * warn_ratio:
        return (f"RSS {current_rss / _GiB:.2f} GiB is "
                f"{current_rss / baseline_rss:.1f}x the startup baseline "
                f"{baseline_rss / _GiB:.2f} GiB — possible leak; check the "
                f"gc/objects trend in this log")
    return None


class MemoryMonitor:
    """Periodic sampler: logs RSS / object count / GC / scratch size every
    `interval_s` and warns once when growth trips assess_growth. Baseline is
    the first sample (taken after startup + gc.freeze()). `latest` backs the
    /health surface. Runs as an asyncio task so it needs no extra thread."""

    def __init__(self, scratch_dir: Path | None = None, interval_s: float = 60.0):
        self._scratch_dir = scratch_dir
        self._interval_s = interval_s
        self._baseline_rss = 0
        self._warned = False
        self.latest: dict = {}

    async def run(self) -> None:
        import asyncio
        while True:
            self.latest = sample(self._scratch_dir, count_objects=True)
            rss = self.latest["rss_bytes"]
            if self._baseline_rss == 0 and rss > 0:
                self._baseline_rss = rss
            scratch = self.latest.get("scratch_bytes", 0)
            log.info("mem: rss=%.0f MiB objects=%d gc_counts=%s scratch=%.0f MiB",
                     rss / 1024**2, self.latest.get("objects", -1),
                     self.latest["gc"]["counts"], scratch / 1024**2)
            warning = assess_growth(self._baseline_rss, rss)
            if warning and not self._warned:
                log.warning("memory growth alarm: %s", warning)
                self._warned = True
            await asyncio.sleep(self._interval_s)
