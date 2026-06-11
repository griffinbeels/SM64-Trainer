# Practice Tracker Phase 1 (Tracking Core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the event broadcaster into a practice tracker: attempts (anchor→outcome, both clocks), SQLite journal + derived views, per-star stats/PBs/links, REST API, and a tabbed Preact UI — spec §11 Phase 1, delivering features #3, #4, #6, #9, #11(reset failures).

**Architecture:** Events flow poller → detectors → `TrackerService` (journals to SQLite, feeds an incremental attempt projection, emits derived events) → broadcaster → WS/UI. Attempts are a **pure projection** of the journal (`project(events) → attempts`), materialized in an `attempts` table; clear/restore commands append journal events and re-run the projection, which is how retroactive target re-attribution works. Spec: `docs/superpowers/specs/2026-06-10-practice-tracker-platform-design.md`.

**Tech Stack:** Python 3.12 / uv, FastAPI + uvicorn, sqlite3 (stdlib, WAL), pytest + httpx TestClient, vendored Preact+htm (zero-build UI).

**Conventions for every task:** run tests with `uv run pytest -q` from the repo root (never pip, never bare pytest). Commit after each green task with the message given. All timestamps UTC ISO-8601 with `Z` suffix. Frames are 30 fps game frames.

**Windows shell note:** this machine runs PowerShell 5.1 — never chain shell commands with `&&`/`||`; every shell snippet below is one command per line. The `&&`/`||` tokens you will see inside JavaScript code blocks are JS syntax (JSX conditionals, defaults) and belong in the .js files verbatim.

---

## File structure (Phase 1 end state)

```
src/sm64_events/
  core/timefmt.py          NEW  format_igt (moved out of star_grab)
  detectors/anchors.py     NEW  AnchorDetector: practice_reset / state_loaded
  detectors/lifecycle.py   MOD  game_reset narrowed to boot-range resets
  detectors/star_grab.py   MOD  import format_igt from core.timefmt
  storage/__init__.py      NEW
  storage/db.py            NEW  Database (sqlite3, migrations, all table I/O), EventRow
  tracking/__init__.py     NEW
  tracking/projection.py   NEW  Attempt dataclass, Projector, project()/replay()
  tracking/service.py      NEW  TrackerService (pipeline: journal→project→broadcast, commands)
  tracking/views.py        NEW  build_session_view() for GET /api/session
  stats/__init__.py        NEW
  stats/registry.py        NEW  StatDef registry + built-in stats
  links.py                 NEW  per-star external link registry
  server/broadcaster.py    MOD  publish() returns seq
  server/api.py            NEW  REST router
  server/app.py            MOD  service wiring, /ui static mount, /health extension
  main.py                  MOD  composition root wires db + service
  ui/index.html            MOD  app shell (importmap, styles, mount point)
  ui/app.js                NEW  Preact root: header + tabs
  ui/api.js                NEW  fetch helpers
  ui/store.js              NEW  session state hook + WS subscription
  ui/components/header.js  NEW  status / session / target / clock controls
  ui/components/practice.js NEW star sections (times, clear, PB, chips)
  ui/components/statmenu.js NEW stat menu popover
  ui/components/feed.js    NEW  live feed tab (port of current viewer)
  ui/vendor/               NEW  preact.module.js, hooks.module.js, htm.module.js
tests/
  test_timefmt.py, test_storage.py, test_anchors.py, test_projection.py,
  test_tracker_service.py, test_stats.py, test_links.py, test_views.py, test_api.py
data/                      NEW  tracker.db lives here (gitignored)
```

Detector order in `main.py`: `[GameResetDetector(), AnchorDetector(), StarGrabDetector()]` — resets before grabs (existing domain rule).

---

### Task 1: Move `format_igt` to `core/timefmt.py`

Stats and views need the time formatter; they must not import from the detectors layer.

**Files:**
- Create: `src/sm64_events/core/timefmt.py`
- Modify: `src/sm64_events/detectors/star_grab.py`
- Test: `tests/test_timefmt.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_timefmt.py
from sm64_events.core.timefmt import format_igt


def test_format_igt():
    assert format_igt(0) == "0'00\"00"
    assert format_igt(231) == "0'07\"70"
    assert format_igt(1800) == "1'00\"00"
    assert format_igt(1800 + 65) == "1'02\"16"
```

- [ ] **Step 2: Run it — expect import failure**

Run: `uv run pytest tests/test_timefmt.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sm64_events.core.timefmt'`

- [ ] **Step 3: Create the module and re-point star_grab**

```python
# src/sm64_events/core/timefmt.py
"""Usamune timer display format: M'SS"CC (30 fps frames -> centiseconds)."""


def format_igt(frames: int) -> str:
    mins = frames // 1800
    secs = (frames % 1800) // 30
    cents = (frames % 30) * 100 // 30
    return f"{mins}'{secs:02d}\"{cents:02d}"
```

In `src/sm64_events/detectors/star_grab.py`: delete the local `format_igt` definition (lines 28–33) and add to the imports:

```python
from sm64_events.core.timefmt import format_igt
```

`tests/test_star_grab.py` imports `format_igt` from `sm64_events.detectors.star_grab` — the re-import keeps that working; do not change the test.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass (including the existing `test_igt_format` in test_star_grab.py).

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/core/timefmt.py src/sm64_events/detectors/star_grab.py tests/test_timefmt.py
git commit -m "refactor: move format_igt to core/timefmt so stats can use it without importing detectors"
```

---

### Task 2: Storage — `Database` with schema v1

One class owns the SQLite file: migrations, journal, attempts cache, sessions, PBs, ui_state. Synchronous sqlite3 with a lock (writes are tiny and rare); WAL mode.

**Files:**
- Create: `src/sm64_events/storage/__init__.py` (empty), `src/sm64_events/storage/db.py`
- Modify: `.gitignore` (add `data/`)
- Test: `tests/test_storage.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_storage.py
import json
from datetime import datetime, timezone

from sm64_events.core.events import Event
from sm64_events.storage.db import Database

T0 = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)


def make_db(tmp_path) -> Database:
    return Database(tmp_path / "t.db")


def ev(type_="star_collected", frame=100, payload=None) -> Event:
    return Event(type=type_, frame=frame, timestamp_utc=T0, payload=payload or {})


def test_migrations_set_user_version_and_create_tables(tmp_path):
    db = make_db(tmp_path)
    names = {r["name"] for r in db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"events", "sessions", "attempts", "pbs", "ui_state"} <= names
    assert db._conn.execute("PRAGMA user_version").fetchone()[0] == 1


def test_reopening_existing_db_is_idempotent(tmp_path):
    make_db(tmp_path).close()
    db = make_db(tmp_path)  # second open: migrations must not re-run/crash
    assert db._conn.execute("PRAGMA user_version").fetchone()[0] == 1


def test_journal_append_and_read_back(tmp_path):
    db = make_db(tmp_path)
    sid = db.insert_session("2026-06-10T12:00:00Z")
    jid = db.append_event(sid, seq=7, event=ev(payload={"course_id": 2}))
    rows = db.events()
    assert len(rows) == 1 and rows[0].id == jid
    assert rows[0].session_id == sid and rows[0].seq == 7
    assert rows[0].type == "star_collected" and rows[0].frame == 100
    assert rows[0].payload == {"course_id": 2}
    assert rows[0].wall_time_utc == "2026-06-10T12:00:00Z"


def test_sessions_insert_and_end(tmp_path):
    db = make_db(tmp_path)
    sid = db.insert_session("2026-06-10T12:00:00Z", label="evening")
    db.end_session(sid, "2026-06-10T13:00:00Z")
    row = db._conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    assert row["label"] == "evening" and row["ended_utc"] == "2026-06-10T13:00:00Z"


def test_attempts_replace_and_read(tmp_path):
    from sm64_events.tracking.projection import Attempt
    db = make_db(tmp_path)
    a = Attempt(id=10, session_id=1, course_id=2, star_id=2, strat_tag=None,
                anchor_type="practice_reset", anchor_frame=500, outcome="success",
                outcome_detail=None, igt_frames=343, rta_frames=350,
                started_utc="2026-06-10T12:00:00Z", ended_utc="2026-06-10T12:00:12Z",
                cleared=False, cleared_reason=None)
    db.replace_attempts([a])
    assert db.attempts() == [a]
    b = a.__class__(**{**a.__dict__, "outcome": "reset"})
    db.upsert_attempt(b)
    assert db.attempts()[0].outcome == "reset"


def test_pbs_and_ui_state(tmp_path):
    db = make_db(tmp_path)
    db.insert_pb(course_id=2, star_id=2, strat_tag=None, timer_mode="igt",
                 frames=343, attempt_id=10, saved_utc="2026-06-10T12:01:00Z")
    pbs = db.pbs()
    assert pbs[0]["frames"] == 343 and pbs[0]["timer_mode"] == "igt"
    assert db.get_state("stat_menu", default=[1]) == [1]
    db.set_state("stat_menu", [{"key": "best"}])
    assert db.get_state("stat_menu", default=None) == [{"key": "best"}]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_storage.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sm64_events.storage'`

- [ ] **Step 3: Implement**

Create empty `src/sm64_events/storage/__init__.py`. Note: `Attempt` is defined in Task 3's neighbor (`tracking/projection.py`) — to keep this task self-contained, create `src/sm64_events/tracking/__init__.py` (empty) and the dataclass-only first slice of `src/sm64_events/tracking/projection.py` now:

```python
# src/sm64_events/tracking/projection.py
"""Pure attempt projection: journal events in -> attempts out. (Projector
arrives in the projection task; this slice defines the Attempt row shape.)"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Attempt:
    id: int                    # journal id of the attempt's first event
    session_id: int
    course_id: int | None      # None = failure with no declared target yet
    star_id: int | None
    strat_tag: str | None
    anchor_type: str           # practice_reset | state_loaded | none
    anchor_frame: int | None
    outcome: str               # success | reset | hard_reset | abandoned
    outcome_detail: str | None
    igt_frames: int | None
    rta_frames: int | None
    started_utc: str
    ended_utc: str
    cleared: bool
    cleared_reason: str | None
```

```python
# src/sm64_events/storage/db.py
"""SQLite store: append-only event journal + derived/materialized tables.

The journal is the source of truth; `attempts` is a rebuildable cache of
tracking.projection.project(events). Sync sqlite3 behind a lock: writes are
one tiny row per game event, far below any contention threshold."""
import json
import sqlite3
import threading
from pathlib import Path

from sm64_events.core.events import Event
from sm64_events.tracking.projection import Attempt

MIGRATIONS = [
    # v1
    """
    CREATE TABLE sessions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      started_utc TEXT NOT NULL,
      ended_utc TEXT,
      label TEXT
    );
    CREATE TABLE events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      session_id INTEGER NOT NULL REFERENCES sessions(id),
      seq INTEGER NOT NULL,
      type TEXT NOT NULL,
      frame INTEGER NOT NULL,
      wall_time_utc TEXT NOT NULL,
      payload TEXT NOT NULL
    );
    CREATE TABLE attempts (
      id INTEGER PRIMARY KEY,
      session_id INTEGER NOT NULL,
      course_id INTEGER, star_id INTEGER, strat_tag TEXT,
      anchor_type TEXT NOT NULL, anchor_frame INTEGER,
      outcome TEXT NOT NULL, outcome_detail TEXT,
      igt_frames INTEGER, rta_frames INTEGER,
      started_utc TEXT NOT NULL, ended_utc TEXT NOT NULL,
      cleared INTEGER NOT NULL DEFAULT 0, cleared_reason TEXT
    );
    CREATE TABLE pbs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      course_id INTEGER NOT NULL, star_id INTEGER NOT NULL, strat_tag TEXT,
      timer_mode TEXT NOT NULL, frames INTEGER NOT NULL,
      attempt_id INTEGER, saved_utc TEXT NOT NULL
    );
    CREATE TABLE ui_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
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
        rows = self._conn.execute("SELECT * FROM pbs ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    # -- ui_state ------------------------------------------------------------
    def get_state(self, key: str, default):
        row = self._conn.execute("SELECT value FROM ui_state WHERE key=?",
                                 (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_state(self, key: str, value) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO ui_state (key, value) VALUES (?,?)",
                (key, json.dumps(value)))
            self._conn.commit()
```

