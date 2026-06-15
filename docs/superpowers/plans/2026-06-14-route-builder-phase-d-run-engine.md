# Route Builder — Phase D (Run Engine, backend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The server-side full-game **run** engine: an app-maintained forgiving-RTA timer over a route, with per-step splits, abort/restart, PB/gold, and a REST + WS surface — all pure and pytest-testable. The run-view UI is Phase D-UI (separate plan).

**Architecture:** Mirror `SegmentEngine`. A pure `RunTracker` (`tracking/runs.py`) is embedded in the `Projector` and fed each event plus the attempts the projector just closed; it maintains the active run and emits finished/aborted `RunRecord`s. The journal is the source of truth: a journaled `run_started` event arms run mode, so runs **re-derive on replay** (the `runs` table is a cache like `attempts`). Times come from event **wall-clock** timestamps (the run clock is RTA, not game frames — user decision; `start_offset` models the SM64 emulator reset-timing convention). The live ticking clock is the UI's job (Phase D-UI) off the authoritative `started_utc` + offset.

**Tech Stack:** Python 3.12 (uv), SQLite, FastAPI, pytest.

**Source spec:** `docs/superpowers/specs/2026-06-14-route-builder-design.md` (§4.2 runs table, §4.3 run settings, §5.2 RunTracker, §5.4/§5.5 service+API, §8 edge cases). **Depends on Phases A–C** (merged): routes table/CRUD, `tracking/routes.py`, route view.

**Scope note:** Plan **4 of 5** (backend half of "run mode"). The **run-view UI** (splits panel, Focus mode, click-to-hide) is **Phase D-UI**; run history + progression graph is **Phase E**. Build NO UI here.

**Locked lifecycle (from brainstorming):**
- `start_run(route_id)` journals `run_started {route_id, route_name, route_steps (snapshot), mode:"forgiving", start_offset_ms}` — this **arms** run mode. The clock starts at 0 on the **next `game_reset` (F1)**. Every later `game_reset` **aborts** the in-progress run (saved) and **restarts** a fresh one. The final step completing **finishes** the run (saved). `end_run()` (journaled `run_ended`) disarms; an active run is saved aborted.
- **Forgiving:** the clock never stops for a step-reset. A step's `elapsed_ms` rolls up all retries (it's wall-clock from run start to that step's completion). Resets/deaths on the current step bump its `fails`; the run continues.
- **Step completion** = a closed **success** attempt matching the current step's candidate. A group step needs **K distinct** candidates (no duplicates), any order.
- **Run identity** = the journal id of the `game_reset` that started the run (stable across rebuilds).
- **Times** stored offset-free: `elapsed_ms` (cumulative from start) per split, `total_ms` = final elapsed; `start_offset_ms` stored per run; **displayed time = elapsed + offset** (applied in the view). PB = min finished `total_ms` for a route; **gold** = best per-step duration (matched by step signature); **sum-of-best** = Σ gold. `is_pb` is frozen at finish (for the Phase E graph).
- **Pause-subtraction is deferred** (v1 = pure RTA from start). Flagged for a follow-up.

**Convention:** commits end with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. Stage explicit paths (`git add -A` is hook-blocked). Verify the branch before each commit. Execute in an isolated worktree off current master.

**Shared-contract caution:** Task 6 edits `tracking/projection.py` (a "never edit in two branches" file). The desktop-gui-packaging worktree does NOT touch it, and the other session's in-flight perf work touches `procmem.py`/`poller.py`/`app.py` — NOT `projection.py`. Before editing, re-confirm with `git -C <main> diff --name-only | grep projection` is empty.

---

## File Structure

- **Create** `src/sm64_events/tracking/runs.py` — `RunRecord` + pure `RunTracker` + pure PB/gold helpers. No db, no I/O.
- **Modify** `src/sm64_events/tracking/projection.py` — embed `RunTracker` in `Projector`; feed it `(ev, closed)`; expose `finished_runs()` + `active_run_view()` + `run_notices`; `replay()` returns runs.
- **Modify** `src/sm64_events/storage/db.py` — migration **v8** (`runs` table) + `insert_run`/`upsert_run`/`runs`/`replace_runs`; run settings via `ui_state`.
- **Modify** `src/sm64_events/tracking/service.py` — `start_run`/`end_run`/`run_settings`/`update_run_settings`; persist runs in `start`/`_reproject`/`_track`; broadcast `run_*`.
- **Modify** `src/sm64_events/tracking/views.py` — `build_run_view` (active run + splits + PB/gold) and `build_run_history`.
- **Modify** `src/sm64_events/server/api.py` — `/api/run/start`, `/api/run/end`, `/api/run`, `/api/run/history`, `GET|PUT /api/run/settings`.
- **Test** `tests/test_runs.py` (new) · `tests/test_storage.py` · `tests/test_projection.py` · `tests/test_tracker_service.py` · `tests/test_views.py` · `tests/test_api.py`.
- **Modify** `CLAUDE.md` — module-map rows. **README/docs/api.md** — run API + WS events.

---

## Task 1: `runs` table (migration v8) + CRUD + run settings

**Files:** Modify `src/sm64_events/storage/db.py`; Test `tests/test_storage.py`.

- [ ] **Step 1: Version ripple** — every `PRAGMA user_version` assertion in `tests/test_storage.py` currently expects **7** (after Phase A's v7). Bump each to **8** (and the failed-migration test's `7`→`8` and its final `8`→`9`). Add `"runs"` to the table-name subset assertion.

- [ ] **Step 2: Write the failing tests** — add to `tests/test_storage.py`:

