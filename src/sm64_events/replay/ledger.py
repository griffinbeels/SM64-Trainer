"""The picture ledger -- capture stamps every DISTINCT picture it grabs.

Round 32, item 40. His spec, 2026-08-28: "when we build the video, at every
single frame of gameplay, we have access to all the memory addresses and
data in-game that would allow us to embed each frame with extra information
that we can use to do any type of future analysis with (e.g., input
timeline, or if we want to extract other information later, we should be
able to extend the frame-by-frame information storage at any point)."

The recorder photographs the emulator's window ~120 times a second, so one
presented picture is grabbed several times. This module watches those
grabs, notices each NEW picture by content, and records one row for it:
the picture's own composition time, the RAM frame current at that moment,
and every extra stamp registered. Extraction matches the encoded clip's
picture runs to these rows by time (mapalign.ledger_map), which is what
retires estimating the capture journey with timing constants -- the rows
SAY when each picture appeared and what the game was doing.

Dedup is EXACT equality of a strided sample: pre-encode grabs of one
presented picture are byte-identical copies of the same surface, unlike
encoded frames, whose compression noise is why picture_runs thresholds a
mean instead. A change the sample misses costs nothing downstream: that
run inherits its neighbour's row and the rising rule advances it by the
consecutive-pictures law (mapalign's second rule).

Extending the per-picture record is ONE line at wiring time:
``ledger.stamps["name"] = callable``. Every later row carries that field,
rides into the clip's sidecar as ``picture_ledger``, and is analysable
long after the ring forgot the footage. A stamp that raises loses its
field, never the row, never the capture thread.
"""
import logging
from collections import deque
from collections.abc import Callable

log = logging.getLogger(__name__)

# Every 8th row and column of the grab (~1.6% of pixels). Dense enough that
# any real scene change lands on it; misses are repaired by the rising rule.
# Cost, measured at his 1600x1224 window: 93 us per grab, ~1.1% of a core
# at the full 120 grabs/s (2026-08-28) -- the strided copy dominates.
SAMPLE_STRIDE = 8
# How long a row is answerable -- matches frameclock.RETENTION_S: the ring's
# own retention decides how far back a clip can be cut, never this.
RETENTION_S = 1800.0
_ROWS_CEILING = 35                     # eviction sizing only, above real 30/s
# A grab can catch the surface MID-update: the torn picture differs from
# both neighbours, so one presented picture lands TWO rows a few ms apart
# (measured on attempt 4518: 118 of 647 gaps under 20 ms against the
# game's ~33 ms picture cadence). A change this soon after the last row is
# that same present settling -- fold it into the row it belongs to (the
# first, whose time and stamp are the present moment's) instead of
# appending a second. The game presents a NEW picture at most every 33 ms,
# so nothing real is this close.
MIN_ROW_GAP_S = 0.020


class PictureLedger:
    """One row per distinct captured picture; append on the capture thread,
    read snapshots anywhere (deque.append is atomic, readers copy)."""

    def __init__(self, retention_s: float = RETENTION_S):
        self._rows: deque[tuple[float, int | None, dict | None]] = deque(
            maxlen=int(retention_s * _ROWS_CEILING))
        self._prev_sample: bytes | None = None
        self._prev_shape = None
        self._warned = False
        # THE extension point: name -> zero-arg callable, sampled at the
        # moment each new picture is noticed. Register at wiring time.
        self.stamps: dict[str, Callable[[], object]] = {}

    def observe(self, bgra, capture_ts: float | None,
                frame: int | None, extras: dict | None = None) -> bool:
        """One grab off the capture thread. True = a NEW picture (row
        landed). `extras` are caller-computed per-grab stamps (the registry
        covers zero-arg probes; a stamp that needs THIS grab's own time --
        the frame-edge phase -- arrives here instead). Never raises: a
        ledger bug must not cost the capture."""
        try:
            if capture_ts is None:
                return False           # a picture nobody can place in time
            shape = getattr(bgra, "shape", None)
            sample = bgra[::SAMPLE_STRIDE, ::SAMPLE_STRIDE].tobytes()
            if shape == self._prev_shape and sample == self._prev_sample:
                return False
            self._prev_shape = shape
            self._prev_sample = sample
            if self._rows and capture_ts - self._rows[-1][0] < MIN_ROW_GAP_S:
                return False           # a torn grab settling, not a new picture
            extras = dict(extras or {})
            for name, probe in self.stamps.items():
                try:
                    extras[name] = probe()
                except Exception:
                    if not self._warned:
                        self._warned = True
                        log.exception("picture-ledger stamp %r failed; "
                                      "its field is dropped", name)
            self._rows.append((float(capture_ts), frame, extras or None))
            return True
        except Exception:
            if not self._warned:
                self._warned = True
                log.exception("picture ledger observe failed; capture "
                              "continues without it")
            return False

    def rows_between(self, t0: float, t1: float) -> list[dict]:
        """The distinct pictures composed in [t0, t1], oldest first, as
        JSON-able dicts: ts, frame, plus any registered stamp fields."""
        return [{"ts": ts, "frame": frame, **(extras or {})}
                for ts, frame, extras in list(self._rows)
                if t0 <= ts <= t1]
