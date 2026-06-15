# Route Builder — Phase D-UI (Run View) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The visible full-game run timer — a "Run" tab with a route picker, Start/End controls, a live splits panel (ticking clock, ± vs PB, gold), **Focus mode**, and **click-to-hide** on any timer. Consumes the Phase D backend (`/api/run/*` + `run_*` WS events).

**Architecture:** Mostly frontend. A new `ui/components/runview.js` (the Run tab) renders `GET /api/run`; `store.js` holds the `run` state and re-fetches it on `run_*`/`game_reset` WS events; the clock **ticks client-side** off the authoritative `started_utc` + `start_offset_ms`. One small backend addition: `build_run_view` gains per-step PB-cumulative + gold-duration so ± and gold highlighting are server-computed.

**Tech Stack:** Vendored Preact + htm. Verification: **frontend-smoke-test** (Chrome DevTools MCP) + **human-audit**. Backend tweak is pytest-gated.

**Source spec:** `docs/superpowers/specs/2026-06-14-route-builder-design.md` (§6 run view; §3 Focus mode + click-to-hide). **Depends on Phase D backend** (merged): `GET /api/run` (`{active, pb, gold, start_offset_ms}`), `POST /api/run/start|end`, and the broadcast `run_started`/`run_progress`/`run_finished`/`run_aborted` events. The F1→`game_reset` live gate has passed.

**Scope note:** Plan **5 of 6** (final UI for run mode). **Phase E** (run history list + progression graph) is separate. Build only the live run view here.

**Locked decisions:**
- A **new "Run" tab** (flag at human-audit; trivial to relocate).
- Clock ticks client-side: `elapsed = now − Date.parse(started_utc) + start_offset_ms` (no pause-subtraction in v1, matching the backend).
- Each step row shows its **cumulative split** (this run) + **± vs PB cumulative** + **gold ★** when its segment duration beats the route's best. Current step shows the live cumulative (= the big clock).
- **Focus mode** (button, `localStorage sm64.runFocus`): neutral monochrome, no ± deltas, no gold coloring. **Click-to-hide** (`localStorage sm64.runHidden` set of timer keys): any timer → `----`, click again to reveal.

**Convention:** commits end with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. Stage explicit paths (`git add -A` is hook-blocked). Verify branch before each commit. Execute in an isolated worktree off current master. **Smoke-test on `SM64_PORT=8066`** (dev-from-source defaults to 8065 now; avoid colliding with a running dev server).

---

## File Structure

- **Modify** `src/sm64_events/tracking/views.py` — extend `build_run_view` per-step with `pb_elapsed_ms` + `gold_ms`.
- **Modify** `src/sm64_events/ui/store.js` — `run` state + `refreshRun`; refetch on `run_*`/`game_reset`.
- **Create** `src/sm64_events/ui/components/runview.js` — the Run tab.
- **Modify** `src/sm64_events/ui/app.js` — add the "Run" tab.
- **Modify** `src/sm64_events/ui/index.html` — run-view CSS.
- **Modify** `README.md` / `CLAUDE.md` — Run tab + `/api/run/*` surface.

---

## Task 1: Per-step comparison data in `build_run_view`

**Files:** Modify `src/sm64_events/tracking/views.py`; Test `tests/test_views.py`.

- [ ] **Step 1: Write the failing test** — add to `tests/test_views.py`:

```python
def test_build_run_view_adds_per_step_pb_and_gold(tmp_path):
    from sm64_events.tracking.views import build_run_view
    db, svc = make(tmp_path)
    rid = asyncio.run(svc.create_route({"name": "RC", "steps": [
        {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]},
        {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 1}]}]}))
    # one finished run in history: step0 cumulative 60s, step1 cumulative 130s
    db.insert_run({"id": 1, "route_id": rid, "route_name": "RC",
        "route_steps": [{"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]},
                        {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 1}]}],
        "mode": "forgiving", "status": "finished", "reached_step": 2,
        "total_ms": 130000, "start_offset_ms": 1360,
        "started_utc": "t", "ended_utc": "t", "is_pb": 1,
        "splits": [{"step_index": 0, "elapsed_ms": 60000},
                   {"step_index": 1, "elapsed_ms": 130000}]})
    # start an active run on the same route
    asyncio.run(svc.start_run(rid))
    asyncio.run(svc.publish(ev("game_reset", 0)))
    view = build_run_view(db, svc)
    s0, s1 = view["active"]["steps"]
    assert s0["pb_elapsed_ms"] == 60000 and s0["gold_ms"] == 60000     # step0 duration 60s
    assert s1["pb_elapsed_ms"] == 130000 and s1["gold_ms"] == 70000    # step1 duration 70s
```

