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
import { ClauseRow } from "./segments.js";
import { Medal } from "./ranks.js";
import { Icon } from "./icons.js";
import { PageState } from "./states.js";

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
  return html`<div class="routepick">
    <select value=${mode} onchange=${(e) => setMode(e.target.value)}>
      <option value="star">Star</option>
      <option value="segment">Segment</option>
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
      <${Icon} name="plus" size=${15} /> ${label || "Add"}
    </button>
  </div>`;
}

// One step row. step = raw {label?, need, candidates[]}; view = resolved
// {candidates:[{display}], step_rate, cumulative, broken} (parallel by index).
function StepRow({ step, view, idx, total, catalog, segs, onChange, onMove, onRemove, weakest }) {
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
      <span class="routenum">${idx + 1}</span>
      ${group ? html`<span class="chip">${step.need} of ${step.candidates.length}</span>` : null}
      ${step.label ? html`<b>${step.label}</b>` : null}
      ${view.rank ? html`<${Medal} rank=${view.rank} size=${16} />` : null}
      ${weakest ? html`<span class="weakflag">weakest</span>` : null}
      <span style="flex:1"></span>
      <span class="route-stat"><small>Step</small><b>${pct(view.step_rate)}</b></span>
      <span class="route-stat cumulative"><small>Through here</small><b>${pct(view.cumulative)}</b></span>
      <div class="routestep-actions">
        <button class="icon-button" disabled=${idx === 0} title="Move step up"
            aria-label="Move step up" onclick=${() => onMove(-1)}>
          <${Icon} name="arrowUp" size=${15} />
        </button>
        <button class="icon-button" disabled=${idx === total - 1} title="Move step down"
            aria-label="Move step down" onclick=${() => onMove(1)}>
          <${Icon} name="arrowDown" size=${15} />
        </button>
        <button class="icon-button danger-icon" title="Remove step"
            aria-label="Remove step" onclick=${onRemove}>
          <${Icon} name="trash" size=${15} />
        </button>
      </div>
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
      <${ItemPicker} catalog=${catalog} segs=${segs} label="Add option" onPick=${addCand} />
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
      <button onclick=${doExport}><${Icon} name="download" size=${16} /> Export this route</button>
      ${exp != null ? html`<div>
        <textarea class="routejson" readonly>${exp}</textarea>
        <div><button onclick=${copy}>Copy JSON</button></div>
      </div>` : null}
    </div>` : null}
    <div>
      <div class="meta">Paste a shared route to import:</div>
      <textarea class="routejson" value=${imp}
          oninput=${(e) => setImp(e.target.value)}></textarea>
      <div>
        <button onclick=${doPreview} disabled=${!imp.trim()}>Preview</button>
        <button onclick=${doImport} disabled=${!imp.trim()}>
          <${Icon} name="upload" size=${16} /> Import
        </button>
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
  const [startCond, setStartCond] = useState(null);   // local start-condition edit buffer
  const [segs, setSegs] = useState([]);
  const [vocab, setVocab] = useState(null);
  const [err, setErr] = useState(null);
  const catalog = (t.view && t.view.catalog) || { courses: [] };

  const loadRoutes = async () => { const rs = await getJSON("/api/routes"); setRoutes(rs); return rs; };
  const loadView = async (id) =>
    setView(id == null ? null : await getJSON(`/api/routes/${id}`).catch(() => null));
  useEffect(() => {
    loadRoutes();
    getJSON("/api/segments").then(setSegs);
    getJSON("/api/segments/vocab").then(setVocab).catch(() => {});
  }, []);
  // re-fetch the resolved view whenever the selection OR the raw routes change
  // (a saveSteps PUT reloads routes -> this refreshes the % columns).
  // Known limitation (accepted, final review 2026-07-23): this tab has no WS
  // subscription, so step medals / avg_rank go stale on rank_mode_changed or
  // rank_standards_changed until reselect/edit. The live surface is the
  // Practice tab's RouteFocus, which re-fetches on every t.view update.
  useEffect(() => { loadView(selId); }, [selId, routes]);
  // mirror the loaded route's start_condition into a local edit buffer, so a
  // half-typed parameterized trigger (type chosen, required param not yet) can
  // render its param inputs WITHOUT PUTting an incomplete clause (which 409s).
  useEffect(() => {
    setStartCond(view ? (view.start_condition || { type: "reset_game" }) : null);
  }, [view]);

  if (routes === null) return html`<${PageState}
      kind=${t.connected ? "loading" : "offline"}
      title="Preparing the route workshop" />`;
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
  function clauseComplete(c) {
    const spec = vocab && vocab.triggers.find((tt) => tt.key === c.type);
    if (!spec) return false;
    return Object.entries(spec.params).every(([n, m]) => !m.required || c[n] != null);
  }
  async function saveStartCondition(c) {
    setStartCond(c);                  // local first: lets param inputs appear for an incomplete clause
    if (!clauseComplete(c)) return;   // don't PUT until required params are filled (was 409-ing on type change)
    try { setErr(null); await send("PUT", `/api/routes/${selId}`, { start_condition: c }); await loadRoutes(); }
    catch (e) { setErr(String(e)); }
  }

  return html`<div class="workshop-page routes-page">
    <header class="practice-card workshop-hero">
      <div class="workshop-title">
        <span class="workshop-title-icon"><${Icon} name="routes" size=${22} /></span>
        <div>
          <span class="eyebrow">Build</span>
          <h2>Routes</h2>
          <p>Arrange stars and segments into a clear practice plan or complete run.</p>
        </div>
      </div>
      <button class="primary-button" onclick=${createRoute}>
        <${Icon} name="plus" size=${17} /> New route
      </button>
    </header>

    ${err ? html`<div class="practice-card workshop-error badx">${err}</div>` : null}

    <div class="routes-workshop">
      <aside class="practice-card workshop-card route-library">
        <div class="workshop-card-heading">
          <div>
            <span class="eyebrow">Library</span>
            <h3>Your routes</h3>
          </div>
          <span class="count-badge">${routes.length}</span>
        </div>
        <div class="route-list" role="list">
          ${routes.length === 0 ? html`<div class="workshop-empty compact">
            No routes yet. Build one from stars, segments, or groups.
          </div>` : routes.map((r) => html`<button role="listitem"
              class=${`route-list-item ${r.id === selId ? "on" : ""}`}
              onclick=${() => setSelId(r.id)}>
            <span>
              <b>${r.name}</b>
              <small>${r.steps.length} ${r.steps.length === 1 ? "step" : "steps"}</small>
            </span>
            <${Icon} name="chevron" size=${16} />
          </button>`)}
        </div>
        ${selected ? html`<div class="library-actions">
          <button onclick=${renameRoute}><${Icon} name="edit" size=${15} /> Rename</button>
          <button class="danger-text" onclick=${deleteRoute}>
            <${Icon} name="trash" size=${15} /> Delete
          </button>
        </div>` : null}
      </aside>

      <main class="route-workspace">
        ${selected && view ? html`
          <section class="practice-card workshop-card route-setup-card">
            <div class="workshop-card-heading route-identity">
              <div>
                <span class="eyebrow">Route setup</span>
                <h2>${selected.name}</h2>
              </div>
              ${view.avg_rank ? html`<div class="routeavg">
                <span class="meta">Average</span>
                <${Medal} rank=${view.avg_rank.tier} size=${18} />
                <b>${view.avg_rank.tier}</b>
                <span class="meta">${view.avg_rank.score}/9</span>
              </div>` : null}
            </div>
            ${vocab ? html`<div class="routestart">
              <div class="setting-copy">
                <b>Run starts when</b>
                <span>Choose the event that begins the route clock.</span>
              </div>
              <${ClauseRow} clause=${startCond || view.start_condition || { type: "reset_game" }}
                types=${vocab.triggers} vocab=${vocab}
                onChange=${(c) => saveStartCondition(c)} />
            </div>` : null}
          </section>

          <section class="practice-card workshop-card route-plan-card">
            <div class="workshop-card-heading">
              <div>
                <span class="eyebrow">Practice plan</span>
                <h3>Route steps</h3>
              </div>
              <span class="count-badge">${selected.steps.length}</span>
            </div>
            <div class="route-step-list">
              ${selected.steps.length === 0
                ? html`<div class="workshop-empty compact">
                    Start with a star or segment. You can add alternatives to any step later.
                  </div>` : null}
              ${selected.steps.map((step, i) => {
                // Render from the RAW steps (structural source of truth); pull the
                // resolved %s by index with a safe fallback. saveSteps reloads routes
                // and the view separately, so on a removal they are transiently
                // different lengths — indexing the view here avoids a render crash.
                const vs = (view.steps && view.steps[i])
                  || { candidates: [], step_rate: 0, cumulative: 0, broken: false };
                return html`<${StepRow} key=${i} step=${step} view=${vs} idx=${i}
                    total=${selected.steps.length} catalog=${catalog} segs=${segs}
                    weakest=${i === view.weakest_step}
                    onChange=${(s) => editStep(i, s)}
                    onMove=${(dir) => moveStep(i, dir)}
                    onRemove=${() => removeStep(i)} />`;
              })}
            </div>
            <div class="routeadd">
              <div class="setting-copy">
                <b>Add a step</b>
                <span>Pick one star or segment to append to the route.</span>
              </div>
              <${ItemPicker} catalog=${catalog} segs=${segs}
                  label="Add step" onPick=${addStep} />
            </div>
          </section>
        ` : html`<section class="practice-card workshop-card route-empty-card">
          <div class="workshop-empty">
            <span class="workshop-empty-icon"><${Icon} name="routes" size=${34} /></span>
            <h3>Choose a route to begin</h3>
            <p>Every step, option, and success rate will be visible in one ordered plan.</p>
            <button class="primary-button" onclick=${createRoute}>
              <${Icon} name="plus" size=${16} /> Create a route
            </button>
          </div>
        </section>`}

        <details class="practice-card workshop-card route-share-card">
          <summary>
            <span><${Icon} name="download" size=${17} /></span>
            <span><b>Import or export</b><small>Share route JSON or bring in someone else's plan.</small></span>
            <${Icon} name="chevron" size=${16} className="detail-chevron" />
          </summary>
          <${ImportExport} routeId=${selId}
              onImported=${(id) => loadRoutes().then(() => setSelId(id))} />
        </details>
      </main>
    </div>
  </div>`;
}