```python
# -- migration v8: runs (full-game run history) ------------------------------

def test_migration_v8_creates_runs_table(tmp_path):
    db = make_db(tmp_path)
    names = {r["name"] for r in db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "runs" in names


def _run_row(**o):
    d = dict(id=500, route_id=1, route_name="R", route_steps=[{"need": 1,
             "candidates": [{"type": "star", "course": 2, "star": 0}]}],
             mode="forgiving", status="finished", reached_step=1,
             total_ms=120000, start_offset_ms=1360,
             started_utc="2026-06-14T00:00:00Z", ended_utc="2026-06-14T00:02:00Z",
             is_pb=1, splits=[{"step_index": 0, "elapsed_ms": 120000}])
    d.update(o); return d


def test_run_insert_and_read(tmp_path):
    db = make_db(tmp_path)
    db.insert_run(_run_row())
    [r] = db.runs()
    assert r["id"] == 500 and r["status"] == "finished" and r["total_ms"] == 120000
    assert r["route_steps"][0]["need"] == 1            # JSON round-trips
    assert r["splits"][0]["elapsed_ms"] == 120000
    assert r["is_pb"] is True


def test_runs_filter_by_route_and_finished(tmp_path):
    db = make_db(tmp_path)
    db.insert_run(_run_row(id=1, route_id=1, status="finished"))
    db.insert_run(_run_row(id=2, route_id=1, status="aborted", is_pb=0))
    db.insert_run(_run_row(id=3, route_id=2, status="finished"))
    assert {r["id"] for r in db.runs(route_id=1)} == {1, 2}
    assert {r["id"] for r in db.runs(route_id=1, finished_only=True)} == {1}


def test_replace_runs_rebuilds_cache(tmp_path):
    db = make_db(tmp_path)
    db.insert_run(_run_row(id=9))
    db.replace_runs([])
    assert db.runs() == []


def test_run_settings_default_and_set(tmp_path):
    db = make_db(tmp_path)
    assert db.get_state("run_settings", {"start_offset_ms": 1360}) == {"start_offset_ms": 1360}
    db.set_state("run_settings", {"start_offset_ms": 2000})
    assert db.get_state("run_settings", {})["start_offset_ms"] == 2000
```

- [ ] **Step 3: Run → fail.** `uv run pytest tests/test_storage.py -q` → no `runs` table / no `insert_run`.

- [ ] **Step 4: Migration v8** — append to `MIGRATIONS` in `db.py` (after v7):

```python
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
```

- [ ] **Step 5: CRUD** — in `db.py`, after the routes section, add:

```python
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
```

- [ ] **Step 6: Run → pass.** `uv run pytest tests/test_storage.py -q`.

- [ ] **Step 7: Commit.**

```bash
git add src/sm64_events/storage/db.py tests/test_storage.py
git commit -m "feat(storage): runs table (migration v8) + CRUD + run settings"
```

---

## Task 2: `RunTracker` — arm + start + finish (single-step runs)

**Files:** Create `src/sm64_events/tracking/runs.py`; Test `tests/test_runs.py` (new).

The tests use a tiny fake event + the real `Attempt`. Build the tracker incrementally (Tasks 2→5).

- [ ] **Step 1: Write the failing tests** — create `tests/test_runs.py`:

```python
import pytest

from sm64_events.tracking.projection import Attempt
from sm64_events.tracking.runs import RunTracker, pb_run, gold_splits


class Ev:
    """Minimal event stand-in (RunTracker reads .type/.id/.wall_time_utc/.payload)."""
    def __init__(self, type, id=0, wall="2026-06-14T00:00:00Z", payload=None):
        self.type = type; self.id = id; self.wall_time_utc = wall
        self.payload = payload or {}


def att(outcome="success", course=None, star=None, segment_id=None):
    return Attempt(id=1, session_id=1, course_id=course, star_id=star,
                   strat_tag=None, anchor_type="none", anchor_frame=None,
                   outcome=outcome, outcome_detail=None, igt_frames=None,
                   rta_frames=None, started_utc="t", ended_utc="t",
                   cleared=False, cleared_reason=None, segment_id=segment_id)


STAR = {"type": "star", "course": 2, "star": 0}
SEG = {"type": "segment", "segment_id": 5}


def started(steps, offset=1360, rid=1):
    return Ev("run_started", payload={"route_id": rid, "route_name": "R",
              "route_steps": steps, "mode": "forgiving", "start_offset_ms": offset})


def test_arm_then_game_reset_starts_run():
    rt = RunTracker()
    steps = [{"need": 1, "candidates": [STAR]}]
    assert rt.feed(started(steps), []) == []          # arming produces nothing
    assert rt.active_run_view() is None               # not started until F1
    rt.feed(Ev("game_reset", id=100, wall="2026-06-14T00:00:01Z"), [])
    v = rt.active_run_view()
    assert v is not None and v["id"] == 100 and v["current_step"] == 0


def test_completing_only_step_finishes_run():
    rt = RunTracker()
    steps = [{"need": 1, "candidates": [STAR]}]
    rt.feed(started(steps), [])
    rt.feed(Ev("game_reset", id=100, wall="2026-06-14T00:00:00Z"), [])
    done = rt.feed(Ev("star_collected", id=101, wall="2026-06-14T00:02:00Z"),
                   [att(course=2, star=0)])
    assert len(done) == 1
    r = done[0]
    assert r.status == "finished" and r.reached_step == 1
    assert r.total_ms == 120000 and r.start_offset_ms == 1360
    assert r.splits[0]["elapsed_ms"] == 120000
    assert r.is_pb is True                             # first finished run
    assert rt.active_run_view() is None                # run over


def test_segment_step_completes_on_segment_success():
    rt = RunTracker()
    rt.feed(started([{"need": 1, "candidates": [SEG]}]), [])
    rt.feed(Ev("game_reset", id=100), [])
    done = rt.feed(Ev("attempt_completed", id=101, wall="2026-06-14T00:00:30Z"),
                   [att(segment_id=5)])
    assert done and done[0].status == "finished"


def test_completion_before_start_is_ignored():
    rt = RunTracker()
    rt.feed(started([{"need": 1, "candidates": [STAR]}]), [])
    # armed but no game_reset yet -> a grab does nothing
    assert rt.feed(Ev("star_collected", id=99), [att(course=2, star=0)]) == []
    assert rt.active_run_view() is None
```

- [ ] **Step 2: Run → fail** (`ModuleNotFoundError: ...runs`). `uv run pytest tests/test_runs.py -q`.

- [ ] **Step 3: Create `tracking/runs.py` with the core** :

