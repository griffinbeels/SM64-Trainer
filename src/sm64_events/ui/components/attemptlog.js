// src/sm64_events/ui/components/attemptlog.js
//
// The attempt-row machinery and the rank-banner helpers -- extracted out of
// practice.js (2026-08-03, task 5 of spec practice-log-entity-cards) so
// practicelog.js can use them without closing an import cycle: practicelog.js
// needs these, and practice.js is about to import practicelog.js. Exporting
// them from practice.js would have made the graph entitysection.js <-
// practice.js <-> practicelog.js, and this project has already paid once for
// an import cycle that passes `node --check` and only fails as an
// unrelated-looking ReferenceError on render.
//
// Moved verbatim, comments included -- the evidence in them is the point.
// No behaviour change.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { send } from "../api.js";
import { ReplayPlayer } from "./replay.js";
import { StatMenu } from "./statmenu.js";
import { RankIcon } from "./rankicon.js";
import { StratPicker } from "./stratpicker.js";
import { Icon } from "./icons.js";
import { EmptyState } from "./emptystate.js";
import { caveatOf, cardBadge } from "./marks.js";

const html = htm.bind(h);

const OUTCOME_LABEL = { success: "✔", reset: "✘ reset",
  hard_reset: "✘ hard reset", abandoned: "– abandoned", death: "✘ death" };

export const SORT_OPTIONS = [
  ["newest", "newest first"], ["oldest", "oldest first"],
  ["fastest", "fastest first"], ["slowest", "slowest first"]];

// Row time on the current clock: completion time for successes, how-far-in
// for failures. Nulls sort last in both directions.
export function rowTime(a, clock) {
  return clock === "igt" ? a.igt_frames : a.rta_frames;
}

// "newest"/"oldest" sort by journal_id, NOT the raw id (spec 2026-07-28-
// multi-step-segments, live report): a reattributed 100-coin attempt keeps
// its SEGMENT-namespace id (a huge number, tracking/projection.py caveat
// 2/11), which permanently outranks every native star-namespace attempt
// for the same entity under a plain numeric sort regardless of when it
// actually happened -- his practice log showed two real successes stuck
// at the top forever while newer resets piled up underneath them.
// journal_id (views.py::_attempt_json, the SAME resolver segment-section
// recency already used) strips the namespace offset back to the
// chronological journal id both kinds share.
export function comparator(sort, clock) {
  if (sort === "oldest") return (a, b) => a.journal_id - b.journal_id;
  if (sort === "fastest")
    return (a, b) => (rowTime(a, clock) ?? Infinity) - (rowTime(b, clock) ?? Infinity);
  if (sort === "slowest")
    return (a, b) => (rowTime(b, clock) ?? -Infinity) - (rowTime(a, clock) ?? -Infinity);
  return (a, b) => b.journal_id - a.journal_id; // newest (default)
}

export function delta(frames) {
  if (frames === null || frames === undefined) return "";
  const cls = frames > 0 ? "delta-up" : "delta-down";
  const sign = frames > 0 ? "+" : "";
  return html` <span class=${cls}>${sign}${(frames / 30).toFixed(2)}s</span>`;
}

