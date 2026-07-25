// src/sm64_events/ui/components/segments.js — definition list + builder.
// The form is 100% vocab-driven: GET /api/segments/vocab supplies trigger
// types, param schemas, sentence templates, and level/area/course/star
// enums; adding a trigger type in tracking/segments.py appears here with
// zero UI changes.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { Icon } from "./icons.js";
import { IconPicker, iconSrcFromStem } from "./iconpicker.js";
import { PageState } from "./states.js";
import { buildTree } from "../group.js";
import { GroupedList, useOpenGroups } from "./grouplist.js";

const html = htm.bind(h);

// --- world-topology option filtering (vocab.connections + param `flow`) ----
// level_enter/level_exit params carry a `flow` annotation ({role, peer,
// peer_subarea} — tracking/segments.py): once the OTHER side of the move is
// picked, this side's dropdown only offers world-possible options
// (addresses.WORLD_EDGES_*). "dest" params filter by the source's
// SUCCESSORS, "source" params by the destination's PREDECESSORS. UI-only —
// a stored value the topology disagrees with stays selectable (ParamInput
// keeps the current value in the list) so legacy defs never blank out.

const nodeKey = ([level, area]) => (area == null ? String(level) : `${level}:${area}`);

function nodesFor(level, subarea, conn) {
  // A level with no subarea picked means "any of its subarea nodes" (only
  // Castle Inside has them — derived from the map itself, not hardcoded).
  if (subarea != null) return [`${level}:${subarea}`];
  const subKeys = Object.keys(conn).filter((k) => k.startsWith(`${level}:`));
  return subKeys.length ? subKeys : [String(level)];
}

export function allowedIds(schema, clause, conn) {
  // Set of permitted ids for a flow-annotated level/subarea param, or null
  // when unconstrained (no flow, no topology, peer side unpicked).
  const flow = schema.flow;
  if (!flow || !conn) return null;
  const peerLevel = clause[flow.peer];
  if (peerLevel == null) return null;
  const peerNodes = nodesFor(peerLevel, clause[flow.peer_subarea], conn);
  let pairs; // [level, area|null] nodes reachable on THIS param's side
  if (flow.role === "dest") {
    pairs = peerNodes.flatMap((k) => conn[k] || []);
  } else {
    const peerSet = new Set(peerNodes);
    pairs = Object.entries(conn)
      .filter(([, dests]) => dests.some((d) => peerSet.has(nodeKey(d))))
      .map(([k]) => (k.includes(":")
        ? k.split(":").map(Number) : [Number(k), null]));
  }
  if (schema.kind === "level")
    // a level_changed edge never stays inside one level, so the peer's own
    // level is excluded (no "Castle Inside -> Castle Inside")
    return new Set(pairs.map(([lvl]) => lvl).filter((lvl) => lvl !== peerLevel));
  // subarea param: its owning level is the only_when controller's value
  const ownLevel = clause[schema.only_when.param];
  return new Set(pairs.filter(([lvl, area]) => lvl === ownLevel && area != null)
    .map(([, area]) => area));
}

