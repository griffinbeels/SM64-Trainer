// src/sm64_events/ui/components/standards.js — collapsible, view-by-default
// rank-standards table for one entity (star:c:s or segment:id). Each cutoff time
// links to the fastest example video that RANKS that tier (server-resolved
// cutoff_videos: auto band from xcams clips + the user's per-cell overrides); the
// strat header links to the Mario-row video (= the overall fastest). Edit mode
// adds a ▶ button per cell to paste/clear an override, and the section links out
// to the xcams Daily Star page for browsing every example.
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import { Disclose } from "./collapsible.js";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { fmtIgtShort, fmtSeconds } from "../format.js";
import { TimeFields } from "./timefields.js";
import { ceilingOf, slowestFirst } from "../ladderorder.js";
import { RANK_NAMES, rankColor } from "./ranks.js";
import { capName, capGradient } from "./caps.js";
import { StratModal } from "./stratmodal.js";
import { Modal } from "./modal.js";
import { Icon } from "./icons.js";
const html = htm.bind(h);
const enc = encodeURIComponent;

// Each exit-star variant gets its OWN hue from the site palette, so the
// columns under one heading read as one group instead of as a run of
// strategies that happen to sit near each other (user, 2026-08-03: "better
// delineate which columns belong to which overarching exit star strategy").
// THE single owner of the cycle -- the stylesheet never names a band colour,
// it only reads the `--std-band` this hands it, so adding a hue is a row here.
// Six is one more than any course can have exit stars, so a repeat needs the
// unfiled-strategy "Other" band on a fully-defined course; even then two bands
// in one hue cannot be ADJACENT until there are ten of them.
const BAND_TINTS = ["--blue", "--gold", "--green", "--coral", "--violet", "--caveat"];

// Your time is essentially never AT a cutoff, so "you are here" is not a
// cell: it is a point BETWEEN two rows in one column. This returns that
// point as a 0..1 fraction of the gap between the two cutoffs it falls
// between, which is also exactly your division within the tier.
export function markerPosition(ladderSeconds, timeCs) {
  const rows = Object.entries(ladderSeconds)
    .map(([tier, seconds]) => [tier, Math.round(seconds * 100)])
    .sort((left, right) => left[1] - right[1]);               // fastest first
  if (!rows.length || timeCs == null) return null;
  if (timeCs <= rows[0][1]) return { above: null, below: rows[0][0], frac: 1 };
  for (let index = 0; index < rows.length - 1; index += 1) {
    const [fastTier, fastCs] = rows[index], [slowTier, slowCs] = rows[index + 1];
    if (timeCs <= slowCs) {
      const span = slowCs - fastCs;
      return { above: fastTier, below: slowTier,
        frac: span > 0 ? (slowCs - timeCs) / span : 1 };
    }
  }
  return { above: rows[rows.length - 1][0], below: null, frac: 0 };
}

// score is NOT computed here. It comes from sectionRank.score, which
// _section_banner (tracking/views.py) already grades against the active
// strategy's own ladder via ranks/scoring.py::score_for -- a JS copy of that
// curve would silently disagree the next time the Python side changes (the
// Iron tail moved 2026-07-25; a standards.js copy would have drifted).

const fmtScore = (score) => (score == null ? "—" : score.toFixed(1));

// Deliberate mirror of ranks/classify.py::display_cs (keep the two in
// lockstep, same as fmtIgt mirrors core/timefmt.py in format.js): frames ->
// DISPLAYED centiseconds (30fps quantized), so the marker's position never
// disagrees with the time fmtIgt shows for the same frame count (project
// rule 7 / Usamune IGT clock). This is pure quantization arithmetic, not a
// curve -- unlike the score, it has had no reason to drift.
function displayCs(frames) {
  return Math.floor(frames / 30) * 100 + Math.floor(((frames % 30) * 100) / 30);
}