export function AttemptRow({ a, t, idx, focus, clearFocus, isNew, openCompare, sec }) {
  const [showReplay, setShowReplay] = useState(false);
  const [flash, setFlash] = useState(false);
  const rowRef = useRef(null);
  // Progress-graph pick (see useGraphPick): when this row is
  // the focused one, scroll it into view, flash it, and — when the pick
  // says a saved replay file exists — open the player exactly as if the
  // ▶ button was pressed. Keyed on the nonce so re-clicking the same node
  // works after the user closed the player; runs on mount too, which is
  // what makes a row revealed by the pagination bump handle its own pick.
  useEffect(() => {
    if (!focus || focus.id !== a.id) return;
    if (focus.openReplay) setShowReplay(true);
    requestAnimationFrame(() => {
      if (rowRef.current)
        rowRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
    });
    setFlash(true);
    if (clearFocus) clearFocus(); // one pick = one handling; later remounts must not re-fire
  }, [focus && focus.nonce]);
  // The un-flash timer is its OWN effect, keyed on `flash` rather than
  // folded into the one above -- added for practicelog.js's `LogCard`
  // (Task 6, Finding 1 round 2). `focus` is now passed conditionally
  // (`selected ? focus : null`), so `clearFocus()` above (called the MOMENT
  // this row's own pick lands) sets the shared focus back to null on the
  // very next render -- which used to tear down THIS SAME effect (its
  // dependency, `focus && focus.nonce`, had changed) and cancel the pending
  // `setTimeout` before it ever fired, leaving `flash` stuck at `true`
  // forever. Splitting the timer onto `flash` itself means neither
  // `clearFocus()` nor a later browse to a different entity's card (which
  // sets THIS row's `focus` prop to null the same way) can cancel it early
  // -- it always turns itself off ~1600ms after switching on, independent
  // of whatever `focus` does meanwhile. Proved live: two rows read as
  // flashed at once (an earlier PB-jump row that never turned off, plus a
  // fresh trend-graph pick on a different, newly-browsed entity) until this
  // split; no section ever stopped being the one shown before Task 6, so a
  // row's own `focus` going null while it stayed mounted could not happen
  // previously.
  useEffect(() => {
    if (!flash) return;
    const timer = setTimeout(() => setFlash(false), 1600);
    return () => clearTimeout(timer);
  }, [flash]);
  async function clear() {
    await send("POST", `/api/attempts/${a.id}/clear`, { reason: "accidental" });
    t.refresh();
  }
  async function restore() {
    await send("POST", `/api/attempts/${a.id}/restore`);
    t.refresh();
  }
  // Segment attempts are RTA-only (igt is null; the server rejects igt PB
  // saves with "segments are RTA-only") — force rta whatever the view clock.
  const isSeg = a.segment_id != null;
  async function savePb() {
    await send("POST", "/api/pb",
      { attempt_id: a.id, timer_mode: isSeg ? "rta" : t.clock });
    t.refresh();
  }
  async function undoPb() {
    await send("POST", "/api/pb/undo",
      { attempt_id: a.id, timer_mode: isSeg ? "rta" : t.clock });
    t.refresh();
  }
  const time = isSeg ? a.rta : (t.clock === "igt" ? a.igt : a.rta);
  const frames = isSeg ? a.rta_frames : (t.clock === "igt" ? a.igt_frames : a.rta_frames);
  const inTime = isSeg ? a.rta : a.igt; // failures: how-far-in on the section's clock
  // Glow when saving would set a new PB: beats the recorded PB, or no PB
  // exists yet. frames > 0 excludes same-tick race rows (rta=0 junk) whose
  // "PB" would be meaningless.
  const pbBeat = a.outcome === "success" && !a.cleared
    && frames != null && frames > 0
    && (a.pb_delta_frames === null || a.pb_delta_frames < 0);
  // Star rows don't carry course/star on the attempt itself (_attempt_json
  // omits them) — derive the entity from the section for stars, from the
  // attempt for segments.
  const entity = a.segment_id != null ? `segment:${a.segment_id}`
    : (sec ? `star:${sec.course_id}:${sec.star_id}` : null);
  const strat = a.strat_tag || (sec && sec.last_strat) || null;
  // The server's answer to "may this be saved as a PB", as a caveat key we
  // already know how to draw. Never re-derived here: save_pb refuses on the
  // same predicate, and a button that offers what the server rejects is the
  // drift this shares one door to prevent.
  const blockedPb = caveatOf(a.pb_blocked_by);
  // The mark on the TIME, not on the save button: this row's number is not
  // the quantity it looks like ("if you've been practicing all wrong, you
  // should know", 2026-08-02). Same key vocabulary, same badge, one door —
  // the server decides which rows earn it (tracking/caveats.py's PROVEN-only
  // rule, measured), this only draws it.
  const timeMark = caveatOf(a.caveat);
  const row = html`<tr ref=${(el) => { rowRef.current = el; }}
      class="${a.cleared ? "cleared" : ""} ${flash ? "row-flash" : ""} ${isNew ? "row-new" : ""}">
    <td class="meta attempt-index">#${idx + 1}</td>
    <td class="attempt-medal">${a.rank
      ? html`<${RankIcon} tier=${a.rank.rank} division=${a.rank.division} size=${22} />` : ""}</td>
    <td class="attempt-result ${a.outcome === "success" ? "good" : "badx"}">
      ${OUTCOME_LABEL[a.outcome] || a.outcome}
      ${a.outcome === "death" && a.outcome_detail
        ? html` <span class="meta">(${a.outcome_detail})</span>` : ""}
      ${a.outcome === "success" && time ? html` <b>${time}</b>` : ""}
      ${timeMark ? cardBadge(timeMark) : ""}
      ${a.outcome !== "success" && inTime ? html` <span class="meta">${inTime} in</span>` : ""}
      ${t.showDust && a.rollouts_total > 0
        ? html` <span class="meta">· ${a.rollouts_dustless}/${a.rollouts_total} dustless rollouts</span>` : ""}
      ${t.showDust && a.jumps_total > 0
        ? html` <span class="meta">· ${a.jumps_dustless}/${a.jumps_total} dustless jumps</span>` : ""}
      ${a.cleared && a.cleared_reason
        ? html` <span class="meta">(${a.cleared_reason})</span>` : ""}
    </td>
    <td class="attempt-delta">${a.outcome === "success" ? delta(a.pb_delta_frames) : ""}</td>
    <td class="meta attempt-strategy">
      ${sec
        ? html`<${StratPicker} entity=${entity} strategies=${sec.strategies}
            groups=${sec.strategy_groups}
            active=${a.strat_tag} blankLabel="— no strategy —"
            highlightUnset=${false} allowBlank=${!sec.default_strat}
            submit=${(tag) => send("POST", `/api/attempts/${a.id}/strat`,
                                   { strat_tag: tag })}
            onChanged=${t.refresh} />`
        : html`<span>${a.strat_tag || "— no strategy —"}</span>`}
    </td>
    <td class="attempt-actions">
      <button class="icon-button" onclick=${() => setShowReplay(!showReplay)}
          title="View replay" aria-label="View replay">
        <${Icon} name=${showReplay ? "chevron" : "play"} size=${16} /></button>
      ${a.outcome === "success" && !a.cleared
        ? (a.is_current_pb
          ? html` <button onclick=${undoPb}
              title="delete this save — the previous PB becomes current again">Undo PB</button>`
          : blockedPb
            // Not a slow PB — a different quantity, which no leaderboard
            // accepts (2026-08-02: "these fake PBs just shouldn't be
            // allowed"). Shown rather than hidden, and carrying the SAME
            // badge a PB already saved with this problem wears, so the row
            // explains itself instead of leaving a button that silently
            // stopped working. The server refuses it too — this is the
            // affordance, not the rule (tracking/caveats.py::pb_blocked_by).
            ? html` <button class="pb-blocked" disabled
                title=${`Cannot be saved as a PB — ${blockedPb.sentence}`}
                aria-label=${`Cannot be saved as a PB — ${blockedPb.sentence}`}>
                <${Icon} name="bookmark" size=${14} />
                <span class="save-pb-wide">Save as PB</span>
                <span class="save-pb-narrow">Save PB</span>
                ${cardBadge(blockedPb)}</button>`
            : html` <button class=${pbBeat ? "pb-glow" : ""} onclick=${savePb}>
                <${Icon} name="bookmark" size=${14} />
                <span class="save-pb-wide">Save as PB</span>
                <span class="save-pb-narrow">Save PB</span></button>`)
        : ""}
      ${a.cleared
        ? html` <button onclick=${restore}>undo</button>`
        : html` <button class="icon-button" onclick=${clear}
            title="Clear this attempt as a mistake" aria-label="Clear attempt">×</button>`}
    </td>
  </tr>`;
  const onCompare = (openCompare && entity)
    ? () => openCompare({ attemptId: a.id, entity, strat })
    : null;
  const expandedRow = showReplay
    ? html`<tr class="replay-row"><td colspan="6"><${ReplayPlayer} attemptId=${a.id} onCompare=${onCompare} /></td></tr>`
    : null;
  return [row, expandedRow];
}

