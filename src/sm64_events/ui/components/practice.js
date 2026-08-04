// src/sm64_events/ui/components/practice.js
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON } from "../api.js";
import { requestTarget } from "../target.js";
import { useUiLog } from "../uilog.js";
import { hasPracticeContext, justCompletedSegment,
        practicedHere, starPracticableHere, stageKey } from "../stagecontext.js";
import { StageBanner } from "./stagebanner.js";
import { RankBanner, rankColor } from "./ranks.js";
import { useHeldWhileCelebrating } from "../rankclimb.js";
import { CollapseToggle, cardClass, useCollapsed } from "./collapsible.js";
import { RankIcon } from "./rankicon.js";
import { StratPicker } from "./stratpicker.js";
import { StepTrack } from "./steptrack.js";
import { useTargetPicker } from "./targetpicker.js";
import { Icon } from "./icons.js";
import { PageState } from "./states.js";
import { displayName, entityKey, entityNoun, sectionClock, sectionPb }
  from "../entitysection.js";
import { EntityAnalysis, EntityDrawer } from "./entitydetail.js";
import { comparator, useGraphPick, PbTag,
         bannerLabel, bannerHint, ranksAreAtFloor, showsEntityBanner,
         rankIdentity, SORT_OPTIONS } from "./attemptlog.js";
import { liveSnapshot, resolveFocus, newestJournalId } from "../focustarget.js";
import { orderedSections, PracticeLog } from "./practicelog.js";

const html = htm.bind(h);

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

// The objective card's symbol + eyebrow, which on the ACTIVE card double as
// the target picker's trigger (the header's PRACTICE TARGET card owned that
// job until 2026-07-26 — see targetpicker.js for why it moved here). Shared
// by the star card, the segment card and the no-target card so all three open
// the same dialog from the same place; `openPicker` absent renders exactly the
// two plain spans that were there before.
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

