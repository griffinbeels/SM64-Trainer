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
import { displayName, entityKey, entityNoun, sectionClock, sectionPb }
  from "../entitysection.js";
import { entityIconSrc, fallbackToGenericStar, fallbackSlotForEntityKey }
  from "./entityicons.js";
import { RankBanner } from "./ranks.js";
import { Icon } from "./icons.js";
import { ShrinkToFitName } from "./shrinkname.js";
import { StepTrack } from "./steptrack.js";
import { AttemptTable, AttemptLogEmpty, HideToggle, SortControl,
         ResetFilterToggle, StatMenuTrigger, comparator, bannerLabel,
         bannerHint, ranksAreAtFloor, showsEntityBanner, rankIdentity, PbTag }
  from "./attemptlog.js";
import { logTuning, logTuningVars, logTuningClasses, rankPlacementFor,
         nextStepModeFor, NARROW_CONTAINER_PX } from "../logtuning.js";

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

/**
 * Both kinds merged, newest activity first.
 *
 * `last_activity` is the SERVER's own journal-id stamp (views.py) and both
 * section lists arrive already sorted by it — this is the merge, not a
 * re-derivation. A section with no attempts in scope carries -1 and sorts
 * last, which is right: a target you set and have not run yet is not the
 * thing you were just doing.
 */
export function orderedSections(view) {
  return [...(view.stars || []), ...(view.segments || [])]
    .slice()
    .sort((a, b) => (b.last_activity ?? -1) - (a.last_activity ?? -1));
}

/**
 * The entity key of the TOP card -- the newest thing practiced, and the one
 * the system's single auto-open slot always points at (Griffin, 2026-08-04:
 * "We should automatically keep open the last entry in the system... the top
 * entry is the newest one, and it's auto-opened by default"). Null once
 * `view` has nothing classified in it yet (the unassigned bucket is not an
 * entity and is never eligible -- UnassignedLogCard's own comment).
 */
