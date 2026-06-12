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
    # v2 — Phase 2: rollout sub-event counts on attempts
    """
    ALTER TABLE attempts ADD COLUMN rollouts_total INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE attempts ADD COLUMN rollouts_dustless INTEGER NOT NULL DEFAULT 0;
    """,
    # v3 — Phase 2 fix round: chained double/triple jump counts
    """
    ALTER TABLE attempts ADD COLUMN jumps_total INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE attempts ADD COLUMN jumps_dustless INTEGER NOT NULL DEFAULT 0;
    """,
    # v4 — segment events: definitions table, attempt linkage, kind-aware PBs
    """
    CREATE TABLE IF NOT EXISTS segment_defs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      enabled INTEGER NOT NULL DEFAULT 1,
      start_triggers TEXT NOT NULL,
      end_triggers TEXT NOT NULL,
      guards TEXT NOT NULL DEFAULT '[]',
      created_utc TEXT NOT NULL
    );
    ALTER TABLE attempts ADD COLUMN segment_id INTEGER;
    CREATE TABLE pbs_v2 (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      course_id INTEGER, star_id INTEGER, segment_id INTEGER, strat_tag TEXT,
      timer_mode TEXT NOT NULL, frames INTEGER NOT NULL,
      attempt_id INTEGER, saved_utc TEXT NOT NULL
    );
    INSERT INTO pbs_v2 (id, course_id, star_id, strat_tag, timer_mode,
                        frames, attempt_id, saved_utc)
      SELECT id, course_id, star_id, strat_tag, timer_mode, frames,
             attempt_id, saved_utc FROM pbs;
    DROP TABLE pbs;
    ALTER TABLE pbs_v2 RENAME TO pbs;
    INSERT INTO segment_defs (name, enabled, start_triggers, end_triggers, guards, created_utc) VALUES
      ('LBLJ', 1, '[{"type":"level_enter","to":6,"from":16}]', '[{"type":"level_enter","to":17}]', '[]', '2026-06-11T00:00:00Z'),
      ('MIPS Clip', 1, '[{"type":"level_exit","from":7,"to":6}]', '[{"type":"level_enter","to":23}]', '[]', '2026-06-11T00:00:00Z'),
      ('Lakitu Skip', 1, '[{"type":"spawned","level":16}]', '[{"type":"level_enter","to":6}]', '[]', '2026-06-11T00:00:00Z'),
      ('BitS Entry', 1, '[{"type":"area_enter","level":6,"area":2}]', '[{"type":"level_enter","to":21}]', '[]', '2026-06-11T00:00:00Z'),
      ('BitDW Pipe Entry', 1, '[{"type":"level_enter","to":17},{"type":"attempt_anchor","level":17}]', '[{"type":"warp_entered","level":17}]', '[]', '2026-06-11T00:00:00Z'),
      ('BitFS Pipe Entry', 1, '[{"type":"level_enter","to":19},{"type":"attempt_anchor","level":19}]', '[{"type":"warp_entered","level":19}]', '[]', '2026-06-11T00:00:00Z'),
      ('BitS Pipe Entry', 1, '[{"type":"level_enter","to":21},{"type":"attempt_anchor","level":21}]', '[{"type":"warp_entered","level":21}]', '[]', '2026-06-11T00:00:00Z'),
      ('Bowser 1', 1, '[{"type":"level_enter","to":30},{"type":"attempt_anchor","level":30}]', '[{"type":"key_grabbed","level":30}]', '[]', '2026-06-11T00:00:00Z'),
      ('Bowser 2', 1, '[{"type":"level_enter","to":33},{"type":"attempt_anchor","level":33}]', '[{"type":"key_grabbed","level":33}]', '[]', '2026-06-11T00:00:00Z'),
      ('Bowser 3', 1, '[{"type":"level_enter","to":34},{"type":"attempt_anchor","level":34}]', '[{"type":"key_grabbed","level":34}]', '[]', '2026-06-11T00:00:00Z');
    """,
]

