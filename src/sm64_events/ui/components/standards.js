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
import { StratModal } from "./stratmodal.js";
import { Modal } from "./modal.js";
import { Icon } from "./icons.js";
const html = htm.bind(h);
const enc = encodeURIComponent;

export function StandardsPanel({ entity, activeStrat, strategies, onChanged }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState(null);
  const [editing, setEditing] = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const [videoEdit, setVideoEdit] = useState(null);
  async function load() { setData(await getJSON(`/api/ranks/standards?entity=${enc(entity)}`)); }
  // Reload on EVERY open, not just the first: a strat created from the
  // practice dropdown or header picker while this panel sat cached would
  // otherwise show empty cells forever (its data is fetched out-of-band,
  // not via the session view). Old data stays visible until replaced.
  function toggle() { const n = !open; setOpen(n); if (n) load(); }
  async function put(strat, rank, seconds) {
    await send("PUT", `/api/ranks/standards/${enc(entity)}/${enc(strat)}/${enc(rank)}`, { seconds });
    await load(); onChanged && onChanged();
  }
  async function delStrat(s) {
    // Dual-meaning x (user-picked): seeded strats are community data —
    // clear-only; custom strats fully delete (tombstone hides attempt-
    // observed occurrences server-side; re-creating the name restores).
    const msg = isSeeded(s)
      ? `Clear rank standards for "${s}"? (The column stays while the strategy is in use.)`
      : `Delete strategy "${s}"?\nRemoves it from all dropdowns and clears its rank `
        + `standards. Past attempts keep their recorded times; re-creating the same `
        + `name restores them.`;
    if (!window.confirm(msg)) return;
    const qs = isSeeded(s) ? "" : "?purge=true";
    await send("DELETE", `/api/ranks/standards/${enc(entity)}/${enc(s)}${qs}`);
    await load(); onChanged && onChanged();
  }
  function editVideo(strat, rank) {
    setVideoEdit({ strat, rank, url: userVid(strat, rank) || "", saving: false, error: null });
  }
  async function saveVideo(nextUrl = videoEdit.url) {
    const { strat, rank } = videoEdit;
    const url = nextUrl.trim();
    const path = `/api/ranks/standards/${enc(entity)}/${enc(strat)}/${enc(rank)}/video`;
    setVideoEdit({ ...videoEdit, saving: true, error: null });
    try {
    await send(url ? "PUT" : "DELETE", path, url ? { url } : undefined);
      await load(); onChanged && onChanged(); setVideoEdit(null);
    } catch (e) {
      setVideoEdit({ ...videoEdit, saving: false, error: String(e) });
    }
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
  const isSeeded = (s) => (data.seeded || []).includes(s);

  // Columns = store strategies (community order first) + every other strat
  // this section knows (registered / used on attempts — sec.strategies from
  // views.py). A known strat with no store entry renders an empty column, so
  // custom strats are fillable the moment they exist. Object.hasOwn (not
  // `in`): a strat named e.g. "constructor" must not vanish via the proto
  // chain.
  const strats = data
    ? [...Object.keys(data.strategies),
       ...(strategies || []).filter((s) => !Object.hasOwn(data.strategies, s))]
    : [];
  return html`<div class="stdpanel">
    <button class="disc standards-toggle" onclick=${toggle} aria-expanded=${open}>
      <${Icon} name="rank" size=${16} />
      <span>Rank standards</span>
      ${activeStrat ? html`<span class="meta"> · active: ${activeStrat}</span>` : null}
      <${Icon} name="chevron" size=${16} className="standards-chevron" />
    </button>
    ${open && !data ? html`<div class="stdbody"><div class="inline-state loading">
      <${Icon} name="updates" size=${16} /> Loading standards…
    </div></div>` : null}
    ${open && data ? html`<div class="stdbody">
      <div class="stdtools">
        <button class=${editing ? "is-selected" : ""} onclick=${() => setEditing(!editing)}>
          <${Icon} name=${editing ? "check" : "edit"} size=${15} /> ${editing ? "Done editing" : "Edit"}
        </button>
        ${editing ? html`<button onclick=${() => setShowAdd(true)}>
          <${Icon} name="plus" size=${15} /> Strategy
        </button>` : null}
        <button class="quiet-button" onclick=${reset}>
          <${Icon} name="restart" size=${15} /> Community defaults
        </button>
        ${data.xcams_url ? html`<a class="meta" href=${data.xcams_url} target="_blank" rel="noopener"
            title="browse every example run for this star on the xcams Daily Star page">Examples on xcams ↗</a>` : null}
      </div>
      <table class="stdtable"><thead><tr><th>Strat</th>
        ${strats.map((s) => html`<th class=${s === activeStrat ? "col-active" : ""}>${headVid(s)
          ? html`<a href=${headVid(s)} target="_blank" rel="noopener" title="fastest-time video">${s}</a>`
          : s}${editing ? html` <button class="candx" title=${isSeeded(s) ? "clear this strategy's standards" : "delete this strategy"} onclick=${() => delStrat(s)}>×</button>` : ""}</th>`)}</tr></thead>
        <tbody>
        ${RANK_NAMES.filter((r) => r !== "Iron").map((rank) => html`<tr>
          <td style=${`background:${rankColor(rank)};color:#111;font-weight:700`}>${rank}</td>
          ${strats.map((s) => {
            const v = (data.strategies[s] || {})[rank];
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
    ${showAdd ? html`<${StratModal} entity=${entity} existing=${strats}
        onSaved=${async () => { setShowAdd(false); await load(); onChanged && onChanged(); }}
        onClose=${() => setShowAdd(false)} />` : null}
    ${videoEdit ? html`<${Modal} title="Example video" icon="play"
        description=${`${videoEdit.rank} rank · ${videoEdit.strat}`}
        onClose=${videoEdit.saving ? null : () => setVideoEdit(null)}
        footer=${html`
          <button onclick=${() => setVideoEdit(null)} disabled=${videoEdit.saving}>Cancel</button>
          ${videoEdit.url ? html`<button class="danger-text"
              onclick=${() => saveVideo("")} disabled=${videoEdit.saving}>
            <${Icon} name="trash" size=${15} /> Clear video
          </button>` : null}
          <button class="primary-button" onclick=${() => saveVideo()}
              disabled=${videoEdit.saving || !videoEdit.url.trim()}>
            <${Icon} name="save" size=${15} />
            ${videoEdit.saving ? "Saving…" : "Save video"}
          </button>`}>
      <label class="modal-field">
        <span class="field-label">Video URL</span>
        <input type="url" autofocus placeholder="https://…"
            value=${videoEdit.url}
            oninput=${(e) => setVideoEdit({ ...videoEdit, url: e.target.value })} />
        <small>Use a direct video or YouTube URL for this rank example.</small>
      </label>
      ${videoEdit.error ? html`<div class="modal-error">${videoEdit.error}</div>` : null}
    <//>` : null}
  </div>`;
}
