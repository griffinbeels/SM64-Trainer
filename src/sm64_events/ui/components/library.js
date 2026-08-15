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
import { lastPracticed, trayToImport } from "./librarymodel.js";
import { LibraryNav } from "./librarynav.js";
import { LibraryTarget } from "./librarytarget.js";
import { LibraryTray, LibraryGrid } from "./librarytray.js";
import { Icon } from "./icons.js";

const html = htm.bind(h);

// "3 hours ago", off an ISO timestamp -- the header speaks human time, never
// a raw revision stamp (round 1: "The text here for Sheet 2026-blah blah is
// unsettling. I think we should just say 'Last refreshed: [time] ago'").
function agoLabel(iso) {
  const then = Date.parse(iso || "");
  if (!Number.isFinite(then)) return null;
  const seconds = Math.max(0, (Date.now() - then) / 1000);
  if (seconds < 90) return "just now";
  if (seconds < 5400) return `${Math.round(seconds / 60)} minutes ago`;
  if (seconds < 129600) return `${Math.round(seconds / 3600)} hours ago`;
  return `${Math.round(seconds / 86400)} days ago`;
}

function statusLine(status, error) {
  // FINAL REVIEW FIX (medium: two fetches fail into a permanent spinner).
  // `error` was not a parameter before -- a dead or slow server were
  // indistinguishable, both reading "Loading the community sheet…" forever.
  if (error) return `Could not load the sheet status: ${error}`;
  if (!status) return "Loading the community sheet…";
  if (status.source === "local") {
    const ago = agoLabel(status.fetched_at);
    return ago ? `Last refreshed: ${ago}` : "Refreshed by you";
  }
  return "Bundled with the app — refresh to pull the newest sheet";
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
    return html`<p class="library-refresh-msg is-ok">Updated just now — ${state.targets} targets.</p>`;
  return html`<p class="library-refresh-msg">Already up to date — ${state.reason}</p>`;
}

// A Twitch link failing must not sink the batch (task-6-brief.md step 1):
// every tray item still gets its own import attempt, and this renders what
// came back for the ones that didn't land. `result` is null before the
// first Study click. TWO independent lines, either or both -- a batch can
// both fail partially AND span more than one entity:
// - a failure line, whenever `failures` is non-empty (a full success shows
//   nothing here, since the page has already routed to Compare to show the
//   result directly);
// - `mixedEntityNote` (fix round 1, CONTROLLER RULING): Compare shows one
//   (entity, strat) pane at a time, so a Study batch spanning more than one
//   entity still only opens the FIRST one there -- this says so, always
//   visible, never a title/tooltip (acceptance.md's rule for anything that
//   silently does less than its own affordance promises).
function StudyMessage({ result }) {
  if (!result) return null;
  const hasFailures = result.failures.length > 0;
  if (!hasFailures && !result.mixedEntityNote) return null;
  const succeeded = result.total - result.failures.length;
  const parts = result.failures.map((f) => `'${f.label}' failed: ${f.detail}`);
  return html`<div class="library-study-msg">
    ${hasFailures
      ? html`<p class="library-study-msg-line is-error">${succeeded} of ${result.total} imported; ${parts.join("; ")}</p>`
      : null}
    ${result.mixedEntityNote
      ? html`<p class="library-study-msg-line">${result.mixedEntityNote}</p>`
      : null}
  </div>`;
}

// A best-effort friendly name for an entity_key, off the CURRENT session
// view -- the same `course_name`/`star_name`/segment `name` fields the
// practice log already shows for these sections, never re-derived. Falls
// back to the raw key when the session view has never carried that section
// (an entity the player has not practiced yet this session): still an
// honest, readable identifier, never a fabricated label.
function entityLabel(t, key) {
  const view = t && t.view;
  if (!view) return key;
  if (key.startsWith("segment:")) {
    const id = Number(key.slice("segment:".length));
    const sec = (view.segments || []).find((s) => s.segment_id === id);
    return sec ? sec.name : key;
  }
  const match = key.match(/^star:(\d+):(\d+)$/);
  if (!match) return key;
  const [, courseId, starId] = match;
  const sec = (view.stars || []).find(
    (s) => s.course_id === Number(courseId) && s.star_id === Number(starId));
  return sec ? `${sec.course_name} — ${sec.star_name}` : key;
}

