"""Bound unsaved history by completed attempts; never do file work on polling."""
from collections import OrderedDict
from datetime import datetime, timedelta
import threading


def utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class AttemptHistory:
    MAX_ATTEMPTS = 1000

    def __init__(self, count=10):
        self._lock = threading.Lock()
        self.count = count
        self._completed = OrderedDict()
        self._active_start = None
        self._recent = set()
        self._oldest_recent = None
        self._expired_through = -1

    def observe(self, completed, active_start, *, replace=False):
        # Called after committed live projection, never historical broadcasts.
        with self._lock:
            if replace:
                self._completed.clear()
            self._active_start = utc(active_start) if active_start else None
            for attempt in completed:
                self._completed[attempt.id] = (utc(attempt.started_utc), utc(attempt.ended_utc))
            if completed:
                # Corrections/reprojection can deliver older completions late.
                self._completed = OrderedDict(sorted(self._completed.items(),
                                                     key=lambda item: (item[1][1], item[0])))
            while len(self._completed) > self.MAX_ATTEMPTS:
                identity, _ = self._completed.popitem(last=False)
                self._expired_through = max(identity, self._expired_through)
            if completed or replace:
                self._refresh()

    def _refresh(self):
        recent = list(self._completed.items())[-self.count:] if self.count else []
        self._recent = {identity for identity, _ in recent}
        self._oldest_recent = min((row[0] for _, row in recent), default=None)

    def configure(self, count):
        with self._lock:
            self.count = count
            self._refresh()

    def clear(self):
        with self._lock:
            self._completed.clear()
            self._active_start = None
            self._expired_through = -1
            self._refresh()

    def cutoff(self, pre_pad_s):
        with self._lock:
            if self._oldest_recent is None:
                return None
            start = self._oldest_recent
            if self._active_start is not None:
                start = min(start, self._active_start)
            return start - timedelta(seconds=pre_pad_s)

    def allows(self, attempt_id):
        with self._lock:
            if self.count is None:
                return True
            if attempt_id not in self._completed:
                return attempt_id > self._expired_through
            return attempt_id in self._recent
