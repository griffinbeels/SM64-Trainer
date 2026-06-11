"""SQLite store: append-only event journal + derived/materialized tables.

The journal is the source of truth — append-only, except whole-session
deletion, a user-level operation (delete_session). `attempts` is a
rebuildable cache of tracking.projection.project(events). Sync sqlite3
behind a lock: writes are one tiny row per game event, far below any
contention threshold."""
import json
import sqlite3
import threading
from pathlib import Path

from sm64_events.core.events import Event
from sm64_events.tracking.projection import Attempt

MIGRATIONS = [
    # v1
    """
    CREATE TABLE IF NOT EXISTS sessions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      started_utc TEXT NOT NULL,
      ended_utc TEXT,
      label TEXT
    );
    CREATE TABLE IF NOT EXISTS events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      session_id INTEGER NOT NULL REFERENCES sessions(id),
      seq INTEGER NOT NULL,
      type TEXT NOT NULL,
      frame INTEGER NOT NULL,
      wall_time_utc TEXT NOT NULL,
      payload TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS attempts (
      id INTEGER PRIMARY KEY,
      session_id INTEGER NOT NULL,
      course_id INTEGER, star_id INTEGER, strat_tag TEXT,
      anchor_type TEXT NOT NULL, anchor_frame INTEGER,
      outcome TEXT NOT NULL, outcome_detail TEXT,
      igt_frames INTEGER, rta_frames INTEGER,
      started_utc TEXT NOT NULL, ended_utc TEXT NOT NULL,
      cleared INTEGER NOT NULL DEFAULT 0, cleared_reason TEXT
    );
    CREATE TABLE IF NOT EXISTS pbs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      course_id INTEGER NOT NULL, star_id INTEGER NOT NULL, strat_tag TEXT,
      timer_mode TEXT NOT NULL, frames INTEGER NOT NULL,
      attempt_id INTEGER, saved_utc TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS ui_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """,
]

_ATTEMPT_COLS = ("id", "session_id", "course_id", "star_id", "strat_tag",
                 "anchor_type", "anchor_frame", "outcome", "outcome_detail",
                 "igt_frames", "rta_frames", "started_utc", "ended_utc",
                 "cleared", "cleared_reason")


class EventRow:
    """One journal row, payload already decoded."""
    __slots__ = ("id", "session_id", "seq", "type", "frame",
                 "wall_time_utc", "payload")

    def __init__(self, id, session_id, seq, type, frame, wall_time_utc, payload):
        self.id, self.session_id, self.seq = id, session_id, seq
        self.type, self.frame = type, frame
        self.wall_time_utc, self.payload = wall_time_utc, payload


def _iso(dt) -> str:
    return dt.isoformat().replace("+00:00", "Z")


class Database:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            for i, script in enumerate(MIGRATIONS[version:], start=version + 1):
                self._conn.executescript(script)
                self._conn.execute(f"PRAGMA user_version = {i}")
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- journal -----------------------------------------------------------
    def append_event(self, session_id: int, seq: int, event: Event) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO events (session_id, seq, type, frame, wall_time_utc, payload)"
                " VALUES (?,?,?,?,?,?)",
                (session_id, seq, event.type, event.frame,
                 _iso(event.timestamp_utc), json.dumps(event.payload)))
            self._conn.commit()
            return cur.lastrowid

    def events(self) -> list[EventRow]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM events ORDER BY id").fetchall()
            return [EventRow(r["id"], r["session_id"], r["seq"], r["type"],
                             r["frame"], r["wall_time_utc"], json.loads(r["payload"]))
                    for r in rows]

    # -- sessions ----------------------------------------------------------
    def insert_session(self, started_utc: str, label: str | None = None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO sessions (started_utc, label) VALUES (?,?)",
                (started_utc, label))
            self._conn.commit()
            return cur.lastrowid

    def end_session(self, session_id: int, ended_utc: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE sessions SET ended_utc=? WHERE id=?",
                               (ended_utc, session_id))
            self._conn.commit()

    def reopen_session(self, session_id: int) -> None:
        with self._lock:
            self._conn.execute("UPDATE sessions SET ended_utc=NULL WHERE id=?",
                               (session_id,))
            self._conn.commit()

    def sessions(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT s.id, s.started_utc, s.ended_utc, s.label,"
                " (SELECT COUNT(*) FROM attempts a WHERE a.session_id = s.id)"
                "   AS attempts"
                " FROM sessions s ORDER BY s.id DESC").fetchall()
            return [dict(r) for r in rows]

    def delete_session(self, session_id: int) -> None:
        """Hard-deletes the session's journal slice. The attempts cache is
        NOT touched here — callers must re-project afterwards (the journal
        is the source of truth). PB rows survive: they carry their frames;
        a dangling attempt_id is informational only."""
        with self._lock:
            self._conn.execute("DELETE FROM events WHERE session_id=?",
                               (session_id,))
            self._conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            self._conn.commit()

    # -- attempts (derived cache) -------------------------------------------
    def _attempt_params(self, a: Attempt) -> tuple:
        return (a.id, a.session_id, a.course_id, a.star_id, a.strat_tag,
                a.anchor_type, a.anchor_frame, a.outcome, a.outcome_detail,
                a.igt_frames, a.rta_frames, a.started_utc, a.ended_utc,
                int(a.cleared), a.cleared_reason)

    def replace_attempts(self, attempts: list[Attempt]) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM attempts")
            self._conn.executemany(
                f"INSERT INTO attempts ({','.join(_ATTEMPT_COLS)})"
                f" VALUES ({','.join('?' * len(_ATTEMPT_COLS))})",
                [self._attempt_params(a) for a in attempts])
            self._conn.commit()

    def upsert_attempt(self, a: Attempt) -> None:
        with self._lock:
            self._conn.execute(
                f"INSERT OR REPLACE INTO attempts ({','.join(_ATTEMPT_COLS)})"
                f" VALUES ({','.join('?' * len(_ATTEMPT_COLS))})",
                self._attempt_params(a))
            self._conn.commit()

    def attempts(self) -> list[Attempt]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM attempts ORDER BY id").fetchall()
            return [Attempt(**{**{k: r[k] for k in _ATTEMPT_COLS},
                               "cleared": bool(r["cleared"])}) for r in rows]

    # -- pbs -----------------------------------------------------------------
    def insert_pb(self, course_id: int, star_id: int, strat_tag: str | None,
                  timer_mode: str, frames: int, attempt_id: int | None,
                  saved_utc: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO pbs (course_id, star_id, strat_tag, timer_mode,"
                " frames, attempt_id, saved_utc) VALUES (?,?,?,?,?,?,?)",
                (course_id, star_id, strat_tag, timer_mode, frames,
                 attempt_id, saved_utc))
            self._conn.commit()
            return cur.lastrowid

    def pbs(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM pbs ORDER BY id").fetchall()
            return [dict(r) for r in rows]

    # -- ui_state ------------------------------------------------------------
    def get_state(self, key: str, default):
        with self._lock:
            row = self._conn.execute("SELECT value FROM ui_state WHERE key=?",
                                     (key,)).fetchone()
            return json.loads(row["value"]) if row else default

    def set_state(self, key: str, value) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO ui_state (key, value) VALUES (?,?)",
                (key, json.dumps(value)))
            self._conn.commit()
