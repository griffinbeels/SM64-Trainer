"""Bounded media-worker PCM, preserving source blocks and their original clock.

This is not the audio callback queue. That producer handoff must be bounded
separately. Here libav receives at most an explicit lead beyond committed video;
a missing video worker cannot turn the muxer's private audio queue into a buffer.
"""

from collections import deque
from dataclasses import dataclass
import math
import threading

from sm64_events.replay.media import MEDIA_HZ


@dataclass(frozen=True, slots=True)
class PcmBlock:
    data: bytes
    first_us: int
    admitted_at: float


class PcmBacklog(RuntimeError):
    """The recording run must fail explicitly; never drop PCM and claim sync."""


class PcmBuffer:
    def __init__(self, run, *, rate, max_bytes, max_blocks, max_age, lead_ticks):
        if any(type(v) is not int or v <= 0 for v in (rate, max_bytes, max_blocks)):
            raise ValueError("positive integer PCM limits required")
        if type(lead_ticks) is not int or lead_ticks < 0:
            raise ValueError("nonnegative integer audio lead required")
        if not math.isfinite(max_age) or max_age <= 0:
            raise ValueError("positive PCM age limit required")
        self.owner = threading.get_ident()
        self.origin_us = round(run.origin_ts * 1_000_000)
        self.rate, self.max_bytes, self.max_blocks = rate, max_bytes, max_blocks
        self.max_age, self.lead_ticks = max_age, lead_ticks
        self.blocks = deque()
        self.bytes = 0
        self._last_us = None
        self._last_now = None
        self._last_end = None
        self.closed = False
        self.tail_samples = 0

    def _owner(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError("PCM buffer belongs to its media worker")

    def append(self, data, first_us, *, now):
        self._owner()
        if self.closed:
            raise PcmBacklog("PCM buffer sealed")
        if type(data) is not bytes or not data or len(data) % 4:
            raise ValueError("complete immutable s16le stereo PCM required")
        if type(first_us) is not int or not math.isfinite(now):
            raise ValueError("valid PCM source and admission clocks required")
        if self._last_us is not None and first_us < self._last_us:
            raise PcmBacklog("PCM source clock moved backward")
        if self._last_now is not None and now < self._last_now:
            raise ValueError("PCM admission clock moved backward")
        if (
            len(self.blocks) >= self.max_blocks
            or self.bytes + len(data) > self.max_bytes
        ):
            raise PcmBacklog("PCM capacity exceeded")
        self.blocks.append(PcmBlock(data, first_us, now))
        self.bytes += len(data)
        self._last_us, self._last_now = first_us, now

    def _samples_before(self, block, end_ticks, final):
        # Compare rational clocks without adding epoch-sized floats. Samples
        # belong to the half-open interval when their START is before the end.
        numerator = (
            (self.origin_us - block.first_us) * MEDIA_HZ + end_ticks * 1_000_000
        ) * self.rate
        denominator = MEDIA_HZ * 1_000_000
        return max(0, (numerator + (denominator - 1 if final else 0)) // denominator)

    def drain(self, video_end_ticks, write, *, final=False):
        self._owner()
        if self.closed:
            raise PcmBacklog("PCM buffer sealed")
        if type(video_end_ticks) is not int or video_end_ticks < 0:
            raise ValueError("committed integer video end required")
        if self._last_end is not None and video_end_ticks < self._last_end:
            raise ValueError("committed video end moved backward")
        self._last_end = video_end_ticks
        limit = video_end_ticks + (0 if final else self.lead_ticks)
        written = 0
        while self.blocks:
            block = self.blocks[0]
            samples = len(block.data) // 4
            available = self._samples_before(block, limit, final)
            if not final and available < samples:
                break
            count = min(samples, available)
            if count:
                # Normal delivery shares the original bytes. Only the known
                # final cut may split a source block; no cumulative retimestamp.
                data = block.data if count == samples else block.data[: count * 4]
                write(data, block.first_us)
                written += count
            self.blocks.popleft()
            self.bytes -= len(block.data)
            if final:
                self.tail_samples += samples - count
        if final:
            self.closed = True
        return written

    def check_age(self, now):
        """Stale means writable but unwritten. A block waiting on a video
        frontier that has not reached it (a true pause) is not stale; it is
        exactly what the bounded buffer is for."""
        self._owner()
        if not math.isfinite(now):
            raise ValueError("finite PCM admission clock required")
        if not self.blocks or now - self.blocks[0].admitted_at <= self.max_age:
            return
        block = self.blocks[0]
        if self._last_end is not None:
            limit = self._last_end + self.lead_ticks
            if self._samples_before(block, limit, False) < len(block.data) // 4:
                return  # ahead of the committed video, not stuck behind it
        raise PcmBacklog("PCM pending age exceeded")

    def abort(self):
        self._owner()
        self.blocks.clear()
        self.bytes = 0
        self.closed = True
