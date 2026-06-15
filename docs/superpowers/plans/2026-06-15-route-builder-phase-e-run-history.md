# Route Builder — Phase E (Run History + Progression Graph) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The final piece — under the Run tab, a **run-history list** (finished/aborted filter) and a **progression graph** of finished-run totals over time (gold dots = PBs). Plus the wrap-up docs (README run section + `/api/run/*` API surface).

**Architecture:** Pure frontend. `GET /api/run/history?route_id=` already returns `{runs:[…], pb}` (Phase D). A `RunHistory` + small `RunGraph` are added to `runview.js` (the Run tab), fetched on mount / route change / run events. The graph is a small dedicated SVG — the existing `progress.js` is per-star/per-clock/per-session and doesn't fit a list of whole-game runs, so we reuse its *visual pattern* (line + dots, gold for PBs), not the component.

**Tech Stack:** Vendored Preact + htm. Verification: **frontend-smoke-test** (Chrome DevTools MCP) + **human-audit**. No Python changes.

**Source spec:** `docs/superpowers/specs/2026-06-14-route-builder-design.md` (§6 run history; §4.2 `is_pb` frozen for the graph). **Depends on Phase D** (merged): `GET /api/run/history` returns `runs[]` each with `status`, `reached_step`, `total_ms`, `start_offset_ms`, `started_utc`, `is_pb`, `display_total`, and `pb`.

**Scope note:** Plan **6 of 6** — the last phase. After this the route builder + run mode is feature-complete.

**Locked decisions:**
- `RunHistory` lives **in `runview.js`** (one Run-tab file, cohesive), rendered below the active/idle panel for the selected route. (Original plan named a separate `runhistory.js`; folding it in avoids a new-file/import round-trip — note it.)
- **Current PB** for the header = the min-total finished run (computed client-side from `runs`); the graph's **gold dots use `is_pb`** (frozen was-a-PB-when-achieved) so the dots trace PB *progression*.
- Displayed times fold the per-run offset (`total_ms + start_offset_ms`), reusing `runview.js`'s `fmtMs`.

**Convention:** commits end with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. Stage explicit paths (`git add -A` is hook-blocked). Verify branch before each commit. `node --check` any edited JS before committing (the Phase C backtick lesson). Execute in an isolated worktree off current master. Smoke-test on `SM64_PORT=8066`.

---

## File Structure

- **Modify** `src/sm64_events/ui/components/runview.js` — add `RunGraph` + `RunHistory`; render `RunHistory` in the `Run` component.
- **Modify** `src/sm64_events/ui/index.html` — history/graph CSS.
- **Modify** `README.md` — run-mode section + `/api/run/*` API surface (the deferred wrap-up doc).
- **Modify** `CLAUDE.md` — extend the run-view row to mention history/graph.

No new endpoints, no Python. Full pytest suite stays green (unaffected) as a sanity check.

---

## Task 1: History/graph CSS

**Files:** Modify `src/sm64_events/ui/index.html`.

- [ ] **Step 1: Add CSS** (after the run-view rules from Phase D-UI):

```css
  .runhistory { margin-top: 1rem; border-top: 1px solid #2c3140; padding-top: .6rem; }
  .rungraph { width: 100%; height: 150px; display: block; margin: .4rem 0; }
  .rungraph-empty { color: #6c7686; font-size: .85em; }
```

- [ ] **Step 2: Commit.**

```bash
git add src/sm64_events/ui/index.html
git commit -m "feat(ui): run-history + graph CSS"
```

---

## Task 2: `RunHistory` + `RunGraph` in the Run tab

**Files:** Modify `src/sm64_events/ui/components/runview.js`.

- [ ] **Step 1: Add `useCallback` to the hooks import.** Change the existing import:
  ```javascript
  import { useEffect, useRef, useState } from "preact/hooks";
  ```
  to:
  ```javascript
  import { useCallback, useEffect, useRef, useState } from "preact/hooks";
  ```

- [ ] **Step 2: Add the components.** Insert these two functions **before** `export function Run({ t })` (so `fmtMs`, defined above them, is in scope):