```python
"""Full-game run timer — forgiving RTA over a route (spec 2026-06-14, Phase D).

A RUN is one continuous attempt at a whole route. run_started (journaled by
start_run) ARMS run mode with the route snapshot + start_offset; the clock then
starts at 0 on the NEXT game_reset (F1). Each later game_reset ABORTS the
in-progress run (saved) and restarts a fresh one; the final step FINISHES it.

Forgiving: the wall clock never stops for a step-reset — a step's elapsed time
rolls up all its retries. Step completion = a closed SUCCESS attempt matching
the current step's candidate; a group needs K DISTINCT candidates (no dups),
any order. Times come from event wall_time (the run clock is wall-clock RTA,
NOT game frames — user decision; start_offset models the SM64 emulator
reset-timing convention). Stored offset-free; display adds the offset.

Pure over the journal: re-derives every run on replay (the runs table is a
cache like attempts). Run id = the game_reset journal id that started it.
Pause-aware subtraction is deferred (v1 = pure RTA from start)."""
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class RunRecord:
    id: int                  # journal id of the starting game_reset
    route_id: int | None
    route_name: str
    route_steps: list
    mode: str
    status: str              # "finished" | "aborted"
    reached_step: int
    total_ms: int | None
    start_offset_ms: int
    started_utc: str
    ended_utc: str
    is_pb: bool
    splits: list             # [{step_index, completed_item, elapsed_ms, attempts, fails}]

    def as_row(self) -> dict:
        """Dict shaped for db.insert_run / db.runs round-trips."""
        return {"id": self.id, "route_id": self.route_id,
                "route_name": self.route_name, "route_steps": self.route_steps,
                "mode": self.mode, "status": self.status,
                "reached_step": self.reached_step, "total_ms": self.total_ms,
                "start_offset_ms": self.start_offset_ms,
                "started_utc": self.started_utc, "ended_utc": self.ended_utc,
                "is_pb": self.is_pb, "splits": self.splits}


def _ms(a_utc: str, b_utc: str) -> int:
    a = datetime.fromisoformat(a_utc.replace("Z", "+00:00"))
    b = datetime.fromisoformat(b_utc.replace("Z", "+00:00"))
    return int((b - a).total_seconds() * 1000)


def _cand_matches(cand: dict, a) -> bool:
    if cand["type"] == "segment":
        return a.segment_id == cand["segment_id"]
    return (a.segment_id is None and a.course_id == cand["course"]
            and a.star_id == cand["star"])


def _cand_key(cand: dict):
    return ("seg", cand["segment_id"]) if cand["type"] == "segment" \
        else ("star", cand["course"], cand["star"])


class RunTracker:
    """One active run + accumulated finished/aborted runs. Pure over the feed;
    the projector embeds it (mirrors SegmentEngine)."""

    def __init__(self):
        self._armed = None       # {route_id, route_name, route_steps, mode, offset}
        self._active = None      # active run state, or None
        self._finished: list[RunRecord] = []   # all produced (for is_pb)
        self.run_notices: list[dict] = []       # live broadcast queue

    # -- queries -------------------------------------------------------------
    def active_run_view(self) -> dict | None:
        if self._active is None:
            return None
        act, steps = self._active, self._armed["route_steps"]
        return {"id": act["id"], "route_id": self._armed["route_id"],
                "route_name": self._armed["route_name"], "mode": self._armed["mode"],
                "started_utc": act["started_utc"],
                "start_offset_ms": self._armed["offset"],
                "current_step": act["current"],
                "steps": [{"index": i, "need": steps[i]["need"],
                           "done": list(p["done"]), "attempts": p["attempts"],
                           "fails": p["fails"], "elapsed_ms": p["elapsed_ms"]}
                          for i, p in enumerate(act["steps"])]}

    def finished_runs(self) -> list[RunRecord]:
        return list(self._finished)

    # -- feed ----------------------------------------------------------------
    def feed(self, ev, closed) -> list[RunRecord]:
        produced = []
        if ev.type == "run_started":
            if self._active is not None:
                produced.append(self._finalize("aborted", ev.wall_time_utc))
            p = ev.payload
            self._armed = {"route_id": p.get("route_id"),
                           "route_name": p.get("route_name", ""),
                           "route_steps": p.get("route_steps", []),
                           "mode": p.get("mode", "forgiving"),
                           "offset": int(p.get("start_offset_ms", 0))}
            self._active = None
        elif ev.type == "run_ended":
            if self._active is not None:
                produced.append(self._finalize("aborted", ev.wall_time_utc))
            self._armed = None
            self._active = None
        elif ev.type == "game_reset":
            if self._armed is not None:
                if self._active is not None:
                    produced.append(self._finalize("aborted", ev.wall_time_utc))
                self._begin(ev)
        if self._active is not None and closed:
            for a in closed:
                fin = self._apply(a, ev)
                if fin is not None:
                    produced.append(fin)
                    break
        for r in produced:
            self._finished.append(r)
        self._set_notices(produced)
        return produced

    # -- internals -----------------------------------------------------------
    def _begin(self, ev) -> None:
        self._active = {
            "id": ev.id, "started_utc": ev.wall_time_utc, "current": 0,
            "steps": [{"done": [], "attempts": 0, "fails": 0,
                       "elapsed_ms": None, "completed_item": None}
                      for _ in self._armed["route_steps"]]}

    def _apply(self, a, ev):
        act, steps = self._active, self._armed["route_steps"]
        i = act["current"]
        if i >= len(steps):
            return None
        step, prog = steps[i], act["steps"][i]
        matched = next((c for c in step["candidates"] if _cand_matches(c, a)), None)
        if matched is None:
            return None
        if a.outcome != "success":
            prog["attempts"] += 1
            prog["fails"] += 1
            return None
        key = _cand_key(matched)
        if key in prog["done"]:
            return None                       # no duplicate credit
        prog["done"].append(key)
        prog["attempts"] += 1
        if len(prog["done"]) >= step["need"]:
            prog["elapsed_ms"] = _ms(act["started_utc"], ev.wall_time_utc)
            prog["completed_item"] = matched
            act["current"] += 1
            if act["current"] >= len(steps):
                return self._finalize("finished", ev.wall_time_utc)
        return None

    def _finalize(self, status: str, ended_utc: str) -> RunRecord:
        act, steps = self._active, self._armed["route_steps"]
        splits = [{"step_index": i, "completed_item": p["completed_item"],
                   "elapsed_ms": p["elapsed_ms"], "attempts": p["attempts"],
                   "fails": p["fails"]}
                  for i, p in enumerate(act["steps"]) if p["elapsed_ms"] is not None]
        total = _ms(act["started_utc"], ended_utc)
        is_pb = False
        if status == "finished":
            prior = [r.total_ms for r in self._finished
                     if r.status == "finished" and r.route_id == self._armed["route_id"]
                     and r.total_ms is not None]
            is_pb = not prior or total < min(prior)
        rec = RunRecord(
            id=act["id"], route_id=self._armed["route_id"],
            route_name=self._armed["route_name"],
            route_steps=self._armed["route_steps"], mode=self._armed["mode"],
            status=status, reached_step=act["current"], total_ms=total,
            start_offset_ms=self._armed["offset"], started_utc=act["started_utc"],
            ended_utc=ended_utc, is_pb=is_pb, splits=splits)
        self._active = None
        return rec

    def _set_notices(self, produced) -> None:
        notices = []
        for r in produced:
            notices.append({"event": "run_finished" if r.status == "finished"
                            else "run_aborted", "run_id": r.id,
                            "status": r.status})
        if self._active is not None:
            notices.append({"event": "run_progress", "run_id": self._active["id"],
                            "current_step": self._active["current"]})
        self.run_notices = notices


def pb_run(runs: list) -> dict | None:
    """Finished run with the smallest total_ms (the PB), or None."""
    fin = [r for r in runs if r["status"] == "finished" and r["total_ms"] is not None]
    return min(fin, key=lambda r: r["total_ms"]) if fin else None


def _step_durations(run: dict) -> dict:
    """step_index -> this run's duration for that step (delta of cumulative
    elapsed_ms). Only steps that completed are present."""
    out, prev = {}, 0
    for s in run["splits"]:
        if s["elapsed_ms"] is None:
            continue
        out[s["step_index"]] = s["elapsed_ms"] - prev
        prev = s["elapsed_ms"]
    return out


def gold_splits(runs: list, route_steps: list) -> dict:
    """step_index -> best (min) duration across finished runs whose step
    SIGNATURE matches the current route at that index (so reordering is safe).
    Returns {"durations": {i: ms}, "sum_of_best": ms|None}."""
    def sig(steps, i):
        if i >= len(steps):
            return None
        s = steps[i]
        return (s.get("need"), tuple(sorted(map(_cand_key, s["candidates"]))))
    want = [sig(route_steps, i) for i in range(len(route_steps))]
    best: dict = {}
    for r in runs:
        if r["status"] != "finished":
            continue
        durs = _step_durations(r)
        for i, d in durs.items():
            if i < len(want) and sig(r["route_steps"], i) == want[i]:
                if i not in best or d < best[i]:
                    best[i] = d
    sob = sum(best.values()) if len(best) == len(route_steps) and route_steps else None
    return {"durations": best, "sum_of_best": sob}
```