Append `data/` on its own line to `.gitignore`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_storage.py -q` then `uv run pytest -q`
Expected: PASS / all green.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/storage src/sm64_events/tracking tests/test_storage.py .gitignore
git commit -m "feat: SQLite store — append-only event journal plus derived attempts/sessions/pbs/ui_state"
```

---

### Task 3: `AnchorDetector` + narrow `game_reset` to boot-range resets

`practice_reset` = Usamune IGT dropped to near zero while global_timer ran on. `state_loaded` = global_timer jumped backward to a non-boot value (section/savestate load). `game_reset` now means ONLY a backward jump into the boot range (console reset / ROM reload) so the two detectors never both fire. This narrows the public meaning of `game_reset` — README is updated in Task 12.

**Files:**
- Create: `src/sm64_events/detectors/anchors.py`
- Modify: `src/sm64_events/detectors/lifecycle.py`
- Test: `tests/test_anchors.py`, modify `tests/test_lifecycle.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_anchors.py
from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.anchors import AnchorDetector

ACT_IDLE = 0x0C400201


def snap(timer: int, igt: int = 0) -> GameSnapshot:
    return GameSnapshot(
        wall_time_utc=datetime(2026, 6, 10, tzinfo=timezone.utc),
        global_timer=timer, mario_action=ACT_IDLE, mario_action_timer=0,
        num_stars=5, last_completed_course=1, last_completed_star=3,
        igt_overall=igt)


def test_igt_drop_to_zero_emits_practice_reset():
    events = AnchorDetector().process(snap(1000, igt=500), snap(1002, igt=0))
    assert len(events) == 1
    ev = events[0]
    assert ev.type == "practice_reset" and ev.frame == 1002
    assert ev.payload == {"igt_frames_before": 500}


def test_igt_drop_to_small_value_still_practice_reset():
    # the poll may land a few frames after the zeroing
    events = AnchorDetector().process(snap(1000, igt=500), snap(1004, igt=4))
    assert len(events) == 1 and events[0].type == "practice_reset"


def test_igt_running_normally_is_silent():
    assert AnchorDetector().process(snap(1000, igt=500), snap(1001, igt=501)) == []
    assert AnchorDetector().process(snap(1000, igt=500), snap(1001, igt=500)) == []


def test_igt_drop_to_large_value_is_not_a_practice_reset():
    # e.g. a Usamune timer-mode change; not a retry anchor
    assert AnchorDetector().process(snap(1000, igt=500), snap(1001, igt=300)) == []


def test_backward_global_timer_emits_state_loaded():
    events = AnchorDetector().process(snap(5000, igt=900), snap(3000, igt=120))
    assert len(events) == 1
    ev = events[0]
    assert ev.type == "state_loaded" and ev.frame == 3000
    assert ev.payload == {"igt_frames_restored": 120}


def test_backward_jump_into_boot_range_is_left_to_game_reset():
    assert AnchorDetector().process(snap(5000, igt=900), snap(50, igt=0)) == []


def test_state_loaded_takes_priority_over_practice_reset():
    # a load that also restores a near-zero IGT must classify as state_loaded
    events = AnchorDetector().process(snap(5000, igt=900), snap(3000, igt=3))
    assert [e.type for e in events] == ["state_loaded"]
```

Update `tests/test_lifecycle.py` — replace the whole file:

```python
# tests/test_lifecycle.py
from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.lifecycle import GameResetDetector


def snap(timer: int) -> GameSnapshot:
    return GameSnapshot(
        wall_time_utc=datetime(2026, 6, 10, tzinfo=timezone.utc),
        global_timer=timer, mario_action=0, mario_action_timer=0,
        num_stars=0, last_completed_course=0, last_completed_star=0,
    )


def test_backward_jump_into_boot_range_emits_game_reset():
    events = GameResetDetector().process(snap(5000), snap(100))
    assert len(events) == 1
    assert events[0].type == "game_reset"
    assert events[0].frame == 100


def test_backward_jump_to_midgame_value_is_a_state_load_not_a_reset():
    # savestate/section-state loads are AnchorDetector's state_loaded
    assert GameResetDetector().process(snap(5000), snap(3000)) == []


def test_forward_progress_is_silent():
    assert GameResetDetector().process(snap(100), snap(101)) == []


def test_paused_game_is_silent():
    assert GameResetDetector().process(snap(100), snap(100)) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_anchors.py tests/test_lifecycle.py -q`
Expected: FAIL — anchors module missing; lifecycle's new mid-game test fails.

- [ ] **Step 3: Implement**

```python
# src/sm64_events/detectors/anchors.py
"""Attempt anchors: classify timer discontinuities into retry events.

practice_reset — Usamune level reset / level re-entry: the overall IGT
  drops to near zero while gGlobalTimer keeps running. Payload carries the
  IGT the moment before the drop: that is the failed attempt's duration.
state_loaded — savestate / Usamune section-state load: gGlobalTimer jumps
  backward to a mid-game value (a full-RAM restore rewinds it). Backward
  jumps into the boot range are console resets and belong to game_reset
  (lifecycle.py shares BOOT_TIMER_MAX so exactly one of the two fires).

VERIFY (live gate): confirm with the human that a Usamune SECTION state
load moves global_timer backward (full-RAM restore). If Usamune implements
section states as warps instead, loads will classify as practice_reset —
acceptable for attempt tracking, but the payload distinction matters for
the anchor→outcome clock, so characterize it once on real hardware."""
from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot

BOOT_TIMER_MAX = 120   # global_timer below ~4 s after a backward jump = console reset
NEAR_ZERO_IGT = 30     # IGT below 1 s after a drop = fresh practice reset


class AnchorDetector:
    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        if curr.global_timer < prev.global_timer:
            if curr.global_timer < BOOT_TIMER_MAX:
                return []  # console reset — GameResetDetector owns this
            return [Event(type="state_loaded", frame=curr.global_timer,
                          timestamp_utc=curr.wall_time_utc,
                          payload={"igt_frames_restored": curr.igt_overall})]
        if (curr.igt_overall < prev.igt_overall
                and curr.igt_overall <= NEAR_ZERO_IGT):
            return [Event(type="practice_reset", frame=curr.global_timer,
                          timestamp_utc=curr.wall_time_utc,
                          payload={"igt_frames_before": prev.igt_overall})]
        return []
```

Replace `src/sm64_events/detectors/lifecycle.py`:

```python
# src/sm64_events/detectors/lifecycle.py
"""game_reset: gGlobalTimer moved backward INTO THE BOOT RANGE (console
reset / ROM reload). Mid-game backward jumps are savestate loads and emit
state_loaded from detectors/anchors.py instead — exactly one fires."""
from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.anchors import BOOT_TIMER_MAX


class GameResetDetector:
    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        if curr.global_timer >= prev.global_timer:
            return []
        if curr.global_timer >= BOOT_TIMER_MAX:
            return []  # state load, not a reset — see detectors/anchors.py
        return [Event(type="game_reset", frame=curr.global_timer,
                      timestamp_utc=curr.wall_time_utc, payload={})]
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/detectors/anchors.py src/sm64_events/detectors/lifecycle.py tests/test_anchors.py tests/test_lifecycle.py
git commit -m "feat: anchor detector classifies retries (practice_reset/state_loaded); game_reset narrowed to boot-range jumps"
```

---

### Task 4: Attempt projection (the state machine)

Pure function of the journal. Two passes: collect cleared attempt ids first, then run the sequential state machine — that ordering is what makes "mark grab as mistake" retroactively re-attribute later failures (spec §4).

**Files:**
- Modify: `src/sm64_events/tracking/projection.py` (extend the Task 2 slice)
- Test: `tests/test_projection.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_projection.py
from sm64_events.storage.db import EventRow
from sm64_events.tracking.projection import Projector, cleared_ids, project, replay

W = "2026-06-10T12:00:00Z"


def jev(id, type, frame, payload=None, session_id=1):
    return EventRow(id=id, session_id=session_id, seq=id, type=type,
                    frame=frame, wall_time_utc=W, payload=payload or {})


def star(id, frame, course=2, star_id=2, igt=343):
    return jev(id, "star_collected", frame,
               {"course_id": course, "star_id": star_id, "igt_frames": igt})


def test_anchor_then_grab_is_a_success_attempt_with_both_clocks():
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0}),
        star(2, 1350, igt=343),
    ])
    assert len(attempts) == 1
    a = attempts[0]
    assert a.id == 1 and a.outcome == "success"
    assert a.anchor_type == "practice_reset" and a.anchor_frame == 1000
    assert a.course_id == 2 and a.star_id == 2
    assert a.igt_frames == 343 and a.rta_frames == 350
    assert a.cleared is False


def test_grab_without_anchor_is_a_grab_only_attempt():
    attempts = project([star(5, 2000)])
    a = attempts[0]
    assert a.id == 5 and a.anchor_type == "none"
    assert a.anchor_frame is None and a.rta_frames is None
    assert a.igt_frames == 343 and a.outcome == "success"


def test_new_anchor_closes_open_attempt_as_reset_failure():
    attempts = project([
        star(1, 900),                                          # sets target
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0}),
        jev(3, "practice_reset", 1400, {"igt_frames_before": 380}),
    ])
    assert len(attempts) == 2
    fail = attempts[1]
    assert fail.id == 2 and fail.outcome == "reset"
    assert fail.course_id == 2 and fail.star_id == 2     # attributed to target
    assert fail.igt_frames == 380                        # duration before reset
    assert fail.rta_frames == 400


def test_state_loaded_anchor_gives_rta_clock():
    attempts = project([
        jev(1, "state_loaded", 3000, {"igt_frames_restored": 120}),
        star(2, 3360),
    ])
    assert attempts[0].anchor_type == "state_loaded"
    assert attempts[0].rta_frames == 360


def test_failure_without_any_target_has_null_identity():
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0}),
        jev(2, "practice_reset", 1500, {"igt_frames_before": 480}),
    ])
    assert attempts[0].course_id is None and attempts[0].star_id is None


def test_game_reset_closes_as_hard_reset():
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0}),
        jev(2, "game_reset", 50),
    ])
    assert attempts[0].outcome == "hard_reset"
    assert attempts[0].igt_frames is None
    assert attempts[0].rta_frames is None   # frame went backward: no delta


def test_session_started_closes_open_attempt_as_abandoned():
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0}),
        jev(2, "session_started", 0, {"session_id": 2}),
    ])
    assert attempts[0].outcome == "abandoned"


def test_target_set_command_overrides_attribution():
    attempts = project([
        jev(1, "target_set", 0, {"course_id": 8, "star_id": 2, "strat_tag": "carpetless"}),
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0}),
        jev(3, "practice_reset", 1400, {"igt_frames_before": 380}),
    ])
    a = attempts[0]
    assert (a.course_id, a.star_id, a.strat_tag) == (8, 2, "carpetless")


def test_valid_grab_moves_target_and_strat_persists():
    attempts = project([
        jev(1, "target_set", 0, {"course_id": 8, "star_id": 2, "strat_tag": "x"}),
        star(2, 900, course=2, star_id=2),                     # grab WF — target moves
        jev(3, "practice_reset", 1000, {"igt_frames_before": 0}),
        jev(4, "practice_reset", 1400, {"igt_frames_before": 380}),
    ])
    fail = attempts[1]
    assert (fail.course_id, fail.star_id) == (2, 2)
    assert fail.strat_tag == "x"   # strat is sticky until changed


def test_cleared_grab_does_not_move_target_retroactively():
    # going for SSL (8,2); accidentally grab WF (2,2); failures follow;
    # then the WF grab is marked a mistake -> failures re-attribute to SSL.
    events = [
        jev(1, "target_set", 0, {"course_id": 8, "star_id": 2}),
        star(2, 900, course=2, star_id=2),                     # accidental
        jev(3, "practice_reset", 1000, {"igt_frames_before": 0}),
        jev(4, "practice_reset", 1400, {"igt_frames_before": 380}),
        jev(5, "attempt_cleared", 0, {"attempt_id": 2, "reason": "accidental"}),
    ]
    attempts = project(events)
    grab = next(a for a in attempts if a.id == 2)
    fail = next(a for a in attempts if a.id == 3)
    assert grab.cleared is True and grab.cleared_reason == "accidental"
    assert grab.course_id == 2                  # the grab itself keeps its star
    assert (fail.course_id, fail.star_id) == (8, 2)   # re-attributed
    # restore flips it back
    attempts2 = project(events + [jev(6, "attempt_restored", 0, {"attempt_id": 2})])
    fail2 = next(a for a in attempts2 if a.id == 3)
    assert next(a for a in attempts2 if a.id == 2).cleared is False
    assert (fail2.course_id, fail2.star_id) == (2, 2)


def test_unknown_and_derived_event_types_are_ignored():
    attempts = project([
        jev(1, "emulator_connected", 0),
        jev(2, "attempt_completed", 0, {"attempt_id": 99}),
        jev(3, "level_changed", 0, {"from": 1, "to": 2}),
    ])
    assert attempts == []


def test_cleared_ids_last_action_wins():
    events = [
        jev(1, "attempt_cleared", 0, {"attempt_id": 7, "reason": "oops"}),
        jev(2, "attempt_restored", 0, {"attempt_id": 7}),
        jev(3, "attempt_cleared", 0, {"attempt_id": 9, "reason": "accidental"}),
    ]
    assert cleared_ids(events) == {9: "accidental"}


def test_replay_returns_end_state_projector():
    attempts, proj = replay([
        star(1, 900),
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0}),
    ])
    assert len(attempts) == 1            # the grab closed; the reset is open
    assert isinstance(proj, Projector)
    assert proj.target == (2, 2)
    more = proj.feed(star(3, 1300))
    assert len(more) == 1 and more[0].id == 2 and more[0].outcome == "success"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_projection.py -q`
