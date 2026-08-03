// src/sm64_events/ui/components/practice.js
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { requestTarget } from "../target.js";
import { hasPracticeContext, justCompletedSegment,
        practicedHere, starPracticableHere } from "../stagecontext.js";
import { ReplayPlayer } from "./replay.js";
import { StatMenu, DUST_STAT_KEYS } from "./statmenu.js";
import { Timeline } from "./timeline.js";
import { Progress, hasProgressPoints } from "./progress.js";
import { StageBanner } from "./stagebanner.js";
import { RankBanner, rankColor } from "./ranks.js";
import { useHeldWhileCelebrating } from "../rankclimb.js";
import { CollapseToggle, cardClass, useCollapsed } from "./collapsible.js";
import { RankIcon } from "./rankicon.js";
import { StandardsPanel } from "./standards.js";
import { StratPicker } from "./stratpicker.js";
import { useTargetPicker } from "./targetpicker.js";
import { FailureCompilation } from "./failcomp.js";
import { Icon } from "./icons.js";
import { PageState } from "./states.js";
import { EmptyState } from "./emptystate.js";
import { caveatOf, cardBadge } from "./marks.js";
import { displayName, entityKey, entityNoun, sectionPb } from "../entitysection.js";

const html = htm.bind(h);

const OUTCOME_LABEL = { success: "✔", reset: "✘ reset",
  hard_reset: "✘ hard reset", abandoned: "– abandoned", death: "✘ death" };

const SORT_OPTIONS = [
  ["newest", "newest first"], ["oldest", "oldest first"],
  ["fastest", "fastest first"], ["slowest", "slowest first"]];

