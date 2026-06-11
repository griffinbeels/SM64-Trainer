// src/sm64_events/ui/components/practice.js
import { h } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { send } from "../api.js";
import { StatMenu } from "./statmenu.js";

const html = htm.bind(h);

const OUTCOME_LABEL = { success: "✔", reset: "✘ reset",
  hard_reset: "✘ hard reset", abandoned: "– abandoned" };

function delta(frames) {
  if (frames === null || frames === undefined) return "";
  const cls = frames > 0 ? "delta-up" : "delta-down";
  const sign = frames > 0 ? "+" : "";
  return html` <span class=${cls}>${sign}${(frames / 30).toFixed(2)}s vs PB</span>`;
}

function AttemptRow({ a, t, idx }) {
  async function clear() {
    await send("POST", `/api/attempts/${a.id}/clear`, { reason: "accidental" });
    t.refresh();
  }
  async function restore() {
    await send("POST", `/api/attempts/${a.id}/restore`);
    t.refresh();
  }
  async function savePb() {
    await send("POST", "/api/pb", { attempt_id: a.id, timer_mode: t.clock });
    t.refresh();
  }
  const time = t.clock === "igt" ? a.igt : a.rta;
  return html`<tr class=${a.cleared ? "cleared" : ""}>
    <td class="meta">#${idx + 1}</td>
    <td class=${a.outcome === "success" ? "good" : "badx"}>
      ${OUTCOME_LABEL[a.outcome] || a.outcome}
      ${a.outcome === "success" && time ? html` <b>${time}</b>` : ""}
      ${a.outcome !== "success" && a.igt ? html` <span class="meta">${a.igt} in</span>` : ""}
    </td>
    <td>${a.outcome === "success" ? delta(a.pb_delta_frames) : ""}</td>
    <td class="meta">${a.strat_tag || ""}</td>
    <td style="text-align:right">
      ${a.outcome === "success" && !a.cleared
        ? html`<button onclick=${savePb}>Save as PB</button> ` : ""}
      ${a.cleared
        ? html`<button onclick=${restore}>undo</button>`
        : html`<button onclick=${clear} title="clear (mistake)">×</button>`}
    </td>
  </tr>`;
}

function StarSection({ sec, t }) {
  const pb = sec.pb[t.clock];
  return html`<div class="starsec">
    <div class="shead">
      <b>${sec.course_name} · ${sec.star_name}</b>
      <a href=${sec.links.ukikipedia} target="_blank">RTA Guide</a>
      ${sec.links.example && html`<a href=${sec.links.example} target="_blank">Example</a>`}
      <span class="pbtag">${pb ? `PB ${pb.display} (${t.clock})` : "no PB yet"}</span>
    </div>
    <table>${sec.attempts.map((a, i) => html`<${AttemptRow} a=${a} t=${t} idx=${i} />`)}</table>
    <div class="chips">
      ${sec.stats.map((s) => html`
        <span class="chip" title=${s.key}>${s.label} ${s.display ?? "–"}</span>`)}
    </div>
  </div>`;
}

export function Practice({ t }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const v = t.view;
  if (!v) return html`<p class="meta">loading… (server unreachable? check /health)</p>`;
  return html`
    <div style="display:flex;justify-content:flex-end">
      <button onclick=${() => setMenuOpen(!menuOpen)}>⚙ stats</button>
    </div>
    ${menuOpen && html`<${StatMenu} t=${t} close=${() => setMenuOpen(false)} />`}
    ${v.stars.length === 0 && v.unassigned.length === 0
      ? html`<p class="meta">No attempts this session yet — grab a star.</p>` : ""}
    ${v.stars.map((sec) => html`<${StarSection} sec=${sec} t=${t} />`)}
    ${v.unassigned.length > 0 && html`<div class="starsec">
      <div class="shead"><b>No target</b>
        <span class="meta">failures before any star was grabbed or set</span></div>
      <table>${v.unassigned.map((a, i) => html`<${AttemptRow} a=${a} t=${t} idx=${i} />`)}</table>
    </div>`}`;
}
