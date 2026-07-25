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
import { Modal } from "./modal.js";
import { buildTree } from "../group.js";
import { usePaneCap } from "../viewport.js";
import { GroupedList, useOpenGroups } from "./grouplist.js";
import { EntityPicker } from "./entitymodal.js";
import { optionIcon, parseStarId, segmentLevelsOf, segmentOptions,
         starOptionsFromCatalog } from "../entities.js";

const html = htm.bind(h);
const pct = (r) => `${Math.round((r ?? 0) * 100)}%`;

// Library grouping (2026-07-24). `category` is free text on the route row, so
// the UI only decides ORDER: the two seeded groups lead, user-made groups
// follow alphabetically, and anything uncategorised sinks to the bottom.
const MAIN_CATEGORY = "Main Categories";
const STAGE_CATEGORY = "Stage RTA";
const UNCATEGORISED = "Uncategorized";
const LEAD_CATEGORIES = [MAIN_CATEGORY, STAGE_CATEGORY];
// Which groups are OPEN, not which are shut — so the default (nothing stored)
// is everything collapsed, which is what a 48-route library wants (user
// request 2026-07-24). New key name: inverting the meaning under the old one
// would have flipped every existing user's library open.
const OPEN_KEY = "sm64.routeCatsOpen";

// A category is a PATH: "Main Categories/16 Star". One free-text field still
// holds it (no migration, no second column, any depth), and the library nests
// a collapsible group per level. A route with no separator is simply
// top-level. A category containing "/" would be ambiguous — don't make one.
const CATEGORY_SEP = "/";
const splitCategory = (c) => (c || UNCATEGORISED).split(CATEGORY_SEP)
  .map((part) => part.trim()).filter(Boolean);
const categoryOf = (r) => splitCategory(r.category)[0] || UNCATEGORISED;
const subCategoryOf = (r) => splitCategory(r.category)[1] || null;

const categoryNames = (routes) =>
  [...new Set(routes.map(categoryOf))].filter((c) => c !== UNCATEGORISED).sort();

const subCategoryNames = (routes, top) =>
  [...new Set(routes.filter((r) => categoryOf(r) === top)
    .map(subCategoryOf).filter(Boolean))].sort();

function rankTop(name) {
  const lead = LEAD_CATEGORIES.indexOf(name);
  if (lead !== -1) return [0, lead];
  return name === UNCATEGORISED ? [2, 0] : [1, 0];
}

// Grouping POLICY for the route library; the engine is group.js buildTree.
// Two levels off the free-text category PATH ("Main Categories/16 Star"):
// seeded groups lead, user groups follow, Uncategorized last, names compared
// numeric-aware (buildTree's compareKeys) so "0 Star" < "1 Star" < "16 Star"
// < "70 Star" < "120 Star" rather than the lexicographic 0, 1, 120, 16, 70.
const CATEGORY_LEVELS = [
  { of: categoryOf, label: (name) => name,
    order: (name) => [...rankTop(name), name] },
  { of: subCategoryOf, label: (name) => name, order: (name) => name },
];

const NEW_OPTION = "__new__";   // can't collide with a real category name

/** Category + sub-category picker. Each level offers the existing names plus
 *  "New…", so a user can file a route under an existing group or invent one
 *  without leaving the dialog. Resolves to a path string, or "" for none. */
