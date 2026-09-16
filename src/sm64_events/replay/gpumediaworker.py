"""Bounded media sink, independent of the worker returning native capture credits.

Only small owned stamps, compressed packets and clocked PCM cross this boundary.
Selection and GPU custody never wait for AAC, SQLite, fragment parsing or disk.
Queue admission is not delivery: receipts advance after successful sink writes.
"""

from collections import deque
import copy
import io
import threading
import time

from sm64_events.replay.gpupublication import (
    PublicationBusyError, PublicationError, PublicationWriteError,
)
from sm64_events.replay.packetmux import PacketFragmentMux


def _row_size(row):
    """Conservative bounded metadata accounting without serializing on capture."""
    pending, size, nodes = [row], 0, 0
    while pending:
        value = pending.pop()
        nodes += 1
        size += 64
        if nodes > 1024 or size > 65536:
            raise PublicationWriteError("picture metadata exceeded bounds")
        if isinstance(value, dict):
            if len(value) > 512:
                raise PublicationWriteError("picture metadata exceeded bounds")
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, (tuple, list)):
            if len(value) > 512:
                raise PublicationWriteError("picture metadata exceeded bounds")
            pending.extend(value)
        elif isinstance(value, str):
            size += len(value) * 4
        elif type(value) is int:
            size += value.bit_length() // 8
        elif value is not None and type(value) not in (float, bool):
            raise PublicationWriteError("unsupported picture metadata value")
    if size > 65536:
        raise PublicationWriteError("picture metadata exceeded bounds")
    return size


