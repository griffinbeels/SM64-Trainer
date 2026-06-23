// src/sm64_events/ui/components/standards.js — collapsible, view-by-default
// rank-standards table for one entity (star:c:s or segment:id).
import { h } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { RANK_NAMES, rankColor } from "./ranks.js";
const html = htm.bind(h);

export function StandardsPanel({ entity, activeStrat, onChanged }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState(null);
  const [editing, setEditing] = useState(false);
  async function load() { setData(await getJSON(`/api/ranks/standards?entity=${encodeURIComponent(entity)}`)); }
  function toggle() { const n = !open; setOpen(n); if (n && !data) load(); }
  async function put(strat, rank, seconds) {
    await send("PUT", `/api/ranks/standards/${encodeURIComponent(entity)}/${encodeURIComponent(strat)}/${rank}`, { seconds });
    await load(); onChanged && onChanged();
  }
  async function addStrat() {
    const s = (window.prompt("New strategy name:") || "").trim();
    if (!s) return;
    await send("POST", `/api/ranks/standards/${encodeURIComponent(entity)}`, { strategy: s });
    await load(); onChanged && onChanged();
  }
  async function reset() {
    if (!window.confirm("Reset this entity to community defaults?")) return;
    await send("POST", `/api/ranks/standards/${encodeURIComponent(entity)}/reset`);
    await load(); onChanged && onChanged();
  }
  const strats = data ? Object.keys(data.strategies) : [];
  return html`<div class="stdpanel">
    <div class="disc" onclick=${toggle} style="cursor:pointer">
      <span>${open ? "▾" : "▸"}</span> Rank standards
      ${activeStrat ? html`<span class="meta"> · active: ${activeStrat}</span>` : null}
    </div>
    ${open && data ? html`<div class="stdbody">
      <div class="stdtools">
        <button class="meta" onclick=${() => setEditing(!editing)}>${editing ? "Done" : "Edit"}</button>
        ${editing ? html`<button class="meta" onclick=${addStrat}>+ Strategy</button>` : null}
        <button class="meta" onclick=${reset}>Reset to community defaults</button>
      </div>
      <table class="stdtable"><thead><tr><th>Strat</th>
        ${strats.map((s) => html`<th class=${s === activeStrat ? "col-active" : ""}>${s}</th>`)}</tr></thead>
        <tbody>
        ${RANK_NAMES.filter((r) => r !== "Iron").map((rank) => html`<tr>
          <td style=${`background:${rankColor(rank)};color:#111;font-weight:700`}>${rank}</td>
          ${strats.map((s) => {
            const v = data.strategies[s][rank];
            return html`<td class=${s === activeStrat ? "col-active" : ""}>
              ${editing
                ? html`<input class="stdinp" value=${v ?? ""} placeholder="—"
                    onchange=${(e) => { const n = parseFloat(e.target.value); if (!isNaN(n)) put(s, rank, n); }} />`
                : (v != null ? v.toFixed(2) : "—")}</td>`;
          })}</tr>`)}
        </tbody></table>
    </div>` : null}
  </div>`;
}