- [ ] **Step 4: Run → pass.** `uv run pytest tests/test_runs.py -q` (the four Task-2 tests).

- [ ] **Step 5: Commit.**

```bash
git add src/sm64_events/tracking/runs.py tests/test_runs.py
git commit -m "feat(runs): RunTracker arm/start/finish + run record"
```

---

## Task 3: RunTracker — groups, forgiving retries, abort/restart, end_run

**Files:** Modify `tests/test_runs.py` (the implementation from Task 2 already covers these — this task PROVES it with tests; add code only if a test fails).

- [ ] **Step 1: Write the tests** — add to `tests/test_runs.py`:

```python
def test_group_needs_k_distinct_no_duplicates():
    rt = RunTracker()
    A = {"type": "star", "course": 2, "star": 0}
    B = {"type": "star", "course": 2, "star": 1}
    C = {"type": "star", "course": 2, "star": 2}
    rt.feed(started([{"need": 2, "candidates": [A, B, C]}]), [])
    rt.feed(Ev("game_reset", id=100), [])
    # grab A, then A again (dup — no credit), then B -> 2 distinct -> finish
    assert rt.feed(Ev("star_collected", id=101), [att(course=2, star=0)]) == []
    assert rt.feed(Ev("star_collected", id=102), [att(course=2, star=0)]) == []  # dup
    done = rt.feed(Ev("star_collected", id=103, wall="2026-06-14T00:00:40Z"),
                   [att(course=2, star=1)])
    assert done and done[0].status == "finished" and done[0].reached_step == 1


def test_reset_within_step_counts_fail_run_continues():
    rt = RunTracker()
    rt.feed(started([{"need": 1, "candidates": [STAR]}]), [])
    rt.feed(Ev("game_reset", id=100), [])
    # a reset attempt on the current star: fail, run keeps going (no finalize)
    assert rt.feed(Ev("practice_reset", id=101), [att(outcome="reset", course=2, star=0)]) == []
    assert rt.active_run_view() is not None
    v = rt.active_run_view()
    assert v["steps"][0]["fails"] == 1
    # then a success finishes it
    done = rt.feed(Ev("star_collected", id=102, wall="2026-06-14T00:01:00Z"),
                   [att(course=2, star=0)])
    assert done and done[0].splits[0]["fails"] == 1


def test_game_reset_aborts_in_progress_and_restarts():
    rt = RunTracker()
    rt.feed(started([{"need": 1, "candidates": [STAR]},
                     {"need": 1, "candidates": [SEG]}]), [])
    rt.feed(Ev("game_reset", id=100, wall="2026-06-14T00:00:00Z"), [])
    rt.feed(Ev("star_collected", id=101, wall="2026-06-14T00:00:30Z"),
            [att(course=2, star=0)])           # step 1 done, on step 2 now
    aborted = rt.feed(Ev("game_reset", id=200, wall="2026-06-14T00:01:00Z"), [])
    assert len(aborted) == 1
    assert aborted[0].status == "aborted" and aborted[0].reached_step == 1
    assert aborted[0].id == 100                # the first run's id
    # a fresh run is now active, id=200, back at step 0
    v = rt.active_run_view()
    assert v["id"] == 200 and v["current_step"] == 0


def test_end_run_aborts_active_and_disarms():
    rt = RunTracker()
    rt.feed(started([{"need": 1, "candidates": [STAR]}]), [])
    rt.feed(Ev("game_reset", id=100), [])
    out = rt.feed(Ev("run_ended", id=300, wall="2026-06-14T00:00:10Z"), [])
    assert out and out[0].status == "aborted"
    assert rt.active_run_view() is None
    # disarmed: a later game_reset does NOT start a run
    rt.feed(Ev("game_reset", id=400), [])
    assert rt.active_run_view() is None
```

- [ ] **Step 2: Run → these should PASS** with the Task-2 implementation. `uv run pytest tests/test_runs.py -q`. If any fail, fix `runs.py` minimally to satisfy the test (the behavior is specified above), then re-run.

- [ ] **Step 3: Commit.**

```bash
git add tests/test_runs.py src/sm64_events/tracking/runs.py
git commit -m "test(runs): groups, forgiving retries, abort/restart, end_run"
```

