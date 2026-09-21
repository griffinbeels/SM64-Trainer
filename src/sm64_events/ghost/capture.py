"""Recorder-owned native pose pump; Python never paces ghost playback frames."""
from __future__ import annotations

import logging
import struct
import threading
from dataclasses import replace
from typing import Callable

from sm64_events.ghost.native_channel import NativeChannel
from sm64_events.ghost.native_protocol import Producer
from sm64_events.memory.ghost_layout import GhostLayout
from sm64_events.replay.capturecontrol import CaptureControl
from sm64_events.replay.ownedclose import CleanupPending, retain

log = logging.getLogger("sm64.ghost.capture")


def grouped(producer: Producer, frames: tuple[bytes, ...]):
    """Queue batches can straddle rewind; attach each record's own identity."""
    group, identity = [], None
    for frame in frames:
        if len(frame) != 192:
            raise ValueError("native ghost record size changed")
        key = (struct.unpack_from("<I", frame, 8)[0],
               struct.unpack_from("<I", frame, 24)[0])
        if identity is not None and key != identity:
            yield replace(producer, configuration=identity[0], epoch=identity[1]), tuple(group)
            group = []
        identity = key
        group.append(frame)
    if identity is not None:
        yield replace(producer, configuration=identity[0], epoch=identity[1]), tuple(group)


class NativeCapture:
    """Start only after acquiring the machine-wide ReplayRecorder lock.

    A timeout from stop retains the live thread and channel. A caller must keep
    this owner until a later stop succeeds; timeout is never release evidence.
    """

    def __init__(self, layout, on_batch: Callable[[Producer, tuple[bytes, ...]], None],
                 *, producer_pid: int | None = None):
        config = layout if isinstance(layout, GhostLayout) else GhostLayout.from_layout(layout)
        self._layout = config.to_bytes()
        self._on_batch, self._pid = on_batch, producer_pid
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        self._playback = {"enabled": False}
        self._revision = 1
        self._status = {"available": False, "reason": "not_started"}
        self._channel = None
        self._pending_cleanup = None

    def start(self):
        if self._thread is not None:
            raise RuntimeError("native ghost capture already started")
        self._thread = threading.Thread(target=self._run, name="ghost-pose-capture", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 3.0) -> bool:
        self._stop.set()
        if self._thread is None:
            return True
        self._thread.join(timeout_s)
        return not self._thread.is_alive()

    def set_playback(self, **controls):
        """Human-paced controls; immutable native assets load on its worker."""
        with self._lock:
            if controls == self._playback:
                return
            self._playback = dict(controls)
            self._revision += 1

    def snapshot_status(self) -> dict:
        with self._lock:
            return dict(self._status)

    def _report(self, **status):
        with self._lock:
            self._status = status

    def _discover(self):
        if self._pid is not None:
            return self._pid
        with CaptureControl() as control:
            return control.status().producer_pid

    def _close_channel(self) -> bool:
        try:
            if self._pending_cleanup is not None:
                self._pending_cleanup.close()
                self._pending_cleanup = None
            if self._channel is not None:
                self._channel.close()
        except CleanupPending as error:
            self._pending_cleanup = (retain(self._pending_cleanup, error)
                                     if self._pending_cleanup is not None else error.owner)
            return False
        except (OSError, RuntimeError):
            log.exception("Native ghost channel retains ownership during close")
            return False
        self._channel = None
        return True

    def _run(self):
        try:
            self._pump()
        finally:
            while not self._close_channel():
                threading.Event().wait(0.25)
            self._report(available=False, reason="stopped")

    def _pump(self):
        sent_revision = 0
        while not self._stop.is_set():
            try:
                if self._channel is None:
                    self._channel = NativeChannel(self._discover(), self._layout)
                    sent_revision = 0
                with self._lock:
                    revision = self._revision
                    control = dict(self._playback) if revision != sent_revision else None
                status, frames = self._channel.poll(control)
                sent_revision = revision
                producer = Producer.from_status(status)
                for identity, batch in grouped(producer, frames):
                    if self._stop.is_set():
                        break
                    self._on_batch(identity, batch)
                self._report(available=True, reason=status.reason, state=status.state,
                             dropped=status.dropped, producer=producer,
                             playback_state=status.playback_state,
                             playback_reason=status.playback_reason,
                             displayed=bool(status.displayed))
                self._stop.wait(0.016)
            except BlockingIOError:
                self._stop.wait(0.016)
            except CleanupPending as error:
                self._pending_cleanup = error.owner
                self._report(available=False, reason=str(error))
                while not self._close_channel():
                    threading.Event().wait(0.25)
            except (OSError, RuntimeError, ValueError) as error:
                self._report(available=False, reason=str(error))
                if not self._close_channel():
                    # A close failure may retain handles. Never acquire a new
                    # channel until the preceding owner has actually released.
                    while not self._close_channel():
                        threading.Event().wait(0.25)
                self._stop.wait(0.5)
