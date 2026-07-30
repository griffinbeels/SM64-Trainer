// src/sm64_events/ui/components/stagebanner.js
// Quick-select row, driven by t.stage (the broadcast-only stage_changed event)
// and dispatched on its `mode`:
//   "stars"         : a main course 1-15 -> that course's stars (name +
//                     last-strategy subtext); click sets the star target.
//                     The "100 Coins" cell is special: the server always
//                     redirects that pick to the course's own 100-coin-exit
//                     SEGMENT (tracking/service.py::_hundred_coin_redirect —
//                     "nobody times just the 100 star grab, it's always with
//                     something else", user 2026-07-24), and since that
//                     segment now arms on course ENTRY (corpus reshape
//                     2026-07-29) it is armed on every visit. This row shows
//                     the segment's own rank/strat ON the star cell rather
//                     than appending a duplicate extra cell, and only glows
//                     the cell active for a deliberate pick or a just-landed
//                     success — never merely because it is silently tracking
//                     (live report 2026-07-29, see hundredCoinSegmentFor).
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
import { useEffect } from "preact/hooks";
import htm from "htm";
import { CollapseToggle, cardClass, useCollapsed } from "./collapsible.js";
import { send } from "../api.js";
import { armedSegments, hasPracticeContext, justCompletedSegment,
        practiceMode } from "../stagecontext.js";
import { requestTarget } from "../target.js";
import { Icon } from "./icons.js";
import { PracticeCell } from "./practicecell.js";
import { iconIdentityForKey, useIconPicking } from "./iconpicker.js";
import { entityIconSrc, fallbackToGenericStar, genericStarSrc } from "./entityicons.js";
import { RankIcon } from "./rankicon.js";
import { useRouteSwap } from "../routeswap.js";
import { mareloTuning } from "../marelotuning.js";

const html = htm.bind(h);

const CASTLE_AREA_NAMES = { 1: "Lobby", 2: "Upstairs", 3: "Basement" };

// One row per PRACTICE_MODES id. The two lists are pinned to each other by
// tests/test_ui_practice_context.py: a mode missing here would fall through to
// the armed-only row, and a row whose mode is missing there is unreachable,
// because the context question below is asked FIRST.
const STAGE_ROWS = { stars: StarRow, bowser_course: BowserCourseRow,
                     arena: ArenaRow, castle: SegmentRow };