```javascript
function fmtDate(utc) {
  try { return new Date(utc).toLocaleString(); } catch { return utc; }
}

// Small dedicated progression graph: finished-run totals over time, gold dots
// for runs that were a PB when achieved (is_pb). Reuses progress.js's VISUAL
// pattern (line + dots, gold = PB) — not the component, whose per-star/clock
// shape doesn't fit whole-game runs. y inverted (lower time = higher = better).
function RunGraph({ runs }) {
  const fin = runs.filter((r) => r.status === "finished" && r.total_ms != null);
  if (fin.length < 2)
    return html`<div class="rungraph-empty">finish at least 2 runs to see a graph</div>`;
  const W = 600, H = 150, pad = 22;
  const val = (r) => r.total_ms + r.start_offset_ms;
  const vals = fin.map(val);
  const min = Math.min(...vals), max = Math.max(...vals), span = (max - min) || 1;
  const x = (i) => pad + (i * (W - 2 * pad)) / (fin.length - 1);
  const y = (v) => pad + ((v - min) / span) * (H - 2 * pad);
  const path = fin.map((r, i) =>
    `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(val(r)).toFixed(1)}`).join(" ");
  return html`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="rungraph">
    <path d=${path} fill="none" stroke="#4a6fa5" stroke-width="1.5" />
    ${fin.map((r, i) => html`<circle cx=${x(i)} cy=${y(val(r))} r="3.5"
        fill=${r.is_pb ? "#ffd75f" : "#6fa8ff"}>
      <title>${fmtMs(val(r))}${r.is_pb ? " · PB" : ""} — ${fmtDate(r.started_utc)}</title>
    </circle>`)}
  </svg>`;
}

function RunHistory({ t, routeId }) {
  const [hist, setHist] = useState(null);
  const [finishedOnly, setFinishedOnly] = useState(true);
  const load = useCallback(async () => {
    if (routeId == null) { setHist(null); return; }
    try { setHist(await getJSON(`/api/run/history?route_id=${routeId}`)); }
    catch { setHist(null); }
  }, [routeId]);
  // refetch on mount, route change, and run events (t.run flips on run_*)
  useEffect(() => { load(); }, [load, t.run]);

  if (routeId == null)
    return html`<div class="runhistory meta">pick a route to see its run history</div>`;
  if (!hist) return html`<div class="runhistory meta">loading history…</div>`;

  const finished = hist.runs.filter((r) => r.status === "finished" && r.total_ms != null);
  const pbRun = finished.length
    ? finished.reduce((a, b) => (a.total_ms <= b.total_ms ? a : b)) : null;
  const list = [...hist.runs].reverse();   // newest first for the table
  const shown = finishedOnly ? list.filter((r) => r.status === "finished") : list;

  return html`<div class="runhistory">
    <div class="shead">
      <b>Run history</b>
      <label class="meta"><input type="checkbox" checked=${finishedOnly}
          onchange=${(e) => setFinishedOnly(e.target.checked)} /> finished only</label>
      ${pbRun ? html`<span class="pbtag">PB ${pbRun.display_total}</span>` : null}
    </div>
    <${RunGraph} runs=${hist.runs} />
    ${shown.length === 0
      ? html`<p class="meta">no ${finishedOnly ? "finished " : ""}runs yet</p>`
      : html`<table><tbody>
        ${shown.map((r) => html`<tr>
          <td class="meta">${fmtDate(r.started_utc)}</td>
          <td>${r.status === "finished"
              ? html`<b>${r.display_total}</b>${r.is_pb ? html` <span class="rungold">★</span>` : ""}`
              : html`<span class="meta">aborted · reached step ${r.reached_step}</span>`}</td>
        </tr>`)}
      </tbody></table>`}
  </div>`;
}
```

- [ ] **Step 3: Render it in `Run`.** In the `Run` component's returned JSX, immediately **before** the final `</div>` that closes the top-level `<div class=${focus ? "runfocus" : ""}>`, add:

```javascript
    <${RunHistory} t=${t} routeId=${active ? active.route_id : routeId} />
```

