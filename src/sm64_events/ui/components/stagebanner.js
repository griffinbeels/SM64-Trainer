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
//   "bowser_course" : BitDW/BitFS/BitS -> TWO targets: the "reds" 8-coin star
//                     AND the level's "no reds" pipe-entry segment. Picking one
//                     flips the pipe segment's `enabled` (mutual exclusion):
//                     "no reds" enables + targets it, "reds" disables it +
//                     targets the star.
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
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { CellRow, SurfaceExchange } from "./cellrow.js";
import { CollapseToggle, cardClass, useCollapsed } from "./collapsible.js";
import { send } from "../api.js";
import { armedSegments, hasPracticeContext, hasStandardsFor,
         justCompletedSegment, justCompletedStar, practiceMode,
         selectorSurfaceId } from "../stagecontext.js";
import { requestTarget } from "../target.js";
import { handIsEmpty, loneOption } from "../loneoption.js";
import { CellToggles } from "./celltoggles.js";
import { Icon } from "./icons.js";
import { PracticeCell } from "./practicecell.js";
import { caveatOf, cellBadge } from "./marks.js";
import { iconIdentityForKey, useIconPicking } from "./iconpicker.js";
import { entityIconSrc, fallbackToGenericStar, genericStarSrc } from "./entityicons.js";
import { RankIcon } from "./rankicon.js";
import { RANK_FLOOR } from "./caps.js";
import { useRouteSwap } from "../routeswap.js";
import { mareloTuning } from "../marelotuning.js";
import { familyLabel } from "../redsfamily.js";

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
// Which half of a Bowser Reds run the player is timing, REMEMBERED per level
// (user, 2026-07-30: "We need to remember the option that the user selected ...
// the last time they visited a bowser stage. Currently we do not remember
// this").
//
// It used to be DERIVED from the target (`pipeMode = !starActive`), and that is
// exactly why it could not be remembered AND why it flipped on its own: grabbing
// the reds star makes that star the thing the app last saw you do, the derived
// value went true, and the Star button lit itself mid-run. Explicit state cannot
// be flipped by something you did in the level.
//
// localStorage, keyed by LEVEL, alongside the other per-client look/preference
// keys (`sm64.starIcons`, `sm64.runFocus`, `sm64.activeRoute`). Not server state:
// it selects which of two real entities you are pointed at, and the pointing
// itself is already server state via the target.
const BOWSER_MODE_KEY = "sm64.bowserMode";
const BOWSER_MODES = ["pipe", "star"];
const DEFAULT_BOWSER_MODE = "pipe";   // "By default, the PIPE should be selected"

function readBowserModes() {
  try { return JSON.parse(localStorage.getItem(BOWSER_MODE_KEY)) || {}; }
  catch { return {}; }
}
export function bowserModeFor(level) {
  const stored = readBowserModes()[String(level)];
  return BOWSER_MODES.includes(stored) ? stored : DEFAULT_BOWSER_MODE;
}
function writeBowserMode(level, mode) {
  if (!BOWSER_MODES.includes(mode)) return;
  const all = readBowserModes();
  all[String(level)] = mode;
  try { localStorage.setItem(BOWSER_MODE_KEY, JSON.stringify(all)); } catch { /* full */ }
}