_ATTEMPT_COLS = ("id", "session_id", "course_id", "star_id", "strat_tag",
                 "anchor_type", "anchor_frame", "outcome", "outcome_detail",
                 "igt_frames", "rta_frames", "started_utc", "ended_utc",
                 "cleared", "cleared_reason",
                 "rollouts_total", "rollouts_dustless",
                 "jumps_total", "jumps_dustless",
                 "segment_id")


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
                # One transaction per entry: a mid-migration crash rolls back
                # BOTH the partial schema changes and the version write
                # (PRAGMA user_version is a header field — transactional).
                # Without this, a crash inside v4's DROP/RENAME leaves no pbs
                # table, and re-opening dies on the duplicate-column ALTER.
                try:
                    self._conn.executescript(
                        f"BEGIN;{script};PRAGMA user_version = {i};COMMIT;")
                except Exception:
                    # a failed statement leaves the explicit txn open on the
                    # connection (write lock held) — release it before
                    # re-raising so a retry/reopen isn't "database is locked"
                    if self._conn.in_transaction:
                        self._conn.execute("ROLLBACK")
                    raise

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

    def delete_events(self, ids: list[int]) -> None:
        with self._lock:
            self._conn.executemany("DELETE FROM events WHERE id=?",
                                   [(i,) for i in ids])
            self._conn.commit()

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
                int(a.cleared), a.cleared_reason,
                a.rollouts_total, a.rollouts_dustless,
                a.jumps_total, a.jumps_dustless,
                a.segment_id)

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

    # -- segment definitions -------------------------------------------------
    def segment_defs(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM segment_defs ORDER BY id").fetchall()
        return [{"id": r["id"], "name": r["name"],
                 "enabled": bool(r["enabled"]),
                 "start_triggers": json.loads(r["start_triggers"]),
                 "end_triggers": json.loads(r["end_triggers"]),
                 "guards": json.loads(r["guards"]),
                 "created_utc": r["created_utc"]} for r in rows]

    def insert_segment_def(self, name: str, start_triggers: list,
                           end_triggers: list, guards: list,
                           created_utc: str, enabled: bool = True) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO segment_defs (name, enabled, start_triggers,"
                " end_triggers, guards, created_utc) VALUES (?,?,?,?,?,?)",
                (name, int(enabled), json.dumps(start_triggers),
                 json.dumps(end_triggers), json.dumps(guards), created_utc))
            self._conn.commit()
            return cur.lastrowid

    def update_segment_def(self, def_id: int, **fields) -> None:
        cols = {"name": lambda v: v, "enabled": int,
                "start_triggers": json.dumps, "end_triggers": json.dumps,
                "guards": json.dumps}
        if set(fields) - set(cols):
            raise ValueError(f"unknown fields {sorted(set(fields) - set(cols))}")
        sets, vals = [], []
        for k, conv in cols.items():
            if k in fields:
                sets.append(f"{k}=?"); vals.append(conv(fields[k]))
        if not sets:
            return
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE segment_defs SET {','.join(sets)} WHERE id=?",
                (*vals, def_id))
            self._conn.commit()
        if cur.rowcount == 0:
            raise LookupError(f"segment {def_id} not found")

    def delete_segment_def(self, def_id: int) -> None:
        # attempts cache rows are NOT touched — callers must re-project
        # (mirrors delete_session)
        with self._lock:
            cur = self._conn.execute("DELETE FROM segment_defs WHERE id=?",
                                     (def_id,))
            self._conn.execute("DELETE FROM pbs WHERE segment_id=?",
                               (def_id,))  # spec: cascade — nothing to refer to
            self._conn.commit()
        if cur.rowcount == 0:
            raise LookupError(f"segment {def_id} not found")

    # -- pbs -----------------------------------------------------------------
    def insert_pb(self, course_id: int | None, star_id: int | None,
                  strat_tag: str | None, timer_mode: str, frames: int,
                  attempt_id: int | None, saved_utc: str,
                  segment_id: int | None = None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO pbs (course_id, star_id, segment_id, strat_tag,"
                " timer_mode, frames, attempt_id, saved_utc)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (course_id, star_id, segment_id, strat_tag, timer_mode,
                 frames, attempt_id, saved_utc))
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
