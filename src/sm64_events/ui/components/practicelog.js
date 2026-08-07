// src/sm64_events/ui/components/practicelog.js
//
// The practice log: the session's history, grouped by what was practiced.
//
// It used to belong to ONE entity -- the active target's card carried it --
// which meant the only way to finish a movement was to leave the place that
// displays it. Griffin, task 0027: "The ONLY way for this segment to finish
// is by entering into BITFS, but by entering into BITFS, the DDD->BITFS
// segment disappears from the practice screen... we can never see how we
// performed in the segment we just completed."
//
// So the log is a page-level, recency-ordered list of entity cards, and the
// practice index it replaced is gone -- the index listed the same set in
// catalog order.
//
// ONE card serves both kinds. That is rule 11 becoming structural rather
// than an agreement between two hand-written sections that a test compares.
import { h } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import htm from "htm";
import { displayName, entityIdentity, entityKey, entityNoun, isSegment,
         sectionClock, sectionPb, standardsIdentity } from "../entitysection.js";
import { entityIconSrc, fallbackToGenericStar, fallbackSlotForEntityKey }
  from "./entityicons.js";
import { RankBanner } from "./ranks.js";
import { Icon } from "./icons.js";
import { ShrinkToFitName } from "./shrinkname.js";
import { StepTrack } from "./steptrack.js";
import { Disclose } from "./collapsible.js";
import { useFeedMotion } from "./feedmotion.js";
import { StratPicker } from "./stratpicker.js";
import { StandardsPanel } from "./standards.js";
import { AttemptTable, AttemptLogEmpty, HideToggle, SortControl,
         ResetFilterToggle, StatMenuTrigger, comparator, bannerLabel,
         bannerHint, ranksAreAtFloor, showsEntityBanner, rankIdentity, PbTag }
  from "./attemptlog.js";
import { logTuning, logTuningVars, logTuningClasses, rankPlacementFor,
         nextStepModeFor, NARROW_CONTAINER_PX } from "../logtuning.js";
import { bowserModeFor } from "./stagebanner.js";

const html = htm.bind(h);

// How many entity cards render before "Show 5 more". Superseded rule, kept
// legible rather than deleted outright: every shown card used to be OPEN by
// default (Griffin, at the time: "The drop down should always be opened by
// default"). With nine entities that read as a very long page -- the reason
// this whole condensed-card redesign happened at all -- and his later,
// narrower rule replaces it: only the ONE newest card opens on the system's
// own initiative (see `isCardOpen` below). An uncapped lifetime-scope view
// therefore renders every entity COLLAPSED but one, not expanded.
const CARDS_PER_PAGE = 5;

// How many attempt rows one page of an OPEN card shows. Real pagination
// (spec practice-log-entity-cards, amendment A8) -- "we should replace the
// show more with the number of pages we have to scroll through, and when we
// click on the PB, it would bring us to the correct page." Superseded the
// old "Show 10 more" reveal, which grew a `visible` threshold rather than
// moving a cursor -- two ways to decide what a card shows was exactly the
// kind of divergence this branch has already paid for once.
const ROWS_PER_PAGE = 10;

/**
 * Both kinds merged, newest activity first -- except the entity currently
 * being practiced, which always leads regardless of its own recency.
 *
 * `last_activity` is the SERVER's own journal-id stamp (views.py) and both
 * section lists arrive already sorted by it — this is the merge, not a
 * re-derivation. A section with no attempts in scope carries -1 and sorts
 * last: "a target you set and have not run yet is not the thing you were
 * just doing" -- true of any OTHER freshly-set target, but not of THIS one.
 *
 * CORRECTED (2026-08-05): entering Bowser 1 with it the only segment
 * available used to leave its card at the very bottom (recency -1) until an
 * attempt landed, then jump it to the top. Griffin: "it should be at the top
 * immediately (because it's the only star / segment available)." `activeKey`
 * is the SAME "is this the entity he is doing right now" signal `LogCard`'s
 * own `.log-card-active` gold border already reads (practice.js's
 * `live.activeKey`, itself `activeStar`/`primarySeg` -- the target the
 * player is standing in front of) -- reused, not reinvented, per the
 * standing rule against a second answer to "did he choose this." With no
 * active entity (or one not present in this view), the merge is byte-
 * identical to before.
 */
export function orderedSections(view, activeKey = null) {
  const sorted = [...(view.stars || []), ...(view.segments || [])]
    .slice()
    .sort((a, b) => (b.last_activity ?? -1) - (a.last_activity ?? -1));
  if (activeKey == null) return sorted;
  const idx = sorted.findIndex((sec) => entityKey(sec) === activeKey);
  if (idx <= 0) return sorted;          // not found, or already leading
  const [active] = sorted.splice(idx, 1);
  sorted.unshift(active);
  return sorted;
}

/**
 * Has a target-only, zero-attempt section EARNED a place in the log?
 *
 * Griffin: "If we leave without practicing anything, its card should
 * disappear from the list (because we didn't even reset / practice
 * anything)." The server's own rule keeps a section for the practice target
 * unconditionally (views.py, "the practice target ALWAYS gets a section") --
 * correct for what the SERVER can see (the target survives a hub on
 * purpose, caveat 12), but it has no notion of "he has since walked away and
 * touched nothing," which is a fact about the PLAYER's position and
 * therefore a client-side question, the same one `ui/stagecontext.js`'s
 * `practicedHere`/`starPracticableHere` already answer for the highlight on
 * this very card (`active`, below) -- reused via `activeKey`, not
 * reinvented.
 *
 * Three ways in, checked in order:
 *   1. A real attempt landed in scope, ever -- unconditional. "A card that
 *      recorded even one attempt stays" (the log is a record of what he DID,
 *      not of what is merely selected).
 *   2. It is still ARMED (`armed_detail` non-null, star or segment) -- the
 *      standing "a RUNNING segment is never invisible" rule (2026-07-24),
 *      checked independently of `activeKey` on purpose: several defs can arm
 *      off one course entry with no single one of them "the" pick
 *      (practice.js's own `ambiguousPins`), and none of them may vanish
 *      merely because none is unambiguous.
 *   3. It is the entity `activeKey` names AND it has a real course of its
 *      own (`course_id != null`). The course_id guard is the one piece that
 *      is NOT a restatement of `activeKey` -- measured live (a synthetic
 *      TrackerService run, entering the Bowser 1 arena, auto-selecting its
 *      fight, then leaving to the lobby with nothing grabbed): the fight
 *      disarms correctly (the topological engine's own doing), but
 *      `practicedHere`'s course-less bucket treats EVERY course-less place
 *      (the castle, any hub, any OTHER arena) as "still here" for an
 *      arena-originated entity -- so `activeKey` kept naming it long after
 *      he had genuinely left the arena for the lobby, still with zero
 *      attempts. A course-BEARING entity (an ordinary star, most castle
 *      movements) has no such gap: `practicedHere` requires the player's
 *      OWN course to match exactly, so `activeKey` alone already means "he
 *      is standing right where this is practiced."
 */
