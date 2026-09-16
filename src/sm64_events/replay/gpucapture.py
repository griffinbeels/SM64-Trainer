"""Recorder-owned GPU source/sink pair with an independently supervised lease."""

import logging
from functools import partial
import sys
import threading
import time

from sm64_events.replay.capturecontrol import CaptureControl, CAP_GPU, CLOSED
from sm64_events.replay.gpuaudio import PcmHandoff
from sm64_events.replay.gpusettings import GpuSettings
from sm64_events.replay.pluginsource import table_for
from sm64_events.replay.ownedclose import CleanupPending, CleanupRetrier, retain
from sm64_events.replay.tickwait import TickWaiter

log = logging.getLogger("sm64.replay")


class GpuCleanupError(RuntimeError):
    """An owned worker/resource may survive; recorder must retain scratch."""


class GpuSink:
    """Audio endpoint. Its stop follows source shutdown in ReplayRecorder."""

    def __init__(self, owner):
        self.owner = owner

    def start(self):
        pass  # VideoSource.start owns the one runtime worker.

    def submit_audio(self, data):
        self.owner.submit_audio(data)

    def stop(self):
        self.owner.finish()

    @property
    def publication_error(self):
        return self.owner.error or self.owner.status().get("error")

    def queue_depth(self):
        return self.owner.status().get("pending", 0), 0


