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
import { useEffect, useMemo, useState } from "preact/hooks";
import htm from "htm";
import { displayName, entityKey, entityNoun, sectionClock, sectionPb }
  from "../entitysection.js";
import { entityIconSrc, fallbackToGenericStar, fallbackSlotForEntityKey }
  from "./entityicons.js";
import { RankBanner } from "./ranks.js";
import { Icon } from "./icons.js";
import { ShrinkToFitName } from "./shrinkname.js";
import { AttemptTable, AttemptLogEmpty, HideToggle, SortControl,
         ResetFilterToggle, StatMenuTrigger, comparator, bannerLabel,
         bannerHint, ranksAreAtFloor, showsEntityBanner, rankIdentity, PbTag }
  from "./attemptlog.js";
import { logTuning, logTuningVars, logTuningClasses } from "../logtuning.js";

const html = htm.bind(h);

// Which of RankBanner's `layout` values a `rankStyle` choice resolves to.
// Three of the six options are field-hiding CSS rules alone (banners/chips/
// capsOnly all render RankBanner's plain "row" layout, just with some of its
// fields hidden by a class); Stacked and Stacked-side-by-side share
// RankBanner's OWN "stacked" layout -- the same MARELO-shaped arrangement,
// differing only in which direction `.log-card-ranks` lays the two banners
// out (a CSS-only difference, index.html) -- and Column is a third, distinct
// `layout` value of its own (spec 2026-08-04-rank-variants).
const RANK_LAYOUT_BY_STYLE = { stacked: "stacked", stackedRow: "stacked", column: "column" };

// How many entity cards render before "Show 5 more". Every shown card is
// OPEN by default (Griffin: "The drop down should always be opened by
// default"), so an uncapped lifetime-scope view would render every entity in
// the journal expanded.
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

export function LogCard({ sec, t, ui, freshIds, openCompare, focus,
                          clearFocus, selected, onSelect, forceOpen,
                          rankLayout = "row", active = false,
                          nameOverflow = "ellipsis", rankIconSize = 24 }) {
  const [open, setOpen] = useState(true);
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
  useEffect(() => {
    if (forceOpen) setOpen(true);
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
  return html`<section class="log-card ${selected ? "is-selected" : ""}
      ${isOpen ? "" : "is-closed"}">
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
        </span>
      </button>
      <div class="log-card-ranks">
        <${RankBanner} label=${bannerLabel(sec, entityNoun(sec))}
            hint=${bannerHint(sec, entityNoun(sec))} banner=${sec.rank}
            atFloor=${ranksAreAtFloor(sec)} lane=${ek} order=${0}
            replayKey=${sec.last_strat || ""}
            identity=${rankIdentity(ek, "strategy", sec, t)}
            layout=${rankLayout} showNext=${active} iconSize=${rankIconSize} />
        ${showsEntityBanner(sec) && html`<${RankBanner}
            label=${entityNoun(sec)} banner=${sec.entity_rank}
            atFloor=${ranksAreAtFloor(sec)} lane=${ek} order=${1}
            identity=${rankIdentity(ek, "entity", sec, t)}
            layout=${rankLayout} showNext=${active} iconSize=${rankIconSize} />`}
      </div>
      <${PbTag} pb=${sectionPb(sec, t.clock)} mode=${clock} rows=${rows}
        pick=${null} t=${t} />
      <button type="button" class="log-card-fold" onclick=${() => setOpen(!isOpen)}
          aria-expanded=${isOpen ? "true" : "false"}
          title=${`${isOpen ? "Collapse" : "Expand"} ${named.name}'s attempts`}>
        <${Icon} name="chevron" size=${18} />
      </button>
    </div>
    ${/* Same markup StarSection/SegmentSection render for their own
         objective card (practice.js), and the same reason: `armed_detail` is
         SERVER truth, re-derived from the journal on every view fetch, and
         it is NOT segment-only -- the 100-coin star carries it too, which is
         why both sections already draw this identically. Before this task
         an armed-but-not-active entity still got its own full objective-card
         (inside the now-deleted practice index) and this row rode along for
         free; a `LogCard` is the only surface such an entity gets now, so it
         is the one that has to carry the row, or "is the system aware I'm
         mid-movement" silently stops being answerable the moment that
         movement is not also the active target. Occupies zero height when
         `armed_detail` is null -- true of every ordinary card. */""}
    ${sec.armed_detail && html`<div class="seg-waiting">
      <span class="seg-waiting-step">Step${" "}
        ${sec.armed_detail.progress + 1}${" "}of${" "}
        ${sec.armed_detail.total + 1}</span>
      <span class="seg-waiting-for">Waiting for${" "}
        ${sec.armed_detail.waiting_for}</span>
    </div>`}
    ${isOpen && html`<div class="log-card-body">
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
 */
export function PracticeLog({ v, t, ui, freshIds, openCompare, focus,
                              clearFocus, focusKey, onSelect, activeKey = null }) {
  const [shown, setShown] = useState(CARDS_PER_PAGE);
  // The tuning slot is read ONCE here, at the page-level wiring layer -- never
  // inside LogCard, and never per attempt-row render. What comes back is a
  // CSS custom-property style object plus a modifier-class string; every
  // `.log-card` underneath inherits the vars for free (they cascade) and
  // matches the class-scoped rules in index.html by being a descendant.
  // LogCard itself stays entirely unaware that a tuning system exists --
  // except for `rankLayout`, `nameOverflow` and `rankIconSize`, which are JS
  // rather than CSS (RankBanner's `layout` prop selects an actual
  // arrangement of DOM parts, not merely a class an already-rendered tree
  // can hide fields under; the `shrinkToFit` value of `nameOverflow` is a JS
  // measurement, not a CSS rule at all -- ui/components/shrinkname.js;
  // `rankIconSize` is a NUMBER the Hat/Medal sprite draws itself at
  // (`RankIcon`'s own `size` prop, rankicon.js) -- a `--icon-size` custom
  // property alone only ever reserved the icon's wing-spill MARGIN, never
  // resized the sprite, which is the root cause BUG 2's own fix note in
  // index.html explains) and so have to be resolved here and handed down
  // explicitly, the three deliberate cracks in that wall.
  const logVars = useMemo(() => logTuningVars(logTuning()), []);
  const logClasses = useMemo(() => logTuningClasses(logTuning()), []);
  const rankLayout = RANK_LAYOUT_BY_STYLE[logTuning().rankStyle] || "row";
  const nameOverflow = logTuning().nameOverflow;
  const rankIconSize = logTuning().rankIconSize;
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
  return html`<section class="practice-card log-list-card ${logClasses}" style=${logVars}>
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
        return html`<${LogCard} key=${ek} sec=${sec} t=${t} ui=${ui}
          freshIds=${freshIds} openCompare=${openCompare}
          focus=${focus} clearFocus=${clearFocus}
          selected=${ek === focusKey} onSelect=${onSelect}
          forceOpen=${ek === focusKey}
          rankLayout=${rankLayout} active=${activeKey != null && ek === activeKey}
          nameOverflow=${nameOverflow} rankIconSize=${rankIconSize} />`;
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