// WHICH of the two top-level cells (Reds vs No Reds) was last explicitly
// practiced, remembered per level — the sibling of BOWSER_MODE_KEY above
// (that one is the star/pipe TOGGLE *within* Reds; this is the choice
// between Reds and No Reds itself). Round 2, item 5 (user, 2026-07-30: "If I
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
// `toggles` is components/celltoggles.js's overlay -- this cell's own pieces,
// drawn inside its art. An `onPickOverride` prop lived here for the expanded
// family's fold gesture and was deleted with it in round 22: a cell's click is
// its target write again, with no second meaning to dispatch on.
function StandardSegmentCell({ t, s, setPicking, nameOverride, onPicked,
                              toggles = null }) {
  const tgt = ((t.view || {}).target) || {};
  async function pick() {
    await pickSegmentTarget(t, s);
    if (onPicked) onPicked();
  }
  return html`<${PracticeCell} dimIdle=${STAR_DIM_IDLE}
    active=${tgt.kind === "segment" && tgt.segment_id === s.segment_id}
    toggles=${toggles}
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
const armedExtraCells = (t, v, shownIds, setPicking, keep = () => true) =>
  armedSegments(t, v)
    .filter((s) => !shownIds.has(s.segment_id) && keep(s))
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

// A STAR'S PIECES ARE SWITCHES ON THE STAR, NOT CELLS BESIDE IT (round 22,
// 2026-08-08). This replaces PROGRESSIVE DISCLOSURE outright -- `useFamilyView`
// and the whole of `ui/subsections.js` (familyRoot / visibleEntities /
// isExpanded), the `.selector-expanded` treatment and the "tap the parent again
// to go back" fold are DELETED, not disabled.
//
// Griffin, opening the round: "as a complete redesign / upgrade, we should
// actually re-use the same exact design we use as the Pipe/Star selector for
// the Bowser stages... EVERY SUBSECTION ADDED TO A STAR just appears as a new
// small button overlayed... the buttons are not toggles BETWEEN options, but
// rather enable/disable options."
//
// What that retires and why it is a deletion rather than a patch: with no
// subsection ever loose in the row there is no family to expand INTO, so there
// is no fold to come back FROM and no `preferredRoot` tie to break when a piece
// has several parents. Round 20's plural parents survive intact -- the question
// simply moved from "which family does the row expand into" to "which stars
// draw this badge", and `parents.includes(starKey)` answers both.
//
// The switch is the definition's own `enabled` flag, which already exists and
// is per-DEFINITION rather than per-parent. His call, asked and answered at
// capture: dimming a piece dims it EVERYWHERE. The argument he took is that
// per-parent could only ever hide a CARD -- one definition has one live timer,
// so it cannot record under one parent and not another -- and he asked for
// tracking to stop, not for a display filter.
function subsectionToggles(t, subsections, parentKey) {
  const mine = subsections.filter(
    (s) => (s.parents || []).includes(parentKey));
  if (!mine.length) return null;
  const flip = (s) => send("PUT", `/api/segments/${s.segment_id}`,
                           { enabled: !s.enabled });
  return html`<${CellToggles} toggles=${mine.map((s) => ({
    key: `seg:${s.segment_id}`,
    iconSrc: entityIconSrc(t, segKey(s)),
    selected: !!s.enabled,
    title: s.enabled
      ? `${s.name} — tracked; click to stop tracking it`
      : `${s.name} — not tracked; click to track it`,
    onToggle: () => flip(s),
    onIconError: (errorEvent) => fallbackToGenericStar(errorEvent, 0),
  }))} />`;
}

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

  // A STAR'S SUBSECTIONS BELONG HERE (task 0087, and the case Griffin named
  // first: "sometimes we want to practice only a small portion of a star"),
  // and since round 22 they belong INSIDE the star's own cell rather than
  // beside it -- see `subsectionToggles`. DISABLED ones are kept in this
  // list on purpose: the badge is the only control that can turn one back on,
  // so filtering them out here would make the switch one-way.
  const subsections = (v.segment_targets || [])
    .filter((s) => (s.parents || []).length > 0
                   && startsInLevel(stage.level)(s));
  // Every piece drawn as a badge, so the armed-extras union below does not
  // ALSO draw it as a loose cell -- which is exactly what put a stray "Volcano
  // Entry" beside his two LLL stars (his screenshot, 2026-08-09). "A running
  // segment is never invisible" still holds: it is visible on its parent.
  const badged = new Set(subsections.map((s) => s.segment_id));
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
        toggles=${subsectionToggles(t, subsections,
                                       starKey(stage.course_id, i))}
        onPick=${() => pick(i)}
        onEdit=${() => setPicking(iconIdentityForKey(starKey(stage.course_id, i)))} />`)}
      ${armedExtraCells(t, v, badged, setPicking,
                        startsInLevel(stage.level))}
    <//>
    ${pickerModal}
  </section>`;
}

// BitDW/BitFS/BitS: TWO cells since 2026-07-30 (spec 2026-07-28-multi-step-
// segments, "the Bowser Reds star/pipe toggle") — "No Reds" (the legacy
// EXCLUSIVE pipe-only segment, "seg:<abbrev>-pipe", cancelled the moment any
// star is grabbed) and "Reds", which folds what used to be a THIRD cell
// (the STRICT "seg:reds->pipe:<abbrev>" segment, a waypoint on the reds grab
// then the pipe entry) into a star/pipe TOGGLE inside the Reds cell itself:
// a reds run is ONE practiced thing worth timing two ways -- the grab alone
// or the whole run to the pipe -- never three separate cells (user's own
// words: "the third cell goes away... what replaces it is a toggle inside
// the Reds cell").
//
// `is_reds_pipe` (views.py's segment_targets) is the server-provided
// discriminator between the two Bowser segments sharing a level -- replacing
// the by-NAME guess this row's own docstring used to flag as a future-rename
// risk ("<Abbrev> — 8 Red Coins → Pipe" vs "<Abbrev> Pipe Entry", no other
// signal to tell them apart).
//
// Clicking the STAR icon targets the star (ends at the grab, grades the
// " (Star)" strategies); clicking the PIPE icon targets seg:reds->pipe:
// <abbrev> (stage entry -> grab -> pipe, grades " (Pipe)") -- both feed the
// SAME requestTarget every other cell uses, so the normal target_changed
// flow updates everything else. The displayed selection is DERIVED from the
// current target (star vs the paired segment), never stored client-side --
// the same "memory can't disagree with what's tracking" reasoning the
// retired mutual-exclusion memory used, and default PIPE (user's mock-up)
// falls out for free as "anything but an explicit star pick".
//
// NO ROUTE OVERRIDE -- deleted 2026-08-02, and written down because the thing
// that was wrong was the PREMISE, not the code. An active route naming this
// stage's reds used to force Pipe and disable the star half (user, 2026-07-27:
// "you can't just stop at the star grab for reds, you HAVE to do the pipe
// timing"), justified here by the claim that every seeded Bowser Reds route
// step "already names seg:reds->pipe:<abbrev>, never the bare star". That claim
// was false in all eight instances: tools/corpus_routes_main.py pairs
// `star(16, 0, "BitDW - 8 Red Coins")` WITH `*BOWSER_1_REDS` every time (and
// 17/18 likewise), so the bare grab is a graded route step in its own right and
// the lock hid a half the route itself measures. It read as a plain bug too --
// 16 Star does reds only in BitDW, so BitDW sat dead beside two freely
// toggleable siblings (live report 2026-08-02, three screenshots). Pinned by
// tests/test_stagebanner_bowser_row.py.
function BowserCourseRow({ t, v, stage, freshIds }) {
  const [fold, toggleFold] = useCollapsed("selector");
  const [setPicking, pickerModal] = useIconPicking(t);
  const course = v.catalog.courses.find((c) => c.id === stage.course_id);
  const tgt = v.target || {};
  const segs = segsForLevel(v, stage.level);
  const pipeSeg = segs.find((s) => s.is_reds_pipe);
  const noRedsSeg = segs.find((s) => !s.is_reds_pipe);

  const starActive = tgt.kind !== "segment"
    && tgt.course_id === stage.course_id && tgt.star_id === 0;
  const pipeTargeted = !!pipeSeg
    && tgt.kind === "segment" && tgt.segment_id === pipeSeg.segment_id;
  const redsActive = starActive || pipeTargeted;

  // EXPLICIT, remembered per level -- never derived from the target. Deriving it
  // is what made the Star button light itself the moment you grabbed the reds
  // star while timing to the pipe (user, 2026-07-30: "it incorrectly SWAPS OVER
  // TO STAR MODE ... despite me being in pipe mode"). Nothing that happens in
  // the level moves this now; only a click does.
  const [mode, setModeState] = useState(() => bowserModeFor(stage.level));
  const setMode = (next) => { writeBowserMode(stage.level, next); setModeState(next); };
  // Re-read on level change: one mounted row serves BitDW, BitFS and BitS in
  // turn, so without this the first stage's choice would follow you into the
  // next one and read as the memory being broken rather than shared.
  useEffect(() => { setModeState(bowserModeFor(stage.level)); }, [stage.level]);

  // The remembered choice is the ONLY input -- see the route-lock paragraph in
  // this function's own docstring for the override that used to sit here.
  const pipeMode = mode === "pipe";

  async function pickStar(options) {
    setMode("star");
    writeBowserFamily(stage.level, "reds");
    await requestTarget(t, { course_id: stage.course_id, star_id: 0 }, options);
  }
  async function pickPipe(options) {
    setMode("pipe");
    writeBowserFamily(stage.level, "reds");
    if (!pipeSeg) return;
    if (!pipeSeg.enabled)
      await send("PUT", `/api/segments/${pipeSeg.segment_id}`, { enabled: true });
    await requestTarget(t, { kind: "segment", segment_id: pipeSeg.segment_id },
                        options);
  }
  // Clicking the CARD selects it in whatever mode is already chosen (user,
  // 2026-07-30, with a mock-up box drawn round the whole cell: "if we just click
  // on the card itself normally, it should activate it (with whatever pipe/star
  // mode we have selected already)"). The two toggle buttons stay their own
  // targets and stopPropagation, so this never double-fires.
  const pickCard = () => (pipeMode ? pickPipe() : pickStar());
  // The "No Reds" cell's own explicit pick -- StandardSegmentCell's onPicked
  // prop (below) already covers the CLICK path; this is the same write for
  // the auto-retarget effect (item 5), which has no cell click to hang off.
  async function pickNoReds(options) {
    if (!noRedsSeg) return;
    await pickSegmentTarget(t, noRedsSeg, options);
    writeBowserFamily(stage.level, "no_reds");
  }

  // DETECTION drives the remembered choice too, not only a click (item 2,
  // round 2, user 2026-07-30: "if I successfully complete a Star Reds / Pipe
  // Reds run, then we should highlight the Reds card... Same can be said in
  // the inverse; if I enter the pipe without grabbing the star, then we
  // chose to do No Reds"). `freshIds` is practice.js's own attempt-id
  // recency Set (useFreshAttemptIds), threaded down through StageBanner for
  // exactly this -- both an earlier session on this branch and the sibling
  // that shipped the 100-coin star independently found they needed it here
  // and declined to half-build the plumbing; this finishes it.
  //
  // The star half is gated on `starActive` (the CURRENT target really is
  // this star) specifically to tell "a stand-alone Star-timed run just
  // finished" apart from "the reds star was merely GRABBED as this Pipe
  // segment's own waypoint" -- projection.py's _close_by_grab always
  // records a real star attempt on every reds grab, pipe run or not, so a
  // fresh success alone can't tell the two apart. What can: projection.py
  // now protects an explicitly-targeted, still-armed segment's target from
  // being stolen by its own mid-sequence grab (the round-2 flash fix, item
  // 3), so while a Pipe run is genuinely in progress the target never
  // becomes the star at all and `starActive` stays false throughout --
  // only a real stand-alone star pick ever satisfies this guard. A flip on
  // the wrong event would be worse than the manual toggle it replaces.
  const starJustDone = starActive
    && justCompletedStar(v, freshIds, stage.course_id, 0);
  const pipeJustDone = !!pipeSeg
    && justCompletedSegment(v, freshIds, pipeSeg.segment_id);
  const noRedsJustDone = !!noRedsSeg
    && justCompletedSegment(v, freshIds, noRedsSeg.segment_id);
  useEffect(() => {
    if (starJustDone) { setMode("star"); writeBowserFamily(stage.level, "reds"); }
    else if (pipeJustDone) { setMode("pipe"); writeBowserFamily(stage.level, "reds"); }
    else if (noRedsJustDone) { writeBowserFamily(stage.level, "no_reds"); }
  }, [starJustDone, pipeJustDone, noRedsJustDone]);

  // Returning to a Bowser stage RE-TARGETS the remembered family (item 5,
  // round 2: "If I have selected reds (or no reds) and leave a bowser
  // stage, and come back, I would expect that same selection to persist to
  // my next session" -- read as re-targeting, not merely pre-selecting a
  // toggle nobody has clicked). Only fires when NEITHER of this row's two
  // things is already the target -- an explicit pick made just now
  // (including this very effect's own write, on the next render) must
  // never be clobbered, and a level with no remembered family is left
  // exactly as the row already renders it (no target, both cells idle).
  // The remembered star/pipe SUB-mode is read fresh off localStorage here
  // rather than off `mode` state: this effect and the mode-refresh effect
  // above both fire off the same [stage.level] change, and a `setState`
  // call doesn't update its own variable inside the same commit -- reading
  // bowserModeFor directly sidesteps that ordering question entirely.
  //
  // A SEGMENT ALREADY IN HAND IS NEVER TAKEN OUT OF IT (live report
  // 2026-08-02). This row was the third thief, after _close_by_grab's star
  // grab and ArenaRow's arena entry, and it was found the same way: he
  // picked `Bowser 1 → WF` in the lobby, walked into BitDW to run it, and
  // 17 ms after the level change this effect re-targeted the remembered
  // reds family (journal ids 240 → 246), so the movement lost its target,
  // its arm and its card. *"If I selected a segment that spans multiple
  // courses / areas, it should stay selected."* The old guard only declined
  // when the target was one of THIS row's own two cells, which is exactly
  // the case that was never the problem — a convenience default may fill an
  // empty hand; it may not take something out of one.
  useEffect(() => {
    const family = bowserFamilyFor(stage.level);
    if (!family) return;
    if (redsActive) return;
    if (tgt.kind === "segment") return;
    // `auto`: the remembered-family retarget is a fill, not a click — the
    // cell's own onPick path passes nothing and stays sovereign.
    if (family === "reds") {
      if (bowserModeFor(stage.level) === "pipe") pickPipe({ auto: true });
      else pickStar({ auto: true });
    } else if (noRedsSeg) {
      pickNoReds({ auto: true });
    }
  }, [stage.level]);

  if (!course) return html`<${StagePlaceholder} t=${t} />`;

  const shownIds = new Set([pipeSeg, noRedsSeg].filter(Boolean)
    .map((s) => s.segment_id));

  return html`<section class="practice-card selector-card stagebanner ${cardClass(fold)}">
    <div class="shead"><b>${course.name}</b>
      <span class="meta">tap Star or Pipe to pin the reds run</span>

      <${CollapseToggle} collapsed=${fold} toggle=${toggleFold}
        label="the course selector" /></div>
    <${CellRow} class="starrow segcells">
      <${RedsCell} t=${t} v=${v} stage=${stage} course=${course}
        redsActive=${redsActive} pipeMode=${pipeMode}
        pipeSeg=${pipeSeg} onPickStar=${pickStar} onPickPipe=${pickPipe}
        onPickCard=${pickCard} setPicking=${setPicking} />
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