class GpuCapture:
    frame_source = "plugin"
    frame_source_note = None

    def __init__(
        self,
        expected_pid,
        layout,
        *,
        nominal_rate,
        settings=None,
        demand_factory=None,
        session_factory=None,
        retry_gate=None,
        producer_identity=None,
        pending_control=None,
    ):
        self.expected_pid, self.layout = expected_pid, layout
        self.nominal_rate = nominal_rate
        self.settings = settings or GpuSettings()
        self._demand_factory, self._session_factory = demand_factory, session_factory
        self._stop, self._done = threading.Event(), threading.Event()
        # Not a threading.Event: that wait is quantized to Windows' 15.6 ms
        # timer and the native pool is only 266 ms deep (tickwait.py).
        self._wake = TickWaiter()
        self._worker = self._demand = self._pcm = None
        self._session = None
        self._pending_control = pending_control
        self._pending_cleanup = None  # (closer, kind) retained after the bounded backoff
        self._idle_check = lambda: False
        self._pause_check = lambda: False
        self._productive = False
        self._sessions_started = 0
        self._status = {"kind": "gpu", "state": "ready", "pending": 0}
        self.error = None
        self.cleanup_error = None
        self.cfg = self.clock = self.ledger = self.publish = None
        self._on_stopped = None
        self.retry_gate, self.producer_identity = retry_gate, producer_identity

    def create_sink(self, cfg, clock, ledger, publish):
        if self.cfg is not None:
            raise RuntimeError("GPU capture sink already bound")
        if not cfg.picture_feed:
            raise RuntimeError("GPU capture requires the exact picture feed")
        self.cfg, self.clock, self.ledger, self.publish = cfg, clock, ledger, publish
        return GpuSink(self)

    def set_idle_check(self, check):
        self._idle_check = check

    def set_pause_check(self, check):
        """Only an explicit pause retires capture; inactivity retains pre-roll."""
        self._pause_check = check

    def want_capture(self):
        return not self._stop.is_set() and not self._pause_check()

    def wait(self, period):
        self._wake.wait(period)

    def watch(self, handle):
        """A native producer event ends the wait the moment it publishes."""
        self._wake.watch(handle)

    def unwatch(self, handle):
        self._wake.unwatch(handle)

    def refresh_demand(self):
        if self._pause_check() and self._demand is not None:
            self._demand.request_stop("session paused", expected=True)
        self._wake.set()

    def start(self, _on_frame, on_stopped):
        # Python receives tiny samples and encoded packets; raw-frame callback is
        # deliberately unused. It cannot create a second ledger/encoder stream.
        if self._worker is not None or self.cfg is None:
            raise RuntimeError("GPU capture needs one prepared source/sink pair")
        if getattr(sys, "frozen", False):
            raise RuntimeError("GPU helper executable is not bundled in this build")
        self._on_stopped = on_stopped
        self._worker = threading.Thread(
            target=self._run, name="replay-gpu-media", daemon=True
        )
        self._worker.start()

    def _run(self):
        try:
            if self._pending_control is not None:
                self._retry_cleanup(self._close_pending_control, "control", "discovery close failed")
                raise RuntimeError("GPU discovery cleanup retried")
            demand_factory, session_factory = (
                self._demand_factory,
                self._session_factory,
            )
            if demand_factory is None:
                from sm64_events.replay.gpudemand import GpuDemand

                demand_factory = GpuDemand
            if session_factory is None:
                from sm64_events.replay.gpucapture_session import CaptureSession

                session_factory = CaptureSession
            while not self._stop.is_set():
                if self._pause_check():
                    self._status = {**self._status, "state": "paused", "pending": 0}
                    self.wait(0.1)
                    continue
                if self.retry_gate is not None and not self.retry_gate.wait(
                    self, self.producer_identity
                ):
                    if not self.want_capture():
                        continue
                    break
                demand = self._new_demand(demand_factory)
                self._run_session(demand, session_factory)
                # A normal media return can race the supervisor's terminal
                # publication. Its joined outcome, not the earlier loop guard,
                # decides whether this request really ended without failure.
                terminal = demand.snapshot
                if terminal.state == "fault" and not terminal.lifecycle:
                    raise RuntimeError(terminal.reason)
                if self.want_capture():
                    break  # Source/ROM stopped: normal recorder discovery reattaches.
        except Exception as exc:
            if isinstance(exc, CleanupPending):
                self._retry_cleanup(exc.owner.close, "control", exc)
            if isinstance(exc, GpuCleanupError):
                self.cleanup_error = exc
            self.error = str(exc)[:512]
            if self.retry_gate is not None:
                self.retry_gate.failed(
                    self.producer_identity, self.error,
                    productive=self._productive and self.cleanup_error is None,
                )
            log.exception("GPU recording stopped")
        finally:
            self.end_audio(self._pcm)
            self._status = {"kind": "gpu", "state": "stopped", "error": self.error}
            self._done.set()
            if self._on_stopped is not None:
                self._on_stopped()

    def _run_session(self, demand, session_factory):
        """Keep one session and lease owned through startup, failure and close."""
        try:
            demand.start()
            self._session = session_factory(self, demand)
            try:
                self._session.run()
            except GpuCleanupError as exc:
                # The channel belongs to THIS media thread. Preserve its
                # session and retry closure here; the recorder may only
                # wait for proof, never close native custody itself.
                closer = getattr(self._session, "close", None)
                if closer is None:
                    raise
                failure = getattr(self._session, "error", None) or str(exc)
                self._retry_cleanup(closer, "media", exc)
                raise RuntimeError(failure) from exc
            except Exception:
                self._session = None  # Session.run proved its cleanup before raising.
                raise
            self._session = None
        finally:
            try:
                demand.close(timeout=2.0)
            except Exception as exc:  # noqa: BLE001 - arbitrary supervisor close must retain its owner on failure.
                self._retry_cleanup(partial(demand.close, timeout=2.0), "lease", exc)
            self._demand = None

    CLEANUP_DELAYS = (1.0, 2.0, 4.0)

    def _retry_cleanup(self, closer, kind, error):
        """Retain one failed owner until its same-thread close proves quiet.

        Stop never abandons an owner: finish() remains bounded and may report
        pending cleanup while this daemon waits. No fresh demand or media work
        runs here. A late writer/helper exit can then release the recorder.
        """
        from sm64_events.replay.gpupublication import PublicationError

        closer = CleanupRetrier(closer)
        self.cleanup_error = GpuCleanupError(f"GPU {kind} cleanup pending: {error}")
        # Bounded: a few backoffs here, then the owner stays retained and the
        # recorder's recovery loop retries it through finish(). An endless
        # loop here never returned to the caller (round 48 gate hang).
        for attempts, delay in enumerate(self.CLEANUP_DELAYS, start=1):
            self._status = {"kind": "gpu", "state": "retiring", "pending": 0,
                            "error": str(self.cleanup_error), "cleanup_attempts": attempts,
                            "retry_in_s": delay}
            if attempts == 1:
                log.warning("%s; retaining its owner and retrying in %.1fs", self.cleanup_error, delay)
            deadline = time.monotonic() + delay
            while time.monotonic() < deadline:
                self.wait(deadline - time.monotonic())
            try:
                closer()
            except PublicationError:
                # CaptureSession.close only emits this after ALL other owners
                # closed; the finished file error does not imply live custody.
                self.cleanup_error = None
                self._session = None
                return
            except Exception as exc:  # noqa: BLE001 - arbitrary owned closer; expose failure without abandoning custody.
                self.cleanup_error = GpuCleanupError(f"GPU {kind} cleanup pending: {exc}")
            else:
                self.cleanup_error = None
                if kind == "media":
                    self._session = None
                return
        # Give up here; keep the retained owner for the next finish().
        self._pending_cleanup = (closer, kind)
        log.warning("%s; retained for the recorder's next recovery attempt", self.cleanup_error)

    def _new_demand(self, factory):
        self._timings = None
        self._demand = factory(self.expected_pid, table_for(self.layout), self.settings.request())
        self._productive = False
        self._sessions_started += 1
        self._status = {"kind": "gpu", "state": "preparing", "pending": 0}
        return self._demand

    def begin_audio(self):
        handoff = PcmHandoff(
            max_bytes=self.settings.pcm_bytes,
            max_blocks=self.settings.pcm_blocks,
            max_age=self.settings.max_age,
        )
        if self._pcm is not None:
            raise RuntimeError("prior GPU audio input not retired")
        self._pcm = handoff
        return handoff

    def end_audio(self, handoff):
        # Detach first: later callbacks cannot enter this retired run. A callback
        # already inside submit finishes under its lock; drained() observes that
        # same lock and cannot certify an empty queue before its append finishes.
        if self._pcm is handoff:
            self._pcm = None
        if handoff is not None:
            handoff.close_input()

    def submit_audio(self, data):
        import time

        handoff = self._pcm
        if handoff is not None:
            if not handoff.submit(data, time.time(), now=time.monotonic()):
                self._wake.set()

    def report(self, session):
        self._timings = session.timings
        status = session.adapter.status(refresh=False)
        identity = session.demand.identity
        if session.media.mux.video_count and session.frontier is not None:
            self._productive = True
        self._status = dict(
            kind="gpu",
            state=session.state,
            **status,
            source_epoch=session.channel.header.epoch,
            publication=session.output.status() if session.output is not None else None,
            video_packets=session.media.mux.video_count,
            encoded_bytes_pending=session.media.order.pending_bytes,
            pcm_bytes_pending=session.media.pcm.bytes,
            audio_handoff_bytes=session.handoff.bytes,
            frontier=session.frontier,
            capture_receipt=dict(
                producer_pid=identity.producer_pid,
                producer_birth=identity.producer_birth,
                control_generation=identity.control_generation,
                token=identity.token,
                source_epoch=session.channel.header.epoch,
                delivered=session.media.delivered,
            ),
        )

    def status(self):
        timings = getattr(self, "_timings", None)
        return {**self._status, "automatic_idle": bool(self._idle_check()),
                "sessions_started": self._sessions_started,
                "stage_timings": timings.summary() if timings is not None else {}}

    @property
    def recording_active(self):
        """Cached health, distinct from a worker merely waiting to retry."""
        return self._status.get("state") == "recording" and not self.error

    def capture_retired(self):
        """No media/lease owner remains, for opportunistic maintenance only."""
        return (self.cleanup_error is None and self._demand is None
                and self._status.get("state") in {"paused", "stopped"})

    def report_wait(self, reason):
        self._status = {
            "kind": "gpu",
            "state": "unavailable",
            "pending": 0,
            "error": reason,
        }

    def request_stop(self):
        self._stop.set()
        if self._demand is not None:
            self._demand.request_stop("recorder stopped", expected=True)
        self._wake.set()

    def stop(self):
        self.request_stop()
        if self._worker is None:
            self._close_pending_control()

    def _close_pending_control(self):
        if self._pending_control is not None:
            try:
                self._pending_control.close()
            except CleanupPending as exc:
                self._pending_control = retain(self._pending_control, exc)
                raise
            self._pending_control = None

    def finish(self):
        self.request_stop()
        if self._worker is None:
            self._close_pending_control()
            return
        self._worker.join(timeout=self.settings.close_s + 3.0)
        if self._worker.is_alive():
            raise GpuCleanupError("GPU media worker did not finish; retain its scratch")
        pending = getattr(self, "_pending_cleanup", None)
        if pending is not None and self.cleanup_error is not None:
            # The worker gave up after its bounded backoff. Each recovery
            # attempt by the recorder retries the retained owner once, here.
            closer, kind = pending
            try:
                closer()
            except Exception as exc:  # noqa: BLE001 - keep custody; the recorder retries again later.
                self.cleanup_error = GpuCleanupError(f"GPU {kind} cleanup pending: {exc}")
            else:
                self.cleanup_error = None
                self._pending_cleanup = None
                if kind == "media":
                    self._session = None
        if self.cleanup_error is not None:
            raise self.cleanup_error


