"""Independent capture lease supervisor; never performs media or GPU operations.

Only the recorder owner explicitly starts this object. Reading its immutable
snapshot creates no control mapping or request. One worker owns every lease API
call, including cleanup. Media code may set stop_event or call request_stop even
while its own worker is blocked. A stopped/faulted demand never restarts itself.
"""

from dataclasses import dataclass, replace
import logging
import threading
import time

from sm64_events.replay import capturecontrol as C
from sm64_events.replay.gpurequest import GpuRequest, RequestIdentity
from sm64_events.replay.gpudiagnostics import native_reason
from sm64_events.replay.ownedclose import CleanupPending, retain

log = logging.getLogger("sm64.replay")


@dataclass(frozen=True, slots=True)
class DemandSnapshot:
    state: str = "new"
    reason: str = ""
    identity: RequestIdentity | None = None
    native_state: int | None = None
    renewals: int = 0
    cleanup_error: str = ""
    lifecycle: bool = False
    cancelled: bool = False
    cleanup_attempts: int = 0


class DemandFailure(RuntimeError):
    """A request ended; callers must explicitly create a fresh demand to retry."""

    def __init__(self, reason, *, lifecycle=False):
        super().__init__(reason)
        self.lifecycle = lifecycle


class GpuDemand:
    def __init__(
        self,
        expected_pid,
        table,
        limits,
        *,
        control_name="sm64_trainer_gfx_v1",
        stop_event=None,
        control_factory=C.CaptureControl,
        request_factory=GpuRequest.acquire,
        clock=time.monotonic,
        poll_seconds=0.1,
        renew_seconds=0.5,
        setup_timeout=5.0,
    ):
        if type(expected_pid) is not int or expected_pid <= 0:
            raise ValueError("expected producer PID required")
        if not 0 < poll_seconds <= 0.1 or not 0 < renew_seconds <= 0.5:
            raise ValueError("poll and renewal intervals exceed lease safety bounds")
        if not 0 < setup_timeout <= 5.0:
            raise ValueError("setup deadline must be within five seconds")
        self.expected_pid, self.table, self.limits = expected_pid, tuple(table), limits
        self.control_name = control_name
        self.stop_event = stop_event if stop_event is not None else threading.Event()
        self.ready, self.done = threading.Event(), threading.Event()
        self._ended = threading.Event()
        self._control_factory, self._request_factory = control_factory, request_factory
        self._clock, self._poll, self._renew = clock, poll_seconds, renew_seconds
        self._setup_timeout = setup_timeout
        self._lock, self._thread = threading.Lock(), None
        self._snapshot = DemandSnapshot()
        self._stop_reason = ""
        self._control = self._request = None
        self._extra_owner = None
        self._cleanup_wait = threading.Event()

    @property
    def snapshot(self):
        with self._lock:
            return self._snapshot

    @property
    def identity(self):
        return self.snapshot.identity

    def _publish(self, **changes):
        with self._lock:
            self._snapshot = replace(self._snapshot, **changes)

    def start(self):
        with self._lock:
            if self._thread is not None or self._snapshot.state != "new":
                raise RuntimeError("capture demand cannot be restarted")
            if self.stop_event.is_set():
                self._snapshot = DemandSnapshot(
                    "stopped", self._stop_reason or "stopped"
                )
                self.done.set()
                return self
            self._snapshot = DemandSnapshot("discovering")
            self._thread = threading.Thread(
                target=self._run, name="gpu-demand", daemon=True
            )
            self._thread.start()
        return self

    def request_stop(self, reason="stopped", *, expected=False):
        """Signal only: safe for callbacks, never joins or invokes native APIs."""
        with self._lock:
            if not self._stop_reason:
                self._stop_reason = str(reason)
                # Only the recorder's explicit idle/stop gesture qualifies.
                # Error cleanup and a stop following an observed fault cannot
                # relabel a failed request as ordinary cancellation.
                if expected and self._snapshot.state not in ("ending", "fault"):
                    self._snapshot = replace(self._snapshot, cancelled=True)
        self.stop_event.set()

    def reconcile_lifecycle(self, timeout=0.25):
        """Media-error reconciliation only; lease reads stay on the supervisor.

        A native channel may close before the next (at most 100 ms) control
        poll. Wait at most 250 ms for an explicit typed lifecycle outcome.
        Timeout/missing evidence is a failure, never permission to retry.
        Cleanup is still independently joined and checked by close().
        """
        if not 0 <= timeout <= 0.25:
            raise ValueError("lifecycle reconciliation must be within 250 ms")
        self._ended.wait(timeout)
        return self.snapshot.lifecycle

    def close(self, timeout=1.5):
        """Caller-side join. Timeout is explicit; this object still owns cleanup."""
        self.request_stop()
        thread = self._thread
        if thread is None:
            with self._lock:
                if self._snapshot.state == "new":
                    self._snapshot = DemandSnapshot("stopped", self._stop_reason)
            self.done.set()
        elif thread is not threading.current_thread():
            thread.join(timeout)
        if not self.done.is_set():
            raise TimeoutError("capture lease supervisor still owns cleanup")
        # The supervisor thread made one attempt per owner and finished. A
        # caller's later close (the recorder's bounded recovery loop) retries
        # each retained owner once more, here, never on the media thread.
        if any(getattr(self, name) is not None
               for name in ("_extra_owner", "_request", "_control")):
            cleanup = "; ".join(filter(None, (
                self._retire("_extra_owner"), self._retire("_request"), self._retire("_control"))))
            if not cleanup:
                self._publish(cleanup_error="")
        if self.snapshot.cleanup_error:
            raise DemandFailure(
                f"capture lease cleanup failed: {self.snapshot.cleanup_error}"
            )

    def _admit(self, status, *, check_stop=True):
        if check_stop and self.stop_event.is_set():
            raise DemandFailure("stopped_before_request")
        if status.producer_pid != self.expected_pid:
            raise DemandFailure("producer_pid_changed", lifecycle=True)
        if status.state == C.CLOSED:
            raise DemandFailure("producer_closed", lifecycle=True)
        if not status.capabilities & C.CAP_GPU:
            raise DemandFailure("gpu_capture_unavailable")
        if not status.rom_open:
            raise DemandFailure("rom_closed", lifecycle=True)

    def _discover(self):
        while not self.stop_event.is_set():
            control = None
            admitted = False
            try:
                try:
                    self._control = self._control_factory(self.control_name)
                except CleanupPending as exc:
                    self._control = exc.owner
                    raise
                control = self._control
                status = control.status()
                if status.producer_pid != self.expected_pid:
                    raise DemandFailure("producer_pid_changed", lifecycle=True)
                if status.state == C.CLOSED:
                    raise DemandFailure("producer_closed", lifecycle=True)
                if not status.capabilities & C.CAP_GPU:
                    raise DemandFailure("gpu_capture_unavailable")
                if status.rom_open:
                    admitted = True
                    return control
                # A non-practice ROM is a real run: stay idle and say why.
                self._publish(state="baseline_rom" if status.baseline_rom else "waiting_rom",
                              native_state=status.state)
            except (FileNotFoundError, BlockingIOError):
                self._publish(state="discovering")
            except CleanupPending as exc:
                self._remember_pending(exc)
                raise
            finally:
                if control is not None and not admitted:
                    cleanup = self._retire("_control")
                    if cleanup:
                        raise DemandFailure(f"capture control cleanup retried: {cleanup}")
            self.stop_event.wait(self._poll)
        return None

    def _check_identity(self, status, identity):
        # A stop arriving after this read cannot erase an already observed
        # producer fault. Request admission still checks stop before acquiring.
        self._admit(status, check_stop=False)
        birth = status.producer_created_lo | (status.producer_created_hi << 32)
        if (status.producer_pid, birth, status.generation) != (
            identity.producer_pid,
            identity.producer_birth,
            identity.control_generation,
        ):
            raise DemandFailure("producer_identity_changed", lifecycle=True)

    def _monitor(self, control, request):
        started = last_renew = self._clock()
        acknowledged = active = False
        while not self.stop_event.is_set():
            now = self._clock()
            if not active and now - started >= self._setup_timeout:
                raise DemandFailure("capture_setup_deadline")
            try:
                status = control.status()
            except BlockingIOError:
                status = None
            if status is not None:
                self._check_identity(status, request.identity)
                if status.ack_token == request.identity.token:
                    acknowledged = True
                    if status.state not in (C.PREPARING, C.ACTIVE):
                        log.info(
                            "GPU native terminal: pid=%s token=%s state=%s reason=%s (%s)",
                            self.expected_pid, request.identity.token, status.state,
                            status.reason, native_reason(status.reason),
                        )
                        raise DemandFailure(
                            f"native_capture_ended:{status.state}:{status.reason}",
                            lifecycle=(
                                status.state == C.UNAVAILABLE
                                and status.reason == C.FRESH_REQUEST
                            ),
                        )
                    if active and status.state != C.ACTIVE:
                        raise DemandFailure("native_capture_restarted")
                    active |= status.state == C.ACTIVE
                    self._publish(
                        state="active" if active else "preparing",
                        native_state=status.state,
                    )
                elif acknowledged:
                    raise DemandFailure("native_request_replaced")
            if now - last_renew >= self._renew:
                if not request.renew():
                    raise DemandFailure("capture_lease_lost")
                last_renew = self._clock()
                self._publish(renewals=self.snapshot.renewals + 1)
            self.stop_event.wait(
                min(self._poll, max(0, self._renew - (self._clock() - last_renew)))
            )

    # Quick same-thread retries before the supervisor gives up and finishes with
    # the owner retained. The 30 s cadence beyond this belongs to the recorder's
    # recovery loop, which calls close() again; an unbounded loop here kept
    # `done` unset forever, so every caller's close() timed out and the recorder
    # retried a supervisor that could never finish (round 48: 2,166
    # "retrying in 30s" lines in one test run).
    RETIRE_DELAYS = (1.0, 2.0)

    def _retire(self, attribute):
        """Retry one retained owner on this supervisor, never on the media thread."""
        first_error = ""
        original_reason = self.snapshot.reason
        for delay in (*self.RETIRE_DELAYS, None):
            resource = getattr(self, attribute)
            if resource is None:
                return ""
            try:
                resource.close()
            except Exception as exc:  # noqa: BLE001 - arbitrary owned adapters must not lose cleanup ownership.
                setattr(self, attribute, retain(resource, exc))
                first_error = first_error or str(exc)[:512]
                attempts = min(self.snapshot.cleanup_attempts + 1, 32)
                self.stop_event.set()
                self._publish(state="fault", lifecycle=False, cancelled=False,
                              reason=(original_reason or f"capture {attribute} cleanup pending"),
                              cleanup_error=str(exc)[:512], cleanup_attempts=attempts)
                self._ended.set()
                if attempts == 1 or attempts % 8 == 0:
                    log.warning("GPU lease %s cleanup pending (attempt %d): %s", attribute, attempts, exc)
                if delay is None:
                    return first_error
                # stop_event is already set. It cannot waive the retry delay.
                self._cleanup_wait.wait(delay)
            else:
                setattr(self, attribute, None)
                return ""
        return first_error

    def _remember_pending(self, error):
        if (isinstance(error, CleanupPending)
                and all(error.owner is not held for held in (self._control, self._request))):
            self._extra_owner = (retain(self._extra_owner, error)
                                 if self._extra_owner is not None else error.owner)

    def _open_request(self, control):
        try:
            self._request = self._request_factory(
                control, self.table, self.limits, validate=self._admit)
        except CleanupPending as exc:
            self._request = exc.owner
            raise
        return self._request

    def _run(self):
        failure = ""
        lifecycle = False
        try:
            control = self._discover()
            if control is not None and not self.stop_event.is_set():
                request = self._open_request(control)
                if (
                    request.identity is None
                    or request.identity.producer_pid != self.expected_pid
                ):
                    raise DemandFailure("missing_request_identity")
                if not self.stop_event.is_set():
                    self._publish(state="pending", identity=request.identity)
                    self.ready.set()
                    self._monitor(control, request)
        except Exception as error:  # noqa: BLE001 - the supervisor owns arbitrary injected adapters and their cleanup.
            self._remember_pending(error)
            if not (self.stop_event.is_set() and isinstance(error, DemandFailure)
                    and str(error) == "stopped_before_request"):
                failure = str(error)
                lifecycle = isinstance(error, DemandFailure) and error.lifecycle
                if not isinstance(error, DemandFailure):
                    log.exception("GPU lease supervisor failed before cleanup")
                # Publish classification before cleanup, which can take longer
                # than the media worker's bounded reconciliation window. Its
                # owner must still join close(); cleanup failure overrides this.
                self._publish(state="ending", reason=failure, lifecycle=lifecycle,
                              cancelled=False)
                self._ended.set()
        finally:
            self.stop_event.set()
            cleanup = "; ".join(filter(None, (
                self._retire("_extra_owner"), self._retire("_request"), self._retire("_control"))))
            if cleanup:
                failure = f"{failure}; cleanup: {cleanup}".lstrip("; ")
                lifecycle = False  # Cleanup remains a real fault, even at ROM close.
            self.stop_event.set()
            self._publish(
                state="fault" if failure else "stopped",
                reason=failure or self._stop_reason or "stopped",
                # Empty only when both actual owners proved closed; a retained
                # owner keeps its error so close() raises and can retry it.
                cleanup_error=cleanup,
                lifecycle=lifecycle,
            )
            self.done.set()
            self._ended.set()
