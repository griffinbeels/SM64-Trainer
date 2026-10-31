"""Detect sustained missing operational progress from cached facts only.

This is not visual recognition or a gameplay telemetry stream. Ordinary waits
withdraw the expectation; reports use the existing bounded Sentry destination.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import math
import threading
import time

from sm64_events.core import error_reporting

log = logging.getLogger("sm64.diagnostics")
MESSAGES = {
    "recording": "Gameplay recording made no picture progress",
    "ghost": "Expected ghost playback has no display receipt",
    "setup": "Active setup verification made no progress",
}


#: How long an expectation must go without progress before it is a failure.
GRACE_S = 20.0


def ghost_display_missing(ghost: dict) -> bool:
    """The renderer was asked to show ghosts and has not displayed them:
    the one predicate the report watch and the practice card's setup notice
    share (the watch adds player activity and freshness on top)."""
    return (ghost.get("expected_display") is True and ghost.get("available") is True
            and (ghost.get("awaiting_preparation") is True or ghost.get("display_pending") is True)
            and ghost.get("displayed") is False)


@dataclass(frozen=True)
class Pending:
    scope: object
    progress: object
    since: float


class SilentFailureMonitor:
    def __init__(self, probe, *, reporter=error_reporting.capture_problem,
                 consent=error_reporting.collection_epoch, clock=time.monotonic,
                 wall_clock=time.time, interval_s=1.0, grace_s=GRACE_S, cooldown_s=3600.0):
        self._probe, self._reporter, self._consent = probe, reporter, consent
        self._clock, self._wall = clock, wall_clock
        self.interval_s, self.grace_s, self.cooldown_s = interval_s, grace_s, cooldown_s
        self._stop = threading.Event()
        self._thread = None
        self._finished = threading.Event()
        self._finish_lock = threading.Lock()
        self._deferred_close = None
        self._epoch = None
        self._previous = None
        self._pending = {}
        self._reported = {}

    def start(self):
        if self._thread is not None:
            raise RuntimeError("silent failure monitor already started")
        self._thread = threading.Thread(target=self._run, name="failure-progress-watch", daemon=True)
        try:
            self._thread.start()
        except RuntimeError:
            self._thread = None
            raise

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        return self._thread is None or not self._thread.is_alive()

    def close_after_stop(self, close):
        """Retain a passive snapshot if an in-flight report outlasts shutdown."""
        with self._finish_lock:
            finished = self._finished.is_set() or self._thread is None
            if not finished:
                self._deferred_close = close
        if finished:
            close()

    def _run(self):
        try:
            while not self._stop.wait(self.interval_s):
                try:
                    self.step()
                except Exception:  # noqa: BLE001 - optional reporting cannot stop practice or report recursively
                    self._previous = None
                    self._pending.clear()
                    log.debug("Silent failure observation unavailable.")
        finally:
            with self._finish_lock:
                self._finished.set()
                close = self._deferred_close
            if close is not None:
                close()

    def _moving(self, sample, now):
        game = sample.get("game") or {}
        frame = game.get("frame")
        current = (game.get("scope"), frame, now)
        old, self._previous = self._previous, current
        return (game.get("attached") is True and not game.get("paused") and not game.get("held")
                and type(frame) is int and old is not None and current[0] == old[0]
                and type(old[1]) is int and frame > old[1]
                and 0 < now - old[2] <= self.interval_s * 3)

    def _candidates(self, sample):
        capture, ghost, setup = (sample.get(name) or {} for name in ("capture", "ghost", "setup"))
        enabled = (capture.get("recording_enabled") is True and not capture.get("paused")
                   and not capture.get("gated") and not capture.get("storage_pressure"))
        recording = enabled and capture.get("player_active") is True
        ghost_expected = (capture.get("player_active") is True
                          and ghost_display_missing(ghost) and self._fresh(ghost))
        checks = setup.get("checks") or {}
        setup_expected = (recording and setup.get("pending") is True and self._fresh(setup)
                          and setup.get("installed") is True and setup.get("running") is True
                          and setup.get("rom_supported") is True and checks.get("game") is True
                          and any(checks.get(key) is False for key in ("plugin", "pictures", "inputs")))
        return {
            "recording": (capture.get("scope"), capture.get("pictures"), recording),
            "ghost": (ghost.get("scope"), ghost.get("progress"), ghost_expected),
            "setup": (setup.get("scope"), tuple(checks.get(k) for k in ("plugin", "pictures", "inputs")), setup_expected),
        }

    def _fresh(self, row):
        stamp = row.get("observed_unix_s")
        return (type(stamp) in (int, float) and math.isfinite(stamp)
                and 0 <= self._wall() - stamp <= self.interval_s * 3)

    def step(self):
        """One deterministic observation; only the worker calls it in production."""
        epoch = self._consent()
        if epoch != self._epoch:
            self._epoch, self._previous = epoch, None
            self._pending.clear()
            self._reported.clear()
        if epoch is None or self._stop.is_set():
            return
        sample = self._probe()
        now = self._clock()
        if not isinstance(sample, dict) or not self._moving(sample, now):
            self._pending.clear()
            return
        for name, (scope, progress, expected) in self._candidates(sample).items():
            if not expected:
                self._pending.pop(name, None)
                continue
            before = self._pending.get(name)
            if before is None or scope != before.scope or progress != before.progress:
                self._pending[name] = Pending(scope, progress, now)
                continue
            if (now - before.since < self.grace_s
                    or now - self._reported.get(name, -math.inf) < self.cooldown_s):
                continue
            if self._stop.is_set() or self._consent() != epoch:
                return
            self._reported[name] = now
            self._reporter(MESSAGES[name], expected_generation=epoch, context={
                "component": "silent_failure", "stage": name, "operation": "progress_watch",
                "stalled_for_s": min(3600, int(now - before.since)),
            })
