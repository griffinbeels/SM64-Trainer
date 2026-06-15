# Route Builder — Phase B (Builder UI) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the disabled "Routes" tab real — a builder where you create routes, add star/segment/group steps, reorder them, see per-step and cumulative success live, and import/export a route as copy-pastable JSON.

**Architecture:** Pure frontend over the Phase A REST API. One new Preact component (`ui/components/routes.js`) plus CSS and an `app.js` wiring change. Display names + per-step/cumulative success come from `GET /api/routes/{id}` (server-computed); raw editable steps come from `GET /api/routes`. Every structural edit `PUT`s the new `steps` and re-fetches, so the % columns stay live. No Python changes.

**Tech Stack:** Vendored Preact + htm (no build step — files are served raw; edit + refresh). Verification is the **frontend-smoke-test** skill (Chrome DevTools MCP), the project's mandatory frontend gate — there is no JS unit-test harness.

**Source spec:** `docs/superpowers/specs/2026-06-14-route-builder-design.md` (§6 UI — Routes tab builder). **Depends on Phase A** (merged to master): `/api/routes` CRUD, `/api/routes/{id}` view with `step_rate`/`cumulative`/`broken`, `/api/routes/{id}/export`, `/api/routes/import?dry_run=`.

**Scope note:** Plan **2 of 5**. This is the *builder* only. Route Practice focus mode (Phase C), Run mode (Phase D), and run history (Phase E) are separate plans. Do NOT build practice-focus or run UI here.