function CategoryModal({ routes, current, onCancel, onSave }) {
  const parts = current ? splitCategory(current) : [];
  const tops = categoryNames(routes);
  const [top, setTop] = useState(parts[0] || tops[0] || "");
  const [topNew, setTopNew] = useState("");
  const [sub, setSub] = useState(parts[1] || "");
  const [subNew, setSubNew] = useState("");

  const creatingTop = top === NEW_OPTION;
  const creatingSub = sub === NEW_OPTION;
  const resolvedTop = (creatingTop ? topNew : top).trim();
  const resolvedSub = (creatingSub ? subNew : sub).trim();
  const path = !resolvedTop ? ""
    : resolvedSub ? `${resolvedTop}${CATEGORY_SEP}${resolvedSub}` : resolvedTop;
  const subs = subCategoryNames(routes, resolvedTop);

  return html`<${Modal} title="Choose a category" onClose=${onCancel}
    footer=${html`<div class="modal-actions">
      <button onclick=${onCancel}>Cancel</button>
      <button class="primary-button" onclick=${() => onSave(path)}>Save</button>
    </div>`}>
    <label class="field">
      <span>Category</span>
      <select value=${top} onchange=${(e) => { setTop(e.target.value);
                                               setSub(""); }}>
        <option value="">— none —</option>
        ${tops.map((c) => html`<option value=${c}>${c}</option>`)}
        <option value=${NEW_OPTION}>New category…</option>
      </select>
    </label>
    ${creatingTop ? html`<label class="field">
      <span>New category name</span>
      <input value=${topNew} autofocus placeholder="e.g. Practice drills"
        oninput=${(e) => setTopNew(e.target.value)} />
    </label>` : null}

    <label class="field">
      <span>Sub-category <small class="meta">optional</small></span>
      <select value=${sub} disabled=${!resolvedTop}
        onchange=${(e) => setSub(e.target.value)}>
        <option value="">— none —</option>
        ${subs.map((c) => html`<option value=${c}>${c}</option>`)}
        <option value=${NEW_OPTION}>New sub-category…</option>
      </select>
    </label>
    ${creatingSub ? html`<label class="field">
      <span>New sub-category name</span>
      <input value=${subNew} placeholder="e.g. 16 Star"
        oninput=${(e) => setSubNew(e.target.value)} />
    </label>` : null}

    <p class="meta">Filed under <b>${path || "Uncategorized"}</b></p>
  <//>`;
}

// Star/segment picker shared by "add step" and "add option to a group". The
// star side is one grouped control (course = optgroup, star = option) and the
// segment side groups by castle region, same as the segment library beside it
// — both through EntityPicker/entities.js rather than a fourth hand-rolled
// pair of selects.
function ItemPicker({ catalog, segs, vocab, t, onPick, label }) {
  const [mode, setMode] = useState("star");
  const starGroups = starOptionsFromCatalog(catalog);
  const firstStar = starGroups[0] ? starGroups[0].options[0].id : null;
  const [star, setStar] = useState(firstStar);
  const segGroups = segmentOptions(segs, (vocab || {}).origins);
  const [segId, setSegId] = useState(segs[0] ? String(segs[0].id) : null);
  // Icon context assembled HERE, not inside the picker: the picker resolves
  // no domain art of its own. segmentLevels comes from each row's ORIGIN — the
  // canonical arm location the server already derives — NOT from start_levels,
  // which /api/segments does not carry (that field is on the session view's
  // segment_targets). Assuming it did made every segment row fall back to a
  // plain star, found by a live render check 2026-07-25.
  const iconContext = {
    courseIcons: (t && t.courseIcons) || {},
    starIconsMode: (t && t.starIcons) || "course",
    iconOverrides: ((t && t.view) || {}).icon_overrides || {},
    segmentLevels: segmentLevelsOf(segs),
  };
  const pick = () => {
    // Neither branch has anything to post before its picker resolves a first
    // value (star: catalog.course_groups missing course_groups on the
    // fallback catalog leaves `star` null; segment: segs not yet fetched
    // leaves `segId` null) — Number(null) is 0, and NaN serialises as null,
    // either of which would post an incomplete candidate the server has to
    // reject with a confusing 409 instead of the button staying inert
    // (review M2, M3).
    if (mode === "star") {
      if (star == null) return;
      const picked = parseStarId(star);
      onPick({ type: "star", course: picked.course, star: picked.star });
    } else {
      if (segId == null) return;
      onPick({ type: "segment", segment_id: Number(segId) });
    }
  };
  return html`<div class="routepick">
    <select value=${mode} onchange=${(e) => setMode(e.target.value)}>
      <option value="star">Star</option>
      <option value="segment">Segment</option>
    </select>
    ${mode === "star"
      ? html`<${EntityPicker} groups=${starGroups} value=${star} depth=${2}
          title="Choose a star"
          iconFor=${(id) => optionIcon("star", id, iconContext)}
          onChange=${(id) => setStar(id)} />`
      : segs.length === 0
        ? html`<span class="meta">no segments defined</span>`
        : html`<${EntityPicker} groups=${segGroups} value=${segId} depth=${2}
            title="Choose a segment"
            iconFor=${(id) => optionIcon("segment", id, iconContext)}
            onChange=${(id) => setSegId(id)} />`}
    <button disabled=${mode === "segment" && segs.length === 0} onclick=${pick}>
      <${Icon} name="plus" size=${15} /> ${label || "Add"}
    </button>
  </div>`;
}