// Shared table component used by both StarSection and the unassigned block.
// attempts: the full ordered list for stable numbering;
// rows: the filtered/sorted subset to actually render.
export function AttemptTable({ attempts, rows, t, focus, clearFocus, freshIds, openCompare, sec }) {
  return html`<table class="attempt-table">
    ${rows.map((a) => {
      const idx = attempts.indexOf(a);
      return html`<${AttemptRow} key=${a.id} a=${a} t=${t} idx=${idx}
        focus=${focus} clearFocus=${clearFocus}
        isNew=${freshIds ? freshIds.has(a.id) : false}
        openCompare=${openCompare} sec=${sec} />`;
    })}
  </table>`;
}

export function HideToggle({ hidden, showHidden, setShowHidden }) {
  if (hidden.length === 0) return null;
  return html`<button class="meta"
      style="background:none;border:none;cursor:pointer"
      onclick=${() => setShowHidden(!showHidden)}>
    ${showHidden ? "hide" : "show"} ${hidden.length} hidden
  </button>`;
}

// Progress-graph pick, shared by StarSection and SegmentSection (and the
// clickable PB tag below). Reveal an attempt's row — bump pagination if it's
// past the fold — then scroll to it, flash it, and auto-open its replay when a
// saved file exists (HEAD existence probe). Graph points and the PB attempt are
// always non-cleared successes, which no list filter removes, so they live in
// `rows` whenever they're in scope; a pick whose attempt is out of scope (e.g.
// a PB from an earlier session, viewed in session scope) is held as pending and
// revealed when a later view brings the row in — the PB tag switches to
// lifetime scope to make that happen.
export function useGraphPick(rows, visible, setVisible) {
  const [focus, setFocus] = useState(null);
  const pickNonce = useRef(0);
  // pendingId: an attempt picked while it wasn't (yet) in `rows` — e.g. the PB
  // tag jumping to an out-of-scope PB, which first switches scope to lifetime.
  // Claimed synchronously in pick() (before the async replay probe) so the
  // scope refetch can't re-render past us; the effect below reveals it once the
  // new view brings the row in.
  const pendingId = useRef(null);
  async function reveal(attemptId) {
    // Membership first: bail before the replay probe when the row isn't loaded
    // (an out-of-scope PB before its scope switch lands), so no wasted request.
    const idx = rows.findIndex((a) => a.id === attemptId);
    if (idx === -1) return false;
    if (idx >= visible) setVisible(Math.ceil((idx + 1) / 10) * 10);
    let openReplay = false;
    try {
      openReplay = (await fetch(`/api/replay/saved/${attemptId}`,
                                { method: "HEAD" })).ok;
    } catch { /* probe is best-effort: still scroll + flash */ }
    setFocus({ id: attemptId, nonce: ++pickNonce.current, openReplay });
    return true;
  }
  async function pick(attemptId) {
    pendingId.current = attemptId;                    // claim before any await
    if (await reveal(attemptId)) pendingId.current = null;
  }
  // A pick whose attempt wasn't in `rows` waits here until a new view brings it
  // in (the PB tag switching scope to lifetime), then reveals it.
  useEffect(() => {
    if (pendingId.current == null) return;
    if (!rows.some((a) => a.id === pendingId.current)) return;
    const id = pendingId.current;
    pendingId.current = null;
    reveal(id);
  }, [rows]);
  return { focus, pick, clearFocus: () => setFocus(null) };
}