export function hasEarnedACard(sec, activeKey) {
  if (sec.attempts && sec.attempts.length > 0) return true;
  if (sec.armed_detail != null) return true;
  return sec.course_id != null && activeKey != null && entityKey(sec) === activeKey;
}

/**
 * A Bowser course's Reds star and its paired reds->pipe segment are ONE run
 * graded two ways, never two things practiced -- Griffin: "if I have pipe
 * selected, it shouldn't show the card for (Star). If I have (Star)
 * selected, and I grab the star, it shouldn't show the pipe card... if I
 * enter the pipe, it should show the pipe card (and swap to pipe mode)."
 *
 * Only excludes when BOTH halves of a pair are present at once (a course
 * he has only ever practiced one way keeps its one card regardless of what
 * `modeForCourse` happens to answer for it, including the untouched
 * "pipe" default -- there is nothing to resolve a conflict between). When
 * both are present, the one matching `modeForCourse(sec.course_id)` wins;
 * everything else, including every OTHER course's Bowser pair, passes
 * through untouched.
 *
 * `modeForCourse` is injected rather than read from storage in here on
 * purpose -- `stagebanner.js`'s `bowserModeFor` already owns that memory
 * (keyed by LEVEL, not course_id) and this function stays a pure,
 * node-testable transform of a `(sections, lookup)` pair, never a second
 * reader of `localStorage`.
 */
export function applyRedsPipeExclusivity(sections, modeForCourse) {
  if (!modeForCourse) return sections;
  const byKey = new Map(sections.map((sec) => [entityKey(sec), sec]));
  const exclude = new Set();
  for (const sec of sections) {
    if (isSegment(sec) || sec.pipe_segment_id == null) continue;
    const pipeKey = `segment:${sec.pipe_segment_id}`;
    if (!byKey.has(pipeKey)) continue;    // only one half present -- nothing to resolve
    const mode = modeForCourse(sec.course_id);
    exclude.add(mode === "pipe" ? entityKey(sec) : pipeKey);
  }
  return exclude.size ? sections.filter((sec) => !exclude.has(entityKey(sec))) : sections;
}

/**
 * The entity key of the TOP card -- the newest thing practiced, and the one
 * the system's single auto-open slot always points at (Griffin, 2026-08-04:
 * "We should automatically keep open the last entry in the system... the top
 * entry is the newest one, and it's auto-opened by default"). Null once
 * `view` has nothing classified in it yet (the unassigned bucket is not an
 * entity and is never eligible -- UnassignedLogCard's own comment).
 */
// Has this entity actually recorded anything in the current scope? THE one
// question the auto-open slot turns on, exported because two callers ask it
// about two different entities (the recency winner, below; the ACTIVE one, in
// practice.js) and the first version of this rule was applied to only one of
// them -- which is exactly how a card he had just selected kept auto-opening
// with nothing in it after the rule below already said it must not.
export const hasRecordedAttempts = (sec) => (sec.attempts || []).length > 0;

// Every entity key that has recorded something, in recency order. Frozen
// alongside the rest of the celebration snapshot (practice.js) so that
// "does the active card qualify" cannot change its answer mid-climb.
export function playedEntityKeys(view) {
  if (!view) return [];
  return orderedSections(view).filter(hasRecordedAttempts).map(entityKey);
}

export function topEntityKey(view) {
  if (!view) return null;
  // EMPTY ENTITIES ARE NOT ELIGIBLE for the auto-open slot, which is a
  // narrower rule than "the top card" and deliberately not the same question
  // as which card LEADS the list.
  //
  // Griffin, 2026-08-05: "The preselected option should be closed by default
  // because otherwise we're wasting space to tell the user they dont have
  // anything practiced, which they already know. When we actually have an
  // attempt or reset, then it autoopens."
  //
  // Selecting something puts its card FIRST (orderedSections' `activeKey`
  // hoist, the active-leads round) -- but opening it there spends most of a
  // screen on an empty state whose entire message is "nothing yet", which is
  // the one thing he can already see from the card he just picked. So the
  // slot skips a section with no attempts in scope, and the same entity
  // claims it the moment its first row lands, because recording an attempt
  // makes it the newest by activity too. No separate "now open it" trigger
  // has to exist.
  //
  // Note this reads `orderedSections(view)` WITHOUT the active key on
  // purpose: the slot follows recency, and the active-leads hoist is a
  // PRESENTATION order. Passing the key here would make a freshly chosen
  // entity the slot holder again and undo exactly this rule.
  const played = playedEntityKeys(view);
  return played.length ? played[0] : null;
}

/**
 * Whether ONE card should be open, absent a forced reveal (`forceOpen`,
 * below). Two facts, in order:
 *
 *   1. has the user EVER touched this card's own fold himself? His choice
 *      always wins, in whichever direction, and outlives every later change
 *      of which entity is newest -- "arriving entries must never close" a
 *      card he opened, and a card he closed "stays closed... not on the next
 *      view refresh, not on a re-render."
 *   2. failing that, does this entity currently hold the system's ONE
 *      auto-open slot -- is it the top of the recency-ordered list?
 *
 * `overrides` maps entityKey -> "open" | "closed", and is expected to live
 * ABOVE any one card's own mount lifetime (`PracticeLog`'s own state, not
 * `LogCard`'s) -- a card that falls off the page's pagination and later
 * returns must not forget a manual choice just because its component
 * unmounted in between.
 *
 * Pure, so node drives it directly (tests/test_ui_practice_log.py), the same
 * reason `orderedSections` above is.
 */