// Poll GET /api/compare/import/{jobId} until it stops running (task-6-
// caveats.md point 1: `state` is "running" | "done" | "error", `comparison`
// is null until "done"). A plain setTimeout loop, not setInterval -- each
// tick waits for the PREVIOUS response before scheduling the next, so a slow
// answer can't stack up overlapping requests for the same job.
function pollImportJob(jobId) {
  return new Promise((resolve, reject) => {
    const tick = () => {
      getJSON(`/api/compare/import/${jobId}`)
        .then((status) => (status.state === "running" ? setTimeout(tick, 400) : resolve(status)))
        .catch(reject);
    };
    tick();
  });
}

/**
 * t            the tracker store
 * active       true while the Library tab is the one on screen
 * intent       null, or {kind:"target", entity, strat?, tier?} / {kind:
 *              "compare", attemptId?, entity, strat} — a caller elsewhere in
 *              the app asking the Library to open on something specific
 *              (openLibrary in app.js). "target" routes straight to that
 *              entity's target page, the same door auto-open uses; `strat`/
 *              `tier` ride along into LibraryTarget's `focusStrat`/
 *              `focusTier` props (Task 4) -- the deep-linking CALLER (the
 *              standards ladder's own tier rows, the practice-log book mark)
 *              is still Task 7's job, this only wires the pipe through.
 *              "compare" (Task 6) is app.js's `openCompare` arriving here
 *              instead of opening the Compare pane directly (task-6-
 *              caveats.md point 4) -- it never touches `stage`/`entry` at
 *              all, just forwards straight into `enterCompare` with no
 *              browse stop, since the caller already has everything
 *              (attemptId/entity/strat) an attempt row's own Compare button
 *              always had. Both kinds clear themselves so they fire once.
 * clearIntent  () => void, called once an intent has been acted on
 * enterCompare (intent) => void — app.js's own Compare-pane entry point
 *              (`{attemptId?, entity, strat}` — Compare's existing prop
 *              shape, unchanged by this task). Used for the "compare" intent
 *              above and by `studyInCompare` below, once its imports land.
 */
