// src/sm64_events/ui/components/timeline.js
import { h } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { send } from "../api.js";
import { fmtIgt } from "../format.js";

const html = htm.bind(h);

// Marker styles per outcome. Extending the graph = one row here plus one
// row in TIMELINE_OUTCOMES (tracking/views.py).
const MARKERS = {
  success: { color: "#a3e0a3" },
  reset: { color: "#e0a3a3" },
  death: { color: "#d96a6a" },
};
const ANNOT = "#8ab4f8"; // strategy annotation flags (spec §3)

const W = 600, H = 28, PAD = 8, MID = H / 2, BAND = 16; // BAND: label strip above

// "3" / "3.5" (seconds) or 0'03"50 (IGT) -> frames at 30 fps; null = unparseable
export function parseTimeInput(text) {
  const trimmed = String(text).trim();
  if (trimmed === "") return null;   // Number("") === 0 — must not place a marker at 0'00"00
  const igt = trimmed.match(/^(\d+)'(\d{1,2})"(\d{1,2})$/);
  if (igt) {
    // a single centisecond digit reads as tenths (0'03"5 == 50cs), matching
    // the always-two-digit display format
    const cs = igt[3].length === 1 ? +igt[3] * 10 : +igt[3];
    return (+igt[1] * 60 + +igt[2]) * 30 + Math.round((cs * 30) / 100);
  }
  const secs = Number(trimmed);
  return Number.isFinite(secs) && secs >= 0 ? Math.round(secs * 30) : null;
}

function Marker({ p, x }) {
  const m = MARKERS[p.outcome] || { color: "#888" };
  const label = html`<title>${p.outcome} · ${p.igt}</title>`;
  if (p.outcome === "success") {
    return html`<circle cx=${x} cy=${MID} r="4.5" fill=${m.color}>${label}</circle>`;
  }
  if (p.outcome === "death") {
    return html`<g stroke=${m.color} stroke-width="1.6">
      <line x1=${x - 3.5} y1=${MID - 3.5} x2=${x + 3.5} y2=${MID + 3.5} />
      <line x1=${x - 3.5} y1=${MID + 3.5} x2=${x + 3.5} y2=${MID - 3.5} />${label}</g>`;
  }
  return html`<line x1=${x} y1=${MID - 5} x2=${x} y2=${MID + 5}
                    stroke=${m.color} stroke-width="1.6">${label}</line>`;
}

// tl: attempt-point payload (may be null before any attempts);
// sec: the star section (course/star ids, last_strat, markers_by_strat);
// t: the tracker store (refresh after PUT).
export function Timeline({ tl, sec, t }) {
  const strat = sec.last_strat || "";
  const markers = (sec.markers_by_strat || {})[strat] || [];
  const [form, setForm] = useState(null); // {time, label} while the editor is open
  const points = tl ? tl.points : [];
  const showStrip = points.length > 0 || markers.length > 0;

  const axisMax = Math.max(tl ? tl.max_frames : 0,
    ...points.map((p) => p.frames), ...markers.map((m) => m.frames)) || 1;
  const x = (f) => PAD + (f / axisMax) * (W - 2 * PAD);

  async function save(list) {
    await send("PUT", "/api/markers", {
      course_id: sec.course_id, star_id: sec.star_id,
      strat_tag: sec.last_strat || null,
      markers: list.map(({ frames, label }) => ({ frames, label })),
    });
    setForm(null);
    t.refresh();
  }
  function addFromForm() {
    const frames = parseTimeInput(form.time);
    const label = (form.label || "").trim();
    if (frames === null || !label) return;
    save([...markers, { frames, label }]);
  }
  function clickToPlace(e) {
    // map the click through the SVG's CTM: viewBox 600x44 letterboxes inside
    // the wider container (preserveAspectRatio "meet"), so bounding-box
    // fractions are NOT viewBox coordinates.
    const p = new DOMPoint(e.clientX, e.clientY)
      .matrixTransform(e.currentTarget.getScreenCTM().inverse());
    const f = Math.round(Math.max(0, Math.min(1, (p.x - PAD) / (W - 2 * PAD))) * axisMax);
    setForm({ time: (f / 30).toFixed(2), label: form ? form.label : "" });
  }

  const TOT = H + BAND;
  return html`<div style="margin:.3rem 0">
    ${showStrip && html`<div>
      <svg viewBox="0 0 ${W} ${TOT}" style="width:100%;height:${TOT}px;display:block;cursor:crosshair"
           onclick=${clickToPlace}>
        ${markers.map((m) => html`<g>
          <text x=${Math.min(Math.max(x(m.frames), 20), W - 20)} y="10" fill=${ANNOT}
                font-size="9" text-anchor="middle">${m.label}</text>
          <line x1=${x(m.frames)} y1="13" x2=${x(m.frames)} y2=${BAND + H - 4}
                stroke=${ANNOT} stroke-width="1.2" stroke-dasharray="3,2">
            <title>${m.label} · ${fmtIgt(m.frames)}</title></line></g>`)}
        <g transform="translate(0 ${BAND})">
          <line x1=${PAD} y1=${MID} x2=${W - PAD} y2=${MID} stroke="#3a4150" />
          ${tl && tl.max_is_success && html`<line x1=${x(tl.max_frames)} y1=${MID - 7}
              x2=${x(tl.max_frames)} y2=${MID + 7} stroke="#3a4150"
              stroke-dasharray="2,2"><title>longest success · ${tl.max_display}</title></line>`}
          ${points.map((p) => html`<${Marker} p=${p} x=${x(p.frames)} />`)}
        </g>
      </svg>
      <div class="meta" style="display:flex;justify-content:space-between">
        <span>0'00"00</span>
        <span>${tl ? `${tl.max_is_success ? "" : "~"}${tl.max_display}` : fmtIgt(axisMax)}</span>
      </div>
    </div>`}
    <div class="chips">
      ${markers.map((m, i) => html`<span class="chip" style="color:${ANNOT}">
        ${fmtIgt(m.frames)} ${m.label}
        <span style="cursor:pointer;opacity:.6" title="delete marker"
              onclick=${() => save(markers.filter((_, j) => j !== i))}> ×</span></span>`)}
      ${form
        ? html`<span class="chip">
            <input size="8" placeholder=${'3 or 0\'03"00'} value=${form.time}
                   oninput=${(e) => setForm({ ...form, time: e.target.value })}
                   onkeydown=${(e) => e.key === "Enter" && addFromForm()} />
            <input size="14" placeholder="label" maxlength="60" value=${form.label}
                   oninput=${(e) => setForm({ ...form, label: e.target.value })}
                   onkeydown=${(e) => e.key === "Enter" && addFromForm()} />
            <button onclick=${addFromForm}>add</button>
            <button onclick=${() => setForm(null)}>cancel</button></span>`
        : html`<span class="chip" style="cursor:pointer;border-style:dashed"
              onclick=${() => setForm({ time: "", label: "" })}>+ marker</span>`}
    </div>
  </div>`;
}