export function ParamInput({ schema, name, value, vocab, clause, onChange }) {
  // "" MUST become null, never Number("")===0 — 0 is a real area/level id,
  // so a bare Number() silently scoped cleared optional params to area 0.
  const numOrNull = (s) => (s === "" ? null : Number(s));
  const dropdown = (entries, anyLabel, pickLabel) => html`<select
      value=${value ?? ""} onchange=${(e) => onChange(numOrNull(e.target.value))}>
    <option value="">${schema.required ? pickLabel : anyLabel}</option>
    ${entries.map(([id, n]) => html`<option value=${id}>${n}</option>`)}
  </select>`;
  // world-topology filter (see allowedIds above); the CURRENT value always
  // stays listed so an out-of-topology stored def renders and saves intact
  const allowed = allowedIds(schema, clause, vocab.connections);
  const permitted = ([id]) => !allowed || allowed.has(Number(id))
    || Number(id) === value;
  if (schema.kind === "level") {
    // schema.enum restricts the choices (area_enter offers only the castle
    // hubs); absent enum = the full level list.
    const entries = Object.entries(vocab.levels).filter(
      ([id]) => (!schema.enum || schema.enum.includes(Number(id)))
        && permitted([id]));
    return dropdown(entries, "(any level)", "— pick level —");
  }
  if (schema.kind === "subarea")
    // Castle interior areas (lobby/upstairs/basement). Always optional — the
    // empty option is the explicit "Any" (matches any interior area). Shown
    // only when the companion level is Castle Inside (ClauseRow only_when).
    return dropdown(Object.entries(vocab.castle_areas).filter(permitted),
                    "Any", "— pick subarea —");
  if (schema.kind === "course")
    return dropdown(Object.entries(vocab.courses), "(any course)", "— pick course —");
  if (schema.kind === "star") {
    // dependent on the sibling course param: no course (or "any course")
    // implies any star, so the selector is disabled until a course is picked
    const names = vocab.stars[String(clause.course)] || [];
    return html`<select value=${value ?? ""} disabled=${clause.course == null}
        onchange=${(e) => onChange(numOrNull(e.target.value))}>
      <option value="">${schema.required ? "— pick star —" : "(any star)"}</option>
      ${names.map((n, i) => html`<option value=${i}>${n}</option>`)}
    </select>`;
  }
  if (schema.kind === "seconds") {
    // Stored as FRAMES (30 fps int — the project's primary clock); edited as
    // decimal seconds. "" stays null so a cleared input doesn't become 0
    // (0 is meaningful: "no minimum").
    // oninput, NOT onchange: the app re-renders on ~1s poll ticks, and a
    // controlled input's uncommitted keystrokes are wiped by every re-render
    // (onchange only commits on blur/Enter — live smoke test 2026-07-23 lost
    // typed values ~1s in). Any typed input in this UI must commit per
    // keystroke or hold local state; <select> commits instantly, so onchange
    // stays fine there.
    return html`<input type="number" min="0" step="0.1" style="width:5rem"
        value=${value == null ? "" : value / 30}
        placeholder="seconds"
        oninput=${(e) => onChange(e.target.value === ""
          ? null : Math.round(Number(e.target.value) * 30))} />`;
  }
  // oninput for the same reason as the seconds branch above (poll re-render
  // wipes uncommitted onchange values — this bit star_count guards too).
  return html`<input type="number" style="width:5rem" value=${value ?? ""}
      placeholder=${name}
      oninput=${(e) => onChange(numOrNull(e.target.value))} />`;
}

export function ClauseRow({ clause, types, vocab, tint, onChange, onRemove }) {
  const spec = types.find((t) => t.key === clause.type) || types[0];
  // A param with only_when shows only while its controlling param equals the
  // gate value (subarea selectors appear only for Castle Inside).
  const visible = (pname) => {
    const ow = spec.params[pname]?.only_when;
    return !ow || clause[ow.param] === ow.equals;
  };
  const setParam = (pname, v) => {
    const next = { ...clause, [pname]: v };
    // a star id is meaningless outside its course — clear it on course change
    if (pname === "course" && "star" in spec.params) next.star = null;
    // Consistency sweep, two passes (clearing a level param can invalidate
    // its subarea): (a) a hidden param holds nothing — a stale "Basement"
    // can't cling to "Castle Grounds"; (b) a sibling value the world
    // topology now rules out is cleared (picking "to LLL" drops a "from WF"
    // that can no longer reach it). The just-typed value itself is exempt
    // from (b) — the user's pick wins and the siblings adjust around it.
    for (let pass = 0; pass < 2; pass++)
      for (const [p, meta] of Object.entries(spec.params)) {
        if (meta.only_when
            && next[meta.only_when.param] !== meta.only_when.equals)
          next[p] = null;
        if (p !== pname && next[p] != null) {
          const allowed = allowedIds(meta, next, vocab.connections);
          if (allowed && !allowed.has(next[p])) next[p] = null;
        }
      }
    onChange(next);
  };
  const param = (pname) => html`<${ParamInput} schema=${spec.params[pname]}
      name=${pname} vocab=${vocab} clause=${clause} value=${clause[pname]}
      onChange=${(v) => setParam(pname, v)} />`;
  // "{to} coming from {from}" → inputs interleaved with muted words.
  // Params a template forgets to mention render appended — the registry
  // test makes that unreachable; this keeps a bad vocab usable, not blank.
  // Hidden (only_when unmet) params render nothing but stay "mentioned" so
  // they don't reappear in the extras tail.
  const mentioned = new Set();
  const toks = (spec.template || "").split(/(\{\w+\})/);
  const rendered = toks.map((tok, i) => {
    const m = /^\{(\w+)\}$/.exec(tok);
    if (m && spec.params[m[1]]) {
      mentioned.add(m[1]);
      return visible(m[1]) ? param(m[1]) : null;
    }
    const word = tok.trim();
    if (!word) return null;
    // words introducing a hidden param hide with it — "coming from" must
    // not dangle when the castle-only 'from' selector is hidden
    const next = /^\{(\w+)\}$/.exec(toks[i + 1] || "");
    if (next && spec.params[next[1]] && !visible(next[1])) return null;
    return html`<span class="segword">${word}</span>`;
  });
  const extras = Object.keys(spec.params).filter(
    (p) => !mentioned.has(p) && visible(p));
  return html`<div class="segclause tint${tint ?? 0}">
    <select value=${clause.type}
        onchange=${(e) => onChange({ type: e.target.value })}>
      ${types.map((t) => html`<option value=${t.key}>${t.label}</option>`)}
    </select>
    ${rendered}
    ${extras.map(param)}
    ${onRemove ? html`<button class="icon-button clause-remove" title="Remove condition"
        aria-label="Remove condition" onclick=${onRemove}>
      <${Icon} name="close" size=${16} />
    </button>` : null}
  </div>`;
}

