// src/sm64_events/ui/components/stagebanner.js
// Quick-select row, driven by t.stage (the broadcast-only stage_changed event)
// and dispatched on its `mode`:
//   "stars"         : a main course 1-15 -> that course's stars (name +
//                     last-strategy subtext); click sets the star target.
//                     The "100 Coins" cell is a PLAIN star cell like the
//                     other six (spec 2026-07-28-multi-step-segments, "the
//                     100-coin star IS the segment") — its rank/strat/pb
//                     come from the star's OWN section like any other star,
//                     because its underlying HUNDRED_COIN_EXIT engine's
//                     completed attempts attribute directly to this star now
//                     (tracking/projection.py, segments.hundred_coin_entity),
//                     rather than to a separate segment this row used to
//                     borrow display data from. The engine still arms on
//                     course entry and matches independently of the target;
//                     its progress surfaces on the Active Target card
//                     (practice.js's armed_detail row), never as a second
//                     cell here or a borrowed rank on this one.
//   "bowser_course" : BitDW/BitFS/BitS -> TWO targets: the "Reds" movement
//                     (seg:reds->pipe:<abbrev>, stage entry -> grab -> pipe)
//                     and the level's "No Reds" pipe-entry segment. The reds
//                     STAR is a [[subsection]] of the Reds movement (round 31,
//                     task 3) -- picking Reds targets the movement, and the
//                     star's own grab/PB/rank draw NESTED inside its
//                     practice-log card with no pick of their own.
//   "arena"         : a Bowser 1/2/3 fight arena -> the single fight segment,
//                     AUTO-selected on entry (always overriding the current
//                     target — you fell in to fight, so that's the practice).
//   "castle"        : a Castle Inside subarea (lobby/upstairs/basement) -> the
//                     enabled segments whose start triggers begin in that
//                     subarea (v.segment_targets, filtered by level+area).
// EVERY mode renders through the shared PracticeCell (art + rank medal + name
// + sub-line, identical active/dim/glow/bob styling) — segment rows only wire
// segment data into it (spec 2026-07-24-segment-icon-cells, pinned by
// tests/test_star_icons.py). A RUNNING segment must never be invisible (spec
// addendum): every row appends armedExtraCells for armed segments its own
// filter didn't include, and with no row at all an ArmedOnlyRow replaces the
// placeholder while anything is armed. Cell art resolves user override
// (view's icon_overrides, either mode — incl. uploaded `user:` icons) >
// course-mode split-icon > generic gold star; the hover ✎ on any cell opens
// the IconPicker to set/clear the override.
// Selection POSTs /api/target (and PUTs /api/segments/{id} for the Bowser
// enable/disable) -- the same endpoints the rest of the UI uses, so the normal
// target_changed flow updates the header, the pinned section, and this.
import { h } from "preact";
import { useEffect } from "preact/hooks";
import htm from "htm";
import { CellRow, SurfaceExchange } from "./cellrow.js";
import { CollapseToggle, cardClass, useCollapsed } from "./collapsible.js";
import { send } from "../api.js";
import { armedSegments, hasPracticeContext, hasStandardsFor,
         justCompletedSegment, practiceMode,
         selectorSurfaceId } from "../stagecontext.js";
import { requestTarget } from "../target.js";
import { handIsEmpty, loneOption } from "../loneoption.js";
import { PracticeCell } from "./practicecell.js";
import { iconIdentityForKey, useIconPicking } from "./iconpicker.js";
import { entityIconSrc } from "./entityicons.js";
import { isPiece } from "../subsections.js";

const html = htm.bind(h);

const CASTLE_AREA_NAMES = { 1: "Lobby", 2: "Upstairs", 3: "Basement" };

// One row per PRACTICE_MODES id. The two lists are pinned to each other by
// tests/test_ui_practice_context.py: a mode missing here would fall through to
// the armed-only row, and a row whose mode is missing there is unreachable,
// because the context question below is asked FIRST.
const STAGE_ROWS = { stars: StarRow, bowser_course: BowserCourseRow,
                     arena: ArenaRow, castle: SegmentRow };

// `freshIds` is practice.js's own attempt-id recency Set (useFreshAttemptIds),
// threaded all the way down from there (spec 2026-07-28-multi-step-segments
// round 2) so a row can tell a FRESH completion apart from mere history --
// today only BowserCourseRow reads it (the Reds row's detection-driven family
// memory, items 2/5), every other row simply ignores the prop.
export function StageBanner({ t, freshIds }) {
  const v = t.view;
  // The one door (../stagecontext.js), shared with the Active-target card so
  // the two cannot say different things about the same place — they did, at
  // the file select, where this drew its placeholder while the card below
  // still named a star from the session before.
  const Row = STAGE_ROWS[practiceMode(t)];
  const body = !hasPracticeContext(t)
    ? html`<${StagePlaceholder} t=${t} />`
    : (Row ? html`<${Row} t=${t} v=${v} stage=${t.stage} freshIds=${freshIds} />`
           : html`<${ArmedOnlyRow} t=${t} v=${v} />`);
  // The whole CARD swapping is a change to the same display, so it exchanges
  // too (live report 2026-08-02: "if there previously were no options
  // available, but I transition to a stage with options... right now it
  // incorrectly cuts. In all circumstances where we change this display, it
  // should animate in / out"). This wrapper is the one thing here that never
  // unmounts, which is the whole reason the fade can outlive the row it is
  // fading out — a row component takes its own state with it when it goes.
  return html`<${SurfaceExchange} class="selector-exchange"
    identity=${selectorSurfaceId(t)}>${body}<//>`;
}