// The objective card alone -- everything else this card used to carry
// (analysis, attempts log, drawer) is a page-level surface now (Task 6),
// following whichever entity the practice log has in FOCUS rather than
// whichever one is ACTIVE. `rows`/`pick` are handed straight to PbTag and
// used for nothing else: the graph-pick that reveals a row in the log now
// lives in `Practice`, over the FOCUSED section's rows, because the active
// card's own PB may not belong to the section currently in focus.
function StarSection({ sec, t, pinned, openPicker, rows, pick }) {
  const [foldTarget, toggleTarget] = useCollapsed("objective");
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
              groups=${sec.strategy_groups}
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
      ${/* No door here, and the reason is worth stating rather than
           leaving as a gap (rule 11): these steps belong to the hidden
           100-coin ENGINE definition, which views.py excludes from every
           picker on purpose. Opening it in the editor would offer to edit
           a definition the user is not allowed to target. */""}
      <${StepTrack} detail=${sec.armed_detail} />
    </section>
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
function SegmentSection({ sec, t, pinned, openPicker, rows, pick, openSegment }) {
  const [foldTarget, toggleTarget] = useCollapsed("objective");
  // armedSegs is the single live source: WS notices are instant, every view
  // fetch reconciles it so it cannot stay stale — see store.js refresh().
  const armed = t.armedSegs.has(sec.segment_id);
  const tgt = (t.view && t.view.target) || {};
  const isTarget = tgt.kind === "segment" && tgt.segment_id === sec.segment_id;
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
      <${StepTrack} detail=${sec.armed_detail}
        onEdit=${openSegment && !sec.broken
          ? () => openSegment(sec.segment_id) : null} />
    </section>
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

// The empty objective card alone -- the analysis half of "nothing selected"
// is the page-level EntityAnalysis's own empty state (it renders whenever
// `focusedSec` is null, which is exactly this state), and the unassigned
// attempts that used to fill a log card here are the practice log's own
// last card now (PracticeLog filters that bucket itself).
function EmptyPractice({ openPicker, inContext }) {
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
  </div>`;
}

export function Practice({ t, openCompare, openSegment }) {
  // Records what this page actually PAINTS — the selector's cells and every
  // objective card — so a report about a cell that "was just there a couple
  // frames ago" is readable afterwards instead of guessable from a
  // screenshot (../uilog.js, core/uilog.py). ONE observer for the whole page
  // deliberately: four banner row modes and three card types render here, and
  // an observer per surface is four things to keep in step with the markup.
  const pageRef = useRef(null);
  useUiLog(pageRef);
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
  // `newestAttemptId` rides the SAME freeze as target/stage rather than a
  // second mechanism beside it: it is one of resolveFocus's three signals
  // (ui/focustarget.js) and all three have to move together, or a browse
  // pick can be dropped mid-celebration by a signal none of this hold was
  // ever meant to let through. Reading it live instead (as `newestJournalId
  // (v)` further down used to) reproduces exactly: browse a non-active card,
  // an INCIDENTAL attempt lands elsewhere and starts a climb, the live
  // newestAttemptId changes on the next render while activeKey/stageKey stay
  // held, snapshotsAgree fails, and the browse pick drops WHILE the
  // celebration it should have waited for is still running.
  const frozen = useHeldWhileCelebrating({
    target: (t.view && t.view.target) || null, stage: t.stage,
    armedOrder: t.armedOrder, lastPinnedSeg: t.lastPinnedSeg,
    newestAttemptId: newestJournalId(t.view) });
  const v = t.view && { ...t.view, target: frozen.target };

  // `held` is `t` with the frozen SELECTION swapped in (and `view` carrying
  // the held target): every action and all section data still come from the
  // live store, and only which-stage-am-I-looking-at waits for the
  // celebration. `target` lives on the view, not on `t`, so it is spread
  // through `v` rather than listed here.
  //
  // This block (through `primarySeg` below) used to sit after an early
  // "still loading" return. It moved above that return in Task 6: the focus
  // resolver's `useGraphPick` call further down is a HOOK and must run on
  // every render regardless of whether `v` exists yet, so everything it
  // depends on has to tolerate `v` being null during the very first render,
  // before any view has loaded. `tgt`/`segs` are the only two lines that
  // needed a guard for that; everything after them already short-circuits
  // to empty/undefined whenever `tgt`/`segs` are empty.
  const held = { ...t, view: v, stage: frozen.stage,
                 armedOrder: frozen.armedOrder, lastPinnedSeg: frozen.lastPinnedSeg };
  const tgt = (v && v.target) || {};
  const segs = (v && v.segments) || [];
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
  // own section back in the practice log below instead of hiding it twice.
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
  // WHAT HE CLICKED LEADS. Same ruling as the three target thieves, applied
  // to the ORDER rather than to the write (live report 2026-08-02): "once we
  // select something, it should always appear in that display below, and
  // continue to be displayed while it's still valid and selected."
  //
  // This became reachable the moment "no active route" stopped meaning "no
  // filter" (segments.py::_route_allows): all FOUR `Bowser 2 → X` movements
  // now arm on the same event, `frozen.armedOrder` has no way to order things
  // that arrived together, and `armedPins[0]` was whichever landed first —
  // so tapping Upstairs lit that CELL while the card below kept reading
  // "Bowser 2 → BitS". Two surfaces, one question, two answers.
  //
  // `sort` is stable, so this promotes the target and leaves every other pin
  // in its existing most-recently-armed order.
  const isTargetedSeg = (sec) => sec != null && tgt.kind === "segment"
    && tgt.segment_id === sec.segment_id;
  const armedPins = [...frozen.armedOrder].reverse()
    .map((id) => segs.find((s) => s.segment_id === id))
    .filter(Boolean)
    .filter((sec) => !isAmbientlyArmed(sec))
    .sort((a, b) => (isTargetedSeg(b) ? 1 : 0) - (isTargetedSeg(a) ? 1 : 0));
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
  // The other half of the same ruling: an explicitly picked segment leads even
  // when it is NOT armed and something else is. Sorting armedPins alone would
  // only cover the case where his pick happens to be running too.
  const pickedSeg = segs.find(isTargetedSeg);
  // AN EMPTY HAND WITH SEVERAL THINGS RUNNING CLAIMS NOTHING (live report
  // 2026-08-02): standing Upstairs with `BitS Entry`, `Bowser 2 → WDW` and
  // `Bowser 2 → BitS` all armed and NO cell lit, the card still announced
  // "ACTIVE SEGMENT · BitS Entry · Running". *"When I don't have selected in
  // the UI, it should never display an active segment like this, especially
  // since there are multiple options."*
  //
  // The rule it bends is his own — "a RUNNING segment is never invisible"
  // (2026-07-24) — and that rule was written when at most one thing armed at
  // a time, so "the armed one" and "the one he means" were the same section.
  // Since `_route_allows` stopped treating "no route" as "no filter" they are
  // not: six movements arm off one exit. ONE armed segment is still shown,
  // because there is nothing to be ambiguous about and the old rule is right
  // there; TWO OR MORE with no pick shows none, because choosing between them
  // would assert a selection he never made — the same "what he clicked wins"
  // ruling, in the case where he has clicked nothing.
  const ambiguousPins = !pickedSeg && armedPins.length > 1;
  const pinnedSegs = !inContext || starActive || ambiguousPins ? []
    : pickedSeg && !armedPins.includes(pickedSeg)
        && (pickedSeg.armed_detail || here(pickedSeg))
      ? [pickedSeg, ...armedPins]
    : armedPins.length ? armedPins
    : stickyPin && !isAmbientlyArmed(stickyPin)
      && (stickyPin.armed_detail || here(stickyPin)) ? [stickyPin]
    : activeSeg && !isAmbientlyArmed(activeSeg)
      && (activeSeg.armed_detail || here(activeSeg)) ? [activeSeg] : [];
  // Only one detail surface owns the fixed Objective / Analysis / Log
  // tracks. Additional armed segments remain reachable in the practice log
  // below instead of inserting more full cards above the crop.
  const primarySeg = pinnedSegs[0];

  // Whose history the analysis card and detail drawer are drawing --
  // ui/focustarget.js's spring-loaded browse mode (Task 6, "the second we
  // start playing again in LLL (via a reset / star grab), or through
  // warping / basically anything that would trigger changing the
  // star/segment selector, that new area or star or segment should take
  // ownership of that card"). Read off `frozen`, never `t` directly, so a
  // celebration freezing the page freezes the takeover with it — a rank
  // climbing on the card you are browsing finishes before the page moves.
  const [manualFocus, setManualFocus] = useState(null);
  const live = liveSnapshot({
    activeKey: activeStar ? entityKey(activeStar)
      : primarySeg ? entityKey(primarySeg) : null,
    stageKey: stageKey(frozen.stage),
    // Frozen alongside its siblings (see the useHeldWhileCelebrating call
    // above) -- NOT re-read live here. A live read let a browse pick drop
    // mid-celebration the instant an unrelated attempt landed elsewhere,
    // which is exactly the takeover useHeldWhileCelebrating exists to delay.
    newestAttemptId: frozen.newestAttemptId,
  });
  const focusKey = resolveFocus(manualFocus, live);
  const focusedSec = v
    ? orderedSections(v).find((sec) => entityKey(sec) === focusKey) || null
    : null;
  const selectFocus = (key) => setManualFocus({ key, at: live });
  // The rows useGraphPick checks membership against -- the same default
  // filter every log card applies (cleared/abandoned hidden, resets per the
  // shared toggle). Computed here, at page level, because the trend graph
  // (EntityAnalysis) and the card a pick reveals a row in (a LogCard, in the
  // practice log below) are separate components now; useGraphPick used to
  // live inside the one card that drew both.
  const focusedRows = focusedSec
    ? focusedSec.attempts
        .filter((a) => !a.cleared && a.outcome !== "abandoned")
        .filter((a) => !(ui.hideResets
          && (a.outcome === "reset" || a.outcome === "hard_reset")))
        .slice()
        .sort(comparator(ui.sort, sectionClock(focusedSec, t.clock)))
    : [];
  // `visible`/`setVisible` below are NOT the pagination that ends up on
  // screen -- that lives inside whichever `LogCard` is selected, which is a
  // different component and cannot be reached from here. Passing a
  // page-level counter nothing renders from is exactly how this used to fail
  // silently past the tenth row: `reveal()` bumped an orphaned number,
  // believed it had widened the view, and the real (per-card) window never
  // moved. `Infinity`/a no-op make that plain rather than leaving a `10`
  // that looks load-bearing and is not -- `LogCard` itself now notices a
  // focused pick outside its own window and widens itself (practicelog.js).
  const { focus, pick, clearFocus } = useGraphPick(focusedRows, Infinity, () => {});
  // The active card's PB link jumps to a row in the LOG, which may not be
  // the section currently in focus. Clearing the manual pick first is what
  // makes the row reachable: `useGraphPick` already holds a pick whose
  // attempt is not in `rows` yet and reveals it when a later render brings
  // it in -- the same path the out-of-scope lifetime PB uses.
  const pickFromActive = (attemptId) => { setManualFocus(null); pick(attemptId); };

  if (!v) return html`<${PageState} kind=${t.connected ? "loading" : "offline"}
      title=${t.connected ? "Preparing your practice view" : "Waiting for the trainer"}
      message=${t.connected
        ? "Loading your target, attempts, and current stage…"
        : "The app will reconnect automatically when the local server is available."} />`;

  return html`<div class="practice-page" ref=${pageRef}>
    <${StageBanner} t=${held} freshIds=${freshIds} />

    ${/* ONE picker for the page, not one per section: only the primary card
         offers the trigger. Log cards (below) have no target-picker trigger
         of their own -- browsing a card's history is not the same gesture as
         picking a new target. */""}
    ${activeStar
      ? html`<${StarSection} key=${`${activeStar.course_id}:${activeStar.star_id}`}
          sec=${activeStar} t=${held} pinned=${true}
          openPicker=${openTargetPicker}
          rows=${focusedRows} pick=${pickFromActive} />`
      : primarySeg
        ? html`<${SegmentSection} key=${`seg:${primarySeg.segment_id}`}
            openSegment=${openSegment}
            sec=${primarySeg} t=${held} pinned=${true}
            openPicker=${openTargetPicker}
            rows=${focusedRows} pick=${pickFromActive} />`
        : html`<${EmptyPractice} openPicker=${openTargetPicker}
            inContext=${inContext} />`}

    <${EntityAnalysis} sec=${focusedSec} t=${held} onPick=${pick}
      manualPick=${manualFocus} />

    ${routeView && html`<section class="practice-card route-focus-card">
      ${/* Was the practice toolbar's note. It is real state feedback, not
           guidance for a control, so it moved to the surface it is about
           rather than being deleted with the toolbar. */
        null}
      <p class="toolbar-note">Route focus is on · history still records</p>
      <${RouteFocus} rv=${routeView} t=${held} ui=${ui}
        freshIds=${freshIds} openCompare=${openCompare} />
    </section>`}

    <${PracticeLog} v=${v} t=${held} ui=${ui} freshIds=${freshIds}
      openCompare=${openCompare} focus=${focus} clearFocus=${clearFocus}
      focusKey=${focusKey} onSelect=${selectFocus}
      activeKey=${live.activeKey} />

    <${EntityDrawer} sec=${focusedSec} t=${held} />

    ${targetPickerDialog}
  </div>`;
}
