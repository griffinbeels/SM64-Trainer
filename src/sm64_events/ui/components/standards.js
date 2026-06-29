// src/sm64_events/ui/components/standards.js — collapsible, view-by-default
// rank-standards table for one entity (star:c:s or segment:id). Each cutoff time
// links to the fastest example video that RANKS that tier (server-resolved
// cutoff_videos: auto band from xcams clips + the user's per-cell overrides); the
// strat header links to the Mario-row video (= the overall fastest). Edit mode
// adds a ▶ button per cell to paste/clear an override, and the section links out
// to the xcams Daily Star page for browsing every example.
import { h } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { RANK_NAMES, rankColor } from "./ranks.js";
const html = htm.bind(h);
const enc = encodeURIComponent;

export function StandardsPanel({ entity, activeStrat, onChanged }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState(null);
  const [editing, setEditing] = useState(false);
  async function load() { setData(await getJSON(`/api/ranks/standards?entity=${enc(entity)}`)); }
  function toggle() { const n = !open; setOpen(n); if (n && !data) load(); }
  async function put(strat, rank, seconds) {
    await send("PUT", `/api/ranks/standards/${enc(entity)}/${enc(strat)}/${enc(rank)}`, { seconds });
    await load(); onChanged && onChanged();
  }
  async function addStrat() {
    const s = (window.prompt("New strategy name:") || "").trim();
    if (!s) return;
    await send("POST", `/api/ranks/standards/${enc(entity)}`, { strategy: s });
    await load(); onChanged && onChanged();
  }
  async function delStrat(s) {
    if (!window.confirm(`Remove strategy "${s}"?`)) return;
    await send("DELETE", `/api/ranks/standards/${enc(entity)}/${enc(s)}`);
    await load(); onChanged && onChanged();
  }
  async function editVideo(strat, rank) {
    const cur = userVid(strat, rank) || "";
    const next = window.prompt(`Example video URL — ${rank} (${strat})\nblank to clear:`, cur);
    if (next === null) return;                                  // cancelled
    const url = next.trim();
    const path = `/api/ranks/standards/${enc(entity)}/${enc(strat)}/${enc(rank)}/video`;
    await send(url ? "PUT" : "DELETE", path, url ? { url } : undefined);
    await load(); onChanged && onChanged();
  }
  async function reset() {
    if (!window.confirm("Reset this entity to community defaults?")) return;
    await send("POST", `/api/ranks/standards/${enc(entity)}/reset`);
    await load(); setEditing(false); onChanged && onChanged();
  }
  // per-(strat,rank) video accessors (resolved auto+override vs raw user override)
  const cutoffVid = (s, rank) =>
    (data.cutoff_videos && data.cutoff_videos[s] && data.cutoff_videos[s][rank]) || null;
  const userVid = (s, rank) =>
    (data.user_videos && data.user_videos[s] && data.user_videos[s][rank]) || null;
  const headVid = (s) => cutoffVid(s, "Mario") || (data.videos && data.videos[s]) || null;

  const strats = data ? Object.keys(data.strategies) : [];
  return html`<div class="stdpanel">
    <div class="disc" onclick=${toggle} style="cursor:pointer">
      <span>${open ? "▾" : "▸"}</span> Rank standards
      ${activeStrat ? html`<span class="meta"> · active: ${activeStrat}</span>` : null}
    </div>
    ${open && !data ? html`<div class="stdbody"><span class="meta">Loading…</span></div>` : null}
    ${open && data ? html`<div class="stdbody">
      <div class="stdtools">
        <button class="meta" onclick=${() => setEditing(!editing)}>${editing ? "Done" : "Edit"}</button>
        ${editing ? html`<button class="meta" onclick=${addStrat}>+ Strategy</button>` : null}
        <button class="meta" onclick=${reset}>Reset to community defaults</button>
        ${data.xcams_url ? html`<a class="meta" href=${data.xcams_url} target="_blank" rel="noopener"
            title="browse every example run for this star on the xcams Daily Star page">Examples on xcams ↗</a>` : null}
      </div>
      <table class="stdtable"><thead><tr><th>Strat</th>
        ${strats.map((s) => html`<th class=${s === activeStrat ? "col-active" : ""}>${headVid(s)
          ? html`<a href=${headVid(s)} target="_blank" rel="noopener" title="fastest-time video">${s}</a>`
          : s}${editing ? html` <button class="candx" title="remove strategy" onclick=${() => delStrat(s)}>×</button>` : ""}</th>`)}</tr></thead>
        <tbody>
        ${RANK_NAMES.filter((r) => r !== "Iron").map((rank) => html`<tr>
          <td style=${`background:${rankColor(rank)};color:#111;font-weight:700`}>${rank}</td>
          ${strats.map((s) => {
            const v = data.strategies[s][rank];
            const vid = cutoffVid(s, rank);
            const label = v != null ? v.toFixed(2) : "—";
            return html`<td class=${s === activeStrat ? "col-active" : ""}>
              ${editing
                ? html`<span class="stdcell"><input class="stdinp" value=${v ?? ""} placeholder="—"
                      onchange=${(e) => { const n = parseFloat(e.target.value); if (!isNaN(n)) put(s, rank, n); }} />
                    <button class="vidbtn" title=${`${userVid(s, rank) ? "edit" : "add"} ${rank} example video`}
                      onclick=${() => editVideo(s, rank)}>${userVid(s, rank) ? "▶✎" : "▶＋"}</button></span>`
                : (vid
                    ? html`<a href=${vid} target="_blank" rel="noopener" title=${`example ${rank} run`}>${label}</a>`
                    : label)}</td>`;
          })}</tr>`)}
        </tbody></table>
    </div>` : null}
  </div>`;
}
