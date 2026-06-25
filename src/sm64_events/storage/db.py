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
    -- EDITING A SEED VALUE BELOW? It only reaches FRESH dbs — existing ones
    -- (the live data/tracker.db, carried across sessions) NEVER re-read this
    -- seed. Ship a paired repair migration that UPDATEs the live rows, guarded
    -- on the exact OLD value so user customizations survive (see v5 LBLJ, v6
    -- Bowser 3). Omitting it leaves every existing db on the broken value —
    -- exactly how Bowser 3 shipped ending on star_grabbed for weeks.
    INSERT INTO segment_defs (name, enabled, start_triggers, end_triggers, guards, created_utc) VALUES
      ('LBLJ', 1, '[{"type":"level_enter","to":6,"from":16},{"type":"attempt_anchor","level":6,"area":1}]', '[{"type":"level_enter","to":17}]', '[]', '2026-06-11T00:00:00Z'),
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
    # v5 — warp-menu arming (live gate 2026-06-12): the Usamune warp menu
    # (06 01 00) deposits Mario at the castle lobby entrance — equivalent to
    # the grounds→lobby door — emitting only a practice_reset (menu pause →
    # warp → IGT reset; no level edge), so a level_enter-only LBLJ never
    # armed.  LBLJ gains an area-scoped attempt_anchor (lobby = area 1;
    # scoping prevents basement respawns from cross-arming).  Fresh DBs get
    # the new triggers from the edited v4 seed above; this entry repairs
    # existing DBs.  Name-guarded so a user-renamed/repurposed row id 1 is
    # left alone.
    """
    UPDATE segment_defs SET start_triggers='[{"type":"level_enter","to":6,"from":16},{"type":"attempt_anchor","level":6,"area":1}]' WHERE id=1 AND name='LBLJ';
    """,
    # v6 — grand-star repair (live report 2026-06-12): the B3 grand star is
    # NOT a collectable star — it enters ACT_JUMBO_STAR_CUTSCENE, never a
    # star-dance action, so it fires key_grabbed which='grand' and NEVER
    # star_collected (detectors/key.py; addresses.py FIGHT_END_LEVELS).  The
    # ORIGINAL v4 seed (commit c9a03cd) ended Bowser 3 on star_grabbed, which
    # the grand star can never satisfy — the segment armed but never
    # completed.  419c4e6 corrected the v4 seed for FRESH DBs but, unlike the
    # v5 LBLJ fix, shipped no repair for EXISTING ones, so every db seeded
    # before it kept the broken trigger.  This is that repair, mirroring v5.
    # Triple-guarded (id + name + the EXACT broken seed value) so a
    # user-renamed or deliberately re-pointed row is left untouched.
    """
    UPDATE segment_defs SET end_triggers='[{"type":"key_grabbed","level":34}]' WHERE id=10 AND name='Bowser 3' AND end_triggers='[{"type":"star_grabbed"}]';
    """,
    # v7 — routes: ordered star/segment practice plans (spec 2026-06-14).
    # Config like segment_defs (NOT history); steps is JSON, see
    # tracking/routes.py for the shape. The runs table arrives in v8 (Phase D).
    """
    CREATE TABLE IF NOT EXISTS routes (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      steps TEXT NOT NULL,
      created_utc TEXT NOT NULL,
      updated_utc TEXT NOT NULL
    );
    """,
    # v8 — runs: full-game run history (spec 2026-06-14, Phase D). Cache like
    # attempts, rebuilt from the journal (run_started + completions + resets).
    # route_steps/splits are JSON; id = the game_reset journal id that started
    # the run. Times stored offset-free; display adds start_offset_ms.
    """
    CREATE TABLE IF NOT EXISTS runs (
      id INTEGER PRIMARY KEY,
      route_id INTEGER,
      route_name TEXT NOT NULL,
      route_steps TEXT NOT NULL,
      mode TEXT NOT NULL,
      status TEXT NOT NULL,
      reached_step INTEGER NOT NULL,
      total_ms INTEGER,
      start_offset_ms INTEGER NOT NULL DEFAULT 0,
      started_utc TEXT NOT NULL,
      ended_utc TEXT NOT NULL,
      is_pb INTEGER NOT NULL DEFAULT 0,
      splits TEXT NOT NULL
    );
    """,
    # v9 — per-route run-start condition (spec 2026-06-15). The run clock starts
    # when this trigger fires; existing routes default to the game reset (F1).
    """
    ALTER TABLE routes ADD COLUMN start_condition TEXT NOT NULL
      DEFAULT '{"type":"reset_game"}';
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

    # -- routes (config) -----------------------------------------------------
    def routes(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM routes ORDER BY id").fetchall()
        return [{"id": r["id"], "name": r["name"],
                 "steps": json.loads(r["steps"]),
                 "start_condition": json.loads(r["start_condition"]),
                 "created_utc": r["created_utc"],
                 "updated_utc": r["updated_utc"]} for r in rows]

    def insert_route(self, name: str, steps: list, created_utc: str,
                     start_condition: dict | None = None) -> int:
        sc = start_condition if start_condition is not None else {"type": "reset_game"}
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO routes (name, steps, start_condition, created_utc, updated_utc)"
                " VALUES (?,?,?,?,?)",
                (name, json.dumps(steps), json.dumps(sc), created_utc, created_utc))
            self._conn.commit()
            return cur.lastrowid

    def update_route(self, route_id: int, **fields) -> None:
        cols = {"name": lambda v: v, "steps": json.dumps,
                "start_condition": json.dumps,
                "updated_utc": lambda v: v}
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
                f"UPDATE routes SET {','.join(sets)} WHERE id=?",
                (*vals, route_id))
            self._conn.commit()
        if cur.rowcount == 0:
            raise LookupError(f"route {route_id} not found")

    def delete_route(self, route_id: int) -> None:
        with self._lock:
            cur = self._conn.execute("DELETE FROM routes WHERE id=?",
                                     (route_id,))
            self._conn.commit()
        if cur.rowcount == 0:
            raise LookupError(f"route {route_id} not found")

    # -- runs (history cache) ------------------------------------------------
    _RUN_COLS = ("id", "route_id", "route_name", "route_steps", "mode",
                 "status", "reached_step", "total_ms", "start_offset_ms",
                 "started_utc", "ended_utc", "is_pb", "splits")

    def _run_params(self, r: dict) -> tuple:
        return (r["id"], r["route_id"], r["route_name"],
                json.dumps(r["route_steps"]), r["mode"], r["status"],
                r["reached_step"], r["total_ms"], r["start_offset_ms"],
                r["started_utc"], r["ended_utc"], int(r["is_pb"]),
                json.dumps(r["splits"]))

    def insert_run(self, r: dict) -> None:
        with self._lock:
            self._conn.execute(
                f"INSERT OR REPLACE INTO runs ({','.join(self._RUN_COLS)})"
                f" VALUES ({','.join('?' * len(self._RUN_COLS))})",
                self._run_params(r))
            self._conn.commit()

    upsert_run = insert_run   # same INSERT OR REPLACE (id is stable)

    def replace_runs(self, runs: list) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM runs")
            self._conn.executemany(
                f"INSERT INTO runs ({','.join(self._RUN_COLS)})"
                f" VALUES ({','.join('?' * len(self._RUN_COLS))})",
                [self._run_params(r) for r in runs])
            self._conn.commit()

    def runs(self, route_id: int | None = None,
             finished_only: bool = False) -> list[dict]:
        q, params = "SELECT * FROM runs", []
        where = []
        if route_id is not None:
            where.append("route_id=?"); params.append(route_id)
        if finished_only:
            where.append("status='finished'")
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY id"
        with self._lock:
            rows = self._conn.execute(q, params).fetchall()
        return [{"id": r["id"], "route_id": r["route_id"],
                 "route_name": r["route_name"],
                 "route_steps": json.loads(r["route_steps"]), "mode": r["mode"],
                 "status": r["status"], "reached_step": r["reached_step"],
                 "total_ms": r["total_ms"], "start_offset_ms": r["start_offset_ms"],
                 "started_utc": r["started_utc"], "ended_utc": r["ended_utc"],
                 "is_pb": bool(r["is_pb"]), "splits": json.loads(r["splits"])}
                for r in rows]

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

    def current_pb(self, course_id: int | None, star_id: int | None,
                   timer_mode: str, segment_id: int | None = None,
                   strat_tag: str | None = None) -> dict | None:
        """Latest saved row for one star/segment + mode — the same row
        views._current_pbs picks (later saves win). Kind-aware like
        insert_pb: segment rows match by segment_id, star rows by
        course+star (segment_id IS NULL keeps the kinds disjoint).

        When strat_tag is given, restricts to PBs achieved WITH that
        strategy — the per-strategy ranking lookup (only a strategy's own
        times count toward its rank; the overall/strat-blind PB never does)."""
        strat_clause = " AND strat_tag=?" if strat_tag is not None else ""
        strat_param = (strat_tag,) if strat_tag is not None else ()
        if segment_id is not None:
            q = ("SELECT * FROM pbs WHERE segment_id=? AND timer_mode=?"
                 + strat_clause + " ORDER BY id DESC LIMIT 1")
            params = (segment_id, timer_mode) + strat_param
        else:
            q = ("SELECT * FROM pbs WHERE course_id=? AND star_id=?"
                 " AND segment_id IS NULL AND timer_mode=?"
                 + strat_clause + " ORDER BY id DESC LIMIT 1")
            params = (course_id, star_id, timer_mode) + strat_param
        with self._lock:
            row = self._conn.execute(q, params).fetchone()
            return dict(row) if row else None

    def delete_pb(self, pb_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM pbs WHERE id=?", (pb_id,))
            self._conn.commit()

    def delete_pbs_for_attempts(self, attempt_ids: list[int]) -> None:
        """Session-scoped wipes: drop pb rows saved from the wiped attempts
        so the previous PB (latest remaining row) restores automatically."""
        with self._lock:
            self._conn.executemany("DELETE FROM pbs WHERE attempt_id=?",
                                   [(i,) for i in attempt_ids])
            self._conn.commit()

    def delete_pbs_for_star(self, course_id: int, star_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM pbs WHERE course_id=? AND star_id=?"
                " AND segment_id IS NULL", (course_id, star_id))
            self._conn.commit()

    def delete_pbs_for_segment(self, segment_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM pbs WHERE segment_id=?",
                               (segment_id,))
            self._conn.commit()

    def wipe_all_history(self, keep_session_id: int) -> None:
        """Factory-reset of practice HISTORY: every journal event, every pb,
        every session row except the active one (it stays open and keeps
        receiving events). Segment definitions and ui_state survive — they
        are user configuration, not history. Callers must re-project."""
        with self._lock:
            self._conn.execute("DELETE FROM events")
            self._conn.execute("DELETE FROM pbs")
            self._conn.execute("DELETE FROM sessions WHERE id<>?",
                               (keep_session_id,))
            self._conn.commit()

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