Expected: FAIL — `ImportError: cannot import name 'Projector'`

- [ ] **Step 3: Implement**

Append to `src/sm64_events/tracking/projection.py` (keep the `Attempt` dataclass from Task 2; extend the module docstring):

```python
"""(append to docstring) Two-pass projection: cleared_ids() first, then the
sequential Projector — so a grab marked "mistake" never moves the practice
target, which retroactively re-attributes every later failure. Attempt ids
are the journal id of the attempt's first event: stable across rebuilds."""

ANCHOR_EVENT_TYPES = ("practice_reset", "state_loaded")


def cleared_ids(events) -> dict[int, str | None]:
    """attempt_id -> reason for attempts whose LAST clear/restore is a clear."""
    cleared: dict[int, str | None] = {}
    for ev in events:
        if ev.type == "attempt_cleared":
            cleared[int(ev.payload["attempt_id"])] = ev.payload.get("reason")
        elif ev.type == "attempt_restored":
            cleared.pop(int(ev.payload["attempt_id"]), None)
    return cleared


class Projector:
    """Sequential pass; feed() returns attempts CLOSED by that event."""

    def __init__(self, cleared: dict[int, str | None] | None = None):
        self._cleared = cleared if cleared is not None else {}
        self.target: tuple[int, int] | None = None
        self.strat_tag: str | None = None
        self._open = None  # EventRow of the open attempt's anchor

    def feed(self, ev) -> list[Attempt]:
        if ev.type in ANCHOR_EVENT_TYPES:
            closed = self._close_by_reset(ev)
            self._open = ev
            return closed
        if ev.type == "star_collected":
            return self._close_by_grab(ev)
        if ev.type == "game_reset":
            return self._close(ev, outcome="hard_reset", igt_frames=None)
        if ev.type == "session_started":
            return self._close(ev, outcome="abandoned", igt_frames=None)
        if ev.type == "target_set":
            self.target = (ev.payload["course_id"], ev.payload["star_id"])
            if "strat_tag" in ev.payload:
                self.strat_tag = ev.payload["strat_tag"]
            return []
        return []

    # -- closers -------------------------------------------------------------
    def _close_by_reset(self, ev) -> list[Attempt]:
        igt = ev.payload.get("igt_frames_before") if ev.type == "practice_reset" else None
        return self._close(ev, outcome="reset", igt_frames=igt)

    def _close_by_grab(self, ev) -> list[Attempt]:
        grabbed = (ev.payload["course_id"], ev.payload["star_id"])
        first = self._open if self._open is not None else ev
        attempt = self._build(
            first=first, close=ev, outcome="success",
            course_id=grabbed[0], star_id=grabbed[1],
            igt_frames=ev.payload.get("igt_frames"))
        self._open = None
        if not attempt.cleared:
            self.target = grabbed  # last VALID grab moves the practice target
        return [attempt]

    def _close(self, ev, outcome: str, igt_frames: int | None) -> list[Attempt]:
        if self._open is None:
            return []
        course_id, star_id = self.target if self.target else (None, None)
        attempt = self._build(first=self._open, close=ev, outcome=outcome,
                              course_id=course_id, star_id=star_id,
                              igt_frames=igt_frames)
        self._open = None
        return [attempt]

    def _build(self, first, close, outcome, course_id, star_id, igt_frames) -> Attempt:
        is_anchored = first.type in ANCHOR_EVENT_TYPES
        rta = (close.frame - first.frame
               if is_anchored and close.frame >= first.frame else None)
        return Attempt(
            id=first.id, session_id=first.session_id,
            course_id=course_id, star_id=star_id, strat_tag=self.strat_tag,
            anchor_type=first.type if is_anchored else "none",
            anchor_frame=first.frame if is_anchored else None,
            outcome=outcome, outcome_detail=None,
            igt_frames=igt_frames, rta_frames=rta,
            started_utc=first.wall_time_utc, ended_utc=close.wall_time_utc,
            cleared=first.id in self._cleared,
            cleared_reason=self._cleared.get(first.id))


def replay(events) -> tuple[list[Attempt], Projector]:
    proj = Projector(cleared_ids(events))
    attempts: list[Attempt] = []
    for ev in events:
        attempts.extend(proj.feed(ev))
    return attempts, proj


def project(events) -> list[Attempt]:
    return replay(events)[0]
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/projection.py tests/test_projection.py
git commit -m "feat: attempt projection — anchor->outcome state machine with retroactive target re-attribution"
```

---

### Task 5: `TrackerService` — the event pipeline + commands

The poller's `broadcaster` slot gets the service (duck-typed: it only needs `.publish`). The service broadcasts, journals, feeds the projector, persists closed attempts, and emits derived events. Commands append journal events through the same path; clear/restore trigger a full re-projection. With no database it degrades to broadcast-only (spec §9).

**Files:**
- Modify: `src/sm64_events/server/broadcaster.py` (publish returns seq)
- Create: `src/sm64_events/tracking/service.py`
- Test: `tests/test_tracker_service.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_tracker_service.py
import asyncio
from datetime import datetime, timezone

from sm64_events.core.events import Event
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.storage.db import Database
from sm64_events.tracking.service import TrackerService

T0 = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)


def ev(type_, frame, payload=None):
    return Event(type=type_, frame=frame, timestamp_utc=T0, payload=payload or {})


def star(frame, course=2, star_id=2, igt=343):
    return ev("star_collected", frame,
              {"course_id": course, "star_id": star_id, "igt_frames": igt})


def make(tmp_path):
    db = Database(tmp_path / "t.db")
    svc = TrackerService(db, Broadcaster())
    asyncio.run(svc.start())
    return db, svc


def test_start_creates_session_and_journals_it(tmp_path):
    db, svc = make(tmp_path)
    assert svc.session_id == 1
    assert [e.type for e in db.events()] == ["session_started"]


def test_events_are_journaled_and_attempts_persisted(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350)))
    attempts = db.attempts()
    assert len(attempts) == 1 and attempts[0].outcome == "success"
    types = [e.type for e in db.events()]
    assert "attempt_completed" in types          # derived event journaled too
    assert "target_changed" in types


def test_broadcaster_seq_returned_and_stored(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(star(900)))
    rows = db.events()
    assert all(r.seq > 0 for r in rows)
    assert len({r.seq for r in rows}) == len(rows)   # distinct seqs


def test_set_target_and_attribution(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.set_target(8, 2, strat_tag="carpetless"))
    assert svc.target == (8, 2) and svc.strat_tag == "carpetless"
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(ev("practice_reset", 1400, {"igt_frames_before": 380})))
    fails = [a for a in db.attempts() if a.outcome == "reset"]
    assert (fails[0].course_id, fails[0].star_id) == (8, 2)


def test_clear_reprojects_and_restore_undoes(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.set_target(8, 2))
    asyncio.run(svc.publish(star(900)))            # accidental WF grab
    grab_id = db.attempts()[0].id
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(ev("practice_reset", 1400, {"igt_frames_before": 380})))
    asyncio.run(svc.clear_attempt(grab_id, reason="accidental"))
    fails = [a for a in db.attempts() if a.outcome == "reset"]
    assert (fails[0].course_id, fails[0].star_id) == (8, 2)   # re-attributed
    assert svc.target == (8, 2)
    asyncio.run(svc.restore_attempt(grab_id))
    fails = [a for a in db.attempts() if a.outcome == "reset"]
    assert (fails[0].course_id, fails[0].star_id) == (2, 2)


def test_save_pb_inserts_row_and_journals(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350)))
    aid = db.attempts()[0].id
    pb = asyncio.run(svc.save_pb(aid, "igt"))
    assert pb["frames"] == 343 and db.pbs()[0]["course_id"] == 2
    assert "pb_saved" in [e.type for e in db.events()]


def test_save_pb_rejects_missing_attempt(tmp_path):
    import pytest
    db, svc = make(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(svc.save_pb(999, "igt"))


def test_new_session_closes_open_attempt_as_abandoned(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.new_session())
    assert svc.session_id == 2
    assert db.attempts()[0].outcome == "abandoned"


def test_restart_resumes_from_journal(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.set_target(8, 2))
    asyncio.run(svc.publish(star(900)))
    db2 = Database(tmp_path / "t.db")
    svc2 = TrackerService(db2, Broadcaster())
    asyncio.run(svc2.start())
    assert svc2.session_id == 2
    assert svc2.target == (2, 2)        # state rebuilt from journal


def test_degraded_mode_without_db_still_broadcasts(tmp_path):
    svc = TrackerService(None, Broadcaster())
    asyncio.run(svc.start())
    asyncio.run(svc.publish(star(900)))   # must not raise
    assert svc.session_id is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_tracker_service.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sm64_events.tracking.service'`

- [ ] **Step 3: Implement**

In `src/sm64_events/server/broadcaster.py`, change `publish` to return the seq (one-line signature + return; nothing else changes):

```python
    async def publish(self, event: Event) -> int:
        self._seq += 1
        wire = to_wire(event, self._seq)
        log.info("event %s", json.dumps(wire))
        dead = []
        for ws in list(self._clients):
            try:
                await ws.send_json(wire)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)
        return self._seq
```