function StagePlaceholder({ t }) {
  return html`<section class="practice-card selector-card stagebanner selector-empty">
    <div class="selector-empty-symbol" aria-hidden="true">☆</div>
    ${/* It said "or pick one from the active target card below" until
         2026-07-27. That stopped being true the day a pick from a place like
         this started being REFUSED (tracking/practicable.py) — the card is
         still there, but everything in it would come back with the server's
         "you can only practice what you are standing in". */""}
    <div><b>No course target available</b>
      <span class="meta">Move into a course — you practice what you are
        standing in.</span></div>
      </section>`;
}

// --- route focus ------------------------------------------------------------
// With a route active the selectors narrow to that route's members. Returns
// null when there is nothing to narrow BY — no active route, or the route
// doesn't touch this course/these segments — and null means "show everything",
// so an unrelated detour never leaves the player staring at an empty banner.

function routeStarFilter(v, courseId) {
  const keys = v.active_route && v.active_route.star_keys;
  if (!keys || !keys.length) return null;
  const mine = keys.filter((k) => k.startsWith(`${courseId}:`));
  return mine.length ? new Set(mine) : null;
}

function routeSegmentFilter(v) {
  const ids = v.active_route && v.active_route.segment_ids;
  return ids && ids.length ? new Set(ids) : null;
}

// segments offered for the current whole level (Bowser banners) — the pipe-entry
// segments (course levels) or fight segments (arenas). Disabled ones are kept;
// the Bowser banner shows them so its "no reds" click can enable them.
const segsForLevel = (v, level) =>
  (v.segment_targets || []).filter((s) => (s.start_levels || []).includes(level));

// Look flags — flip during the human-audit playtest to taste. Kept as
// constants (not props) so the cell below stays a single readable line.
const STAR_DIM_IDLE = true;  // false = every star equally bright

// Art resolution lives in entityicons.js (`entityIconSrc`), over the pure
// chain in ../entities.js (`entityIcon`) — this row passes an entity key and
// gets a URL. It derived its own stems until 2026-07-26, which is how the
// same segment came to wear Bowser here and a plain gold star on the Rank tab.
//
// The cell itself is components/practicecell.js, shared with the entity
// picker's grid (2026-07-25) so a star looks the same where you pick it and
// where you practice it.

// A cell's art is `entityIconSrc(t, <entity key>)` and nothing else — this row
// used to derive a stem itself (LEVEL_ICONS off the segment's start_levels,
// `${prefix}${slot+1}` off COURSE_ICON_PREFIXES) and the Rank tab derived a
// different one from the same entity. The start-level lookup now happens once,
// inside entityicons.js's iconContext; the Bowser/cap courses this row had no
// branch for resolve there too (2026-07-26).
// WHICH of the two top-level cells (Reds vs No Reds) was last explicitly
// practiced, remembered per level. Its own sibling key, `sm64.bowserMode`
// (the star-vs-pipe TOGGLE that used to live *within* Reds), is retired as of
// round 31 (task 3, 2026-08-10) — the reds STAR is a [[subsection]] of the
// Reds movement now, not a second thing this row picks between, so there is
// nothing left for a sub-mode to remember. This key survives unchanged: Reds
// vs No Reds is still a real choice between two segments that both still
// exist. Round 2, item 5 (user, 2026-07-30: "If I
// have selected reds (or no reds) and leave a bowser stage, and come back, I
// would expect that same selection to persist to my next session. This is
// different than a normal stage, we generally are swapping between the two
// different approaches while practicing"). No default value: an unset level
// means "nothing chosen yet", which the auto-retarget effect below reads as
// "don't retarget" — unlike the star/pipe sub-toggle, which needs SOME
// visual default even before a pick, there is nothing to default this to
// without inventing a choice the player never made.
const BOWSER_FAMILY_KEY = "sm64.bowserFamily";
const BOWSER_FAMILIES = ["reds", "no_reds"];

function readBowserFamilies() {
  try { return JSON.parse(localStorage.getItem(BOWSER_FAMILY_KEY)) || {}; }
  catch { return {}; }
}
function bowserFamilyFor(level) {
  const stored = readBowserFamilies()[String(level)];
  return BOWSER_FAMILIES.includes(stored) ? stored : null;
}
function writeBowserFamily(level, family) {
  if (!BOWSER_FAMILIES.includes(family)) return;
  const all = readBowserFamilies();
  all[String(level)] = family;
  try { localStorage.setItem(BOWSER_FAMILY_KEY, JSON.stringify(all)); } catch { /* full */ }
}

const segKey = (s) => `segment:${s.segment_id}`;
const starKey = (courseId, slot) => `star:${courseId}:${slot}`;

