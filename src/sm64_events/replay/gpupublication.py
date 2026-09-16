"""Bounded compressed-byte handoff; disk publication never owns capture credits.

Admission preserves the mux byte stream, but is not a publication receipt. Only
the archive writer exposes completed fragments. No pixels or media clocks change.
"""

from collections import deque
import io
import threading
import time


class PublicationBusyError(RuntimeError):
    """The writer still owns its archive; scratch must remain protected."""


class PublicationError(RuntimeError):
    """Publication failed, but the writer has demonstrably finished."""


class PublicationWriteError(RuntimeError):
    """Mux output refused; the session must still prove writer completion."""


class ArchiveOutput(io.RawIOBase):
    def __init__(self, archive, *, max_bytes, max_blocks=256, max_age=2.0,
                 clock=time.monotonic):
        super().__init__()
        if min(max_bytes, max_blocks, max_age) <= 0:
            raise ValueError("positive publication bounds required")
        self.archive, self.clock = archive, clock
        self.max_bytes, self.max_blocks, self.max_age = max_bytes, max_blocks, max_age
        self._condition = threading.Condition()
        self._queue = deque()
        self._inflight = None
        self._bytes = self._blocks = 0
        self._error = self._finish_error = None
        self._finishing = False
        self._peak_ms = self._peak_unix = 0.0
        self._worker = threading.Thread(target=self._run, name="replay-publication", daemon=True)
        self._worker.start()

    def writable(self):
        return True

    def _check(self):
        if self._error:
            raise PublicationWriteError(self._error)
        oldest = self._inflight or (self._queue[0] if self._queue else None)
        if oldest and self.clock() - oldest[0] > self.max_age:
            self._error = "compressed publication exceeded age budget"
            raise PublicationWriteError(self._error)

    def check(self):
        with self._condition:
            self._check()

    def write(self, data):
        size = len(data)
        with self._condition:
            self._check()
            if self._finishing:
                raise PublicationWriteError("compressed publication is closed")
            if not size:
                return 0
            # Include the block currently inside archive.feed in both bounds.
            if self._bytes + size > self.max_bytes or self._blocks >= self.max_blocks:
                self._error = "compressed publication exceeded capacity"
                raise PublicationWriteError(self._error)
            self._queue.append((self.clock(), bytes(data)))
            self._bytes += size
            self._blocks += 1
            self._condition.notify()
        return size

    def _run(self):
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._queue or self._finishing)
                    if not self._queue:
                        break
                    self._inflight = self._queue.popleft()
                    data = self._inflight[1]
                start, utc = time.monotonic(), time.time()
                try:
                    self.archive.feed(data)
                finally:
                    elapsed = (time.monotonic() - start) * 1000
                    with self._condition:
                        if self.clock() - self._inflight[0] > self.max_age:
                            self._error = self._error or "compressed publication exceeded age budget"
                        if elapsed > self._peak_ms:
                            self._peak_ms, self._peak_unix = elapsed, utc
                        self._bytes -= len(data)
                        self._blocks -= 1
                        self._inflight = None
        except Exception as exc:  # noqa: BLE001 - transfer archive/parser failures to the capture owner.
            with self._condition:
                self._error = self._error or f"compressed publication failed: {exc}"[:512]
                self._queue.clear()
                self._bytes = self._blocks = 0
        finally:
            try:
                cause = self._error or self._finish_error
                self.archive.finish(cause)
                # FragmentArchive retains parser/close failures without raising.
                failure = getattr(self.archive, "error", None)
                if failure and failure != cause:
                    with self._condition:
                        self._error = self._error or f"compressed publication close failed: {failure}"[:512]
            except Exception as exc:  # noqa: BLE001 - failed close cannot certify publication or scratch cleanup.
                with self._condition:
                    self._error = self._error or f"compressed publication close failed: {exc}"[:512]

    def status(self):
        with self._condition:
            return dict(pending_bytes=self._bytes, pending_blocks=self._blocks,
                        error=self._error, max_feed_ms=round(self._peak_ms, 3),
                        max_feed_started_unix_s=self._peak_unix)

    def finish(self, error=None, *, timeout):
        with self._condition:
            self._finishing = True
            self._finish_error = self._finish_error or error
            self._condition.notify()
        self._worker.join(timeout)
        if self._worker.is_alive():
            raise PublicationBusyError("compressed publication writer still owns archive")
        with self._condition:
            if self._error:
                raise PublicationError(self._error)