```python
# src/sm64_events/tracking/service.py
"""Event pipeline + command surface.

The Poller publishes here (duck-typed broadcaster). Order per event:
broadcast first (liveness is never gated on the db), then journal, then
feed the projector; attempts closed by the event are persisted and an
attempt_completed derived event is emitted through the same pipeline
(the projector ignores derived types, so this cannot recurse).

Commands (set_target, clear/restore, save_pb, new_session) append journal
events through the same path so the journal stays the single source of
truth; clear/restore re-run the full projection because their effect is
retroactive. With db=None the service degrades to broadcast-only."""
import logging
from datetime import datetime, timezone

from sm64_events.core.events import Event
from sm64_events.core.timefmt import format_igt
from sm64_events.memory.addresses import course_name, star_name
from sm64_events.storage.db import Database, EventRow
from sm64_events.tracking.projection import Projector, cleared_ids, replay

log = logging.getLogger("sm64.tracker")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


class TrackerService:
    def __init__(self, db: Database | None, broadcaster):
        self.db = db
        self.broadcaster = broadcaster
        self.session_id: int | None = None
        self._projector = Projector()

    # -- pipeline -------------------------------------------------------------
    async def start(self) -> None:
        if self.db is None:
            log.error("tracker running WITHOUT a database (broadcast-only)")
            return
        events = self.db.events()
        attempts, self._projector = replay(events)
        self.db.replace_attempts(attempts)
        self.session_id = self.db.insert_session(_iso(_now()))
        await self.publish(Event(type="session_started", frame=0,
                                 timestamp_utc=_now(),
                                 payload={"session_id": self.session_id}))

    async def publish(self, event: Event) -> None:
        seq = await self.broadcaster.publish(event)
        if self.db is None or self.session_id is None:
            return
        try:
            jid = self.db.append_event(self.session_id, seq, event)
        except Exception:
            log.exception("journal write failed; event broadcast only")
            return
        row = EventRow(id=jid, session_id=self.session_id, seq=seq,
                       type=event.type, frame=event.frame,
                       wall_time_utc=_iso(event.timestamp_utc),
                       payload=event.payload)
        target_before = self._projector.target
        for attempt in self._projector.feed(row):
            self.db.upsert_attempt(attempt)
            await self.publish(self._attempt_completed_event(attempt, event))
        if self._projector.target != target_before:
            await self.publish(Event(
                type="target_changed", frame=event.frame,
                timestamp_utc=event.timestamp_utc,
                payload=self._target_payload()))

    def _attempt_completed_event(self, a, close_event: Event) -> Event:
        return Event(type="attempt_completed", frame=close_event.frame,
                     timestamp_utc=close_event.timestamp_utc, payload={
                         "attempt_id": a.id, "session_id": a.session_id,
                         "course_id": a.course_id, "star_id": a.star_id,
                         "course_name": course_name(a.course_id) if a.course_id is not None else None,
                         "star_name": star_name(a.course_id, a.star_id) if a.course_id is not None else None,
                         "strat_tag": a.strat_tag,
                         "anchor_type": a.anchor_type, "outcome": a.outcome,
                         "outcome_detail": a.outcome_detail,
                         "igt_frames": a.igt_frames,
                         "igt": format_igt(a.igt_frames) if a.igt_frames is not None else None,
                         "rta_frames": a.rta_frames,
                     })

    def _target_payload(self) -> dict:
        c, s = self._projector.target if self._projector.target else (None, None)
        return {"course_id": c, "star_id": s,
                "strat_tag": self._projector.strat_tag}

    # -- state ------------------------------------------------------------------
    @property
    def target(self):
        return self._projector.target

    @property
    def strat_tag(self):
        return self._projector.strat_tag

    def _require_db(self) -> Database:
        if self.db is None or self.session_id is None:
            raise RuntimeError("database unavailable")
        return self.db

    # -- commands ----------------------------------------------------------------
    async def set_target(self, course_id: int, star_id: int,
                         strat_tag: str | None = None) -> None:
        self._require_db()
        payload = {"course_id": course_id, "star_id": star_id}
        if strat_tag is not None:
            payload["strat_tag"] = strat_tag
        await self.publish(Event(type="target_set", frame=0,
                                 timestamp_utc=_now(), payload=payload))

    async def clear_attempt(self, attempt_id: int, reason: str | None = None) -> None:
        db = self._require_db()
        if not any(a.id == attempt_id for a in db.attempts()):
            raise ValueError(f"no attempt {attempt_id}")
        await self.publish(Event(type="attempt_cleared", frame=0,
                                 timestamp_utc=_now(),
                                 payload={"attempt_id": attempt_id, "reason": reason}))
        await self._reproject()

    async def restore_attempt(self, attempt_id: int) -> None:
        self._require_db()
        await self.publish(Event(type="attempt_restored", frame=0,
                                 timestamp_utc=_now(),
                                 payload={"attempt_id": attempt_id}))
        await self._reproject()

    async def _reproject(self) -> None:
        db = self._require_db()
        events = db.events()
        attempts, projector = replay(events)
        # keep the live session: replayed projector state is authoritative
        self._projector = projector
        db.replace_attempts(attempts)
        await self.publish(Event(type="attempts_invalidated", frame=0,
                                 timestamp_utc=_now(), payload={}))

    async def save_pb(self, attempt_id: int, timer_mode: str) -> dict:
        db = self._require_db()
        if timer_mode not in ("igt", "rta"):
            raise ValueError(f"bad timer_mode {timer_mode!r}")
        attempt = next((a for a in db.attempts() if a.id == attempt_id), None)
        if attempt is None or attempt.outcome != "success" or attempt.cleared:
            raise ValueError(f"attempt {attempt_id} is not a saveable success")
        frames = attempt.igt_frames if timer_mode == "igt" else attempt.rta_frames
        if frames is None:
            raise ValueError(f"attempt {attempt_id} has no {timer_mode} clock")
        db.insert_pb(course_id=attempt.course_id, star_id=attempt.star_id,
                     strat_tag=attempt.strat_tag, timer_mode=timer_mode,
                     frames=frames, attempt_id=attempt_id, saved_utc=_iso(_now()))
        payload = {"course_id": attempt.course_id, "star_id": attempt.star_id,
                   "strat_tag": attempt.strat_tag, "timer_mode": timer_mode,
                   "frames": frames, "attempt_id": attempt_id}
        await self.publish(Event(type="pb_saved", frame=0,
                                 timestamp_utc=_now(), payload=payload))
        return payload

    async def new_session(self, label: str | None = None) -> int:
        db = self._require_db()
        db.end_session(self.session_id, _iso(_now()))
        self.session_id = db.insert_session(_iso(_now()), label=label)
        await self.publish(Event(type="session_started", frame=0,
                                 timestamp_utc=_now(),
                                 payload={"session_id": self.session_id,
                                          "label": label}))
        return self.session_id
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass (existing broadcaster tests unaffected — the return value is additive).

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/server/broadcaster.py src/sm64_events/tracking/service.py tests/test_tracker_service.py
git commit -m "feat: tracker service — journal->projection->broadcast pipeline with clear/restore reprojection and PB commands"
```

---

### Task 6: Stats registry

One registry; each stat is `{key, label, fmt, default params, compute}`. Computation always excludes cleared attempts; success times come from the selected clock. `success_rate` takes the failure-outcome set as a param — feature #11's "add failure reasons easily" knob.

**Files:**
- Create: `src/sm64_events/stats/__init__.py` (empty), `src/sm64_events/stats/registry.py`
- Test: `tests/test_stats.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_stats.py
from sm64_events.stats.registry import REGISTRY, compute_stat, registry_meta
from sm64_events.tracking.projection import Attempt


def attempt(id=1, outcome="success", igt=300, rta=310, cleared=False):
    return Attempt(id=id, session_id=1, course_id=2, star_id=2, strat_tag=None,
                   anchor_type="practice_reset", anchor_frame=0,
                   outcome=outcome, outcome_detail=None,
                   igt_frames=igt, rta_frames=rta,
                   started_utc="2026-06-10T12:00:00Z",
                   ended_utc="2026-06-10T12:00:10Z",
                   cleared=cleared, cleared_reason=None)


SAMPLE = [
    attempt(1, igt=300), attempt(2, igt=360),
    attempt(3, outcome="reset", igt=120),
    attempt(4, igt=330, cleared=True),         # cleared: excluded everywhere
    attempt(5, outcome="abandoned"),           # excluded from success_rate
]


def test_avg_last_n():
    assert compute_stat("avg_last_n", SAMPLE, {"n": 1}, clock="igt") == 360
    assert compute_stat("avg_last_n", SAMPLE, {"n": 10}, clock="igt") == 330


def test_avg_lifetime_best_worst_count():
    assert compute_stat("avg_lifetime", SAMPLE, {}, clock="igt") == 330
    assert compute_stat("best", SAMPLE, {}, clock="igt") == 300
    assert compute_stat("worst", SAMPLE, {}, clock="igt") == 360
    assert compute_stat("attempt_count", SAMPLE, {}, clock="igt") == 3


def test_clock_selects_rta():
    assert compute_stat("best", SAMPLE, {}, clock="rta") == 310


def test_success_rate_default_failures():
    # 2 successes, 1 reset -> 2/3
    assert abs(compute_stat("success_rate", SAMPLE, {}, clock="igt") - 2 / 3) < 1e-9


def test_success_rate_custom_failure_set():
    # counting nothing as failure -> 1.0
    assert compute_stat("success_rate", SAMPLE, {"failures": []}, clock="igt") == 1.0


def test_empty_inputs_return_none():
    assert compute_stat("best", [], {}, clock="igt") is None
    assert compute_stat("success_rate", [], {}, clock="igt") is None


def test_registry_meta_is_ui_renderable():
    meta = registry_meta()
    keys = {m["key"] for m in meta}
    assert {"avg_last_n", "avg_lifetime", "best", "worst",
            "success_rate", "attempt_count"} <= keys
    for m in meta:
        assert {"key", "label", "fmt", "params"} <= set(m)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_stats.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

```python
# src/sm64_events/stats/registry.py
"""THE stat registry: adding a stat = adding one StatDef here. The UI's
stat menu renders from registry_meta(); nothing else changes anywhere.

Every compute() sees attempts already scoped to one star by the caller,
ordered by attempt id (chronological). Cleared attempts are excluded here,
in one place. fmt tells the UI how to render: time | percent | int."""
from dataclasses import dataclass, field
from typing import Callable, Sequence

from sm64_events.tracking.projection import Attempt

DEFAULT_FAILURES = ["reset", "hard_reset"]  # 'abandoned' excluded by default


def _live(attempts: Sequence[Attempt]) -> list[Attempt]:
    return [a for a in attempts if not a.cleared]


def _times(attempts: Sequence[Attempt], clock: str) -> list[int]:
    out = []
    for a in _live(attempts):
        if a.outcome != "success":
            continue
        v = a.igt_frames if clock == "igt" else a.rta_frames
        if v is not None:
            out.append(v)
    return out


def _avg_last_n(attempts, params, clock):
    times = _times(attempts, clock)[-int(params["n"]):]
    return sum(times) / len(times) if times else None


def _avg_lifetime(attempts, params, clock):
    times = _times(attempts, clock)
    return sum(times) / len(times) if times else None


def _best(attempts, params, clock):
    times = _times(attempts, clock)
    return min(times) if times else None


def _worst(attempts, params, clock):
    times = _times(attempts, clock)
    return max(times) if times else None


def _attempt_count(attempts, params, clock):
    return len([a for a in _live(attempts) if a.outcome == "success"])


def _success_rate(attempts, params, clock):
    failures = set(params.get("failures", DEFAULT_FAILURES))
    counted = [a for a in _live(attempts)
               if a.outcome == "success" or a.outcome in failures]
    if not counted:
        return None
    wins = sum(1 for a in counted if a.outcome == "success")
    return wins / len(counted)


@dataclass(frozen=True)
class StatDef:
    key: str
    label: str
    fmt: str                                   # time | percent | int
    compute: Callable[[Sequence[Attempt], dict, str], float | int | None]
    params: dict = field(default_factory=dict)  # defaults, UI-overridable