- [ ] **Step 2: Run → fail.** `uv run pytest tests/test_views.py -k run -q`.

- [ ] **Step 3: Implement.** In `build_run_view` (views.py), where the active steps are built and `pb`/`gold` are computed, enrich each step. Replace the active-steps construction with one that also attaches `pb_elapsed_ms` + `gold_ms`:

```python
def build_run_view(db, service) -> dict:
    """Live run state for the run panel: the active run (resolved step names +
    elapsed + per-step PB-cumulative and gold-duration for ±/gold) plus the
    route's PB total and gold sum-of-best."""
    from sm64_events.tracking.runs import pb_run, gold_splits, _step_durations
    act = service.active_run()
    seg_names = {d["id"]: d["name"] for d in db.segment_defs()}
    offset = service.run_settings()["start_offset_ms"]
    out = {"active": None, "pb": None, "gold": None, "start_offset_ms": offset}
    if act is None:
        return out
    steps_def = next((r["steps"] for r in db.routes()
                      if r["id"] == act["route_id"]), [])
    runs = db.runs(route_id=act["route_id"]) if act["route_id"] is not None else []
    pb = pb_run(runs)
    gold = gold_splits(runs, steps_def)
    pb_cum = {s["step_index"]: s["elapsed_ms"] for s in pb["splits"]} if pb else {}
    gold_dur = gold["durations"]
    steps = []
    for i, s in enumerate(act["steps"]):
        cands = _resolve_cands(steps_def[i]["candidates"], seg_names) \
            if i < len(steps_def) else []
        steps.append({**s, "candidates": cands,
                      "display": cands[0]["display"] if cands else "?",
                      "elapsed_display": _fmt_ms(
                          None if s["elapsed_ms"] is None
                          else s["elapsed_ms"] + offset),
                      "pb_elapsed_ms": pb_cum.get(i),
                      "gold_ms": gold_dur.get(i)})
    out["active"] = {**act, "steps": steps}
    out["pb"] = {"total_ms": pb["total_ms"],
                 "display": _fmt_ms(pb["total_ms"] + offset)} if pb else None
    out["gold"] = {"sum_of_best": gold["sum_of_best"],
                   "display": _fmt_ms(None if gold["sum_of_best"] is None
                                      else gold["sum_of_best"] + offset)}
    return out
```

> This supersedes the Phase D `build_run_view`. `_step_durations` is imported but only `gold_splits`/`pb_run` are used here — drop the `_step_durations` import if your linter flags it. `_resolve_cands`/`_fmt_ms` already exist from Phase D.

- [ ] **Step 4: Run → pass + full suite.** `uv run pytest tests/test_views.py -q` then `uv run pytest -q`.

- [ ] **Step 5: Commit.**

```bash
git add src/sm64_events/tracking/views.py tests/test_views.py
git commit -m "feat(views): per-step PB-cumulative + gold in run view"
```

---

## Task 2: `run` state in the store

**Files:** Modify `src/sm64_events/ui/store.js`.

