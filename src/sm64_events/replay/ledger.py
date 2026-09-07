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
frames to these rows through the FEED LOG (`replay/feedmap.py`), which is
what retires estimating the capture journey with timing constants -- the
rows SAY when each picture appeared and what the game was doing.

Dedup is EXACT equality of a strided sample: pre-encode grabs of one
presented picture are byte-identical copies of the same surface, unlike
encoded frames, whose compression noise is why the offline pad reader's
`picture_runs` thresholds a mean instead. A change the sample misses costs
nothing downstream: the sink feeds one frame per row either way, so a
missed change is one fewer picture in the clip, never a wrong one.

Extending the per-picture record is ONE line at wiring time:
``ledger.stamps["name"] = callable``. Every later row carries that field,
rides into the clip's sidecar as ``picture_ledger``, and is analysable
long after the ring forgot the footage. A stamp that raises loses its
field, never the row, never the capture thread.
"""
import logging
from collections import deque
from collections.abc import Callable
from pathlib import Path

from sm64_events.replay.picturearchive import PictureArchive

log = logging.getLogger(__name__)

# Every 8th row and column of the grab (~1.6% of pixels). Dense enough that
# any real scene change lands on it; misses are repaired by the rising rule.
# Cost, measured at his 1600x1224 window: 93 us per grab, ~1.1% of a core
# at the full 120 grabs/s (2026-08-28) -- the strided copy dominates.
SAMPLE_STRIDE = 8
# The standalone in-memory mode used by small tools/tests. The recorder
# opens a disk archive under its ownership lock; its rows follow footage
# retention and this value never caps a production session.
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
        # One entry per accepted video write: actual encoder-run/PTS identity,
        # media wall time and captured row. Heartbeats retain their source row
        # even when a later query starts after the last real picture.
        self._feeds: deque[dict] = deque(
            maxlen=int(retention_s * _ROWS_CEILING))
        self._last_fed_row: tuple[str | None, float | None] = (None, None)
        self._archive: PictureArchive | None = None

    def open_archive(self, path: Path) -> None:
        """Called by the recorder owner before capture, never at app build.

        The deque remains a small dedup cache; the archive answers all
        retained footage, including a whole session or a 24-hour buffer.
        """
        if self._archive is not None:
            if path.exists():
                self._archive.resume()
                return
            # Another recorder owner reset the shared scratch while this
            # instance was detached. None of that footage survives either.
            self.reset()
        archive = PictureArchive(path)
        for ts, frame, extras in self._rows:
            archive.add_row({"ts": ts, "frame": frame, **(extras or {})})
        for feed in self._feeds:
            archive.add_feed(feed)
        self._archive = archive
        self._rows = deque(self._rows, maxlen=350)
        self._feeds = deque(self._feeds, maxlen=350)

    def reset(self) -> None:
        """The recorder's scratch reset invalidates footage and identities together."""
        if self._archive is not None:
            self._archive.close()
            self._archive = None
        self._rows.clear()
        self._feeds.clear()
        self._prev_sample = self._prev_shape = None
        self._last_fed_row = (None, None)

    def discard_segment(self, seg) -> None:
        if self._archive is not None:
            self._archive.discard_segment(seg)

    def flush(self) -> None:
        if self._archive is not None:
            self._archive.flush()

    def detach(self) -> None:
        """Release writable handles while kept footage remains queryable."""
        if self._archive is not None:
            self._archive.close()

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
            if (self._rows
                    and capture_ts - self._rows[-1][0] < MIN_ROW_GAP_S
                    and frame == self._rows[-1][1]):
                # A torn grab settling shares its present's stamp. A row
                # this close with a DIFFERENT stamp is a catch-up present
                # after an emulator stall (measured on his lava clip:
                # stamp advances of +3..+7) -- a real picture, kept.
                return False
            extras = dict(extras or {})
            for name, probe in self.stamps.items():
                try:
                    extras[name] = probe()
                except Exception:
                    if not self._warned:
                        self._warned = True
                        log.exception("picture-ledger stamp %r failed; "
                                      "its field is dropped", name)
            if self._archive is not None:
                self._archive.add_row({"ts": float(capture_ts), "frame": frame, **extras})
            self._rows.append((float(capture_ts), frame, extras or None))
            return True
        except Exception:
            if not self._warned:
                self._warned = True
                log.exception("picture ledger observe failed; capture "
                              "continues without it")
            return False

    def mark_fed(self, row_ts: float | None, wrote_at: float,
                 *, media_run=None, pts: int | None = None) -> None:
        """File an accepted picture with its assigned media timestamp.

        row_ts=None repeats the preceding picture within this encoder run.
        The captured row time and actual assigned PTS remain separate: a late
        picture can be placed after an already-written heartbeat without
        losing the identity of the inputs it contains.
        """
        run_id = media_run.id if media_run else None
        repeated = row_ts is None
        if repeated and self._last_fed_row[0] == run_id:
            row_ts = self._last_fed_row[1]
        self._last_fed_row = (run_id, row_ts)
        feed = {"at": float(wrote_at), "ts": row_ts,
                "run_id": run_id, "pts": pts, "repeat": repeated}
        if self._archive is not None:
            self._archive.add_feed(feed)
        self._feeds.append(feed)

    def feeds_between(self, t0: float, t1: float) -> list[dict]:
        """Frames in a media-time span, with run/PTS and captured-row identity."""
        if self._archive is not None:
            return self._archive.feeds_between(t0, t1)
        return [dict(entry) for entry in list(self._feeds)
                if t0 <= entry["at"] <= t1]

    def rows_between(self, t0: float, t1: float) -> list[dict]:
        """The distinct pictures composed in [t0, t1], oldest first, as
        JSON-able dicts: ts, frame, plus any registered stamp fields."""
        if self._archive is not None:
            return self._archive.rows_between(t0, t1)
        return [{"ts": ts, "frame": frame, **(extras or {})}
                for ts, frame, extras in list(self._rows)
                if t0 <= ts <= t1]