// The fields the Builder's save sends — must stay a SUBSET of the server's
// SegmentBody/SegmentPatch fields (cross-checked by tests/
// test_segments_editor_ui.py against the pydantic model).
const SAVE_FIELDS = ["name", "enabled", "start_triggers", "end_triggers",
                     "guards"];

function Builder({ vocab, initial, onSaved, onCancel, apiRef, t, load }) {
  const blank = { name: "", enabled: true,
    start_triggers: [{ type: "level_enter" }],
    end_triggers: [{ type: "level_enter" }], guards: [] };
  const [d, setD] = useState(initial || blank);
  // Icon override (existing segments only — the override is keyed by id, so
  // a not-yet-saved segment has nowhere to hang one). Read live from the
  // session view so a pick from the banner's ✎ shows here too.
  const [pickingIcon, setPickingIcon] = useState(false);
  const iconOverride = initial && initial.id != null
    ? ((((t || {}).view || {}).icon_overrides || {})[`segment:${initial.id}`]
       || null)
    : null;
  // Origin override (existing segments only — keyed by id, like the icon).
  // The library files a segment by where its rules say it starts; when that
  // reads wrong, this pins it. "Auto" always NAMES the detected place, so a
  // misclassification is visible to the person who has to fix it.
  const detected = (initial && initial.origin) || null;
  const [origin, setOrigin] = useState(
    detected && detected.source === "override" ? detected.key : "");
  const [err, setErr] = useState(null);
  const edit = (k, i, clause) => setD({ ...d,
    [k]: d[k].map((c, j) => (j === i ? clause : c)) });
  const add = (k, types) => setD({ ...d, [k]: [...d[k], { type: types[0].key }] });
  const drop = (k, i) => setD({ ...d, [k]: d[k].filter((_, j) => j !== i) });

  async function save() {
    try {
      setErr(null);
      // ALLOWLIST of what this editor edits — never spread the GET row back:
      // rows are raw db rows and grow server-only columns over time
      // (id, created_utc, then seed_key/seed_dirty in migration v11), which
      // the strict SegmentPatch (extra="forbid") rejects — a denylist here
      // 422'd every save of a seeded segment (regression 2026-07-24, pinned
      // by tests/test_segments_editor_ui.py). waypoints/category are
      // deliberately absent: this editor doesn't author them, and an
      // omitted PATCH field stays untouched server-side.
      const body = Object.fromEntries(
        SAVE_FIELDS.map((field) => [field, d[field]]));
      if (initial && initial.id != null) {
        await send("PUT", `/api/segments/${initial.id}`, body);
      } else {
        await send("POST", "/api/segments", body);
      }
      onSaved();
      return true;
    } catch (e) { setErr(String(e)); return false; }
  }

  async function saveOrigin(nextKey) {
    setOrigin(nextKey);
    try {
      await send("POST", `/api/segments/${initial.id}/origin`,
                 { origin: nextKey || null });
      // Like toggle/remove below: the library's grouping reads `defs`, which
      // only `load()` refreshes — `t.refresh()` alone updates the session
      // view (no `origin` field), so the row silently stayed in its old
      // group until the tab was re-entered (review I2).
      load(); t.refresh();
    } catch (e) { setErr(String(e)); }
  }

  // Expose a save handle + live dirty flag so the parent can offer "save your
  // changes?" when the user clicks edit on a DIFFERENT segment (Segments
  // tryEdit). dirty = the form differs from what we opened with (reverting an
  // edit back clears it). Reassigned each render so the parent reads current
  // state at click time.
  if (apiRef) apiRef.current = {
    save, dirty: JSON.stringify(d) !== JSON.stringify(initial || blank),
  };

  // One bordered group per side; each alternative clause inside gets its
  // own tinted card (cycling) so "new color = new alternative" reads at a
  // glance even when a wrapped row spans two lines.
  const section = (label, hint, icon, k, types, cls) => html`<section class="segsection ${cls}">
    <div class="seghead">
      <span class="seghead-icon"><${Icon} name=${icon} size=${17} /></span>
      <span><b>${label}</b><small>${hint}</small></span>
    </div>
    ${d[k].map((c, i) => html`<${ClauseRow} clause=${c} types=${types}
        tint=${i % 4} vocab=${vocab} onChange=${(cl) => edit(k, i, cl)}
        onRemove=${() => drop(k, i)} />`)}
    <button class="quiet-button segment-add-condition" onclick=${() => add(k, types)}>
      <${Icon} name="plus" size=${15} /> Add another condition
    </button>
  </section>`;

  return html`<div class="segbuilder">
    <div class="builder-heading">
      <div>
        <span class="eyebrow">${initial ? "Edit segment" : "New segment"}</span>
        <h2>${initial ? initial.name : "Create a practice segment"}</h2>
      </div>
      <button class="icon-button" title="Close editor" aria-label="Close editor"
          onclick=${onCancel}><${Icon} name="close" /></button>
    </div>
    <label class="builder-name">
      <span class="field-label">Segment name</span>
      <input placeholder="e.g. Lobby to BitDW" value=${d.name}
          oninput=${(e) => setD({ ...d, name: e.target.value })} />
    </label>
    ${initial && initial.id != null && html`<div class="builder-icon">
      <span class="field-label">Icon</span>
      <img class="builder-icon-preview" alt="" draggable="false"
           src=${iconOverride ? iconSrcFromStem(iconOverride)
                              : "/ui/assets/star_1.png"} />
      <button type="button" onclick=${() => setPickingIcon(true)}>
        Choose icon…</button>
      <span class="meta">${iconOverride || "default"} · shown on the course
        quick-select</span>
    </div>`}
    ${initial && initial.id != null && html`<label class="builder-origin">
      <span class="field-label">Library category</span>
      <select value=${origin} onchange=${(e) => saveOrigin(e.target.value)}>
        <option value="">Auto (${detected ? detected.label : "Anywhere"})</option>
        ${(vocab.origins || []).filter((region) => region.key !== null)
          .map((region) => html`<optgroup key=${region.key} label=${region.label}>
            ${region.children.map((place) => html`<option key=${place.key}
              value=${place.key}>${place.label}</option>`)}
          </optgroup>`)}
      </select>
      <span class="meta">where the library files this segment</span>
    </label>`}
    ${pickingIcon && html`<${IconPicker}
        identity=${{ kind: "segment", segment_id: initial.id }}
        current=${iconOverride}
        onDone=${() => { setPickingIcon(false); if (t) t.refresh(); }} />`}
    <label class="builder-enabled">
      <input type="checkbox" checked=${d.enabled}
          onchange=${(e) => setD({ ...d, enabled: e.target.checked })} />
      <span><b>Available for practice</b><small>Show this segment in target pickers.</small></span>
    </label>
    <div class="segment-definition-grid">
      ${section("Start", "Arm when any one of these happens.", "play",
        "start_triggers", vocab.triggers, "seg-start")}
      ${section("Finish", "Complete when any one of these happens.", "target",
        "end_triggers", vocab.triggers, "seg-end")}
      ${section("Rules", "Optional checks that keep attempts valid.", "shield",
        "guards", vocab.guards, "seg-guard")}
    </div>
    ${err && html`<div class="badx">${err}</div>`}
    <div class="builder-actions">
      <span class="meta">Saving automatically recalculates this segment's history.</span>
      <button onclick=${onCancel}>Cancel</button>
      <button class="primary-button" onclick=${save}>
        <${Icon} name="save" size=${16} /> Save segment
      </button>
    </div>
  </div>`;
}