- [ ] **Step 1: Add the run state + refetch.** In `useTracker()`:
  1. Add state + fetch (near the other `useState`/`getJSON` usage):
     ```javascript
     const [run, setRun] = useState(null);
     const refreshRun = useCallback(async () => {
       try { setRun(await getJSON("/api/run")); } catch (e) { /* keep last */ }
     }, []);
     useEffect(() => { refreshRun(); }, [refreshRun]);
     ```
     (`useCallback` is already imported in store.js; `getJSON` is imported.)
  2. In the `ws.onmessage` handler, after the existing `if (REFRESH_ON.has(ev.type)) refresh();` line, add:
     ```javascript
     if (RUN_REFRESH_ON.has(ev.type)) refreshRun();
     ```
     and define near the top of the file (next to `REFRESH_ON`):
     ```javascript
     const RUN_REFRESH_ON = new Set(["run_started", "run_progress",
       "run_finished", "run_aborted", "game_reset"]);
     ```
  3. Add `run` and `refreshRun` to the returned object:
     ```javascript
     return { view, clock, pickClock, scope, pickScope, feed, connected,
              refresh, paused: pauseState.paused,
              pauseReason: pauseState.reason, togglePause,
              armedSegs, armedOrder, lastPinnedSeg, stage,
              run, refreshRun };
     ```
     (Append `run, refreshRun` to the EXISTING return object — do not drop any existing keys.)

- [ ] **Step 2: Commit.**

```bash
git add src/sm64_events/ui/store.js
git commit -m "feat(ui): run state in the store (refetch on run_* events)"
```

---

## Task 3: Run-view CSS

**Files:** Modify `src/sm64_events/ui/index.html`.

- [ ] **Step 1: Add CSS** (after the route-focus rules):

```css
  .runbar { display: flex; flex-wrap: wrap; gap: .6rem; align-items: center; margin: .6rem 0; }
  .runclock { font-size: 2.2rem; font-weight: bold; letter-spacing: 1px; }
  .runsplits { width: 100%; border-collapse: collapse; font-size: .95em; }
  .runsplits td { padding: .25rem .5rem; border-bottom: 1px dotted #2c3140; }
  .runstep-cur { background: rgba(68,153,136,.14); }
  .runstep-cur td { border-color: #4ca; }
  .rundone { opacity: .85; } .runupcoming { opacity: .4; }
  .rungold { color: #ffd75f; }
  .runahead { color: #a3e0a3; } .runbehind { color: #e0a3a3; }
  .runhide { cursor: pointer; }
  /* Focus mode: strip all performance coloring to a calm monochrome */
  .runfocus .rungold, .runfocus .runahead, .runfocus .runbehind,
  .runfocus .runclock { color: #d8dee9; }
  .runfocus .runstep-cur { background: rgba(200,200,200,.10); }
  .runfocus .runstep-cur td { border-color: #888; }
```

- [ ] **Step 2: Commit.**

```bash
git add src/sm64_events/ui/index.html
git commit -m "feat(ui): run-view CSS"
```

---

## Task 4: The Run tab

**Files:** Create `src/sm64_events/ui/components/runview.js`; Modify `src/sm64_events/ui/app.js`.

- [ ] **Step 1: Create `src/sm64_events/ui/components/runview.js`:**