// WHICH CARD HOLDS THE ONE AUTO-OPEN SLOT, decided against the list actually
// ON SCREEN. Pure; `sections` is the RENDERED list (post-membership,
// post-Reds/Pipe exclusivity), `playedKeys` is `playedEntityKeys(view)` in
// recency order, frozen by the caller's celebration hold.
//
// Two rules, in order:
//
// 1. The ACTIVE entity takes the slot once it has recorded something.
// 2. Otherwise the slot STAYS on the most recently played card still on
//    screen -- it does not go dark. Griffin, 2026-08-05: "when we're moving
//    between courses / segments of the game, I want to still see the thing I
//    just accomplished (until I've now accomplished the next star/segment)...
//    we shouldn't close the last thing until we've started a new one (with a
//    valid practice log entry)." Walking into Bowser 1 selects it, and its
//    empty card correctly does not open (the rule above) -- but the reds run
//    he just finished should not close in the same instant, and it now keeps
//    the slot until Bowser 1's own first row lands, at which point rule 1
//    hands it over.
//
// READING THE RENDERED LIST IS THE LOAD-BEARING PART, not a tidy-up. This was
// resolved in practice.js against the UNFILTERED view, and the two lists
// genuinely disagree: a Bowser course's reds star and its pipe segment tie on
// `last_activity` (measured on his live session -- both 1414), stars sort
// before segments, and `applyRedsPipeExclusivity` renders whichever half his
// star/pipe toggle names. So the slot named the STAR while the log drew the
// SEGMENT, `isCardOpen` matched nothing, and every card sat closed -- which is
// exactly the screenshot that opened this round. A key that names no rendered
// card is indistinguishable from "nothing qualifies", which is why it survived
// a rule whose own tests were all green.
export function autoOpenKey(sections, activeKey = null, playedKeys = []) {
  const rendered = sections || [];
  if (activeKey != null) {
    const active = rendered.find((sec) => entityKey(sec) === activeKey);
    if (active && hasRecordedAttempts(active)) return activeKey;
  }
  for (const key of playedKeys || []) {
    if (rendered.some((sec) => entityKey(sec) === key)) return key;
  }
  return null;
}

export function isCardOpen(overrides, topKey, key) {
  const manual = (overrides || {})[key];
  if (manual != null) return manual === "open";
  return topKey != null && key === topKey;
}

