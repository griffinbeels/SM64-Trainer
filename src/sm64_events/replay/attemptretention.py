"""Bound unsaved history by completed attempts; never do file work on polling.

The window keeps the newest completed attempts while BOTH limits hold: the
attempt count and the total footage they span. Griffin, 2026-11-02: "considering
EITHER the total memory size OR the attempt count. If someone is just rapid fire
doing attempts / resetting, then, well, the buffer will stay pretty small."
A reset is an attempt, on purpose. The newest attempt always stays, however long.

One play can close two attempts over the same footage: a star attempt and a
segment that ended on the same grab (saved clips 35266 and 710000035266). They
are one entry in the window and their shared time counts once. Otherwise a
six-minute run would count as twelve and expire its own star attempt at once.
"""
from collections import OrderedDict
from datetime import datetime, timedelta
import threading

from sm64_events.replay.config import DEFAULT_RETENTION_ATTEMPTS, DEFAULT_RETENTION_FOOTAGE_S


def utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class AttemptHistory:
    MAX_ATTEMPTS = 1000

    def __init__(self, count=DEFAULT_RETENTION_ATTEMPTS, footage_s=DEFAULT_RETENTION_FOOTAGE_S):
        self._lock = threading.Lock()
        self.count = count
        self.footage_s = footage_s
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
        self._recent, self._oldest_recent = set(), None
        if not self.count:  # None: another mode owns retention
            return
        entries, footage = 0, 0.0
        for identity, (start, end) in reversed(self._completed.items()):  # newest first
            oldest = self._oldest_recent
            separate = oldest is None or end <= oldest
            entries += separate
            added = ((end - start) if separate else (oldest - start)).total_seconds()
            added = max(added, 0.0)
            footage += added
            if self._recent and (entries > self.count or (added and footage > self.footage_s)):
                break
            self._recent.add(identity)
            self._oldest_recent = start if oldest is None else min(oldest, start)

    def configure(self, count, footage_s=None):
        with self._lock:
            self.count = count
            if footage_s is not None:
                self.footage_s = footage_s
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
