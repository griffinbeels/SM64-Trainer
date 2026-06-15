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
      ${selected.steps.length === 0
        ? html`<div class="meta">No steps yet — add one below.</div>` : null}
      ${selected.steps.map((step, i) => {
        // Render from the RAW steps (structural source of truth); pull the
        // resolved %s by index with a safe fallback. saveSteps reloads routes
        // and the view separately, so on a removal they are transiently
        // different lengths — indexing the view here (not mapping it) avoids a
        // step=undefined render crash (live smoke 2026-06-14).
        const vs = (view.steps && view.steps[i])
          || { candidates: [], step_rate: 0, cumulative: 0, broken: false };
        return html`<${StepRow} key=${i} step=${step} view=${vs} idx=${i}
            total=${selected.steps.length} catalog=${catalog} segs=${segs}
            onChange=${(s) => editStep(i, s)} onMove=${(dir) => moveStep(i, dir)}
            onRemove=${() => removeStep(i)} />`;
      })}
      <div class="routeadd">
        <span class="meta">Add step:</span>
        <${ItemPicker} catalog=${catalog} segs=${segs} label="+ add step" onPick=${addStep} />
      </div>
    </div>` : null}
    <${ImportExport} routeId=${selId}
        onImported=${(id) => loadRoutes().then(() => setSelId(id))} />
  </div>`;
}