export function Library({ t, active, intent, clearIntent, enterCompare }) {
  const [index, setIndex] = useState(null);
  const [status, setStatus] = useState(null);
  // FINAL REVIEW FIX (medium: two fetches fail into a permanent spinner). A
  // dead server and a slow one used to be indistinguishable -- `index`/
  // `status` just stayed `null` forever on a `.catch(() => {})` that swallowed
  // the failure. These carry the message so the page can say so and offer a
  // real retry instead of an infinite "Loading…".
  const [indexError, setIndexError] = useState(null);
  const [statusError, setStatusError] = useState(null);
  // And a navigation failure (a 404/500 opening a specific target) used to
  // have no `.catch` AT ALL -- for the book-mark deep link `clearIntent()` had
  // already run by then, so the tab landed on the browse grid with no message
  // and nothing to retry. Cleared at the START of the next attempt (below),
  // so a later successful open silently supersedes a stale one.
  const [entryError, setEntryError] = useState(null);
  const [stage, setStage] = useState("browse");   // "browse" | "target"
  const [entry, setEntry] = useState(null);        // {entityKey, rows, focusStrat, focusTier, fallbackLabel}
  const [refreshState, setRefreshState] = useState(null);
  const [studying, setStudying] = useState(false);
  const [studyResult, setStudyResult] = useState(null);  // {total, failures:[{label,detail}]}
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

  // Both fetch-and-set, clear their own error on success -- shared by the
  // mount effect below and by `refresh()`'s own success branch, which used to
  // repeat the same unguarded `getJSON(...).then(...).catch(() => {})` pair.
  function loadIndex() {
    return getJSON("/api/library")
      .then((data) => { setIndex(data); setIndexError(null); })
      .catch((err) => setIndexError(err.message || String(err)));
  }
  function loadStatus() {
    return getJSON("/api/library/status")
      .then((data) => { setStatus(data); setStatusError(null); })
      .catch((err) => setStatusError(err.message || String(err)));
  }

  useEffect(() => { loadIndex(); loadStatus(); }, []);

  // `focus` is {strat?, tier?} -- carried from an `intent` (Task 7's own
  // deep links) into LibraryTarget's `focusStrat`/`focusTier` props. Read
  // once at open time: `intent` is cleared right after this runs (below), so
  // capturing it into `entry` is the only way it survives past that clear.
  function openEntity(entityKey, focus = {}) {
    setEntryError(null);
    return getJSON(`/api/library/entity/${encodeURIComponent(entityKey)}`)
      .then((data) => {
        setEntry({ entityKey, rows: data.targets || [],
                   focusStrat: focus.strat || null, focusTier: focus.tier || null,
                   // Round 3 of task 0098: a standards-table time link lands
                   // on the EXACT entry it exemplifies — the subdivision to
                   // auto-open and the entry card to blink ride the intent.
                   focusDivision: focus.division || null,
                   focusEntryUrl: focus.entryUrl || null,
                   // 2026-08-14: a LINKED entity resolves to the target its
                   // rows are adopted onto (server-side, same door), and
                   // `focus_row_key` names the piece the link points at --
                   // LibraryTarget opens that piece and centers his standing.
                   focusRow: data.focus_row_key || null,
                   // FINAL REVIEW FIX (important: blank-titled book mark). An
                   // entity the sheet never maps (78 of every 84 segments,
                   // measured) returns `{targets: []}` here -- `rows[0]` never
                   // exists, so librarytarget.js's own `label` fell back to
                   // "" and the target page rendered a real "No community
                   // times recorded here yet." paragraph under an EMPTY
                   // `<h3>`. `entityLabel` is the same best-effort name
                   // `mixedEntityNote` below already resolves off the current
                   // session view -- carried only as a FALLBACK; a real sheet
                   // label always wins inside LibraryTarget.
                   fallbackLabel: entityLabel(t, entityKey) });
        setStage("target");
      })
      // FINAL REVIEW FIX (medium: two fetches fail into a permanent
      // spinner). This had NO `.catch` at all -- a 404/500 opening a
      // book-mark deep link was a click that silently did nothing, and
      // `clearIntent()` had already run, so the tab landed on the browse
      // grid with no message and nothing to retry.
      .catch((err) => setEntryError(err.message || String(err)));
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
    setEntryError(null);
    getJSON(`/api/library/target/${value}`)
      .then((row) => {
        setEntry({ entityKey: null, rows: [row], focusStrat: null, focusTier: null,
                   fallbackLabel: null });
        setStage("target");
      })
      .catch((err) => setEntryError(err.message || String(err)));
  }

  function backToBrowse() { setStage("browse"); setEntry(null); setEntryError(null); }

  // Round 5: after a link/unlink the open page re-reads its rows, so
  // `adopted` always comes from the server rather than a client-side guess.
  // Door-agnostic on purpose: every row carries its own stable `index`
  // (both doors stamp it identically), so the numeric door can refresh a
  // page whichever door originally opened it. A failed refresh keeps the
  // last good rows; the next navigation refetches anyway.
  function reloadRows() {
    const current = entry;
    const rowIndexes = current ? current.rows.map((row) => row.index) : [];
    const pageIdentity = rowIndexes.join(",");
    const rowsRefresh = rowIndexes.length
      ? Promise.all(rowIndexes.map((index) =>
          getJSON(`/api/library/target/${index}`)))
        .then((rows) => setEntry((latest) => {
          if (!latest || latest.rows.map((row) => row.index).join(",") !== pageIdentity)
            return latest;
          return { ...latest, rows };
        }))
        .catch(() => {})
      : Promise.resolve();
    // Linking changes the rank store synchronously, but the Practice pickers
    // read their options from the shared session view. Refresh both views as
    // one relink operation so no tab switch or page reload is needed, and so
    // unlink removes the option through the exact same path.
    const sessionRefresh = t && t.refresh ? t.refresh() : Promise.resolve();
    return Promise.all([rowsRefresh, sessionRefresh]);
  }

  // Runs on every activation, not gated on `!active` staying false — an
  // intent may arrive on a tab that is ALREADY open (a click from elsewhere
  // while Library is on screen), and it must still navigate. Auto-open,
  // below, is the one gated to "once".
  useEffect(() => {
    if (!active || !intent) return;
    if (intent.kind === "target") {
      // A real navigation of the Library's OWN page -- suppress the "land on
      // whatever was last practiced" auto-open below, or it would race a
      // fetch this activation is about to abandon anyway.
      autoOpenedRef.current = true;
      openEntity(intent.entity, { strat: intent.strat, tier: intent.tier,
                                  division: intent.division,
                                  entryUrl: intent.entryUrl });
    } else if (intent.kind === "compare") {
      // FINAL REVIEW FIX (broad review finding #7, never landed until now):
      // this is a straight PASS-THROUGH into Compare -- the Library's own
      // page never actually shows anything for the user to have "already
      // auto-opened" past. Setting the ref here anyway spent the ONE-PER-
      // MOUNT auto-open slot on a tab switch the user never saw content
      // from; the Library persistently mounts (app.js's own doc comment),
      // so that spend was permanent for the rest of the session -- an
      // attempt row's Compare button silently disabled "land on your last-
      // practiced target" for every later genuine Library visit. Leaving the
      // ref untouched here means the auto-open effect below may still run
      // once (its own guard already prevents it running twice), which is a
      // real fetch for a target the user will not see this instant, exactly
      // as wasteful as intended -- and no more.
      enterCompare({ attemptId: intent.attemptId, entity: intent.entity, strat: intent.strat });
    }
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
        if (result.applied) { loadIndex(); loadStatus(); }
      })
      .catch((err) => setRefreshState({ error: err.message }));
  }

  // Task 6, step 1: import every tray item -- EACH under its OWN
  // `entity_key` (`trayToImport` reads it off the item, task-6-caveats.md
  // point 6), never a single entity handed in from outside, because the
  // tray is ordinary cross-entity state (librarytray.js's own header). One
  // item failing (a dead link, a private video) must not sink the batch, so
  // every item still gets its own POST + poll + optional trim PUT even after
  // an earlier one has already failed -- `failures` collects per item rather
  // than the loop stopping at the first `catch`.
  //
  // Compare shows exactly ONE (entity, strat) pane at a time (compare.js),
  // so "Study in Compare" has to land on ONE of them: the FIRST item that
  // actually imported, in tray order. Every other import still lands in ITS
  // OWN (entity, strat) bucket server-side and is reachable the moment you
  // navigate Compare there yourself (its "load existing" dropdown) -- this
  // does not silently drop them, it just cannot show more than one entity's
  // worth of video at once, which is Compare's own existing limit, not a new
  // one this task adds. FIX ROUND 1: `mixedEntityNote` says so on-screen
  // (controller ruling) rather than leaving it to be discovered by opening
  // "load existing" and wondering where the rest went.
  async function studyInCompare(items) {
    if (!items.length) return;
    setStudying(true);
    setStudyResult(null);
    const failures = [];
    // {entity_key, strat, comparisonId} per item that actually landed --
    // comparisonId is null on the (rare) branch where the job reported
    // "done" with no `comparison` at all, which still counts as imported
    // for the "N of M" count but has nothing to open.
    const succeeded = [];
    for (const item of items) {
      const { body, edit } = trayToImport(item);
      try {
        const { job_id } = await send("POST", "/api/compare/import", body);
        const status = await pollImportJob(job_id);
        if (status.state === "error") {
          failures.push({ label: body.name, detail: status.message || "import failed" });
          continue;
        }
        // Non-null `edit` only: an untrimmed tray item must inherit whatever
        // trim the comparison already has saved (server-side dedupe on
        // `source_ref` within (entity_key, strat) reuses the existing row),
        // never blank it out. task-6-caveats.md point 2 -- do not
        // "simplify" this condition away.
        if (status.comparison && edit) {
          await send("PUT", `/api/compare/videos/${status.comparison.id}`, edit);
        }
        // `body.strat`, not `item.strat || null` -- `trayToImport` already
        // resolved the item's strat to what it actually IMPORTED under
        // (its own "Standard" default), and routing must land on that same
        // bucket or the strategy picker on the Compare pane would show "no
        // strategy" beside videos saved under "Standard". Caught while
        // building this fix round's own render test: the two independently
        // re-derived the same fallback and would have drifted the moment a
        // future caller ever passes a real per-approach strat.
        succeeded.push({ entity_key: item.entity_key, strat: body.strat,
                          comparisonId: status.comparison ? status.comparison.id : null });
      } catch (err) {
        failures.push({ label: body.name, detail: err.message || String(err) });
      }
    }
    setStudying(false);
    let mixedEntityNote = null;
    if (succeeded.length) {
      // FIX ROUND 1, CRITICAL: land on the first item's (entity, strat) AND
      // actually OPEN what imported there -- `enterCompare`'s `openIds` is
      // the door compare.js's own `openComp` already opens saved
      // comparisons through (task-6-caveats.md point 3's "call the existing
      // door", now applied to the fix too). Before this fix Study routed to
      // the right pane and opened nothing in it.
      const primary = succeeded[0];
      const openIds = succeeded
        .filter((s) => s.entity_key === primary.entity_key && s.strat === primary.strat
                     && s.comparisonId != null)
        .map((s) => s.comparisonId);
      const otherKeys = [...new Set(succeeded
        .filter((s) => s.entity_key !== primary.entity_key)
        .map((s) => s.entity_key))];
      if (otherKeys.length) {
        mixedEntityNote = `Showing ${entityLabel(t, primary.entity_key)} here — also imported `
          + `for ${otherKeys.map((k) => entityLabel(t, k)).join(", ")}; open those from their `
          + `own target page to see them.`;
      }
      enterCompare({ entity: primary.entity_key, strat: primary.strat, openIds });
    }
    setStudyResult({ total: items.length, failures, mixedEntityNote });
  }

  return html`<div class="practice-card workshop-card library-page">
    <div class="workshop-hero">
      <div class="workshop-title">
        <span class="workshop-title-icon"><${Icon} name="library" size=${22} /></span>
        <div>
          <h2>Library</h2>
          <p>${statusLine(status, statusError)}</p>
        </div>
      </div>
      <button type="button" class="primary-button" onclick=${refresh}
          disabled=${refreshState === "loading"}>
        <${Icon} name="restart" size=${15} />
        ${refreshState === "loading" ? "Refreshing…" : "Refresh"}
      </button>
    </div>
    <${RefreshMessage} state=${refreshState} />
    ${/* FINAL REVIEW FIX (medium: two fetches fail into a permanent
         spinner). A failed navigation (a 404/500 opening a book mark or a
         browse-grid pick) used to be a click that silently did nothing --
         this is the one message surface for it, shown only on the browse
         page (the target page itself renders its own real content once a
         later attempt succeeds, which already clears this). */""}
    ${entryError && stage === "browse"
      ? html`<p class="library-refresh-msg is-error">Could not open that target: ${entryError}</p>`
      : null}
    <${LibraryTray} items=${tray} onTrim=${trimTrayItem} onRemove=${removeFromTray}
        onPlayAll=${() => setShowGrid(true)} onStudy=${studyInCompare} studying=${studying} />
    <${StudyMessage} result=${studyResult} />
    ${stage === "target"
      ? html`<div class="library-target-page">
          <button type="button" class="entity-back" onclick=${backToBrowse}>
            <${Icon} name="chevron" size=${15} /> Back
          </button>
          <${LibraryTarget} t=${t} targets=${entry ? entry.rows : []}
              onAdd=${addToTray} trayKeys=${trayKeys}
              focusStrat=${entry ? entry.focusStrat : null}
              focusTier=${entry ? entry.focusTier : null}
              focusDivision=${entry ? entry.focusDivision : null}
              focusEntryUrl=${entry ? entry.focusEntryUrl : null}
              focusRow=${entry ? entry.focusRow : null}
              fallbackLabel=${entry ? entry.fallbackLabel : null}
              onRelink=${reloadRows}
              resolveEntityLabel=${(key) => entityLabel(t, key)} />
        </div>`
      : indexError
        ? html`<div class="library-load-error">
            <p class="library-refresh-msg is-error">Could not load the library: ${indexError}</p>
            <button type="button" class="quiet-button" onclick=${loadIndex}>Retry</button>
          </div>`
        : html`<${LibraryNav} index=${index} onPick=${handlePick} iconFor=${iconFor} />`}
    ${showGrid
      ? html`<${LibraryGrid} items=${tray} onClose=${() => setShowGrid(false)} />`
      : null}
  </div>`;
}
