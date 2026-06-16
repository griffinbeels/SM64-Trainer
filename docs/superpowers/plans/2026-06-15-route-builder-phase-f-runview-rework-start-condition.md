# Route Builder — Phase F (Run-View Rework + Configurable Start Condition)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Address the run-view audit feedback (7 items): make the Run view always-on and correct, and let each route configure WHEN its run clock starts (reusing the trigger system).

**Source:** live audit 2026-06-15. Items:
1. Route **preview** when a route is selected.
2. **No "Start run" button** — selecting a route arms it; F1 (or the start condition) begins it.
3. **Graph fix** — x = oldest→newest; y = run time, **0 at bottom, worst at top, slower = higher** (NOT inverted, NOT min-based).
4. **Click a run → expand its splits** (every step's time/attempts).
5. **Timer always visible** — idle shows `0:00 + offset`; running ticks; finished **freezes** until the next run (never disappears).
6. **Starts immediately on the start condition, beginning at +offset** (already backend behavior; #5 surfaces it).
7. **Per-route configurable run-start condition** — a `start_condition` trigger (default `reset_game`); add a `reset_game` trigger type; `RunTracker` starts on that condition.

**Architecture:** Backend — a route gains `start_condition` (a trigger clause); `RunTracker` is fed the `MatchContext` and starts the clock when the condition matches (a `game_reset` that isn't the condition aborts). Frontend — the Run tab always renders a clock + step list (active → live, finished → frozen, idle → preview), drops the Start button (select = arm), adds click-to-expand split history, and the corrected graph.

**Depends on:** Phases A–E (run engine + run view) merged. **This branch extends the open `route-builder-phase-e` worktree** (its history+graph commits are improved here — the graph fix supersedes them).

**Start-condition semantics (LOCKED):**
- `start_condition` matches → if a run is active, **abort it (saved)**; **begin** a new run. (start = start-or-restart.)
- `game_reset` that is NOT the start condition AND a run is active → **abort** (no begin). Player re-triggers the start condition to begin again.
- `practice_reset`/`state_loaded`/`death` during the current step → **step fail** (forgiving; clock keeps running) — unchanged.
- Default `start_condition = {"type":"reset_game"}` ⇒ identical to today's F1 start/restart. Existing routes get this default via migration v9.

**Convention:** commits end with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. Stage explicit paths. Verify branch before commits. `node --check` edited JS before committing. Smoke on `SM64_PORT=8066`.

**Shared contracts touched:** `tracking/projection.py` (pass `ctx` to `RunTracker.feed`) — desktop-gui/perf branches don't touch it; re-confirm clean before editing.

---

## Backend

### Task 1: `reset_game` trigger

**Files:** `src/sm64_events/tracking/segments.py`; `tests/test_segments.py`.

- [ ] **Test** — add to `tests/test_segments.py`:
```python
def test_reset_game_trigger_matches_game_reset():
    from sm64_events.tracking.segments import TRIGGERS, MatchContext
    t = TRIGGERS["reset_game"]
    class E:  # minimal event
        type = "game_reset"; payload = {}
    assert t.match({"type": "reset_game"}, E(), MatchContext(level=6, prev_level=6, num_stars=0))
    class E2:
        type = "level_changed"; payload = {"from": 1, "to": 2}
    assert not t.match({"type": "reset_game"}, E2(), MatchContext(level=2, prev_level=1, num_stars=0))


def test_vocab_includes_reset_game():
    from sm64_events.tracking.segments import vocab
    assert any(t["key"] == "reset_game" for t in vocab()["triggers"])
```

- [ ] **Implement** — add to the `TRIGGERS` list in `segments.py` (after `attempt_anchor`):
```python
    TriggerType("reset_game", "The game resets (F1 / console reset)",
                {}, "",
                lambda p, ev, ctx: ev.type == "game_reset"),
```

- [ ] Run `uv run pytest tests/test_segments.py -q` → pass. Commit `feat(segments): reset_game trigger type`.

### Task 2: `routes.start_condition` (migration v9 + CRUD)

**Files:** `src/sm64_events/storage/db.py`; `tests/test_storage.py`.

- [ ] **Test** — version ripple: bump `test_storage.py` `user_version` assertions 8→9 (+ the failed-migration test 8→9 / 9→10). Add:
```python
def test_migration_v9_adds_start_condition_default_reset(tmp_path):
    db = make_db(tmp_path)
    rid = db.insert_route("R", [], "t")
    [row] = db.routes()
    assert row["start_condition"] == {"type": "reset_game"}   # default


def test_route_insert_with_explicit_start_condition(tmp_path):
    db = make_db(tmp_path)
    rid = db.insert_route("R", [], "t", start_condition={"type": "level_enter", "to": 9})
    row = next(r for r in db.routes() if r["id"] == rid)
    assert row["start_condition"] == {"type": "level_enter", "to": 9}
    db.update_route(rid, start_condition={"type": "reset_game"}, updated_utc="t2")
    assert db.routes()[0]["start_condition"] == {"type": "reset_game"}
```

- [ ] **Implement** — migration v9 (after v8):
```python
    # v9 — per-route run-start condition (spec 2026-06-15). The run clock starts
    # when this trigger fires; existing routes default to the game reset (F1).
    """
    ALTER TABLE routes ADD COLUMN start_condition TEXT NOT NULL
      DEFAULT '{"type":"reset_game"}';
    """,
```
In `db.routes()` add `"start_condition": json.loads(r["start_condition"])` to the row dict. In `insert_route`, add a `start_condition: dict | None = None` param (default `{"type":"reset_game"}` when None) and include the column. In `update_route`'s `cols`, add `"start_condition": json.dumps`.

- [ ] Run `uv run pytest tests/test_storage.py -q` → pass. Commit `feat(storage): routes.start_condition (migration v9)`.

### Task 3: `routes.py` — validate + export/import carry start_condition

**Files:** `src/sm64_events/tracking/routes.py`; `tests/test_routes.py`.

- [ ] **Test** — add:
```python
def test_validate_route_accepts_valid_start_condition():
    validate_route({"name": "R", "start_condition": {"type": "reset_game"},
        "steps": [{"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]})


def test_validate_route_rejects_bad_start_condition():
    with pytest.raises(ValueError):
        validate_route({"name": "R", "start_condition": {"type": "nope"},
            "steps": [{"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]})


def test_export_import_roundtrips_start_condition():
    segs = {}
    out = export_route("R", [{"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}],
                       segs, start_condition={"type": "level_enter", "to": 9})
    assert out["start_condition"] == {"type": "level_enter", "to": 9}
    res = resolve_import(out, [])
    assert res["start_condition"] == {"type": "level_enter", "to": 9}
```

- [ ] **Implement** in `routes.py`:
  - Import the trigger validators: `from sm64_events.tracking.segments import TRIGGERS, _check_clause`.
  - In `validate_route`, after step validation:
    ```python
    sc = d.get("start_condition")
    if sc is not None:
        _check_clause(sc, TRIGGERS, "start_condition")
    ```
  - `export_route(name, steps, segment_defs, start_condition=None)` — add `"start_condition": start_condition or {"type": "reset_game"}` to the returned dict.
  - `resolve_import` — read `payload.get("start_condition", {"type": "reset_game"})` and include it in the returned dict (key `start_condition`).

- [ ] Run `uv run pytest tests/test_routes.py -q` → pass. Commit `feat(routes): validate + carry start_condition`.

### Task 4: service — route CRUD + start_run carry start_condition

**Files:** `src/sm64_events/tracking/service.py`; `tests/test_tracker_service.py`.

- [ ] **Test** — add:
```python
def test_create_route_default_start_condition(tmp_path):
    db, svc = make(tmp_path)
    rid = asyncio.run(svc.create_route({"name": "R", "steps": [
        {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]}))
    assert next(r for r in db.routes() if r["id"] == rid)["start_condition"] == {"type": "reset_game"}


def test_start_run_includes_start_condition(tmp_path):
    db, svc, sent = make_rec(tmp_path)
    rid = asyncio.run(svc.create_route({"name": "R",
        "start_condition": {"type": "level_enter", "to": 9}, "steps": [
        {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]}))
    asyncio.run(svc.start_run(rid))
    ev = [e for e in db.events() if e.type == "run_started"][-1]
    assert ev.payload["start_condition"] == {"type": "level_enter", "to": 9}
```

- [ ] **Implement** in `service.py`:
  - `create_route`: pass `start_condition` to `db.insert_route(..., start_condition=d.get("start_condition"))`.
  - `update_route`: include `start_condition` in the patched cols (`{k: d[k] for k in ("name","steps","start_condition") if k in d}`).
  - `export_route` (service): pass `route.get("start_condition")` to `route_logic.export_route(...)`.
  - `import_route`: pass `resolved["start_condition"]` to `db.insert_route(...)`.
  - `start_run`: read `route.get("start_condition", {"type": "reset_game"})` and add `"start_condition"` to the `run_started` payload.

- [ ] Run `uv run pytest tests/test_tracker_service.py -q` → pass. Commit `feat(service): carry route start_condition into run_started`.

### Task 5: projection — pass MatchContext to RunTracker

**Files:** `src/sm64_events/tracking/projection.py`; `tests/test_projection.py`. **Shared contract — re-confirm clean.**

- [ ] **Implement** — in `Projector.feed`, change the run feed call to pass the `ctx` already built for the segment engine:
  ```python
  self._runs.feed(ev, closed, ctx)
  ```
  (The `MatchContext` is the same `ctx` passed to `self._segments.feed`. If it's a local var, reuse it; otherwise build it once before both calls.)

- [ ] **Test** — extend `test_replay_derives_finished_run` (Phase D) so the route's default start_condition (reset_game) still produces a finished run via game_reset + grab (it does — reset_game matches game_reset). Add a second test with a `level_enter` start condition:
```python
def test_run_starts_on_configured_level_enter(tmp_path):
    from sm64_events.tracking.projection import replay
    from sm64_events.storage.db import Database
    from sm64_events.core.events import Event
    from datetime import datetime, timezone
    db = Database(tmp_path / "t.db"); sid = db.insert_session("t")
    T = datetime(2026, 6, 15, tzinfo=timezone.utc)
    steps = [{"need": 1, "candidates": [{"type": "star", "course": 9, "star": 0}]}]
    db.append_event(sid, 1, Event(type="run_started", frame=0, timestamp_utc=T, payload={
        "route_id": 1, "route_name": "R", "route_steps": steps, "mode": "forgiving",
        "start_offset_ms": 0, "start_condition": {"type": "level_enter", "to": 9}}))
    # a game_reset must NOT start this run (start condition is level_enter)
    db.append_event(sid, 2, Event(type="game_reset", frame=0, timestamp_utc=T, payload={}))
    attempts, proj = replay(db.events())
    assert proj.active_run_view() is None
    # entering level 9 starts it
    db.append_event(sid, 3, Event(type="level_changed", frame=0, timestamp_utc=T,
        payload={"from": 1, "to": 9}))
    db.append_event(sid, 4, Event(type="star_collected", frame=0, timestamp_utc=T,
        payload={"course_id": 9, "star_id": 0, "igt_frames": 1}))
    attempts, proj = replay(db.events())
    assert len(proj.finished_runs()) == 1
```

- [ ] Run `uv run pytest tests/test_projection.py tests/test_runs.py -q` then `uv run pytest -q` → pass. Commit `feat(projection): feed MatchContext to RunTracker`.

### Task 6: RunTracker — start on the configured condition

**Files:** `src/sm64_events/tracking/runs.py`; `tests/test_runs.py`. **This is the core change.**

- [ ] **Test** — the existing `Ev`/`att` helpers need a `MatchContext` arg now. Update `RunTracker.feed` calls in `test_runs.py` to pass a context, and add:
```python
from sm64_events.tracking.segments import MatchContext
CTX = MatchContext(level=None, prev_level=None, num_stars=None)

# update existing feed calls: rt.feed(ev, closed)  ->  rt.feed(ev, closed, CTX)

def test_default_reset_game_starts_on_game_reset():
    rt = RunTracker()
    steps = [{"need": 1, "candidates": [STAR]}]
    rt.feed(started(steps), [], CTX)          # started() payload: start_condition reset_game
    rt.feed(Ev("game_reset", id=100), [], CTX)
    assert rt.active_run_view() is not None


def test_non_reset_start_condition_ignores_game_reset_then_starts_on_match():
    rt = RunTracker()
    steps = [{"need": 1, "candidates": [STAR]}]
    rt.feed(started(steps, start_condition={"type": "level_enter", "to": 9}), [], CTX)
    rt.feed(Ev("game_reset", id=100), [], CTX)
    assert rt.active_run_view() is None       # game_reset is NOT the start condition
    rt.feed(Ev("level_changed", id=101, payload={"from": 1, "to": 9}), [],
            MatchContext(level=9, prev_level=1, num_stars=None))
    assert rt.active_run_view() is not None


def test_game_reset_aborts_when_not_the_start_condition():
    rt = RunTracker()
    steps = [{"need": 1, "candidates": [STAR]}, {"need": 1, "candidates": [SEG]}]
    rt.feed(started(steps, start_condition={"type": "level_enter", "to": 9}), [], CTX)
    rt.feed(Ev("level_changed", id=101, payload={"from": 1, "to": 9}), [],
            MatchContext(level=9, prev_level=1, num_stars=None))   # started
    out = rt.feed(Ev("game_reset", id=200), [], CTX)               # hard reset -> abort
    assert out and out[0].status == "aborted"
    assert rt.active_run_view() is None        # NOT restarted (game_reset != start cond)
```
Update `started(...)` helper to accept `start_condition` (default `{"type":"reset_game"}`) and put it in the payload.

- [ ] **Implement** in `runs.py`:
  - Import: `from sm64_events.tracking.segments import TRIGGERS`.
  - Add a matcher:
    ```python
    def _cond_fires(cond, ev, ctx) -> bool:
        t = TRIGGERS.get(cond.get("type"))
        return bool(t and t.match(cond, ev, ctx))
    ```
  - `_armed` gains `"start_condition"` (from `run_started` payload, default `{"type":"reset_game"}`).
  - Rewrite `feed` to take `ctx` and use the condition:
    ```python
    def feed(self, ev, closed, ctx) -> list:
        produced = []
        if ev.type == "run_started":
            if self._active is not None:
                produced.append(self._finalize("aborted", ev.wall_time_utc))
            p = ev.payload
            self._armed = {"route_id": p.get("route_id"),
                           "route_name": p.get("route_name", ""),
                           "route_steps": p.get("route_steps", []),
                           "mode": p.get("mode", "forgiving"),
                           "offset": int(p.get("start_offset_ms", 0)),
                           "start_condition": p.get("start_condition", {"type": "reset_game"})}
            self._active = None
        elif ev.type == "run_ended":
            if self._active is not None:
                produced.append(self._finalize("aborted", ev.wall_time_utc))
            self._armed = None
            self._active = None
        elif self._armed is not None:
            if _cond_fires(self._armed["start_condition"], ev, ctx):
                if self._active is not None:
                    produced.append(self._finalize("aborted", ev.wall_time_utc))
                self._begin(ev)
            elif ev.type == "game_reset" and self._active is not None:
                # hard reset that is NOT this route's start condition: the run is
                # over (player bailed); they re-trigger the start condition to begin.
                produced.append(self._finalize("aborted", ev.wall_time_utc))
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
    ```
  - `active_run_view` adds `"start_condition": self._armed["start_condition"]`.

- [ ] Run `uv run pytest tests/test_runs.py -q` → pass (all updated + new). Commit `feat(runs): start the clock on the route's configured condition`.

### Task 7: views — start_condition in payloads + run-history split detail (#4)

**Files:** `src/sm64_events/tracking/views.py`; `tests/test_views.py`.

- [ ] **Test** — add:
```python
def test_route_view_includes_start_condition(tmp_path):
    from sm64_events.tracking.views import build_route_view
    db, svc = make(tmp_path)
    rid = asyncio.run(svc.create_route({"name": "R",
        "start_condition": {"type": "reset_game"}, "steps": [
        {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]}))
    assert build_route_view(db, rid)["start_condition"] == {"type": "reset_game"}


def test_run_history_splits_carry_display_and_duration(tmp_path):
    from sm64_events.tracking.views import build_run_history
    db, svc = make(tmp_path)
    rid = asyncio.run(svc.create_route({"name": "R", "steps": [
        {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]}))
    db.insert_run({"id": 1, "route_id": rid, "route_name": "R",
        "route_steps": [{"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}],
        "mode": "forgiving", "status": "finished", "reached_step": 1, "total_ms": 60000,
        "start_offset_ms": 1360, "started_utc": "t", "ended_utc": "t", "is_pb": 1,
        "splits": [{"step_index": 0, "completed_item": {"type": "star", "course": 2, "star": 0},
                    "elapsed_ms": 60000, "attempts": 1, "fails": 0}]})
    sp = build_run_history(db, route_id=rid)["runs"][0]["splits"][0]
    assert sp["display"] == "Chip off Whomp's Block"
    assert sp["duration_ms"] == 60000 and sp["duration_display"] is not None
```

- [ ] **Implement** in `views.py`:
  - `build_route_view`: add `"start_condition": route["start_condition"]` to the returned dict.
  - `build_run_history`: for each run, enrich `splits` — resolve each split's `display` (from `completed_item` via `star_name`/segment names) and `duration_ms` (cumulative delta from the prior split) + `duration_display` (`_fmt_ms(duration_ms)`):
    ```python
    def _enrich_splits(run, seg_names):
        out, prev = [], 0
        for s in run["splits"]:
            ci = s.get("completed_item") or {}
            disp = (seg_names.get(ci.get("segment_id"), "segment (deleted)")
                    if ci.get("type") == "segment"
                    else star_name(ci.get("course"), ci.get("star"))
                    if ci.get("type") == "star" else "?")
            dur = (s["elapsed_ms"] - prev) if s["elapsed_ms"] is not None else None
            prev = s["elapsed_ms"] if s["elapsed_ms"] is not None else prev
            out.append({**s, "display": disp, "duration_ms": dur,
                        "duration_display": _fmt_ms(dur)})
        return out
    ```
    and in `build_run_history` map `{**r, "display_total": ..., "splits": _enrich_splits(r, seg_names)}` with `seg_names = {d["id"]: d["name"] for d in db.segment_defs()}`.

- [ ] Run `uv run pytest tests/test_views.py -q` then `uv run pytest -q` → pass. Commit `feat(views): start_condition in route view + run-history split detail`.

### Task 8: api — RouteBody/RoutePatch accept start_condition

**Files:** `src/sm64_events/server/api.py`; `tests/test_api.py`.

- [ ] **Test** — add:
```python
def test_create_route_with_start_condition(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        lblj = _lblj(db)
        r = client.post("/api/routes", json={"name": "R",
            "start_condition": {"type": "reset_game"},
            "steps": [{"need": 1, "candidates": [{"type": "segment", "segment_id": lblj}]}]})
        assert r.status_code == 200
        rid = r.json()["id"]
        assert client.get(f"/api/routes/{rid}").json()["start_condition"] == {"type": "reset_game"}
```

- [ ] **Implement** — add `start_condition: dict | None = None` to `RouteBody` and `RoutePatch` in `api.py`. (The service already reads `d.get("start_condition")`; `model_dump()` carries it. For `RoutePatch`, the existing `{k:v for ... if v is not None}` filter passes it through when set.)

- [ ] Run `uv run pytest tests/test_api.py -q` then `uv run pytest -q` → pass (merge gate). Commit `feat(api): routes accept start_condition`.

---

## Frontend

### Task 9: Route builder — start-condition picker (reuse the trigger UI)

**Files:** `src/sm64_events/ui/components/segments.js` (export `ClauseRow`); `src/sm64_events/ui/components/routes.js`.

- [ ] **Implement:**
  - In `segments.js`, export the trigger row + param input so the route builder can reuse the vocab-driven UI: change `function ClauseRow(` → `export function ClauseRow(` and `function ParamInput(` → `export function ParamInput(`.
  - In `routes.js`: fetch vocab (`getJSON("/api/segments/vocab")`) once; in the selected-route editor, render a single start-condition `ClauseRow` bound to the route's `start_condition` (default `{type:"reset_game"}`), saving via the existing PUT (`{start_condition: ...}`) + re-fetch. Label it "Run starts when:". Use `ClauseRow` with `types=${vocab.triggers}` and a one-clause model.
    ```javascript
    // in the route editor, near the steps:
    ${vocab ? html`<div class="routestart">
      <span class="meta">Run starts when:</span>
      <${ClauseRow} clause=${selected.start_condition || { type: "reset_game" }}
        types=${vocab.triggers} vocab=${vocab}
        onChange=${(c) => saveStartCondition(c)} onRemove=${() => {}} />
    </div>` : null}
    ```
    with `async function saveStartCondition(c){ await send("PUT", `/api/routes/${selId}`, { start_condition: c }); loadRoutes(); }`.

- [ ] `node --check` both files; smoke later. Commit `feat(ui): per-route run-start condition picker`.

### Task 10: Run-view rework (items 1–6)

**Files:** `src/sm64_events/ui/components/runview.js`; `src/sm64_events/ui/index.html`.

Rework the `Run` component so it ALWAYS shows a clock + step list, sourced active → finished → preview, with NO Start button (select arms), click-to-expand history splits, and the corrected graph. Replace the `Run` component and `RunGraph` with:

```javascript
// --- replaces the existing RunGraph: x = oldest->newest, y = time with 0 at
// the BOTTOM and the worst time at the TOP (slower = higher; not inverted). ---
function RunGraph({ runs }) {
  const fin = runs.filter((r) => r.status === "finished" && r.total_ms != null);
  if (fin.length < 2)
    return html`<div class="rungraph-empty">finish at least 2 runs to see a graph</div>`;
  const W = 600, H = 150, pad = 22;
  const val = (r) => r.total_ms + r.start_offset_ms;
  const max = Math.max(...fin.map(val)) || 1;        // 0-based axis
  const x = (i) => pad + (i * (W - 2 * pad)) / (fin.length - 1);
  const y = (v) => (H - pad) - (v / max) * (H - 2 * pad);   // 0 -> bottom, max -> top
  const path = fin.map((r, i) =>
    `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(val(r)).toFixed(1)}`).join(" ");
  return html`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="rungraph">
    <line x1=${pad} y1=${H - pad} x2=${W - pad} y2=${H - pad} stroke="#2c3140" />
    <path d=${path} fill="none" stroke="#4a6fa5" stroke-width="1.5" />
    ${fin.map((r, i) => html`<circle cx=${x(i)} cy=${y(val(r))} r="3.5"
        fill=${r.is_pb ? "#ffd75f" : "#6fa8ff"}>
      <title>${fmtMs(val(r))}${r.is_pb ? " · PB" : ""} — ${fmtDate(r.started_utc)}</title>
    </circle>`)}
  </svg>`;
}

export function Run({ t }) {
  const run = t.run;                       // {active, pb, gold, start_offset_ms}
  const [routes, setRoutes] = useState([]);
  const [routeId, setRouteId] = useState(() => {
    const s = localStorage.getItem("sm64.activeRoute"); return s ? Number(s) : null; });
  const [routeView, setRouteView] = useState(null);   // preview steps + start_condition
  const [hist, setHist] = useState(null);
  const [focus, setFocus] = useState(() => localStorage.getItem("sm64.runFocus") === "1");
  const [hidden, setHidden] = useState(loadHidden);
  const [openRun, setOpenRun] = useState(null);        // run id expanded in history
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [err, setErr] = useState(null);

  useEffect(() => { getJSON("/api/routes").then(setRoutes).catch(() => {}); }, []);
  const active = run && run.active;
  const effRouteId = active ? active.route_id : routeId;
  useEffect(() => {
    if (effRouteId == null) { setRouteView(null); setHist(null); return; }
    getJSON(`/api/routes/${effRouteId}`).then(setRouteView).catch(() => setRouteView(null));
    getJSON(`/api/run/history?route_id=${effRouteId}`).then(setHist).catch(() => setHist(null));
  }, [effRouteId, run]);
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNowMs(Date.now()), 60);
    return () => clearInterval(id);
  }, [active && active.id]);

  const toggleFocus = () => {
    const v = !focus; localStorage.setItem("sm64.runFocus", v ? "1" : "0"); setFocus(v); };
  const toggleHide = (key) => setHidden((prev) => {
    const next = new Set(prev); next.has(key) ? next.delete(key) : next.add(key);
    localStorage.setItem("sm64.runHidden", JSON.stringify([...next])); return next; });
  const Timer = ({ k, children, cls }) => html`<span class="runhide ${cls || ""}"
      title="click to hide/show" onclick=${() => toggleHide(k)}>${
      hidden.has(k) ? "- - - -" : children}</span>`;

  // Selecting a route ARMS it (no Start button). "none" disarms.
  async function pickRoute(id) {
    setErr(null);
    if (id == null) {
      localStorage.removeItem("sm64.activeRoute"); setRouteId(null);
      try { await send("POST", "/api/run/end"); } catch (e) {}
      t.refreshRun(); return;
    }
    localStorage.setItem("sm64.activeRoute", String(id)); setRouteId(id);
    try { await send("POST", "/api/run/start", { route_id: id }); } catch (e) { setErr(String(e)); }
    t.refreshRun();
  }

  if (!run) return html`<p class="meta">loading…</p>`;

  // latest finished run for the frozen post-run display
  const lastFinished = hist && [...hist.runs].reverse()
    .find((r) => r.status === "finished" && r.total_ms != null);

  // clock + step rows by state: active (live) > finished (frozen) > idle (preview)
  let clockMs, rows;
  if (active) {
    clockMs = nowMs - Date.parse(active.started_utc) + active.start_offset_ms;
    rows = active.steps.map((s, i) => ({
      key: i, display: s.display, group: s.candidates && s.candidates.length > 1,
      need: s.need, doneN: s.done.length, current: i === active.current_step,
      cumMs: s.elapsed_ms != null ? s.elapsed_ms + active.start_offset_ms
        : (i === active.current_step ? clockMs : null) }));
  } else if (lastFinished) {
    clockMs = lastFinished.total_ms + lastFinished.start_offset_ms;
    rows = lastFinished.splits.map((s) => ({
      key: s.step_index, display: s.display, current: false,
      cumMs: s.elapsed_ms + lastFinished.start_offset_ms }));
  } else {
    clockMs = run.start_offset_ms;                       // idle: 0:00 + offset
    rows = (routeView ? routeView.steps : []).map((s, i) => ({
      key: i, display: (s.candidates[0] && s.candidates[0].display) || s.label || "?",
      group: s.candidates.length > 1, need: s.need, current: false, cumMs: null }));
  }
  const startLabel = routeView && routeView.start_condition
    ? (routeView.start_condition.type === "reset_game" ? "starts on game reset (F1)"
       : `starts on: ${routeView.start_condition.type}`) : "";

  return html`<div class=${focus ? "runfocus" : ""}>
    <div class="runbar">
      <select value=${effRouteId ?? ""} disabled=${!!active}
          onchange=${(e) => pickRoute(e.target.value ? Number(e.target.value) : null)}>
        <option value="">— pick a route —</option>
        ${routes.map((r) => html`<option value=${r.id}>${r.name}</option>`)}
      </select>
      <button onclick=${toggleFocus}>${focus ? "Focus ✓" : "Focus"}</button>
      <span style="flex:1"></span>
      ${run.pb ? html`<span class="meta">PB ${run.pb.display}</span>` : null}
    </div>
    ${err ? html`<div class="badx">${err}</div>` : null}
    ${effRouteId == null
      ? html`<p class="meta">Pick a route to arm a run. The clock starts on the route's start condition (default F1).</p>`
      : html`<div>
        <div class="runclock"><${Timer} k="total">${fmtMs(clockMs)}<//>
          ${active ? "" : html` <span class="meta">${lastFinished ? "(finished)" : startLabel}</span>`}</div>
        <table class="runsplits"><tbody>
          ${rows.map((r) => html`<tr class=${r.current ? "runstep-cur" : (r.cumMs != null ? "rundone" : "runupcoming")}>
            <td class="meta">${r.key + 1}</td>
            <td>${r.group ? html`<span class="chip">${r.need} of</span> ` : ""}${r.display}
              ${r.group && r.doneN != null ? html` <span class="meta">(${r.doneN}/${r.need})</span>` : ""}</td>
            <td style="text-align:right"><${Timer} k=${`step:${r.key}`}>${fmtMs(r.cumMs)}<//></td>
          </tr>`)}
        </tbody></table>
      </div>`}
    <${RunHistory} t=${t} hist=${hist} openRun=${openRun} setOpenRun=${setOpenRun} />
  </div>`;
}
```

And update `RunHistory` to take `hist` as a prop (no own fetch — the parent fetches it) and support click-to-expand splits (#4):

```javascript
function RunHistory({ t, hist, openRun, setOpenRun }) {
  const [finishedOnly, setFinishedOnly] = useState(true);
  if (!hist) return html`<div class="runhistory meta">no run history yet</div>`;
  const finished = hist.runs.filter((r) => r.status === "finished" && r.total_ms != null);
  const pbRun = finished.length
    ? finished.reduce((a, b) => (a.total_ms <= b.total_ms ? a : b)) : null;
  const list = [...hist.runs].reverse();
  const shown = finishedOnly ? list.filter((r) => r.status === "finished") : list;
  return html`<div class="runhistory">
    <div class="shead"><b>Run history</b>
      <label class="meta"><input type="checkbox" checked=${finishedOnly}
          onchange=${(e) => setFinishedOnly(e.target.checked)} /> finished only</label>
      ${pbRun ? html`<span class="pbtag">PB ${pbRun.display_total}</span>` : null}</div>
    <${RunGraph} runs=${hist.runs} />
    ${shown.length === 0 ? html`<p class="meta">no runs yet</p>` : html`<table><tbody>
      ${shown.map((r) => [
        html`<tr style="cursor:pointer"
            onclick=${() => setOpenRun(openRun === r.id ? null : r.id)}>
          <td class="meta">${fmtDate(r.started_utc)}</td>
          <td>${r.status === "finished"
              ? html`<b>${r.display_total}</b>${r.is_pb ? html` <span class="rungold">★</span>` : ""}`
              : html`<span class="meta">aborted · reached step ${r.reached_step}</span>`}
            <span class="meta"> ${openRun === r.id ? "▾" : "▸"}</span></td>
        </tr>`,
        openRun === r.id ? html`<tr><td colspan="2"><table class="runsplits"><tbody>
          ${r.splits.map((s) => html`<tr>
            <td class="meta">${s.step_index + 1}</td><td>${s.display}</td>
            <td style="text-align:right">${s.duration_display}
              <span class="meta">${s.fails ? `· ${s.fails} fail${s.fails > 1 ? "s" : ""}` : ""}</span></td>
          </tr>`)}
        </tbody></table></td></tr>` : null,
      ])}
    </tbody></table>`}
  </div>`;
}
```

CSS (`index.html`): add `.routestart { margin: .5rem 0; }` near the route-builder rules.

- [ ] `node --check src/sm64_events/ui/components/runview.js` → clean. Commit `feat(ui): run-view rework — always-on clock, preview, frozen finish, click-splits, fixed graph, no Start button`.

---

## Task 11: Smoke test + human-audit

- [ ] `uv run pytest -q` green. Seed (as in Phase E) a route with finished runs + start a fresh `run_started`; start `SM64_PORT=8066 uv run python -m sm64_events.main`.
- [ ] **frontend-smoke-test:** Run tab —
  1. Idle (pick a route, no run yet): clock shows `0:01.36` (offset), step **preview** lists the route's steps in order, "starts on game reset (F1)" note, NO Start button.
  2. Selecting a route arms it (no error; `GET /api/run` reflects it after F1).
  3. Graph: x oldest→newest, y 0-at-bottom, slower-higher (the slow run is HIGHER than the fast one); gold PB dots.
  4. Click a history run → expands its per-split breakdown (names + durations + fails).
  5. Focus + click-to-hide still work; clock never disappears (idle/finished states show it).
  6. Route builder (Routes tab): a "Run starts when:" trigger picker; set it to a level-enter and back to reset_game; persists.
- [ ] Fix issues; commit.
- [ ] **human-audit:** real run — Start by F1, watch always-on clock; finish → clock freezes + splits stay; open a past run's splits; try a non-reset start condition on a second route if desired. Sign off.

## Task 12: Docs

- [ ] `CLAUDE.md`: update the route + run rows (start_condition; reset_game trigger; run-view always-on/preview/click-splits). `README`: run-start-condition note. Commit `docs: run-start condition + run-view rework (Phase F)`.

---

## Self-Review
- Items 1–7 each mapped to a task (1→T10 preview; 2→T10 no-button/arm; 3→T10 graph; 4→T7+T10 split detail/expand; 5/6→T10 always-on clock; 7→T1–T9 start_condition end-to-end).
- LBLJ preserved: default `start_condition=reset_game` ⇒ T6's `_cond_fires` matches `game_reset` exactly like before.
- Shared contract: only `projection.py` `feed` call signature (T5); re-confirm clean before editing.
- Migration ripple (v8→v9) handled in T2.
- **Risk:** T6 `RunTracker.feed` signature change ripples to every `test_runs.py` call + the projection call site — update all. The htm rework (T10) is large — `node --check` gate before commit (Phase C lesson).