REGISTRY: dict[str, StatDef] = {d.key: d for d in [
    StatDef("avg_last_n", "Avg last N", "time", _avg_last_n, {"n": 10}),
    StatDef("avg_lifetime", "Lifetime avg", "time", _avg_lifetime),
    StatDef("best", "Best", "time", _best),
    StatDef("worst", "Worst", "time", _worst),
    StatDef("attempt_count", "Successes", "int", _attempt_count),
    StatDef("success_rate", "Success rate", "percent", _success_rate,
            {"failures": DEFAULT_FAILURES}),
]}

DEFAULT_STAT_MENU = [
    {"key": "avg_last_n", "params": {"n": 10}},
    {"key": "avg_last_n", "params": {"n": 50}},
    {"key": "best"}, {"key": "worst"}, {"key": "success_rate"},
]


def compute_stat(key: str, attempts: Sequence[Attempt], params: dict,
                 clock: str) -> float | int | None:
    d = REGISTRY[key]
    return d.compute(attempts, {**d.params, **(params or {})}, clock)


def registry_meta() -> list[dict]:
    return [{"key": d.key, "label": d.label, "fmt": d.fmt, "params": d.params}
            for d in REGISTRY.values()]
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/stats tests/test_stats.py
git commit -m "feat: stat registry — adding a stat is one StatDef; success_rate failure set is a param (#11 knob)"
```

---

### Task 7: Links registry

**Files:**
- Create: `src/sm64_events/links.py`
- Test: `tests/test_links.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_links.py
from sm64_events.links import star_links


def test_normal_star_generates_rta_guide_url():
    links = star_links(2, 2)  # WF "Shoot into the Wild Blue"
    assert links["ukikipedia"] == \
        "https://ukikipedia.net/wiki/RTA_Guide/Shoot_into_the_Wild_Blue"
    assert links["example"] is None


def test_punctuation_kept_spaces_underscored():
    links = star_links(4, 0)  # "Slip Slidin' Away"
    assert links["ukikipedia"].endswith("/RTA_Guide/Slip_Slidin'_Away")


def test_100_coin_star_uses_course_abbreviation():
    assert star_links(2, 6)["ukikipedia"] == \
        "https://ukikipedia.net/wiki/RTA_Guide/WF_100_Coins"


def test_override_wins():
    import sm64_events.links as L
    L.OVERRIDES[(2, 2)] = {"example": "https://example.com/wf-wild-blue"}
    try:
        assert star_links(2, 2)["example"] == "https://example.com/wf-wild-blue"
    finally:
        L.OVERRIDES.pop((2, 2))


def test_unknown_star_still_returns_a_wiki_search_link():
    links = star_links(99, 0)
    assert links["ukikipedia"].startswith("https://ukikipedia.net/wiki/RTA_Guide/")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_links.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

```python
# src/sm64_events/links.py
"""Per-star external link registry (feature #9).

Ukikipedia RTA-guide URLs are generated from the star name (spaces ->
underscores, punctuation kept — pattern live-confirmed 2026-06-10);
100-coin stars use the community course abbreviation (WF_100_Coins).
OVERRIDES holds hand-curated URLs (e.g. Ultimate Star Spreadsheet deep
links, which need a one-time manual gid/range harvest). Ukikipedia 403s
bot fetches: links are for the user's browser, never validated here."""
from sm64_events.memory.addresses import star_name

UKIKIPEDIA_RTA = "https://ukikipedia.net/wiki/RTA_Guide/"

COURSE_ABBREV = {
    1: "BoB", 2: "WF", 3: "JRB", 4: "CCM", 5: "BBH", 6: "HMC", 7: "LLL",
    8: "SSL", 9: "DDD", 10: "SL", 11: "WDW", 12: "TTM", 13: "THI",
    14: "TTC", 15: "RR",
}

# (course_id, star_id) -> {"example": url} — hand-curated additions.
OVERRIDES: dict[tuple[int, int], dict] = {}


def star_links(course_id: int, star_id: int) -> dict:
    if star_id == 6 and course_id in COURSE_ABBREV:
        page = f"{COURSE_ABBREV[course_id]}_100_Coins"
    else:
        page = star_name(course_id, star_id).replace(" ", "_")
    override = OVERRIDES.get((course_id, star_id), {})
    return {"ukikipedia": UKIKIPEDIA_RTA + page,
            "example": override.get("example")}
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/links.py tests/test_links.py
git commit -m "feat: per-star link registry — generated Ukikipedia RTA-guide URLs plus manual override slot"
```

---

### Task 8: Session view builder

Assembles the `GET /api/session` payload: star sections for stars seen this session (times list = session attempts; stats computed over lifetime attempts for that star), PBs with per-attempt deltas, links, target, catalog for the target picker.

**Files:**
- Create: `src/sm64_events/tracking/views.py`
- Test: `tests/test_views.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_views.py
import asyncio
from datetime import datetime, timezone

from sm64_events.core.events import Event
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.storage.db import Database
from sm64_events.tracking.service import TrackerService
from sm64_events.tracking.views import build_session_view

T0 = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)


def ev(type_, frame, payload=None):
    return Event(type=type_, frame=frame, timestamp_utc=T0, payload=payload or {})


def star(frame, course=2, star_id=2, igt=343):
    return ev("star_collected", frame,
              {"course_id": course, "star_id": star_id, "igt_frames": igt})


def make(tmp_path):
    db = Database(tmp_path / "t.db")
    svc = TrackerService(db, Broadcaster())
    asyncio.run(svc.start())
    return db, svc


def seed(svc):
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350, igt=343)))
    asyncio.run(svc.publish(ev("practice_reset", 1400, {"igt_frames_before": 380})))
    asyncio.run(svc.publish(ev("practice_reset", 1900, {"igt_frames_before": 470})))
    asyncio.run(svc.publish(star(2400, igt=350)))


def test_view_groups_by_star_with_stats_and_pb_delta(tmp_path):
    db, svc = make(tmp_path)
    seed(svc)
    aid = next(a.id for a in db.attempts() if a.igt_frames == 343)
    asyncio.run(svc.save_pb(aid, "igt"))
    view = build_session_view(db, svc, clock="igt")
    assert view["session"]["id"] == 1
    assert view["clock"] == "igt"
    assert view["target"]["course_id"] == 2 and view["target"]["star_id"] == 2
    [sec] = view["stars"]
    assert sec["course_id"] == 2 and sec["star_id"] == 2
    assert sec["star_name"] == "Shoot into the Wild Blue"
    assert sec["links"]["ukikipedia"].endswith("Shoot_into_the_Wild_Blue")
    assert sec["pb"]["igt"]["frames"] == 343
    # 3 attempts in section (ordered by id): the star at 1350 closed the
    # first anchor as success; the 1400 anchor opened a fresh attempt that
    # the 1900 anchor closed as reset; the 2400 grab closed the last one.
    outcomes = [a["outcome"] for a in sec["attempts"]]
    assert outcomes == ["success", "reset", "success"]
    last = sec["attempts"][-1]
    assert last["igt"] == "0'11\"66" and last["pb_delta_frames"] == 7
    stats = {s["key"]: s for s in sec["stats"]}
    assert stats["best"]["value"] == 343 and stats["best"]["display"] == "0'11\"43"
    assert abs(stats["success_rate"]["value"] - 2 / 3) < 1e-9
    assert stats["success_rate"]["display"] == "67%"


def test_failures_before_any_grab_land_in_unassigned(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(ev("practice_reset", 1400, {"igt_frames_before": 380})))
    view = build_session_view(db, svc, clock="igt")
    assert view["stars"] == []
    assert len(view["unassigned"]) == 1


def test_view_includes_catalog_and_stat_menu(tmp_path):
    db, svc = make(tmp_path)
    view = build_session_view(db, svc, clock="igt")
    courses = {c["id"]: c for c in view["catalog"]["courses"]}
    assert courses[2]["name"] == "Whomp's Fortress"
    assert courses[2]["stars"][2] == "Shoot into the Wild Blue"
    assert any(s["key"] == "avg_last_n" for s in view["stat_menu"])


def test_cleared_attempts_remain_visible_but_flagged(tmp_path):
    db, svc = make(tmp_path)
    seed(svc)
    aid = db.attempts()[0].id
    asyncio.run(svc.clear_attempt(aid, reason="accidental"))
    view = build_session_view(db, svc, clock="igt")
    [sec] = view["stars"]
    flags = {a["id"]: a["cleared"] for a in sec["attempts"]}
    assert flags[aid] is True
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_views.py -q`
Expected: FAIL — `tracking.views` missing.

- [ ] **Step 3: Implement**

```python
# src/sm64_events/tracking/views.py
"""Builds the GET /api/session payload. Times lists are session-scoped;
stat chips compute over the star's full history (lifetime), per spec §8."""
from sm64_events.core.timefmt import format_igt
from sm64_events.links import star_links
from sm64_events.memory.addresses import (COURSE_NAMES, STAR_NAMES,
                                          course_name, star_name)
from sm64_events.stats.registry import DEFAULT_STAT_MENU, REGISTRY, compute_stat


def _fmt(value, fmt):
    if value is None:
        return None
    if fmt == "time":
        return format_igt(round(value))
    if fmt == "percent":
        return f"{round(value * 100)}%"
    return str(value)


def _current_pbs(db) -> dict:
    """(course, star, mode) -> latest pb row."""
    out = {}
    for row in db.pbs():  # ordered by id: later rows win
        out[(row["course_id"], row["star_id"], row["timer_mode"])] = row
    return out


def _attempt_json(a, pbs, clock):
    pb = pbs.get((a.course_id, a.star_id, clock))
    frames = a.igt_frames if clock == "igt" else a.rta_frames
    delta = (frames - pb["frames"]
             if pb and frames is not None and a.outcome == "success" else None)
    return {"id": a.id, "outcome": a.outcome, "outcome_detail": a.outcome_detail,
            "anchor_type": a.anchor_type, "strat_tag": a.strat_tag,
            "igt_frames": a.igt_frames,
            "igt": format_igt(a.igt_frames) if a.igt_frames is not None else None,
            "rta_frames": a.rta_frames,
            "rta": format_igt(a.rta_frames) if a.rta_frames is not None else None,
            "pb_delta_frames": delta, "cleared": a.cleared,
            "cleared_reason": a.cleared_reason, "ended_utc": a.ended_utc}


def _catalog() -> dict:
    courses = []
    for cid, cname in COURSE_NAMES.items():
        n = len(STAR_NAMES.get(cid, ()))
        if 1 <= cid <= 15:
            n = 7  # six named stars + 100 coins
        courses.append({"id": cid, "name": cname,
                        "stars": [star_name(cid, s) for s in range(max(n, 1))]})
    return {"courses": courses}


def build_session_view(db, service, clock: str) -> dict:
    all_attempts = db.attempts()
    session_attempts = [a for a in all_attempts
                        if a.session_id == service.session_id]
    pbs = _current_pbs(db)
    stat_menu = db.get_state("stat_menu", default=DEFAULT_STAT_MENU)

    sections, unassigned = [], []
    seen: list[tuple[int, int]] = []
    for a in session_attempts:
        if a.course_id is None:
            unassigned.append(_attempt_json(a, pbs, clock))
        elif (a.course_id, a.star_id) not in seen:
            seen.append((a.course_id, a.star_id))

    for course_id, star_id in seen:
        history = [a for a in all_attempts
                   if a.course_id == course_id and a.star_id == star_id]
        in_session = [a for a in history if a.session_id == service.session_id]
        stats = []
        for sel in stat_menu:
            if sel["key"] not in REGISTRY:
                continue
            d = REGISTRY[sel["key"]]
            value = compute_stat(sel["key"], history, sel.get("params"), clock)
            label = d.label.replace("N", str(sel.get("params", {}).get("n", ""))) \
                if d.key == "avg_last_n" else d.label
            stats.append({"key": d.key, "label": label,
                          "params": sel.get("params", {}), "fmt": d.fmt,
                          "value": value, "display": _fmt(value, d.fmt)})
        pb_json = {}
        for mode in ("igt", "rta"):
            row = pbs.get((course_id, star_id, mode))
            pb_json[mode] = ({"frames": row["frames"],
                              "display": format_igt(row["frames"])}
                             if row else None)
        sections.append({
            "course_id": course_id, "star_id": star_id,
            "course_name": course_name(course_id),
            "star_name": star_name(course_id, star_id),
            "links": star_links(course_id, star_id),
            "pb": pb_json,
            "attempts": [_attempt_json(a, pbs, clock) for a in in_session],
            "stats": stats,
        })

    tgt_c, tgt_s = service.target if service.target else (None, None)
    return {
        "session": {"id": service.session_id},
        "clock": clock,
        "target": {"course_id": tgt_c, "star_id": tgt_s,
                   "course_name": course_name(tgt_c) if tgt_c is not None else None,
                   "star_name": star_name(tgt_c, tgt_s) if tgt_c is not None else None,
                   "strat_tag": service.strat_tag},
        "stat_menu": stat_menu,
        "catalog": _catalog(),
        "stars": sections,
        "unassigned": unassigned,
    }
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/views.py tests/test_views.py
git commit -m "feat: session view — per-star sections with session times, lifetime stats, PB deltas, links"
```