```javascript
// src/sm64_events/ui/components/runview.js — full-game run timer (Run tab).
// Renders GET /api/run (via the store's t.run, refetched on run_* WS events);
// the big clock + current-step time TICK client-side off the authoritative
// started_utc + start_offset_ms. Forgiving RTA: no pause subtraction (v1).
// Focus mode (neutral, no ±/gold) and click-to-hide any timer are pure UI
// state in localStorage.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";

const html = htm.bind(h);

function fmtMs(ms) {
  if (ms == null) return "—";
  const sign = ms < 0 ? "-" : "";
  ms = Math.abs(Math.round(ms));
  const m = Math.floor(ms / 60000);
  const s = Math.floor((ms % 60000) / 1000);
  const cs = Math.floor((ms % 1000) / 10);
  return `${sign}${m}:${String(s).padStart(2, "0")}.${String(cs).padStart(2, "0")}`;
}
const fmtDelta = (ms) =>
  ms == null ? "" : `${ms > 0 ? "+" : ms < 0 ? "−" : ""}${(Math.abs(ms) / 1000).toFixed(2)}`;

// Persisted set of hidden timer keys (click-to-hide).
function loadHidden() {
  try { return new Set(JSON.parse(localStorage.getItem("sm64.runHidden") || "[]")); }
  catch { return new Set(); }
}

export function Run({ t }) {
  const run = t.run;
  const [routes, setRoutes] = useState([]);
  const [routeId, setRouteId] = useState(() => {
    const s = localStorage.getItem("sm64.activeRoute"); return s ? Number(s) : null;
  });
  const [focus, setFocus] = useState(() => localStorage.getItem("sm64.runFocus") === "1");
  const [hidden, setHidden] = useState(loadHidden);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [err, setErr] = useState(null);

  useEffect(() => { getJSON("/api/routes").then(setRoutes).catch(() => {}); }, []);
  // tick the live clock only while a run is active
  const active = run && run.active;
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNowMs(Date.now()), 60);
    return () => clearInterval(id);
  }, [active && active.id]);

  const toggleFocus = () => {
    const v = !focus; localStorage.setItem("sm64.runFocus", v ? "1" : "0"); setFocus(v);
  };
  const toggleHide = (key) => setHidden((prev) => {
    const next = new Set(prev);
    next.has(key) ? next.delete(key) : next.add(key);
    localStorage.setItem("sm64.runHidden", JSON.stringify([...next]));
    return next;
  });
  const Timer = ({ k, children, cls }) => html`<span
      class="runhide ${cls || ""}" title="click to hide/show"
      onclick=${() => toggleHide(k)}>${hidden.has(k) ? "- - - -" : children}</span>`;

  async function startRun() {
    if (routeId == null) { setErr("pick a route first"); return; }
    try { setErr(null); await send("POST", "/api/run/start", { route_id: routeId });
      t.refreshRun(); }
    catch (e) { setErr(String(e)); }
  }
  async function endRun() {
    try { await send("POST", "/api/run/end"); t.refreshRun(); }
    catch (e) { setErr(String(e)); }
  }

  if (!run) return html`<p class="meta">loading…</p>`;

  // live total elapsed (ms) from the authoritative start + offset
  const liveMs = active
    ? (nowMs - Date.parse(active.started_utc) + active.start_offset_ms)
    : null;

  return html`<div class=${focus ? "runfocus" : ""}>
    <div class="runbar">
      <select value=${routeId ?? ""}
          onchange=${(e) => { const v = e.target.value ? Number(e.target.value) : null;
            setRouteId(v); if (v != null) localStorage.setItem("sm64.activeRoute", String(v)); }}>
        <option value="">— pick a route —</option>
        ${routes.map((r) => html`<option value=${r.id}>${r.name}</option>`)}
      </select>
      ${active
        ? html`<button onclick=${endRun}>End run</button>`
        : html`<button onclick=${startRun}>Start run</button>`}
      <button onclick=${toggleFocus}>${focus ? "Focus ✓" : "Focus"}</button>
      <span style="flex:1"></span>
      ${run.pb ? html`<span class="meta">PB ${run.pb.display}</span>` : null}
      ${run.gold && run.gold.display ? html`<span class="meta">SoB ${run.gold.display}</span>` : null}
    </div>

    ${err ? html`<div class="badx">${err}</div>` : null}

    ${!active
      ? html`<p class="meta">${routeId == null
          ? "Pick a route and press Start run."
          : "Armed — press F1 to begin the run (the clock starts on reset)."}</p>`
      : html`<div>
        <div class="runclock"><${Timer} k="total">${fmtMs(liveMs)}<//></div>
        <table class="runsplits"><tbody>
          ${active.steps.map((s, i) => {
            const isCur = i === active.current_step;
            const cls = isCur ? "runstep-cur" : (s.elapsed_ms != null ? "rundone" : "runupcoming");
            // cumulative shown: completed -> its split; current -> live; upcoming -> —
            const cumMs = s.elapsed_ms != null ? s.elapsed_ms + active.start_offset_ms
              : (isCur ? liveMs : null);
            // ± vs PB cumulative (only meaningful once this step has data and PB exists)
            const delta = (s.elapsed_ms != null && s.pb_elapsed_ms != null)
              ? (s.elapsed_ms + active.start_offset_ms) - (s.pb_elapsed_ms + active.start_offset_ms)
              : null;
            // gold: this step's segment duration beat the route's best for it
            const prevCum = i > 0 && active.steps[i - 1].elapsed_ms != null
              ? active.steps[i - 1].elapsed_ms : 0;
            const segDur = s.elapsed_ms != null ? s.elapsed_ms - prevCum : null;
            const isGold = !focus && segDur != null && s.gold_ms != null && segDur < s.gold_ms;
            const grp = s.candidates && s.candidates.length > 1;
            return html`<tr class=${cls}>
              <td class="meta">${i + 1}</td>
              <td>${grp ? html`<span class="chip">${s.need} of ${s.candidates.length}</span> ` : ""}
                  ${s.display}${grp ? html` <span class="meta">(${s.done.length}/${s.need})</span>` : ""}</td>
              <td style="text-align:right" class=${isGold ? "rungold" : ""}>
                <${Timer} k=${`step:${i}`}>${fmtMs(cumMs)}<//>${isGold ? " ★" : ""}</td>
              <td style="text-align:right">${focus || delta == null ? "" : html`
                <${Timer} k=${`delta:${i}`} cls=${delta > 0 ? "runbehind" : "runahead"}>
                  ${fmtDelta(delta)}<//>`}</td>
            </tr>`;
          })}
        </tbody></table>
      </div>`}
  </div>`;
}
```