// The Reds cell's own rank badge -- shaped {tier, division, fill, label} for
// useRouteSwap, which only ever reads tier/division/fill numerically; label
// is unused here (no text crossfade in this compact cell, unlike the route
// rank card) but kept non-null so two different ranks in the same tier still
// count as "changed enough to know which one is shown" is unnecessary --
// useRouteSwap keys off tier/division/fill itself, this is just the shape it
// expects.
function redsSwapEntry(rank) {
  return { tier: rank ? rank.rank : null, division: rank ? rank.division : null,
           fill: 0, label: "" };
}

// Reds cell: the star's own art as the base (same entityIconSrc chain every
// other cell uses -- user overrides / course-icon mode apply here exactly as
// anywhere else), with the star/pipe toggle OVERLAID at the bottom-centre
// (mock-up: "star_3.png ... overlaid on top of the Reds segment icon in the
// bottom center"). Not a <button> itself (unlike PracticeCell) -- the two
// toggle icons ARE the only interactive surface here (mock-up: "Both icons
// should be a button"), and a <button> cannot legally nest two more.
// star_3.png is entities.js's own GENERIC_STAR_SLOTS asset (slot 2,
// genericStarSrc) rather than a literal path -- tests/test_single_source.py
// guards "/ui/assets/star_" to entities.js/entityicons.js only, since three
// surfaces once each derived their own star-art stem and disagreed.
// pipe_icon.png is a new, single-purpose glyph with no other consumer, so it
// is named directly here rather than adding a second door for one call site.
function RedsCell({ t, v, stage, course, redsActive, pipeMode,
                   pipeSeg, onPickStar, onPickPipe, onPickCard, setPicking }) {
  const starRank = (v.rank_by_star || {})[`${stage.course_id}:0`];
  const pipeRank = pipeSeg ? pipeSeg.rank : null;
  const shownRank = pipeMode ? pipeRank : starRank;
  // This cell cannot BE a PracticeCell (it nests two toggle buttons and a
  // <button> may not contain one), so rule 11 has to be honoured by hand here
  // -- which is the standing risk with this cell and the reason the caveat is
  // taken from the same two server fields the shared cell reads, per family.
  const mark = caveatOf(pipeMode
    ? (pipeSeg ? pipeSeg.caveat : null)
    : (v.caveat_by_star || {})[`${stage.course_id}:0`]);
  // Unranked but rankable shows the ladder FLOOR rather than "-". Both
  // families live on the STAR entity (the pipe segment has no ladder of its
  // own -- views.py pairs it to the star's ek), so the standards question is
  // asked of the star for either mode.
  const floorRank = !shownRank
      && !(mark && mark.suppressFloor)
      && hasStandardsFor(v, starKey(stage.course_id, 0))
    ? { rank: RANK_FLOOR.tier, division: RANK_FLOOR.division } : null;

  // Squash/pop between families on every toggle -- REUSE of marelo.js's own
  // route-swap hook, not a second hand-tuned curve (user named the
  // reference explicitly: "like how we do it identically in the route
  // change MARELO transition"). Keyed on course + which family is shown, so
  // switching courses never carries a stale swap over.
  const swap = useRouteSwap(`${stage.course_id}:${pipeMode ? "pipe" : "star"}`,
    redsSwapEntry(shownRank), mareloTuning());
  const swapping = !!swap;
  const iconTier = swapping ? (swap.crossed ? swap.to.tier : swap.from.tier)
    : (shownRank ? shownRank.rank : null);
  const iconDivision = swapping ? (swap.crossed ? swap.to.division : swap.from.division)
    : (shownRank ? shownRank.division : null);
  const iconProps = swapping ? swap.icon : null;

  function editKey(keyEvent) {
    if (keyEvent.key !== "Enter" && keyEvent.key !== " ") return;
    keyEvent.preventDefault(); keyEvent.stopPropagation();
    setPicking(iconIdentityForKey(starKey(stage.course_id, 0)));
  }

  // The whole card is a click target, in the mode already selected. Kept a
  // <div role="button"> rather than a real <button>: this cell nests two
  // buttons and the ✎, and a <button> may not contain a button.
  const cardKey = (keyEvent) => {
    if (keyEvent.key !== "Enter" && keyEvent.key !== " ") return;
    keyEvent.preventDefault(); onPickCard();
  };
  return html`<div class="starcell reds-cell ${redsActive ? "active-star" : ""}"
      role="button" tabindex="0"
      title=${pipeMode ? "Practice Reds, timed to the pipe"
                       : "Practice Reds, timed to the star grab"}
      onclick=${() => onPickCard()} onkeydown=${cardKey}>
    ${/* First child, a direct sibling of .starholder -- .caveat-badge is
         absolutely positioned against .starcell, which this cell also is, so
         it lands in the same corner it does on every shared cell. */""}
    ${mark ? cellBadge(mark) : null}
    <span class="starholder">
      <img class="starimg ${redsActive ? "" : "dim"}"
           src=${entityIconSrc(t, starKey(stage.course_id, 0))}
           onerror=${(errorEvent) => fallbackToGenericStar(errorEvent, 0)}
           alt="" draggable="false" />
      ${/* THE SAME overlay a star's subsections use (components/celltoggles.js)
           -- two buttons here, mutually exclusive, with the clock between
           them; N buttons and no clock there. Exclusivity lives in these two
           handlers and nowhere inside the shared component, which is what
           lets one implementation serve both without growing a mode. */""}
      <${CellToggles}
        separator=${html`<span class="reds-toggle-clock" aria-hidden="true">
          <${Icon} name="clock" size=${12} /></span>`}
        toggles=${[
          { key: "star", iconSrc: genericStarSrc(2), ariaLabel: "Star",
            title: "Track the star grab alone",
            selected: !pipeMode, onToggle: onPickStar },
          { key: "pipe", iconSrc: "/ui/assets/pipe_icon.png", ariaLabel: "Pipe",
            title: "Track the run to the pipe",
            selected: pipeMode, onToggle: onPickPipe },
        ]} />
      <span class="reds-arrows" aria-hidden="true">
        <span class="reds-arrow left ${!pipeMode ? "is-lit" : ""}">◀</span>
        <span class="reds-arrow right ${pipeMode ? "is-lit" : ""}">▶</span>
      </span>
    </span>
    <span class="starrank">
      ${iconTier ? html`<${RankIcon} ...${iconProps} tier=${iconTier}
          division=${iconDivision} size=${16} />`
        : (floorRank ? html`<${RankIcon} tier=${floorRank.rank}
            division=${floorRank.division} size=${16} />` : "–")}
    </span>
    <span class="starname">Reds</span>
    ${/* The FAMILY, spelled out, because the two are graded against different
         ladders and a medal that changes with no label to explain it reads as a
         rendering fault (user, 2026-07-30: 'When Pipe is selected, it should be
         displayed as "8 Red Coins (Pipe)", and when Star is selected, it\'s
         displayed as "8 Red Coins (Star)"'). The star's own corpus name leads,
         so this still says what you are practising; the suffix says which half
         is on the clock. `Reds`/`No Reds` stay the cell NAMES, which is the
         pair he asked for one item later. familyLabel (../redsfamily.js) is
         the ONE place the " (Star)"/" (Pipe)" suffix is spelled -- the pinned
         card (practice.js) composes the SAME literal through the same call,
         never a second copy (round 2, item 4's star half). */""}
    <span class="starsub"><span class="strat">
      ${familyLabel(course.stars[0] || "8 Red Coins", pipeMode)}
    </span></span>
    <span class="editicon" role="button" tabindex="0"
        title="Choose icon…" aria-label="Choose icon"
        onclick=${(clickEvent) => { clickEvent.stopPropagation();
          setPicking(iconIdentityForKey(starKey(stage.course_id, 0))); }}
        onkeydown=${editKey}>✎</span>
  </div>`;
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

  // A PIECE IS A BADGE ON ITS PARENT, NEVER A CELL (round 22 — the same rule
  // the star row follows, and the same reason: rule 11, one implementation).
  // Here the parent is a castle MOVEMENT rather than a star, so `segs` is the
  // top-level offer and each piece rides whichever movement claims it.
  //
  // A piece parented to a castle AREA (`area:<node>`) matches no entity here,
  // so it stays a top-level cell — Griffin's item 5: "if the subsection is a
  // top level subsection (e.g., it's associated with a top level area, like
  // any castle area), then it works the same as today, as a standalone top
  // level practice log entry."
  const segs = offered.filter((s) => !(s.parents || []).some(
    (parent) => offered.some((other) => segKey(other) === parent)));
  const pieces = (v.segment_targets || []).filter(
    (s) => (s.parents || []).length > 0
           && s.start_areas.some((a) => a[0] === stage.level
                                        && a[1] === stage.area));

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

  const drawn = new Set([...segs.map((s) => s.segment_id),
                         ...pieces.map((s) => s.segment_id)]);
  const extras = armedExtraCells(t, v, drawn, setPicking);
  if (!segs.length && !extras.length) return html`<${StagePlaceholder} t=${t} />`;

  return html`<section class="practice-card selector-card stagebanner ${cardClass(fold)}">
    <div class="shead"><b>Castle ${CASTLE_AREA_NAMES[stage.area]}</b>
      <span class="meta">tap a segment to practice</span>

      <${CollapseToggle} collapsed=${fold} toggle=${toggleFold}
        label="the course selector" /></div>
    <${CellRow} class="starrow segcells">
      ${segs.map((s) => html`<${StandardSegmentCell}
        key=${`seg:${s.segment_id}`} t=${t} s=${s} setPicking=${setPicking}
        toggles=${subsectionToggles(t, pieces, segKey(s))} />`)}
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
