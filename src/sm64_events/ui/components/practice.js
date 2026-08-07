// src/sm64_events/ui/components/practice.js
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { useUiLog } from "../uilog.js";
import { hasPracticeContext, justCompletedSegment,
        practicedHere, starPracticableHere, stageKey } from "../stagecontext.js";
import { StageBanner } from "./stagebanner.js";
import { useHeldWhileCelebrating } from "../rankclimb.js";
import { useTargetPicker } from "./targetpicker.js";
import { PageState } from "./states.js";
import { entityKey, sectionClock } from "../entitysection.js";
import { EntityAnalysis, EntityDrawer } from "./entitydetail.js";
import { comparator, useGraphPick, SORT_OPTIONS } from "./attemptlog.js";
import { liveSnapshot, resolveFocus, newestJournalId } from "../focustarget.js";
import { orderedSections, playedEntityKeys, PracticeLog } from "./practicelog.js";

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

export function Practice({ t, openCompare, openLibrary }) {
  // Records what this page actually PAINTS — the selector's cells and every
  // practice-log card — so a report about a cell that "was just there a
  // couple frames ago" is readable afterwards instead of guessable from a
  // screenshot (../uilog.js, core/uilog.py). ONE observer for the whole page
  // deliberately: several banner row modes render here, and an observer per
  // surface is several things to keep in step with the markup.
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
  // NO ROUTE VIEW IS FETCHED HERE ANY MORE. The route-focus card this fed is
  // deleted (2026-08-05) -- Griffin: "we should also just get rid of the
  // 'route focus' display card... this reads as noise. I don't think it's
  // actually useful in any way. Unfortunately it ended up not being a useful
  // feature." Choosing a route is unaffected: it lives on the header's route
  // rank card and drives the Run tab, the Rank tab's scope and the selector's
  // own route filtering (`loneOption`), none of which read this.
  //
  // The 404 handler that went with it is not owed a replacement. It existed
  // to forget a route deleted while selected, and predates `store.js`'s
  // reconcile effect, which ADOPTS the server's active route whenever this
  // client has no pick in flight -- so a route that no longer exists is
  // cleared from the authoritative side rather than by this tab noticing a
  // failed fetch. Deleting the fetch also deletes the failure mode it was
  // written for (a deleted route 404ing on every event, forever).
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
  //
  // `topKey` (auto-open-newest, 2026-08-04) rides the SAME freeze for the
  // identical reason: it is `t.view`'s own `stars`/`segments` re-sorted by
  // `last_activity`, which is NOT frozen by this hold (only the selection is
  // -- see the comment above), so an unrelated attempt landing elsewhere
  // during a celebration would otherwise be free to change which card the
  // practice log's own auto-open slot points at WHILE a rank is still
  // climbing on screen -- exactly the "a card closing underneath a running
  // celebration" regression this hold exists to prevent, one level removed
  // from the target/stage it was written for.
  const frozen = useHeldWhileCelebrating({
    target: (t.view && t.view.target) || null, stage: t.stage,
    armedOrder: t.armedOrder, lastPinnedSeg: t.lastPinnedSeg,
    newestAttemptId: newestJournalId(t.view),
    playedKeys: playedEntityKeys(t.view) });
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
  // THE AUTO-OPEN SLOT IS NOT DECIDED HERE. `PracticeLog` resolves it from
  // `playedKeys` against its OWN rendered list (`autoOpenKey`, practicelog.js)
  // -- this page cannot, because it does not know which cards survive
  // membership and Reds/Pipe exclusivity, and a slot naming an unrendered
  // card opens nothing while looking exactly like "nothing qualifies". That
  // was a real, shipped bug: the reds STAR and its pipe SEGMENT tie on
  // `last_activity`, so the slot named the half the log had filtered out and
  // every card sat closed.
  //
  // What stays here is the FREEZE. `playedKeys` rides the celebration hold
  // (the `useHeldWhileCelebrating` call above) because attempt data on `v` is
  // live even mid-climb, and a card flipping open or shut underneath a
  // running rank climb is exactly the takeover that hold exists to delay.
  // `live.activeKey` needs no freeze of its own -- it already derives from
  // `frozen.stage`/`frozen.target`, so it cannot move mid-celebration either.
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

  if (!v) return html`<${PageState} kind=${t.connected ? "loading" : "offline"}
      title=${t.connected ? "Preparing your practice view" : "Waiting for the trainer"}
      message=${t.connected
        ? "Loading your target, attempts, and current stage…"
        : "The app will reconnect automatically when the local server is available."} />`;

  return html`<div class="practice-page" ref=${pageRef}>
    <${StageBanner} t=${held} freshIds=${freshIds} />

    ${/* THE LOG COMES FIRST, at every width and in every mode.
         Griffin, 2026-08-05: "when I select an actual route, it incorrectly
         moves the practice log to be BELOW the route focus info. The
         practice log should always stay at the top, under the quick select
         picker."
         This was already his stated hierarchy -- "selector -> target ->
         practice log -> analysis (all the important information is
         hidden!!!!)" -- but it lived as a `@container (max-width: 1060px)`
         `order` block, so it held only when the pane was narrow, and never
         covered the route-focus card at all. Saying it in the DOM says it
         once, for every width and every mode, and let the CSS block go. */""}
    <${PracticeLog} v=${v} t=${held} ui=${ui} freshIds=${freshIds}
      openCompare=${openCompare} focus=${focus} pick=${pick}
      clearFocus=${clearFocus} openLibrary=${openLibrary}
      focusKey=${focusKey} onSelect=${selectFocus}
      activeKey=${live.activeKey} playedKeys=${frozen.playedKeys}
      openTargetPicker=${openTargetPicker} />

    <${EntityAnalysis} sec=${focusedSec} t=${held} onPick=${pick}
      manualPick=${manualFocus} />

    ${/* The Active Target card is gone (amendment A8 -- "the user can
         already understand what star / segment is active, AND get all of
         the relevant info inside the practice log now"). Its three jobs all
         live here instead: Ready/Running is the log's own highlight on the
         entity `activeKey` names (`active`, below); the PB jump is every
         card's own PbTag now (practicelog.js); the target-picker TRIGGER
         moved into this heading row (amendment A9), the dialog itself
         mounted once at the bottom of this page exactly as before. */""}
    <${EntityDrawer} sec=${focusedSec} t=${held} />

    ${targetPickerDialog}
  </div>`;
}