// `segKey`/`starKey` above emit the same `segment:<id>` / `star:<c>:<s>`
// strings the server stamps as a definition's `parent`, which is what lets a
// row ask "is this piece mine?" with one string compare. A `targetEntityKey`
// helper lived here for progressive disclosure and went with it in round 22 --
// which parent a piece belongs to is a question about the CELL now, not about
// whatever happens to be targeted.

// Row-level icon-picking state (the ✎ on any cell) is iconpicker.js's
// useIconPicking, shared with the Rank tab's coverage tiles since 2026-07-26.

// The banner cell is components/practicecell.js — shared with the entity
// picker's grid so a star looks the same where you pick it and where you
// practice it. The banner passes dimIdle (its own look) and onEdit (the ✎
// icon override, which only exists here).

// The sub-line: the active strategy's name, same for every cell regardless
// of kind (round 2, 2026-07-30 -- "we should reuse the same exact system...
// for segments, we should remove the 'armed'/'running' display... Instead,
// it should use the same visual display as the stars, i.e., replace the
// 'running' with the strategy name"). A segment cell used to swap this for
// a "⏱ running" chip while armed, with its own green `.armed` highlight
// fighting `active-star`'s gold one whenever a segment was both armed and
// targeted -- redundant besides, since the pinned SegmentSection card
// already carries its own "Running"/"Ready" live-state line; repeating it
// on the quick-select cell was a second place for one fact, not a second
// fact. Deleted outright (PracticeCell's `armed` prop is gone) rather than
// merely unused, so there is nothing left here to diverge back into.
const stratSub = (strat) =>
  html`<span class="strat ${strat ? "" : "none"}">${strat || "—"}</span>`;

// Enable-if-needed + target the segment -- the write every plain segment
// pick makes, extracted so BOTH StandardSegmentCell's own click AND
// BowserCourseRow's auto-retarget effect (item 5, below) go through ONE
// place rather than growing a second inline copy.
async function pickSegmentTarget(t, s, options) {
  if (!s.enabled)
    await send("PUT", `/api/segments/${s.segment_id}`, { enabled: true });
  await requestTarget(t, { kind: "segment", segment_id: s.segment_id }, options);
}

// THE lone-route auto-pick, shared by the star row and the castle segment row
// (rule 11 — one implementation, not two that drift). Task 0025: with a route
// active, a place where the route leaves exactly ONE thing to practice needs no
// pick from him. The RULE itself is `loneOption`/`handIsEmpty` in
// ../loneoption.js, import-free so node can test it; this is only the wiring.
//
// Deliberately NOT applied to the two Bowser rows above. `ArenaRow` already
// auto-selects its single fight by its own rule (arriving in an arena IS the
// intent, route or no route), and `BowserCourseRow` is a two-option toggle
// whose mutual exclusion has to see both cells whether or not the route uses
// them — route focus was already withheld there for that reason.
//
// Keyed on the PLACE plus the option's identity, so it fires once on arrival
// rather than every render, and re-arms when he walks somewhere else. It never
// loops: a successful pick makes the hand non-empty, and a REFUSED one leaves
// both deps unchanged so the effect does not run again.
function useLoneRouteOption(v, lone, key, commit) {
  const empty = handIsEmpty(v.target);
  useEffect(() => {
    if (lone && empty) commit();
  }, [key, empty]);
}

// The standard segment cell (castle/arena rows, armed extras): name, strat
// sub, rank medal, resolved icon; click targets it (enabling first if
// needed — a no-op for already-enabled segments). Byte-for-byte the SAME
// PracticeCell call shape StarRow makes (round 2 unification, above) — no
// `armed` prop, no running chip; a segment cell and a star cell differ only
// in which entity's data they carry, never in how that data is drawn.
// `nameOverride` exists for the Bowser row alone: its two cells are the pair
// "Reds" / "No Reds" (user, 2026-07-30: "For all bowser stages, it's Reds or No
// Reds"), while the corpus name of the second is "BitDW Pipe Entry" -- which is
// right in the segment library and in a route, where "No Reds" would name
// nothing. So the SHORT label is this row's, not a corpus rename: renaming the
// definition would rewrite what every other surface calls it, and the row
// already shows the star as "Reds" rather than its real name for the same
// reason. `onPicked` fires after a successful explicit pick so a caller can
// remember "the user chose THIS family" (Bowser's own writeBowserFamily);
// every other caller omits it and nothing changes for them.
// A PIECE OF THIS SEGMENT no longer draws a badge here (round 31) -- it still
// nests inside this cell's practice-log card via `ui/subsections.js`, the same
// mechanism a star's pieces use.
// An `onPickOverride` prop lived here for the expanded family's fold gesture
// and was deleted with it in round 22: a cell's click is its target write
// again, with no second meaning to dispatch on.
function StandardSegmentCell({ t, s, setPicking, nameOverride, onPicked }) {
  const tgt = ((t.view || {}).target) || {};
  async function pick() {
    await pickSegmentTarget(t, s);
    if (onPicked) onPicked();
  }
  return html`<${PracticeCell} dimIdle=${STAR_DIM_IDLE}
    active=${tgt.kind === "segment" && tgt.segment_id === s.segment_id}
    iconSrc=${entityIconSrc(t, segKey(s))}
    rank=${s.rank} hasStandards=${hasStandardsFor(t.view, segKey(s))}
    caveat=${s.caveat}
    name=${nameOverride || s.name}
    sub=${stratSub(s.strat)}
    onPick=${pick}
    onEdit=${() => setPicking(iconIdentityForKey(segKey(s)))} />`;
}

