// src/sm64_events/ui/components/segments.js — definition list + builder.
// The form is 100% vocab-driven: GET /api/segments/vocab supplies trigger
// types, param schemas, and level/area enums; adding a trigger type in
// tracking/segments.py appears here with zero UI changes.
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";

const html = htm.bind(h);

function ParamInput({ schema, name, value, vocab, onChange }) {
  if (schema.kind === "level") {
    return html`<select value=${value ?? ""}
        onchange=${(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}>
      <option value="">${schema.required ? "— pick level —" : "(any)"}</option>
      ${Object.entries(vocab.levels).map(([id, n]) =>
        html`<option value=${id}>${n}</option>`)}
    </select>`;
  }
  if (schema.kind === "area") {
    return html`<select value=${value ?? ""}
        onchange=${(e) => onChange(Number(e.target.value))}>
      <option value="">— pick area —</option>
      ${Object.entries(vocab.castle_areas).map(([id, n]) =>
        html`<option value=${id}>${n}</option>`)}
    </select>`;
  }
  return html`<input type="number" style="width:5rem" value=${value ?? ""}
      placeholder=${name}
      onchange=${(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} />`;
}

function ClauseRow({ clause, types, vocab, onChange, onRemove }) {
  const spec = types.find((t) => t.key === clause.type) || types[0];
  return html`<div class="segclause">
    <select value=${clause.type}
        onchange=${(e) => onChange({ type: e.target.value })}>
      ${types.map((t) => html`<option value=${t.key}>${t.label}</option>`)}
    </select>
    ${Object.entries(spec.params).map(([name, schema]) => html`
      <${ParamInput} schema=${schema} name=${name} vocab=${vocab}
        value=${clause[name]}
        onChange=${(v) => onChange({ ...clause, [name]: v })} />`)}
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

  const clauses = (k, types) => html`
    ${d[k].map((c, i) => html`<${ClauseRow} clause=${c} types=${types}
        vocab=${vocab} onChange=${(cl) => edit(k, i, cl)}
        onRemove=${() => drop(k, i)} />`)}
    <button class="meta" onclick=${() => add(k, types)}>+ alternate trigger</button>`;

  return html`<div class="segbuilder">
    <div><input placeholder="Segment name" value=${d.name}
        onchange=${(e) => setD({ ...d, name: e.target.value })} /></div>
    <div class="label">Starts when any of</div>
    ${clauses("start_triggers", vocab.triggers)}
    <div class="label">Ends when any of</div>
    ${clauses("end_triggers", vocab.triggers)}
    <div class="label">Guards (optional)</div>
    ${clauses("guards", vocab.guards)}
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
  // The view is the authoritative armed state (refetched on reconnect);
  // the live WS-driven armedSegs Set is only a fallback while view is null.
  const viewArmed = new Set((t.view && t.view.segments || [])
    .filter((s) => s.armed).map((s) => s.segment_id));
  const isArmed = (id) => (t.view && t.view.segments
    ? viewArmed.has(id) : t.armedSegs.has(id));
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