---

## Task 4: RunTracker — PB / gold / sum-of-best

**Files:** Modify `tests/test_runs.py` (helpers `pb_run`/`gold_splits` from Task 2; prove + fix).

- [ ] **Step 1: Write the tests:**

```python
def test_pb_run_picks_min_finished_total():
    runs = [{"status": "finished", "total_ms": 130000},
            {"status": "aborted", "total_ms": 50000},
            {"status": "finished", "total_ms": 121000}]
    assert pb_run(runs)["total_ms"] == 121000
    assert pb_run([{"status": "aborted", "total_ms": 1}]) is None


def test_gold_splits_best_per_step_and_sum_of_best():
    steps = [{"need": 1, "candidates": [STAR]}, {"need": 1, "candidates": [SEG]}]
    # run 1: step0 dur 60s, step1 dur 70s (cumulative 60s, 130s)
    r1 = {"status": "finished", "route_steps": steps,
          "splits": [{"step_index": 0, "elapsed_ms": 60000},
                     {"step_index": 1, "elapsed_ms": 130000}]}
    # run 2: step0 dur 55s (gold), step1 dur 80s (cumulative 55s, 135s)
    r2 = {"status": "finished", "route_steps": steps,
          "splits": [{"step_index": 0, "elapsed_ms": 55000},
                     {"step_index": 1, "elapsed_ms": 135000}]}
    g = gold_splits([r1, r2], steps)
    assert g["durations"][0] == 55000 and g["durations"][1] == 70000
    assert g["sum_of_best"] == 125000


def test_is_pb_frozen_only_when_finished_beats_prior():
    rt = RunTracker()
    steps = [{"need": 1, "candidates": [STAR]}]
    def run(reset_id, grab_id, wall_end):
        rt.feed(started(steps), [])
        rt.feed(Ev("game_reset", id=reset_id, wall="2026-06-14T00:00:00Z"), [])
        return rt.feed(Ev("star_collected", id=grab_id, wall=wall_end),
                       [att(course=2, star=0)])[0]
    first = run(100, 101, "2026-06-14T00:02:00Z")    # 120s
    second = run(200, 201, "2026-06-14T00:01:30Z")   # 90s -> PB
    third = run(300, 301, "2026-06-14T00:02:30Z")    # 150s -> not PB
    assert first.is_pb is True and second.is_pb is True and third.is_pb is False
```

- [ ] **Step 2: Run → pass** (fix `runs.py` if needed). `uv run pytest tests/test_runs.py -q`.

- [ ] **Step 3: Commit.**

```bash
git add tests/test_runs.py src/sm64_events/tracking/runs.py
git commit -m "test(runs): PB run, gold splits, sum-of-best, frozen is_pb"
```

---

## Task 5: Embed RunTracker in the Projector

**Files:** Modify `src/sm64_events/tracking/projection.py` (**shared contract** — see caution above); Test `tests/test_projection.py`.

