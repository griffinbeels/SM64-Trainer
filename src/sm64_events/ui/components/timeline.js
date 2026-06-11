// src/sm64_events/ui/components/timeline.js
import { h } from "preact";
import htm from "htm";

const html = htm.bind(h);

// Marker styles per outcome. Extending the graph = one row here plus one
// row in TIMELINE_OUTCOMES (tracking/views.py).
const MARKERS = {
  success: { color: "#a3e0a3" },
  reset: { color: "#e0a3a3" },
  death: { color: "#d96a6a" },
};

const W = 600, H = 28, PAD = 8, MID = H / 2;

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

export function Timeline({ tl }) {
  if (!tl || !tl.points.length) return "";
  const axisMax = Math.max(tl.max_frames,
    ...tl.points.map((p) => p.frames)) || 1;
  const x = (f) => PAD + (f / axisMax) * (W - 2 * PAD);
  return html`<div style="margin:.3rem 0">
    <svg viewBox="0 0 ${W} ${H}" style="width:100%;height:${H}px;display:block">
      <line x1=${PAD} y1=${MID} x2=${W - PAD} y2=${MID} stroke="#3a4150" />
      ${tl.max_is_success && html`<line x1=${x(tl.max_frames)} y1=${MID - 7}
          x2=${x(tl.max_frames)} y2=${MID + 7} stroke="#3a4150"
          stroke-dasharray="2,2"><title>longest success · ${tl.max_display}</title></line>`}
      ${tl.points.map((p) => html`<${Marker} p=${p} x=${x(p.frames)} />`)}
    </svg>
    <div class="meta" style="display:flex;justify-content:space-between">
      <span>0'00"00</span>
      <span>${tl.max_is_success ? "" : "~"}${tl.max_display}</span>
    </div>
  </div>`;
}
