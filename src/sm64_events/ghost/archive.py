"""Recorder-owned pose history, independent of video selection and encoding.

Every row retains its immutable renderer occurrence. UTC narrows the possible
capture epoch; exact game-frame endpoints bind an attempt, never a nearest row.
A star attempt's export also keeps its dance tail (`dance_tail`).
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import threading
import time
from types import SimpleNamespace

from sm64_events.detectors.anchors import NEAR_ZERO_IGT
from sm64_events.ghost.clip import (
    ELAPSED_VALID, RAW_VALID, STAR_CLOCK, CLOCK_KINDS, RECORD_BYTES,
    ClipError, Frame, attempt_origin, frame_from_bytes, producer_values, write_clip,
)
from sm64_events.memory.addresses import STAR_GRAB_ACTIONS

# His ask, 2026-09-23: the ghost stays on screen through its own star grab and
# dance, then fades. A star attempt closes at x-cam (the dance's first frame)
# of the grab that completes it -- for the 100-coin star, the EXIT star after
# it -- so its clip keeps the frames after that through the dance's end:
# display only, the attempt's origin and closing frame are unchanged. Measured dances
# run about 100-125 frames past x-cam (the exit dance, and the longer no-exit
# one); the cap keeps a stuck or unusual tail bounded.
DANCE_TAIL_MAX_FRAMES = 180
# Export can run the moment the attempt closes, before the dance is recorded;
# it waits for the dance to end, for the recording to stall or close, or for
# the cap's own duration plus a second, whichever comes first.
DANCE_TAIL_WAIT_S = DANCE_TAIL_MAX_FRAMES / 30 + 1
DANCE_TAIL_STALL_S = 1.0
# The archive's own ceiling, by bytes, not the clip format's per-clip cap: a
# longer record (GC6's particles) must not shorten how far back a late save
# can reach. Halving the clip cap to 100000 once cut this window to about 55
# minutes (review, 2026-09-23). 288 MiB holds the default two-hour retention
# at 30 Hz (216000 rows) with room for the record to grow; the time bound
# decides what goes, and this only stops a runaway producer.
ARCHIVE_MAX_BYTES = 288 * 1024 * 1024
ARCHIVE_MAX_RECORDS = ARCHIVE_MAX_BYTES // RECORD_BYTES
ARCHIVE_RETENTION_S = 7200


def dance_tail(close: Frame, candidates) -> tuple[list[bytes], bool]:
    """Rows after the closing frame through the end of Mario's star dance,
    and whether that end was seen. The dance ends at the first row that leaves
    the grab/dance actions, stops the game clock (the exit warp freezes it),
    breaks the clip's timing (reset, state load, new star), changes scene, or
    passes DANCE_TAIL_MAX_FRAMES. A repeated occurrence of the same frame is
    kept, as inside the attempt."""
    tail, last = [], close
    for raw in candidates:
        frame = frame_from_bytes(raw)
        repeat = (frame.global_timer, frame.clock.elapsed_frames) == (last.global_timer, last.clock.elapsed_frames)
        if (frame.global_timer > close.global_timer + DANCE_TAIL_MAX_FRAMES
                or frame.live_action not in STAR_GRAB_ACTIONS
                or frame.clock.valid & (RAW_VALID | ELAPSED_VALID) != RAW_VALID | ELAPSED_VALID
                or (frame.clock.continuity_id, frame.clock.origin_id, frame.level, frame.area)
                != (close.clock.continuity_id, close.clock.origin_id, close.level, close.area)
                or frame.qpc < last.qpc
                or not (repeat or (frame.global_timer > last.global_timer
                                   and frame.clock.elapsed_frames > last.clock.elapsed_frames))):
            return tail, True
        tail.append(raw)
        last = frame
    return tail, False


def _utc(value: str) -> float:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("missing timezone")
        return parsed.timestamp()
    except (TypeError, AttributeError, ValueError) as error:
        raise ClipError("attempt has no valid UTC interval") from error


class PoseArchive:
    def __init__(self, path: Path, *, max_records: int = ARCHIVE_MAX_RECORDS,
                 retention_s: float | None = ARCHIVE_RETENTION_S, boundary_slack_s: float = 1,
                 dance_wait_s: float = DANCE_TAIL_WAIT_S, dance_stall_s: float = DANCE_TAIL_STALL_S):
        if type(max_records) is not int or not 1 <= max_records <= ARCHIVE_MAX_RECORDS:
            raise ValueError("pose archive record bound is invalid")
        if ((retention_s is not None and (not math.isfinite(retention_s) or retention_s <= 0))
                or not math.isfinite(boundary_slack_s) or boundary_slack_s < 0):
            raise ValueError("pose archive time bound is invalid")
        self.path = Path(path)
        self.max_records, self.retention_s = max_records, retention_s
        self.boundary_slack_s = boundary_slack_s
        self.dance_wait_s, self.dance_stall_s = dance_wait_s, dance_stall_s
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS producers (identity TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS poses (
                id INTEGER PRIMARY KEY, producer TEXT NOT NULL, sequence TEXT NOT NULL,
                captured REAL NOT NULL, timer INTEGER NOT NULL, valid INTEGER NOT NULL,
                data BLOB NOT NULL, UNIQUE(producer, sequence));
            CREATE INDEX IF NOT EXISTS poses_time ON poses(captured);
            CREATE INDEX IF NOT EXISTS poses_timer ON poses(timer, captured);
        """)
        self._count = self._db.execute("SELECT COUNT(*) FROM poses").fetchone()[0]

    def ingest(self, producer, frames: tuple[bytes, ...]) -> bool:
        """Commit one bounded batch, or nothing. Closed owners reject late input."""
        if not 0 < len(frames) <= 512:
            if not frames:
                return False
            raise ClipError("native ghost batch exceeds channel capacity")
        source = producer_values(producer)
        document = json.dumps(source, sort_keys=True, separators=(",", ":"))
        identity = hashlib.sha256(document.encode()).hexdigest()
        records = tuple(frame_from_bytes(raw) for raw in frames)
        for frame in records:
            if (frame.configuration, frame.epoch) != (source["configuration"], source["epoch"]):
                raise ClipError("native ghost batch disagrees with its producer identity")
        with self._lock:
            if self._db is None:
                return False
            previous = self._db.execute(
                "SELECT data FROM poses WHERE producer=? ORDER BY sequence DESC LIMIT 1",
                (identity,)).fetchone()
            last = frame_from_bytes(previous[0]) if previous else None
            for frame in records:
                if last and (frame.sequence <= last.sequence or frame.dlist_serial <= last.dlist_serial
                             or frame.qpc < last.qpc):
                    raise ClipError("native ghost archive received a stale occurrence")
                last = frame
            rows = [(identity, f"{frame.sequence:016x}",
                     source["utc_anchor"] + (frame.qpc - source["qpc_anchor"]) / source["qpc_frequency"],
                     frame.global_timer, frame.valid_fields, frame.raw) for frame in records]
            with self._db:
                inserted_source = self._db.execute(
                    "INSERT OR IGNORE INTO producers VALUES (?,?)", (identity, document)).rowcount
                self._db.executemany(
                    "INSERT INTO poses(producer,sequence,captured,timer,valid,data) VALUES (?,?,?,?,?,?)", rows)
                newest = self._db.execute("SELECT MAX(captured) FROM poses").fetchone()[0]
                # None follows the recorder's size-only retention mode; the
                # hard record ceiling still bounds native history independently.
                removed = (self._db.execute("DELETE FROM poses WHERE captured < ?",
                                            (newest - self.retention_s,)).rowcount
                           if self.retention_s is not None else 0)
                count = self._count + len(rows) - removed
                if count > self.max_records:
                    count -= self._db.execute("""DELETE FROM poses WHERE id IN
                        (SELECT id FROM poses ORDER BY id LIMIT ?)""", (count - self.max_records,)).rowcount
                if inserted_source:
                    self._db.execute("DELETE FROM producers WHERE identity NOT IN (SELECT producer FROM poses)")
            self._count = count
            return True

    @contextmanager
    def _reader(self):
        with self._lock:
            owned = self._db
            connection = owned or sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                yield connection
            finally:
                if owned is None:
                    connection.close()

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None

    def export_attempt(self, attempt, destination: Path, *, target: dict | None = None) -> dict:
        origin = attempt_origin(attempt)
        start, end_frame = origin["native_clock_frame"], origin["closed_frame"]
        kind = CLOCK_KINDS[origin["native_clock_kind"]]
        lo, hi = _utc(origin["native_clock_utc"]), _utc(attempt.ended_utc)
        if hi < lo:
            raise ClipError("attempt UTC interval is reversed")

        def endpoint(rows):
            frames = [frame_from_bytes(row[1]) for row in rows]
            if any(frame.clock.valid & (RAW_VALID | ELAPSED_VALID) != RAW_VALID | ELAPSED_VALID
                   for frame in frames):
                raise ClipError("attempt endpoint has no exact native elapsed clock")
            coordinates = {(frame.epoch, frame.clock.continuity_id, frame.clock.elapsed_frames,
                            frame.igt, frame.clock.origin_id, frame.clock.valid)
                           for frame in frames}
            if len(coordinates) != 1:
                raise ClipError("attempt has ambiguous native endpoint coordinates")
            return rows[0][0]  # first source occurrence, including a refused pose

        with self._reader() as db:
            candidates = db.execute("""SELECT DISTINCT producer FROM poses
                WHERE timer=? AND captured BETWEEN ? AND ?""",
                (start, lo - self.boundary_slack_s, lo + self.boundary_slack_s)).fetchall()
            matches = []
            for (identity,) in candidates:
                ends = db.execute("""SELECT sequence,data FROM poses WHERE producer=? AND timer=?
                    AND captured BETWEEN ? AND ? ORDER BY sequence""",
                    (identity, end_frame, hi - self.boundary_slack_s, hi + self.boundary_slack_s)).fetchall()
                if not ends:
                    continue
                starts = db.execute("""SELECT sequence,data FROM poses WHERE producer=? AND timer=?
                    AND captured BETWEEN ? AND ? ORDER BY sequence""",
                    (identity, start, lo - self.boundary_slack_s, lo + self.boundary_slack_s)).fetchall()
                first, last = endpoint(starts), endpoint(ends)
                if first <= last:
                    matches.append((identity, first, last))
            if len(matches) != 1:
                raise ClipError("attempt has ambiguous native epochs" if matches else
                                "attempt is missing exact native frame endpoints")
            identity, first, last = matches[0]
            source = json.loads(db.execute("SELECT data FROM producers WHERE identity=?", (identity,)).fetchone()[0])
            rows = db.execute("""SELECT data FROM poses WHERE producer=?
                AND sequence BETWEEN ? AND ? ORDER BY sequence""", (identity, first, last)).fetchall()
        # The immutable copy survives pruning or recorder detach during publication.
        if kind == STAR_CLOCK and frame_from_bytes(rows[0][0]).igt > NEAR_ZERO_IGT:
            raise ClipError("star origin has no canonical near-zero native clock witness")
        close = frame_from_bytes(rows[-1][0])
        # A star run, whatever owns its clock: the 100-coin star is timed by
        # its exit segment and closes on the exit star's grab. Segments keep
        # ending where they close.
        star_run = getattr(attempt, "segment_id", None) is None
        tail = (self._dance_tail(identity, last, close)
                if star_run and close.live_action in STAR_GRAB_ACTIONS else [])
        manifest = write_clip(destination, tuple(row[0] for row in rows) + tuple(tail),
                              SimpleNamespace(**source), clock_kind=kind, tail_records=len(tail))
        return {**manifest, "attempt_id": attempt.id, "session_id": attempt.session_id,
                **origin,
                "target": dict(target) if target else None}

    def _dance_tail(self, identity: str, after: str, close: Frame) -> list[bytes]:
        """Wait, bounded, for the recorder to capture the dance; outside the
        lock between looks so capture keeps committing."""
        deadline = time.monotonic() + self.dance_wait_s
        seen, moved = -1, time.monotonic()
        while True:
            with self._reader() as db:
                recording = self._db is not None
                candidates = [row[0] for row in db.execute(
                    "SELECT data FROM poses WHERE producer=? AND sequence>? ORDER BY sequence LIMIT ?",
                    (identity, after, 4 * DANCE_TAIL_MAX_FRAMES))]
            tail, ended = dance_tail(close, candidates)
            now = time.monotonic()
            if len(candidates) != seen:
                seen, moved = len(candidates), now
            if ended or not recording or now >= deadline or now - moved >= self.dance_stall_s:
                return tail
            time.sleep(0.1)

    def status(self) -> dict:
        with self._reader() as db:
            count, first, last = db.execute("SELECT COUNT(*),MIN(captured),MAX(captured) FROM poses").fetchone()
        return {"records": count, "first_utc": first, "last_utc": last, "closed": self._db is None}
