"""Bounded audio callback handoff and existing PCM pacing for a GPU media run."""

from collections import deque
from dataclasses import dataclass
import math
import threading

from sm64_events.replay.audiopacing import AudioBacklog, AudioPacer, AudioPlacement


@dataclass(frozen=True, slots=True)
class Arrival:
    data: bytes
    ends_at: float
    arrived: float


class PcmHandoff:
    def __init__(self, *, max_bytes, max_blocks, max_age):
        if (
            type(max_bytes) is not int
            or type(max_blocks) is not int
            or min(max_bytes, max_blocks, max_age) <= 0
            or not math.isfinite(max_age)
        ):
            raise ValueError("explicit positive PCM handoff limits required")
        self.max_bytes, self.max_blocks, self.max_age = max_bytes, max_blocks, max_age
        self._queue = deque()
        self._lock = threading.Lock()
        self.bytes = 0
        self.fault = None
        self.closed = False

    def _fail(self, reason):
        self.fault = self.fault or reason
        return False

    def submit(self, data, ends_at, *, now):
        """Audio callback: never wait for a worker, lock, encoder or file."""
        if self.closed or self.fault:
            return False
        if (
            type(data) is not bytes
            or len(data) % 4
            or not math.isfinite(ends_at)
            or not math.isfinite(now)
        ):
            return self._fail("invalid PCM callback value")
        if not data:
            return True
        if not self._lock.acquire(blocking=False):
            return self._fail("PCM handoff contention")
        try:
            if self.closed or self.fault:
                return False
            if (
                self.bytes + len(data) > self.max_bytes
                or len(self._queue) >= self.max_blocks
            ):
                return self._fail("PCM handoff capacity exceeded")
            self._queue.append(Arrival(data, ends_at, now))
            self.bytes += len(data)
            return True
        finally:
            self._lock.release()

    def take(self, now):
        if self.fault:
            raise AudioBacklog(self.fault)
        if not self._lock.acquire(blocking=False):
            return None
        try:
            if not self._queue:
                return None
            if now - self._queue[0].arrived > self.max_age:
                self._fail("PCM handoff age exceeded")
                raise AudioBacklog(self.fault)
            item = self._queue.popleft()
            self.bytes -= len(item.data)
            return item
        finally:
            self._lock.release()

    def close_input(self):
        """Called after the audio source has stopped delivering callbacks."""
        self.closed = True

    def drained(self):
        if self.fault:
            raise AudioBacklog(self.fault)
        if not self._lock.acquire(blocking=False):
            return False
        try:
            return self.closed and not self._queue
        finally:
            self._lock.release()


class GpuAudio:
    def __init__(self, media, handoff, *, monotonic, wall_time):
        self.owner = threading.current_thread()
        self.media, self.handoff = media, handoff
        self.monotonic, self.wall_time = monotonic, wall_time
        # One fixed offset maps the committed video end (wall clock of the
        # run) onto the pacer's monotonic clock.
        self._wall_offset = wall_time() - monotonic()
        self.rate = media.mux.rate
        self.max_pad_samples = min(
            handoff.max_bytes // 4, int(handoff.max_age * self.rate)
        )
        self.placement = AudioPlacement(self.rate, self._write)
        self.pacer = AudioPacer(
            self.rate,
            monotonic,
            # Padding ends where the pacer bounded it: at the committed video
            # end during a pause, so it is muxable at once, never stamped at a
            # wall clock the video has not reached.
            lambda buf: self.placement.put_at(buf, self._pad_end_wall()),
            write_at=self.placement.put_at,
            idle_grace_s=0.05,
            max_pad_samples=self.max_pad_samples,
            pad_limit=self._pad_limit,
        )
        self.finished = False

    def _check(self):
        if threading.current_thread() is not self.owner:
            raise RuntimeError("GPU audio belongs to its media worker")
        if self.finished:
            raise RuntimeError("GPU audio is finished")

    def _pad_limit(self):
        """Silence is only synthesized up to the committed video end: the mux
        cannot take audio past it, so padding further would only pile PCM
        against a frozen frontier (a true pause) until the buffer failed."""
        end = getattr(self.media, "video_end", None)  # a legacy sink has no frontier
        if end is None:
            return None
        return self.media.run.origin_ts + end / 90000 - self._wall_offset

    def _pad_end_wall(self):
        limit = self._pad_limit()
        now = self.wall_time()
        return now if limit is None else min(now, limit + self._wall_offset)

    def _write(self, data, first_us):
        self.media.audio(data, first_us, now=self.monotonic())

    def drain(self, *, tick=True):
        self._check()
        for _ in range(self.handoff.max_blocks):
            item = self.handoff.take(self.monotonic())
            if item is None:
                break
            self.pacer.feed(item.data, ends_at=item.ends_at)
        if tick:
            self.pacer.tick()

    def finish(self, final_ts):
        """Real producer must drain before filling only its final silent tail."""
        self._check()
        self.drain(tick=False)
        if not self.handoff.drained():
            return False
        if not math.isfinite(final_ts):
            raise ValueError("finite final audio boundary required")
        first_us = self.placement.next_pts
        if first_us is None:
            first_us = int(round(self.media.run.origin_ts * 1_000_000))
        end_us = int(round(final_ts * 1_000_000))
        samples = max(0, ((end_us - first_us) * self.rate + 999999) // 1_000_000)
        if samples > self.max_pad_samples:
            raise AudioBacklog("final silent tail exceeded sample budget")
        if samples:
            # Exact first sample clock, avoiding another arrival/clamp roundtrip.
            # Media's final PCM cut trims only samples beyond its sealed video end.
            self._write(b"\0" * (samples * 4), first_us)
        self.finished = True
        return True
