"""Shared arrival-clock PCM placement and realtime silence pacing.

The GPU media worker and legacy FFmpeg sink use the same clock arithmetic.
A sample count alone must not replace the observed PCM callback wall clock.
"""


class AudioBacklog(RuntimeError):
    pass


class AudioPlacement:
    def __init__(self, rate, write):
        self.rate, self.write = rate, write
        self.next_pts = None

    def put_at(self, buf, ends_at):
        samples = len(buf) // 4
        if samples <= 0:
            return
        wanted = int(round((ends_at - samples / self.rate) * 1_000_000))
        pts = wanted if self.next_pts is None else max(wanted, self.next_pts)
        self.write(buf, pts)
        # Commit only after delivery succeeds; failures never advance the clock.
        self.next_pts = pts + int(round(samples * 1_000_000 / self.rate))


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
