// src/sm64_events/ui/components/segments.js — definition list + builder.
// The form is 100% vocab-driven: GET /api/segments/vocab supplies trigger
// types, param schemas, sentence templates, and level/area/course/star
// enums; adding a trigger type in tracking/segments.py appears here with
// zero UI changes.
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";

const html = htm.bind(h);

function ParamInput({ schema, name, value, vocab, clause, onChange }) {
  // "" MUST become null, never Number("")===0 — 0 is a real area/level id,
  // so a bare Number() silently scoped cleared optional params to area 0.
  const numOrNull = (s) => (s === "" ? null : Number(s));
  const dropdown = (entries, anyLabel, pickLabel) => html`<select
      value=${value ?? ""} onchange=${(e) => onChange(numOrNull(e.target.value))}>
    <option value="">${schema.required ? pickLabel : anyLabel}</option>
    ${entries.map(([id, n]) => html`<option value=${id}>${n}</option>`)}
  </select>`;
  if (schema.kind === "level")
    return dropdown(Object.entries(vocab.levels), "(any level)", "— pick level —");
  if (schema.kind === "area")
    return dropdown(Object.entries(vocab.castle_areas), "(any area)", "— pick area —");
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
  return html`<input type="number" style="width:5rem" value=${value ?? ""}
      placeholder=${name}
      onchange=${(e) => onChange(numOrNull(e.target.value))} />`;
}

function ClauseRow({ clause, types, vocab, tint, onChange, onRemove }) {
  const spec = types.find((t) => t.key === clause.type) || types[0];
  const setParam = (pname, v) => {
    const next = { ...clause, [pname]: v };
    // a star id is meaningless outside its course — clear it on course change
    if (pname === "course" && "star" in spec.params) next.star = null;
    onChange(next);
  };
  const param = (pname) => html`<${ParamInput} schema=${spec.params[pname]}
      name=${pname} vocab=${vocab} clause=${clause} value=${clause[pname]}
      onChange=${(v) => setParam(pname, v)} />`;
  // "{to} coming from {from}" → inputs interleaved with muted words.
  // Params a template forgets to mention render appended — the registry
  // test makes that unreachable; this keeps a bad vocab usable, not blank.
  const mentioned = new Set();
  const rendered = (spec.template || "").split(/(\{\w+\})/).map((tok) => {
    const m = /^\{(\w+)\}$/.exec(tok);
    if (m && spec.params[m[1]]) { mentioned.add(m[1]); return param(m[1]); }
    const word = tok.trim();
    return word ? html`<span class="segword">${word}</span>` : null;
  });
  const extras = Object.keys(spec.params).filter((p) => !mentioned.has(p));
  return html`<div class="segclause tint${tint ?? 0}">
    <select value=${clause.type}
        onchange=${(e) => onChange({ type: e.target.value })}>
      ${types.map((t) => html`<option value=${t.key}>${t.label}</option>`)}
    </select>
    ${rendered}
    ${extras.map(param)}
    <button onclick=${onRemove}>✕</button>
  </div>`;
}

function Builder({ vocab, initial, onSaved, onCancel }) {
  const blank = { name: "", enabled: true,
    start_triggers: [{ type: "level_enter" }],
    end_triggers: [{ type: "level_enter" }], guards: [] };
  const [d, setD] = useState(initial || blank);
  const [err, setErr] = useState(null);
  const edit = (k, i, clause) => setD({ ...d,
    [k]: d[k].map((c, j) => (j === i ? clause : c)) });
  const add = (k, types) => setD({ ...d, [k]: [...d[k], { type: types[0].key }] });
  const drop = (k, i) => setD({ ...d, [k]: d[k].filter((_, j) => j !== i) });

  async function save() {
    try {
      setErr(null);
      // Strip server-only fields: edit rows come from GET /api/segments and
      // carry id/created_utc, which SegmentPatch (extra="forbid") rejects.
      const { id: _id, created_utc: _c, ...body } = d;
      if (initial && initial.id != null) {
        await send("PUT", `/api/segments/${initial.id}`, body);
      } else {
        await send("POST", "/api/segments", body);
      }
      onSaved();
    } catch (e) { setErr(String(e)); }
  }

  // One bordered group per side; each alternative clause inside gets its
  // own tinted card (cycling) so "new color = new alternative" reads at a
  // glance even when a wrapped row spans two lines.
  const section = (label, k, types, cls) => html`<div class="segsection ${cls}">
    <div class="seghead">${label}</div>
    ${d[k].map((c, i) => html`<${ClauseRow} clause=${c} types=${types}
        tint=${i % 4} vocab=${vocab} onChange=${(cl) => edit(k, i, cl)}
        onRemove=${() => drop(k, i)} />`)}
    <button class="meta" onclick=${() => add(k, types)}>+ alternate trigger</button>
  </div>`;

  return html`<div class="segbuilder">
    <div><input placeholder="Segment name" value=${d.name}
        oninput=${(e) => setD({ ...d, name: e.target.value })} /></div>
    ${section("Starts when any of", "start_triggers", vocab.triggers, "seg-start")}
    ${section("Ends when any of", "end_triggers", vocab.triggers, "seg-end")}
    ${section("Guards (optional)", "guards", vocab.guards, "seg-guard")}
    ${err && html`<div class="badx">${err}</div>`}
    <div>
      <button onclick=${save}>Save — history recomputes automatically</button>
      <button onclick=${onCancel}>Cancel</button>
    </div>
  </div>`;
}

export function Segments({ t }) {
  const [defs, setDefs] = useState(null);
  const [vocabData, setVocabData] = useState(null);
  const [editing, setEditing] = useState(null);   // null | "new" | def object
  const load = async () => setDefs(await getJSON("/api/segments"));
  useEffect(() => { load();
    getJSON("/api/segments/vocab").then(setVocabData); }, []);
  if (!defs || !vocabData) return html`<div class="meta">loading…</div>`;

  const tgt = (t.view && t.view.target) || {};
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

  return html`<div>
    ${defs.map((d) => html`<div class="segrow">
      <b>${d.name}</b>
      ${isArmed(d.id) && html`<span class="chip good">⏱ armed</span>`}
      ${tgt.kind === "segment" && tgt.segment_id === d.id
        && html`<span class="chip">★ target</span>`}
      <span style="flex:1"></span>
      <button onclick=${() => setTarget(d)}>set target</button>
      <button onclick=${() => toggle(d)}>${d.enabled ? "disable" : "enable"}</button>
      <button onclick=${() => setEditing(d)}>edit</button>
      <button onclick=${() => remove(d)}>delete</button>
    </div>`)}
    ${editing
      ? html`<${Builder} vocab=${vocabData}
          initial=${editing === "new" ? null : editing}
          onSaved=${() => { setEditing(null); load(); t.refresh(); }}
          onCancel=${() => setEditing(null)} />`
      : html`<button onclick=${() => setEditing("new")}>+ New segment</button>`}
  </div>`;
}