---

### Task 9: REST API + app/main wiring + detector isolation

**Files:**
- Create: `src/sm64_events/server/api.py`
- Modify: `src/sm64_events/server/app.py`, `src/sm64_events/server/poller.py`, `src/sm64_events/main.py`
- Test: `tests/test_api.py`, `tests/test_poller_isolation.py`

- [ ] **Step 0a: Write the failing detector-isolation test** (spec §9: one bad detector must never kill the poll loop)

```python
# tests/test_poller_isolation.py
import asyncio
from datetime import datetime, timezone

from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.server.poller import Poller


def snap(timer):
    return GameSnapshot(
        wall_time_utc=datetime(2026, 6, 10, tzinfo=timezone.utc),
        global_timer=timer, mario_action=0, mario_action_timer=0,
        num_stars=0, last_completed_course=0, last_completed_star=0)


class FakeMemory:
    attached = True
    def detach(self): self.attached = False


class FakeReader:
    def __init__(self): self.t = 100
    def read(self):
        self.t += 1
        return snap(self.t)


class Boom:
    def process(self, prev, curr):
        raise RuntimeError("boom")


class Emits:
    def process(self, prev, curr):
        return [Event(type="ok", frame=curr.global_timer,
                      timestamp_utc=curr.wall_time_utc, payload={})]


class Recorder:
    def __init__(self): self.events = []
    async def publish(self, event): self.events.append(event)


def test_one_bad_detector_does_not_kill_the_tick_or_starve_others():
    rec = Recorder()
    poller = Poller(FakeMemory(), [Boom(), Emits()], rec, reader=FakeReader())
    asyncio.run(poller.tick())   # primes _prev; no detector runs yet
    asyncio.run(poller.tick())   # Boom raises, Emits must still publish
    assert [e.type for e in rec.events] == ["ok"]
```

Run: `uv run pytest tests/test_poller_isolation.py -q` — expected FAIL with `RuntimeError: boom`.

- [ ] **Step 0b: Isolate detectors in `poller.tick`**

In `src/sm64_events/server/poller.py`, replace the detector loop inside `tick()`:

```python
        if self._prev is not None:
            for detector in self.detectors:
                try:
                    events = detector.process(self._prev, curr)
                except Exception:
                    log.exception("detector %s failed; skipped this tick",
                                  type(detector).__name__)
                    continue
                for event in events:
                    await self.broadcaster.publish(event)
```

Run: `uv run pytest tests/test_poller_isolation.py tests/test_poller.py -q` — expected PASS.

- [ ] **Step 1: Write the failing API tests**

```python
# tests/test_api.py
import asyncio
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from sm64_events.core.events import Event
from sm64_events.server.app import create_app
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.poller import Poller
from sm64_events.storage.db import Database
from sm64_events.tracking.service import TrackerService

T0 = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)


class OfflineMemory:
    attached = False
    def attach(self): return False
    def detach(self): pass


def make_client(tmp_path):
    db = Database(tmp_path / "t.db")
    broadcaster = Broadcaster()
    service = TrackerService(db, broadcaster)
    poller = Poller(OfflineMemory(), [], service)
    app = create_app(poller, broadcaster, service=service)
    return TestClient(app), service, db


def seed(service):
    async def go():
        await service.publish(Event(type="practice_reset", frame=1000,
                                    timestamp_utc=T0,
                                    payload={"igt_frames_before": 0}))
        await service.publish(Event(type="star_collected", frame=1350,
                                    timestamp_utc=T0,
                                    payload={"course_id": 2, "star_id": 2,
                                             "igt_frames": 343}))
    asyncio.run(go())


def test_session_view_roundtrip(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        seed(service)
        r = client.get("/api/session?clock=igt")
        assert r.status_code == 200
        body = r.json()
        assert body["stars"][0]["star_name"] == "Shoot into the Wild Blue"


def test_target_clear_restore_pb_session_endpoints(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        seed(service)
        aid = db.attempts()[0].id
        assert client.post("/api/target", json={
            "course_id": 8, "star_id": 2, "strat_tag": "carpetless"
        }).status_code == 200
        assert service.target == (8, 2)
        r = client.post("/api/pb", json={"attempt_id": aid, "timer_mode": "igt"})
        assert r.status_code == 200 and r.json()["frames"] == 343
        assert client.post(f"/api/attempts/{aid}/clear",
                           json={"reason": "accidental"}).status_code == 200
        assert db.attempts()[0].cleared is True
        assert client.post(f"/api/attempts/{aid}/restore").status_code == 200
        assert db.attempts()[0].cleared is False
        r = client.post("/api/session/new", json={})
        assert r.status_code == 200 and r.json()["session_id"] == 2


def test_pb_on_missing_attempt_is_404(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        r = client.post("/api/pb", json={"attempt_id": 999, "timer_mode": "igt"})
        assert r.status_code == 404


def test_stats_registry_and_statmenu(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        r = client.get("/api/stats/registry")
        assert any(s["key"] == "success_rate" for s in r.json())
        menu = [{"key": "best"}, {"key": "avg_last_n", "params": {"n": 25}}]
        assert client.put("/api/statmenu", json={"selections": menu}).status_code == 200
        assert client.get("/api/session").json()["stat_menu"] == menu


def test_links_endpoint(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        r = client.get("/api/links/2/2")
        assert r.json()["ukikipedia"].endswith("Shoot_into_the_Wild_Blue")


def test_health_reports_db_and_session(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        body = client.get("/health").json()
        assert body["db"] == "ok" and body["session_id"] == 1


def test_api_absent_when_no_service(tmp_path):
    broadcaster = Broadcaster()
    poller = Poller(OfflineMemory(), [], broadcaster)
    app = create_app(poller, broadcaster)
    with TestClient(app) as client:
        assert client.get("/api/session").status_code == 404
        assert client.get("/health").json()["db"] == "absent"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_api.py -q`
Expected: FAIL — `create_app` rejects the `service` kwarg / `server.api` missing.

- [ ] **Step 3: Implement**

```python
# src/sm64_events/server/api.py
"""REST command/query surface for the tracker UI (spec §7)."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from sm64_events.links import star_links
from sm64_events.stats.registry import registry_meta
from sm64_events.tracking.views import build_session_view


class TargetBody(BaseModel):
    course_id: int
    star_id: int
    strat_tag: str | None = None


class ClearBody(BaseModel):
    reason: str | None = None


class PbBody(BaseModel):
    attempt_id: int
    timer_mode: str


class SessionBody(BaseModel):
    label: str | None = None


class StatMenuBody(BaseModel):
    selections: list[dict]


def create_api_router(service, db) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/session")
    def session(clock: str = "igt"):
        if clock not in ("igt", "rta"):
            raise HTTPException(422, "clock must be igt or rta")
        return build_session_view(db, service, clock=clock)

    @router.post("/session/new")
    async def session_new(body: SessionBody):
        sid = await service.new_session(label=body.label)
        return {"session_id": sid}

    @router.post("/target")
    async def target(body: TargetBody):
        await service.set_target(body.course_id, body.star_id, body.strat_tag)
        return {"ok": True}

    @router.post("/attempts/{attempt_id}/clear")
    async def clear(attempt_id: int, body: ClearBody):
        try:
            await service.clear_attempt(attempt_id, reason=body.reason)
        except ValueError as e:
            raise HTTPException(404, str(e))
        return {"ok": True}

    @router.post("/attempts/{attempt_id}/restore")
    async def restore(attempt_id: int):
        await service.restore_attempt(attempt_id)
        return {"ok": True}

    @router.post("/pb")
    async def save_pb(body: PbBody):
        try:
            return await service.save_pb(body.attempt_id, body.timer_mode)
        except ValueError as e:
            raise HTTPException(404, str(e))

    @router.get("/stats/registry")
    def stats_registry():
        return registry_meta()

    @router.put("/statmenu")
    def put_statmenu(body: StatMenuBody):
        db.set_state("stat_menu", body.selections)
        return {"ok": True}

    @router.get("/links/{course_id}/{star_id}")
    def links(course_id: int, star_id: int):
        return star_links(course_id, star_id)

    return router
```

In `src/sm64_events/server/app.py`:

1. Add imports:
```python
from fastapi.staticfiles import StaticFiles
from sm64_events.server.api import create_api_router
```
2. Change the signature to `def create_app(poller: Poller, broadcaster: Broadcaster, service=None, debug_hooks: bool = False) -> FastAPI:`
3. In `lifespan`, before creating the poller task: 
```python
        if service is not None:
            await service.start()
```
4. After `app = FastAPI(...)`:
```python
    app.mount("/ui", StaticFiles(directory=str(_UI_INDEX.parent)), name="ui")
    if service is not None:
        app.include_router(create_api_router(service, service.db))
```
5. Extend `/health`'s dict with:
```python
            "db": "absent" if service is None or service.db is None else "ok",
            "session_id": service.session_id if service is not None else None,
```

In `src/sm64_events/main.py`, replace `build()`:

```python
# src/sm64_events/main.py
"""Composition root: registry -> memory -> poller -> detectors -> tracking -> app."""
import logging
from pathlib import Path

from sm64_events.core.logging_setup import configure_logging
from sm64_events.detectors.anchors import AnchorDetector
from sm64_events.detectors.lifecycle import GameResetDetector
from sm64_events.detectors.star_grab import StarGrabDetector
from sm64_events.memory.pj64 import Pj64Memory
from sm64_events.server.app import create_app
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.poller import Poller
from sm64_events.storage.db import Database
from sm64_events.tracking.service import TrackerService

DB_PATH = Path("data") / "tracker.db"


def build():
    configure_logging()
    memory = Pj64Memory()
    broadcaster = Broadcaster()
    try:
        db = Database(DB_PATH)
    except Exception:
        logging.getLogger("sm64.tracker").exception(
            "database unavailable — running broadcast-only")
        db = None
    service = TrackerService(db, broadcaster)
    detectors = [GameResetDetector(), AnchorDetector(), StarGrabDetector()]
    poller = Poller(memory, detectors, service)  # service IS the event sink
    return create_app(poller, broadcaster, service=service)


app = build()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8064)
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass (existing test_app.py keeps passing — `service` defaults to None).

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/server/api.py src/sm64_events/server/app.py src/sm64_events/server/poller.py src/sm64_events/main.py tests/test_api.py tests/test_poller_isolation.py
git commit -m "feat: REST API and composition — tracker service wired in; detector exceptions isolated per tick"
```

---

### Task 10: UI foundation — vendor Preact, shell, store

No pytest here; verification is the dev server + browser (and the frontend-smoke-test skill).

**Files:**
- Create: `src/sm64_events/ui/vendor/{preact.module.js,hooks.module.js,htm.module.js}`, `src/sm64_events/ui/app.js`, `src/sm64_events/ui/api.js`, `src/sm64_events/ui/store.js`
- Modify: `src/sm64_events/ui/index.html`