(So history shows for the active run's route when running, or the picked route when idle.)

- [ ] **Step 4: `node --check` then commit.**

Run: `node --check src/sm64_events/ui/components/runview.js`
Expected: no output (clean). Do NOT commit otherwise.

```bash
git add src/sm64_events/ui/components/runview.js
git commit -m "feat(ui): run history list + progression graph (Run tab)"
```

---

## Task 3: Frontend smoke test + human-audit (the gate)

- [ ] **Step 1: Python sanity gate.** `uv run pytest -q` → PASS, unchanged count (no Python touched).

- [ ] **Step 2: Seed finished runs + start the server.** From the worktree, seed a route + several finished runs (and one aborted) via the JOURNAL so `replay` derives them (directly-inserted `runs` rows would be wiped by `start()`'s `replace_runs`):

```bash
SM64_PORT=8066 uv run python - <<'PY'
from datetime import datetime, timezone, timedelta
from sm64_events.storage.db import Database
from sm64_events.core.events import Event
db = Database("data/tracker.db")
sid = db.insert_session("2026-06-15T09:00:00Z")
steps = [{"need":1,"candidates":[{"type":"star","course":2,"star":0}]}]
rid = db.insert_route("History Demo", steps, "2026-06-15T09:00:00Z")
seq = 0
def ev(t, type, payload):
    global seq; seq += 1
    db.append_event(sid, seq, Event(type=type, frame=0, timestamp_utc=t, payload=payload))
base = datetime(2026,6,15,9,0,0,tzinfo=timezone.utc)
ev(base, "run_started", {"route_id":rid,"route_name":"History Demo",
   "route_steps":steps,"mode":"forgiving","start_offset_ms":1360})
# 3 finished runs: 120s, 90s (PB), 100s — then 1 aborted
for k,(dur) in enumerate([120,90,100]):
    t0 = base + timedelta(minutes=10*(k+1))
    ev(t0, "game_reset", {})
    ev(t0 + timedelta(seconds=dur), "star_collected", {"course_id":2,"star_id":0,"igt_frames":100})
t3 = base + timedelta(minutes=50)
ev(t3, "game_reset", {})
ev(t3 + timedelta(seconds=20), "run_ended", {})   # aborted ~20s in
db.close()
print("seeded route", rid, "with 3 finished + 1 aborted run")
PY
SM64_PORT=8066 uv run python -m sm64_events.main
```
(First snippet seeds and exits; then start the server.) Confirm `GET http://127.0.0.1:8066/api/run/history?route_id=<rid>` returns 4 runs (3 finished, 1 aborted) with the 90s one `is_pb:true`.

- [ ] **Step 3: frontend-smoke-test skill** — drive this, console clean each step:
  1. Open the app → **Run** tab → pick "History Demo".
  2. A **Run history** section appears below: a "finished only" checkbox (checked), a **PB** badge showing the 90s time, a **graph** with 3 dots (the lowest/PB dot gold), and a **table** of the 3 finished runs (the 90s row marked ★).
  3. Uncheck **finished only** → the **aborted** run appears ("aborted · reached step 0"); the graph is unchanged (graph is finished-only).
  4. Hover a graph dot → a tooltip shows its time (+ "· PB" on the gold one).
  5. No console errors throughout.

- [ ] **Step 4: Fix any issues, commit** (if needed):

```bash
git add src/sm64_events/ui/components/runview.js src/sm64_events/ui/index.html
git commit -m "fix(ui): run-history smoke-test fixes"
```

- [ ] **Step 5: human-audit skill** — summarize; have the user open the Run tab → history for a route with real runs, confirm the list + graph + PB read correctly. Wait for sign-off before merge.

---

## Task 4: Docs (Phase E + run-mode wrap-up)

**Files:** `CLAUDE.md`, `README.md`.

- [ ] **Step 1: CLAUDE.md** — extend the `ui/components/runview.js` row to mention history/graph (append to its cell):

```
… + a Run-history section (finished/aborted filter) and a small SVG progression graph (finished totals over time, gold dots = is_pb) from `GET /api/run/history`
```

- [ ] **Step 2: README** — add the run-mode section + API surface (the wrap-up doc deferred from Phase D/D-UI). Under the route/practice overview:

```markdown
- **Run mode (Run tab)** — run a whole route as a forgiving-RTA speedrun: Start
  run, press F1 to begin (clock starts at the configured offset, default 1.36s),
  per-step splits roll up retries, F1 restarts, the final step saves the run.
  Live ± vs your PB and gold splits; **Focus** mode hides deltas/colors and any
  timer is click-to-hide. A **Run history** list + progression graph track your
  finished runs and PBs over time.
```
And add to the API surface (in `docs/api.md` if present, else the README API section): `POST /api/run/start` `{route_id}`, `POST /api/run/end`, `GET /api/run`, `GET /api/run/history?route_id=`, `GET|PUT /api/run/settings` (`{start_offset_ms}`), and the WS events `run_started`/`run_ended` (journaled) + `run_progress`/`run_finished`/`run_aborted` (broadcast-only).

- [ ] **Step 3: Commit.**

```bash
git add CLAUDE.md README.md
git commit -m "docs: run history + run-mode API surface (Phase E)"
```

---

## Self-Review (completed during planning)

**Spec coverage (§6 run history):**
- Run-history list with finished/aborted filter → Task 2 (`RunHistory`, `finishedOnly`).
- Progression graph, finished totals over time, gold dots = PBs → Task 2 (`RunGraph`, `is_pb`).
- Current-PB header → Task 2 (min-total finished run, client-side).
- Fetched from `GET /api/run/history`, refetched on run events → Task 2 (`load` on `[load, t.run]`).
- Run-mode README + API surface (wrap-up) → Task 4.
- No backend change needed (Phase D's `build_run_history` already carries `display_total`/`is_pb`/`status`/`reached_step`).

**Consistency:** consumes the Phase D `GET /api/run/history` shape (`runs[].{status, total_ms, start_offset_ms, started_utc, is_pb, display_total, reached_step}`); reuses `runview.js`'s `fmtMs`; `useCallback` added to the hooks import; `RunHistory` rendered with `active ? active.route_id : routeId` (same effective-route logic as the picker fix).

**Placeholder scan:** none — full components, CSS, integration, and the seed script.

**Risk:** new htm in `RunGraph`/`RunHistory` (SVG inside template literals) — `node --check` before commit (Task 2 Step 4). The graph math (inverted y, min/max span) is the subtle part; the seed (90s = PB, gold dot lowest) makes it visually checkable in the smoke test.

---

## Feature complete

After Phase E, the route builder + run mode is end-to-end: define routes (builder, K-of-N groups, import/export, cumulative success) → focus-practice them → run them as forgiving-RTA speedruns with live splits/PB/gold/Focus → review run history + PB progression. Recommend a **`create-artifacts` wrap-up** pass (the session's live-gate findings — F1/`game_reset`, the htm-backtick class of bug, the shared-checkout merge discipline — into tests/comments/memories) once Phase E merges.
