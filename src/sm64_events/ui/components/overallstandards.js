// Overall uses the server's full compiled curve; strategy tables keep their own ladders.
// Preserve the user's 2026-08-10 layout: Capless first, then every cap with
// five indented divisions. The browser performs no statistical fitting.
import { h } from "preact";
import { useRef, useState } from "preact/hooks";
import { useIdentityFetch } from "../refetch.js";
import htm from "htm";
import { Disclose } from "./collapsible.js";
import { Icon } from "./icons.js";
import { RankIcon } from "./rankicon.js";
import { TimeFields } from "./timefields.js";
import { getJSON, send } from "../api.js";
import { nounOfKey } from "../entitysection.js";
import { capName, divisionDigit } from "./caps.js";
import {
  overallCurveOf, curveBands, curveStandingOn, bandRangeLabel, divisionRangeLabel,
} from "./librarymodel.js";

const html = htm.bind(h);
const enc = encodeURIComponent;

function useOverallStandards(entity, version, standardsRevision) {
  const identity = entity + ":" + version;
  const [loaded, setLoaded] = useState(null);
  const [failure, setFailure] = useState(null);
  const [localRevision, setLocalRevision] = useState(0);
  // Fetch before opening; keep the current answer during same-target refresh.
  // Identity-tagged answers prevent one stale frame on a JP/US switch.
  useIdentityFetch(identity, standardsRevision + ":" + localRevision, (cleared) => {
    if (!entity) { setLoaded(null); setFailure(null); return undefined; }
    let cancelled = false;
    if (cleared) setLoaded(null);
    setFailure(null);
    const query = version ? "&version=" + enc(version) : "";
    getJSON("/api/ranks/standards?entity=" + enc(entity) + query)
      .then((data) => { if (!cancelled) setLoaded({ identity, data }); })
      .catch(() => { if (!cancelled) setFailure(identity); });
    return () => { cancelled = true; };
  });
  return { data: loaded && loaded.identity === identity ? loaded.data : null,
    failed: failure === identity, reload: () => setLocalRevision((value) => value + 1) };
}

function readOverall(data, pbCs) {
  if (!data) return { bands: [], you: null, curve: null };
  try {
    const curve = overallCurveOf(data);
    return { curve, bands: curveBands(curve), you: curveStandingOn(curve, pbCs) };
  } catch {
    return { bands: [], you: null, curve: null,
      error: "These Overall standards could not be read. Refresh the data or update the app." };
  }
}

function OverallDivision({ band, division, you, nextStep }) {
  const key = band.tier + "/" + division.numeral;
  const isYou = you && you.rank === band.tier && you.division === division.numeral;
  const isNext = key === nextStep;
  const className = "library-overall-division" + (isYou ? " is-you" : "") + (isNext ? " is-next" : "");
  return html`<div class=${className}>
    <span class="library-overall-division-label">
      <span class="rank-icon-slot" style="--icon-size: 16px">
        <${RankIcon} tier=${band.tier} division=${division.numeral} size=${16} /></span>
      ${" "}${capName(band.tier)}${" "}${divisionDigit(division.numeral)}
    </span>
    <span class="meta">${divisionRangeLabel(division)}</span>
    ${isNext ? html`<span class="chip library-overall-next">next</span>` : ""}
    ${isYou ? html`<span class="chip library-overall-you">◀ you</span>` : ""}
  </div>`;
}

function CutoffEditor({ band, busy, error, onSave, onCancel }) {
  const [seconds, setSeconds] = useState(band.cutoffCs / 100);
  const latest = useRef(seconds);
  const commit = (value) => { latest.current = value; setSeconds(value); };
  function submit(event) {
    event.preventDefault();
    // Enter must commit the focused TimeFields box before reading the draft.
    const focused = event.currentTarget.querySelector("input:focus");
    if (focused) focused.blur();
    onSave(band.tier, latest.current);
  }
  return html`<form class="library-overall-band-head" onsubmit=${submit}>
    <span>Fixed ${capName(band.tier)} cutoff:</span>
    ${busy ? html`<span role="status">Saving…</span>` : html`
      <${TimeFields} seconds=${seconds} compact=${true}
        label=${"Overall " + capName(band.tier) + " cutoff"} onCommit=${commit} />`}
    <button type="submit" disabled=${busy}>Save cutoff</button>
    <button type="button" disabled=${busy} onclick=${onCancel}>Cancel</button>
    ${error ? html`<span role="alert">${error}</span>` : null}
  </form>`;
}

