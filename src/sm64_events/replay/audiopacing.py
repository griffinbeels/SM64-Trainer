"""Shared arrival-clock PCM placement and realtime silence pacing.

The GPU media worker and legacy FFmpeg sink use the same clock arithmetic.
A sample count alone must not replace the observed PCM callback wall clock.
"""

from collections import deque
import math


class AudioBacklog(RuntimeError):
    pass


class AudioPlacement:
    """Place each PCM chunk at its arrival clock, never overlapping or reordering.

    A chunk cannot end after it arrived. After a stall of the callback thread
    (the recorder's forced gen-2 collection stops every thread for 54-139 ms)
    the device's buffered chunks arrive together: the first opens a gap at its
    arrival time, the rest were laid out after it, and every later chunk
    followed on, so audio stayed late by the stall for the rest of the
    recording (2026-09-23: 144 ms after a 140 ms stall, 504 ms after 500 ms).
    Placement now pulls back by dropping a chunk's oldest samples so that it
    ends on arrival, never by moving a chunk earlier than audio already
    written: at once when a chunk is more than LATE_TOLERANCE_S late (a
    stall's backlog), and otherwise when every chunk in the last
    SETTLE_WINDOW_S arrived late, by the least of those lateness values (what
    a burst leaves behind). Callback jitter keeps its minimum near zero, so
    ordinary audio is never trimmed. A stall costs its own audio instead of
    shifting everything after it.
    """

    LATE_TOLERANCE_S = 0.05
    SETTLE_WINDOW_S = 0.5
    SETTLE_FLOOR_S = 0.010  # one callback period: less is jitter, left alone

    def __init__(self, rate, write):
        self.rate, self.write = rate, write
        self.next_pts = None
        self.dropped_samples = 0
        self._lateness = deque()  # (arrival, lateness µs) since the last pull-back
        # Where the trailing run of padded silence began (µs), None when the
        # last write was real audio. See put_at.
        self._padded_since = None

    def _pull_back(self, late, ends_at):
        """Samples to drop from this chunk's head, 0 when it is on time."""
        if late > self.LATE_TOLERANCE_S * 1_000_000:
            self._lateness.clear()
            return math.ceil(late * self.rate / 1_000_000)
        self._lateness.append((ends_at, max(late, 0.0)))
        full = ends_at - self._lateness[0][0] >= self.SETTLE_WINDOW_S
        while ends_at - self._lateness[0][0] > self.SETTLE_WINDOW_S:
            self._lateness.popleft()
        if full:
            settled = min(value for _, value in self._lateness)
            if settled > self.SETTLE_FLOOR_S * 1_000_000:
                self._lateness.clear()
                return math.ceil(settled * self.rate / 1_000_000)
        return 0

    def missing(self, until: float) -> int | None:
        """Samples between the audio already written and wall time `until`:
        the silence that exactly closes the stamps up to then. None before
        anything is written: there are no stamps to close yet."""
        if self.next_pts is None:
            return None
        return max(0, int((until * 1_000_000 - self.next_pts) * self.rate // 1_000_000))

    def pad_at(self, buf, ends_at):
        """Silence the pacer invented to keep the stream at realtime."""
        start = self.next_pts
        self.put_at(buf, ends_at, padding=True)
        if self._padded_since is None and self.next_pts != start:
            self._padded_since = start if start is not None else self.next_pts

    def put_at(self, buf, ends_at, *, padding=False):
        samples = len(buf) // 4
        if samples <= 0:
            return
        wanted = int(round((ends_at - samples / self.rate) * 1_000_000))
        pts = wanted if self.next_pts is None else max(wanted, self.next_pts)
        late = pts + samples * 1_000_000 / self.rate - ends_at * 1_000_000
        invented = 0
        if not padding and self._padded_since is not None and late > 0:
            # Real audio for a time the pacer already filled with silence (the
            # producer stalled past the idle grace, then delivered that time).
            # The silence is ours, so the real chunk never waits behind it: the
            # overlap goes at once, whatever the tolerance, instead of leaving
            # the rest of the recording late by it.
            covered = max(0, self.next_pts - max(wanted, self._padded_since))
            invented = min(samples, math.ceil(min(late, covered) * self.rate / 1_000_000))
            late -= invented * 1_000_000 / self.rate
        extra = self._pull_back(late, ends_at) if late > 0 or not invented else 0
        drop = min(samples, invented + extra)
        if drop:
            buf, samples = buf[drop * 4:], samples - drop
            self.dropped_samples += drop
            if not samples:
                return  # all of it belongs before audio already written
        self.write(buf, pts)
        # Commit only after delivery succeeds; failures never advance the clock.
        self.next_pts = pts + int(round(samples * 1_000_000 / self.rate))
        if not padding:
            self._padded_since = None


class AudioPacer:
    """Keep ffmpeg's audio pipe fed CONTINUOUSLY AT REALTIME by draining real
    PCM and padding silence up to the wall-clock-expected sample count.

    Both ffmpeg inputs are wall-clock-stamped, so the input scheduler reads
    whichever stream is behind in wall time and BLOCKS on it. If the audio pipe
    falls behind — which it does whenever the game is quiet (WASAPI loopback
    delivers no packets) — ffmpeg waits for audio and stops draining the VIDEO
    stdin, collapsing the captured frame rate (live: 16.9 fed/s, ffmpeg
    duplicating >10000 frames → choppy ~17 fps). Holding audio at realtime
    keeps the scheduler from ever waiting on it; padded silence is stamped at
    its write wall-clock and aresample reconciles it.

    Pure logic — clock and writer are injected so the no-starve invariant is
    unit-testable without ffmpeg. `feed` writes real PCM; `tick` pads silence
    to realtime. Returns samples written so callers/tests can observe."""

    def __init__(
        self,
        rate: int,
        now,
        write,
        write_at=None,
        idle_grace_s=0.0,
        *,
        max_pad_samples=None,
        pad_limit=None,
        missing=None,
    ):
        if max_pad_samples is not None and (
            type(max_pad_samples) is not int or max_pad_samples <= 0
        ):
            raise ValueError("positive padding sample budget required")
        self._max_pad_samples = max_pad_samples
        # Optional: the latest clock silence may be padded up to. The GPU
        # sink can only mux audio up to its committed video end, so padding
        # past a frozen frontier (a true pause) only fills the PCM buffer
        # until it fails; bounded by the frontier it simply waits.
        self._pad_limit = pad_limit
        self._rate = rate
        self._now = now
        self._write = write
        # Optional: (real_pcm, ends_at) for a writer that stamps chunks
        # itself (the picture feed's NUT stream); padding still goes
        # through `write`, which stamps it as ending now.
        self._write_at = write_at
        # Optional: for a writer that stamps chunks itself (`write_at`), the
        # samples its stamps lack up to a bound on this pacer's clock
        # (AudioPlacement.missing). Padding then closes exactly that hole.
        # Counted instead (elapsed since the first fed chunk, less samples
        # fed), the pad drifted from the stamps: short by whatever a pull-back
        # had dropped and by how late the first chunk was handled. Each short
        # pad left a hole, and the encoders' aresample (async,
        # min_hard_comp=0.1) closes a hole under 100 ms by laying the next
        # audio straight after the last: early by the hole. On a loaded
        # machine (2026-09-24, 24 runs of the flash and click cut) the click
        # landed up to 141 ms ahead of the flash.
        self._missing = missing
        self._idle_grace_s = idle_grace_s
        self._last_real_at = None
        self._t0 = None
        self._delivered = 0

    def feed(self, real_pcm: bytes, ends_at: float | None = None) -> None:
        if not real_pcm:
            return
        self._last_real_at = self._now()
        if self._t0 is None:
            self._t0 = self._last_real_at
        if ends_at is not None and self._write_at is not None:
            self._write_at(real_pcm, ends_at)
        else:
            self._write(real_pcm)
        self._delivered += len(real_pcm) // 4  # 2ch * s16

    def tick(self) -> int:
        now = self._now()
        if self._t0 is None:
            self._t0 = now
        # A normal callback batch is not a silent gap. Speculative padding
        # occupies its timestamps and pushes the next real PCM forward.
        if (
            self._last_real_at is not None
            and now - self._last_real_at < self._idle_grace_s
        ):
            return 0
        bound = now
        if self._pad_limit is not None:
            limit = self._pad_limit()
            if limit is not None:
                bound = min(now, limit)
        pad = self._missing(bound) if self._missing is not None else None
        if pad is None:
            expected = int((bound - self._t0) * self._rate)
            pad = expected - self._delivered
        if self._max_pad_samples is not None and pad > self._max_pad_samples:
            raise AudioBacklog("silence pacing exceeded its sample budget")
        if pad > 0:
            self._write(b"\x00" * (pad * 4))
            self._delivered += pad
            return pad
        return 0

    @property
    def delivered(self) -> int:
        return self._delivered