// PB tag: a clickable jump to the PB's attempt row WHEN the caller passes a
// `pick` — same reveal path as its gold progress-graph dot (scroll, flash,
// open saved replay). The objective card passes its own `pick`; a log card
// (practicelog.js) passes `pick=null` on purpose, so its tag renders as plain
// text there — the card's own trend graph is the working entry point into
// that same list, and a second clickable jump into a table already on screen
// would be redundant rather than broken. When the PB is out of the current
// scope (a lifetime PB from an earlier session, viewed in session scope) its
// row isn't loaded, so clicking first switches to lifetime scope; pick()
// holds the request until the lifetime view brings the row in. `mode` is
// just the clock label shown in parens.
// `pb.caveat` is a CAVEAT key (components/marks.js) or absent — the server's
// own answer to "does this saved time mean what the rank beside it implies".
// Derived in tracking/views.py from `timed_by`/`closed_by`/`timed_at`, so this
// surface and the quick-select cell can never word the same fact two ways.
export function PbTag({ pb, mode, rows, pick, t, showCaveat = true }) {
  if (!pb) return html`<span class="pbtag">no PB yet</span>`;
  function jump() {
    if (!pick) return;
    if (!rows.some((a) => a.id === pb.attempt_id) && t.scope !== "lifetime")
      t.pickScope("lifetime");
    pick(pb.attempt_id);
  }
  const mark = showCaveat ? caveatOf(pb.caveat) : null;
  return html`<span class="pbtag">PB ${pick
    ? html`<a class="pblink" onclick=${jump}
        title="jump to this PB in the list below">${pb.display}</a>`
    : pb.display} (${mode})${mark ? cardBadge(mark) : null}</span>`;
}

