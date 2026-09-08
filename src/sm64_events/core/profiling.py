"""Opt-in, bounded wall-time instrumentation; never writes from a hot path.

Durations are inclusive (nested stages must not be summed). Quantiles are
histogram bucket upper bounds, not exact samples. Disabled wrappers only test
a boolean; active sessions expire even if their client disappears.
"""
import math
import os
import threading
import time
import uuid
from bisect import bisect_left
from datetime import datetime, timezone
from functools import wraps
from inspect import iscoroutinefunction


BUCKETS_MS = (.01, .025, .05, .1, .25, .5, 1, 2, 4, 8, 16, 33.334,
              50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 300000)
MAX_STAGES = 96


class Profiler:
    def __init__(self, clock=time.perf_counter):
        self._clock = clock
        self._lock = threading.Lock()
        self.enabled = False
        self.session_id = None
        self._start = self._end = self._deadline = 0.0
        self._started_utc = None
        self._stages = {}
        self._pending = {}
        self._outside_window = 0
        self._overflow = 0

    def active(self):
        if not self.enabled:
            return False
        if self._clock() >= self._deadline:
            with self._lock:
                if self.enabled and self._clock() >= self._deadline:
                    self.enabled = False
                    self._end = self._deadline
        return self.enabled

    def start(self, duration_s=60):
        if (isinstance(duration_s, bool) or not isinstance(duration_s, (int, float))
                or not math.isfinite(duration_s) or not 1 <= duration_s <= 300):
            raise ValueError("duration_s must be between 1 and 300 seconds")
        self.active()
        with self._lock:
            if self.enabled:
                raise RuntimeError("a profiling session is already active")
            self.session_id = uuid.uuid4().hex
            self._start = self._clock()
            self._deadline = self._start + duration_s
            self._end = self._deadline
            self._started_utc = datetime.now(timezone.utc).isoformat()
            self._stages = {}
            self._pending = {}
            self._outside_window = 0
            self._overflow = 0
            self.enabled = True
        return self.snapshot()

    def stop(self, session_id):
        self.active()
        with self._lock:
            if not session_id or session_id != self.session_id:
                raise ValueError("profiling session does not match")
            if self.enabled:
                self._end = self._clock()
                self.enabled = False
        return self.snapshot()

    def begin(self, name=None):
        if not self.active():
            return None
        with self._lock:
            if not self.enabled or self._clock() >= self._deadline:
                return None
            if name is not None:
                if name not in self._pending and len(self._pending) >= MAX_STAGES:
                    self._overflow += 1
                    return None
                self._pending[name] = self._pending.get(name, 0) + 1
            return self.session_id, self._clock(), name

    def finish(self, name, token, *, error=False):
        if token is not None:
            with self._lock:
                if token[0] != self.session_id:
                    return
                now = self._clock()
                if len(token) > 2 and token[2] is not None:
                    self._pending[token[2]] -= 1
                if not self.enabled or now >= self._deadline:
                    self._outside_window += 1
                    return
                self._record_locked(name, (now - token[1]) * 1000, error)

    def record(self, name, duration_ms, *, session_id, error=False):
        if (not self.active() or not math.isfinite(duration_ms)
                or duration_ms < 0):
            return
        with self._lock:
            if (not self.enabled or session_id != self.session_id
                    or self._clock() >= self._deadline):
                return
            self._record_locked(name, duration_ms, error)

    def _record_locked(self, name, duration_ms, error):
        if name not in self._stages:
            if len(self._stages) >= MAX_STAGES:
                self._overflow += 1
                return
            self._stages[name] = [0, 0., math.inf, 0., 0,
                                  [0] * (len(BUCKETS_MS) + 1)]
        row = self._stages[name]
        row[0] += 1
        row[1] += duration_ms
        row[2] = min(row[2], duration_ms)
        row[3] = max(row[3], duration_ms)
        row[4] += int(error)
        row[5][bisect_left(BUCKETS_MS, duration_ms)] += 1

    def snapshot(self):
        self.active()
        with self._lock:
            return {"version": 1, "enabled": self.enabled,
                    "process_id": os.getpid(),
                    "session_id": self.session_id, "started_utc": self._started_utc,
                    "elapsed_s": max(0., (self._clock() if self.enabled else self._end)
                                     - self._start),
                    "duration_s": self._deadline - self._start,
                    "semantics": "inclusive_wall_time",
                    "quantile_method": "histogram_upper_bound",
                    "buckets_ms": list(BUCKETS_MS),
                    "stages": {name: _summary(row) for name, row in self._stages.items()},
                    "pending_calls": {name: count for name, count in self._pending.items() if count},
                    "counters": {"stage_limit_rejections": self._overflow,
                                 "completed_outside_window": self._outside_window}}


def _summary(row):
    count, total, minimum, maximum, errors, buckets = row

    def quantile(q):
        target, cumulative = math.ceil(count * q), 0
        for index, size in enumerate(buckets):
            cumulative += size
            if cumulative >= target:
                return BUCKETS_MS[index] if index < len(BUCKETS_MS) else maximum
        return None

    return dict(count=count, total_ms=total, min_ms=minimum, max_ms=maximum,
                mean_ms=total / count, errors=errors, bucket_counts=list(buckets),
                p50_ms=quantile(.5), p95_ms=quantile(.95), p99_ms=quantile(.99))


profile = Profiler()


def measured(name, *, interval=False):
    """Measure a synchronous/async operation without changing its result/errors.

Optional start-to-start intervals are per thread and per session; they include
scheduling delay, unlike the operation duration. No game clock is modified.
"""
    previous = threading.local()

    def begin():
        token = profile.begin(name)
        if token and interval:
            last = getattr(previous, "token", None)
            if last and last[0] == token[0]:
                profile.record(name + ".interval", (token[1] - last[1]) * 1000,
                               session_id=token[0])
            previous.token = token
        return token

    def decorate(fn):
        if iscoroutinefunction(fn):
            @wraps(fn)
            async def asynchronous(*args, **kwargs):
                if not profile.enabled:
                    return await fn(*args, **kwargs)
                token, failed = begin(), True
                try:
                    result = await fn(*args, **kwargs)
                    failed = False
                    return result
                finally:
                    profile.finish(name, token, error=failed)
            return asynchronous

        @wraps(fn)
        def synchronous(*args, **kwargs):
            if not profile.enabled:
                return fn(*args, **kwargs)
            token, failed = begin(), True
            try:
                result = fn(*args, **kwargs)
                failed = False
                return result
            finally:
                profile.finish(name, token, error=failed)
        return synchronous
    return decorate
