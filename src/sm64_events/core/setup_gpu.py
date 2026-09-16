"""Read-only GPU setup evidence, separate from legacy pixel-stream headers.

ControlV1 acknowledges the recorder lease, not rendered frames. Active capture
requires recent acknowledgement AND actual selected-picture receipt movement.
Intentional idle revokes that lease: its passive control worker sleeps. Only
then may a positive receipt survive, tied to the same live producer/request;
SetupRuntime still verifies the loaded ROM and fresh game/input observations.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import threading
import time

from sm64_events.core.onboarding import CounterWindow
from sm64_events.replay.capturecontrol import ACTIVE, CAP_GPU, PASSIVE, CaptureControl
from sm64_events.replay.ownedclose import CleanupPending, retain

log = logging.getLogger("sm64.replay")


def read_gpu_control():
    """Validate PID birth through CaptureControl; never acquire or renew demand."""
    with CaptureControl() as control:
        return control.status()


def _receipt(recorder):
    health = recorder.get("frame_source_health") or {}
    if health.get("kind") != "gpu":
        return None, None
    receipt = health.get("capture_receipt") or {}
    identity = tuple(receipt.get(name) for name in (
        "producer_pid", "producer_birth", "control_generation", "token", "source_epoch"))
    delivered = receipt.get("delivered")
    if (any(type(value) is not int or value <= 0 for value in identity)
            or type(delivered) is not int or delivered < 0):
        return None, None
    return identity, delivered


@dataclass(frozen=True)
class GpuSetupEvidence:
    """A bounded server observation of the GPU route's control page."""
    producer_pid: int | None
    identity: tuple | None
    delivered: int | None
    idle: bool
    alive: bool
    pictures: bool

    def matches(self, recorder) -> bool:
        identity, delivered = _receipt(recorder)
        return bool(
            recorder.get("recording") and recorder.get("frame_source") == "plugin"
            and bool(recorder.get("idle")) == self.idle
            and identity is not None and identity == self.identity
            and delivered is not None and self.delivered is not None
            and delivered >= self.delivered)


class GpuSetupProbe:
    """One fixed movement window per control/request, shared by setup readers."""
    def __init__(self, recorder_status, control_status=read_gpu_control, *, clock=time.monotonic):
        self.recorder_status = recorder_status
        self.control_status = control_status
        self._control = CounterWindow(clock)
        self._pictures = CounterWindow(clock)
        self._lock = threading.Lock()
        self._clock = clock
        self._pending_control = None
        self._cleanup_error = None
        self._cleanup_attempts = 0
        self._retry_at = 0.0
        self._closed = False

    def __call__(self) -> GpuSetupEvidence | None:
        with self._lock:
            recorder = self.recorder_status() or {}
            health = recorder.get("frame_source_health") or {}
            try:
                control = self._read_control()
            except CleanupPending as exc:
                self._pending_control = (retain(self._pending_control, exc)
                                         if self._pending_control is not None else exc.owner)
                self._cleanup_failed(exc)
                control = None
            except (OSError, RuntimeError) as exc:
                if self._pending_control is not None:
                    self._cleanup_failed(exc)
                control = None
            if control is None or not control.capabilities & CAP_GPU:
                self._control.observe(None, alive=None)
                self._pictures.observe(None, pictures=None)
                # A GPU source cannot borrow an old legacy ring's healthy bits.
                return (GpuSetupEvidence(None, None, None, False, False, False)
                        if health.get("kind") == "gpu" else None)
            return self._observe(control, recorder)

    def _read_control(self):
        if self._closed or self._clock() < self._retry_at:
            return None
        self._close_pending()
        return self.control_status()

    def _cleanup_failed(self, error):
        self._cleanup_attempts = min(self._cleanup_attempts + 1, 32)
        delay = min(30.0, 2 ** min(self._cleanup_attempts - 1, 5))
        self._cleanup_error = str(error)[:512]
        self._retry_at = self._clock() + delay
        if self._cleanup_attempts == 1 or delay == 30.0:
            log.warning("GPU setup control cleanup pending; retry in %.0fs: %s", delay, error)

    def _close_pending(self):
        if self._pending_control is not None:
            try:
                self._pending_control.close()
            except CleanupPending as exc:
                self._pending_control = retain(self._pending_control, exc)
                raise
            self._pending_control = None
        self._cleanup_error = None
        self._cleanup_attempts = 0
        self._retry_at = 0.0

    def close(self):
        """Stop discovery and prove any partial read-only owner was released."""
        with self._lock:
            self._closed = True
            self._close_pending()

    def cleanup_status(self):
        return {"closed": self._closed, "pending": self._pending_control is not None,
                "error": self._cleanup_error, "attempts": self._cleanup_attempts,
                "retry_in_s": max(0.0, self._retry_at - self._clock())}

    def _observe(self, control, recorder):
        birth = control.producer_created_lo | (control.producer_created_hi << 32)
        control_key = (control.producer_pid, birth, control.generation, control.ack_token)
        current = bool(control.rom_open and control.state in (ACTIVE, PASSIVE)
                       and not control.reason)
        movement = self._control.observe(
            control_key, alive=control.ack_heartbeat if current else None)
        identity, delivered = _receipt(recorder)
        same_request = bool(identity and identity[:4] == control_key)
        recording = bool(recorder.get("recording") and recorder.get("frame_source") == "plugin")
        idle = bool(recorder.get("idle"))
        active = current and control.state == ACTIVE and not idle
        pictures = self._pictures.observe(
            identity if same_request else None,
            pictures=delivered if recording and same_request and active else None)
        idle_receipt = bool(current and idle and recording and same_request
                            and delivered is not None and delivered > 0)
        alive = bool(current and (movement["alive"] or idle_receipt))
        return GpuSetupEvidence(
            control.producer_pid, identity, delivered, idle, alive,
            bool(alive and (pictures["pictures"] or idle_receipt)))