// Grouping POLICY for the segment library: castle region -> place, both read
// off the server's `origin` stamp (GET /api/segments). The JS never derives
// region membership — the taxonomy has ONE home, tracking/segments.py, and
// the editor's override picker reads the same list from vocab.
//
// Order is carried by the server too: vocab().origins is already in gameflow
// order with each region's Bowser and secret stages pinned above its main
// courses, so the level `order` functions just look up that position.
const originOf = (segment) => (segment.origin || {});

function originLevels(taxonomy) {
  const regionOrder = new Map();
  const placeOrder = new Map();
  taxonomy.forEach((region, regionIndex) => {
    regionOrder.set(String(region.key), regionIndex);
    region.children.forEach((place, placeIndex) =>
      placeOrder.set(place.key, placeIndex));
  });
  const regionLabels = new Map(taxonomy.map((r) => [String(r.key), r.label]));
  const placeLabels = new Map(taxonomy.flatMap((r) =>
    r.children.map((place) => [place.key, place.label])));
  return [
    // ?? null, not || {} alone: a row with no `origin` stamp at all makes
    // originOf return {}, and String(undefined) is the literal string
    // "undefined" — which matches no taxonomy entry and renders its own
    // group header (review M4). String(null) correctly routes into
    // "Anywhere" (the {key: null} taxonomy entry).
    { of: (segment) => String(originOf(segment).region ?? null),
      label: (key) => regionLabels.get(key) || key,
      // an unknown region (a stored override we no longer offer) sorts last
      // rather than vanishing
      order: (key) => regionOrder.has(key) ? regionOrder.get(key) : 999 },
    { of: (segment) => originOf(segment).key || null,
      label: (key) => placeLabels.get(key) || key,
      order: (key) => placeOrder.has(key) ? placeOrder.get(key) : 999 },
  ];
}