// True when the strategy rank and the entity rank are the SAME MEASURE:
// the active strategy's ladder grades this time exactly where the entity's
// best-possible ladder does. Always the case for a star with only ONE
// strategy carrying standards -- its ladder IS the pointwise best -- and in
// general whenever no other strategy beats it around the player's current
// time. The practice log now always renders one banner; this tells its rank
// picker whether there is a second measurement to offer.
//
// One measure or two? Answered by the SERVER, from the ladders themselves
// (views.py::ranks_share_ladder), not by comparing the two graded values.
//
// The field-by-field comparison this replaces was stable enough while both
// sides were graded, but it could not answer the question at all before a
// first time existed -- so the entity banner was simply absent until one
// landed and then appeared out of nowhere, which is the live report this
// fixes (2026-07-27). Reading the ladders also stops two genuinely different
// measures merging on a run that happens to grade them alike and splitting
// again on the next.
export function ranksShareOneLadder(sec) {
  return !!sec.one_ladder;
}

// A strategy with a ladder but no time yet is at the FLOOR, not unranked:
// both banners draw Capless V (ranks.js). Keyed off the STRATEGY banner's own
// sentinel reason, which is the one place that knows a ladder exists.
export function ranksAreAtFloor(sec) {
  return !!(sec.rank && !sec.rank.rank && sec.rank.reason === "unranked");
}

// Whether the practice card has a separate Overall measurement to offer.
// A floor has no entity payload yet, but its distinct ladder is still real and
// both modes honestly draw Capless V. A missing payload in any other state is
// not exposed as a button whose destination has nothing to say.
export function showsEntityBanner(sec) {
  if (ranksShareOneLadder(sec)) return false;
  return !!sec.entity_rank || ranksAreAtFloor(sec);
}

// What a rank banner considers "the same measurement", so its level-up climb
// fires on a real rise and nothing else (ui/rankclimb.js). Four things can
// replace a banner's numbers without anyone having earned anything, and all
// four are in here: a different entity (a new target), a different ladder
// (the two banners grade against different ones -- `which`), a different
// strategy (the strategy banner re-grades on that strat's own ladder), and a
// different grading mode (PB vs an average window). Change any of them and
// the banner SNAPS to the new rank instead of climbing to it.
export function rankIdentity(entityKey, which, sec, t) {
  const mode = (t.view && t.view.rank_mode) || "";
  return `${entityKey}|${which}|${sec.last_strat || ""}|${mode}`;
}

// The rank wash moved onto each `.rank-banner` itself (index.html,
// 2026-07-27) so it can cross-fade with the climb instead of painting the
// tier the climb is heading FOR. Nothing to hand down from here any more:
// the split it needed was the DOM boundary between the two banners all
// along, and the colour is the banner's own `--climb-color`.