// One step row. step = raw {label?, need, candidates[]}; view = resolved
// {candidates:[{display}], step_rate, cumulative, broken} (parallel by index).
function StepRow({ step, view, idx, total, catalog, segs, vocab, t, onChange, onMove, onRemove, weakest }) {
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
      <${ItemPicker} catalog=${catalog} segs=${segs} vocab=${vocab} t=${t}
          label="Add option" onPick=${addCand} />
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
  const [openGroups, toggleCategory] = useOpenGroups(OPEN_KEY);
  // Same measured cap as the segments workshop (ui/viewport.js): --pane-cap
  // inherits to the library and the workspace, so the PAGE never scrolls.
  const workshopRef = usePaneCap();
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

  // Category is a free-text PATH on the route row; the library nests groups by
  // it. The modal offers the existing names at each level, which is what keeps
  // them from fragmenting into "Main Categories"/"main categories"/"Main".
  // `pendingName` non-null = we're mid-create and the modal's Save also
  // creates the route; otherwise Save re-files the selected route.
  const [catFor, setCatFor] = useState(null);   // null | {name?, current}

  async function createRoute() {
    const name = window.prompt("New route name:");
    if (!name) return;
    setCatFor({ name, current: null });
  }
  async function saveCategory(path) {
    const pending = catFor;
    setCatFor(null);
    const category = path || null;
    try {
      if (pending.name) {
        const out = await send("POST", "/api/routes",
                               { name: pending.name, steps: [], category });
        await loadRoutes(); setSelId(out.id);
      } else {
        await send("PUT", `/api/routes/${selId}`, { category });
        await loadRoutes();
      }
    } catch (e) { setErr(String(e)); }
  }
  async function renameRoute() {
    const name = window.prompt("Rename route:", selected.name);
    if (!name || name === selected.name) return;
    try { await send("PUT", `/api/routes/${selId}`, { name }); await loadRoutes(); }
    catch (e) { setErr(String(e)); }
  }
  const recategoriseRoute = () =>
    setCatFor({ name: null, current: selected.category });
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
    ${catFor ? html`<${CategoryModal} routes=${routes}
        current=${catFor.current} onCancel=${() => setCatFor(null)}
        onSave=${saveCategory} />` : null}
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

    <div class="routes-workshop" ref=${workshopRef}>
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
          </div>` : html`<${GroupedList}
            tree=${buildTree(routes, CATEGORY_LEVELS)}
            open=${openGroups} toggle=${toggleCategory}
            renderRow=${(r) => html`<button role="listitem"
                key=${r.id}
                class=${`route-list-item ${r.id === selId ? "on" : ""}`}
                onclick=${() => setSelId(r.id)}>
              <span>
                <b>${r.name}</b>
                <small>${r.steps.length} ${r.steps.length === 1 ? "step" : "steps"}</small>
              </span>
              <${Icon} name="chevron" size=${16} />
            </button>`} />`}
        </div>
        ${selected ? html`<div class="library-actions">
          <button onclick=${renameRoute}><${Icon} name="edit" size=${15} /> Rename</button>
          <button onclick=${recategoriseRoute}>
            <${Icon} name="routes" size=${15} /> Category
          </button>
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
                    vocab=${vocab} t=${t} weakest=${i === view.weakest_step}
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
              <${ItemPicker} catalog=${catalog} segs=${segs} vocab=${vocab} t=${t}
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