- [ ] **Step 1: Vendor the libraries (PowerShell)**

```powershell
New-Item -ItemType Directory -Force src/sm64_events/ui/vendor, src/sm64_events/ui/components
Invoke-WebRequest https://unpkg.com/preact@10.24.3/dist/preact.module.js -OutFile src/sm64_events/ui/vendor/preact.module.js
Invoke-WebRequest https://unpkg.com/preact@10.24.3/hooks/dist/hooks.module.js -OutFile src/sm64_events/ui/vendor/hooks.module.js
Invoke-WebRequest https://unpkg.com/htm@3.1.1/dist/htm.module.js -OutFile src/sm64_events/ui/vendor/htm.module.js
```

Verify each file is non-empty JS (`Get-Content src/sm64_events/ui/vendor/preact.module.js -TotalCount 1`).

- [ ] **Step 2: Replace `src/sm64_events/ui/index.html`**

```html
<!doctype html>
<html><head><meta charset="utf-8"><title>SM64 Practice Tracker</title>
<script type="importmap">
{"imports": {
  "preact": "/ui/vendor/preact.module.js",
  "preact/hooks": "/ui/vendor/hooks.module.js",
  "htm": "/ui/vendor/htm.module.js"
}}
</script>
<style>
  body { font-family: Consolas, monospace; background: #14161a; color: #d8dee9;
         max-width: 980px; margin: 1.5rem auto; padding: 0 1rem; }
  h1 { font-size: 1.2rem; margin: 0; }
  a { color: #6fa8ff; }
  button { font: inherit; background: #232730; color: #d8dee9; border: 1px solid #3a4150;
           border-radius: 4px; padding: .15rem .55rem; cursor: pointer; }
  button:hover { background: #2c3140; }
  select, input { font: inherit; background: #1b1e24; color: #d8dee9;
                  border: 1px solid #3a4150; border-radius: 4px; padding: .15rem .3rem; }
  .bar { display: flex; flex-wrap: wrap; gap: .6rem; align-items: center;
         background: #1b1e24; border-radius: 6px; padding: .5rem .8rem; margin: .8rem 0; }
  .dot { padding: .1rem .5rem; border-radius: 4px; font-size: .85em; }
  .ok { background: #1d3a1d; color: #a3e0a3; } .bad { background: #3a1d1d; color: #e0a3a3; }
  .tabs { display: flex; gap: .3rem; margin: .8rem 0 0 0; }
  .tab { border: 1px solid #3a4150; border-bottom: none; border-radius: 6px 6px 0 0;
         padding: .25rem 1rem; cursor: pointer; opacity: .65; }
  .tab.on { background: #1b1e24; opacity: 1; font-weight: bold; }
  .pane { border: 1px solid #3a4150; border-radius: 0 6px 6px 6px; padding: .8rem; }
  .starsec { border: 1px solid #2c3140; border-radius: 8px; padding: .6rem .8rem; margin: 0 0 .8rem 0; }
  .shead { display: flex; flex-wrap: wrap; gap: .6rem; align-items: baseline; }
  .shead b { color: #ffd75f; }
  .meta { color: #6c7686; font-size: .85em; }
  .pbtag { color: #a3e0a3; margin-left: auto; }
  table { width: 100%; border-collapse: collapse; margin: .4rem 0; font-size: .92em; }
  td { padding: .15rem .45rem; border-bottom: 1px dotted #2c3140; }
  .good { color: #a3e0a3; } .badx { color: #e0a3a3; } .cleared { opacity: .45; text-decoration: line-through; }
  .chips { display: flex; flex-wrap: wrap; gap: .35rem; margin-top: .35rem; }
  .chip { border: 1px solid #3a4150; border-radius: 999px; padding: .05rem .6rem; font-size: .85em; }
  .delta-up { color: #e0a3a3; } .delta-down { color: #a3e0a3; }
  .popover { position: absolute; background: #1b1e24; border: 1px solid #3a4150;
             border-radius: 6px; padding: .7rem; z-index: 5; }
  li { margin: .25rem 0; list-style: none; } ul { padding: 0; }
  .star { color: #ffd75f; }
  .src { font-size: .75em; padding: 0 .3em; border-radius: 3px; margin-left: .3em; }
  .src-result { background: #1d3a1d; color: #a3e0a3; }
  .src-counter { background: #2d2d3a; color: #a3b0e0; }
  .src-reconstructed { background: #3a321d; color: #e0cba3; }
</style></head>
<body>
<div id="app"></div>
<script type="module" src="/ui/app.js"></script>
</body></html>
```

- [ ] **Step 3: Create `api.js`, `store.js`, `app.js`**

```javascript
// src/sm64_events/ui/api.js — thin fetch wrappers for /api/*
export async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}
export async function send(method, url, body) {
  const r = await fetch(url, {
    method, headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}
```

```javascript
// src/sm64_events/ui/store.js — session state + live WS subscription
import { useEffect, useState, useCallback } from "preact/hooks";
import { getJSON } from "./api.js";

const REFRESH_ON = new Set(["attempt_completed", "attempts_invalidated",
  "pb_saved", "session_started", "target_changed", "star_collected"]);

export function useTracker() {
  const [view, setView] = useState(null);
  const [clock, setClock] = useState(localStorage.getItem("clock") || "igt");
  const [feed, setFeed] = useState([]);
  const [connected, setConnected] = useState(false);

  const refresh = useCallback(async (c) => {
    try { setView(await getJSON(`/api/session?clock=${c || clock}`)); }
    catch (e) { console.error(e); }
  }, [clock]);

  useEffect(() => { refresh(); }, [clock]);

  useEffect(() => {
    let ws, closed = false;
    function connect() {
      ws = new WebSocket(`ws://${location.host}/ws/events`);
      ws.onopen = () => setConnected(true);
      ws.onclose = () => { setConnected(false);
        if (!closed) setTimeout(connect, 2000); };
      ws.onmessage = (e) => {
        const ev = JSON.parse(e.data);
        setFeed((f) => [ev, ...f].slice(0, 200));
        if (REFRESH_ON.has(ev.type)) refresh();
      };
    }
    connect();
    return () => { closed = true; ws && ws.close(); };
  }, [refresh]);

  const pickClock = (c) => { localStorage.setItem("clock", c); setClock(c); };
  return { view, clock, pickClock, feed, connected, refresh };
}
```

```javascript
// src/sm64_events/ui/app.js — root: header + tabs
import { h, render } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { useTracker } from "./store.js";
import { Header } from "./components/header.js";
import { Practice } from "./components/practice.js";
import { Feed } from "./components/feed.js";

const html = htm.bind(h);
const TABS = ["Practice", "Routes", "Live feed"];

function App() {
  const t = useTracker();
  const [tab, setTab] = useState("Practice");
  return html`
    <h1>SM64 Practice Tracker</h1>
    <${Header} t=${t} />
    <div class="tabs">
      ${TABS.map((name) => html`
        <div class="tab ${tab === name ? "on" : ""}"
             onclick=${() => name !== "Routes" && setTab(name)}
             title=${name === "Routes" ? "Phase 4" : ""}
             style=${name === "Routes" ? "opacity:.3;cursor:default" : ""}>${name}</div>`)}
    </div>
    <div class="pane">
      ${tab === "Practice" ? html`<${Practice} t=${t} />` : html`<${Feed} t=${t} />`}
    </div>`;
}

render(html`<${App} />`, document.getElementById("app"));
```

- [ ] **Step 4: Stub the three components so the shell loads** (real versions in Task 11)

```javascript
// src/sm64_events/ui/components/header.js  (stub — replaced in Task 11)
import { h } from "preact"; import htm from "htm";
const html = htm.bind(h);
export function Header({ t }) {
  return html`<div class="bar">
    <span class="dot ${t.connected ? "ok" : "bad"}">${t.connected ? "live" : "offline"}</span>
  </div>`;
}
```

```javascript
// src/sm64_events/ui/components/practice.js  (stub — replaced in Task 11)
import { h } from "preact"; import htm from "htm";
const html = htm.bind(h);
export function Practice({ t }) {
  return html`<p class="meta">${t.view ? `${t.view.stars.length} star(s) this session` : "loading…"}</p>`;
}
```

```javascript
// src/sm64_events/ui/components/feed.js  (stub — replaced in Task 11)
import { h } from "preact"; import htm from "htm";
const html = htm.bind(h);
export function Feed({ t }) {
  return html`<ul>${t.feed.map((ev) => html`<li>${ev.type} <span class="meta">#${ev.seq}</span></li>`)}</ul>`;
}
```

- [ ] **Step 5: Smoke test**

Run: `uv run uvicorn sm64_events.main:app --host 127.0.0.1 --port 8064` then open `http://127.0.0.1:8064/`.
Expected: header bar with live/offline dot, three tabs (Routes greyed), Practice pane shows "0 star(s) this session", zero console errors. With no PJ64 running this still works — the API serves from the (empty) database. Stop the server after checking.

- [ ] **Step 6: Commit**

```bash
git add src/sm64_events/ui
git commit -m "feat: UI foundation — vendored Preact+htm, importmap shell, session store with WS auto-refresh"
```

---

### Task 11: UI — Practice tab, stat menu, feed

**Files:**
- Replace stubs: `src/sm64_events/ui/components/{header.js,practice.js,feed.js}`
- Create: `src/sm64_events/ui/components/statmenu.js`

- [ ] **Step 1: Implement `header.js`**

```javascript
// src/sm64_events/ui/components/header.js
import { h } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { send } from "../api.js";

const html = htm.bind(h);

export function Header({ t }) {
  const v = t.view;
  const tgt = v && v.target;
  const [editing, setEditing] = useState(false);

  async function newSession() {
    await send("POST", "/api/session/new", {});
    t.refresh();
  }

  return html`<div class="bar">
    <span class="dot ${t.connected ? "ok" : "bad"}">${t.connected ? "live" : "offline"}</span>
    ${v && html`<span class="meta">session ${v.session.id}</span>`}
    <button onclick=${newSession}>New session</button>
    <span>Target:
      ${tgt && tgt.course_id !== null
        ? html` <b>${tgt.course_name} · ${tgt.star_name}</b>`
        : html` <span class="meta">none (grab a star or set one)</span>`}
      ${tgt && tgt.strat_tag ? html` <span class="meta">«${tgt.strat_tag}»</span>` : ""}
      <button onclick=${() => setEditing(!editing)}>▾</button>
    </span>
    <span style="margin-left:auto">Clock:
      <select value=${t.clock} onchange=${(e) => t.pickClock(e.target.value)}>
        <option value="igt">Usamune IGT</option>
        <option value="rta">anchor → grab</option>
      </select>
    </span>
    ${editing && v && html`<${TargetEditor} t=${t} close=${() => setEditing(false)} />`}
  </div>`;
}

function TargetEditor({ t, close }) {
  const v = t.view;
  const tgt = v.target;
  const [course, setCourse] = useState(tgt.course_id ?? 1);
  const [star, setStar] = useState(tgt.star_id ?? 0);
  const [strat, setStrat] = useState(tgt.strat_tag || "");
  const courses = v.catalog.courses;
  const stars = (courses.find((c) => c.id === Number(course)) || { stars: [] }).stars;

  async function apply() {
    await send("POST", "/api/target", {
      course_id: Number(course), star_id: Number(star),
      strat_tag: strat || null,
    });
    close(); t.refresh();
  }

  return html`<div class="popover">
    <div>
      <select value=${course} onchange=${(e) => { setCourse(e.target.value); setStar(0); }}>
        ${courses.map((c) => html`<option value=${c.id}>${c.name}</option>`)}
      </select>
      <select value=${star} onchange=${(e) => setStar(e.target.value)}>
        ${stars.map((name, i) => html`<option value=${i}>${name}</option>`)}
      </select>
    </div>
    <div style="margin-top:.4rem">
      <input placeholder="strat tag (optional)" value=${strat}
             oninput=${(e) => setStrat(e.target.value)} />
      <button onclick=${apply}>Set target</button>
    </div>
  </div>`;
}
```

