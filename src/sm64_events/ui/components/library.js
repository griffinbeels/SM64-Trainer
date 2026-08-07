// src/sm64_events/ui/components/library.js — the Library tab.
//
// Mounted permanently (app.js, the same `display:none` trick Compare uses),
// not remounted per tab switch: "on first activation" (task-3-brief.md step
// 2) only means something if the component has a memory of having already
// done it, and a persistent mount is what lets `intent` from elsewhere in the
// app (Task 5's own job) arrive on an ALREADY-open tab and still navigate.
//
// Two things this component owns, and Task 4 owns a third: browsing (course
// grid -> a group's target grid, librarynav.js) and auto-open (landing on the
// last-practiced entity's target page the moment the tab first opens). The
// TARGET PAGE ITSELF is a placeholder here -- see LibraryTargetPage below --
// task-3-caveats.md point 4 is explicit that `/api/library/target/{index}`
// belongs to Task 4, so this only ever shows what `/api/library/entity/{key}`
// or the course grid's own light summary already carries.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { entityIconSrc, genericStarSrc } from "./entityicons.js";
import { lastPracticed } from "./librarymodel.js";
import { LibraryNav } from "./librarynav.js";
import { Icon } from "./icons.js";

const html = htm.bind(h);

function statusLine(status) {
  if (!status) return "Loading the community sheet…";
  const revision = status.sheet_revision || "unknown revision";
  const source = status.source === "local" ? "your last refresh" : "bundled with the app";
  return `Sheet ${revision} · ${source}`;
}

// All three /api/library/refresh outcomes render inline -- applied, not
// newer, and the 503 a failed fetch raises (task-3-caveats.md point 4: "All
// three outcomes must render"). `state` is null (nothing to say yet),
// "loading", the refresh response body, or {error} from a caught throw.
function RefreshMessage({ state }) {
  if (!state || state === "loading") return null;
  if (state.error)
    return html`<p class="library-refresh-msg is-error">Could not refresh: ${state.error}</p>`;
  if (state.applied)
    return html`<p class="library-refresh-msg is-ok">Updated to ${state.sheet_revision} (${state.targets} targets).</p>`;
  return html`<p class="library-refresh-msg">Already up to date — ${state.reason}</p>`;
}

// The placeholder Task 4 replaces. `entry` is {entityKey, rows} — `rows` is
// whatever the caller already had in hand: the FULL target objects
// `/api/library/entity/{key}` returns for an entity-driven open, or the
// light per-target summary `/api/library` already shipped for a course-grid
// pick on a Castle Movement / stage RTA (neither of which carries an entity
// key to look up by). Both shapes carry `label`/`section`/`miss_reason`,
// which is all this placeholder draws — render the target's LABEL so the
// render test can assert an auto-open actually landed here.
function LibraryTargetPage({ entry, onBack }) {
  const rows = (entry && entry.rows) || [];
  return html`<div class="library-target">
    <button type="button" class="entity-back" onclick=${onBack}>
      <${Icon} name="chevron" size=${15} /> Back
    </button>
    ${rows.length === 0
      ? html`<p class="library-target-empty">No community times recorded here yet.</p>`
      : rows.map((row) => html`<div class="library-target-row" key=${row.index}>
          <h3>${row.label}</h3>
          <p class="library-target-section">${row.section}</p>
          ${row.miss_reason === "castle_movement"
            ? html`<span class="chip">Browse only</span>` : null}
          ${row.miss_reason === "route"
            ? html`<span class="chip">Stage route</span>` : null}
        </div>`)}
  </div>`;
}

/**
 * t            the tracker store
 * active       true while the Library tab is the one on screen
 * intent       null, or {kind:"target", entity, strat?, tier?} / {kind:
 *              "compare", ...} — a caller elsewhere in the app asking the
 *              Library to open on something specific (openLibrary in
 *              app.js). Only the "target" kind is acted on here: it routes
 *              straight to that entity's target page, the same door
 *              auto-open uses, and clears itself so it fires once. `strat`/
 *              `tier` ride along unconsumed — Task 5's own job.
 * clearIntent  () => void, called once an intent has been acted on
 */
