"""Disk-backed replay ring: a deque of segment files with two eviction
rules — retention age (None = keep the whole session) and a byte cap that
applies regardless (the disk guard from the spec). Video segments and audio
PCM chunks share one ring and one eviction policy so A/V coverage stays
aligned. Eviction deletes files; missing files are tolerated (crash debris
is cleaned by the recorder at startup). Thread-safe: one encoder writer
calls add(); N FastAPI reader threads call covering()/coverage() — a single
Lock serialises all mutations and iterations."""
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

# Free disk we refuse to consume: a near-full system volume thrashes the whole
# machine (Windows squeezes the pagefile), which reads as the same "everything
# is laggy / out of memory" symptom as a RAM leak. The buffer never grows so
# large that free space would drop below this.
_DISK_MARGIN_BYTES = 5 * 1024 ** 3


def effective_cap(configured_cap: int, free_bytes: int, current_total: int,
                  *, margin_bytes: int = _DISK_MARGIN_BYTES) -> int:
    """The byte cap actually enforced: the configured cap, but never so large
    that free disk would fall below margin_bytes. free_bytes is space free NOT
    counting our buffer; on top of what we already hold we may grow into
    (free - margin), and when free has ALREADY dropped below the margin that
    term is negative — the cap falls below current_total so eviction reclaims
    the deficit (a disk that filled under us shrinks the buffer back). Pure —
    unit-tested."""
    return min(configured_cap, current_total + (free_bytes - margin_bytes))


@dataclass(frozen=True)
class SegmentInfo:
    path: Path
    kind: str               # "video" | "audio"
    utc_start: datetime
    utc_end: datetime
    size_bytes: int


class SegmentRing:
    def __init__(self, retention_s: float | None, max_bytes: int,
                 free_bytes_fn=None,
                 disk_margin_bytes: int = _DISK_MARGIN_BYTES):
        self._retention_s = retention_s
        self._max_bytes = max_bytes
        # free_bytes_fn() -> bytes free on the scratch volume (None = no disk
        # gating, e.g. unit tests). When set, eviction also caps the buffer so
        # free disk can't drop below disk_margin_bytes regardless of max_bytes.
        self._free_bytes_fn = free_bytes_fn
        self._disk_margin = disk_margin_bytes
        self._segments: deque[SegmentInfo] = deque()
        self._total_bytes = 0
        self._lock = threading.Lock()  # 1 encoder writer, N API reader threads

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

    @property
    def retention_s(self) -> float | None:
        return self._retention_s

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    def set_limits(self, retention_s: float | None, max_bytes: int) -> None:
        """Live-apply new eviction limits (the UI settings panel) and evict
        immediately — a user who just shrank the cap expects disk to free
        now, not when the next segment lands."""
        with self._lock:
            self._retention_s = retention_s
            self._max_bytes = max_bytes
            if self._segments:
                self._evict(now=self._segments[-1].utc_end)

    def add(self, seg: SegmentInfo) -> None:
        """Assumes utc_end is monotonically non-decreasing across add() calls
        (one writer thread, segments emitted in stream order)."""
        with self._lock:
            self._segments.append(seg)
            self._total_bytes += seg.size_bytes
            self._evict(now=seg.utc_end)

    def _evict(self, now: datetime) -> None:
        # caller holds self._lock
        def drop_head():
            old = self._segments.popleft()
            self._total_bytes -= old.size_bytes
            old.path.unlink(missing_ok=True)

        if self._retention_s is not None:
            horizon = now - timedelta(seconds=self._retention_s)
            while self._segments and self._segments[0].utc_end <= horizon:
                drop_head()
        cap = self._max_bytes
        if self._free_bytes_fn is not None:
            try:
                free = self._free_bytes_fn()
            except OSError:
                free = None  # scratch volume not ready/gone — fall back to cap
            if free is not None:
                cap = effective_cap(self._max_bytes, free, self._total_bytes,
                                    margin_bytes=self._disk_margin)
        while self._segments and self._total_bytes > cap:
            drop_head()

    def covering(self, kind: str, start: datetime, end: datetime) -> list[SegmentInfo]:
        with self._lock:
            return [s for s in self._segments
                    if s.kind == kind and s.utc_end > start and s.utc_start < end]

    def coverage(self, kind: str) -> tuple[datetime, datetime] | None:
        with self._lock:
            ks = [s for s in self._segments if s.kind == kind]
            if not ks:
                return None
            return ks[0].utc_start, ks[-1].utc_end