// Row time on the current clock: completion time for successes, how-far-in
// for failures. Nulls sort last in both directions.
function rowTime(a, clock) {
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
function comparator(sort, clock) {
  if (sort === "oldest") return (a, b) => a.journal_id - b.journal_id;
  if (sort === "fastest")
    return (a, b) => (rowTime(a, clock) ?? Infinity) - (rowTime(b, clock) ?? Infinity);
  if (sort === "slowest")
    return (a, b) => (rowTime(b, clock) ?? -Infinity) - (rowTime(a, clock) ?? -Infinity);
  return (a, b) => b.journal_id - a.journal_id; // newest (default)
}

// New-entry blink: attempt ids first seen AFTER the initial view load get
// .row-new (three gold pulses, ~2.4s) so the row that just landed is
// unmissable. The first view after mount and the first after a
// session↔lifetime flip are absorbed silently — those bring in OLD
// attempts, not new entries. Expiry is real state, not just animation-end:
// keyed reorders re-insert the <tr>, which replays any animation class
// still present on it.
function useFreshAttemptIds(t) {
  const [freshIds, setFreshIds] = useState(() => new Set());
  const base = useRef(null);            // { scope, ids } — every id ever seen
  useEffect(() => {
    const v = t.view;
    if (!v) return;
    const ids = [
      ...v.stars.flatMap((s) => s.attempts),
      ...(v.segments || []).flatMap((s) => s.attempts),
      ...v.unassigned,
    ].map((a) => a.id);
    if (!base.current || base.current.scope !== t.scope) {
      base.current = { scope: t.scope, ids: new Set(ids) };
      return;
    }
    const fresh = ids.filter((id) => !base.current.ids.has(id));
    if (fresh.length === 0) return;
    fresh.forEach((id) => base.current.ids.add(id));
    setFreshIds((prev) => new Set([...prev, ...fresh]));
    setTimeout(() => setFreshIds((prev) => {   // per-batch timer — an effect
      const next = new Set(prev);              // cleanup would cancel this
      fresh.forEach((id) => next.delete(id));  // batch's expiry whenever the
      return next;                             // next view lands within 2.6s
    }), 2600);
  }, [t.view]);
  return freshIds;
}

function delta(frames) {
  if (frames === null || frames === undefined) return "";
  const cls = frames > 0 ? "delta-up" : "delta-down";
  const sign = frames > 0 ? "+" : "";
  return html` <span class=${cls}>${sign}${(frames / 30).toFixed(2)}s</span>`;
}

function AttemptRow({ a, t, idx, focus, clearFocus, isNew, openCompare, sec }) {
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
    const timer = setTimeout(() => setFlash(false), 1600);
    if (clearFocus) clearFocus(); // one pick = one handling; later remounts must not re-fire
    return () => clearTimeout(timer);
  }, [focus && focus.nonce]);
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
function AttemptTable({ attempts, rows, t, focus, clearFocus, freshIds, openCompare, sec }) {
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

function HideToggle({ hidden, showHidden, setShowHidden }) {
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
function useGraphPick(rows, visible, setVisible) {
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

// PB tag: always a clickable jump to the PB's attempt row — same reveal path as
// its gold progress-graph dot (scroll, flash, open saved replay). When the PB
// is out of the current scope (a lifetime PB from an earlier session, viewed in
// session scope) its row isn't loaded, so clicking first switches to lifetime
// scope; pick() holds the request until the lifetime view brings the row in.
// `mode` is just the clock label shown in parens.
// `pb.caveat` is a CAVEAT key (components/marks.js) or absent — the server's
// own answer to "does this saved time mean what the rank beside it implies".
// Derived in tracking/views.py from `timed_by`/`closed_by`/`timed_at`, so this
// surface and the quick-select cell can never word the same fact two ways.
export function PbTag({ pb, mode, rows, pick, t }) {
  if (!pb) return html`<span class="pbtag">no PB yet</span>`;
  function jump() {
    if (!pick) return;
    if (!rows.some((a) => a.id === pb.attempt_id) && t.scope !== "lifetime")
      t.pickScope("lifetime");
    pick(pb.attempt_id);
  }
  const mark = caveatOf(pb.caveat);
  return html`<span class="pbtag">PB ${pick
    ? html`<a class="pblink" onclick=${jump}
        title="jump to this PB in the list below">${pb.display}</a>`
    : pb.display} (${mode})${mark ? cardBadge(mark) : null}</span>`;
}

// Validity-bounds chip (spec 2026-07-23): the section's effective min/max
// completion time — successes outside the range are auto-ignored server-side
// (auto-cleared into the hidden bucket; stats/PBs/graphs/runs skip them).
// Dimmed while on the implicit 0.5s default. Edited in SECONDS, stored as
// frames (x30). Stars persist via PUT/DELETE /api/stars/{c}/{s}/time-filter;
// segments rewrite their def's min_time/max_time guard rows through
// PUT /api/segments/{id} — both paths reproject, so history reflags
// immediately. Blank min = the 0.5s default; typed 0 = no minimum; blank
// max = no max.
function TimeFilterChip({ sec, t }) {
  const [open, setOpen] = useState(false);
  const [minS, setMinS] = useState("");
  const [maxS, setMaxS] = useState("");
  const tf = sec.time_filter;
  if (!tf) return null;
  const isSeg = sec.segment_id != null;
  const fmtS = (f) => (f % 30 === 0 ? String(f / 30) : (f / 30).toFixed(2));
  const label = tf.max_frames != null
    ? `⏱ ${fmtS(tf.min_frames)}–${fmtS(tf.max_frames)}s`
    : `⏱ ≥ ${fmtS(tf.min_frames)}s`;

  function openEditor() {
    setMinS(fmtS(tf.min_frames));
    setMaxS(tf.max_frames != null ? fmtS(tf.max_frames) : "");
    setOpen(true);
  }

  async function putSegGuards(minF, maxF) {
    // RMW the def's guard list: time rows replaced, other guards untouched
    const defs = await getJSON("/api/segments");
    const d = defs.find((x) => x.id === sec.segment_id);
    if (!d) return;
    const guards = (d.guards || []).filter(
      (g) => g.type !== "min_time" && g.type !== "max_time");
    if (minF != null) guards.push({ type: "min_time", frames: minF });
    if (maxF != null) guards.push({ type: "max_time", frames: maxF });
    await send("PUT", `/api/segments/${sec.segment_id}`, { guards });
  }

  async function save() {
    const minF = minS === "" ? null : Math.round(Number(minS) * 30);
    const maxF = maxS === "" ? null : Math.round(Number(maxS) * 30);
    if (isSeg) await putSegGuards(minF, maxF);
    // 15 mirrors projection.DEFAULT_MIN_FRAMES (blank min = keep the default)
    else await send("PUT",
      `/api/stars/${sec.course_id}/${sec.star_id}/time-filter`,
      { min_frames: minF == null ? 15 : minF, max_frames: maxF });
    setOpen(false);
    t.refresh();
  }

  async function reset() {
    if (isSeg) await putSegGuards(null, null);
    else await send("DELETE",
      `/api/stars/${sec.course_id}/${sec.star_id}/time-filter`);
    setOpen(false);
    t.refresh();
  }

  if (!open) return html`<button class="meta" style=${tf.is_default ? "opacity:.55" : ""}
      title="valid-time bounds — successes outside this range are ignored"
      onclick=${openEditor}>${label}</button>`;
  return html`<span class="meta">
    min <input type="number" min="0" step="0.1" style="width:4rem"
      value=${minS} oninput=${(e) => setMinS(e.target.value)} />s
    max <input type="number" min="0" step="0.1" style="width:4rem"
      value=${maxS} placeholder="∞" oninput=${(e) => setMaxS(e.target.value)} />s
    <button onclick=${save}>save</button>
    <button onclick=${reset} title="back to the 0.5s default">reset</button>
    <button onclick=${() => setOpen(false)}>cancel</button>
  </span>`;
}

// True when the strategy banner and the entity banner are the SAME MEASURE:
// the active strategy's ladder grades this time exactly where the entity's
// best-possible ladder does. Always the case for a star with only ONE
// strategy carrying standards -- its ladder IS the pointwise best -- and in
// general whenever no other strategy beats it around the player's current
// time. Two identical banners read as a duplicated widget (spec round 3), so
// only one renders; see bannerLabel for why it can't render as a plain
// "Strategy" one either.
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
function ranksShareOneLadder(sec) {
  return !!sec.one_ladder;
}

// A strategy with a ladder but no time yet is at the FLOOR, not unranked:
// both banners draw Capless V (ranks.js). Keyed off the STRATEGY banner's own
// sentinel reason, which is the one place that knows a ladder exists.
function ranksAreAtFloor(sec) {
  return !!(sec.rank && !sec.rank.rank && sec.rank.reason === "unranked");
}

// Whether the entity's own RankBanner renders beside the strategy one. Both
// the render and the wash below read this ONE predicate on purpose: a card
// whose wash is split in half while only one banner rendered would draw a
// colour boundary under nothing, which is the same class of "correct data,
// unexplainable picture" bug the split exists to fix.
function showsEntityBanner(sec) {
  if (ranksShareOneLadder(sec)) return false;
  return !!sec.entity_rank || ranksAreAtFloor(sec);
}

// The lone banner names BOTH measures when it IS both. Suppressing the
// entity banner and leaving the survivor labelled "STRATEGY" reads as a
// star rank that failed to load -- "it reads as there being a bug where the
// star ranking is missing. In fact, it's both the same thing, right?" (live
// report 2026-07-25 round 6, on a star whose only strategy is Standard).
// The dual label costs nothing here: this is exactly the case where one
// banner has the whole row, which is why the round-4 label budget (13
// characters unaffordable when TWO banners share ~390px) doesn't bind.
function bannerLabel(sec, entityNoun) {
  return ranksShareOneLadder(sec) ? `Strategy · ${entityNoun}` : "Strategy";
}

// Why the one banner carries two names, for anyone who hovers it.
function bannerHint(sec, entityNoun) {
  if (!ranksShareOneLadder(sec)) return null;
  return `This strategy's standards are the best this ${entityNoun.toLowerCase()}`
    + ` has, so the strategy rank and the ${entityNoun.toLowerCase()}'s own`
    + " rank are the same right now.";
}

// What a rank banner considers "the same measurement", so its level-up climb
// fires on a real rise and nothing else (ui/rankclimb.js). Four things can
// replace a banner's numbers without anyone having earned anything, and all
// four are in here: a different entity (a new target), a different ladder
// (the two banners grade against different ones -- `which`), a different
// strategy (the strategy banner re-grades on that strat's own ladder), and a
// different grading mode (PB vs an average window). Change any of them and
// the banner SNAPS to the new rank instead of climbing to it.
function rankIdentity(entityKey, which, sec, t) {
  const mode = (t.view && t.view.rank_mode) || "";
  return `${entityKey}|${which}|${sec.last_strat || ""}|${mode}`;
}

// The rank wash moved onto each `.rank-banner` itself (index.html,
// 2026-07-27) so it can cross-fade with the climb instead of painting the
// tier the climb is heading FOR. Nothing to hand down from here any more:
// the split it needed was the DOM boundary between the two banners all
// along, and the colour is the banner's own `--climb-color`.

// Names the star/segment's fastest known strategy, next to the strategy
// picker -- NOT inside the rank banner (round 4, 2026-07-25): on the
// live-report card, "· fastest here: Sign Clip" and the next: target both
// got clipped mid-word competing for the same line, and a tooltip on
// truncated visible text still reads as a layout fault. This header area
// puts the two strategy NAMES next to each other, which is the actual
// comparison being made, in a wider row that isn't also carrying a medal,
// a division, and a progress bar. Renders nothing when there's no fastest
// strategy to name, or when it's the one already active (activeStrategyIsFastest).
function StrategyFastestHint({ sec }) {
  const fastest = sec.entity_rank && sec.entity_rank.fastest_strat;
  if (!fastest || fastest === sec.last_strat) return null;
  return html`<span class="objective-strategy-fastest"
      title=${`fastest strategy here: ${fastest}`}>· fastest: ${fastest}</span>`;
}

// The practice log's two empty states. They are NOT one state and must not
// share copy: "nothing recorded yet" is answered by going and running the
// thing, while "everything is filtered out" is answered by the two toggles in
// this card's own footer — and sending a user off to practise when their
// attempts are sitting right there behind a checkbox is the worse miss. One
// component so the star and segment cards can't drift apart on either.
function AttemptLogEmpty({ hasAttempts }) {
  return hasAttempts
    ? html`<${EmptyState} headline="Every attempt is filtered out"
        hint="Clear the filters below to bring them back." />`
    : html`<${EmptyState} headline="No attempts logged yet"
        hint="Every run you finish lands here automatically." />`;
}

// The trend graph plots SUCCESSFUL attempts only, so it stays empty through a
// session of resets — the copy has to say "completed" or it reads as broken
// to someone who has been practising for an hour.
function TrendEmpty() {
  return html`<${EmptyState} headline="No completed attempts yet"
      hint="Finish a run and your times start charting here." />`;
}

// The objective card's symbol + eyebrow, which on the ACTIVE card double as
// the target picker's trigger (the header's PRACTICE TARGET card owned that
// job until 2026-07-26 — see targetpicker.js for why it moved here). Shared
// by the star card, the segment card and the no-target card so all three open
// the same dialog from the same place; `openPicker` absent renders exactly the
// two plain spans that were there before, which is what every card in the
// practice index still gets.
function ObjectiveEyebrow({ iconName, label, openPicker }) {
  const inside = html`<span class="objective-symbol">
      <${Icon} name=${iconName} size=${20} /></span>
    <span class="eyebrow">${label}</span>`;
  if (!openPicker) return inside;
  return html`<button type="button" class="objective-pick" onclick=${openPicker}
      title="Practice a different star, segment, or strategy">
    ${inside}<${Icon} name="chevron" size=${14} />
  </button>`;
}

// The stat chips. ONE component for both section kinds (rule 11) — the chips
// loop was pasted into StarSection and SegmentSection identically, and a
// second copy is how the two drift.
//
// The CONTROL that chooses which chips show is a separate component,
// StatMenuTrigger below — moved into the practice-log card's header on
// 2026-07-28 (user: "For the stats button, we should move it to be inside
// the practice log, to the left of the sort filter"), leaving the chips
// themselves here, unmoved.
function StatChipsRow({ sec, t }) {
  return html`<div class="chips stat-chips">
    ${sec.stats.filter((stat) => t.showDust || !DUST_STAT_KEYS.has(stat.key))
      .map((stat) => html`
      <span class="chip" title=${stat.key}>${stat.label} ${stat.display ?? "–"}</span>`)}
  </div>`;
}

// The trigger + popover for the stat menu — ONE shared component (rule 11:
// a control pasted into both cards is exactly the shape that drifts), placed
// in the practice-log card's header, left of the sort control, in BOTH
// StarSection and SegmentSection.
//
// `.attempts-card` is a fixed-height `overflow: hidden` box (`.claude/rules/
// ui-core.md`: "an element that WRAPS inside a fixed-height card costs
// nothing visible and clips its sibling" — the same trap here would clip the
// popover itself rather than a sibling). A `position: absolute` popover
// anchored inside that card would be cut off the instant it grew past the
// card's own 458px, which the stat-menu checklist easily does. Fixed
// positioning, anchored off the trigger's own measured rect, escapes that
// clip entirely — nothing between the trigger and the viewport declares a
// transform/filter/perspective/contain that would trap a `position: fixed`
// descendant back inside an ancestor's overflow (verified against
// index.html: none of `.attempts-card`, `.practice-detail-grid`,
// `.practice-page`, `.view-pane`, `.workspace`, `.app-main`, `.app-shell`
// declare one).
function StatMenuTrigger({ t }) {
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

function StarSection({ sec, t, ui, pinned, freshIds, openCompare, openPicker }) {
  const [showHidden, setShowHidden] = useState(false);
  const [visible, setVisible] = useState(10);
  const [foldTarget, toggleTarget] = useCollapsed("objective");
  const [foldAnalysis, toggleAnalysis] = useCollapsed("analysis");
  const [foldLog, toggleLog] = useCollapsed("attempts");
  const base = showHidden ? sec.attempts
    : sec.attempts.filter((a) => !a.cleared && a.outcome !== "abandoned");
  const hidden = sec.attempts.filter((a) => a.cleared || a.outcome === "abandoned");
  const rows = base
    .filter((a) => !(ui.hideResets
      && (a.outcome === "reset" || a.outcome === "hard_reset")))
    .slice()
    .sort(comparator(ui.sort, t.clock));
  const shown = rows.slice(0, visible);
  const { focus, pick, clearFocus } = useGraphPick(rows, visible, setVisible);

  async function wipeData() {
    const name = `${sec.course_name} · ${sec.star_name}`;
    const msg = t.scope === "lifetime"
      ? `Wipe ALL data for ${name} across every session?\n`
        + "All attempts and PBs for this star are permanently removed "
        + "(markers and strategies are kept).\nThis cannot be undone."
      : `Wipe this session's data for ${name}?\n`
        + "The session's attempts and any PBs saved from them are "
        + "permanently removed (earlier PBs are kept).\nThis cannot be undone.";
    if (!window.confirm(msg)) return;
    await send("POST", "/api/wipe", { kind: "star", course_id: sec.course_id,
                                      star_id: sec.star_id, scope: t.scope });
    t.refresh();
  }

  const ek = entityKey(sec);
  const named = displayName(sec, (t.view.catalog || {}).courses || []);
  const pb = sectionPb(sec, t.clock);
  return html`<div class="practice-detail-grid ${pinned ? "is-primary" : ""}">
    <section class="practice-card objective-card ${pinned ? "active-star" : ""} ${cardClass(foldTarget)}">
      <div class="objective-heading">
        <${ObjectiveEyebrow} iconName="target" openPicker=${openPicker}
          label=${pinned ? "Active target" : "Star practice"} />
        <div class="objective-name" title=${`${sec.course_name} · ${named.name}`}>
          <span class="objective-context">${named.context}</span>
          <h2>${named.name}</h2>
        </div>
        <div class="objective-strategy">
          <span class="field-label">Strategy</span>
          <${StratPicker} entity=${ek}
              identity=${{ course_id: sec.course_id, star_id: sec.star_id }}
              strategies=${sec.strategies} active=${sec.last_strat}
              onChanged=${t.refresh} />
          <${StrategyFastestHint} sec=${sec} />
        </div>
        <${CollapseToggle} collapsed=${foldTarget} toggle=${toggleTarget}
          label="the active target card" />
      </div>
      <div class="objective-metrics">
          <div class="rank-slot">
            <${RankBanner} label=${bannerLabel(sec, entityNoun(sec))}
                hint=${bannerHint(sec, entityNoun(sec))} banner=${sec.rank}
                atFloor=${ranksAreAtFloor(sec)} lane=${ek} order=${0}
                replayKey=${sec.last_strat || ""}
                identity=${rankIdentity(ek, "strategy", sec, t)} />
            ${showsEntityBanner(sec) && html`<${RankBanner} label=${entityNoun(sec)} banner=${sec.entity_rank}
                atFloor=${ranksAreAtFloor(sec)} lane=${ek} order=${1}
                identity=${rankIdentity(ek, "entity", sec, t)} />`}
          </div>
        ${/* Same clock + word the segment card's live state uses. It was a
             bare "○" glyph until 2026-07-26, which only became visible as an
             asymmetry once the heading icon moved into ObjectiveEyebrow --
             tests/test_ui_section_parity.py went red, correctly: the two
             cards are meant to be siblings, and ONLY_IN_* staying empty is
             the property worth keeping. An ordinary star has nothing to arm,
             so its word stays constant -- except the 100-coin star (spec
             2026-07-28-multi-step-segments), whose armed_detail is SERVER
             truth (re-derived every view fetch, same reasoning the segment
             card's pin logic already trusts it for) rather than the
             client-pushed armedSegs set a segment_id would key into. */""}
        <div class="objective-live-state ${sec.armed_detail ? "running" : ""}"
            aria-label="Practice state">
          <${Icon} name="clock" size=${17} /><span>${sec.armed_detail ? "Running" : "Ready"}</span>
        </div>
        <${PbTag} pb=${pb} mode=${t.clock} rows=${rows} pick=${pick} t=${t} />
      </div>
      ${/* Progress + what the 100-coin star's own engine is waiting for next
           (spec 2026-07-28-multi-step-segments) -- the SAME row
           SegmentSection renders below, shared markup and shared meaning:
           null while idle (every star but 100 Coins, always), so this row
           occupies zero height then. The user's own reason for keeping it:
           "i like the idea of knowing for sure the system is aware of me
           grabbing that first star, proven by it progressing to the next
           step" -- it must survive the presentation change and read as the
           STAR's own progress, not a segment's. */""}
      ${sec.armed_detail && html`<div class="seg-waiting">
        <span class="seg-waiting-step">Step${" "}
          ${sec.armed_detail.progress + 1}${" "}of${" "}
          ${sec.armed_detail.total + 1}</span>
        <span class="seg-waiting-for">Waiting for${" "}
          ${sec.armed_detail.waiting_for}</span>
      </div>`}
    </section>

    <section class="practice-card analysis-card ${cardClass(foldAnalysis)}">
      <div class="card-heading">
        <div><span class="eyebrow">Analysis</span><h3>Attempt history</h3></div>
        <${CollapseToggle} collapsed=${foldAnalysis} toggle=${toggleAnalysis}
          label="the analysis card" />
      </div>
      <div class="analysis-block timeline-block">
        <h4>Attempt timeline <span class="hint" tabindex="0"
          data-tip="Every attempt in the selected scope, positioned by its completion or reset time">ⓘ</span></h4>
        <${Timeline} tl=${sec.timeline} sec=${sec} t=${t} />
      </div>
      <div class="analysis-block trend-block">
        <h4>Performance trend <span class="hint" tabindex="0"
          data-tip="Successful attempts over time — gold dots are saved PBs; click a dot to jump to its row">ⓘ</span></h4>
        ${hasProgressPoints(sec.progress, t.clock)
          ? html`<${Progress} prog=${sec.progress} clock=${t.clock} onPick=${pick} />`
          : html`<${TrendEmpty} />`}
      </div>
    </section>

    <section class="practice-card attempts-card ${cardClass(foldLog)}">
      <div class="card-heading attempts-heading">
        <div><span class="eyebrow">Practice log</span><h3>Recent attempts</h3></div>
        <div class="attempts-tools">
          <${CollapseToggle} collapsed=${foldLog} toggle=${toggleLog}
            label="the practice log" />
          <span class="meta">${rows.length} shown</span>
          <${StatMenuTrigger} t=${t} />
          <${SortControl} ui=${ui} />
        </div>
      </div>
      ${rows.length
        ? html`<div class="attempt-scroll">
            <${AttemptTable} attempts=${sec.attempts} rows=${shown} t=${t}
              focus=${focus} clearFocus=${clearFocus} freshIds=${freshIds}
              openCompare=${openCompare} sec=${sec} />
          </div>`
        : html`<${AttemptLogEmpty} hasAttempts=${sec.attempts.length > 0} />`}
      <div class="attempt-footer">
        <div class="attempt-pagination">
          ${rows.length > visible && html`<button class="quiet-button"
              onclick=${() => setVisible(visible + 10)}>Show 10 more</button>`}
          ${visible > 10 && html`<button class="quiet-button"
              onclick=${() => setVisible(Math.max(10, visible - 10))}>Show fewer</button>`}
        </div>
        <div class="attempt-footer-tools">
          <${ResetFilterToggle} ui=${ui} />
          <${HideToggle} hidden=${hidden} showHidden=${showHidden}
              setShowHidden=${setShowHidden} />
        </div>
      </div>
    </section>

    <details class="practice-card detail-drawer" open>
      <summary>Stats, standards, and practice options</summary>
      <div class="detail-tools">
        <a href=${sec.links.ukikipedia} target="_blank">RTA Guide ↗</a>
        ${sec.links.example && html`<a href=${sec.links.example} target="_blank">Example ↗</a>`}
        <${TimeFilterChip} sec=${sec} t=${t} />
        <button class="danger-text" onclick=${wipeData}
          title=${t.scope === "lifetime"
            ? "Wipe this star's data across all sessions"
            : "Wipe this star's data in the current session"}>Clear data</button>
      </div>
      <${StatChipsRow} sec=${sec} t=${t} />
      <${StandardsPanel} entity=${ek}
          activeStrat=${sec.last_strat} strategies=${sec.strategies}
          sectionRank=${sec.rank} sectionPb=${sec.pb}
          family=${sec.pipe_segment_id != null ? "Star" : null}
          onChanged=${t.refresh} defaultOpen=${true} />
      <${FailureCompilation} identity=${{ course_id: sec.course_id, star_id: sec.star_id }}
          defaultOpen=${true} />
    </details>
  </div>`;
}

// Segment sibling of StarSection — deliberately NOT a generalization:
// segments are RTA-only (igt is null everywhere) and have no links.
// Everything else must stay at feature parity with the star card; the shared
// pieces are components (StratPicker, PbTag, TimeFilterChip, …) so a feature
// can't land on one card and miss the other, and
// tests/test_ui_section_parity.py fails when it does.
// Broken sections (definition deleted, history remains) render but drop the
// timeline/marker editor and the strat picker — both key off the deleted
// definition (POST /api/strat 404s for a segment that no longer exists).
function SegmentSection({ sec, t, ui, pinned, freshIds, openCompare, openPicker }) {
  const [showHidden, setShowHidden] = useState(false);
  const [visible, setVisible] = useState(10);
  const [foldTarget, toggleTarget] = useCollapsed("objective");
  const [foldAnalysis, toggleAnalysis] = useCollapsed("analysis");
  const [foldLog, toggleLog] = useCollapsed("attempts");
  // armedSegs is the single live source: WS notices are instant, every view
  // fetch reconciles it so it cannot stay stale — see store.js refresh().
  const armed = t.armedSegs.has(sec.segment_id);
  const tgt = (t.view && t.view.target) || {};
  const isTarget = tgt.kind === "segment" && tgt.segment_id === sec.segment_id;
  const base = showHidden ? sec.attempts
    : sec.attempts.filter((a) => !a.cleared && a.outcome !== "abandoned");
  const hidden = sec.attempts.filter((a) => a.cleared || a.outcome === "abandoned");
  const rows = base
    .filter((a) => !(ui.hideResets
      && (a.outcome === "reset" || a.outcome === "hard_reset")))
    .slice()
    .sort(comparator(ui.sort, "rta"));
  const shown = rows.slice(0, visible);
  const { focus, pick, clearFocus } = useGraphPick(rows, visible, setVisible);

  async function wipeData() {
    const msg = t.scope === "lifetime"
      ? `Wipe ALL data for ${sec.name} across every session?\n`
        + "All attempts and PBs for this segment are permanently removed "
        + "(the definition and markers are kept).\nThis cannot be undone."
      : `Wipe this session's data for ${sec.name}?\n`
        + "The session's attempts and any PBs saved from them are "
        + "permanently removed (earlier PBs are kept).\nThis cannot be undone.";
    if (!window.confirm(msg)) return;
    await send("POST", "/api/wipe", { kind: "segment",
                                      segment_id: sec.segment_id,
                                      scope: t.scope });
    t.refresh();
  }

  const pinTag = armed ? "Running" : isTarget ? "Ready" : "Recent";
  const ek = entityKey(sec);
  const named = displayName(sec, (t.view.catalog || {}).courses || []);
  return html`<div class="practice-detail-grid ${pinned ? "is-primary" : ""}">
    <section class="practice-card objective-card ${pinned ? "active-star" : ""} ${cardClass(foldTarget)}">
      <div class="objective-heading">
        <${ObjectiveEyebrow} iconName="segments" openPicker=${openPicker}
          label=${pinned ? "Active segment" : "Segment practice"} />
        <div class="objective-name" title=${named.name}>
          <span class="objective-context">${named.context}</span>
          <h2>${named.name}</h2>
        </div>
        <div class="objective-strategy">
          <span class="field-label">Strategy</span>
          ${!sec.broken
            ? html`<${StratPicker} entity=${`segment:${sec.segment_id}`}
                identity=${{ kind: "segment", segment_id: sec.segment_id }}
                strategies=${sec.strategies} active=${sec.last_strat}
                allowBlank=${!sec.default_strat}
                onChanged=${t.refresh} />`
            : html`<span class="meta">Definition deleted</span>`}
          <${StrategyFastestHint} sec=${sec} />
        </div>
        <${CollapseToggle} collapsed=${foldTarget} toggle=${toggleTarget}
          label="the active target card" />
      </div>
      <div class="objective-metrics">
          <div class="rank-slot">
            <${RankBanner} label=${bannerLabel(sec, entityNoun(sec))}
                hint=${bannerHint(sec, entityNoun(sec))} banner=${sec.rank}
                atFloor=${ranksAreAtFloor(sec)} lane=${ek} order=${0}
                replayKey=${sec.last_strat || ""}
                identity=${rankIdentity(ek, "strategy", sec, t)} />
            ${showsEntityBanner(sec) && html`<${RankBanner} label=${entityNoun(sec)} banner=${sec.entity_rank}
                atFloor=${ranksAreAtFloor(sec)} lane=${ek} order=${1}
                identity=${rankIdentity(ek, "entity", sec, t)} />`}
          </div>
        <div class="objective-live-state ${armed ? "running" : ""}"
            aria-label=${`Segment state: ${pinTag}`}>
          <${Icon} name="clock" size=${17} /><span>${pinTag}</span>
        </div>
        <${PbTag} pb=${sectionPb(sec, t.clock)} mode="rta" rows=${rows} pick=${pick} t=${t} />
      </div>
      ${/* Progress + what a multi-step arm is waiting for next (Task 6,
           spec 2026-07-28-multi-step-segments). sec.armed_detail is null
           while idle, so this row occupies zero height then -- .objective-
           card's third grid row is "auto" sized and simply collapses.
           waiting_for is already card-facing (card_waiting_for_sentence,
           tracking/segments.py) -- an imperative step like "Enter Shifting
           Sand Land", never the builder's editor-voice sentence, which read
           as broken English under this label ("Waiting for You enter level
           Shifting Sand Land"). */""}
      ${sec.armed_detail && html`<div class="seg-waiting">
        <span class="seg-waiting-step">Step${" "}
          ${sec.armed_detail.progress + 1}${" "}of${" "}
          ${sec.armed_detail.total + 1}</span>
        <span class="seg-waiting-for">Waiting for${" "}
          ${sec.armed_detail.waiting_for}</span>
      </div>`}
    </section>

    <section class="practice-card analysis-card ${cardClass(foldAnalysis)}">
      <div class="card-heading">
        <div><span class="eyebrow">Analysis</span><h3>Attempt history</h3></div>
        <${CollapseToggle} collapsed=${foldAnalysis} toggle=${toggleAnalysis}
          label="the analysis card" />
      </div>
      <div class="analysis-block timeline-block">
        <h4>Attempt timeline <span class="hint" tabindex="0"
          data-tip="Every attempt in the selected scope, positioned by its completion or reset time">ⓘ</span></h4>
        ${!sec.broken
          ? html`<${Timeline} tl=${sec.timeline} sec=${sec} t=${t} />`
          : html`<div class="stable-empty compact">Timeline unavailable for a deleted definition.</div>`}
      </div>
      <div class="analysis-block trend-block">
        <h4>Performance trend <span class="hint" tabindex="0"
          data-tip="Successful attempts over time — gold dots are saved PBs; click a dot to jump to its row">ⓘ</span></h4>
        ${hasProgressPoints(sec.progress, "rta")
          ? html`<${Progress} prog=${sec.progress} clock="rta" onPick=${pick} />`
          : html`<${TrendEmpty} />`}
      </div>
    </section>

    <section class="practice-card attempts-card ${cardClass(foldLog)}">
      <div class="card-heading attempts-heading">
        <div><span class="eyebrow">Practice log</span><h3>Recent attempts</h3></div>
        <div class="attempts-tools">
          <${CollapseToggle} collapsed=${foldLog} toggle=${toggleLog}
            label="the practice log" />
          <span class="meta">${rows.length} shown</span>
          <${StatMenuTrigger} t=${t} />
          <${SortControl} ui=${ui} />
        </div>
      </div>
      ${rows.length
        ? html`<div class="attempt-scroll">
            <${AttemptTable} attempts=${sec.attempts} rows=${shown} t=${t}
              focus=${focus} clearFocus=${clearFocus} freshIds=${freshIds}
              openCompare=${openCompare} sec=${sec} />
          </div>`
        : html`<${AttemptLogEmpty} hasAttempts=${sec.attempts.length > 0} />`}
      <div class="attempt-footer">
        <div class="attempt-pagination">
          ${rows.length > visible && html`<button class="quiet-button"
              onclick=${() => setVisible(visible + 10)}>Show 10 more</button>`}
          ${visible > 10 && html`<button class="quiet-button"
              onclick=${() => setVisible(Math.max(10, visible - 10))}>Show fewer</button>`}
        </div>
        <div class="attempt-footer-tools">
          <${ResetFilterToggle} ui=${ui} />
          <${HideToggle} hidden=${hidden} showHidden=${showHidden}
              setShowHidden=${setShowHidden} />
        </div>
      </div>
    </section>

    <details class="practice-card detail-drawer" open>
      <summary>Stats, standards, and practice options</summary>
      <div class="detail-tools">
        ${!sec.broken && html`<${TimeFilterChip} sec=${sec} t=${t} />`}
        <button class="danger-text" onclick=${wipeData}
          title=${t.scope === "lifetime"
            ? "Wipe this segment's data across all sessions"
            : "Wipe this segment's data in the current session"}>Clear data</button>
      </div>
      <${StatChipsRow} sec=${sec} t=${t} />
      <${StandardsPanel} entity=${sec.pipe_star_entity || `segment:${sec.segment_id}`}
          activeStrat=${sec.last_strat} strategies=${sec.strategies}
          sectionRank=${sec.rank} sectionPb=${sec.pb}
          family=${sec.pipe_star_entity ? "Pipe" : null}
          onChanged=${t.refresh} defaultOpen=${true} />
      <${FailureCompilation} identity=${{ segment_id: sec.segment_id }}
          defaultOpen=${true} />
    </details>
  </div>`;
}

// --- Route Practice focus (Phase C) ---------------------------------------
// Non-destructive focus layer: when a route is active the Practice tab shows
// ONLY that route's members, in route order. The current-step pointer reads
// the live target; clicking a candidate sets the target (retry anything
// freely). Driven by the route view (GET /api/routes/{id}) for order + names +
// %s, cross-referenced to the session view for the current step's full section.
const fpct = (r) => `${Math.round((r ?? 0) * 100)}%`;

function candIsTarget(c, tgt) {
  return c.kind === "segment"
    ? (tgt.kind === "segment" && tgt.segment_id === c.segment_id)
    : (tgt.kind !== "segment" && tgt.course_id === c.course && tgt.star_id === c.star);
}

async function setTargetCandidate(c, t) {
  await requestTarget(t, c.kind === "segment"
    ? { kind: "segment", segment_id: c.segment_id }
    : { course_id: c.course, star_id: c.star });
}

function RouteFocus({ rv, t, ui, freshIds, openCompare }) {
  const v = t.view;
  const tgt = v.target || {};
  // current = first step whose any candidate is the live target; else step 0
  // (the suggested start). next = the following step (badge only — advancing is
  // a suggestion; the target auto-follows completions, the user may click any
  // step to retry).
  let currentIdx = rv.steps.findIndex((s) =>
    s.candidates.some((c) => candIsTarget(c, tgt)));
  if (currentIdx === -1) currentIdx = 0;

  return html`<div>
    <div class="meta listhead">route — ${rv.name}</div>
    ${rv.steps.length === 0
      ? html`<p class="meta">This route has no steps yet — add some in the Routes tab.</p>`
      : null}
    ${rv.steps.map((s, i) => {
      const isCurrent = i === currentIdx;
      const badge = isCurrent
        ? html`<span class="chip routecur">▶ CURRENT</span>`
        : i === currentIdx + 1 ? html`<span class="chip">NEXT</span>` : null;
      return html`<div class="routefstep ${isCurrent ? "active-star" : ""}">
        <div class="shead">
          <span class="routenum">${i + 1}.</span>
          ${badge}
          ${s.candidates.length > 1
            ? html`<span class="chip">${s.need} of ${s.candidates.length}</span>` : null}
          ${s.label ? html`<b>${s.label}</b>` : null}
          ${s.candidates.map((c) => html`<button
              class=${candIsTarget(c, tgt) ? "pb-glow" : ""}
              onclick=${() => setTargetCandidate(c, t)}
              title="practice this">${c.display}</button>`)}
          <span style="flex:1"></span>
          ${s.rank ? html`<${RankIcon} tier=${s.rank.rank} division=${s.rank.division} size=${16} />` : null}
          <span class="routerate">step ${fpct(s.step_rate)}</span>
          <span class="routecum">cum ${fpct(s.cumulative)}</span>
        </div>
      </div>`;
    })}
  </div>`;
}

// Practice-log controls live IN the log card they act on (2026-07-24 UX
// pass): sort in the card heading, reset visibility in the footer. They used
// to sit in an "analysis toolbar" under the charts, which read as controls
// for the graphs.
function SortControl({ ui }) {
  return html`<label class="sort-control" title="Order the practice log">
    <${Icon} name="sort" size=${16} /><span class="sr-only">Sort attempts</span>
    <select value=${ui.sort} onchange=${(e) => ui.setSort(e.target.value)}>
      ${SORT_OPTIONS.map(([k, label]) => html`<option value=${k}>${label}</option>`)}
    </select></label>`;
}

function ResetFilterToggle({ ui }) {
  return html`<label class="reset-toggle" title="Hide reset attempts from the log">
    <${Icon} name="eyeOff" size=${16} />
    <input type="checkbox" checked=${ui.hideResets}
           onchange=${(e) => ui.setHideResets(e.target.checked)} />
    <span>Hide resets</span></label>`;
}

function EmptyPractice({ v, t, ui, unassignedRows, freshIds, openCompare,
                         hidden, showHidden, setShowHidden, openPicker,
                         inContext }) {
  // Two states, two remedies, and the caller owns the words (the emptystate
  // rule): standing in a course with nothing chosen, the fix is choosing. On
  // the game's main screen or a hub there is nothing here TO choose, and
  // "pick one above" would be pointing at the banner's own placeholder.
  return html`<div class="practice-detail-grid is-primary">
    <section class="practice-card objective-card objective-empty">
      <div class="objective-heading">
        <${ObjectiveEyebrow} iconName="target" label="Active objective"
          openPicker=${openPicker} />
        <div class="objective-name">
          <span class="objective-context">${inContext
            ? "Waiting for a target" : "Nothing to practice here"}</span>
          <h2>No active objective</h2>
        </div>
      </div>
      <div class="objective-metrics">
        <div class="rank-slot stable-empty compact">Rank —</div>
        <div class="objective-live-state"><span class="live-state-icon">○</span><span>Idle</span></div>
        <span class="pbtag">PB —</span>
      </div>
    </section>
    <section class="practice-card analysis-card">
      <div class="card-heading">
        <div><span class="eyebrow">Analysis</span><h3>Attempt history</h3></div>
      </div>
      <${EmptyState} headline="Nothing selected to practice"
          hint=${inContext
            ? "Pick a star or segment above — its timeline, trend and log all "
              + "fill in here. The session keeps recording either way."
            : "Move into a course and its stars and segments appear above — "
              + "you practice what you are standing in. The session keeps "
              + "recording either way."} />
    </section>
    <section class="practice-card attempts-card">
      <div class="card-heading attempts-heading">
        <div><span class="eyebrow">Practice log</span><h3>Unassigned attempts</h3></div>
        <div class="attempts-tools">
          <span class="meta">${unassignedRows.length} shown</span>
          <${StatMenuTrigger} t=${t} />
          <${SortControl} ui=${ui} />
        </div>
      </div>
      ${unassignedRows.length
        ? html`<div class="attempt-scroll">
            <${AttemptTable} attempts=${v.unassigned} rows=${unassignedRows}
              t=${t} freshIds=${freshIds} openCompare=${openCompare} />
          </div>`
        : html`<${EmptyState} headline="Nothing waiting for a target"
            hint=${"Runs you finish without a target picked collect here "
                 + "until you assign them."} />`}
      <div class="attempt-footer">
        <div class="attempt-pagination"></div>
        <div class="attempt-footer-tools">
          <${ResetFilterToggle} ui=${ui} />
          <${HideToggle} hidden=${hidden} showHidden=${showHidden}
              setShowHidden=${setShowHidden} />
        </div>
      </div>
    </section>
  </div>`;
}

export function Practice({ t, openCompare }) {
  const [showUnassignedHidden, setShowUnassignedHidden] = useState(false);
  const stored = localStorage.getItem("sm64.sort");
  const [sort, setSortState] = useState(
    SORT_OPTIONS.some(([k]) => k === stored) ? stored : "newest");
  const [hideResets, setHideResetsState] = useState(
    localStorage.getItem("sm64.hideResets") === "1");
  const ui = {
    sort, hideResets,
    setSort: (v) => { localStorage.setItem("sm64.sort", v); setSortState(v); },
    setHideResets: (v) => {
      localStorage.setItem("sm64.hideResets", v ? "1" : "0");
      setHideResetsState(v);
    },
  };
  const freshIds = useFreshAttemptIds(t);
  const [openTargetPicker, targetPickerDialog] = useTargetPicker(t);
  const { activeRouteId, pickRoute } = t;
  const [routeView, setRouteView] = useState(null);
  // Refetch the resolved route view on selection change AND on every session
  // view update, so per-step/cumulative % stay live as attempts land. A 404
  // (route deleted) clears it → the tab falls back to normal practice.
  useEffect(() => {
    if (activeRouteId == null) { setRouteView(null); return; }
    getJSON(`/api/routes/${activeRouteId}`).then(setRouteView).catch((e) => {
      setRouteView(null);
      // A 404 means the remembered route is GONE, so forget it. Clearing only
      // the local view left the stale id in localStorage, and this effect
      // re-runs on every session-view update — so a deleted route re-fetched
      // and 404'd on every event, forever, across reloads (live log
      // 2026-07-24). Any other failure (server restart, network blip) keeps
      // the selection: losing it would be worse than one empty render.
      if (e && e.status === 404) pickRoute(null);
    });
  }, [activeRouteId, t.view]);
  // Held while any rank on screen is mid-climb (user, 2026-07-27: "if the
  // celebration occurs, and then… they leave the stage, we should prevent the
  // practice UI from transitioning to the next stage until the celebration is
  // completed"). Grabbing the star and immediately walking out is the normal
  // way to end a run, so without this the reward is routinely cut off one
  // frame after it starts.
  //
  // What is held is the SELECTION — which stage, which target, which segment
  // is pinned — and nothing else. Holding the whole view instead deadlocks,
  // measured: the header's MARELO bar reads `t.marelo` rather than this view,
  // so it begins its own climb first, the hold engages, and the frozen view
  // then withholds the very rank-up that would have made the card's banner
  // climb — the page sat still through the entire celebration it was meant to
  // be protecting. Letting section DATA through is also just correct: the
  // attempt that earned the rank-up should appear in the log while the bar
  // climbs.
  const frozen = useHeldWhileCelebrating({
    target: (t.view && t.view.target) || null, stage: t.stage,
    armedOrder: t.armedOrder, lastPinnedSeg: t.lastPinnedSeg });
  const v = t.view && { ...t.view, target: frozen.target };
  if (!v) return html`<${PageState} kind=${t.connected ? "loading" : "offline"}
      title=${t.connected ? "Preparing your practice view" : "Waiting for the trainer"}
      message=${t.connected
        ? "Loading your target, attempts, and current stage…"
        : "The app will reconnect automatically when the local server is available."} />`;

  // `held` is `t` with the frozen SELECTION swapped in (and `view` carrying
  // the held target): every action and all section data still come from the
  // live store, and only which-stage-am-I-looking-at waits for the
  // celebration. `target` lives on the view, not on `t`, so it is spread
  // through `v` rather than listed here.
  const held = { ...t, view: v, stage: frozen.stage,
                 armedOrder: frozen.armedOrder, lastPinnedSeg: frozen.lastPinnedSeg };
  const tgt = v.target || {};
  const segs = v.segments || [];
  // Active star and active segment are mutually exclusive — a single practice
  // focus. The server keeps ONE target and retires the star target the moment
  // a segment arms OR Mario enters a different course (projection.py), so a
  // LIVE star target authoritatively means "doing stars": highlight that star
  // and suppress every segment pin. With no star target we're in segment-land
  // — pin armed > sticky-recent > target-segment as before. (Tied to the
  // server rule: don't reintroduce a frontend "armed beats star" override, or
  // setting a star while a segment is still armed would wrongly hide the star.)
  // ...and NEITHER is active where nothing can be practiced. The target
  // survives a hub on purpose (caveat 12 — the castle is transit, so an
  // exit-and-re-enter keeps it), but "survives" is not "active": a new session
  // on the game's main screen drew "No course target available" in the banner
  // and, directly below it, the PREVIOUS session's Lethal Lava Land star under
  // an ACTIVE TARGET eyebrow (live report 2026-07-27). Same predicate the
  // banner uses, asked of `held` so a celebration freezes both together, and
  // suppressing it here rather than inside the cards is what puts the target's
  // own section back in the practice index below instead of hiding it twice.
  //
  // ...and a pin must also BELONG to the course under your feet, which is a
  // SECOND rule (`practicedHere`) and not the same as having a context at
  // all: warping lobby -> Whomp's Fortress -> Hazy Maze Cave left "ACTIVE
  // SEGMENT LBLJ" on screen in both, each of which has a perfectly good
  // context of its own (live report 2026-07-27). The server had retired the
  // TARGET both times; what kept the card up was `lastPinnedSeg`, a sticky
  // client memory set on arm that no place change ever cleared — hence the
  // "Recent" tag rather than "Ready".
  const inContext = hasPracticeContext(held);
  const here = (sec) => practicedHere(sec, held);
  // `starPracticableHere`, NOT `inContext`: the latter's last clause is "some
  // segment is armed", which is about segments keeping themselves visible. In
  // the Castle Lobby a castle movement is armed, so `inContext` was true and a
  // Whomp's Fortress star still rendered as ACTIVE TARGET beside the banner's
  // own "No course target available" (live report 2026-07-30). The TARGET is
  // deliberately kept — the castle is transit and walking back in restores the
  // card — this only stops it claiming to be active where it cannot be run.
  const starActive = starPracticableHere(held)
    && tgt.kind !== "segment" && tgt.course_id != null;
  const isActiveStar = (sec) => sec.course_id === tgt.course_id
    && sec.star_id === tgt.star_id;
  const isActiveSeg = (sec) => tgt.kind === "segment"
    && sec.segment_id === tgt.segment_id;
  // `here` on the star too, which also makes the switch INSTANT: stage_changed
  // is broadcast-only and moves `t.stage` with no refetch, so between the warp
  // and the next view the target field still names the star you just left.
  const activeStar = starActive
    ? v.stars.find((sec) => isActiveStar(sec) && here(sec)) : undefined;
  const activeSeg = segs.find(isActiveSeg);
  // Pinned segments — presentation only, the target does not move:
  // every currently-ARMED segment is "active now" and pins to the top,
  // most recently armed first (armedOrder appends on arm → reverse).
  // With nothing armed, the sticky last-armed pin keeps the page on the
  // segment being practiced (an accidental exit disarms — correct timing
  // semantics — but the section stays put until a different segment arms);
  // before anything has ever armed, the target segment pins.
  // HUNDRED_COIN_EXIT segments used to arm on entering ANY course with a
  // 100-coin star and pin a "Segment · WF — 100 Coins → Exit" card the
  // moment you walked in, with no gate at all (live report 2026-07-30) --
  // a category-keyed isAmbientlyArmed exemption narrowed that here. Spec
  // 2026-07-28-multi-step-segments ("the 100-coin star IS the segment")
  // DISSOLVES that ONE family's problem outright: it never surfaces as a
  // segment section any more (views.py excludes it from `segments`/
  // `segment_targets` entirely, and its attempts attribute to the STAR),
  // so `segs.find(...)` below can never find one to pin for that family.
  //
  // But the SAME ambient-arm shape is not unique to it -- Bowser's
  // seg:reds->pipe:<abbrev> and the legacy exclusive pipe-entry trio arm
  // the identical way (course/stage entry) and DO still have segment
  // sections, so they still need the gate; the category string could never
  // have covered them (seg:reds->pipe:*'s own category is "Castle
  // Movement", indistinguishable from an ordinary movement). `sec.
  // arms_ambiently` (views.py, segments.arms_ambiently) is the GENERAL,
  // server-derived answer -- "does this def's own def arm on mere presence
  // rather than a deliberate action" -- so this gate now covers whatever
  // arms ambiently next with no JS-side enumeration to keep in step.
  const isAmbientlyArmed = (sec) => sec != null && sec.arms_ambiently
    && !(tgt.kind === "segment" && tgt.segment_id === sec.segment_id)
    && !justCompletedSegment(v, freshIds, sec.segment_id);
  const armedPins = [...frozen.armedOrder].reverse()
    .map((id) => segs.find((s) => s.segment_id === id))
    .filter(Boolean)
    .filter((sec) => !isAmbientlyArmed(sec));
  const stickyPin = frozen.lastPinnedSeg != null
    ? segs.find((s) => s.segment_id === frozen.lastPinnedSeg)
    : undefined;
  // ARMED pins are exempt from BOTH rules — a running timer is visible
  // wherever it has got to (user rule 2026-07-24), which is also why
  // `!inContext` can never drop one: armedPins is empty whenever that fires.
  // The other two must belong here UNLESS they are themselves still armed
  // (spec 2026-07-28-multi-step-segments, Task 6): armedPins is built from
  // `frozen.armedOrder`, a client-side push list that is empty on a fresh
  // page load even while the server already has a loose segment running
  // several courses into its sequence, so stickyPin/activeSeg are the paths
  // that catch it. `sec.armed_detail` is SERVER truth, re-derived from the
  // journal on every view fetch (views.py's armed_arms) — never
  // `lastPinnedSeg`/armedSegments, which is what let "ACTIVE SEGMENT LBLJ"
  // survive two course changes after the server had already retired it
  // (live report 2026-07-27, the comment above `here`'s own definition).
  // A pin that has genuinely disarmed carries `armed_detail: null` and falls
  // straight through to the plain `here()` course check, so that fix stays
  // intact — this only widens the exemption to a pin that is STILL running.
  const pinnedSegs = !inContext || starActive ? []
    : armedPins.length ? armedPins
    : stickyPin && !isAmbientlyArmed(stickyPin)
      && (stickyPin.armed_detail || here(stickyPin)) ? [stickyPin]
    : activeSeg && !isAmbientlyArmed(activeSeg)
      && (activeSeg.armed_detail || here(activeSeg)) ? [activeSeg] : [];
  // Only one detail surface owns the fixed Objective / Analysis / Attempts
  // tracks. Additional armed segments remain reachable in the stable index
  // below instead of inserting more full cards above the crop.
  const primarySeg = pinnedSegs[0];
  const restStars = v.stars.filter((sec) => sec !== activeStar);
  const restSegs = segs.filter((sec) => sec !== primarySeg);
  // Deliberately stable at a fixed view: live attempts must not reshuffle this
  // index underneath an OBS crop or a player's pointer. The active objective
  // above carries recency; this list follows the server's catalog order.
  const restSections = [...restStars, ...restSegs];

  const unassignedVisible = v.unassigned.filter(
    (a) => !a.cleared && a.outcome !== "abandoned");
  const unassignedHidden = v.unassigned.filter(
    (a) => a.cleared || a.outcome === "abandoned");
  // The unassigned log honors the same sort/reset controls as the section
  // logs — they render in its card too.
  const unassignedRows = (showUnassignedHidden ? v.unassigned : unassignedVisible)
    .filter((a) => !(hideResets
      && (a.outcome === "reset" || a.outcome === "hard_reset")))
    .slice()
    .sort(comparator(sort, t.clock));

  return html`<div class="practice-page">
    <${StageBanner} t=${held} freshIds=${freshIds} />

    ${/* ONE picker for the page, not one per section: only the primary card
         offers the trigger, and mounting a dialog's state inside every card
         in the practice index would pay for ~30 copies of a fetch effect
         that never runs. */""}
    ${activeStar
      ? html`<${StarSection} key=${`${activeStar.course_id}:${activeStar.star_id}`}
          sec=${activeStar} t=${held} ui=${ui} pinned=${true}
          openPicker=${openTargetPicker}
          freshIds=${freshIds} openCompare=${openCompare} />`
      : primarySeg
        ? html`<${SegmentSection} key=${`seg:${primarySeg.segment_id}`}
            sec=${primarySeg} t=${held} ui=${ui} pinned=${true}
            openPicker=${openTargetPicker}
            freshIds=${freshIds} openCompare=${openCompare} />`
        : html`<${EmptyPractice} v=${v} t=${held} ui=${ui}
            unassignedRows=${unassignedRows} freshIds=${freshIds}
            openCompare=${openCompare} hidden=${unassignedHidden}
            showHidden=${showUnassignedHidden} openPicker=${openTargetPicker}
            inContext=${inContext}
            setShowHidden=${setShowUnassignedHidden} />`}

    ${routeView
      ? html`<section class="practice-card route-focus-card">
          ${/* Was the practice toolbar's note. It is real state feedback, not
               guidance for a control, so it moves to the surface it is about
               rather than being deleted with the toolbar. */
            null}
          <p class="toolbar-note">Route focus is on · history still records</p>
          <${RouteFocus} rv=${routeView} t=${held} ui=${ui}
            freshIds=${freshIds} openCompare=${openCompare} />
        </section>`
      : restSections.length > 0 && html`<section class="practice-index">
          <div class="index-heading">
            <div><span class="eyebrow">Practice index</span><h3>Stars and segments</h3></div>
            <span class="meta">Stable catalog order · open any item for its history</span>
          </div>
          <div class="practice-index-list">
            ${restSections.map((sec) => html`<details class="practice-index-item">
              <summary>
                <span class="index-icon"><${Icon}
                  name=${sec.kind === "segment" ? "segments" : "practice"} size=${18} /></span>
                <span class="index-name">${sec.kind === "segment"
                  ? sec.name : `${sec.course_name} · ${sec.star_name}`}</span>
                <span class="meta">${sec.attempts.length} attempts</span>
                <span class="index-chevron"><${Icon} name="chevron" size=${16} /></span>
              </summary>
              ${sec.kind === "segment"
                ? html`<${SegmentSection} key=${`seg:${sec.segment_id}`}
                    sec=${sec} t=${held} ui=${ui} pinned=${false}
                    freshIds=${freshIds} openCompare=${openCompare} />`
                : html`<${StarSection} key=${`${sec.course_id}:${sec.star_id}`}
                    sec=${sec} t=${held} ui=${ui} pinned=${false}
                    freshIds=${freshIds} openCompare=${openCompare} />`}
            </details>`)}
          </div>
        </section>`}

    ${targetPickerDialog}
  </div>`;
}