export function topEntityKey(view) {
  if (!view) return null;
  const sections = orderedSections(view);
  return sections.length ? entityKey(sections[0]) : null;
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
export function isCardOpen(overrides, topKey, key) {
  const manual = (overrides || {})[key];
  if (manual != null) return manual === "open";
  return topKey != null && key === topKey;
}

export function LogCard({ sec, t, ui, freshIds, openCompare, focus,
                          clearFocus, selected, onSelect, forceOpen,
                          open, onSetOpen,
                          active = false,
                          nameOverflow = "ellipsis", rankIconSize = 24,
                          rankPlacement = "head", nextStepMode = "classic" }) {
  const [showHidden, setShowHidden] = useState(false);
  const [visible, setVisible] = useState(10);
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
  // A pick (the objective card's PbTag jump, or a trend-graph dot in
  // EntityAnalysis) is resolved at PAGE level -- Practice's own
  // `useGraphPick`, over whichever entity is FOCUSED -- and that hook has no
  // visibility into any card's own row pagination; it never did. Feeding it
  // a page-level `visible`/`setVisible` widened a counter nothing renders
  // from, which is exactly why a pick past the tenth row silently did
  // nothing (found only by driving the mounted page past page one). THIS
  // card owns the one `visible` that actually governs `shown` below, so it
  // is the one that has to notice a focused pick landing outside its own
  // current window and widen itself -- the same one-shot-nudge shape
  // `forceOpen` already uses above, keyed on the same `focus.nonce` so a
  // repeat pick on an already-widened card still re-checks.
  useEffect(() => {
    if (!selected || !focus) return;
    const idx = rows.findIndex((a) => a.id === focus.id);
    if (idx === -1) return;               // not this card's attempt
    setVisible((current) => (idx >= current
      ? Math.ceil((idx + 1) / 10) * 10
      : current));
  }, [selected, focus && focus.nonce]);
  const shown = rows.slice(0, visible);
  const isOpen = open;
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
  return html`<section class="log-card ${selected ? "is-selected" : ""}
      ${isOpen ? "" : "is-closed"} ${twoLadder ? "log-card-two-ladder" : "log-card-one-ladder"}
      ${inBody ? "log-card-ranks-in-body" : ""}">
    ${/* The HEADING selects; the chevron opens. Two gestures, two targets,
         so browsing a card's graphs and folding it away never fight. */""}
    <div class="log-card-head">
      <button type="button" class="log-card-select" onclick=${() => onSelect(ek)}
          aria-pressed=${selected ? "true" : "false"}
          title=${`Show ${named.name}'s timeline and trend above`}>
        <img class="log-card-art" src=${entityIconSrc(t, ek)} alt=""
          onerror=${(e) => fallbackToGenericStar(e, fallbackSlotForEntityKey(ek))} />
        <span class="log-card-name">
          <span class="log-card-context">${named.context}</span>
          <${ShrinkToFitName} text=${named.name}
            enabled=${nameOverflow === "shrinkToFit"} />
          ${/* Ranks-in-body mode only: a collapsed card in this mode shows
               NO rank display anywhere (it lives inside `.log-card-body`,
               which only renders while open), so the strategy identity has
               to live somewhere else or a card cannot be told apart from a
               sibling on the same star/segment (Griffin: "we should also
               display the strategy name used for this entry... each
               strategy gets its own card"). Shown in BOTH open and closed
               states for one consistent answer to "which strategy is this"
               rather than one that appears and disappears with the fold. */""}
          ${inBody && sec.last_strat
            && html`<span class="log-card-strat">${sec.last_strat}</span>`}
        </span>
      </button>
      ${!inBody && ranksBlock}
      <${PbTag} pb=${sectionPb(sec, t.clock)} mode=${clock} rows=${rows}
        pick=${null} t=${t} />
      <button type="button" class="log-card-fold" onclick=${() => onSetOpen(!isOpen)}
          aria-expanded=${isOpen ? "true" : "false"}
          title=${`${isOpen ? "Collapse" : "Expand"} ${named.name}'s attempts`}>
        <${Icon} name="chevron" size=${18} />
      </button>
    </div>
    ${/* The SAME shared component StarSection/SegmentSection render for their
         own objective card (practice.js's `StepTrack`, steptrack.js), and the
         same reason: `armed_detail` is SERVER truth, re-derived from the
         journal on every view fetch, and it is NOT segment-only -- the
         100-coin star carries it too, which is why both sections already
         draw this identically. Before this task an armed-but-not-active
         entity still got its own full objective-card (inside the now-deleted
         practice index) and this row rode along for free; a `LogCard` is the
         only surface such an entity gets now, so it is the one that has to
         carry the row, or "is the system aware I'm mid-movement" silently
         stops being answerable the moment that movement is not also the
         active target. `StepTrack` renders nothing when `armed_detail` is
         null -- true of every ordinary card. No `onEdit` here: that door is
         the PINNED card's own affordance (practice.js), not every card this
         entity happens to also appear as in the log. */""}
    <${StepTrack} detail=${sec.armed_detail} />
    ${isOpen && html`<div class="log-card-body">
      ${inBody && ranksBlock}
      ${rows.length
        ? html`<${AttemptTable} attempts=${sec.attempts} rows=${shown} t=${t}
            focus=${selected ? focus : null} clearFocus=${clearFocus}
            freshIds=${freshIds} openCompare=${openCompare} sec=${sec} />`
        : html`<${AttemptLogEmpty} hasAttempts=${sec.attempts.length > 0} />`}
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
    </div>`}
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
  const [open, setOpen] = useState(true);
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
  return html`<section class="log-card is-unassigned ${open ? "" : "is-closed"}">
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
    ${open && html`<div class="log-card-body">
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
    </div>`}
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
 * ago. It exists for exactly one thing: whether a card's rank banners show
 * their next-step line at all (Griffin: "hide the 'to level up' display
 * there, and only display it when the user's actively practicing that
 * star"). LogCard resolves its own `active` from this and hands it to
 * RankBanner as `showNext` -- ranks.js itself never reaches for the store.
 *
 * `topKey` names the entity the system's ONE auto-open slot follows (see
 * `isCardOpen` above) -- the caller's own `topEntityKey(v)`, taken through
 * whatever celebration hold freezes `activeKey`/`focusKey`/etc, so a running
 * climb is never interrupted by an unrelated card folding shut underneath it
 * (`.claude/rules/ui-climb.md`; `practice.js` freezes it alongside `target`/
 * `stage`/`newestAttemptId`). A DIFFERENT signal from `focusKey`/`activeKey`:
 * the newest thing PRACTICED is routinely not the thing currently SELECTED or
 * ACTIVE (this component's own comment on `activeKey`, below).
 */
export function PracticeLog({ v, t, ui, freshIds, openCompare, focus,
                              clearFocus, focusKey, onSelect, activeKey = null,
                              topKey = null }) {
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
  const sections = orderedSections(v);
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
  const page = sections.slice(0, shown);
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
      </div>
    </div>
    <div class="log-list">
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
          freshIds=${freshIds} openCompare=${openCompare}
          focus=${focus} clearFocus=${clearFocus}
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