- [ ] **Step 2: Implement `practice.js`**

```javascript
// src/sm64_events/ui/components/practice.js
import { h } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { send } from "../api.js";
import { StatMenu } from "./statmenu.js";

const html = htm.bind(h);

const OUTCOME_LABEL = { success: "✔", reset: "✘ reset",
  hard_reset: "✘ hard reset", abandoned: "– abandoned" };

function delta(frames) {
  if (frames === null || frames === undefined) return "";
  const cls = frames > 0 ? "delta-up" : "delta-down";
  const sign = frames > 0 ? "+" : "";
  return html` <span class=${cls}>${sign}${(frames / 30).toFixed(2)}s vs PB</span>`;
}

function AttemptRow({ a, t, idx }) {
  async function clear() {
    await send("POST", `/api/attempts/${a.id}/clear`, { reason: "accidental" });
    t.refresh();
  }
  async function restore() {
    await send("POST", `/api/attempts/${a.id}/restore`);
    t.refresh();
  }
  async function savePb() {
    await send("POST", "/api/pb", { attempt_id: a.id, timer_mode: t.clock });
    t.refresh();
  }
  const time = t.clock === "igt" ? a.igt : a.rta;
  return html`<tr class=${a.cleared ? "cleared" : ""}>
    <td class="meta">#${idx + 1}</td>
    <td class=${a.outcome === "success" ? "good" : "badx"}>
      ${OUTCOME_LABEL[a.outcome] || a.outcome}
      ${a.outcome === "success" && time ? html` <b>${time}</b>` : ""}
      ${a.outcome !== "success" && a.igt ? html` <span class="meta">${a.igt} in</span>` : ""}
    </td>
    <td>${a.outcome === "success" ? delta(a.pb_delta_frames) : ""}</td>
    <td class="meta">${a.strat_tag || ""}</td>
    <td style="text-align:right">
      ${a.outcome === "success" && !a.cleared
        ? html`<button onclick=${savePb}>Save as PB</button> ` : ""}
      ${a.cleared
        ? html`<button onclick=${restore}>undo</button>`
        : html`<button onclick=${clear} title="clear (mistake)">×</button>`}
    </td>
  </tr>`;
}

function StarSection({ sec, t }) {
  const pb = sec.pb[t.clock];
  return html`<div class="starsec">
    <div class="shead">
      <b>${sec.course_name} · ${sec.star_name}</b>
      <a href=${sec.links.ukikipedia} target="_blank">RTA Guide</a>
      ${sec.links.example && html`<a href=${sec.links.example} target="_blank">Example</a>`}
      <span class="pbtag">${pb ? `PB ${pb.display} (${t.clock})` : "no PB yet"}</span>
    </div>
    <table>${sec.attempts.map((a, i) => html`<${AttemptRow} a=${a} t=${t} idx=${i} />`)}</table>
    <div class="chips">
      ${sec.stats.map((s) => html`
        <span class="chip" title=${s.key}>${s.label} ${s.display ?? "–"}</span>`)}
    </div>
  </div>`;
}

export function Practice({ t }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const v = t.view;
  if (!v) return html`<p class="meta">loading…</p>`;
  return html`
    <div style="display:flex;justify-content:flex-end">
      <button onclick=${() => setMenuOpen(!menuOpen)}>⚙ stats</button>
    </div>
    ${menuOpen && html`<${StatMenu} t=${t} close=${() => setMenuOpen(false)} />`}
    ${v.stars.length === 0 && v.unassigned.length === 0
      ? html`<p class="meta">No attempts this session yet — grab a star.</p>` : ""}
    ${v.stars.map((sec) => html`<${StarSection} sec=${sec} t=${t} />`)}
    ${v.unassigned.length > 0 && html`<div class="starsec">
      <div class="shead"><b>No target</b>
        <span class="meta">failures before any star was grabbed or set</span></div>
      <table>${v.unassigned.map((a, i) => html`<${AttemptRow} a=${a} t=${t} idx=${i} />`)}</table>
    </div>`}`;
}
```

- [ ] **Step 3: Implement `statmenu.js`**

```javascript
// src/sm64_events/ui/components/statmenu.js
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";

const html = htm.bind(h);
const keyOf = (s) => `${s.key}:${JSON.stringify(s.params || {})}`;

export function StatMenu({ t, close }) {
  const [registry, setRegistry] = useState([]);
  const [selected, setSelected] = useState(t.view.stat_menu);
  useEffect(() => { getJSON("/api/stats/registry").then(setRegistry); }, []);

  function toggle(entry) {
    const k = keyOf(entry);
    setSelected((sel) => sel.some((s) => keyOf(s) === k)
      ? sel.filter((s) => keyOf(s) !== k) : [...sel, entry]);
  }

  async function apply() {
    await send("PUT", "/api/statmenu", { selections: selected });
    close(); t.refresh();
  }

  // offer avg_last_n at a few useful Ns plus every parameterless stat
  const offers = registry.flatMap((d) => d.key === "avg_last_n"
    ? [10, 25, 50, 100].map((n) => ({ key: d.key, params: { n }, label: `Avg last ${n}` }))
    : [{ key: d.key, params: d.params, label: d.label }]);

  return html`<div class="popover" style="right:1rem">
    ${offers.map((o) => html`<label style="display:block">
      <input type="checkbox"
             checked=${selected.some((s) => keyOf(s) === keyOf(o))}
             onchange=${() => toggle({ key: o.key, params: o.params })} />
      ${o.label}</label>`)}
    <div style="margin-top:.5rem"><button onclick=${apply}>Apply</button>
      <button onclick=${close}>Cancel</button></div>
  </div>`;
}
```

- [ ] **Step 4: Implement `feed.js`** (port of the old viewer's rendering)

```javascript
// src/sm64_events/ui/components/feed.js
import { h } from "preact";
import htm from "htm";

const html = htm.bind(h);

export function Feed({ t }) {
  return html`<ul>
    ${t.feed.map((ev) => {
      if (ev.type === "star_collected") {
        const p = ev.payload;
        return html`<li>
          <span class="star">⭐ ${p.course_name} — ${p.star_name}</span>
          ${" "}<b>${p.igt}</b>
          <span class="src src-${p.igt_source}">${p.igt_source}</span>
          <span class="meta"> ${p.igt_frames}f · course ${p.course_id} star ${p.star_id}${p.already_collected ? " (already collected)" : ""} · frame ${ev.frame} · #${ev.seq}</span>
        </li>`;
      }
      return html`<li>${ev.type}
        <span class="meta"> ${JSON.stringify(ev.payload)} · frame ${ev.frame} · #${ev.seq}</span></li>`;
    })}
  </ul>`;
}
```

- [ ] **Step 5: Smoke test with seeded data**

Run the server, then seed via the API (PowerShell):

```powershell
# no emulator needed: exercise the UI through the REST surface
Invoke-RestMethod -Method POST -Uri http://127.0.0.1:8064/api/target -ContentType application/json -Body '{"course_id":2,"star_id":2,"strat_tag":"practice"}'
```

Open `http://127.0.0.1:8064/` and verify: target shows in header; clock toggle re-fetches; stat menu opens, applies, persists across refresh; Live feed tab shows `target_set`. Run the **frontend-smoke-test skill** (Chrome DevTools MCP) — zero console errors required. For full visual verification with real attempt rows, do Task 12's live gate.

- [ ] **Step 6: Run the full suite, then commit**

Run: `uv run pytest -q` — all green.

```bash
git add src/sm64_events/ui
git commit -m "feat: practice tab — star sections with clear/undo, Save-as-PB, stat chips, stat menu, live feed port"
```

---

### Task 12: Docs + live verification gate

**Files:**
- Modify: `README.md` (event schema: new types; API section; game_reset semantics change), `CLAUDE.md` (module map additions), `docs/architecture.md` (data-flow diagram gains tracking/storage; roadmap prunes delivered items)

- [ ] **Step 1: Update README**

Add the new event types to the schema section (`practice_reset`, `state_loaded`, `attempt_completed`, `target_set`, `target_changed`, `attempt_cleared`, `attempt_restored`, `pb_saved`, `session_started`, `attempts_invalidated`) with one-line payload descriptions; document that `game_reset` now means boot-range resets only (savestate loads are `state_loaded`); add an "HTTP API" section listing the `/api/*` endpoints with one-line descriptions; note `data/tracker.db` and that deleting it resets all history.

- [ ] **Step 2: Update CLAUDE.md module map**

Add rows: tracking/attempt logic → `tracking/projection.py`; event pipeline/commands → `tracking/service.py`; session view → `tracking/views.py`; database → `storage/db.py`; stats → `stats/registry.py`; star links → `links.py`; REST API → `server/api.py`; UI components → `ui/components/`. Update the parallel-work-zones line: `storage/`, `stats/`, `tracking/` are one zone (they share the Attempt contract); add `tracking/projection.py` to the shared-contracts list.

- [ ] **Step 3: Update docs/architecture.md**

Extend the data-flow diagram with the tracking/storage hop; record under a new "Attempt tracking" heading: attempt id = journal id of first event; two-pass projection for retroactive attribution; why broadcast precedes journaling (liveness never gated on db). Remove "Stats consumer" from the roadmap (delivered); leave deaths/doors/routes with pointers to the spec.

- [ ] **Step 4: Full suite + live gate with the human**

Run: `uv run pytest -q` — green.
Then with PJ64 + Usamune running: `uv run python tools/verify_addresses.py` and a live session checking, in order: (1) Usamune level reset → `practice_reset` in the feed; (2) section-state load → `state_loaded` (**VERIFY the global_timer-backward assumption** flagged in anchors.py — if it doesn't fire, characterize with `tools/watch_timer.py` and adjust); (3) grab a star → attempt row appears with both clocks; (4) reset-spam → reset failures chain; (5) clear an attempt → re-attribution visible; (6) Save as PB → delta on next grab; (7) console reset → `game_reset` and `hard_reset` outcome.

- [ ] **Step 5: Commit**

```bash
git add README.md CLAUDE.md docs/architecture.md
git commit -m "docs: phase 1 tracker — event schema additions, module map, attempt-tracking domain notes"
```

---

## Phase 2–4 outlines (separate plans when their turn comes)

**Phase 2 — New detectors (features #2, full #11).** Snapshot fields `particle_flags` (Mario+0x08 = `0x8033B178`), `health` (+0xAE), `num_lives` (+0xAD), `curr_level`, `mario_pos` (+0x3C) — all VERIFY-gated. `RolloutDetector` (dive→rollout edge; N observed `ACT_DIVE_SLIDE` frames = `frames_late`, 0 = dustless — decomp facts in spec §3) emitting `rollout {dustless, frames_late, level}`; `DeathDetector` (action-set edge, cause payload; death/outcome wiring: death closes attempts as `outcome=death`); `LevelChangeDetector` → `abandoned` outcome activates. Projection extends: `rollout` events while OPEN accumulate `rollouts_total/rollouts_dustless` onto the attempt (new columns, migration v2); `dustless_rate` StatDef. Per-attempt rollout rate in the times table.

**Phase 3 — Triggers + menu (feature #5, menu failure).** `TRIGGERS` registry rows `{name, action_id, level?, position_box?}` with star/key-door actions (`0x1331`/`0x132E`); `TriggerDetector` + `trigger` events; menu-open address hunt with the human (`hunt_value.py`/`watch_timer.py` playbook), then `MenuDetector` + `menu` outcome with configurable buffer.

**Phase 4 — Routes (feature #10).** `routes` table (name + segments JSON), CRUD endpoints, route board endpoint computing per-segment Laplace-smoothed last-50 success rate and cumulative survival; Routes tab UI (segments, p, ∏p). Estimator pulled from the stat registry.
