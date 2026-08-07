// src/sm64_events/ui/components/library.js — the Library tab.
//
// Mounted permanently (app.js, the same `display:none` trick Compare uses),
// not remounted per tab switch: "on first activation" (task-3-brief.md step
// 2) only means something if the component has a memory of having already
// done it, and a persistent mount is what lets `intent` from elsewhere in the
// app (Task 5's own job) arrive on an ALREADY-open tab and still navigate.
//
// Three things this component owns: browsing (course grid -> a group's
// target grid, librarynav.js), auto-open (landing on the last-practiced
// entity's target page the moment the tab first opens), and the plumbing the
// real target page (librarytarget.js, Task 4) needs but does not own itself
// -- resolving BOTH doors to one full-target shape, and the comparison tray's
// STATE (librarytray.js, Task 5, owns its rendering -- the chips, the trim
// editors, the grid overlay; this file just holds the array and the three
// mutations "+"/trim/remove make, so it survives navigating to a different
// target the way "tray state lives here" asked for).
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { entityIconSrc, genericStarSrc } from "./entityicons.js";
import { lastPracticed } from "./librarymodel.js";
import { LibraryNav } from "./librarynav.js";
import { LibraryTarget } from "./librarytarget.js";
import { LibraryTray, LibraryGrid } from "./librarytray.js";
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

/**
 * t            the tracker store
 * active       true while the Library tab is the one on screen
 * intent       null, or {kind:"target", entity, strat?, tier?} / {kind:
 *              "compare", ...} — a caller elsewhere in the app asking the
 *              Library to open on something specific (openLibrary in
 *              app.js). Only the "target" kind is acted on here: it routes
 *              straight to that entity's target page, the same door
 *              auto-open uses, and clears itself so it fires once. `strat`/
 *              `tier` ride along into LibraryTarget's `focusStrat`/
 *              `focusTier` props (Task 4) -- the deep-linking CALLER (the
 *              standards ladder's own tier rows, the practice-log book mark)
 *              is still Task 7's job; this only wires the pipe through.
 * clearIntent  () => void, called once an intent has been acted on
 */
export function Library({ t, active, intent, clearIntent }) {
  const [index, setIndex] = useState(null);
  const [status, setStatus] = useState(null);
  const [stage, setStage] = useState("browse");   // "browse" | "target"
  const [entry, setEntry] = useState(null);        // {entityKey, rows, focusStrat, focusTier}
  const [refreshState, setRefreshState] = useState(null);
  // The comparison tray. Task 4 seeded MINIMAL state here ("tray state lives
  // here", the shape `{key, runner, time_cs, video, strat, trim}`) just
  // enough for "+" to work; Task 5 (librarytray.js) is what actually reads
  // it, so this grows the SAME state -- trim edits and removal -- rather
  // than inventing a second shape, plus one field: `entity_key`, added in
  // Task 5's fix round 1 so Task 6 can import each item onto the entity it
  // actually came from rather than needing a single tray-wide entity handed
  // in from outside (the tray can hold items from more than one entity
  // across navigation). `key` is a COMPOSITE the entry's owning approach
  // computes (librarytarget.js::entryTrayKey) -- never `entry.video` alone,
  // which the controller measured colliding across sibling entities on the
  // real snapshot (one recording cited as evidence for two different
  // stars). An entry with no video never reaches `onAdd` at all
  // (librarytarget.js disables "+" there, since there is nothing to embed or
  // import into Compare).
  const [tray, setTray] = useState([]);
  const [showGrid, setShowGrid] = useState(false);
  const trayKeys = new Set(tray.map((item) => item.key));
  const addToTray = (item) => setTray((prev) =>
    prev.some((existing) => existing.key === item.key) ? prev : [...prev, item]);
  const trimTrayItem = (key, trim) => setTray((prev) =>
    prev.map((item) => (item.key === key ? { ...item, trim } : item)));
  const removeFromTray = (key) => setTray((prev) =>
    prev.filter((item) => item.key !== key));
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

  // `focus` is {strat?, tier?} -- carried from an `intent` (Task 7's own
  // deep links) into LibraryTarget's `focusStrat`/`focusTier` props. Read
  // once at open time: `intent` is cleared right after this runs (below), so
  // capturing it into `entry` is the only way it survives past that clear.
  function openEntity(entityKey, focus = {}) {
    return getJSON(`/api/library/entity/${encodeURIComponent(entityKey)}`)
      .then((data) => {
        setEntry({ entityKey, rows: data.targets || [],
                   focusStrat: focus.strat || null, focusTier: focus.tier || null });
        setStage("target");
      });
  }

  // The course grid hands back an entity key when a target has one, else its
  // numeric index into the index we already fetched (librarynav.js's own
  // contract). A Castle Movement / stage RTA target has no entity to look up
  // by, but it still needs its FULL shape (approaches/subsections as real
  // arrays, not `GET /api/library`'s own summary counts) -- exactly the door
  // task-3-caveats.md point 4 left for this task: `GET
  // /api/library/target/{index}`, which library_api.py already spreads as
  // `{index, ...target}`, the same full shape `/api/library/entity/{key}`'s
  // own `targets` array carries. One shape either door produces is what lets
  // librarytarget.js stay ignorant of which door it came through.
  function handlePick(value) {
    if (typeof value === "string") { openEntity(value); return; }
    getJSON(`/api/library/target/${value}`)
      .then((row) => {
        setEntry({ entityKey: null, rows: [row], focusStrat: null, focusTier: null });
        setStage("target");
      });
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
    openEntity(intent.entity, { strat: intent.strat, tier: intent.tier });
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
    <${LibraryTray} items=${tray} onTrim=${trimTrayItem} onRemove=${removeFromTray}
        onPlayAll=${() => setShowGrid(true)} onStudy=${null} />
    ${stage === "target"
      ? html`<div class="library-target-page">
          <button type="button" class="entity-back" onclick=${backToBrowse}>
            <${Icon} name="chevron" size=${15} /> Back
          </button>
          <${LibraryTarget} t=${t} targets=${entry ? entry.rows : []}
              onAdd=${addToTray} trayKeys=${trayKeys}
              focusStrat=${entry ? entry.focusStrat : null}
              focusTier=${entry ? entry.focusTier : null} />
        </div>`
      : html`<${LibraryNav} index=${index} onPick=${handlePick} iconFor=${iconFor} />`}
    ${showGrid
      ? html`<${LibraryGrid} items=${tray} onClose=${() => setShowGrid(false)} />`
      : null}
  </div>`;
}
