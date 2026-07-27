// src/sm64_events/ui/components/stagebanner.js
// Quick-select row, driven by t.stage (the broadcast-only stage_changed event)
// and dispatched on its `mode`:
//   "stars"         : a main course 1-15 -> that course's stars (name +
//                     last-strategy subtext); click sets the star target.
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
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { send } from "../api.js";
import { Icon } from "./icons.js";
import { PracticeCell } from "./practicecell.js";
import { IconPicker } from "./iconpicker.js";
import { COURSE_ICON_PREFIXES, LEVEL_ICONS, genericStarSrc,
         resolveIcon } from "./entityicons.js";

const html = htm.bind(h);

const CASTLE_AREA_NAMES = { 1: "Lobby", 2: "Upstairs", 3: "Basement" };

export function StageBanner({ t }) {
  const v = t.view;
  const stage = t.stage;
  if (!v) return html`<${StagePlaceholder} t=${t} />`;
  let row = null;
  switch (stage && stage.mode) {
    case "stars":         row = html`<${StarRow} t=${t} v=${v} stage=${stage} />`; break;
    case "bowser_course": row = html`<${BowserCourseRow} t=${t} v=${v} stage=${stage} />`; break;
    case "arena":         row = html`<${ArenaRow} t=${t} v=${v} stage=${stage} />`; break;
    case "castle":        row = html`<${SegmentRow} t=${t} v=${v} stage=${stage} />`; break;
  }
  // No banner for this place (hub, unknown stage) but a timer is live:
  // show the running segments instead of the empty placeholder.
  if (!row && armedSegments(t, v).length)
    row = html`<${ArmedOnlyRow} t=${t} v=${v} />`;
  return row || html`<${StagePlaceholder} t=${t} />`;
}

function StagePlaceholder({ t }) {
  return html`<section class="practice-card selector-card stagebanner selector-empty">
    <div class="selector-empty-symbol" aria-hidden="true">☆</div>
    <div><b>No course target available</b>
      <span class="meta">Move into a course, or pick one from the active
        target card below.</span></div>
    <${PendingChip} t=${t} />
  </section>`;
}

// The target the player has picked but not reached yet — a server-held INTENT
// rather than a target that has moved (tracking/pending_target.py). It rides
// the stage header because the banner already answers "where am I, and what
// can I practice here"; an intent is that same question one step ahead. It
// renders nothing at all when nothing is held, so no stage header ever
// changes height for it. Every mode's header gets one, including the
// placeholder — an intent must not become invisible in exactly the place
// (a hub, a cap stage) you are most likely to be while walking toward it.
function PendingChip({ t }) {
  const held = t && t.view && t.view.pending_target;
  if (!held) return null;
  // The ENTITY's own name, never "course · star": the destination is already
  // the next span, and printing it twice spent the truncation budget on the
  // repeat instead of on the thing being waited for (render, 2026-07-26 —
  // "Shifting Sand Land · Shi…" beside "on reaching Shifting Sand Land").
  const name = held.kind === "segment" ? held.segment_name : held.star_name;
  const where = held.where;   // null for a segment — see pending_target_payload
  async function cancel(event) {
    event.stopPropagation();
    await send("DELETE", "/api/target/pending");
    t.refresh();
  }
  return html`<span class="pending-target"
      title=${where
        ? `Waiting for you to enter ${where}. Enter a different course `
          + "instead and this is dropped."
        : "Waiting for you to reach where this segment starts. Enter a "
          + "different course instead and this is dropped."}>
    <${Icon} name="target" size=${14} />
    <b>Next</b><span class="pending-target-name">${name}</span>
    ${where && html`<span class="meta">in ${where}</span>`}
    <button type="button" class="pending-clear" onclick=${cancel}
        aria-label=${`Cancel practising ${name}`}>×</button>
  </span>`;
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

const armedSegments = (t, v) =>
  (v.segment_targets || []).filter((s) => t.armedSegs.has(s.segment_id));

// Look flags — flip during the human-audit playtest to taste. Kept as
// constants (not props) so the cell below stays a single readable line.
const STAR_DIM_IDLE = true;  // false = every star equally bright

// COURSE_ICON_PREFIXES, LEVEL_ICONS, resolveIcon, isGenericArt and
// fallbackToGenericStar live in entityicons.js (task D,
// 2026-07-25-marelo-legibility) — the Rank tab's Top-N strip needed the SAME
// table this row already had. The PURE data behind them (the prefix list, the
// level map, the substitutes) sits one layer further down in ../entities.js,
// which imports nothing and can therefore be node-tested; entityicons.js
// re-exports it so a component never has to know which of the two it wants.
//
// The cell itself is components/practicecell.js, shared with the entity
// picker's grid (2026-07-25) so a star looks the same where you pick it and
// where you practice it.

const segCourseStem = (s) =>
  (s.start_levels || []).map((lvl) => LEVEL_ICONS[lvl]).find(Boolean) || null;
const segIconSrc = (t, s) =>
  resolveIcon(t, `segment:${s.segment_id}`, segCourseStem(s), 0);

// Row-level icon-picking state: the ✎ on any cell opens ONE picker per row,
// hoisted OUT of the cells so clicks inside the modal can never bubble into
// a cell's target-setting onclick. identity = the /api/icon body + its ek.
function useIconPicking(t) {
  const [picking, setPicking] = useState(null);
  const modal = picking && html`<${IconPicker} identity=${picking}
      current=${(((t.view || {}).icon_overrides) || {})[picking.ek] || null}
      onDone=${() => { setPicking(null); t.refresh(); }} />`;
  return [setPicking, modal];
}

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
    await send("POST", "/api/target", { kind: "segment", segment_id: s.segment_id });
    t.refresh();
  }
  return html`<${PracticeCell} dimIdle=${STAR_DIM_IDLE}
    active=${tgt.kind === "segment" && tgt.segment_id === s.segment_id}
    armed=${armed}
    iconSrc=${segIconSrc(t, s)}
    rank=${s.rank} name=${s.name}
    sub=${armed ? runningChip : stratSub(s.strat)}
    onPick=${pick}
    onEdit=${() => setPicking({ kind: "segment", segment_id: s.segment_id,
                                ek: `segment:${s.segment_id}` })} />`;
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