export function Segments({ t }) {
  const [defs, setDefs] = useState(null);
  const [query, setQuery] = useState("");
  const [vocabData, setVocabData] = useState(null);
  const [editing, setEditing] = useState(null);   // null | "new" | def object
  const editorRef = useRef(null);   // the open Builder's {save, dirty} handle
  const [openGroups, toggleGroup] = useOpenGroups("sm64.segOriginsOpen");
  const load = async () => setDefs(await getJSON("/api/segments"));
  useEffect(() => { load();
    getJSON("/api/segments/vocab").then(setVocabData); }, []);
  if (!defs || !vocabData) return html`<${PageState}
      kind=${t.connected ? "loading" : "offline"}
      title="Preparing the segment workshop" />`;

  const tgt = (t.view && t.view.target) || {};
  // Case-insensitive substring match on the name, applied per keystroke. The
  // seeded corpus pushes this library past 60 entries, where scrolling for
  // "BitFS Pipe Entry" is slower than typing "pipe" (user request
  // 2026-07-24). Matching the CATEGORY too means "castle" finds every
  // movement without anyone having to name them consistently.
  const needle = query.trim().toLowerCase();
  const shown = needle
    ? defs.filter((d) => `${d.name} ${d.category || ""}`
                           .toLowerCase().includes(needle))
    : defs;
  // armedSegs is the single live source: WS notices keep it instant,
  // every view fetch reconciles it so it never stays stale (store.js).
  const isArmed = (id) => t.armedSegs.has(id);
  async function setTarget(d) {
    await send("POST", "/api/target", { kind: "segment", segment_id: d.id });
    t.refresh();
  }
  async function toggle(d) {
    await send("PUT", `/api/segments/${d.id}`, { enabled: !d.enabled });
    load(); t.refresh();
  }
  async function remove(d) {
    if (!window.confirm(`Delete "${d.name}" and its history/PBs?`)) return;
    await send("DELETE", `/api/segments/${d.id}`);
    load(); t.refresh();
  }
  // Clicking "edit" while another segment's editor is open: offer to save any
  // unsaved changes, then swap immediately. Previously the click was a no-op
  // until the open editor was closed (a wasted click) — the Builder ignored
  // the new `initial` (useState reads it once) and had no key to remount.
  async function tryEdit(d) {
    if (editing) {
      const isNew = editing === "new";
      if (!isNew && editing.id === d.id) return;   // already editing this one
      const api = editorRef.current;
      if (api && api.dirty) {
        const keep = window.confirm(
          `Save your changes to "${isNew ? "the new segment" : editing.name}" `
          + `before editing "${d.name}"?\n\nOK = save · Cancel = discard`);
        if (keep && !(await api.save())) return;   // save failed -> stay put
      }
    }
    setEditing(d);
  }

  return html`<div class="workshop-page segments-page">
    <header class="practice-card workshop-hero">
      <div class="workshop-title">
        <span class="workshop-title-icon"><${Icon} name="segments" size=${22} /></span>
        <div>
          <span class="eyebrow">Build</span>
          <h2>Segments</h2>
          <p>Define repeatable sections once, then practice and rank them like stars.</p>
        </div>
      </div>
      <button class="primary-button" onclick=${() => setEditing("new")}>
        <${Icon} name="plus" size=${17} /> New segment
      </button>
    </header>

    <div class="segments-workshop">
      <aside class="practice-card workshop-card segment-library">
        <div class="workshop-card-heading">
          <div>
            <span class="eyebrow">Library</span>
            <h3>Your segments</h3>
          </div>
          <span class="count-badge">${shown.length}${
            shown.length === defs.length ? "" : ` / ${defs.length}`}</span>
        </div>
        <input class="library-search" type="search" value=${query}
          placeholder="Search segments…" aria-label="Search segments"
          oninput=${(e) => setQuery(e.target.value)} />
        <div class="segment-list">
          ${defs.length === 0 ? html`<div class="workshop-empty compact">
            No segments yet. Create one to time a repeatable section of the game.
          </div>` : shown.length === 0 ? html`<div class="workshop-empty compact">
            No segment matches “${query}”.
          </div>` : html`<${GroupedList}
            tree=${buildTree(shown, originLevels(vocabData.origins || []))}
            open=${openGroups} toggle=${toggleGroup}
            forceOpen=${() => needle.length > 0}
            renderRow=${(d) => {
              const targeted = tgt.kind === "segment" && tgt.segment_id === d.id;
              return html`<article key=${d.id}
                  class=${`segrow ${editing !== "new" && editing?.id === d.id ? "on" : ""}`}>
                <button class="segment-row-main" onclick=${() => tryEdit(d)}>
                  <span class="segment-row-name">${d.name}</span>
                  <span class="segment-row-state">
                    ${isArmed(d.id) && html`<span class="chip good">● Running</span>`}
                    ${targeted && html`<span class="chip target-chip">◎ Target</span>`}
                    ${!d.enabled && html`<span class="chip muted-chip">Hidden</span>`}
                  </span>
                </button>
                <div class="segment-row-actions">
                  <button class=${targeted ? "is-selected" : ""} onclick=${() => setTarget(d)}
                      title="Set as practice target">
                    <${Icon} name="target" size=${15} /> Target
                  </button>
                  <button onclick=${() => toggle(d)} title=${d.enabled ? "Hide from practice" : "Show in practice"}>
                    <${Icon} name=${d.enabled ? "eyeOff" : "check"} size=${15} />
                    ${d.enabled ? "Hide" : "Show"}
                  </button>
                  <button onclick=${() => tryEdit(d)} title="Edit segment">
                    <${Icon} name="edit" size=${15} /> Edit
                  </button>
                  <button class="icon-button danger-icon" onclick=${() => remove(d)}
                      title="Delete segment" aria-label=${`Delete ${d.name}`}>
                    <${Icon} name="trash" size=${15} />
                  </button>
                </div>
              </article>`;
            }} />`}
        </div>
      </aside>

      <main class="practice-card workshop-card segment-editor">
        ${editing
          ? html`<${Builder} key=${editing === "new" ? "new" : editing.id}
              vocab=${vocabData} apiRef=${editorRef} t=${t} load=${load}
              initial=${editing === "new" ? null : editing}
              onSaved=${() => { setEditing(null); load(); t.refresh(); }}
              onCancel=${() => setEditing(null)} />`
          : html`<div class="workshop-empty">
              <span class="workshop-empty-icon"><${Icon} name="segments" size=${34} /></span>
              <h3>Choose a segment to edit</h3>
              <p>Its start, finish, and rules will stay organized in separate cards.</p>
              <button class="primary-button" onclick=${() => setEditing("new")}>
                <${Icon} name="plus" size=${16} /> Create a segment
              </button>
            </div>`}
      </main>
    </div>
  </div>`;
}
