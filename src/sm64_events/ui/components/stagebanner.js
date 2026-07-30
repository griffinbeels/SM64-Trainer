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
import { armedSegments, hasPracticeContext, practiceMode } from "../stagecontext.js";
import { requestTarget } from "../target.js";
import { Icon } from "./icons.js";
import { PracticeCell } from "./practicecell.js";
import { iconIdentityForKey, useIconPicking } from "./iconpicker.js";
import { entityIconSrc, genericStarSrc } from "./entityicons.js";

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

// "Just completed" reuses practice.js's freshIds — the SAME attempt-id
// recency Set the attempt-table row blink already reads (useFreshAttemptIds)
// — rather than inventing a second notion of "recent" for this cell. True
// only when the segment's own most-recent attempt (by id — a section's
// attempts are not guaranteed newest-first) landed as a FRESH success.
const justCompletedSegment = (v, freshIds, segmentId) => {
  if (!freshIds || !freshIds.size) return false;
  const sec = (v.segments || []).find((s) => s.segment_id === segmentId);
  if (!sec || !sec.attempts.length) return false;
  const latest = sec.attempts.reduce((a, b) => (a.id > b.id ? a : b));
  return latest.outcome === "success" && freshIds.has(latest.id);
};

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

// BitDW/BitFS/BitS: THREE independently-tracked things — the "reds" 8-coin
// star, grabbing reds then taking the pipe, and taking the pipe without reds.
// No mutual exclusion (retired 2026-07-29, live report): the corpus reshape
// that arms the pipe-entry segments on stage ENTRY gives each Bowser level
// TWO segment_targets entries sharing the same start_levels — one STRICT with
// a waypoint on the reds grab ("seg:reds->pipe:*"), one EXCLUSIVE and
// cancelled the moment any star is grabbed ("seg:<abbrev>-pipe", the legacy
// row) — so the matcher itself already keeps whichever of the two applies
// armed in parallel with the reds star, with no picker involved: "if 1 is
// armed, 2 should always be armed. If 2 is armed, then 1 should also always
// be armed" (user). This row used to ENFORCE a choice by writing the pipe
// segment's `enabled` flag — the SAME toggle now fights the matcher's own
// bookkeeping, so it is gone; clicking a cell only sets the target, same as
// every other segment cell. StandardSegmentCell is reused here for BOTH pipe
// cells, so each shows its OWN honest name/rank/strat instead of a shared
// "No reds" label. There is no server field distinguishing "the reds->pipe
// segment" from "the pipe-only segment" (segment_targets carries no
// waypoints/match_mode), so the two rely on the corpus's own names already
// being distinct ("<Abbrev> — 8 Red Coins → Pipe" vs "<Abbrev> Pipe Entry") —
// flagged in this task's report, since a future rename could make them read
// alike again with no guard here to catch it.
function BowserCourseRow({ t, v, stage }) {
  const [fold, toggleFold] = useCollapsed("selector");
  const [setPicking, pickerModal] = useIconPicking(t);
  const course = v.catalog.courses.find((c) => c.id === stage.course_id);
  const tgt = v.target || {};
  const pipes = segsForLevel(v, stage.level);
  const redsActive = tgt.kind !== "segment"
    && tgt.course_id === stage.course_id && tgt.star_id === 0;

  // "reds" — practice the 8-coin star. No longer disables anything: the
  // matcher's own EXCLUSIVE mode already cancels the pipe-only segment the
  // moment a star is grabbed, and the reds->pipe segment WANTS the reds grab
  // (it is that segment's own waypoint).
  async function pickReds() {
    await requestTarget(t, { course_id: stage.course_id, star_id: 0 });
  }

  if (!course) return html`<${StagePlaceholder} t=${t} />`;

  return html`<section class="practice-card selector-card stagebanner ${cardClass(fold)}">
    <div class="shead"><b>${course.name}</b>
      <span class="meta">all three track together — tap one to pin it</span>

      <${CollapseToggle} collapsed=${fold} toggle=${toggleFold}
        label="the course selector" /></div>
    <div class="starrow segcells">
      <${PracticeCell} dimIdle=${STAR_DIM_IDLE}
        active=${redsActive}
        iconSrc=${entityIconSrc(t, starKey(stage.course_id, 0))}
        rank=${(v.rank_by_star || {})[`${stage.course_id}:0`]}
        name="Reds" title=${course.stars[0] || "8 Red Coins"}
        sub=${html`<span class="strat">${course.stars[0] || "8 Red Coins"}</span>`}
        onPick=${pickReds}
        onEdit=${() => setPicking(iconIdentityForKey(starKey(stage.course_id, 0)))} />
      ${pipes.map((s) => html`<${StandardSegmentCell}
        key=${`seg:${s.segment_id}`} t=${t} s=${s} setPicking=${setPicking} />`)}
      ${armedExtraCells(t, v, new Set(pipes.map((s) => s.segment_id)),
                        setPicking)}
    </div>
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
