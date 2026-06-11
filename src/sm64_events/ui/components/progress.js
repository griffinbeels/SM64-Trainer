// src/sm64_events/ui/components/progress.js — completion time over time
// (spec §4). One segment per session, ⫽ breaks between segments (lifetime);
// gold = explicitly saved PBs for the current clock. Y: faster = lower.
// No click interaction; if any is added, map through getScreenCTM (see
// timeline.js clickToPlace for the letterbox rationale).
import { h } from "preact";
import htm from "htm";

const html = htm.bind(h);

const W = 600, H = 170, PADL = 56, PADR = 10, PADT = 12, PADB = 26, GAP = 18;
const GOLD = "#e0c36a", GOLD_RIM = "#f5e2a8", GREEN = "#a3e0a3",
      GRID = "#262c38", AXIS = "#3a4150", TXT = "#6c7686";

function fmtIgt(frames) {
  const m = Math.floor(frames / 1800), s = Math.floor((frames % 1800) / 30),
        c = Math.floor(((frames % 30) * 100) / 30);
  return `${m}'${String(s).padStart(2, "0")}"${String(c).padStart(2, "0")}`;
}

// Local-timezone tick label; MM/DD/YY prefix when the graph spans >1 day.
function fmtTick(iso, withDate) {
  const d = new Date(iso);
  const time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return withDate
    ? `${d.toLocaleDateString([], { month: "2-digit", day: "2-digit", year: "2-digit" })} ${time}`
    : time;
}

export function Progress({ prog, clock }) {
  if (!prog) return "";
  const fKey = clock === "igt" ? "igt_frames" : "rta_frames";
  const pbKey = clock === "igt" ? "is_pb_igt" : "is_pb_rta";
  // frames > 0 drops same-tick race rows (rta=0 junk; see projection.py
  // caveat 1) — deliberately a CLIENT-side filter so the igt clock keeps them
  const segs = prog.sessions
    .map((s) => ({ ...s, points: s.points.filter((p) => p[fKey] != null && p[fKey] > 0) }))
    .filter((s) => s.points.length > 0);
  if (!segs.length) return "";

  const all = segs.flatMap((s) => s.points.map((p) => p[fKey]));
  let lo = Math.min(...all), hi = Math.max(...all);
  const span = Math.max(hi - lo, 30);
  lo = Math.max(0, lo - span * 0.15);
  hi = hi + span * 0.15;
  const y = (f) => PADT + ((hi - f) / (hi - lo)) * (H - PADT - PADB);

  const stamps = segs.flatMap((s) => s.points.map((p) => Date.parse(p.t_utc)));
  const withDate = new Date(Math.min(...stamps)).toDateString()
    !== new Date(Math.max(...stamps)).toDateString();

  // segment widths proportional to point count; within a segment, x is
  // linear wall-clock time for that session
  const innerW = W - PADL - PADR - GAP * (segs.length - 1);
  const total = all.length;
  let cursor = PADL;
  const placed = segs.map((s) => {
    const w = Math.max(innerW * (s.points.length / total), 24);
    const t0 = Date.parse(s.points[0].t_utc);
    const t1 = Date.parse(s.points[s.points.length - 1].t_utc);
    const left = cursor;
    const xs = s.points.map((p) => t1 > t0
      ? left + 8 + ((Date.parse(p.t_utc) - t0) / (t1 - t0)) * (w - 16)
      : left + w / 2);
    cursor += w + GAP;
    return { ...s, left, w, xs };
  });

  const mid = (lo + hi) / 2;
  const last = placed[placed.length - 1];
  return html`<div style="margin:.3rem 0">
    <svg viewBox="0 0 ${W} ${H}" style="width:100%;display:block">
      ${[hi, mid, lo].map((v, i) => html`<g>
        <line x1=${PADL} y1=${y(v)} x2=${W - PADR} y2=${y(v)}
              stroke=${i === 2 ? AXIS : GRID} />
        <text x=${PADL - 6} y=${y(v) + 3} fill=${TXT} font-size="9"
              text-anchor="end">${fmtIgt(Math.round(v))}</text></g>`)}
      ${placed.map((s, i) => html`<g>
        ${i > 0 && html`<g stroke=${AXIS} stroke-width="1.4">
          <line x1=${s.left - GAP + 4} y1=${y(lo) - 6} x2=${s.left - GAP + 10} y2=${y(lo) + 6} />
          <line x1=${s.left - GAP + 9} y1=${y(lo) - 6} x2=${s.left - GAP + 15} y2=${y(lo) + 6} /></g>`}
        <polyline fill="none" stroke=${AXIS} stroke-width="1.2"
          points=${s.points.map((p, j) => `${s.xs[j]},${y(p[fKey])}`).join(" ")} />
        ${s.points.map((p, j) => html`<circle cx=${s.xs[j]} cy=${y(p[fKey])}
            r=${p[pbKey] ? 5 : 4.5} fill=${p[pbKey] ? GOLD : GREEN}
            stroke=${p[pbKey] ? GOLD_RIM : "none"} stroke-width="1">
          <title>${p[pbKey] ? "PB " : ""}${clock === "igt" ? p.igt : p.rta} · ${fmtTick(p.t_utc, true)}</title>
        </circle>`)}
        <text x=${s.left + s.w / 2} y=${H - 8} fill=${TXT} font-size="9"
              text-anchor="middle">${fmtTick(s.points[0].t_utc, withDate)}</text>
      </g>`)}
      ${placed.length === 1 && last.points.length > 1 && html`<text
          x=${W - PADR} y=${H - 8} fill=${TXT} font-size="9" text-anchor="end"
        >${fmtTick(last.points[last.points.length - 1].t_utc, withDate)}</text>`}
    </svg>
  </div>`;
}