**Convention:** every commit message ends with the repo trailer
`Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
(omitted below for brevity). Stage explicit paths (`git add -A` is hook-blocked). Verify the branch before each commit (shared checkout). Execute in an isolated worktree branched from current master.

---

## File Structure

- **Create** `src/sm64_events/ui/components/routes.js` — the entire Routes tab: route picker + CRUD, the step editor (reorder / add / remove / group `need`), candidate chips, and the import/export panel. One file, one responsibility (mirrors how `segments.js` is a single builder file).
- **Modify** `src/sm64_events/ui/app.js` — un-stub the "Routes" tab and route it to `<Routes/>`.
- **Modify** `src/sm64_events/ui/index.html` — CSS for the route builder.
- **Modify** `README.md` — note the Routes tab in the consumer-facing surface.
- **Modify** `CLAUDE.md` — module-map row for `ui/components/routes.js`.

No new endpoints, no Python. The full pytest suite must stay green (it's unaffected) as a sanity check.

---

## Task 1: Route-builder CSS

**Files:**
- Modify: `src/sm64_events/ui/index.html` (inside the `<style>` block, after the `.segbuilder` rule ~line 55)

- [ ] **Step 1: Add the CSS**

Insert these rules into the `<style>` block in `src/sm64_events/ui/index.html` (right after the `.segbuilder { … }` line):

```css
  .routebuilder { border: 1px solid #3a4150; border-radius: 8px; padding: .6rem; margin: .6rem 0; }
  .routestep { border: 1px solid #2c3140; border-radius: 8px; padding: .4rem .55rem; margin: .4rem 0; }
  .routestep.routebroken { border-color: #4a2f2f; }
  .routestep-head { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; }
  .routestep-foot { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; margin-top: .35rem; }
  .routenum { color: #6c7686; }
  .routerate { color: #cead7f; font-size: .85em; }
  .routecum { color: #e0c36a; font-size: .85em; }
  .routecands { display: flex; flex-wrap: wrap; gap: .35rem; margin: .3rem 0; }
  .routepick { display: inline-flex; flex-wrap: wrap; gap: .3rem; align-items: center; }
  .routeadd { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; margin-top: .4rem; }
  .candx { border: none; background: transparent; color: #e0a3a3; padding: 0 0 0 .25rem; cursor: pointer; }
  .routeio { border: 1px solid #2c3140; border-radius: 8px; padding: .6rem; margin-top: .8rem; display: flex; flex-direction: column; gap: .6rem; }
  .routejson { width: 100%; min-height: 5rem; font: inherit; background: #1b1e24; color: #d8dee9; border: 1px solid #3a4150; border-radius: 4px; }
```

- [ ] **Step 2: Commit**

```bash
git add src/sm64_events/ui/index.html
git commit -m "feat(ui): route-builder CSS"
```

---

## Task 2: The Routes component + tab wiring

**Files:**
- Create: `src/sm64_events/ui/components/routes.js`
- Modify: `src/sm64_events/ui/app.js`

- [ ] **Step 1: Create `src/sm64_events/ui/components/routes.js`**

```javascript
// src/sm64_events/ui/components/routes.js — route builder + import/export.
// A route is an ordered list of steps; each step is "complete K of N"
// (a single item is need=1 with one candidate). Display names + per-step and
// cumulative success come from GET /api/routes/{id} (server-computed); the raw
// editable steps come from GET /api/routes. Every structural edit PUTs the new
// steps and re-fetches, so the % columns stay live. Mirrors segments.js
// conventions (getJSON/send, htm, one builder file).
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";

const html = htm.bind(h);
const pct = (r) => `${Math.round((r ?? 0) * 100)}%`;

// Star/segment picker shared by "add step" and "add option to a group".
function ItemPicker({ catalog, segs, onPick, label }) {
  const [mode, setMode] = useState("star");
  const [course, setCourse] = useState(catalog.courses[0] ? catalog.courses[0].id : 0);
  const [star, setStar] = useState(0);
  const [segId, setSegId] = useState(segs[0] ? segs[0].id : null);
  const courseObj = catalog.courses.find((c) => c.id === course);
  const pick = () => onPick(mode === "star"
    ? { type: "star", course, star }
    : { type: "segment", segment_id: segId });
  return html`<span class="routepick">
    <select value=${mode} onchange=${(e) => setMode(e.target.value)}>
      <option value="star">star</option>
      <option value="segment">segment</option>
    </select>
    ${mode === "star"
      ? html`<select value=${course}
            onchange=${(e) => { setCourse(Number(e.target.value)); setStar(0); }}>
          ${catalog.courses.map((c) => html`<option value=${c.id}>${c.name}</option>`)}
        </select>
        <select value=${star} onchange=${(e) => setStar(Number(e.target.value))}>
          ${(courseObj ? courseObj.stars : []).map((s, i) =>
            html`<option value=${i}>${s}</option>`)}
        </select>`
      : segs.length === 0
        ? html`<span class="meta">no segments defined</span>`
        : html`<select value=${segId ?? ""}
            onchange=${(e) => setSegId(Number(e.target.value))}>
          ${segs.map((s) => html`<option value=${s.id}>${s.name}</option>`)}
        </select>`}
    <button disabled=${mode === "segment" && segs.length === 0} onclick=${pick}>
      ${label || "add"}
    </button>
  </span>`;
}

// One step row. step = raw {label?, need, candidates[]}; view = resolved
// {candidates:[{display}], step_rate, cumulative, broken} (parallel by index).
function StepRow({ step, view, idx, total, catalog, segs, onChange, onMove, onRemove }) {
  const setNeed = (n) => onChange({ ...step, need: n });
  const addCand = (c) => onChange({ ...step, candidates: [...step.candidates, c] });
  const removeCand = (i) => {
    const candidates = step.candidates.filter((_, j) => j !== i);
    if (candidates.length === 0) { onRemove(); return; }   // last option gone -> drop the step
    const need = Math.min(step.need, candidates.length);
    onChange({ ...step, candidates, need });
  };
  const group = step.candidates.length > 1;
  return html`<div class="routestep ${view.broken ? "routebroken" : ""}">
    <div class="routestep-head">
      <span class="routenum">${idx + 1}.</span>
      ${group ? html`<span class="chip">${step.need} of ${step.candidates.length}</span>` : null}
      ${step.label ? html`<b>${step.label}</b>` : null}
      <span class="routerate">step ${pct(view.step_rate)}</span>
      <span class="routecum">cum ${pct(view.cumulative)}</span>
      <span style="flex:1"></span>
      <button disabled=${idx === 0} onclick=${() => onMove(-1)}>↑</button>
      <button disabled=${idx === total - 1} onclick=${() => onMove(1)}>↓</button>
      <button onclick=${onRemove}>✕</button>
    </div>
    <div class="routecands">
      ${step.candidates.map((c, i) => html`<span class="chip">
        ${(view.candidates[i] && view.candidates[i].display) || "?"}
        <button class="candx" title="remove option"
            onclick=${() => removeCand(i)}>×</button>
      </span>`)}
    </div>
    <div class="routestep-foot">
      ${group ? html`<label class="meta">need
        <select value=${step.need} onchange=${(e) => setNeed(Number(e.target.value))}>
          ${step.candidates.map((_, i) => html`<option value=${i + 1}>${i + 1}</option>`)}
        </select></label>` : null}
      <${ItemPicker} catalog=${catalog} segs=${segs} label="+ option" onPick=${addCand} />
    </div>
  </div>`;
}

function ImportExport({ routeId, onImported }) {
  const [exp, setExp] = useState(null);
  const [imp, setImp] = useState("");
  const [preview, setPreview] = useState(null);
  const [err, setErr] = useState(null);

  async function doExport() {
    try { setErr(null);
      setExp(JSON.stringify(await getJSON(`/api/routes/${routeId}/export`), null, 2)); }
    catch (e) { setErr(String(e)); }
  }
  function parse() { return JSON.parse(imp); }   // throws -> caught below
  async function doPreview() {
    try { setErr(null); setPreview(null);
      setPreview(await send("POST", "/api/routes/import?dry_run=true", { payload: parse() })); }
    catch (e) { setErr(String(e)); }
  }
  async function doImport() {
    try { setErr(null);
      const out = await send("POST", "/api/routes/import", { payload: parse() });
      setImp(""); setPreview(null); onImported(out.id); }
    catch (e) { setErr(String(e)); }
  }
  const copy = () => navigator.clipboard && navigator.clipboard.writeText(exp || "");

  return html`<div class="routeio">
    ${routeId != null ? html`<div>
      <button onclick=${doExport}>Export this route</button>
      ${exp != null ? html`<div>
        <textarea class="routejson" readonly>${exp}</textarea>
        <div><button onclick=${copy}>Copy</button></div>
      </div>` : null}
    </div>` : null}
    <div>
      <div class="meta">Paste a shared route to import:</div>
      <textarea class="routejson" value=${imp}
          oninput=${(e) => setImp(e.target.value)}></textarea>
      <div>
        <button onclick=${doPreview} disabled=${!imp.trim()}>Preview</button>
        <button onclick=${doImport} disabled=${!imp.trim()}>Import</button>
      </div>
      ${preview ? html`<div class="meta">Will reuse: ${preview.reused.join(", ") || "none"}
        · create: ${preview.created.join(", ") || "none"}</div>` : null}
    </div>
    ${err ? html`<div class="badx">${err}</div>` : null}
  </div>`;
}

export function Routes({ t }) {
  const [routes, setRoutes] = useState(null);
  const [selId, setSelId] = useState(null);
  const [view, setView] = useState(null);
  const [segs, setSegs] = useState([]);
  const [err, setErr] = useState(null);
  const catalog = (t.view && t.view.catalog) || { courses: [] };

  const loadRoutes = async () => { const rs = await getJSON("/api/routes"); setRoutes(rs); return rs; };
  const loadView = async (id) =>
    setView(id == null ? null : await getJSON(`/api/routes/${id}`).catch(() => null));
  useEffect(() => { loadRoutes(); getJSON("/api/segments").then(setSegs); }, []);
  // re-fetch the resolved view whenever the selection OR the raw routes change
  // (a saveSteps PUT reloads routes -> this refreshes the % columns).
  useEffect(() => { loadView(selId); }, [selId, routes]);

  if (routes === null) return html`<div class="meta">loading…</div>`;
  const selected = routes.find((r) => r.id === selId) || null;

  async function saveSteps(steps) {
    try { setErr(null); await send("PUT", `/api/routes/${selId}`, { steps }); await loadRoutes(); }
    catch (e) { setErr(String(e)); }
  }
  const editStep = (i, step) => saveSteps(selected.steps.map((s, j) => (j === i ? step : s)));
  const moveStep = (i, dir) => {
    const steps = selected.steps.slice();
    const j = i + dir;
    [steps[i], steps[j]] = [steps[j], steps[i]];
    saveSteps(steps);
  };
  const removeStep = (i) => saveSteps(selected.steps.filter((_, j) => j !== i));
  const addStep = (c) => saveSteps([...selected.steps, { need: 1, candidates: [c] }]);

  async function createRoute() {
    const name = window.prompt("New route name:");
    if (!name) return;
    try { const out = await send("POST", "/api/routes", { name, steps: [] });
      await loadRoutes(); setSelId(out.id); }
    catch (e) { setErr(String(e)); }
  }
  async function renameRoute() {
    const name = window.prompt("Rename route:", selected.name);
    if (!name || name === selected.name) return;
    try { await send("PUT", `/api/routes/${selId}`, { name }); await loadRoutes(); }
    catch (e) { setErr(String(e)); }
  }
  async function deleteRoute() {
    if (!window.confirm(`Delete route "${selected.name}"?`)) return;
    try { await send("DELETE", `/api/routes/${selId}`); setSelId(null); await loadRoutes(); }
    catch (e) { setErr(String(e)); }
  }

  return html`<div>
    <div class="bar">
      <select value=${selId ?? ""}
          onchange=${(e) => setSelId(e.target.value ? Number(e.target.value) : null)}>
        <option value="">— pick a route —</option>
        ${routes.map((r) => html`<option value=${r.id}>${r.name}</option>`)}
      </select>
      <button onclick=${createRoute}>+ New route</button>
      ${selected ? html`<button onclick=${renameRoute}>Rename</button>` : null}
      ${selected ? html`<button onclick=${deleteRoute}>Delete</button>` : null}
    </div>
    ${err ? html`<div class="badx">${err}</div>` : null}
    ${selected && view ? html`<div class="routebuilder">
      ${view.steps.length === 0
        ? html`<div class="meta">No steps yet — add one below.</div>` : null}
      ${view.steps.map((vs, i) => html`<${StepRow} key=${i}
          step=${selected.steps[i]} view=${vs} idx=${i} total=${view.steps.length}
          catalog=${catalog} segs=${segs}
          onChange=${(s) => editStep(i, s)} onMove=${(dir) => moveStep(i, dir)}
          onRemove=${() => removeStep(i)} />`)}
      <div class="routeadd">
        <span class="meta">Add step:</span>
        <${ItemPicker} catalog=${catalog} segs=${segs} label="+ add step" onPick=${addStep} />
      </div>
    </div>` : null}
    <${ImportExport} routeId=${selId}
        onImported=${(id) => loadRoutes().then(() => setSelId(id))} />
  </div>`;
}
```

- [ ] **Step 2: Wire the tab in `src/sm64_events/ui/app.js`**

Replace the entire file with:

```javascript
// src/sm64_events/ui/app.js — root: header + tabs
import { h, render } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { useTracker } from "./store.js";
import { Header } from "./components/header.js";
import { Practice } from "./components/practice.js";
import { Feed } from "./components/feed.js";
import { Segments } from "./components/segments.js";
import { Routes } from "./components/routes.js";

const html = htm.bind(h);
const TABS = ["Practice", "Segments", "Routes", "Live feed"];

function App() {
  const t = useTracker();
  const [tab, setTab] = useState("Practice");
  return html`
    <h1>SM64 Practice Tracker</h1>
    <${Header} t=${t} />
    <div class="tabs">
      ${TABS.map((name) => html`
        <div class="tab ${tab === name ? "on" : ""}"
             onclick=${() => setTab(name)}>${name}</div>`)}
    </div>
    <div class="pane">
      ${tab === "Practice" ? html`<${Practice} t=${t} />`
        : tab === "Segments" ? html`<${Segments} t=${t} />`
        : tab === "Routes" ? html`<${Routes} t=${t} />`
        : html`<${Feed} t=${t} />`}
    </div>`;
}

render(html`<${App} />`, document.getElementById("app"));
```

- [ ] **Step 3: Commit**

```bash
git add src/sm64_events/ui/components/routes.js src/sm64_events/ui/app.js
git commit -m "feat(ui): Routes tab — route builder + import/export"
```

---

## Task 3: Frontend smoke test (the gate)

**Files:** none changed unless bugs are found (then fix `routes.js` / CSS and amend the smoke-test findings into a follow-up commit).

There is no JS unit-test harness; the **frontend-smoke-test** skill is the mandatory gate. Use the **manage-server** skill (or `scripts/start_server.cmd`) to run the server on port 8064, then drive the page in Chrome DevTools MCP.

- [ ] **Step 1: Python sanity gate (nothing should have changed server-side)**

Run: `uv run pytest -q`
Expected: PASS (same count as before Phase B — Phase B is JS/HTML only).

- [ ] **Step 2: Start the server**

Invoke the **manage-server** skill to start (or restart) the server. Confirm `GET /health` is OK and the page loads at `http://localhost:8064/`.

- [ ] **Step 3: Run the frontend-smoke-test skill on the Routes tab**

Invoke **frontend-smoke-test**. Drive this exact script in the browser, checking the console stays clean (no errors) at each step:
1. Click the **Routes** tab — it must open (no longer greyed out).
2. **+ New route** → name it "Smoke Route" → it appears selected in the picker.
3. **Add step** → mode "star", pick a course + star → **+ add step**. A step row appears showing the star name and `step %` / `cum %` (0% with no history — expected).
4. **Add step** → mode "segment", pick a segment (e.g. "LBLJ") → a second step row appears.
5. On step 1, **+ option** → add a second star → the row now shows a `1 of 2` chip and a `need` selector; set need to 2 → the chip reads `2 of 2`.
6. Use **↑ / ↓** to reorder the two steps → order persists after the implied refetch (the numbers and % follow).
7. Remove an option's **×**; remove a step's **✕** → both update.
8. **Export this route** → JSON appears in the textarea with `"kind":"sm64-route"`; **Copy** works (no console error).
9. Paste that JSON into the import textarea → **Preview** shows "Will reuse … / create …"; **Import** creates a new route and selects it.
10. Reload the page → the routes still exist (persisted) and reopen correctly.

- [ ] **Step 4: Fix any issues found, then commit**

If the smoke test surfaces render/console bugs (htm quirks, undefined reads, etc.), fix them in `routes.js` (or CSS) and re-run the smoke script until clean.

```bash
git add src/sm64_events/ui/components/routes.js src/sm64_events/ui/index.html
git commit -m "fix(ui): route builder smoke-test fixes"
```

(If no fixes were needed, skip this commit and note "smoke test clean, no fixes".)

- [ ] **Step 5: Human audit**

Invoke the **human-audit** skill: summarize what changed, point the user at the Routes tab, and wait for confirmation before wrapping up (frontend change → human verifies live behavior, per the project's workflow).

---

## Task 4: Docs

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: README — consumer-facing surface**

In `README.md`, under the UI/feature overview, add a short line for the Routes tab:

```markdown
- **Routes tab** — build an ordered route of stars/segments (with "complete K
  of N" group steps), see per-step and cumulative success rates, and
  import/export a route as copy-pastable JSON to share. (Practice-focus and the
  full-game run timer arrive in later phases.)
```

- [ ] **Step 2: CLAUDE.md — module-map row**

Add a row to the module-map table in `CLAUDE.md`, near the other `ui/components` rows:

```
| Route builder UI | `ui/components/routes.js` — Routes tab: route picker + CRUD, step editor (reorder / add / remove / `need` for K-of-N groups), candidate chips, import/export panel. Display names + per-step/cumulative % from `GET /api/routes/{id}`; raw steps from `GET /api/routes`; every edit PUTs steps and re-fetches |
```

- [ ] **Step 3: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: Routes tab in README + module map (Phase B)"
```

---

## Self-Review (completed during planning)

**Spec coverage (Phase B / §6 builder):**
- Routes tab un-stubbed → Task 2.
- Route picker + create/rename/delete → Task 2.
- Add star (catalog) / add segment (segment list) / group with `need` → Task 2 (`ItemPicker`, `StepRow`).
- Reorder (↑/↓ — drag deferred; up/down needs no JS dep and is reliable) → Task 2.
- Two % columns (step + cumulative) from the route view → Task 2 (`StepRow` reads `view.step_rate`/`view.cumulative`).
- Import/Export with dry-run preview → Task 2 (`ImportExport`).
- Broken (deleted-segment) step styling → Task 2 (`routebroken`).
- Verification: frontend-smoke-test + human-audit → Task 3.
- Deferred (correctly out of Phase B): practice focus, run mode, run history, cross-client `routes_changed` live sync (the component reloads after its own mutations; multi-client sync lands with the store changes in Phase C/D).

**Consistency:** uses the same `getJSON`/`send` client and htm/`html` binding as `segments.js`; CSS class names introduced in Task 1 are all consumed in Task 2 (`routestep`, `routestep-head/foot`, `routenum`, `routerate`, `routecum`, `routecands`, `routepick`, `routebuilder`, `routeadd`, `candx`, `routeio`, `routejson`, `routebroken`); `step`/`view` are parallel-by-index everywhere; the API shapes (`steps`, `{step_rate, cumulative, broken, candidates[].display}`, import `{reused, created, id}`) match Phase A exactly.

**Placeholder scan:** none — full component code, exact CSS, exact app.js, explicit smoke script.

**Known design choice:** ↑/↓ reordering instead of drag-and-drop (the spec mockup showed a drag handle). Rationale: drag needs a library or non-trivial pointer code (vendored Preact only, no deps); ↑/↓ is dependency-free, accessible, and equivalent in function. Flag to the user during human-audit if drag is wanted — it can be a later polish.

---

## Subsequent phases (separate plan files)

- **Phase C** — Route Practice focus mode (client-side filter/order over the route + session views; current/next; click-to-retry) + `routes_changed`/active-route in `store.js`.
- **Phase D** — Run mode: `runs` table (migration v8), `RunTracker` in `tracking/runs.py` wired into `projection.replay()`, run lifecycle (F1 start + 1.36s offset, forgiving splits, abort/restart), PB/gold, run view, Focus mode, click-to-hide.
- **Phase E** — Run history (finished/aborted filter) + progression graph.