export function StageBanner({ t, freshIds }) {
  const v = t.view;
  // The one door (../stagecontext.js), shared with the Active-target card so
  // the two cannot say different things about the same place — they did, at
  // the file select, where this drew its placeholder while the card below
  // still named a star from the session before.
  if (!hasPracticeContext(t)) return html`<${StagePlaceholder} t=${t} />`;
  const Row = STAGE_ROWS[practiceMode(t)];
  // freshIds is practice.js's own attempt-id recency Set (useFreshAttemptIds)
  // — threaded through so StarRow can tell a just-landed 100-coin success
  // apart from mere background tracking without inventing a second notion of
  // "recent". Only StarRow reads it; the other rows ignore the extra prop.
  return Row ? html`<${Row} t=${t} v=${v} stage=${t.stage} freshIds=${freshIds} />`
             : html`<${ArmedOnlyRow} t=${t} v=${v} />`;
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
const segKey = (s) => `segment:${s.segment_id}`;
const starKey = (courseId, slot) => `star:${courseId}:${slot}`;

// Row-level icon-picking state (the ✎ on any cell) is iconpicker.js's
// useIconPicking, shared with the Rank tab's coverage tiles since 2026-07-26.

// The banner cell is components/practicecell.js — shared with the entity
// picker's grid so a star looks the same where you pick it and where you
// practice it. The banner passes dimIdle (its own look) and onEdit (the ✎
// icon override, which only exists here).

// The armed sub-line: the running chip replaces the strat while the
// segment's start condition is met (timer live NOW).
const runningChip = html`<span class="chip good">⏱ running</span>`;
const stratSub = (strat) =>
  html`<span class="strat ${strat ? "" : "none"}">${strat || "—"}</span>`;

// The standard segment cell (castle/arena rows, armed extras): name, strat
// sub (running chip while armed), rank medal, resolved icon; click targets
// it (enabling first if needed — a no-op for already-enabled segments).
function StandardSegmentCell({ t, s, setPicking }) {
  const tgt = ((t.view || {}).target) || {};
  const armed = t.armedSegs.has(s.segment_id);
  async function pick() {
    if (!s.enabled)
      await send("PUT", `/api/segments/${s.segment_id}`, { enabled: true });
    await requestTarget(t, { kind: "segment", segment_id: s.segment_id });
  }
  return html`<${PracticeCell} dimIdle=${STAR_DIM_IDLE}
    active=${tgt.kind === "segment" && tgt.segment_id === s.segment_id}
    armed=${armed}
    iconSrc=${entityIconSrc(t, segKey(s))}
    rank=${s.rank} name=${s.name}
    sub=${armed ? runningChip : stratSub(s.strat)}
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

// The 100-coin star (star_id 6, addresses.py's own special case) is
// redirected server-side to that course's 100-coin-exit SEGMENT before any
// target write lands (tracking/service.py::_hundred_coin_redirect). Since the
// corpus reshape that arms this segment on course ENTRY (corpus_movements.py,
// 2026-07-29) it is armed on every visit, which used to append it as an
// eighth cell next to the seven stars via armedExtraCells — the same thing
// shown twice under two different names (live report 2026-07-29).
//
// Identified here the same way armedExtraCells already filters extras for
// this row — startsInLevel(stage.level) — because that IS this segment's
// distinguishing fact: this row's own comment on startsInLevel notes a plain
// course's only other segments are castle movements, which start on a
// level_exit or a star grab and carry no start level, so no other
// segment_targets entry can match a main course's OWN level. A disabled
// segment degrades to the plain star, mirroring _hundred_coin_redirect's own
// fallback — the enabled filter keeps the two in agreement.
const hundredCoinSegmentFor = (v, level) =>
  (v.segment_targets || []).find((s) => s.enabled && startsInLevel(level)(s));

// "Just completed" — moved to stagecontext.js (2026-07-30, live report: the
// pinned card needed the IDENTICAL recency notion, not a second one) so both
// this cell and practice.js's pinned-card gate import the same function.

function StarRow({ t, v, stage, freshIds }) {
  const [fold, toggleFold] = useCollapsed("selector");
  // hooks first — the early return below must never change the hook count
  const [setPicking, pickerModal] = useIconPicking(t);
  const course = v.catalog.courses.find((c) => c.id === stage.course_id);
  if (!course) return html`<${StagePlaceholder} t=${t} />`;

  const tgt = v.target || {};
  const lastStratFor = (i) =>
    v.last_strat_by_star[`${stage.course_id}:${i}`] || "";
  // Rank under that star's ACTIVE strat (server-graded). Changing the strat
  // refreshes the view and swaps the medal automatically — see views.py.
  const rankFor = (i) =>
    (v.rank_by_star || {})[`${stage.course_id}:${i}`];

  // The "100 Coins" cell overwrites itself with the segment it redirects to
  // — see hundredCoinSegmentFor's comment above. Active/armed styling is
  // gated tighter than a normal segment cell (defect 2026-07-29): this
  // segment now arms on every course entry, so it must track SILENTLY and
  // only glow when the player actually chose it or it just paid off — never
  // merely because it happens to be running in the background.
  const hundredCoinSeg = hundredCoinSegmentFor(v, stage.level);
  const hcTargeted = !!hundredCoinSeg && tgt.kind === "segment"
    && tgt.segment_id === hundredCoinSeg.segment_id;
  const hcJustCompleted = !!hundredCoinSeg
    && justCompletedSegment(v, freshIds, hundredCoinSeg.segment_id);
  const hcShowSegment = hcTargeted || hcJustCompleted;
  // Only true while it is BOTH the deliberate target AND actually running —
  // a just-completed segment has already disarmed, so this stays false there
  // and the cell glows on `active` alone (a plain success highlight).
  const hcRunning = hcTargeted && t.armedSegs.has(hundredCoinSeg.segment_id);

  async function pick(i) {
    await requestTarget(t, {
      course_id: stage.course_id, star_id: i,
      strat_tag: lastStratFor(i) || null,
    });
  }

  // Route focus (user request 2026-07-24): with a route active the selector
  // offers ONLY the stars that route collects — practising 16 Star should not
  // present the four Whomp's Fortress stars it never touches. Keys match
  // active_route.star_keys ("<course>:<star>"). No active route, or a route
  // that never visits this course, falls through to the full list rather than
  // an empty row: an empty selector reads as "broken", and standing somewhere
  // your route skips is a normal thing to do.
  const routeStars = routeStarFilter(v, stage.course_id);
  const shown = course.stars
    .map((name, i) => ({ name, i }))
    .filter(({ i }) => !routeStars || routeStars.has(`${stage.course_id}:${i}`));

  return html`<section class="practice-card selector-card stagebanner ${cardClass(fold)}">
    <div class="shead"><b>${course.name}</b>
      <span class="meta">${routeStars
        ? html`showing this route's stars · tap to practice`
        : "tap a star to practice"}</span>
      <${CollapseToggle} collapsed=${fold} toggle=${toggleFold}
        label="the course selector" /></div>
    <div class="starrow">
      ${shown.map(({ name, i }) => {
        // "100 Coins" is star_name's own special case (addresses.py) — the
        // one cell that may represent the segment instead of the plain star.
        const isHundredCoins = !!hundredCoinSeg && name === "100 Coins";
        return html`<${PracticeCell} dimIdle=${STAR_DIM_IDLE}
          key=${`${stage.course_id}:${i}`}
          active=${isHundredCoins ? hcShowSegment
            : tgt.kind !== "segment"
              && tgt.course_id === stage.course_id && tgt.star_id === i}
          armed=${isHundredCoins ? hcRunning : false}
          iconSrc=${entityIconSrc(t, starKey(stage.course_id, i))}
          fallbackSlot=${i}
          rank=${isHundredCoins ? hundredCoinSeg.rank : rankFor(i)}
          name=${name}
          sub=${isHundredCoins
            ? (hcRunning ? runningChip : stratSub(hundredCoinSeg.strat))
            : stratSub(lastStratFor(i))}
          onPick=${() => pick(i)}
          onEdit=${() => setPicking(iconIdentityForKey(starKey(stage.course_id, i)))} />`;
      })}
      ${armedExtraCells(t, v,
                        hundredCoinSeg ? new Set([hundredCoinSeg.segment_id]) : new Set(),
                        setPicking, startsInLevel(stage.level))}
    </div>
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
// Route override (user: "you can't just stop at the star grab for reds, you
// HAVE to do the pipe timing... it should always be using the Pipe timing").
// Every seeded Bowser Reds route step already names seg:reds->pipe:<abbrev>,
// never the bare star (tools/corpus_routes_*), so this makes an assumption
// the corpus already relies on VISIBLE rather than leaving a control the
// player can move that then silently does not apply: forced-but-offered
// would read as a real choice, so the star half is disabled with its own
// title instead.
function BowserCourseRow({ t, v, stage }) {
  const [fold, toggleFold] = useCollapsed("selector");
  const [setPicking, pickerModal] = useIconPicking(t);
  const course = v.catalog.courses.find((c) => c.id === stage.course_id);
  const tgt = v.target || {};
  const segs = segsForLevel(v, stage.level);
  const pipeSeg = segs.find((s) => s.is_reds_pipe);
  const noRedsSeg = segs.find((s) => !s.is_reds_pipe);

  const starActive = tgt.kind !== "segment"
    && tgt.course_id === stage.course_id && tgt.star_id === 0;
  const pipeMode = !starActive;   // default PIPE: anything but an explicit star pick
  const pipeTargeted = pipeMode && !!pipeSeg
    && tgt.kind === "segment" && tgt.segment_id === pipeSeg.segment_id;
  const redsActive = starActive || pipeTargeted;

  const routeStars = routeStarFilter(v, stage.course_id);
  const routeSegs = routeSegmentFilter(v);
  const forcedPipe = !!((routeStars && routeStars.has(`${stage.course_id}:0`))
    || (routeSegs && pipeSeg && routeSegs.has(pipeSeg.segment_id)));

  async function pickStar() {
    await requestTarget(t, { course_id: stage.course_id, star_id: 0 });
  }
  async function pickPipe() {
    if (!pipeSeg) return;
    if (!pipeSeg.enabled)
      await send("PUT", `/api/segments/${pipeSeg.segment_id}`, { enabled: true });
    await requestTarget(t, { kind: "segment", segment_id: pipeSeg.segment_id });
  }

  if (!course) return html`<${StagePlaceholder} t=${t} />`;

  const shownIds = new Set([pipeSeg, noRedsSeg].filter(Boolean)
    .map((s) => s.segment_id));

  return html`<section class="practice-card selector-card stagebanner ${cardClass(fold)}">
    <div class="shead"><b>${course.name}</b>
      <span class="meta">${forcedPipe
        ? "route active — Reds always times to the pipe"
        : "tap Star or Pipe to pin the reds run"}</span>

      <${CollapseToggle} collapsed=${fold} toggle=${toggleFold}
        label="the course selector" /></div>
    <div class="starrow segcells">
      <${RedsCell} t=${t} v=${v} stage=${stage} course=${course}
        redsActive=${redsActive} pipeMode=${pipeMode} forcedPipe=${forcedPipe}
        pipeSeg=${pipeSeg} onPickStar=${pickStar} onPickPipe=${pickPipe}
        setPicking=${setPicking} />
      ${noRedsSeg ? html`<${StandardSegmentCell}
        key=${`seg:${noRedsSeg.segment_id}`} t=${t} s=${noRedsSeg} setPicking=${setPicking} />`
        : null}
      ${armedExtraCells(t, v, shownIds, setPicking)}
    </div>
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
function RedsCell({ t, v, stage, course, redsActive, pipeMode, forcedPipe,
                   pipeSeg, onPickStar, onPickPipe, setPicking }) {
  const starRank = (v.rank_by_star || {})[`${stage.course_id}:0`];
  const pipeRank = pipeSeg ? pipeSeg.rank : null;
  const shownRank = pipeMode ? pipeRank : starRank;

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

  return html`<div class="starcell reds-cell ${redsActive ? "active-star" : ""}">
    <span class="starholder">
      <img class="starimg ${redsActive ? "" : "dim"}"
           src=${entityIconSrc(t, starKey(stage.course_id, 0))}
           onerror=${(errorEvent) => fallbackToGenericStar(errorEvent, 0)}
           alt="" draggable="false" />
      <span class="reds-toggle">
        <button type="button" class="reds-toggle-btn ${!pipeMode ? "is-selected" : ""}"
            disabled=${forcedPipe}
            aria-pressed=${!pipeMode}
            title=${forcedPipe
              ? "This route always times Reds to the pipe"
              : "Track the star grab alone"}
            onclick=${(clickEvent) => { clickEvent.stopPropagation();
              if (!forcedPipe) onPickStar(); }}>
          <img src=${genericStarSrc(2)} alt="Star" draggable="false" />
        </button>
        <span class="reds-toggle-clock" aria-hidden="true">
          <${Icon} name="clock" size=${12} />
        </span>
        <button type="button" class="reds-toggle-btn ${pipeMode ? "is-selected" : ""}"
            aria-pressed=${pipeMode}
            title="Track the run to the pipe"
            onclick=${(clickEvent) => { clickEvent.stopPropagation(); onPickPipe(); }}>
          <img src="/ui/assets/pipe_icon.png" alt="Pipe" draggable="false" />
        </button>
      </span>
      <span class="reds-arrows" aria-hidden="true">
        <span class="reds-arrow left ${!pipeMode ? "is-lit" : ""}">◀</span>
        <span class="reds-arrow right ${pipeMode ? "is-lit" : ""}">▶</span>
      </span>
    </span>
    <span class="starrank">
      ${iconTier ? html`<${RankIcon} ...${iconProps} tier=${iconTier}
          division=${iconDivision} size=${16} />` : "–"}
    </span>
    <span class="starname">Reds</span>
    <span class="starsub"><span class="strat">
      ${pipeMode ? "Reds → Pipe" : (course.stars[0] || "8 Red Coins")}
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

  // Auto-select the single fight on entry, always overriding the current target
  // (request: "immediately select it and set it as our active segment"). Keyed
  // on stage.level + the segment id so it fires once per arena entry, not every
  // render; the already-targeted guard makes a re-entry a no-op.
  useEffect(() => {
    if (!only) return;
    if (tgt.kind === "segment" && tgt.segment_id === only.segment_id) return;
    (async () => {
      if (!only.enabled)
        await send("PUT", `/api/segments/${only.segment_id}`, { enabled: true });
      await requestTarget(t, { kind: "segment", segment_id: only.segment_id });
    })();
  }, [stage.level, only && only.segment_id]);

  const extras = armedExtraCells(
    t, v, new Set(fights.map((s) => s.segment_id)), setPicking);
  if (!fights.length && !extras.length) return html`<${StagePlaceholder} t=${t} />`;

  return html`<section class="practice-card selector-card stagebanner ${cardClass(fold)}">
    <div class="shead"><b>Bowser Fight</b>
      <span class="meta">auto-selected — tap to re-arm</span>
      
      <${CollapseToggle} collapsed=${fold} toggle=${toggleFold}
        label="the course selector" /></div>
    <div class="starrow segcells">
      ${fights.map((s) => html`<${StandardSegmentCell}
        key=${`seg:${s.segment_id}`} t=${t} s=${s} setPicking=${setPicking} />`)}
      ${extras}
    </div>
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
  const segs = inRoute.length ? inRoute : here;   // never empty the row
  const extras = armedExtraCells(
    t, v, new Set(segs.map((s) => s.segment_id)), setPicking);
  if (!segs.length && !extras.length) return html`<${StagePlaceholder} t=${t} />`;

  return html`<section class="practice-card selector-card stagebanner ${cardClass(fold)}">
    <div class="shead"><b>Castle ${CASTLE_AREA_NAMES[stage.area]}</b>
      <span class="meta">tap a segment to practice</span>
      
      <${CollapseToggle} collapsed=${fold} toggle=${toggleFold}
        label="the course selector" /></div>
    <div class="starrow segcells">
      ${segs.map((s) => html`<${StandardSegmentCell}
        key=${`seg:${s.segment_id}`} t=${t} s=${s} setPicking=${setPicking} />`)}
      ${extras}
    </div>
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
    <div class="starrow segcells">
      ${armedSegments(t, v).map((s) => html`<${StandardSegmentCell}
        key=${`seg:${s.segment_id}`} t=${t} s=${s} setPicking=${setPicking} />`)}
    </div>
    ${pickerModal}
  </section>`;
}