class MediaWorker(io.RawIOBase):
    """One producer, one sink, one run. No slow work while holding queue lock."""

    def __init__(self, template, ledger, publish, *, audio_rate, audio_bitrate,
                 packet_limit, pcm_limit, max_bytes, max_blocks=256, max_age=2.0,
                 timings=None, mux_factory=PacketFragmentMux, clock=time.monotonic):
        super().__init__()
        if min(max_bytes, max_blocks, max_age) <= 0:
            raise ValueError("positive media sink bounds required")
        self.template, self.ledger, self.publish = template, ledger, publish
        self.rate, self.clock = audio_rate, clock
        self.max_bytes, self.max_blocks, self.max_age = max_bytes, max_blocks, max_age
        self.timings = timings
        self._mux_factory = mux_factory
        self._mux_options = dict(audio_rate=audio_rate, audio_bitrate=audio_bitrate,
                                 packet_limit=packet_limit, pcm_limit=pcm_limit,
                                 timings=timings)
        self._condition = threading.Condition()
        self._ready = threading.Event()
        self._queue = deque()
        self._inflight = None
        self._bytes = self._blocks = 0
        self._error = self._finish_error = None
        self._finishing = self._complete = self._bound = False
        self._video_count = self._delivered = 0
        self._peak_ms = self._peak_unix = 0.0
        self._peak_kind = None
        self._peak_bytes = self._peak_blocks = 0
        self.archive = self.mux = None
        self._worker = threading.Thread(target=self._run, name="replay-media-sink", daemon=True)
        self._worker.start()

    @property
    def ready(self):
        return self._ready.is_set()

    @property
    def video_count(self):
        return self._video_count

    @property
    def delivered(self):
        return self._delivered

    def writable(self):
        return True

    def write(self, data):
        # Called only by libav on the sink, never by capture admission.
        if threading.get_ident() != self._worker.ident or self.archive is None:
            raise PublicationWriteError("media output before archive binding")
        self.archive.feed(data)
        return len(data)

    def _check(self):
        if self._error:
            raise PublicationWriteError(self._error)
        oldest = self._inflight or (self._queue[0] if self._queue else None)
        if oldest and self.clock() - oldest[0] > self.max_age:
            self._error = "media sink exceeded age budget"
            raise PublicationWriteError(self._error)

    def check(self):
        with self._condition:
            self._check()

    def _admit(self, kind, payload, size):
        with self._condition:
            self._check()
            if self._finishing:
                raise PublicationWriteError("media sink is closed")
            if self._bytes + size > self.max_bytes or self._blocks >= self.max_blocks:
                self._error = "media sink exceeded capacity"
                raise PublicationWriteError(self._error)
            self._queue.append((self.clock(), kind, payload, size))
            self._bytes += size
            self._blocks += 1
            self._peak_bytes = max(self._peak_bytes, self._bytes)
            self._peak_blocks = max(self._peak_blocks, self._blocks)
            self._condition.notify()

    def bind(self, run):
        if self._bound or not self.ready:
            raise PublicationWriteError("media sink not prepared or already bound")
        self._admit("bind", run, 256)
        self._bound = True

    def add_row(self, row):
        size = _row_size(row)
        self._admit("row", copy.deepcopy(row), size)

    def write_pcm(self, data, first_us):
        self._admit("pcm", (bytes(data), first_us), len(data) + 128)

    def publish_picture(self, picture, row_ts, wrote_at, *, media_run, pts,
                        source_id, repeat):
        self._admit("video", (picture, row_ts, wrote_at, media_run, pts, source_id, repeat),
                    len(picture.data) + 512)

    def close(self):
        # GpuMedia has sealed/placed every accepted packet. Joining is separate
        # and happens only after source/helper retirement.
        self._end(complete=True)

    def abort(self):
        self._end(complete=False, error="capture stopped with an incomplete suffix")

    def _end(self, *, complete=False, error=None):
        with self._condition:
            if not self._finishing:
                self._complete = complete
            self._finishing = True
            self._finish_error = self._finish_error or error
            self._condition.notify()

    def _execute(self, kind, payload):
        if kind == "bind":
            self.archive = self.publish(payload)
            self.mux.bind_run(payload)
        elif kind == "row":
            self.ledger.accept_row(payload)
        elif kind == "pcm":
            self.mux.write_pcm(*payload)
        elif kind == "video":
            picture, row_ts, wrote_at, run, pts, source_id, repeat = payload
            # Persist association before mux may expose bytes to range readers.
            # This feed alone is not a mux/delivery receipt; counts below are.
            operation = lambda: self.ledger.mark_fed(
                row_ts, wrote_at, media_run=run, pts=pts, source_id=source_id)
            if self.timings is None:
                operation()
            else:
                self.timings.measure("ledger_feed", operation)
            self.mux.write_video(picture)
            self._video_count += 1
            if not repeat:
                self._delivered += 1
        else:
            raise RuntimeError("unknown media sink command")

    def _consume(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._queue or self._finishing)
                self._check()
                if not self._queue:
                    return
                self._inflight = self._queue.popleft()
                _, kind, payload, size = self._inflight
            start, utc = time.monotonic(), time.time()
            try:
                self._execute(kind, payload)
            finally:
                elapsed = (time.monotonic() - start) * 1000
                with self._condition:
                    if elapsed > self._peak_ms:
                        self._peak_ms, self._peak_unix = elapsed, utc
                        self._peak_kind = kind
            with self._condition:
                self._check()
                self._bytes -= size
                self._blocks -= 1
                self._inflight = None

    def _run(self):
        try:
            self.mux = self._mux_factory(self, self.template, None, **self._mux_options)
            self.mux.prepare()
            self._ready.set()
            self._consume()
            if self._complete and not self._finish_error and self._bound:
                self.mux.close()
            else:
                self.mux.abort()
        except Exception as exc:  # noqa: BLE001 - transfer foreign codec/database/output failures to the owner.
            with self._condition:
                self._error = self._error or f"media sink failed: {exc}"[:512]
            if self.mux is not None:
                try:
                    self.mux.abort()
                except Exception as cleanup:  # noqa: BLE001 - retain secondary cleanup failure across the worker boundary.
                    self._error = f"{self._error}; mux cleanup failed: {cleanup}"[:512]
        finally:
            try:
                cause = self._error or self._finish_error
                if self.archive is not None:
                    self.archive.finish(cause)
                    failure = getattr(self.archive, "error", None)
                    if failure and failure != cause:
                        self._error = self._error or f"media sink close failed: {failure}"[:512]
            except Exception as exc:  # noqa: BLE001 - retain close errors and scratch ownership.
                self._error = self._error or f"media sink close failed: {exc}"[:512]
            finally:
                with self._condition:
                    self._queue.clear()
                    self._bytes = self._blocks = 0
                    self._inflight = None
                    self._ready.set()

    def status(self):
        with self._condition:
            oldest = self._inflight or (self._queue[0] if self._queue else None)
            return dict(kind="media", pending_bytes=self._bytes, pending_blocks=self._blocks,
                        error=self._error, max_feed_ms=round(self._peak_ms, 3),
                        max_feed_started_unix_s=self._peak_unix,
                        slowest_command=self._peak_kind,
                        peak_bytes=self._peak_bytes, peak_blocks=self._peak_blocks,
                        oldest_ms=round((self.clock() - oldest[0]) * 1000, 3) if oldest else 0,
                        video_packets=self._video_count, delivered=self._delivered)

    def finish(self, error=None, *, timeout):
        self._end(error=error)
        self._worker.join(timeout)
        if self._worker.is_alive():
            raise PublicationBusyError("media sink still owns archive and ledger")
        if self._error:
            raise PublicationError(self._error)