function OverallBand({ band, you, nextStep, pinned, editing, busy, error, onEdit, onSave, onCancel }) {
  const className = "library-overall-band-head" + (you && you.rank === band.tier ? " is-you" : "");
  return html`<div class="library-overall-band" data-tier=${band.tier}>
    <div class=${className}>
      <span class="rank-icon-slot" style="--icon-size: 18px">
        <${RankIcon} tier=${band.tier} division=${"I"} size=${18} /></span>
      ${" "}<b>${capName(band.tier)}</b>
      <span class="meta library-overall-range">${bandRangeLabel(band)}</span>
      ${band.cutoffCs != null ? html`<span class="library-overall-by">
        ${pinned ? html`<span class="meta">Fixed cutoff${" "}</span>` : null}
        <button type="button" disabled=${busy} aria-expanded=${editing}
          aria-label=${"Edit Overall " + capName(band.tier) + " cutoff"}
          onclick=${() => onEdit(band.tier)}>Edit cutoff</button>
      </span>` : null}
    </div>
    <div class="library-overall-divisions">
      ${editing ? html`<${CutoffEditor} band=${band} busy=${busy} error=${error}
        onSave=${onSave} onCancel=${onCancel} />` : null}
      ${(band.divisions || []).map((division) => html`<${OverallDivision}
        key=${division.numeral} band=${band} division=${division} you=${you}
        nextStep=${nextStep} />`)}
    </div>
  </div>`;
}

function counted(count, singular, plural = singular + "s") {
  return count + " " + (count === 1 ? singular : plural);
}

function OverallBasis({ curve }) {
  const metadata = curve.metadata || {};
  const adjustments = metadata.anchor_adjustments || {};
  const provisional = (metadata.families || []).filter((family) => family.provisional).length;
  const legacy = curve.interpolation === "legacy";
  return html`<div>
    <p class="library-overall-note">${legacy
      ? "Legacy standards: these saved cutoffs keep their previous scoring behavior."
      : "Overall combines community standing with progression across strategy families."}
      ${" "}Fixed cutoffs stay where you set them. Other cutoffs can evolve as compatible
      community times arrive, even when your saved time stays the same.
      Strategy cutoff edits are independent of Overall.
    </p>
    ${metadata.population_count != null ? html`<p class="library-overall-note meta">
      ${counted(metadata.population_count, "eligible Sheet runner")}
      ${metadata.family_count != null
        ? " · " + counted(metadata.family_count, "strategy family", "strategy families") : ""}.
      ${metadata.estimated ? " Provisional standards use estimated source times." : ""}
      ${provisional ? " Limited evidence in " + counted(provisional, "provisional family", "provisional families") + "." : ""}
    </p>` : null}
    ${adjustments.fallback_reason ? html`<p class="library-overall-note">
      Your fixed cutoffs are preserved using legacy scoring. They do not fit
      the current curve's whole-frame constraints.
      ${adjustments.unreachable_divisions && adjustments.unreachable_divisions.length
        ? " " + counted(adjustments.unreachable_divisions.length, "division") + " cannot be reached." : ""}
      Reset Overall cutoffs to use the current community curve.
    </p>` : null}
    ${!legacy && Object.keys(adjustments.moved_automatic_cs || {}).length ? html`
      <p class="library-overall-note meta">Automatic cutoffs were adjusted around
        your fixed times to keep every division reachable.</p>` : null}
  </div>`;
}

function useCutoffEdits(entity, version, reload) {
  const [editing, setEditing] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const working = useRef(new Set());
  async function change(rank, seconds) {
    if (working.current.has("save")) return;
    if (rank && !(Number.isFinite(seconds) && seconds > 0)) {
      setError("Enter a positive cutoff time."); return;
    }
    working.current.add("save");
    setBusy(true); setError(null);
    const path = "/api/ranks/overall/" + enc(entity);
    const url = (rank ? path + "/" + enc(rank) : path) + "?version=" + enc(version);
    try {
      await send(rank ? "PUT" : "DELETE", url, rank ? { seconds } : undefined);
      setEditing(null); reload();
    } catch (failure) {
      setError(failure.message || "Could not save Overall cutoffs.");
    } finally {
      working.current.delete("save"); setBusy(false);
    }
  }
  const edit = (tier) => { setError(null); setEditing(tier); };
  return { editing, busy, error, edit, change, cancel: () => edit(null) };
}

