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


@dataclass(frozen=True)
class SegmentInfo:
    path: Path
    kind: str               # "video" | "audio"
    utc_start: datetime
    utc_end: datetime
    size_bytes: int


class SegmentRing:
    def __init__(self, retention_s: float | None, max_bytes: int):
        self._retention_s = retention_s
        self._max_bytes = max_bytes
        self._segments: deque[SegmentInfo] = deque()
        self._total_bytes = 0
        self._lock = threading.Lock()  # 1 encoder writer, N API reader threads

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

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
        while self._segments and self._total_bytes > self._max_bytes:
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
