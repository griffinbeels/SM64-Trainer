// src/sm64_events/ui/components/stratmodal.js — the strategy-creation modal:
// name + the full rank ladder (blank time + optional example-video URL per
// rank), Save/Cancel. On a 100-COIN star it also asks which EXIT STAR the run
// ends on, and the server qualifies the stored name with that variant's label
// (spec 2026-08-03-hundred-coin-exit-variants) — which is why `save` uses the
// name the POST RETURNS for every follow-up write instead of the one it sent.
// Picking an ending the community has no times for MINTS a variant, so this
// modal is the whole "define your own 100-coin route" path. Writes ride the EXISTING ranks endpoints in order
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
import { capName } from "./caps.js";
import { Icon } from "./icons.js";

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
  // "Include in ranking" (live request 2026-07-25 round 7): the same
  // exclusion the Rank tab's breakdown toggles, offered where standards are
  // CREATED, because that is the moment you know whether this is something
  // you want rated. Reads !excluded rather than "has standards" — after this
  // save it will have standards, so an exclusion is the only thing that can
  // still keep it out of a rating.
  const [included, setIncluded] = useState(true);
  // [{star_id, name, label}] for a 100-coin star, [] for everything else — the
  // server's own answer, so neither call site has to know which entities have
  // exit stars. `null` exit means "not asked yet / not applicable".
  const [exitOptions, setExitOptions] = useState([]);
  const [exitStar, setExitStar] = useState(null);
  // The server's own grouping, kept only so the duplicate check can compare
  // LEAVES within the chosen variant. Composing the qualified name here to
  // compare it would be a second implementation of the qualification rule.
  const [groups, setGroups] = useState([]);
  // Written only if the user actually touches it. Saving the state back
  // unconditionally would let a failed/absent fetch (the checkbox falling
  // back to its default) silently rewrite an exclusion the user set
  // elsewhere.
  const [includeTouched, setIncludeTouched] = useState(false);
  const nameRef = useRef(null);
  useEffect(() => { nameRef.current && nameRef.current.focus(); }, []);
  useEffect(() => {
    let alive = true;
    getJSON(`/api/ranks/standards?entity=${enc(entity)}`)
      .then((response) => {
        if (!alive) return;
        const options = response.exit_star_options || [];
        setExitOptions(options);
        setGroups(response.strategy_groups || []);
        // Default to the first ending the community already rates, so the
        // common case is one click and the unrated ones stay a deliberate pick.
        const rated = options.find((o) => o.label);
        if (rated) setExitStar(rated.star_id);
      })
      .catch(() => { /* no exit stars is the normal case */ });
    getJSON("/api/marelo/exclusions")
      .then((response) => alive
        && setIncluded(!(response.excluded || []).includes(entity)))
      .catch(() => { /* leave the box at its default; save writes nothing */ });
    return () => { alive = false; };
  }, [entity]);

  async function save() {
    const strat = name.trim();
    if (!strat) { setError("Strategy name required."); return; }
    const takenHere = exitStar === null
      ? (existing || [])
      : ((groups.find((g) => g.exit_star === exitStar) || {}).strategies || [])
          .map((s) => s.leaf);
    if (takenHere.includes(strat)) {
      setError(`"${strat}" already exists here.`); return;
    }
    if (exitOptions.length && exitStar === null) {
      setError("Pick the star this run ends on."); return;
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
      const takenNow = exitStar === null
        ? Object.keys(current.strategies || {})
        : (((current.strategy_groups || [])
            .find((g) => g.exit_star === exitStar) || {}).strategies || [])
            .map((s) => s.leaf);
      if (takenNow.includes(strat)) {
        setError(`"${strat}" already exists here.`); setSaving(false); return;
      }
      const created = await send("POST", `/api/ranks/standards/${enc(entity)}`,
        { strategy: strat, exit_star: exitStar });
      // THE stored name — variant-qualified when an exit star was chosen.
      // Composing it here instead would be a second implementation of the
      // qualification rule, which is exactly what ranks/standards.py owns.
      const stored = (created && created.strategy) || strat;
      for (const rank of LADDER_RANKS) {
        const rawTime = (times[rank] || "").trim();
        if (rawTime !== "") {
          const seconds = parseFloat(rawTime);
          if (!isNaN(seconds)) {
            await send("PUT",
              `/api/ranks/standards/${enc(entity)}/${enc(stored)}/${enc(rank)}`,
              { seconds });
          }
        }
        const url = (videos[rank] || "").trim();
        if (url) {
          await send("PUT",
            `/api/ranks/standards/${enc(entity)}/${enc(stored)}/${enc(rank)}/video`,
            { url });
        }
      }
      // Last, so a failure here can never strand the ladder itself: the
      // standards are the point of this modal, the ranking flag is a
      // preference the breakdown can still toggle.
      if (includeTouched)
        await send("POST", "/api/marelo/exclude", { entity, excluded: !included });
      onSaved(stored);
    } catch (requestError) {
      // Keep the modal open: the strat may exist with partial data — re-Save
      // is safe (idempotent), and the auto-column union means nothing created
      // here can end up invisible.
      setError(String(requestError));
      setSaving(false);
    }
  }

  return html`<${Modal} title="New strategy" icon="practice" size="large"
      description=${exitOptions.length
        ? "Pick the star this run ends on, name the approach, then add any rank times you already know."
        : "Name the approach, then add any rank times or example videos you already know."}
      onClose=${saving ? null : onClose}
      footer=${html`
        <button onclick=${onClose} disabled=${saving}>Cancel</button>
        <button class="primary-button" onclick=${save} disabled=${saving}>
          <${Icon} name="save" size=${16} /> ${saving ? "Saving…" : "Save strategy"}
        </button>`}>
    ${exitOptions.length ? html`<label class="modal-field exit-star-field">
      <span class="field-label">Ends on</span>
      <select value=${exitStar === null ? "" : String(exitStar)}
          onchange=${(changeEvent) => setExitStar(
            changeEvent.target.value === "" ? null
              : Number(changeEvent.target.value))}>
        <option value="">— pick the exit star —</option>
        ${exitOptions.map((option) => html`
          <option value=${String(option.star_id)}>
            ${option.name}${option.label ? "" : "  (no community times)"}
          </option>`)}
      </select>
      <span class="meta">
        A 100-coin run is timed per exit star, so this decides which ladder
        the strategy is graded against.${" "}
        Ending somewhere the community does not rate starts a new variant.
      </span>
    </label>` : null}
    <label class="modal-field strategy-name-field">
      <span class="field-label">Strategy name</span>
      <input class="stratname" placeholder="e.g. Texture setup" value=${name}
          ref=${nameRef} autofocus
          oninput=${(inputEvent) => setName(inputEvent.target.value)} />
    </label>
    <label class="strategy-include-field">
      <input type="checkbox" checked=${included} onchange=${(changeEvent) => {
        setIncluded(changeEvent.target.checked); setIncludeTouched(true);
      }} />
      <span>
        <b>Include in ranking</b>
        <span class="meta">Anything with no rank standards is never rated;
          unticking keeps this one out of MARELO and every route rating even
          once it has them.</span>
      </span>
    </label>
    <div class="strategy-ladder-heading">
      <div>
        <span class="eyebrow">Optional</span>
        <h3>Rank ladder</h3>
      </div>
      <span>Blank ranks can be filled in later.</span>
    </div>
    <div class="strategy-ladder">
      <div class="strategy-ladder-labels">
        <span>Rank</span><span>Time (seconds)</span><span>Example video</span>
      </div>
      ${LADDER_RANKS.map((rank) => html`<div class="strategy-rank-row">
        <span class="strategy-rank-name" title=${`${capName(rank)} · ${rank} on xcams`}
            style=${`--rank-color:${rankColor(rank)}`}>${capName(rank)}</span>
        <label>
          <span class="sr-only">${capName(rank)} time in seconds</span>
          <input type="number" min="0" step="0.01" placeholder="—"
              value=${times[rank] || ""}
              oninput=${(inputEvent) =>
                setTimes({ ...times, [rank]: inputEvent.target.value })} />
        </label>
        <label>
          <span class="sr-only">${capName(rank)} example video URL</span>
          <input type="url" placeholder="https://…" value=${videos[rank] || ""}
              oninput=${(inputEvent) =>
                setVideos({ ...videos, [rank]: inputEvent.target.value })} />
        </label>
      </div>`)}
    </div>
    ${error ? html`<div class="modal-error">${error}</div>` : null}
  <//>`;
}