function StarRow({ t, v, stage }) {
  // hooks first — the early return below must never change the hook count
  const [setPicking, pickerModal] = useIconPicking(t);
  const course = v.catalog.courses.find((c) => c.id === stage.course_id);
  if (!course) return html`<${StagePlaceholder} t=${t} />`;

  const tgt = v.target || {};
  const prefix = COURSE_ICON_PREFIXES[stage.course_id - 1] || null;
  const lastStratFor = (i) =>
    v.last_strat_by_star[`${stage.course_id}:${i}`] || "";
  // Rank under that star's ACTIVE strat (server-graded). Changing the strat
  // refreshes the view and swaps the medal automatically — see views.py.
  const rankFor = (i) =>
    (v.rank_by_star || {})[`${stage.course_id}:${i}`];

  async function pick(i) {
    await send("POST", "/api/target", {
      course_id: stage.course_id, star_id: i,
      strat_tag: lastStratFor(i) || null,
    });
    t.refresh();
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

  return html`<section class="practice-card selector-card stagebanner">
    <div class="shead"><b>▸ ${course.name}</b>
      <span class="meta">${routeStars
        ? html`showing this route's stars · tap to practice`
        : "tap a star to practice"}</span><${PendingChip} t=${t} /></div>
    <div class="starrow">
      ${shown.map(({ name, i }) => html`<${PracticeCell} dimIdle=${STAR_DIM_IDLE}
        key=${`${stage.course_id}:${i}`}
        active=${tgt.kind !== "segment"
          && tgt.course_id === stage.course_id && tgt.star_id === i}
        iconSrc=${resolveIcon(t, `star:${stage.course_id}:${i}`,
                              prefix ? `${prefix}${i + 1}` : null, i)}
        fallbackSlot=${i} rank=${rankFor(i)} name=${name}
        sub=${stratSub(lastStratFor(i))}
        onPick=${() => pick(i)}
        onEdit=${() => setPicking({ course_id: stage.course_id, star_id: i,
                                    ek: `star:${stage.course_id}:${i}` })} />`)}
      ${armedExtraCells(t, v, new Set(), setPicking,
                        startsInLevel(stage.level))}
    </div>
    ${pickerModal}
  </section>`;
}

// BitDW/BitFS/BitS: the "reds" 8-coin star + the level's "no reds" pipe-entry
// segment(s). Picking flips the pipe segment's enabled flag (mutual exclusion).
function BowserCourseRow({ t, v, stage }) {
  // hooks first (useIconPicking + the restore useEffect below) — the early
  // return must never change the hook count between renders
  const [setPicking, pickerModal] = useIconPicking(t);
  const course = v.catalog.courses.find((c) => c.id === stage.course_id);
  const tgt = v.target || {};
  const pipes = segsForLevel(v, stage.level);
  const redsActive = tgt.kind !== "segment"
    && tgt.course_id === stage.course_id && tgt.star_id === 0;

  // "reds" — practice the 8-coin star: disable any pipe-entry segment so it
  // stops tracking, then target the star.
  async function pickReds() {
    for (const s of pipes)
      if (s.enabled)
        await send("PUT", `/api/segments/${s.segment_id}`, { enabled: false });
    await send("POST", "/api/target", { course_id: stage.course_id, star_id: 0 });
    t.refresh();
  }

  // "no reds" — practice the pipe-entry skip: enable that segment so it tracks,
  // then target it.
  async function pickNoReds(s) {
    if (!s.enabled)
      await send("PUT", `/api/segments/${s.segment_id}`, { enabled: true });
    await send("POST", "/api/target", { kind: "segment", segment_id: s.segment_id });
    t.refresh();
  }

  // Restore the LEVEL'S last selection on entry — walking into BitDW while you
  // were practicing reds there means you are practicing reds, so don't make the
  // player re-pick (request 2026-07-23).
  //
  // The memory is DERIVED, not stored: the mutual exclusion above already
  // records the pick in the pipe segment's `enabled` flag (reds disables it,
  // no reds enables it), and that lives in the db per definition — so the
  // memory is automatically per level, survives restarts, and can never
  // disagree with what the segment is actually tracking. Any pipe enabled ->
  // "no reds"; none (or none defined) -> "reds".
  //
  // Same shape as ArenaRow: it re-applies the SAME functions the buttons call
  // (one behavior, one implementation), no-ops when that choice is already the
  // target, and is keyed on the level so a manual pick mid-level sticks until
  // you leave and come back. The enabled-pipe key makes it converge when the
  // flag is flipped elsewhere (the Segments tab).
  const enabledPipe = pipes.find((s) => s.enabled) || null;
  useEffect(() => {
    if (enabledPipe) {
      if (!(tgt.kind === "segment" && tgt.segment_id === enabledPipe.segment_id))
        pickNoReds(enabledPipe);
    } else if (!redsActive) {
      pickReds();
    }
  }, [stage.level, enabledPipe && enabledPipe.segment_id]);

  if (!course) return html`<${StagePlaceholder} t=${t} />`;

  return html`<section class="practice-card selector-card stagebanner">
    <div class="shead"><b>▸ ${course.name}</b>
      <span class="meta">reds (8-coin star) · or the pipe-entry skip (no reds)</span>
      <${PendingChip} t=${t} /></div>
    <div class="starrow segcells">
      <${PracticeCell} dimIdle=${STAR_DIM_IDLE}
        active=${redsActive}
        iconSrc=${resolveIcon(t, `star:${stage.course_id}:0`, null, 0)}
        rank=${(v.rank_by_star || {})[`${stage.course_id}:0`]}
        name="Reds" title=${course.stars[0] || "8 Red Coins"}
        sub=${html`<span class="strat">${course.stars[0] || "8 Red Coins"}</span>`}
        onPick=${pickReds}
        onEdit=${() => setPicking({ course_id: stage.course_id, star_id: 0,
                                    ek: `star:${stage.course_id}:0` })} />
      ${pipes.map((s) => html`<${PracticeCell} dimIdle=${STAR_DIM_IDLE} key=${`seg:${s.segment_id}`}
        active=${tgt.kind === "segment" && tgt.segment_id === s.segment_id}
        armed=${t.armedSegs.has(s.segment_id)}
        iconSrc=${segIconSrc(t, s)}
        rank=${s.rank} name="No reds" title=${s.name}
        sub=${t.armedSegs.has(s.segment_id) ? runningChip
          : html`<span class="strat">${s.name}</span>`}
        onPick=${() => pickNoReds(s)}
        onEdit=${() => setPicking({ kind: "segment", segment_id: s.segment_id,
                                    ek: `segment:${s.segment_id}` })} />`)}
      ${armedExtraCells(t, v, new Set(pipes.map((s) => s.segment_id)),
                        setPicking)}
    </div>
    ${pickerModal}
  </section>`;
}

// Bowser 1/2/3 arena: the single fight segment, auto-selected on entry.
function ArenaRow({ t, v, stage }) {
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
      await send("POST", "/api/target", { kind: "segment", segment_id: only.segment_id });
      t.refresh();
    })();
  }, [stage.level, only && only.segment_id]);

  const extras = armedExtraCells(
    t, v, new Set(fights.map((s) => s.segment_id)), setPicking);
  if (!fights.length && !extras.length) return html`<${StagePlaceholder} t=${t} />`;

  return html`<section class="practice-card selector-card stagebanner">
    <div class="shead"><b>▸ Bowser Fight</b>
      <span class="meta">auto-selected — tap to re-arm</span>
      <${PendingChip} t=${t} /></div>
    <div class="starrow segcells">
      ${fights.map((s) => html`<${StandardSegmentCell}
        key=${`seg:${s.segment_id}`} t=${t} s=${s} setPicking=${setPicking} />`)}
      ${extras}
    </div>
    ${pickerModal}
  </section>`;
}

function SegmentRow({ t, v, stage }) {
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

  return html`<section class="practice-card selector-card stagebanner">
    <div class="shead"><b>▸ Castle ${CASTLE_AREA_NAMES[stage.area]}</b>
      <span class="meta">tap a segment to practice</span>
      <${PendingChip} t=${t} /></div>
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
  const [setPicking, pickerModal] = useIconPicking(t);
  return html`<section class="practice-card selector-card stagebanner">
    <div class="shead"><b>▸ Running</b>
      <span class="meta">a segment timer is live</span>
      <${PendingChip} t=${t} /></div>
    <div class="starrow segcells">
      ${armedSegments(t, v).map((s) => html`<${StandardSegmentCell}
        key=${`seg:${s.segment_id}`} t=${t} s=${s} setPicking=${setPicking} />`)}
    </div>
    ${pickerModal}
  </section>`;
}