- [ ] **Step 1: Write the failing test** — add to `tests/test_projection.py` (use the file's existing event/replay helpers; if it has none, construct `EventRow`s like `tests/test_storage.py` does via `db.append_event` + `db.events()`, or a minimal `Ev`-style shim matching what `replay` consumes):

```python
def test_replay_derives_finished_run(tmp_path):
    from sm64_events.tracking.projection import replay
    from sm64_events.storage.db import Database
    db = Database(tmp_path / "t.db")
    sid = db.insert_session("2026-06-14T00:00:00Z")
    from sm64_events.core.events import Event
    from datetime import datetime, timezone
    T = datetime(2026, 6, 14, tzinfo=timezone.utc)
    steps = [{"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]
    db.append_event(sid, 1, Event(type="run_started", frame=0, timestamp_utc=T,
        payload={"route_id": 1, "route_name": "R", "route_steps": steps,
                 "mode": "forgiving", "start_offset_ms": 1360}))
    db.append_event(sid, 2, Event(type="game_reset", frame=0, timestamp_utc=T,
        payload={}))
    db.append_event(sid, 3, Event(type="star_collected", frame=0, timestamp_utc=T,
        payload={"course_id": 2, "star_id": 0, "igt_frames": 100}))
    attempts, proj = replay(db.events())
    runs = proj.finished_runs()
    assert len(runs) == 1 and runs[0].status == "finished"
    assert proj.active_run_view() is None
```

- [ ] **Step 2: Run → fail** (`'Projector' object has no attribute 'finished_runs'`).

- [ ] **Step 3: Wire it in `projection.py`:**
  1. Import at top (next to the segments import):
     ```python
     from sm64_events.tracking.runs import RunTracker
     ```
  2. In `Projector.__init__`, after `self._segments = SegmentEngine(...)`, add:
     ```python
     self._runs = RunTracker()
     self.run_notices: list[dict] = []   # live-broadcast queue, drained by service
     ```
  3. In `Projector.feed`, AFTER the segment loop appends segment attempts to `closed` and BEFORE the `BOUNDARY_EVENT_TYPES` accumulator reset, add:
     ```python
     # Run engine sees the same event + the attempts just closed (star AND
     # segment successes/failures); it owns the run lifecycle independently.
     self._runs.feed(ev, closed)
     self.run_notices = self._runs.run_notices
     ```
  4. Add delegate methods on `Projector` (next to `armed_segment_ids`):
     ```python
     def finished_runs(self):
         return self._runs.finished_runs()

     def active_run_view(self):
         return self._runs.active_run_view()
     ```

- [ ] **Step 4: Run → pass.** `uv run pytest tests/test_projection.py tests/test_runs.py -q`. Then the FULL suite to prove the shared-contract edit broke nothing: `uv run pytest -q`.

- [ ] **Step 5: Commit.**

```bash
git add src/sm64_events/tracking/projection.py tests/test_projection.py
git commit -m "feat(projection): embed RunTracker (runs derive on replay)"
```

---

## Task 6: Service — run lifecycle commands + persistence + broadcast

**Files:** Modify `src/sm64_events/tracking/service.py`; Test `tests/test_tracker_service.py`.

- [ ] **Step 1: Write the failing tests:**

```python
# -- runs (Phase D) -----------------------------------------------------------

def _route_with(db, svc):
    lblj = seed_id(db, "LBLJ")
    return asyncio.run(svc.create_route({"name": "Run R", "steps": [
        {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]}))


def test_start_run_journals_and_arms(tmp_path):
    db, svc, sent = make_rec(tmp_path)
    rid = _route_with(db, svc)
    asyncio.run(svc.start_run(rid))
    ev = [e for e in db.events() if e.type == "run_started"][-1]
    assert ev.payload["route_id"] == rid and ev.payload["route_name"] == "Run R"
    assert ev.payload["route_steps"][0]["need"] == 1
    assert ev.payload["start_offset_ms"] == 1360       # default
    assert any(e.type == "run_started" for e in sent)  # broadcast too


def test_full_run_persists_finished_row(tmp_path):
    db, svc = make(tmp_path)
    rid = _route_with(db, svc)
    asyncio.run(svc.start_run(rid))
    asyncio.run(svc.publish(ev("game_reset", 0)))
    asyncio.run(svc.publish(star(900, course=2, star_id=0)))
    [run] = db.runs()
    assert run["status"] == "finished" and run["route_id"] == rid
    assert run["is_pb"] is True


def test_start_run_unknown_route_raises(tmp_path):
    db, svc = make(tmp_path)
    with pytest.raises(LookupError):
        asyncio.run(svc.start_run(99999))


def test_run_settings_get_and_update(tmp_path):
    db, svc = make(tmp_path)
    assert svc.run_settings()["start_offset_ms"] == 1360
    asyncio.run(svc.update_run_settings({"start_offset_ms": 2000}))
    assert svc.run_settings()["start_offset_ms"] == 2000
    with pytest.raises(ValueError):
        asyncio.run(svc.update_run_settings({"start_offset_ms": -5}))


def test_runs_rebuild_on_restart(tmp_path):
    db, svc = make(tmp_path)
    rid = _route_with(db, svc)
    asyncio.run(svc.start_run(rid))
    asyncio.run(svc.publish(ev("game_reset", 0)))
    asyncio.run(svc.publish(star(900, course=2, star_id=0)))
    db2 = Database(tmp_path / "t.db")
    svc2 = TrackerService(db2, Broadcaster())
    asyncio.run(svc2.start())                  # replay re-derives + replace_runs
    assert len(db2.runs()) == 1 and db2.runs()[0]["status"] == "finished"
```

> The existing `star(frame, course=2, star_id=2, igt=343)` helper in this file defaults to `(2,2)`; pass `course=2, star_id=0` so it matches the route's step.

- [ ] **Step 2: Run → fail** (`no attribute 'start_run'`).

- [ ] **Step 3: Implement.** In `service.py`:
  1. Imports (next to the routes import):
     ```python
     RUN_OFFSET_MIN, RUN_OFFSET_MAX = 0, 600000   # 0..10 min, ms
     ```
  2. Persist runs in the pipeline. In `start()`, after `attempts, self._projector = replay(...)` and `self.db.replace_attempts(attempts)`, add:
     ```python
     self.db.replace_runs([r.as_row() for r in self._projector.finished_runs()])
     ```
     In `_reproject()`, after `db.replace_attempts(attempts)`, add the same `db.replace_runs([...])` using the new `projector`.
     In `_track()`, AFTER the segment-notice drain loop and BEFORE the `for attempt in closed:` loop, add a run drain (broadcast-only notices) + persistence of any finished/aborted runs produced this event:
     ```python
     for r in proj.finished_runs()[len(self._persisted_runs):]:
         self.db.upsert_run(r.as_row())
         self._persisted_runs.append(r.id)
         await self.publish(self._run_completed_event(r, event))
         if self._projector is not proj:
             return
     for n in list(proj.run_notices):
         await self.broadcaster.publish(Event(type=n["event"], frame=event.frame,
             timestamp_utc=event.timestamp_utc, payload=n))
         if self._projector is not proj:
             return
     ```
     In `__init__`, add `self._persisted_runs: list[int] = []`; and in `start()`/`_reproject()` reset it to the ids already persisted: `self._persisted_runs = [r.id for r in self._projector.finished_runs()]` (after the replace_runs).
  3. The lifecycle commands + settings:
     ```python
     async def start_run(self, route_id: int) -> None:
         db = self._require_db()
         route = next((r for r in db.routes() if r["id"] == route_id), None)
         if route is None:
             raise LookupError(f"route {route_id} not found")
         offset = self.run_settings()["start_offset_ms"]
         await self.publish(Event(type="run_started", frame=0, timestamp_utc=_now(),
             payload={"route_id": route_id, "route_name": route["name"],
                      "route_steps": route["steps"], "mode": "forgiving",
                      "start_offset_ms": offset}))

     async def end_run(self) -> None:
         self._require_db()
         await self.publish(Event(type="run_ended", frame=0,
             timestamp_utc=_now(), payload={}))

     def run_settings(self) -> dict:
         db = self._require_db()
         return db.get_state("run_settings", {"start_offset_ms": 1360})

     async def update_run_settings(self, patch: dict) -> dict:
         db = self._require_db()
         cur = self.run_settings()
         off = patch.get("start_offset_ms", cur["start_offset_ms"])
         if not isinstance(off, int) or off < RUN_OFFSET_MIN or off > RUN_OFFSET_MAX:
             raise ValueError("start_offset_ms must be 0..600000 ms")
         merged = {**cur, "start_offset_ms": off}
         db.set_state("run_settings", merged)
         return merged

     def active_run(self) -> dict | None:
         return self._projector.active_run_view()
     ```
  4. The run-completed derived event:
     ```python
     def _run_completed_event(self, r, close_event) -> Event:
         return Event(type=r.status == "finished" and "run_finished" or "run_aborted",
             frame=close_event.frame, timestamp_utc=close_event.timestamp_utc,
             payload={"run_id": r.id, "route_id": r.route_id, "status": r.status,
                      "reached_step": r.reached_step, "total_ms": r.total_ms,
                      "is_pb": r.is_pb})
     ```
     > `run_started`/`run_ended` are JOURNALED (they drive replay). `run_finished`/`run_aborted`/`run_progress` are broadcast-only notices (derived; the projector ignores them on replay since they aren't in `_dispatch`). To be safe, confirm `run_finished`/`run_aborted` are NOT journaled — emit them via `self.broadcaster.publish`, not `self.publish`. **Correction to step 3.2:** use `await self.broadcaster.publish(self._run_completed_event(r, event))` (broadcast-only), NOT `self.publish`.

- [ ] **Step 4: Run → pass.** `uv run pytest tests/test_tracker_service.py -q`, then `uv run pytest -q`.

- [ ] **Step 5: Commit.**

```bash
git add src/sm64_events/tracking/service.py tests/test_tracker_service.py
git commit -m "feat(service): run lifecycle (start/end), persistence, settings, broadcast"
```

---

## Task 7: View payloads — active run + history

**Files:** Modify `src/sm64_events/tracking/views.py`; Test `tests/test_views.py`.

- [ ] **Step 1: Write the failing tests:**

```python
def test_build_run_view_active_with_pb_and_gold(tmp_path):
    from sm64_events.tracking.views import build_run_view
    db, svc = make(tmp_path)
    lblj = next(d["id"] for d in db.segment_defs() if d["name"] == "LBLJ")
    rid = asyncio.run(svc.create_route({"name": "RV", "steps": [
        {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]},
        {"need": 1, "candidates": [{"type": "segment", "segment_id": lblj}]}]}))
    asyncio.run(svc.start_run(rid))
    asyncio.run(svc.publish(ev("game_reset", 0)))
    view = build_run_view(db, svc)
    assert view["active"] is not None
    assert view["active"]["current_step"] == 0
    assert view["active"]["start_offset_ms"] == 1360
    # step display names resolved for the live view
    assert view["active"]["steps"][0]["display"] == "Chip off Whomp's Block"
    assert "pb" in view and "gold" in view       # comparison present (None/empty ok)


def test_build_run_view_idle_when_no_run(tmp_path):
    from sm64_events.tracking.views import build_run_view
    db, svc = make(tmp_path)
    assert build_run_view(db, svc)["active"] is None


def test_build_run_history_filters_finished(tmp_path):
    from sm64_events.tracking.views import build_run_history
    db, svc = make(tmp_path)
    rid = asyncio.run(svc.create_route({"name": "H", "steps": [
        {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]}))
    asyncio.run(svc.start_run(rid))
    asyncio.run(svc.publish(ev("game_reset", 0)))
    asyncio.run(svc.publish(star(900, course=2, star_id=0)))
    hist = build_run_history(db, route_id=rid)
    assert len(hist["runs"]) == 1
    assert hist["runs"][0]["display_total"] is not None   # total + offset, formatted
    assert hist["pb"] is not None
```

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement** in `views.py` (reuse `format_igt`? it formats FRAMES; run times are ms — add a small ms formatter). Append:

```python
def _fmt_ms(ms):
    if ms is None:
        return None
    s, ms = divmod(int(ms), 1000)
    m, s = divmod(s, 60)
    return f"{m}:{s:02d}.{ms:03d}"


def _resolve_cands(cands, seg_names):
    out = []
    for c in cands:
        if c["type"] == "segment":
            out.append({"kind": "segment", "segment_id": c["segment_id"],
                        "display": seg_names.get(c["segment_id"],
                                                 f"segment {c['segment_id']} (deleted)")})
        else:
            out.append({"kind": "star", "course": c["course"], "star": c["star"],
                        "display": star_name(c["course"], c["star"])})
    return out


def build_run_view(db, service) -> dict:
    """Live run state for the run panel: the active run (resolved step names +
    elapsed) plus the route's PB total and gold/sum-of-best for comparison."""
    from sm64_events.tracking.runs import pb_run, gold_splits
    act = service.active_run()
    seg_names = {d["id"]: d["name"] for d in db.segment_defs()}
    offset = service.run_settings()["start_offset_ms"]
    out = {"active": None, "pb": None, "gold": None,
           "start_offset_ms": offset}
    if act is not None:
        steps_def = next((r["steps"] for r in db.routes()
                          if r["id"] == act["route_id"]), [])
        steps = []
        for i, s in enumerate(act["steps"]):
            cands = _resolve_cands(steps_def[i]["candidates"], seg_names) \
                if i < len(steps_def) else []
            steps.append({**s, "candidates": cands,
                          "display": cands[0]["display"] if cands else "?",
                          "elapsed_display": _fmt_ms(
                              None if s["elapsed_ms"] is None
                              else s["elapsed_ms"] + offset)})
        out["active"] = {**act, "steps": steps}
    if act is not None and act["route_id"] is not None:
        runs = db.runs(route_id=act["route_id"])
        pb = pb_run(runs)
        steps_def = next((r["steps"] for r in db.routes()
                          if r["id"] == act["route_id"]), [])
        gold = gold_splits(runs, steps_def)
        out["pb"] = {"total_ms": pb["total_ms"],
                     "display": _fmt_ms(pb["total_ms"] + offset)} if pb else None
        out["gold"] = {"sum_of_best": gold["sum_of_best"],
                       "display": _fmt_ms(None if gold["sum_of_best"] is None
                                          else gold["sum_of_best"] + offset)}
    return out


def build_run_history(db, route_id: int | None = None) -> dict:
    """Saved runs (optionally one route) + the PB. display_total folds in the
    per-run offset; finished runs flagged is_pb power the progression graph."""
    from sm64_events.tracking.runs import pb_run
    runs = db.runs(route_id=route_id)
    out_runs = [{**r, "display_total": _fmt_ms(None if r["total_ms"] is None
                                               else r["total_ms"] + r["start_offset_ms"])}
                for r in runs]
    pb = pb_run(runs)
    return {"runs": out_runs,
            "pb": {"total_ms": pb["total_ms"]} if pb else None}
```

- [ ] **Step 4: Run → pass.** `uv run pytest tests/test_views.py -q`.

- [ ] **Step 5: Commit.**

```bash
git add src/sm64_events/tracking/views.py tests/test_views.py
git commit -m "feat(views): run view (active + PB/gold) and run history payloads"
```

---

## Task 8: REST endpoints

**Files:** Modify `src/sm64_events/server/api.py`; Test `tests/test_api.py`.

- [ ] **Step 1: Write the failing tests:**

```python
def test_run_lifecycle_endpoints(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        lblj = _lblj(db)
        rid = client.post("/api/routes", json={"name": "R", "steps": [
            {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]}).json()["id"]
        assert client.post("/api/run/start", json={"route_id": rid}).status_code == 200
        assert client.get("/api/run").json()["active"] is None      # armed, not started
        assert client.post("/api/run/end").status_code == 200


def test_run_start_unknown_route_404(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        assert client.post("/api/run/start", json={"route_id": 9999}).status_code == 404


def test_run_settings_endpoints(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        assert client.get("/api/run/settings").json()["start_offset_ms"] == 1360
        assert client.put("/api/run/settings", json={"start_offset_ms": 2000}).status_code == 200
        assert client.get("/api/run/settings").json()["start_offset_ms"] == 2000
        assert client.put("/api/run/settings", json={"start_offset_ms": -1}).status_code == 409


def test_run_history_endpoint(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        assert client.get("/api/run/history").status_code == 200
        assert "runs" in client.get("/api/run/history").json()
```

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement.** In `api.py`, extend the views import:
  ```python
  from sm64_events.tracking.views import (build_route_view, build_run_history,
                                          build_run_view, build_session_view)
  ```
  Add bodies:
  ```python
  class RunStartBody(BaseModel):
      route_id: int

  class RunSettingsBody(BaseModel):
      start_offset_ms: int
  ```
  Add endpoints (after the routes endpoints; literal paths before any param):
  ```python
      @router.post("/run/start")
      async def run_start(body: RunStartBody):
          try:
              await service.start_run(body.route_id)
          except (LookupError, ValueError, RuntimeError) as e:
              raise _http(e)
          return {"ok": True}

      @router.post("/run/end")
      async def run_end():
          try:
              await service.end_run()
          except (LookupError, ValueError, RuntimeError) as e:
              raise _http(e)
          return {"ok": True}

      @router.get("/run")
      def run_state():
          if service.db is None:
              raise HTTPException(503, "database unavailable")
          return build_run_view(service.db, service)

      @router.get("/run/history")
      def run_history(route_id: int | None = None):
          if service.db is None:
              raise HTTPException(503, "database unavailable")
          return build_run_history(service.db, route_id=route_id)

      @router.get("/run/settings")
      def run_settings_get():
          if service.db is None:
              raise HTTPException(503, "database unavailable")
          return service.run_settings()

      @router.put("/run/settings")
      async def run_settings_put(body: RunSettingsBody):
          try:
              return await service.update_run_settings(body.model_dump())
          except (LookupError, ValueError, RuntimeError) as e:
              raise _http(e)
  ```

- [ ] **Step 4: Run → pass + full suite.** `uv run pytest tests/test_api.py -q` then `uv run pytest -q` (merge gate).

- [ ] **Step 5: Commit.**

```bash
git add src/sm64_events/server/api.py tests/test_api.py
git commit -m "feat(api): run lifecycle + state + history + settings endpoints"
```

---

## Task 9: Docs

**Files:** `CLAUDE.md`, `README.md` (or `docs/api.md` if the desktop-gui move has landed).

- [ ] **Step 1: CLAUDE.md rows:**

```
| Run engine (forgiving-RTA full-game timer) | `tracking/runs.py` — pure `RunTracker`: arm on `run_started`, start clock on next `game_reset` (+`start_offset`), forgiving splits (wall-clock per step), K-of-N no-dup completion, abort/restart on `game_reset`, finish on last step; `pb_run`/`gold_splits` helpers. Run id = starting game_reset journal id; times stored offset-free |
| Run projection wiring | `tracking/projection.py` — `Projector` embeds `RunTracker`, feeds it `(ev, closed)`; `finished_runs()`/`active_run_view()`/`run_notices`. Runs re-derive on replay (cache like attempts) |
| Run storage | `storage/db.py` — `runs` table (migration v8) + insert/upsert/replace/`runs(route_id?,finished_only?)`; run settings in `ui_state` (`start_offset_ms`, default 1360) |
| Run lifecycle + view + API | `tracking/service.py` (`start_run`/`end_run`/`run_settings`; persists runs; broadcasts `run_started`/`run_finished`/`run_aborted`/`run_progress`) · `tracking/views.py` (`build_run_view`/`build_run_history`) · `server/api.py` (`/api/run/*`) |
```

- [ ] **Step 2: README/docs** — document the run WS events (`run_started` journaled; `run_finished`/`run_aborted`/`run_progress` broadcast-only) and `/api/run/*` endpoints in the API surface (in `docs/api.md` if it exists, else README).

- [ ] **Step 3: Commit.**

```bash
git add CLAUDE.md README.md
git commit -m "docs: run engine module map + API surface (Phase D)"
```

---

## Self-Review (completed during planning)

**Spec coverage (§5.2 RunTracker + §4.2/§4.3 + §5.4/§5.5):**
- runs table + CRUD + settings (v8) → Task 1. Version ripple handled.
- RunTracker arm/start/finish, groups, forgiving retries, abort/restart, end_run, PB/gold, frozen is_pb → Tasks 2–4.
- Replay-derived runs (cache) → Task 5 + Task 6 (`replace_runs`/`upsert_run`).
- Lifecycle commands + offset default + broadcast taxonomy (journaled `run_started`/`run_ended`; broadcast-only `run_finished`/`run_aborted`/`run_progress`) → Task 6.
- Active-run view (resolved names + offset) + history + PB/gold → Task 7.
- REST surface + error taxonomy → Task 8.
- Live gate (NOT automatable): confirm **F1 in PJ64 fires `game_reset`** with the human — the run clock's start/restart depends on it. Documented; no new memory addresses.
- Deferred (flagged): pause-subtraction (v1 pure RTA); the **run-view UI** (Phase D-UI); run history list + progression graph (Phase E).

**Type/name consistency:** `RunRecord.as_row()` keys match `db._RUN_COLS` and `db.runs()` output; `_cand_matches`/`_cand_key` consistent across tracker + gold; `active_run_view` shape consumed unchanged by `build_run_view`; `start_offset_ms` default 1360 in db, service, and tests.

**Placeholder scan:** none. **Risk:** Task 6's `_track` ordering (persist+broadcast runs vs the attempt loop) and the journaled-vs-broadcast distinction for run events are the highest-risk spots — the note in Task 6.3 corrects the broadcast call; the code reviewer should verify `run_finished`/`run_aborted` never reach `db.events()` (mirror `test_segment_armed ... not in journaled`).

---

## Subsequent phases (separate plan files)

- **Phase D-UI** — the run view: splits panel (live ticking clock off `started_utc`+offset, ± vs PB, gold highlight), Start/End run controls, **Focus mode** (neutral palette, no ± deltas/gold), **click-to-hide** any timer. Consumes `GET /api/run` + the `run_*` WS events.
- **Phase E** — run history list (finished/aborted filter) + progression graph (reusing `progress.js`), from `GET /api/run/history`.