def discover(
    expected_pid,
    layout,
    *,
    nominal_rate,
    control_factory=CaptureControl,
    retry_gate=None,
):
    """Read-only capability check, retaining a known GPU source during gaps."""
    identity = retry_gate.identity_for(expected_pid) if retry_gate is not None else None
    try:
        with control_factory() as control:
            status = control.status()
            if (
                status.producer_pid == expected_pid
                and status.state != CLOSED
                and status.capabilities & CAP_GPU
            ):
                identity = (
                    retry_gate.observe(status) if retry_gate is not None else None
                )
            elif identity is None:
                return None
            else:
                from sm64_events.replay.gpuretry import producer_key

                # A positively identified successor may use a different plugin.
                # A gap in this producer's control status is not such evidence.
                if producer_key(status) != identity:
                    return None
    except CleanupPending as exc:
        # Recorder ownership is established before this partial discovery
        # object is retried. It cannot be discarded by an attach-loop error.
        return GpuCapture(expected_pid, layout, nominal_rate=nominal_rate,
                          retry_gate=retry_gate, producer_identity=identity,
                          pending_control=exc.owner)
    except (FileNotFoundError, OSError, BlockingIOError):
        if identity is None:
            return None
    return GpuCapture(
        expected_pid,
        layout,
        nominal_rate=nominal_rate,
        retry_gate=retry_gate,
        producer_identity=identity,
    )