// The practice log's two empty states. They are NOT one state and must not
// share copy: "nothing recorded yet" is answered by going and running the
// thing, while "everything is filtered out" is answered by the two toggles in
// this card's own footer — and sending a user off to practise when their
// attempts are sitting right there behind a checkbox is the worse miss. One
// component so the star and segment cards can't drift apart on either.
export function AttemptLogEmpty({ hasAttempts }) {
  return hasAttempts
    ? html`<${EmptyState} headline="Every attempt is filtered out"
        hint="Clear the filters below to bring them back." />`
    : html`<${EmptyState} headline="No attempts logged yet"
        hint="Every run you finish lands here automatically." />`;
}

// The trigger + popover for the stat menu — ONE shared component (rule 11:
// a control pasted into both cards is exactly the shape that drifts). Since
// Task 5 (spec practice-log-entity-cards, 2026-08-03) it is called from
// exactly ONE place — practicelog.js's `PracticeLog` heading, left of the
// sort control — not from StarSection and SegmentSection, which no longer
// render an attempts table of their own at all.
//
// It used to sit in `.attempts-card`, a fixed-height `overflow: hidden` box
// (`.claude/rules/ui-core.md`: "an element that WRAPS inside a fixed-height
// card costs nothing visible and clips its sibling" — the same trap here
// would clip the popover itself rather than a sibling). A `position:
// absolute` popover anchored inside that card would have been cut off the
// instant it grew past the card's own 458px, which the stat-menu checklist
// easily does. `.attempts-card` is gone with StarSection/SegmentSection's own
// attempts table; its replacement (`.log-list-card`) is variable-height and
// does not clip. Fixed positioning, anchored off the trigger's own measured
// rect, is kept as-is rather than revisited — converting back to a
// `position: relative` ancestor would need auditing the whole card for no
// behavioural gain, since nothing between the trigger and the viewport
// declares a transform/filter/perspective/contain that would trap a
// `position: fixed` descendant back inside an ancestor's overflow anyway.
export function StatMenuTrigger({ t }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [anchor, setAnchor] = useState(null);
  const buttonRef = useRef(null);

  const toggle = () => {
    if (!menuOpen && buttonRef.current) {
      const box = buttonRef.current.getBoundingClientRect();
      setAnchor({ top: box.bottom + 6, right: window.innerWidth - box.right });
    }
    setMenuOpen((open) => !open);
  };

  return html`<span class="stat-menu-trigger">
    <button ref=${buttonRef} type="button" class="chip chip-button"
        onclick=${toggle} title="Choose which stats appear on the practice log">
      <${Icon} name="feed" size=${14} />${" "}<span class="stat-menu-label">Stats</span>
    </button>
    ${menuOpen && anchor && html`<div class="stats-popover"
        style=${`top:${anchor.top}px; right:${anchor.right}px`}>
      <${StatMenu} t=${t} close=${() => setMenuOpen(false)} />
    </div>`}
  </span>`;
}

// Practice-log controls live IN the log card they act on (2026-07-24 UX
// pass): sort in the card heading, reset visibility in the footer. They used
// to sit in an "analysis toolbar" under the charts, which read as controls
// for the graphs.
export function SortControl({ ui }) {
  return html`<label class="sort-control" title="Order the practice log">
    <${Icon} name="sort" size=${16} /><span class="sr-only">Sort attempts</span>
    <select value=${ui.sort} onchange=${(e) => ui.setSort(e.target.value)}>
      ${SORT_OPTIONS.map(([k, label]) => html`<option value=${k}>${label}</option>`)}
    </select></label>`;
}

export function ResetFilterToggle({ ui }) {
  return html`<label class="reset-toggle" title="Hide reset attempts from the log">
    <${Icon} name="eyeOff" size=${16} />
    <input type="checkbox" checked=${ui.hideResets}
           onchange=${(e) => ui.setHideResets(e.target.checked)} />
    <span>Hide resets</span></label>`;
}
