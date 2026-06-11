"""Disk-backed replay ring: a deque of segment files with two eviction
rules — retention age (None = keep the whole session) and a byte cap that
applies regardless (the disk guard from the spec). Video segments and audio
PCM chunks share one ring and one eviction policy so A/V coverage stays
aligned. Eviction deletes files; missing files are tolerated (crash debris
is cleaned by the recorder at startup)."""
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
        self.total_bytes = 0

    def add(self, seg: SegmentInfo) -> None:
        self._segments.append(seg)
        self.total_bytes += seg.size_bytes
        self._evict(now=seg.utc_end)

    def _evict(self, now: datetime) -> None:
        def drop_head():
            old = self._segments.popleft()
            self.total_bytes -= old.size_bytes
            old.path.unlink(missing_ok=True)

        if self._retention_s is not None:
            horizon = now - timedelta(seconds=self._retention_s)
            while self._segments and self._segments[0].utc_end <= horizon:
                drop_head()
        while self._segments and self.total_bytes > self._max_bytes:
            drop_head()

    def covering(self, kind: str, start: datetime, end: datetime) -> list[SegmentInfo]:
        return [s for s in self._segments
                if s.kind == kind and s.utc_end > start and s.utc_start < end]

    def coverage(self, kind: str) -> tuple[datetime, datetime] | None:
        ks = [s for s in self._segments if s.kind == kind]
        if not ks:
            return None
        return ks[0].utc_start, ks[-1].utc_end