// A Bowser Reds star and its paired seg:reds->pipe:<abbrev> segment share
// ONE rank-standards entity (the star's -- views.py's _reds_pipe_segments)
// but must never show each other's half: `family` ("Star" | "Pipe" | null)
// filters the COLUMN list to names ending " (<family>)" -- the fetched
// `data.strategies` is the community store's raw entity data (every
// strategy on that entity, both families mixed), unlike `sec.strategies`,
// which views.py already family-filters server-side for the session view.
// A plain string suffix check, not a second "which times count" resolver
// (that stays server-side, grading_basis/valid_frames) -- there is nothing
// here to disagree with.
function inFamily(name, family) {
  return !family || name.endsWith(` (${family})`);
}

export function StandardsPanel({ entity, activeStrat, strategies, onChanged,
    defaultOpen = false, sectionRank = null, sectionPb = null, family = null }) {
  const [open, setOpen] = useState(defaultOpen);
  const [data, setData] = useState(null);
  const [editing, setEditing] = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const [videoEdit, setVideoEdit] = useState(null);
  async function load() { setData(await getJSON(`/api/ranks/standards?entity=${enc(entity)}`)); }
  // When opened by default (or when the card remounts for a new entity while
  // open), fetch on mount — toggle() only loads on a user click, so an
  // open-by-default panel would otherwise sit on "Loading standards…" forever.
  // PREFETCHED THE MOMENT THE PANEL EXISTS, not on first open. Griffin,
  // 2026-08-06: "when opening the rank standards, it seems like it loads the
  // rank standards from disk (or a cold cache, whatever), which causes there
  // to be a brief lag before the rank standards loads -- then, every
  // open/close going forward looks as expected. I think the second the card
  // appears in the list, we should load those rank standards and prepare them
  // to be opened later."
  //
  // The panel used to fetch on open, which was a deliberate "no cost per card"
  // when it moved inside the cards -- and the cost it avoided was invisible
  // while the cost it created was not: the FIRST open of each panel animated
  // a box whose contents arrived mid-flight, so the height it measured was
  // the loading state's and the table appeared afterwards. Every later open
  // looked right, which is exactly the shape of a cold-cache stall.
  //
  // `load()` is idempotent and this component is only mounted for cards the
  // log is actually rendering, so the traffic is one request per visible card
  // per entity change, not per card in the corpus.
  useEffect(() => { load(); }, [entity]);
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
  const allStrats = data
    ? [...Object.keys(data.strategies).filter((s) => inFamily(s, family)),
       ...(strategies || []).filter((s) => inFamily(s, family)
         && !Object.hasOwn(data.strategies, s))]
    : [];
  // A 100-coin star is timed separately per EXIT star, so its columns are
  // BANDED by variant with the leaf name in the column head — "Standard"
  // under "100c + Race" (spec 2026-08-03-hundred-coin-exit-variants). Bands
  // come from the server's own `strategy_groups`; nothing here works out which
  // variant a strategy belongs to. `bands` is empty for every ordinary entity
  // and the table renders exactly as it did before.
  const bands = ((data && data.strategy_groups) || [])
    .map((group) => ({
      label: group.label,
      names: group.strategies.map((s) => s.name).filter((s) => allStrats.includes(s)),
    }))
    .filter((band) => band.names.length);
  const banded = new Set(bands.flatMap((band) => band.names));
  const loose = allStrats.filter((s) => !banded.has(s));
  if (bands.length && loose.length) bands.push({ label: "Other", names: loose });
  // SLOWEST on the left, FASTEST on the right — the table is a PATH, read
  // bottom-left to top-right as you improve (user, 2026-08-03; the rule and
  // its tie-breaks live in ui/ladderorder.js). Applied WITHIN each exit-star
  // band, and to the bands themselves by their own fastest column, so the
  // progression still reads left-to-right across a banded 100-coin table
  // without a column leaving its heading.
  const ladders = (data && data.strategies) || {};
  for (const band of bands) band.names = slowestFirst(band.names, ladders);
  bands.sort((a, b) => {
    const fastest = (band) => Math.min(
      ...band.names.map((name) => ceilingOf(ladders[name])));
    const one = fastest(a), other = fastest(b);
    return one === other ? 0 : other - one;
  });
  // Banded order, so a column always sits under its own heading.
  const strats = bands.length
    ? bands.flatMap((band) => band.names)
    : slowestFirst(allStrats, ladders);
  // Colour is assigned AFTER the bands are ordered, so the leftmost band is
  // always the first hue and the table looks the same every time it opens.
  bands.forEach((band, index) => {
    band.tint = `var(${BAND_TINTS[index % BAND_TINTS.length]})`;
  });
  const tintOf = new Map(bands.flatMap(
    (band) => band.names.map((name) => [name, band.tint])));
  // Where one band ENDS and the next begins: the wash says which columns
  // belong together, this says where the boundary is, and it runs the full
  // height of the table rather than stopping at the heading.
  const bandStart = new Set(bands.map((band) => band.names[0]));
  const bandClass = (strat, ...rest) => [...rest,
    bandStart.has(strat) ? "std-band-start" : ""].filter(Boolean).join(" ");
  const bandStyle = (strat) => (tintOf.has(strat)
    ? `--std-band:${tintOf.get(strat)}` : null);
  const leafOf = new Map(((data && data.strategy_groups) || [])
    .flatMap((group) => group.strategies.map((s) => [s.name, s.leaf])));
  const colHead = (strat) => leafOf.get(strat) || strat;
  // "You are here": the grading basis under the ACTIVE strategy. Avg rank
  // modes carry it on sectionRank.basis; pb mode carries none (the same
  // split _section_banner already encodes server-side), so it falls back to
  // the saved PB row for this entity's clock. Interpolated against THIS
  // strategy's own ladder (the column actually on screen) rather than the
  // entity's best-possible ladder, so the marker's bracketed cutoffs can
  // never disagree with the rows it sits between.
  const activeLadder = data && activeStrat ? (data.strategies[activeStrat] || {}) : {};
  const basisFrames = data && sectionRank && sectionRank.basis
    ? sectionRank.basis.frames
    : (data && sectionPb && sectionPb[data.clock] ? sectionPb[data.clock].frames : null);
  const timeCs = basisFrames != null ? displayCs(basisFrames) : null;
  const hasActiveLadder = Object.keys(activeLadder).length > 0;
  const marker = timeCs != null && hasActiveLadder ? markerPosition(activeLadder, timeCs) : null;
  // The server-graded score for the active strategy's own ladder, or absent
  // when sectionRank is one of _section_banner's sentinel states (no_strat /
  // no_ladder / unranked -- e.g. pb mode with no saved PB on THIS strategy,
  // even while the basisFrames fallback above still finds an entity-wide PB
  // to position the marker with). No client-side fallback curve: a missing
  // score is a real state to show honestly, not something to paper over.
  const entityScore = sectionRank && sectionRank.score != null ? sectionRank.score : null;
  return html`<div class="stdpanel">
    <button class="disc standards-toggle" onclick=${toggle} aria-expanded=${open}>
      <${Icon} name="rank" size=${16} />
      <span>Rank standards</span>
      ${activeStrat ? html`<span class="meta"> · active: ${activeStrat}</span>` : null}
      <${Icon} name="chevron" size=${16} className="standards-chevron" />
    </button>
    <${Disclose} open=${open} className="stdpanel-disclose">
    ${!data ? html`<div class="stdbody"><div class="inline-state loading">
      <${Icon} name="updates" size=${16} /> Loading standards…
    </div></div>` : null}
    ${data ? html`<div class="stdbody">
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
      <table class="stdtable"><thead>
        ${bands.length ? html`<tr class="std-variant-row"><th></th>
          ${bands.map((band) => html`<th class="std-variant std-band-start"
              colspan=${band.names.length} style=${`--std-band:${band.tint}`}
              title="the star this 100-coin run ends on">${band.label}</th>`)}</tr>` : null}
        <tr><th>Strat</th>
        ${strats.map((strat) => html`<th
          class=${bandClass(strat, tintOf.has(strat) ? "std-banded" : "",
            strat === activeStrat ? "col-active" : "")}
          style=${bandStyle(strat)}>${headVid(strat)
          ? html`<a href=${headVid(strat)} target="_blank" rel="noopener" title="fastest-time video">${colHead(strat)}</a>`
          : colHead(strat)}${editing ? html` <button class="candx" title=${isSeeded(strat) ? "clear this strategy's standards" : "delete this strategy"} onclick=${() => delStrat(strat)}>×</button>` : ""}
          ${marker && strat === activeStrat ? html`<span class="std-you-badge"
              title="your current time and score on this ladder">◀ you · ${fmtIgtShort(basisFrames)}${entityScore != null ? ` · ${fmtScore(entityScore)}` : ""}</span>` : ""}</th>`)}</tr></thead>
        <tbody>
        ${RANK_NAMES.filter((r) => r !== "Iron").map((rank) => html`<tr>
          <!-- Large flat surface -> the tier's own gradient where it has
               one (Toadsworth/Toad, addendum 2, 2026-07-25): a flat fill
               here is a lie for a cap that's actually two-tone, and a
               white slab (old Toad) was the live complaint that started
               this. capGradient falls back to null for a flat tier. -->
          <td style=${`background:${capGradient(rank) || rankColor(rank)};color:#111;font-weight:700`}
              title=${`${capName(rank)} · ${rank} on xcams`}>${capName(rank)}</td>
          ${strats.map((strat) => {
            const v = (data.strategies[strat] || {})[rank];
            const vid = cutoffVid(strat, rank);
            // Every rank standard reads in the Usamune display format the practice
            // log and every PB already use -- 1'21"32, and 23"00 under a minute
            // (user, 2026-08-03: "This is important because that matches the
            // format we actually display in the practice log"). Raw seconds
            // beside a formatted PB is two vocabularies for one quantity.
            const label = v != null ? fmtSeconds(v) : "—";
            // "You are here" — a cell highlight would be a lie (your time is
            // essentially never AT a cutoff), so instead the two cutoffs
            // bracketing your interpolated position get their own mark, and
            // every EASIER tier below that (a cutoff you are, by definition,
            // already running faster than) reads as already-beaten.
            const isBracket = marker && strat === activeStrat
              && (rank === marker.above || rank === marker.below);
            const beaten = marker && marker.below && strat === activeStrat
              && RANK_NAMES.indexOf(rank) > RANK_NAMES.indexOf(marker.below);
            const cellClass = bandClass(strat,
              strat === activeStrat ? "col-active" : "",
              isBracket ? "std-marker-bracket" : (beaten ? "std-beaten" : ""));
            return html`<td class=${cellClass} style=${bandStyle(strat)}>
              ${editing
                ? html`<span class="stdcell"><${TimeFields} seconds=${v} compact
                      label=${`${capName(rank)} ${strat}`}
                      onCommit=${(next) => { if (next != null) put(strat, rank, next); }} />
                    <button class="vidbtn" title=${`${userVid(strat, rank) ? "edit" : "add"} ${capName(rank)} example video`}
                      onclick=${() => editVideo(strat, rank)}>${userVid(strat, rank) ? "▶✎" : "▶＋"}</button></span>`
                : (vid
                    ? html`<a href=${vid} target="_blank" rel="noopener" title=${`example ${capName(rank)} run`}>${label}</a>`
                    : label)}</td>`;
          })}</tr>`)}
        </tbody></table>
    </div>` : null}
    <//>
    ${showAdd ? html`<${StratModal} entity=${entity} existing=${strats}
        onSaved=${async () => { setShowAdd(false); await load(); onChanged && onChanged(); }}
        onClose=${() => setShowAdd(false)} />` : null}
    ${videoEdit ? html`<${Modal} title="Example video" icon="play"
        description=${`${capName(videoEdit.rank)} rank · ${videoEdit.strat}`}
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
