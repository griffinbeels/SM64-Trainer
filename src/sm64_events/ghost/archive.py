"""Recorder-owned pose history, independent of video selection and encoding.

Every row retains its immutable renderer occurrence. UTC narrows the possible
capture epoch; exact game-frame endpoints bind an attempt, never a nearest row.
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
from types import SimpleNamespace

from sm64_events.ghost.clip import (
    CLOCK, MAX_RECORDS, ClipError, frame_from_bytes, producer_values, write_clip,
)


def _utc(value: str) -> float:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("missing timezone")
        return parsed.timestamp()
    except (TypeError, AttributeError, ValueError) as error:
        raise ClipError("attempt has no valid UTC interval") from error


class PoseArchive:
    def __init__(self, path: Path, *, max_records: int = MAX_RECORDS,
                 retention_s: float = 7200, boundary_slack_s: float = 1):
        if type(max_records) is not int or not 1 <= max_records <= MAX_RECORDS:
            raise ValueError("pose archive record bound is invalid")
        if (not math.isfinite(retention_s) or retention_s <= 0
                or not math.isfinite(boundary_slack_s) or boundary_slack_s < 0):
            raise ValueError("pose archive time bound is invalid")
        self.path = Path(path)
        self.max_records, self.retention_s = max_records, retention_s
        self.boundary_slack_s = boundary_slack_s
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
                removed = self._db.execute("DELETE FROM poses WHERE captured < ?",
                                           (newest - self.retention_s,)).rowcount
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

    def export_attempt(self, attempt, destination: Path, *, end_frame: int | None = None,
                       target: dict | None = None) -> dict:
        start = getattr(attempt, "anchor_frame", None)
        duration = getattr(attempt, "rta_frames", None)
        if end_frame is None and type(start) is int and type(duration) is int:
            end_frame = start + duration
        if (type(start) is not int or type(end_frame) is not int
                or not 0 <= start <= end_frame < 2**32):
            raise ClipError("attempt has no exact native frame endpoints")
        lo, hi = _utc(attempt.started_utc), _utc(attempt.ended_utc)
        if hi < lo:
            raise ClipError("attempt UTC interval is reversed")
        with self._reader() as db:
            candidates = db.execute("""SELECT DISTINCT producer FROM poses
                WHERE captured BETWEEN ? AND ? AND timer IN (?,?) AND valid & ? != 0""",
                (lo - self.boundary_slack_s, hi + self.boundary_slack_s, start, end_frame, CLOCK)).fetchall()
            matches = []
            for (identity,) in candidates:
                endpoints = db.execute("""SELECT sequence,timer FROM poses
                    WHERE producer=? AND captured BETWEEN ? AND ?
                    AND timer IN (?,?) AND valid & ? != 0 ORDER BY sequence""",
                    (identity, lo - self.boundary_slack_s, hi + self.boundary_slack_s,
                     start, end_frame, CLOCK)).fetchall()
                starts = [sequence for sequence, timer in endpoints if timer == start]
                ends = [sequence for sequence, timer in endpoints if timer == end_frame]
                if starts and ends and starts[0] <= ends[-1]:
                    matches.append((identity, starts[0], ends[-1]))
            if len(matches) != 1:
                raise ClipError("attempt has ambiguous native epochs" if matches else
                                "attempt is missing exact native frame endpoints")
            identity, first, last = matches[0]
            source = json.loads(db.execute("SELECT data FROM producers WHERE identity=?", (identity,)).fetchone()[0])
            rows = db.execute("""SELECT data FROM poses WHERE producer=?
                AND sequence BETWEEN ? AND ? ORDER BY sequence""", (identity, first, last)).fetchall()
        # The immutable copy survives pruning or recorder detach during publication.
        manifest = write_clip(destination, tuple(row[0] for row in rows), SimpleNamespace(**source))
        return {**manifest, "attempt_id": attempt.id, "session_id": attempt.session_id,
                "target": dict(target) if target else None}

    def status(self) -> dict:
        with self._reader() as db:
            count, first, last = db.execute("SELECT COUNT(*),MIN(captured),MAX(captured) FROM poses").fetchone()
        return {"records": count, "first_utc": first, "last_utc": last, "closed": self._db is None}
