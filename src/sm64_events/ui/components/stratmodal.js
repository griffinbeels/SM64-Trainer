// src/sm64_events/ui/components/stratmodal.js — the strategy-creation modal:
// name + the full rank ladder (blank time + optional example-video URL per
// rank), Save/Cancel. Writes ride the EXISTING ranks endpoints in order
// (create → PUT each filled threshold → PUT each filled video), so a partial
// failure leaves a valid strat and re-Save is idempotent (create no-ops,
// PUTs overwrite). Callers own what happens after save (set active / reload)
// via onSaved; Cancel/Esc/backdrop write nothing.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { Modal } from "./modal.js";
import { RANK_NAMES, rankColor } from "./ranks.js";

const html = htm.bind(h);
const enc = encodeURIComponent;
// Iron is the implicit floor everywhere (classify.py) — never a ladder row.
const LADDER_RANKS = RANK_NAMES.filter((rank) => rank !== "Iron");

export function StratModal({ entity, existing, onSaved, onClose }) {
  const [name, setName] = useState("");
  const [times, setTimes] = useState({});     // rank -> raw input string
  const [videos, setVideos] = useState({});   // rank -> raw input string
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const nameRef = useRef(null);
  useEffect(() => { nameRef.current && nameRef.current.focus(); }, []);

  async function save() {
    const strat = name.trim();
    if (!strat) { setError("Strategy name required."); return; }
    if ((existing || []).includes(strat)) {
      setError(`"${strat}" already exists here.`); return;
    }
    // Dropdown sentinels ("+ new strat…" option values) — a strat with this
    // literal name could never be selected, only re-open the modal.
    if (strat === "__new" || strat === "__new__") {
      setError(`"${strat}" is a reserved name.`); return;
    }
    // "/" or "\" would split the REST path after percent-decoding — the
    // create POST (name travels in the body) succeeds but every per-strat
    // PUT 404s, stranding the strat with no data.
    if (/[/\\]/.test(strat)) {
      setError("Strategy names can't contain slashes."); return;
    }
    setSaving(true); setError(null);
    try {
      // Server-truth duplicate check: `existing` (the caller's list) can be
      // narrower than the store — the header picker passes registered-only
      // strats, which would let a community-seeded name slip through and
      // silently overwrite its times.
      const current = await getJSON(`/api/ranks/standards?entity=${enc(entity)}`);
      if (Object.hasOwn(current.strategies || {}, strat)) {
        setError(`"${strat}" already exists here.`); setSaving(false); return;
      }
      await send("POST", `/api/ranks/standards/${enc(entity)}`, { strategy: strat });
      for (const rank of LADDER_RANKS) {
        const rawTime = (times[rank] || "").trim();
        if (rawTime !== "") {
          const seconds = parseFloat(rawTime);
          if (!isNaN(seconds)) {
            await send("PUT",
              `/api/ranks/standards/${enc(entity)}/${enc(strat)}/${enc(rank)}`,
              { seconds });
          }
        }
        const url = (videos[rank] || "").trim();
        if (url) {
          await send("PUT",
            `/api/ranks/standards/${enc(entity)}/${enc(strat)}/${enc(rank)}/video`,
            { url });
        }
      }
      onSaved(strat);
    } catch (requestError) {
      // Keep the modal open: the strat may exist with partial data — re-Save
      // is safe (idempotent), and the auto-column union means nothing created
      // here can end up invisible.
      setError(String(requestError));
      setSaving(false);
    }
  }

  return html`<${Modal} title="New strategy" onClose=${saving ? null : onClose}
      footer=${html`
        <button onclick=${save} disabled=${saving}>${saving ? "Saving…" : "Save"}</button>
        <button onclick=${onClose} disabled=${saving}>Cancel</button>`}>
    <input class="stratname" placeholder="strategy name" value=${name} ref=${nameRef}
           oninput=${(inputEvent) => setName(inputEvent.target.value)} />
    <div class="meta" style="margin:.4rem 0 .2rem">
      Rank standards — optional; leave blank and no rank is awarded until times are entered.
    </div>
    <table class="stdtable">
      <thead><tr><th>Rank</th><th>Time (s)</th><th>Example video (optional)</th></tr></thead>
      <tbody>
      ${LADDER_RANKS.map((rank) => html`<tr>
        <td style=${`background:${rankColor(rank)};color:#111;font-weight:700`}>${rank}</td>
        <td><input class="stdinp" placeholder="—" value=${times[rank] || ""}
              oninput=${(inputEvent) =>
                setTimes({ ...times, [rank]: inputEvent.target.value })} /></td>
        <td><input class="stdvid" placeholder="https://…" value=${videos[rank] || ""}
              oninput=${(inputEvent) =>
                setVideos({ ...videos, [rank]: inputEvent.target.value })} /></td>
      </tr>`)}
      </tbody>
    </table>
    ${error ? html`<div class="modal-error">${error}</div>` : null}
  <//>`;
}