// A RUNNING segment must never be invisible (spec addendum 2026-07-24):
// cells for every armed segment a row's own filter did not already show.
// Pinned into every row by tests/test_star_icons.py.
// A PIECE IS NEVER A LOOSE CELL, on any row (`isPiece`, ui/subsections.js).
// It rides its parent's practice-log card instead, and this filter is why no
// row has to remember that: the extras path is the one that drew "Key Door
// (R) → Wooden Door" beside its own parent (2026-08-09) and the stray
// "Volcano Entry" beside his LLL stars a day earlier, both because each row
// kept its own list of what to exclude. "A running segment is never
// invisible" still holds -- it is visible on its parent's card.
const armedExtraCells = (t, v, shownIds, setPicking, keep = () => true) =>
  armedSegments(t, v)
    .filter((s) => !shownIds.has(s.segment_id) && !isPiece(s) && keep(s))
    .map((s) => html`<${StandardSegmentCell} key=${`seg:${s.segment_id}`}
      t=${t} s=${s} setPicking=${setPicking} />`);

// A course's star-select screen shows that course and nothing else — never a
// castle-movement segment (user request 2026-07-24, after warping DDD -> SSL
// left "DDD → BitFS (sub)" sitting in the Shifting Sand Land row). A segment
// belongs here only if its start trigger names THIS level; movements start on
// a level_exit or a star grab, so they carry no start level and drop out.
//
// This narrows "a RUNNING segment must never be invisible" rather than
// breaking it: the header's tab-independent "Running: …" chip still names
// every armed segment, and the castle rows still offer them. Invisible in one
// course row is not invisible.
const startsInLevel = (level) => (s) => (s.start_levels || []).includes(level);