export function LogCard({ sec, t, ui, freshIds, openCompare, focus,
                          clearFocus, pick, selected, onSelect, forceOpen,
                          open, onSetOpen, openSegment, openLibrary = null,
                          active = false,
                          nameOverflow = "ellipsis", rankIconSize = 24,
                          rankPlacement = "head", nextStepMode = "classic" }) {
  const [showHidden, setShowHidden] = useState(false);
  const [page, setPage] = useState(0);
  // `forceOpen` exists for exactly one moment (Task 6 brief, Step 1): "so a
  // pick on a COLLAPSED card has a scroll target to find" -- it must win
  // just long enough to make the row exist, never permanently. Folding
  // `isOpen = open || forceOpen` (as written when this card first landed)
  // makes `forceOpen` a standing override for as long as the card stays
  // SELECTED, which is most of a browsing session -- the fold chevron
  // renders, answers `onclick`, flips `open`, and `isOpen` never moves,
  // because `forceOpen` is still true. Caught by this task's own Step 8
  // render check (browser only -- no source-scan or unit test can see a
  // control that is clickable and inert). The fix keeps `forceOpen` as a
  // one-shot NUDGE instead of a continuous OR: syncing `open` to true the
  // moment this card becomes the selected one, or the moment a NEW graph
  // pick lands while it already is one (`focus.nonce` changes on every
  // reveal, even a repeat pick on the same still-selected card) -- either
  // way exactly the instant a scroll target might not exist yet. Once
  // synced, `open` is the only thing `isOpen` reads, so the fold button
  // keeps working for the rest of the time this card is selected.
  //
  // `open`/`onSetOpen` moved UP to `PracticeLog` (auto-open-newest, 2026-08-04)
  // -- a card's fold is no longer this component's own `useState`, because the
  // system's one auto-open slot has to keep pointing at whichever entity is
  // newest even while THIS card is unmounted (paginated away, then revealed
  // again by "Show 5 more"). `onSetOpen(true)` here writes into that lifted
  // map exactly the way a genuine chevron click below does -- from this
  // card's own point of view nothing changed, it still just "stays open."
  //
  // Gated on `focus` too, added the SAME round: `forceOpen` alone
  // (`ek === focusKey`) goes true whenever this entity merely becomes the
  // ACTIVE one -- which happens from gameplay alone (a new star target, a
  // segment arming), no click involved -- not only from clicking this card's
  // own header. Before this line existed, that silently reopened a card the
  // user had just closed himself the moment the game made it active again,
  // proven live: closing a card, then letting an unrelated segment arm and
  // become the focus, reopened it with no gesture of his own -- exactly what
  // "a card he closed stays closed... not on the next view refresh" forbids.
  // `focus` genuinely non-null is what tells a real PICK (a PbTag/graph-dot
  // click, which always sets it) apart from a mere focus drift: a pick is the
  // one case this nudge exists FOR ("so a pick on a collapsed card has a
  // scroll target to find"), and it still fires with no override at all --
  // `isCardOpen` never sees a pick, only the plain click this effect makes.
  useEffect(() => {
    if (forceOpen && focus) onSetOpen(true);
  }, [forceOpen, focus && focus.nonce]);
  const ek = entityKey(sec);
  const standards = standardsIdentity(sec);
  const clock = sectionClock(sec, t.clock);
  const named = displayName(sec, (t.view.catalog || {}).courses || []);
  const base = showHidden ? sec.attempts
    : sec.attempts.filter((a) => !a.cleared && a.outcome !== "abandoned");
  const hidden = sec.attempts.filter((a) => a.cleared || a.outcome === "abandoned");
  const rows = base
    .filter((a) => !(ui.hideResets
      && (a.outcome === "reset" || a.outcome === "hard_reset")))
    .slice()
    .sort(comparator(ui.sort, clock));
  // A pick (this card's own PbTag jump, or a trend-graph dot in
  // EntityAnalysis) is resolved at PAGE level -- Practice's own
  // `useGraphPick`, over whichever entity is FOCUSED -- and that hook has no
  // visibility into any card's own row PAGINATION; it never did. THIS card
  // owns the one `page` that actually governs `shown` below, so it is the
  // one that has to notice a focused pick landing outside its own current
  // page and turn to the right one -- the same one-shot-nudge shape
  // `forceOpen` already uses above, keyed on the same `focus.nonce` so a
  // repeat pick on an already-turned card still re-checks. Real pagination
  // (spec practice-log-entity-cards, amendment A8) replaced the old
  // "Show 10 more" reveal, which grew a `visible` threshold instead of
  // moving a cursor -- a PB link has to land on a PAGE, not on a
  // progressively-widened window, and the two mechanisms were never allowed
  // to coexist (this branch has already paid once for two ways to decide
  // what a card shows).
  useEffect(() => {
    if (!selected || !focus) return;
    const idx = rows.findIndex((a) => a.id === focus.id);
    if (idx === -1) return;               // not this card's attempt
    setPage(Math.floor(idx / ROWS_PER_PAGE));
  }, [selected, focus && focus.nonce]);
  const pageCount = Math.max(1, Math.ceil(rows.length / ROWS_PER_PAGE));
  // Clamped rather than trusted outright: the row count can shrink out from
  // under a stored page number (toggling "Hide resets", clearing an
  // attempt), and a stale page past the new last one must fall back to the
  // last real page rather than render nothing.
  const currentPage = Math.min(page, pageCount - 1);
  const shown = rows.slice(currentPage * ROWS_PER_PAGE,
                           currentPage * ROWS_PER_PAGE + ROWS_PER_PAGE);
  const isOpen = open;
  // The active card's own PB jumps to a row IN THIS SAME CARD -- select it
  // (a no-op if it already is the focused entity) and hand the attempt id to
  // the page-level pick, which retries once `focusedRows` -- and therefore
  // this card's own `rows` -- actually reflect the newly-selected entity
  // (`useGraphPick`'s own `pendingId` retry, attemptlog.js). Opening the
  // card and turning to the right page both fall out of machinery that
  // already exists for the trend-graph pick: `forceOpen` syncs `open` true
  // the moment this card is both selected AND carries a landed `focus`
  // (above), and the page-turn effect just above this one runs the instant
  // `rows` includes the picked attempt.
  const pbPick = (attemptId) => { onSelect(ek); pick(attemptId); };
  const broken = isSegment(sec) && sec.broken;
  // Which of the layout matrix's two ladder-count cells this card falls
  // into (index.html's "Layout matrix" CSS section) -- the SAME predicate
  // that decides whether the second `<RankBanner>` below renders at all, so
  // the class can never disagree with what is actually on screen. Server
  // truth end to end: `sec.one_ladder` (views.py::ranks_share_ladder) ->
  // `showsEntityBanner` -> this class -> CSS picks the cell; nothing here
  // re-derives it.
  const twoLadder = showsEntityBanner(sec);
  // Whether this card's rank displays live in the head (today) or the body
  // (spec practice-log-entity-cards, round 3 -- Griffin: "imagine we display
  // the ranked display inside the dropdown when it's opened... If I close
  // the dropdown, I wouldn't see the rank standards in this mode, and
  // instead, the course identity would take up the space"). `rankPlacement`
  // is resolved once per section by `PracticeLog` (below), never here --
  // this is the third crack in "LogCard never imports the registry"
  // (nameOverflow/rankIconSize are the other two), because a CSS class alone
  // cannot move a MOUNTED `RankBanner` between two DOM subtrees without
  // restarting its climb. The markup itself is built ONCE, as a value,
  // so there is exactly one `.log-card-ranks` in the DOM regardless of
  // which placement applies -- never a second copy rendered and hidden,
  // which would double-run `useRankClimb` for the same lane/order identity.
  const inBody = rankPlacement === "body";
  const ranksBlock = html`<div class="log-card-ranks">
      <${RankBanner} label=${bannerLabel(sec, entityNoun(sec))}
          hint=${bannerHint(sec, entityNoun(sec))} banner=${sec.rank}
          atFloor=${ranksAreAtFloor(sec)} lane=${ek} order=${0}
          replayKey=${sec.last_strat || ""}
          identity=${rankIdentity(ek, "strategy", sec, t)}
          showNext=${active} iconSize=${rankIconSize}
          nextStepMode=${nextStepMode} />
      ${twoLadder && html`<${RankBanner}
          label=${entityNoun(sec)} banner=${sec.entity_rank}
          atFloor=${ranksAreAtFloor(sec)} lane=${ek} order=${1}
          identity=${rankIdentity(ek, "entity", sec, t)}
          showNext=${active} iconSize=${rankIconSize}
          nextStepMode=${nextStepMode} />`}
    </div>`;
  return html`<section data-feed-key=${ek}
      class="log-card ${selected ? "is-selected" : ""}
      ${active ? "log-card-active" : ""}
      ${isOpen ? "" : "is-closed"} ${twoLadder ? "log-card-two-ladder" : "log-card-one-ladder"}
      ${inBody ? "log-card-ranks-in-body" : ""}">
    ${/* The HEADING selects; the chevron opens. Two gestures, two targets,
         so browsing a card's graphs and folding it away never fight. */""}
    <div class="log-card-head">
      <div class="log-card-identity">
        <button type="button" class="log-card-select" onclick=${() => onSelect(ek)}
            aria-pressed=${selected ? "true" : "false"}
            title=${`Show ${named.name}'s timeline and trend above`}>
          <img class="log-card-art" src=${entityIconSrc(t, ek)} alt=""
            onerror=${(e) => fallbackToGenericStar(e, fallbackSlotForEntityKey(ek))} />
          <span class="log-card-name">
            <span class="log-card-context">${named.context}</span>
            <${ShrinkToFitName} text=${named.name}
              enabled=${nameOverflow === "shrinkToFit"} />
          </span>
        </button>
        ${/* ON THE IDENTITY LINE, not under it (Griffin, 2026-08-05): "when
             there's a step X of X display, we should display it right after
             the identity display to the right... That way, we can maintain
             the single line height design with these cards." It rendered as
             a sibling of `.log-card-head` before, which made every armed
             card two rows tall while every other card stayed one.
             `StepTrack` renders nothing when `armed_detail` is null, which
             is true of every ordinary card, so this adds no element to the
             common case. `onEdit` -- the doorway into the definition -- stays
             gated on `active` (amendment A8): only the entity actually being
             practised offers "edit this movement", never a card that merely
             happens to appear in the log. */""}
        <${StepTrack} detail=${sec.armed_detail}
          onEdit=${active && openSegment && isSegment(sec) && !sec.broken
            ? () => openSegment(sec.segment_id) : null} />
        ${/* SUPERSEDED (2026-08-04, one-line-heads round): the Ready/Running
             word that used to sit here is DELETED, not merely hidden --
             Griffin: "we should remove the 'ACTIVE' indicator (the card is
             already highlighted so it's obvious it's active)". This word
             was itself only one round old, added as the replacement for the
             deleted Active Target card's own live-state chip (amendment
             A8) -- his ruling now is that the gold `.log-card-active`
             border already carries that meaning on its own, the same thing
             he said the day it was added ("this is already covered by the
             most recent entry in the practice log being highlighted"), so
             this is removing an over-implementation rather than reversing a
             decision. For an ARMED entity specifically, nothing legible was
             lost: `<${StepTrack}>` below renders unconditionally off
             `sec.armed_detail` (never gated on `active`), so "Step X of N ·
             <place>" already told the running story in more detail than the
             bare word "Running" ever did -- confirmed by reading the render,
             not assumed. */""}
        ${/* The strategy NAME becomes the same picker the (now-deleted)
             Active Target card used (amendment A2: "replace the strategy
             name in the card with the same exact drop down we use for
             selecting the strategy... within each card, we would be able to
             change the active strategy"). One card per entity, its attempts
             labelled per strategy and graded on their own ladder -- the
             head's rank follows whichever strategy is picked here, and a
             switch fires the SAME rank-transition animation a legitimate
             strategy change already does (rankIdentity below folds
             `sec.last_strat` into the banner's identity, so this write is
             not a special case for the climb). Unconditional, not gated on
             `inBody`: even in head-placement mode the card needs a way to
             change strategy, and an unset strategy (`sec.last_strat` null)
             still needs the control to SET one, not just to display one
             already chosen. Deleted definitions have nothing left to write
             a strategy onto (SegmentSection's own rule, entitysection.js's
             `entityIdentity` covers only a live def).

             SITS BESIDE THE IDENTITY DISPLAY now, not stacked under it
             (this round, 2026-08-04) -- Griffin: "we should move the
             selector to the right of the identity display. As a result,
             this should also reduce the entire height of the card, because
             we can fit it on one line -- they should all match the size of
             the unassigned card." `.log-card-identity` switched from a flex
             COLUMN (icon+name, then this picker, stacked into up to three
             rows) to a flex ROW: this picker is simply that div's second
             child now, in the SAME grid area identity already owned, so no
             new grid track was needed and nothing here can steal width from
             the ranks column. With the state word above gone too, the
             identity area is back to exactly one row of content -- the
             icon's own height -- which is what lets the head settle back to
             the same 60px `min-height` floor the unassigned card's head has
             always rendered at (icon-size 40px + head padding 10px * 2). */""}
        ${broken
          ? html`<span class="log-card-strat">Definition deleted</span>`
          : html`<span class="log-card-strat-picker">
              <${StratPicker} entity=${ek} identity=${entityIdentity(sec)}
                  strategies=${sec.strategies} active=${sec.last_strat}
                  groups=${sec.strategy_groups} allowBlank=${!sec.default_strat}
                  onChanged=${t.refresh} /></span>`}
      </div>
      ${!inBody && ranksBlock}
      ${/* The PB is a real link now (amendment A8): "if the card is closed,
           and I click that, it should open the card, and scroll allll the
           way to that actual PB entry." `pbPick` selects this card (a no-op
           if it already is the focused entity) and hands the id to the
           page-level `pick` -- the SAME machinery `forceOpen`'s own effect
           (above) and the page-turn effect (above) already exist for a
           trend-graph dot; a PB link needs no second implementation of
           "open + turn to the right page + scroll + flash". */""}
      <${PbTag} pb=${sectionPb(sec, t.clock)} mode=${clock} rows=${rows}
        pick=${pbPick} t=${t} />
      ${/* The book mark (spec 2026-08-07-library-page, section 1: "opens the
           Library at the current target and strategy") -- a doorway OUT to
           the community sheet for whatever this card is showing, the same
           relationship StepTrack's onEdit already has to the Segments tab.
           Shares the fold button's grid-area/flex row (`.log-card-actions`)
           rather than claiming a new column -- adding a fifth named area
           would mean re-deriving it across all four layout matrix cells
           (oneLine/twoLine/stacked/stackNarrow) for one small icon, and
           `.log-card-head`'s own comment already names that as the bug this
           card's grid exists to avoid (the unassigned-card double-height
           regression). Omitted entirely when the caller has no door to offer
           (`ui/tunelog.js`'s inspector, which never passes it) -- never a
           disabled button with nothing behind it. */""}
      <div class="log-card-actions">
        ${openLibrary && html`<button type="button" class="log-card-library-link"
            onclick=${() => openLibrary({ kind: "target", entity: ek, strat: sec.last_strat })}
            title=${`Open ${named.name} in the Library`}>
          <${Icon} name="bookmark" size=${15} />
        </button>`}
        <button type="button" class="log-card-fold" onclick=${() => onSetOpen(!isOpen)}
            aria-expanded=${isOpen ? "true" : "false"}
            title=${`${isOpen ? "Collapse" : "Expand"} ${named.name}'s attempts`}>
          <${Icon} name="chevron" size=${18} />
        </button>
      </div>
    </div>
    ${/* The SAME component StarSection/SegmentSection used to render for
         their own objective card (steptrack.js), and the same reason:
         `armed_detail` is SERVER truth, re-derived from the journal on every
         view fetch, and it is NOT segment-only -- the 100-coin star carries
         it too. `LogCard` is the only surface any entity gets now, so it is
         the one that carries the row for every card, or "is the system
         aware I'm mid-movement" silently stops being answerable the moment
         that movement is not also the active target. `StepTrack` renders
         nothing when `armed_detail` is null -- true of every ordinary card.
         `onEdit` -- the doorway into the definition -- is now gated on
         `active` rather than on being a separate pinned card (the Active
         Target card it used to be exclusive to is deleted, amendment A8):
         only the entity actually being practised offers "edit this
         movement", never a card that merely happens to also appear in the
         log. */""}
    <${Disclose} open=${isOpen} className="log-card-disclose">
      <div class="log-card-body">
      ${inBody && ranksBlock}
      ${rows.length
        ? html`<${AttemptTable} attempts=${sec.attempts} rows=${shown} t=${t}
            focus=${selected ? focus : null} clearFocus=${clearFocus}
            freshIds=${freshIds} openCompare=${openCompare} sec=${sec} />`
        : html`<${AttemptLogEmpty} hasAttempts=${sec.attempts.length > 0} />`}
      <div class="attempt-footer">
        ${/* Real pagination (amendment A8), replacing "Show 10 more": "we
             should replace the show more with the number of pages we have
             to scroll through, and when we click on the PB, it would bring
             us to the correct page... the best practice for this type of
             pagination and easy navigation to the beginning / end of the
             list." First/Prev/page-count/Next/Last -- the conventional
             shape, rather than an invented one. Hidden entirely at one page,
             same as the old buttons hid themselves when nothing more was
             left to reveal. */""}
        ${pageCount > 1 && html`<div class="attempt-pagination">
          <button class="quiet-button" onclick=${() => setPage(0)}
              disabled=${currentPage === 0} title="First page">« First</button>
          <button class="quiet-button" onclick=${() => setPage(currentPage - 1)}
              disabled=${currentPage === 0} title="Previous page">‹ Prev</button>
          <span class="meta attempt-pagination-info">Page ${currentPage + 1} of ${pageCount}</span>
          <button class="quiet-button" onclick=${() => setPage(currentPage + 1)}
              disabled=${currentPage >= pageCount - 1} title="Next page">Next ›</button>
          <button class="quiet-button" onclick=${() => setPage(pageCount - 1)}
              disabled=${currentPage >= pageCount - 1} title="Last page">Last »</button>
        </div>`}
        <div class="attempt-footer-tools">
          <${HideToggle} hidden=${hidden} showHidden=${showHidden}
            setShowHidden=${setShowHidden} />
        </div>
      </div>
      ${/* PER CARD, and CLOSED (Griffin, 2026-08-05): "let's actually move
           this 'Rank Standards' dropdown to the INSIDE of the individual
           practice log cards... for each card, we have the Rank Standards
           dropdown. It should be closed by default. It should display
           whatever the rank standards are for that specific star/segment."
           It was one page-level panel in `EntityDrawer`, following whichever
           entity was focused -- so reading one card's ladder meant selecting
           that card first and then looking somewhere else on the page.
           `standardsIdentity` (entitysection.js) answers WHICH ladder, which
           is not the card's own identity for a Bowser reds pair. Closed also
           means it fetches nothing until asked: the panel loads on open, so
           N cards cost N requests only if he opens N of them. */""}
      <${StandardsPanel} entity=${standards.entity}
        activeStrat=${sec.last_strat} strategies=${sec.strategies}
        sectionRank=${sec.rank} sectionPb=${sec.pb}
        family=${standards.family} openLibrary=${openLibrary}
        onChanged=${t.refresh} defaultOpen=${false} />
      </div>
    <//>
  </section>`;
}

// The bucket for runs finished with no target picked. It is not a practiced
// ENTITY -- there is no course/star/segment identity behind it, so it has no
// icon, no rank ladder, and nothing for a click to select (the Analysis card
// above shows an entity's history; this has none). It still renders in the
// same log-card shape as every entity card above it, so it reads as one more
// row in the same list rather than a different kind of surface -- only the
// header differs (no art, no ranks, no select button).
//
// Griffin: "Unclassified resets… should always appear at the bottom of the
// list, below any of the classified cards" -- PracticeLog renders this last
// and unconditionally, never folded into the recency-ordered pagination
// above (it has no `last_activity` to sort by as a unit; it is one bucket,
// not one entity).
function UnassignedLogCard({ v, t, ui, freshIds, openCompare }) {
  // CLOSED unless he opens it, and it can never win the auto-open slot.
  // Griffin, 2026-08-04: "the unassigned runs card should ALWAYS stay closed,
  // unless the user opens it... This is because that information is noise and
  // should be at the bottom of the screen, tucked away."
  //
  // Two halves, and only this one needed code. The ORDERING half already held
  // by construction: `topEntityKey` reads `orderedSections`, which merges the
  // view's stars and segments, and this bucket is neither -- it is
  // `v.unassigned`, a flat attempt list with no `last_activity` of its own. So
  // an unassigned reset being the newest thing that happened cannot promote
  // this card, and the render above places it last unconditionally.
  //
  // Left as the card's OWN state rather than joining PracticeLog's override
  // map: that map exists so a manual choice survives a card being paginated
  // away and later revealed, and this card is outside the pagination entirely
  // -- it renders after the slice, every time.
  const [open, setOpen] = useState(false);
  const [showHidden, setShowHidden] = useState(false);
  const [visible, setVisible] = useState(10);
  const unassigned = v.unassigned || [];
  const visibleAttempts = showHidden ? unassigned
    : unassigned.filter((a) => !a.cleared && a.outcome !== "abandoned");
  const hidden = unassigned.filter((a) => a.cleared || a.outcome === "abandoned");
  const rows = visibleAttempts
    .filter((a) => !(ui.hideResets
      && (a.outcome === "reset" || a.outcome === "hard_reset")))
    .slice()
    .sort(comparator(ui.sort, t.clock));
  const shown = rows.slice(0, visible);
  return html`<section data-feed-key="unassigned"
      class="log-card is-unassigned ${open ? "" : "is-closed"}">
    <div class="log-card-head">
      <span class="log-card-name">
        <span class="log-card-context">Unassigned</span>
        <b>Runs with no target</b>
      </span>
      <button type="button" class="log-card-fold" onclick=${() => setOpen(!open)}
          aria-expanded=${open ? "true" : "false"}
          title=${`${open ? "Collapse" : "Expand"} the unassigned attempts`}>
        <${Icon} name="chevron" size=${18} />
      </button>
    </div>
    <${Disclose} open=${open} className="log-card-disclose">
      <div class="log-card-body">
      ${rows.length
        ? html`<${AttemptTable} attempts=${unassigned} rows=${shown} t=${t}
            freshIds=${freshIds} openCompare=${openCompare} />`
        : html`<${AttemptLogEmpty} hasAttempts=${unassigned.length > 0} />`}
      <div class="attempt-footer">
        <div class="attempt-pagination">
          ${rows.length > visible && html`<button class="quiet-button"
              onclick=${() => setVisible(visible + 10)}>Show 10 more</button>`}
          ${visible > 10 && html`<button class="quiet-button"
              onclick=${() => setVisible(Math.max(10, visible - 10))}>Show fewer</button>`}
        </div>
        <div class="attempt-footer-tools">
          <${HideToggle} hidden=${hidden} showHidden=${showHidden}
            setShowHidden=${setShowHidden} />
        </div>
      </div>
      </div>
    <//>
  </section>`;
}

/**
 * The page-level practice log: one heading (shared sort/filter/stat
 * controls), the recency-ordered entity cards, and the unassigned bucket
 * pinned to the bottom.
 *
 * `focusKey` names which entity's card is selected -- its graphs feed the
 * Analysis card the caller renders above this one (Task 6 owns that
 * wiring); this component only decides which ONE card carries `.is-selected`
 * and receives the live `focus`/`clearFocus` graph-pick pair.
 *
 * `activeKey` names the entity the player is ACTUALLY practicing right now
 * (practice.js's `activeStar`/`primarySeg`, the same signal `focustarget.js`
 * already reads as `live.activeKey`) -- separate from `focusKey`, which is
 * only ever a BROWSE pick and may point at a card the player left minutes
 * ago. THREE jobs now, all reusing this one signal rather than growing a
 * second answer to "did he choose this": whether a card's rank banners show
 * their next-step line at all (Griffin: "hide the 'to level up' display
 * there, and only display it when the user's actively practicing that
 * star" -- LogCard resolves its own `active` from this and hands it to
 * RankBanner as `showNext`, ranks.js itself never reaching for the store);
 * whether the active entity LEADS the card list regardless of its own
 * recency (`orderedSections`, above -- "it should be at the top immediately,
 * because it's the only star / segment available"); and whether a
 * zero-attempt, target-only section has earned a place at all
 * (`hasEarnedACard`, above -- "if we leave without practicing anything, its
 * card should disappear from the list").
 *
 * `playedKeys` is every entity that has recorded something, newest first
 * (`playedEntityKeys(v)`), taken through whatever celebration hold freezes
 * `activeKey`/`focusKey`/etc, so a running climb is never interrupted by a
 * card folding shut underneath it (`.claude/rules/ui-climb.md`; `practice.js`
 * freezes it alongside `target`/`stage`/`newestAttemptId`). This component
 * turns it into the ONE auto-open slot itself, via `autoOpenKey` against its
 * own rendered `sections` -- the caller cannot do that, because the caller
 * does not know which cards survive membership and Reds/Pipe exclusivity, and
 * a slot naming an unrendered card silently opens nothing at all. A DIFFERENT
 * signal from `focusKey`/`activeKey`: the newest thing PRACTICED is routinely
 * not the thing currently SELECTED or ACTIVE (this component's own comment on
 * `activeKey`, below).
 *
 * `enforceMembership` (default true) gates `hasEarnedACard`/
 * `applyRedsPipeExclusivity` -- both ask "did the SERVER publish this section
 * for a real reason", which only means something for a genuine session view.
 * `ui/tunelog.js`'s own inspector reuses this exact component for fidelity
 * ("PracticeLog, and therefore the REAL LogCard") over a HAND-BUILT `view` of
 * arbitrary rank states to judge side by side (a ladder floor, an unranked
 * sentinel, two rank ladders at once) -- most of those cards carry zero
 * attempts and match no real `activeKey` on purpose, because the fixture's
 * whole point is showing states a real server would only ever publish one of
 * at a time. Filtering that fixture by "did he earn this card" would silently
 * delete most of the inspector's own showcase (caught by
 * `tests/test_ui_rank_progress_track_log_card.py`, which drives the real
 * page rather than asserting on props). The real app never passes this prop
 * and keeps the enforced behaviour.
 */
export function PracticeLog({ v, t, ui, freshIds, openCompare, focus, pick,
                              clearFocus, focusKey, onSelect, activeKey = null,
                              playedKeys = [], openTargetPicker = null,
                              openSegment = null, openLibrary = null,
                              enforceMembership = true }) {
  const [shown, setShown] = useState(CARDS_PER_PAGE);
  // Per-entity fold overrides -- ONLY what the user has touched himself, in
  // either direction. Lives here rather than inside LogCard's own state
  // (that WAS the bug this feature fixes: a card that falls off "Show 5
  // more"'s own page and later returns must not forget a manual close/open
  // just because its component unmounted in between). Absent an entry here,
  // `isCardOpen` falls through to the auto-open rule -- this map is never
  // written to for that case, so a plain top-of-list transition costs no
  // state at all, just a different answer from the same pure function.
  const [openOverrides, setOpenOverrides] = useState({});
  const setCardOpen = (key, nextOpen) => setOpenOverrides(
    (current) => ({ ...current, [key]: nextOpen ? "open" : "closed" }));
  // The tuning slot is read ONCE here, at the page-level wiring layer -- never
  // inside LogCard, and never per attempt-row render. What comes back is a
  // CSS custom-property style object plus a modifier-class string; every
  // `.log-card` underneath inherits the vars for free (they cascade) and
  // matches the class-scoped rules in index.html by being a descendant.
  // LogCard itself stays entirely unaware that a tuning system exists --
  // except for `nameOverflow` and `rankIconSize`, which are JS rather than
  // CSS (the `shrinkToFit` value of `nameOverflow` is a JS measurement, not
  // a CSS rule at all -- ui/components/shrinkname.js; `rankIconSize` is a
  // NUMBER the Hat/Medal sprite draws itself at (`RankIcon`'s own `size`
  // prop, rankicon.js) -- a `--icon-size` custom property alone only ever
  // reserved the icon's wing-spill MARGIN, never resized the sprite, which
  // is the root cause BUG 2's own fix note in index.html explains) and so
  // have to be resolved here and handed down explicitly, the two deliberate
  // cracks in that wall. Which of the six rank-display SHAPES a card shows
  // is no longer a third crack: since the 2026-08-04 layout-matrix round
  // retired the single `rankStyle` choice for a four-cell table (narrow/wide
  // x two-ladder/one-ladder), RankBanner's own `layout` prop can no longer
  // be a page-level JS decision at all -- the active cell depends on THIS
  // card's own ladder count (`showsEntityBanner`, below) crossed with the
  // real-time container width, which only CSS (`@container`) can answer
  // reactively. LogCard passes RankBanner no `layout` (its "row" default);
  // index.html's "Layout matrix" CSS section reaches `.rank-banner` and its
  // children through ancestor context instead.
  //
  // `rankPlacement`/`nextStepMode` (spec practice-log-entity-cards, round 3)
  // are a THIRD and FOURTH crack, and a genuinely different shape from the
  // first two: they vary PER SECTION (crossed with each entity's own ladder
  // count), not once for the whole page, because a card's rank display can
  // only physically relocate between the head and the body via a JS
  // decision (see LogCard's own comment on `rankPlacement`). That decision
  // also needs to know whether THIS card's own container is currently
  // narrow -- the one fact `showsEntityBanner`/`nameOverflow`/`rankIconSize`
  // never had to track live, since none of them change the physical DOM
  // location of anything. `isNarrow` mirrors the "Layout matrix" section's
  // own `@container (max-width: 860px)` threshold
  // (`NARROW_CONTAINER_PX`, logtuning.js) via a `ResizeObserver` on
  // `.practice-page` itself -- the same element every `@container` rule in
  // this card measures against (`.claude/rules/ui-core.md`'s own
  // responsiveness law: the sidebar's 1180px step makes the WINDOW width an
  // unreliable proxy, so nothing here reads `window.innerWidth`). A resize
  // during the one frame this races CSS's own synchronous `@container`
  // match is the one accepted seam -- see this file's own module doc for
  // why it is small and, at the shipped "head" default, unreachable (no
  // physical relocation ever happens unless a cell is actually dialed to
  // "body").
  const pageRef = useRef(null);
  const [isNarrow, setIsNarrow] = useState(false);
  useEffect(() => {
    if (!pageRef.current || typeof ResizeObserver === "undefined") return undefined;
    const container = pageRef.current.closest(".practice-page") || pageRef.current;
    const measure = () => setIsNarrow(container.getBoundingClientRect().width <= NARROW_CONTAINER_PX);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(container);
    return () => observer.disconnect();
  }, []);
  const logVars = useMemo(() => logTuningVars(logTuning()), []);
  const logClasses = useMemo(() => logTuningClasses(logTuning()), []);
  const tuning = logTuning();
  const nameOverflow = tuning.nameOverflow;
  const rankIconSize = tuning.rankIconSize;
  // The Reds/Pipe exclusivity check (below) needs a course -> LEVEL lookup:
  // `stagebanner.js`'s `bowserModeFor` is keyed by level (a Bowser course's
  // own `course_id` and its LEVEL are different numbers -- BitDW is course
  // 16, level 17), and the log has no live "stage" to read the level off of
  // for a course he is not currently standing in. Inverted from the SAME
  // server-shipped `vocab.course_by_level` `entityicons.js`'s `iconContext`
  // already reads for the identical reason (a segment's start level, off
  // the same map) -- never a second hand-written course<->level table, which
  // is exactly the kind of duplicated domain fact this project keeps paying
  // for. Memoised on the vocab's own identity, which changes once per fetch.
  const levelByCourse = useMemo(() => {
    const courseByLevel = (t.vocab || {}).course_by_level || {};
    const out = {};
    for (const [level, course] of Object.entries(courseByLevel)) out[course] = Number(level);
    return out;
  }, [t.vocab]);
  const modeForCourse = (courseId) => {
    const level = levelByCourse[courseId];
    return level != null ? bowserModeFor(level) : null;
  };
  const ordered = orderedSections(v, activeKey);
  const sections = enforceMembership
    ? applyRedsPipeExclusivity(
        ordered.filter((sec) => hasEarnedACard(sec, activeKey)), modeForCourse)
    : ordered;
  // The focused entity (the active target, by default, or a manual browse
  // pick) is not necessarily among the first CARDS_PER_PAGE cards -- recency
  // order is by `last_activity`, and the active target is routinely NOT the
  // most recently touched entity in a well-practiced corpus. Reached only by
  // driving the mounted page against real data: the objective card's PbTag
  // jump found its attempt, called `pick`, and `focus` updated correctly,
  // but the target entity's OWN `.log-card` was never rendered at all --
  // past "Show 5 more" -- so there was no card for the reveal to land on.
  // Same one-shot-nudge shape as LogCard's own row-pagination fix: widen
  // `shown` the moment the focused key sits outside it.
  useEffect(() => {
    if (focusKey == null) return;
    const idx = sections.findIndex((sec) => entityKey(sec) === focusKey);
    if (idx === -1) return;                // not a classified entity (unassigned)
    setShown((current) => (idx >= current ? idx + 1 : current));
  }, [focusKey]);
  // Resolved HERE, never handed down pre-decided: `sections` is the list
  // the user is looking at, and `autoOpenKey`'s own comment records what it
  // cost to learn that those are two different lists.
  const topKey = autoOpenKey(sections, activeKey, playedKeys);
  const page = sections.slice(0, shown);
  const listRef = useRef(null);
  useFeedMotion(listRef, [...page.map(entityKey), "unassigned"]);
  return html`<section class="practice-card log-list-card ${logClasses}"
      style=${logVars} ref=${pageRef}>
    <div class="card-heading attempts-heading">
      <div><span class="eyebrow">Practice log</span><h3>Recent activity</h3></div>
      <div class="attempts-tools">
        ${/* Not just "shown": `.attempts-tools`'s twin usage (the old
             per-section attempts table, before Task 6) counted ROWS with
             this exact wording, beside this exact sort control. This one
             counts CARDS -- same words, same neighbours, a different noun
             behind the number -- so the noun goes in the label rather than
             staying implied. */""}
        <span class="meta">${page.length} entities shown</span>
        <${StatMenuTrigger} t=${t} />
        <${SortControl} ui=${ui} />
        <${ResetFilterToggle} ui=${ui} />
        ${/* The target picker's TRIGGER, re-homed here now that the card it
             used to live on (the Active Target card) is deleted (amendment
             A9). Griffin retired the feature outright, then gave it a home
             anyway: "I have literally never actually used this... I think we
             should keep all the code for this feature, because I might want
             to use it later... Let's move its trigger into the practice
             log's header row, beside Stats and the sort control... I
             probably won't use it, but for the purposes of keeping it ready
             for later, we should keep it there." One modest chip, matching
             Stats' own styling, at the quiet end of the row -- it must never
             grow to compete with the quick-select row for attention, since
             he does not practise by picking: he walks there in game and the
             automation follows. */""}
        ${openTargetPicker && html`<button type="button" class="chip chip-button"
            onclick=${openTargetPicker}
            title="Pick a star or segment to practice manually">
          <${Icon} name="target" size=${14} />${" "}<span class="stat-menu-label">Target</span>
        </button>`}
      </div>
    </div>
    ${/* THE FEED'S OWN MOTION. `.log-list` is the element every card is a
         direct child of, which is what `useFeedMotion` measures against --
         see components/feedmotion.js for why this is FLIP and why it runs in
         a layout effect. The unassigned bucket carries a feed key too: it is
         not an entity, but it is a card in this list and it gets pushed down
         like any other physical object in the space. */""}
    <div class="log-list" ref=${listRef}>
      ${page.map((sec) => {
        const ek = entityKey(sec);
        // Resolved here, per section, and handed to LogCard as plain
        // strings -- see this component's own comment above for why this
        // (unlike nameOverflow/rankIconSize) cannot be a single page-level
        // value. `twoLadder` is computed a second time (LogCard also
        // computes its own, for the `log-card-two-ladder`/`-one-ladder`
        // class) rather than threaded down -- both calls are the same pure,
        // cheap function of `sec`, and keeping LogCard's own computation is
        // what lets it stay a self-contained component a test can render
        // with no page-level wiring at all.
        const twoLadder = showsEntityBanner(sec);
        const rankPlacement = rankPlacementFor(tuning, { isNarrow, twoLadder });
        const nextStepMode = nextStepModeFor(tuning, { isNarrow, twoLadder });
        return html`<${LogCard} key=${ek} sec=${sec} t=${t} ui=${ui}
          freshIds=${freshIds} openCompare=${openCompare} openSegment=${openSegment}
          openLibrary=${openLibrary}
          focus=${focus} clearFocus=${clearFocus} pick=${pick}
          selected=${ek === focusKey} onSelect=${onSelect}
          forceOpen=${ek === focusKey}
          open=${isCardOpen(openOverrides, topKey, ek)}
          onSetOpen=${(next) => setCardOpen(ek, next)}
          active=${activeKey != null && ek === activeKey}
          nameOverflow=${nameOverflow} rankIconSize=${rankIconSize}
          rankPlacement=${rankPlacement} nextStepMode=${nextStepMode} />`;
      })}
      <${UnassignedLogCard} v=${v} t=${t} ui=${ui} freshIds=${freshIds}
        openCompare=${openCompare} />
    </div>
    ${sections.length > shown && html`<div class="log-list-footer">
      <button class="quiet-button"
          onclick=${() => setShown(shown + CARDS_PER_PAGE)}>
        Show ${CARDS_PER_PAGE} more</button>
    </div>`}
  </section>`;
}
