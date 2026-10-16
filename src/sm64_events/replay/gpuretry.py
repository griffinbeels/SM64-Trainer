"""Space failed initialization across recorder reattachments to one producer.

Transient failures retry with capped exponential backoff after proved cleanup.
A capability bit does not reset the backoff. No GPU/native lease work occurs
while waiting, and a failed request is never resubmitted into its old epoch.
Native resource exhaustion is an exception: it persists until a new producer
process birth, even across an observed ROM reload. So does an adapter that can
never record (gpucapture.GpuBackendUnsupported), which is not a failure at all:
it arms no cooldown, and discovery declines that producer from then on.
"""

import threading
import time
import re

from sm64_events.replay.capturecontrol import CaptureControl, CLOSED, CAP_GPU


def producer_key(status):
    return (
        status.producer_pid,
        status.producer_created_lo,
        status.producer_created_hi,
        status.generation,
        status.build_id,
    )


class RetryGate:
    def __init__(
        self,
        *,
        control_factory=CaptureControl,
        clock=time.monotonic,
        cooldown=10.0,
        attempts=2,
        max_cooldown=60.0,
    ):
        self.control_factory, self.clock = control_factory, clock
        self.cooldown, self.attempts = cooldown, attempts
        self.max_cooldown = max(cooldown, max_cooldown)
        self._lock = threading.Lock()
        self._identity = None
        self._failures = 0
        self._until = 0.0
        self._reason = ""
        self._saw_closed = False
        self._exhausted_birth = None
        self._unsupported_birth = None
        self._unsupported_reason = None

    def observe(self, status):
        identity = producer_key(status)
        with self._lock:
            if self._exhausted_birth != (identity[0], identity[1], identity[2]):
                self._exhausted_birth = None
            if identity != self._identity or (self._saw_closed and status.rom_open):
                self._identity = identity
                self._failures, self._until, self._reason = 0, 0.0, ""
            self._saw_closed = not status.rom_open
        return identity

    def failed(self, identity, reason):
        with self._lock:
            if identity != self._identity:
                return  # A retired producer cannot spend its successor's budget.
            self._failures = min(self._failures + 1, 32)
            # This is only called after the old session's ownership closes.
            # A short productive prefix is not enough to waive the cooldown:
            # repeatedly opening, writing one packet and failing must not churn
            # (tests/test_replay_auto_recovery.py owns the back-off).
            delay = min(self.max_cooldown,
                        self.cooldown * 2 ** min(self._failures - 1, 16))
            self._until = self.clock() + delay
            self._reason = str(reason)[:512]
            if re.search(r"native_capture_ended:3:9(?!\d)", self._reason):
                # Native delivery exhaustion is process-lifetime sticky. A ROM
                # reload or a new request cannot prove those resources retired.
                self._exhausted_birth = identity[:3]

    def unsupported(self, identity, reason):
        """This producer's adapter can never record: remember it for its birth."""
        with self._lock:
            if identity is None or identity != self._identity:
                return
            self._unsupported_birth = identity[:3]
            self._unsupported_reason = str(reason)[:512]

    def unsupported_for(self, identity):
        """The reason this producer cannot record, or None."""
        with self._lock:
            if identity is not None and self._unsupported_birth == tuple(identity[:3]):
                return self._unsupported_reason
        return None

    def identity_for(self, pid):
        """A temporarily unreadable control page cannot select a raw fallback."""
        with self._lock:
            if self._identity is not None and self._identity[0] == pid:
                return self._identity
        return None

    def blocked(self, identity):
        with self._lock:
            if identity != self._identity:
                return "producer changed"
            if self._exhausted_birth == identity[:3]:
                return "GPU recording unavailable: fully close and reopen Project64; native resources exhausted."
            if self.clock() < self._until:
                repeated = " after repeated failures" if self._failures >= self.attempts else ""
                return "GPU recording retry cooling down" + repeated + ". " + self._reason
        return None

    def wait(self, owner, identity):
        while owner.want_capture():
            if self.unsupported_for(identity):
                return False
            reason = self.blocked(identity)
            # This is the media worker, after its prior helper/lease were closed.
            # Revalidate even an unblocked cached identity before any request.
            # A missing/busy page only waits; it cannot create GPU resources or
            # switch a previously known GPU producer to raw desktop capture.
            try:
                with self.control_factory() as control:
                    status = control.status()
                if status.state == CLOSED or producer_key(status) != identity:
                    return False
                self.observe(status)
                if status.capabilities & CAP_GPU and status.rom_open:
                    reason = self.blocked(identity)
                    if reason is None:
                        return True
                else:
                    reason = reason or "Waiting for the GPU capture producer and ROM."
            except (FileNotFoundError, OSError, BlockingIOError):
                reason = reason or "Waiting for the GPU capture control page."
            owner.report_wait(reason)
            owner.wait(0.5)
        return False