- [ ] **Step 2: Wire the tab in `src/sm64_events/ui/app.js`.** Add the import and the tab. Change the imports block to include:
  ```javascript
  import { Run } from "./components/runview.js";
  ```
  Change `TABS`:
  ```javascript
  const TABS = ["Practice", "Segments", "Routes", "Run", "Live feed"];
  ```
  In the `pane` render, add the Run branch (before the `Feed` fallback):
  ```javascript
        : tab === "Routes" ? html`<${Routes} t=${t} />`
        : tab === "Run" ? html`<${Run} t=${t} />`
        : html`<${Feed} t=${t} />`}
  ```

- [ ] **Step 3: Commit.**

```bash
git add src/sm64_events/ui/components/runview.js src/sm64_events/ui/app.js
git commit -m "feat(ui): Run tab — live splits, Focus mode, click-to-hide"
```

---

## Task 5: Frontend smoke test + human-audit (the gate)

- [ ] **Step 1: Python sanity gate.** `uv run pytest -q` → PASS (Task 1's view change is the only Python; same+ count).

- [ ] **Step 2: Start the server** on a free port to avoid colliding with any dev server (8065 is now the dev default):
  ```bash
  SM64_PORT=8066 uv run python -m sm64_events.main
  ```
  Pre-seed a route with ≥2 steps and at least one finished run (so PB/gold render): create a route via the Routes tab, then `POST /api/run/start`, `POST` a `game_reset` event isn't possible over HTTP — instead simulate a finished run by inserting one via a short Python snippet against the dev db, OR just exercise the live flow below without PB/gold and confirm those render as "—".

- [ ] **Step 3: frontend-smoke-test skill** — drive this script, console clean each step:
  1. Open the app → the **Run** tab exists and is clickable.
  2. Run tab with no active run → shows the route picker + **Start run** + **Focus** button, and "Pick a route and press Start run."
  3. Pick a route → **Start run** → message changes to "Armed — press F1 to begin." (`GET /api/run` `active` still null — armed, not started). No console error.
  4. Simulate the run starting: with PJ64 absent you can't press F1, so POST a `game_reset` through the journal is not exposed; instead verify the ARMED state and the controls. (Live F1 behavior is covered by the human-audit with the emulator.)
  5. **Focus** button toggles a `Focus ✓` state and the panel loses its green/red/gold coloring (monochrome). Reload → Focus state persists.
  6. **Click-to-hide:** click the big clock / a split cell → it shows `- - - -`; click again → reveals. Reload → hidden state persists.
  7. **End run** returns to the idle picker state with no error.

- [ ] **Step 4: Fix any issues, commit** (if needed):

```bash
git add src/sm64_events/ui/components/runview.js src/sm64_events/ui/index.html src/sm64_events/ui/store.js
git commit -m "fix(ui): run-view smoke-test fixes"
```

- [ ] **Step 5: human-audit skill** — this feature's payoff is live. Summarize, then have the user (with PJ64) do a **real run**: Start run → F1 → play the route → watch the clock tick, splits land with ± vs PB and gold, reset mid-step (clock keeps going), F1 again (run restarts), finish the last step (run saves). Confirm Focus mode + click-to-hide feel right. Wait for sign-off before merge.

---

## Task 6: Docs

**Files:** `CLAUDE.md`, `README.md`.

- [ ] **Step 1: CLAUDE.md row:**

```
| Run view (live splits, Focus, hide) | `ui/components/runview.js` (Run tab) — route picker + Start/End; live splits panel ticking client-side off `started_utc`+offset from `GET /api/run`; ± vs PB-cumulative + gold per step; Focus mode (neutral) + click-to-hide any timer (localStorage `sm64.runFocus`/`sm64.runHidden`). Store holds `run`, refetched on `run_*`/`game_reset` |
```

- [ ] **Step 2: README** — add a Run section under the route/practice overview:

```markdown
- **Run mode (Run tab)** — run a whole route as a forgiving-RTA speedrun: Start
  run, press F1 to begin (clock starts at the configured offset, default 1.36s),
  per-step splits roll up retries, F1 restarts, the final step saves the run.
  Live ± vs your PB and gold splits; a **Focus** mode hides the deltas/colors and
  any timer is click-to-hide.
```
Also add `/api/run/start`, `/api/run/end`, `GET /api/run`, `/api/run/history`, `GET|PUT /api/run/settings` and the `run_*` WS events to the API surface (`docs/api.md` if present, else README).

- [ ] **Step 3: Commit.**

```bash
git add CLAUDE.md README.md
git commit -m "docs: Run tab + run API surface (Phase D-UI)"
```

---

## Self-Review (completed during planning)

**Spec coverage (§6 run view + §3):**
- Run tab + route picker + Start/End → Task 4.
- Live ticking clock off authoritative start+offset → Task 4 (`liveMs`, interval).
- Per-step splits with ± vs PB + gold highlight → Task 1 (data) + Task 4 (render).
- Forgiving display (current step shows live cumulative; completed steps freeze) → Task 4.
- Focus mode (neutral, no ±/gold) → Task 3 CSS + Task 4 `runfocus`.
- Click-to-hide any timer (`----`, persisted) → Task 4 `Timer`/`toggleHide`.
- Store re-fetch on run events → Task 2.
- Deferred (correctly): pause-subtraction (backend v1 too); Phase E history list + progression graph; the run-settings (offset) editor UI (offset is set via `PUT /api/run/settings`; a UI knob can ride with Phase E or settings).

**Consistency:** `GET /api/run` shape (`active.steps[].{elapsed_ms, pb_elapsed_ms, gold_ms, done, need, display, candidates}`, `pb`, `gold`, `start_offset_ms`) matches Task 1's `build_run_view`; store returns `run`/`refreshRun` appended to the existing object; `RUN_REFRESH_ON` includes `game_reset` (the event that starts/restarts a run).

**Placeholder scan:** none — full component, CSS, store diff, and the view extension.

**Risk:** Task 4 is a sizable new component with htm template literals — the implementer must keep backticks/`${}` balanced (the Phase C smoke test caught exactly this class of bug; `node --check` the file before the browser test). The ± and gold math is the subtle part; the human-audit with a real run is the true validation.

---

## Subsequent phase

- **Phase E** — run history list (finished/aborted filter) + progression graph of finished-run totals over time (gold dots = `is_pb`), from `GET /api/run/history`, reusing the `progress.js` pattern. Optionally a run-settings (start-offset) knob.