export function Library({ t, active, intent, clearIntent }) {
  const [index, setIndex] = useState(null);
  const [status, setStatus] = useState(null);
  const [stage, setStage] = useState("browse");   // "browse" | "target"
  const [entry, setEntry] = useState(null);        // the LibraryTargetPage's own data
  const [refreshState, setRefreshState] = useState(null);
  // Whether auto-open has already run once for this mount. A ref, not state
  // -- flipping it must never itself trigger a re-render, only gate the next
  // one. Persistent-mount (see the module comment) is what makes "once" mean
  // "once per time the user opens this tab today", not "once per click".
  const autoOpenedRef = useRef(false);

  const iconFor = (entityKey) =>
    entityKey ? entityIconSrc(t, entityKey) : genericStarSrc();

  useEffect(() => {
    getJSON("/api/library").then(setIndex).catch(() => {});
    getJSON("/api/library/status").then(setStatus).catch(() => {});
  }, []);

  function openEntity(entityKey) {
    return getJSON(`/api/library/entity/${encodeURIComponent(entityKey)}`)
      .then((data) => {
        setEntry({ entityKey, rows: data.targets || [] });
        setStage("target");
      });
  }

  // The course grid hands back an entity key when a target has one, else its
  // numeric index into the index we already fetched (librarynav.js's own
  // contract) -- so the index-branch never calls /api/library/target/{index}
  // (Task 4's door), it just looks the row up in what LibraryNav is already
  // showing.
  function handlePick(value) {
    if (typeof value === "string") { openEntity(value); return; }
    const row = (index ? index.groups : [])
      .flatMap((group) => group.targets)
      .find((target) => target.index === value);
    if (row) { setEntry({ entityKey: null, rows: [row] }); setStage("target"); }
  }

  function backToBrowse() { setStage("browse"); setEntry(null); }

  // Runs on every activation, not gated on `!active` staying false — an
  // intent may arrive on a tab that is ALREADY open (a click from elsewhere
  // while Library is on screen), and it must still navigate. Auto-open,
  // below, is the one gated to "once".
  useEffect(() => {
    if (!active || !intent) return;
    if (intent.kind !== "target") return;
    autoOpenedRef.current = true;
    openEntity(intent.entity);
    clearIntent();
  }, [active, intent]);

  // First activation with no intent in hand: land on whatever was last
  // practiced (librarymodel.js::lastPracticed), same as a bookmark that
  // follows the player. `null` is the empty-log case (task-3-caveats.md
  // point 3, no attempts recorded anywhere yet) and stays on the course
  // grid, same as `stage`'s own initial value -- there is nothing to open.
  useEffect(() => {
    if (!active || autoOpenedRef.current) return;
    autoOpenedRef.current = true;
    const key = lastPracticed(t.view);
    if (key) openEntity(key);
  }, [active]);

  function refresh() {
    setRefreshState("loading");
    send("POST", "/api/library/refresh")
      .then((result) => {
        setRefreshState(result);
        if (result.applied) {
          getJSON("/api/library").then(setIndex).catch(() => {});
          getJSON("/api/library/status").then(setStatus).catch(() => {});
        }
      })
      .catch((err) => setRefreshState({ error: err.message }));
  }

  return html`<div class="practice-card workshop-card library-page">
    <div class="workshop-hero">
      <div class="workshop-title">
        <span class="workshop-title-icon"><${Icon} name="library" size=${22} /></span>
        <div>
          <h2>Library</h2>
          <p>${statusLine(status)}</p>
        </div>
      </div>
      <button type="button" class="primary-button" onclick=${refresh}
          disabled=${refreshState === "loading"}>
        <${Icon} name="restart" size=${15} />
        ${refreshState === "loading" ? "Refreshing…" : "Refresh"}
      </button>
    </div>
    <${RefreshMessage} state=${refreshState} />
    ${stage === "target"
      ? html`<${LibraryTargetPage} entry=${entry} onBack=${backToBrowse} />`
      : html`<${LibraryNav} index=${index} onPick=${handlePick} iconFor=${iconFor} />`}
  </div>`;
}