function OverallContents({ entity, version, data, model, pbCs, reload }) {
  const edits = useCutoffEdits(entity, version, reload);
  const pins = data.overall_overrides || {};
  const pinnedCount = Object.keys(pins).length;
  const { curve, bands, you } = model;
  const nextStep = you && you.next_tier ? you.next_tier + "/" + you.next_division : null;
  return html`
    <${OverallBasis} curve=${curve} />
    ${pinnedCount ? html`<div class="library-overall-band-head">
      <span class="meta">${counted(pinnedCount, "fixed Overall cutoff")} (${version.toUpperCase()})</span>
      <button type="button" disabled=${edits.busy} onclick=${() => edits.change(null)}>
        Reset Overall cutoffs</button>
    </div>` : null}
    ${edits.error && !edits.editing ? html`<p class="library-overall-note" role="alert">${edits.error}</p>` : null}
    <div class="library-overall-ladder">
      ${bands.map((band) => html`<${OverallBand} key=${"overall-" + band.tier}
        band=${band} you=${you} nextStep=${nextStep}
        pinned=${pins[band.tier] != null} editing=${edits.editing === band.tier}
        busy=${edits.busy} error=${edits.error}
        onEdit=${edits.edit} onSave=${edits.change} onCancel=${edits.cancel} />`)}
    </div>
    ${pbCs == null ? html`<p class="library-overall-note meta">
      No saved time here yet, so you sit at the capless floor — the top row is
      where you are, and the next row is your first goal.
    </p>` : null}
  `;
}

function OverallState({ entity, label, failed, data, model }) {
  if (!entity) return html`<p class="library-overall-note">
    Nothing grades this target yet — it is a castle movement with no
    segment of its own. Link one above and the standards appear here.
  </p>`;
  if (failed) return html`<p class="library-overall-note" role="alert">
    Could not refresh the standards for ${label || entity}.
  </p>`;
  if (!data) return html`<div class="inline-state loading">
    <${Icon} name="updates" size=${16} />${" "}Loading standards…
  </div>`;
  if (model.error) return html`<p class="library-overall-note" role="alert">${model.error}</p>`;
  if (!model.bands.length) return html`<p class="library-overall-note">
    ${label || entity}${" "}has no published rank standards, so it carries no overall rank yet.
  </p>`;
  return null;
}

/** One target and displayed version, across all strategies; pbCs may be null. */
export function OverallStandards({ entity, label, pbCs = null, version = null,
    standardsRevision = 0 }) {
  const [open, setOpen] = useState(false);
  const { data, failed, reload } = useOverallStandards(entity, version, standardsRevision);
  const model = readOverall(data, pbCs);
  const resolvedVersion = version || (data && data.version) || "us";
  return html`<div class="library-overall">
    <button type="button" class="disc library-overall-toggle"
        aria-expanded=${open} onclick=${() => setOpen(!open)}>
      <${Icon} name="rank" size=${16} />
      <span>Overall Rank Standards</span>
      <span class="meta">${" "}· ${entity
        ? "what the " + nounOfKey(entity) + " itself grades on, across every strategy"
        : "nothing grades this target yet"}</span>
      <${Icon} name="chevron" size=${16} className="library-overall-chevron" />
    </button>
    <${Disclose} open=${open} className="library-overall-disclose">
      <div class="library-overall-body">
        <${OverallState} entity=${entity} label=${label} failed=${failed}
          data=${data} model=${model} />
        ${model.bands.length ? html`<${OverallContents} key=${entity + ":" + resolvedVersion}
          entity=${entity} version=${resolvedVersion} data=${data} model=${model}
          pbCs=${pbCs} reload=${reload} />` : null}
      </div>
    <//>
  </div>`;
}