// A STAR'S PIECES ALWAYS SHOW, WITH NO SWITCH ON THE ROW (round 31,
// 2026-08-10). Round 22's badge -- one small button per [[subsection]],
// overlaid on the parent's own art -- is gone outright, not merely unused:
// Griffin, retiring it, "There's actually no point. We should just ALWAYS
// display the subsegments inside of the practice log that are associated with
// a specific star / segment. They will always be displayed / enabled and shown
// inside the practice log, just we don't need a button to enable / disable
// them now." The badge wrote the definition's own `enabled` flag via `PUT
// /api/segments/{id}` -- that flag, and its two OTHER doors (the Segments
// tab's editor checkbox and the library row's hide/show button), are
// untouched; the selector simply stopped being a third one. A piece still
// nests inside its parent's card in the practice log regardless
// (`ui/subsections.js::nestSubsections`) -- that mechanism is what "always
// displayed... inside the practice log" already meant, and needed no change
// here.
function StarRow({ t, v, stage }) {
  const [fold, toggleFold] = useCollapsed("selector");
  // hooks first — the early return below must never change the hook count
  const [setPicking, pickerModal] = useIconPicking(t);
  const course = v.catalog.courses.find((c) => c.id === stage.course_id);

  const tgt = v.target || {};
  const lastStratFor = (i) =>
    v.last_strat_by_star[`${stage.course_id}:${i}`] || "";
  // Rank under that star's ACTIVE strat (server-graded). Changing the strat
  // refreshes the view and swaps the medal automatically — see views.py.
  const rankFor = (i) =>
    (v.rank_by_star || {})[`${stage.course_id}:${i}`];
  // "this star's saved time does not mean what the medal implies", or
  // undefined. Server-derived (tracking/caveats.py) and keyed over a WIDER
  // set than rankFor: the most important caveat is that the PB carries no
  // strategy at all, which is exactly the case rank_by_star omits.
  const caveatFor = (i) =>
    (v.caveat_by_star || {})[`${stage.course_id}:${i}`];

  async function pick(i, options) {
    await requestTarget(t, {
      course_id: stage.course_id, star_id: i,
      strat_tag: lastStratFor(i) || null,
    }, options);
  }

  // Route focus (user request 2026-07-24): with a route active the selector
  // offers ONLY the stars that route collects — practising 16 Star should not
  // present the four Whomp's Fortress stars it never touches. Keys match
  // active_route.star_keys ("<course>:<star>"). No active route, or a route
  // that never visits this course, falls through to the full list rather than
  // an empty row: an empty selector reads as "broken", and standing somewhere
  // your route skips is a normal thing to do.
  const routeStars = course ? routeStarFilter(v, stage.course_id) : null;
  // INSIDE A SUBAREA, only that subarea's stars (round 21 item 5: "I'm
  // inside the volcano, so I can only do stars inside there"). The map is
  // server-vocab (`subarea_stars`, measured off his own grabs — see
  // addresses.COURSE_SUBAREA_STARS); a subarea with no row filters nothing,
  // because hiding a valid star is the worse failure. Applied AFTER the
  // route filter with the same never-empty fallback that filter has: a
  // route narrowed to stars outside this subarea must not blank the row.
  //
  // AND NOT WHILE THE STAR SELECT IS UP (round 26, and the THIRD report of
  // one symptom -- "I've mentioned this like 3 times"). The two earlier fixes
  // aimed at a LEVEL-LOAD transient, and the UI log says the narrowing tracks
  // the area byte exactly as designed at every other moment: 2 cells inside
  // the volcano, 5 in the main area, flipping correctly all session. What
  // nothing moves is the area byte while the course's own star-select screen
  // is showing -- he grabbed a star in the volcano at 09:07:40 and the next
  // spawn landed at 09:07:52, TWELVE SECONDS of that screen offering the
  // volcano's two stars. `on_the_star_select` (tracking/service.py) is true
  // from a grab until the next spawn, which is exactly that window.
  //
  // NOT WHILE THE AREA IS STILL THE LOAD'S (round 23, 2026-08-08). A course
  // load walks the area byte through a transient -- entering LLL reads the
  // volcano for 1.74 s, measured on his own journal -- and the STAR-SELECT
  // SCREEN sits inside that window, so the row narrowed to the volcano's two
  // stars on the screen where he picks which star to do: "On the star select,
  // we should show the same options as when we spawn normally." `settling`
  // (detectors/stage.py) marks exactly the emit that rode the level edge; the
  // very next area change clears it, which is precisely the moment he walked
  // somewhere himself. Showing everything is the safe side of this, and the
  // same side `COURSE_SUBAREA_STARS` already takes for a subarea it has no
  // row for.
  const subareaStars = course && !stage.settling && !stage.on_the_star_select
    ? ((t.vocab || {}).subarea_stars || {})[`${stage.level}:${stage.area}`]
    : null;
  const routeShown = course
    ? course.stars
        .map((name, i) => ({ name, i }))
        .filter(({ i }) => !routeStars || routeStars.has(`${stage.course_id}:${i}`))
    : [];
  const hereShown = subareaStars
    ? routeShown.filter(({ i }) => subareaStars.includes(i))
    : routeShown;
  const shown = hereShown.length ? hereShown : routeShown;

  // Task 0025 — DDD during 16 Star offers exactly one star, so pick it.
  // Computed BEFORE the `!course` early return because a hook may not run
  // conditionally; `shown` is empty there, so the rule answers null anyway.
  // `shown` is the route-filtered list when a route is active and the whole
  // course otherwise, which is exactly what the widened rule wants: seven
  // stars is not one, so a no-route course still picks nothing.
  const lone = loneOption(shown);
  useLoneRouteOption(v, lone, `star:${stage.course_id}:${lone ? lone.i : ""}`,
                     () => pick(lone.i, { quiet: true }));

  // A STAR'S SUBSECTIONS (task 0087, and the case Griffin named first:
  // "sometimes we want to practice only a small portion of a star") no longer
  // draw anything ON this row at all (round 31) -- they nest inside the
  // star's own practice-log card instead (`ui/subsections.js`), always shown,
  // with no switch here to forget.
  if (!course) return html`<${StagePlaceholder} t=${t} />`;

  return html`<section class="practice-card selector-card stagebanner ${cardClass(fold)}">
    <div class="shead"><b>${course.name}</b>
      <span class="meta">${routeStars
        ? html`showing this route's stars · tap to practice`
        : "tap a star to practice"}</span>
      <${CollapseToggle} collapsed=${fold} toggle=${toggleFold}
        label="the course selector" /></div>
    <${CellRow} class="starrow">
      ${shown.map(({ name, i }) => html`<${PracticeCell} dimIdle=${STAR_DIM_IDLE}
        key=${`${stage.course_id}:${i}`}
        active=${tgt.kind !== "segment"
          && tgt.course_id === stage.course_id && tgt.star_id === i}
        iconSrc=${entityIconSrc(t, starKey(stage.course_id, i))}
        fallbackSlot=${i}
        rank=${rankFor(i)} hasStandards=${hasStandardsFor(v, starKey(stage.course_id, i))}
        caveat=${caveatFor(i)}
        name=${name}
        sub=${stratSub(lastStratFor(i))}
        onPick=${() => pick(i)}
        onEdit=${() => setPicking(iconIdentityForKey(starKey(stage.course_id, i)))} />`)}
      ${armedExtraCells(t, v, new Set(), setPicking,
                        startsInLevel(stage.level))}
    <//>
    ${pickerModal}
  </section>`;
}

// BitDW/BitFS/BitS: TWO plain cells, both rendered through the shared
// StandardSegmentCell -- "Reds" (the STRICT "seg:reds->pipe:<abbrev>"
// segment: stage entry -> the reds grab as its own waypoint -> the pipe) and
// "No Reds" (the legacy EXCLUSIVE pipe-only segment, "seg:<abbrev>-pipe",
// cancelled the moment any star is grabbed).
//
// This is round 31 (task 3, 2026-08-10), and it is a DELETION, not a
// redesign: earlier rounds tried three cells (912466d), then folded the
// third into a hand-written star/pipe TOGGLE living inside a bespoke Reds
// cell (2026-07-30, "the third cell goes away... what replaces it is a
// toggle inside the Reds cell") -- full history of both in
// tests/test_stagebanner_bowser_row.py's own docstring. The toggle picked
// between grading the star grab alone or the whole run to the pipe, and it
// is gone because that choice no longer exists to make: the reds STAR is a
// [[subsection]] of the Reds movement now (task 1, views.py stamps the
// star's section with `parents: ["segment:<reds->pipe id>"]`), so it draws
// NESTED inside the movement's own practice-log card with its own PB and
// rank, never picked from here. Griffin, approving the design: "You pick
// Reds; the star grab records underneath" -- and "Fundamentally, Bowser
// Reds STAR is just... a subsection of Bowser Reds Pipe entry."
//
// `is_reds_pipe` (views.py's segment_targets) is the server-provided
// discriminator between the two Bowser segments sharing a level.
//
// Clicking Reds targets seg:reds->pipe:<abbrev> through the SAME
// requestTarget every other cell uses (via StandardSegmentCell's own
// pickSegmentTarget), so the normal target_changed flow updates everything
// else. The star still records its own attempt on every grab regardless of
// which segment is targeted (projection.py's _close_by_grab, caveat 12) --
// nothing about detection, matching or attribution changed; only what this
// row offers a PICK on shrank.
//
// `sm64.bowserFamily` (Reds-vs-No-Reds, `bowserFamilyFor`/`writeBowserFamily`)
// is REMEMBERED per level and the return-to-stage re-target still applies --
// it picks between two cells that both still exist. `sm64.bowserMode` (the
// retired star-vs-pipe sub-toggle) and the `justCompletedStar` half of the
// detection-driven memory that served it are DELETED with the toggle: a
// fresh STAR completion could previously only ever mean "light Star mode",
// which no longer exists, and the movement's own completion
// (`justCompletedSegment` on the reds->pipe segment) is still the signal
// Reds-vs-No-Reds reads.
//
// NO ROUTE OVERRIDE -- deleted 2026-08-02 and still true here: nothing in
// this row may consult the active route to decide anything (routes grade
// the bare star grab too, so a route lock hid a half the route itself
// measures -- see tests/test_stagebanner_bowser_row.py).
function BowserCourseRow({ t, v, stage, freshIds }) {
  const [fold, toggleFold] = useCollapsed("selector");
  const [setPicking, pickerModal] = useIconPicking(t);
  const course = v.catalog.courses.find((c) => c.id === stage.course_id);
  const tgt = v.target || {};
  const segs = segsForLevel(v, stage.level);
  const pipeSeg = segs.find((s) => s.is_reds_pipe);
  const noRedsSeg = segs.find((s) => !s.is_reds_pipe);

  const redsActive = !!pipeSeg
    && tgt.kind === "segment" && tgt.segment_id === pipeSeg.segment_id;

  // Both cells' own explicit picks -- StandardSegmentCell's onPicked prop
  // (below) already covers the CLICK path for each; these are the same
  // writes for the auto-retarget effect, which has no cell click to hang
  // off.
  async function pickReds(options) {
    if (!pipeSeg) return;
    await pickSegmentTarget(t, pipeSeg, options);
    writeBowserFamily(stage.level, "reds");
  }
  async function pickNoReds(options) {
    if (!noRedsSeg) return;
    await pickSegmentTarget(t, noRedsSeg, options);
    writeBowserFamily(stage.level, "no_reds");
  }

  // DETECTION drives the remembered choice too, not only a click (round 2,
  // user 2026-07-30: "if I successfully complete a Star Reds / Pipe Reds
  // run, then we should highlight the Reds card... Same can be said in the
  // inverse; if I enter the pipe without grabbing the star, then we chose to
  // do No Reds"). `freshIds` is practice.js's own attempt-id recency Set
  // (useFreshAttemptIds), threaded down through StageBanner for exactly
  // this. The star half of this detection (`justCompletedStar`, gated on a
  // stand-alone star pick) is deleted with the toggle it served -- with no
  // star pick left on this row, only the MOVEMENT's own completion can mean
  // "he chose Reds" now.
  const redsJustDone = !!pipeSeg
    && justCompletedSegment(v, freshIds, pipeSeg.segment_id);
  const noRedsJustDone = !!noRedsSeg
    && justCompletedSegment(v, freshIds, noRedsSeg.segment_id);
  useEffect(() => {
    if (redsJustDone) writeBowserFamily(stage.level, "reds");
    else if (noRedsJustDone) writeBowserFamily(stage.level, "no_reds");
  }, [redsJustDone, noRedsJustDone]);

  // Returning to a Bowser stage RE-TARGETS the remembered family (item 5,
  // round 2: "If I have selected reds (or no reds) and leave a bowser
  // stage, and come back, I would expect that same selection to persist to
  // my next session" -- read as re-targeting, not merely pre-selecting a
  // toggle nobody has clicked). Only fires when NEITHER of this row's two
  // things is already the target -- an explicit pick made just now
  // (including this very effect's own write, on the next render) must
  // never be clobbered, and a level with no remembered family is left
  // exactly as the row already renders it (no target, both cells idle).
  //
  // A SEGMENT ALREADY IN HAND IS NEVER TAKEN OUT OF IT (live report
  // 2026-08-02). This row was the third thief, after _close_by_grab's star
  // grab and ArenaRow's arena entry, and it was found the same way: he
  // picked `Bowser 1 → WF` in the lobby, walked into BitDW to run it, and
  // 17 ms after the level change this effect re-targeted the remembered
  // reds family (journal ids 240 → 246), so the movement lost its target,
  // its arm and its card. *"If I selected a segment that spans multiple
  // courses / areas, it should stay selected."* The guard declines on ANY
  // segment target, not only this row's own two cells, which is exactly the
  // case that was never the problem — a convenience default may fill an
  // empty hand; it may not take something out of one.
  useEffect(() => {
    const family = bowserFamilyFor(stage.level);
    if (!family) return;
    if (redsActive) return;
    if (tgt.kind === "segment") return;
    // `auto`: the remembered-family retarget is a fill, not a click — the
    // cell's own onPick path passes nothing and stays sovereign.
    if (family === "reds") pickReds({ auto: true });
    else if (noRedsSeg) pickNoReds({ auto: true });
  }, [stage.level]);

  if (!course) return html`<${StagePlaceholder} t=${t} />`;

  const shownIds = new Set([pipeSeg, noRedsSeg].filter(Boolean)
    .map((s) => s.segment_id));

  return html`<section class="practice-card selector-card stagebanner ${cardClass(fold)}">
    <div class="shead"><b>${course.name}</b>
      <span class="meta">tap Reds or No Reds to practice</span>

      <${CollapseToggle} collapsed=${fold} toggle=${toggleFold}
        label="the course selector" /></div>
    <${CellRow} class="starrow segcells">
      ${pipeSeg ? html`<${StandardSegmentCell}
        key=${`seg:${pipeSeg.segment_id}`} t=${t} s=${pipeSeg}
        nameOverride="Reds" setPicking=${setPicking}
        onPicked=${() => writeBowserFamily(stage.level, "reds")} />`
        : null}
      ${noRedsSeg ? html`<${StandardSegmentCell}
        key=${`seg:${noRedsSeg.segment_id}`} t=${t} s=${noRedsSeg}
        nameOverride="No Reds" setPicking=${setPicking}
        onPicked=${() => writeBowserFamily(stage.level, "no_reds")} />`
        : null}
      ${armedExtraCells(t, v, shownIds, setPicking)}
    <//>
    ${pickerModal}
  </section>`;
}

// Bowser 1/2/3 arena: the single fight segment, auto-selected on entry.
function ArenaRow({ t, v, stage }) {
  const [fold, toggleFold] = useCollapsed("selector");
  const [setPicking, pickerModal] = useIconPicking(t);
  const tgt = v.target || {};
  const fights = segsForLevel(v, stage.level);
  const only = fights.length === 1 ? fights[0] : null;

  // Auto-select the single fight on entry (request: "immediately select it
  // and set it as our active segment"). Keyed on stage.level + the segment id
  // so it fires once per arena entry, not every render; the already-targeted
  // guard makes a re-entry a no-op.
  //
  // It "always overrode the current target" until 2026-08-01 — the same
  // ruling that stopped a star grab stealing a segment pick (projection.py's
  // _close_by_grab). It was the other thief, and a worse one, because it
  // fires on mere ARRIVAL: picking "Bowser 1 → WF" and then walking into the
  // arena to run it replaced that pick with the fight, so the movement lost
  // its target, its arm and its card before he had done anything at all.
  // A convenience default may fill an empty hand; it may not take something
  // out of one.
  //
  // NARROWED 2026-08-05: `tgt.kind === "segment"` declined whenever ANY
  // segment was targeted, including one belonging to a different place
  // entirely -- so walking into Bowser 3 still holding "No Reds" (BitS) left
  // the arena's only fight unselected. Griffin: "if there's only one option,
  // that's how it should look. Even if this is our first, second, third time
  // visiting this place." The 2026-08-01 rule it protects is about a pick
  // made FOR HERE ("Bowser 1 -> WF", which starts in this very arena), and
  // `heldStartsHere` is that rule stated exactly: keep a held target this
  // stage can actually run, override one it cannot. Same question
  // `ui/stagecontext.js` answers for the pinned card, asked of the row's own
  // `v.segment_targets` rather than a second source.
  const heldStartsHere = tgt.kind === "segment" && (v.segment_targets || [])
    .some((s) => s.segment_id === tgt.segment_id
      && (s.start_areas || []).some((a) => a[0] === stage.level));
  useEffect(() => {
    if (!only) return;
    if (heldStartsHere) return;
    (async () => {
      if (!only.enabled)
        await send("PUT", `/api/segments/${only.segment_id}`, { enabled: true });
      // `auto`: an arrival fill, not a click — the server holds it by the
      // detection rules and refuses to let it steal a promoted detection.
      await requestTarget(t, { kind: "segment", segment_id: only.segment_id },
                          { auto: true });
    })();
  }, [stage.level, only && only.segment_id, heldStartsHere]);

  const extras = armedExtraCells(
    t, v, new Set(fights.map((s) => s.segment_id)), setPicking);
  if (!fights.length && !extras.length) return html`<${StagePlaceholder} t=${t} />`;

  return html`<section class="practice-card selector-card stagebanner ${cardClass(fold)}">
    <div class="shead"><b>Bowser Fight</b>
      <span class="meta">auto-selected — tap to re-arm</span>
      
      <${CollapseToggle} collapsed=${fold} toggle=${toggleFold}
        label="the course selector" /></div>
    <${CellRow} class="starrow segcells">
      ${fights.map((s) => html`<${StandardSegmentCell}
        key=${`seg:${s.segment_id}`} t=${t} s=${s} setPicking=${setPicking} />`)}
      ${extras}
    <//>
    ${pickerModal}
  </section>`;
}

function SegmentRow({ t, v, stage }) {
  const [fold, toggleFold] = useCollapsed("selector");
  const [setPicking, pickerModal] = useIconPicking(t);
  // Route focus narrows the castle's segment offer the same way it narrows the
  // star selector. Deliberately NOT applied to the Bowser/arena rows above:
  // those are two-option toggles whose mutual exclusion (reds vs no-reds)
  // needs to see the pipe segment whether or not the route uses it.
  const routeSegs = routeSegmentFilter(v);
  const here = (v.segment_targets || []).filter((s) =>
    s.enabled &&
    s.start_areas.some((a) => a[0] === stage.level && a[1] === stage.area));
  const inRoute = routeSegs
    ? here.filter((s) => routeSegs.has(s.segment_id)) : here;

  const offered = inRoute.length ? inRoute : here;   // never empty the row

  // A PIECE IS NEVER A CELL OF ITS OWN (round 22 — the same rule the star
  // row follows, and the same reason: rule 11, one implementation). Here the
  // parent is a castle MOVEMENT rather than a star, so `segs` is the
  // top-level offer and each piece nests inside whichever movement's own
  // practice-log card claims it (round 31 dropped the selector badge; the
  // nesting itself is untouched).
  //
  // `isPiece` (ui/subsections.js) is that test, and asking it of the ROW alone
  // is round 30's correction: this line used to require the parent to be in
  // `offered` too, which is a place-based question a piece cannot answer --
  // his BLJs pieces start on doors in a different subarea from BLJs itself, so
  // every one of them failed it and came back as a cell.
  //
  // A piece parented to a castle AREA (`area:<node>`) matches no entity here,
  // so it stays a top-level cell — Griffin's item 5: "if the subsection is a
  // top level subsection (e.g., it's associated with a top level area, like
  // any castle area), then it works the same as today, as a standalone top
  // level practice log entry."
  const segs = offered.filter((s) => !isPiece(s));

  // Task 0025's segment half (rule 11 — the same rule, the same module).
  // READS `segs`, the list actually drawn, since 2026-08-05. It read the
  // route-filtered `inRoute` before, precisely so that a subarea holding one
  // segment would NOT auto-pick — which is the case Griffin then asked for by
  // name ("there genuinely being only one option"). The two lists differ only
  // where the route said nothing, and a place that offers exactly one thing is
  // now a pick whether or not a route agreed.
  const lone = loneOption(segs);
  useLoneRouteOption(
    v, lone, `seg:${stage.level}:${stage.area}:${lone ? lone.segment_id : ""}`,
    () => pickSegmentTarget(t, lone, { quiet: true }));

  const drawn = new Set(segs.map((s) => s.segment_id));
  const extras = armedExtraCells(t, v, drawn, setPicking);
  if (!segs.length && !extras.length) return html`<${StagePlaceholder} t=${t} />`;

  return html`<section class="practice-card selector-card stagebanner ${cardClass(fold)}">
    <div class="shead"><b>Castle ${CASTLE_AREA_NAMES[stage.area]}</b>
      <span class="meta">tap a segment to practice</span>

      <${CollapseToggle} collapsed=${fold} toggle=${toggleFold}
        label="the course selector" /></div>
    <${CellRow} class="starrow segcells">
      ${segs.map((s) => html`<${StandardSegmentCell}
        key=${`seg:${s.segment_id}`} t=${t} s=${s} setPicking=${setPicking} />`)}
      ${extras}
    <//>
    ${pickerModal}
  </section>`;
}

// No stage-specific banner (hub level, unknown mode) but a segment timer is
// live: keep it visible here instead of the placeholder.
function ArmedOnlyRow({ t, v }) {
  const [fold, toggleFold] = useCollapsed("selector");
  const [setPicking, pickerModal] = useIconPicking(t);
  return html`<section class="practice-card selector-card stagebanner ${cardClass(fold)}">
    <div class="shead"><b>Running</b>
      <span class="meta">a segment timer is live</span>
      
      <${CollapseToggle} collapsed=${fold} toggle=${toggleFold}
        label="the course selector" /></div>
    <${CellRow} class="starrow segcells">
      ${armedSegments(t, v).map((s) => html`<${StandardSegmentCell}
        key=${`seg:${s.segment_id}`} t=${t} s=${s} setPicking=${setPicking} />`)}
    <//>
    ${pickerModal}
  </section>`;
}
