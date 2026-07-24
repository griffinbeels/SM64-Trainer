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
// Selection POSTs /api/target (and PUTs /api/segments/{id} for the Bowser
// enable/disable) -- the same endpoints the rest of the UI uses, so the normal
// target_changed flow updates the header, the pinned section, and this.
import { h } from "preact";
import { useEffect } from "preact/hooks";
import htm from "htm";
import { send } from "../api.js";
import { Medal } from "./ranks.js";

const html = htm.bind(h);

const CASTLE_AREA_NAMES = { 1: "Lobby", 2: "Upstairs", 3: "Basement" };

export function StageBanner({ t }) {
  const v = t.view;
  const stage = t.stage;
  if (!v || !stage) return html`<${StagePlaceholder} />`;
  let row = null;
  switch (stage.mode) {
    case "stars":         row = html`<${StarRow} t=${t} v=${v} stage=${stage} />`; break;
    case "bowser_course": row = html`<${BowserCourseRow} t=${t} v=${v} stage=${stage} />`; break;
    case "arena":         row = html`<${ArenaRow} t=${t} v=${v} stage=${stage} />`; break;
    case "castle":        row = html`<${SegmentRow} t=${t} v=${v} stage=${stage} />`; break;
  }
  return row || html`<${StagePlaceholder} />`;
}

function StagePlaceholder() {
  return html`<section class="practice-card selector-card stagebanner selector-empty">
    <div class="selector-empty-symbol" aria-hidden="true">☆</div>
    <div><b>No course target available</b>
      <span class="meta">Move into a course or choose a target above.</span></div>
  </section>`;
}

// segments offered for the current whole level (Bowser banners) — the pipe-entry
// segments (course levels) or fight segments (arenas). Disabled ones are kept;
// the Bowser banner shows them so its "no reds" click can enable them.
const segsForLevel = (v, level) =>
  (v.segment_targets || []).filter((s) => (s.start_levels || []).includes(level));

// StarRow look flags — flip during the human-audit playtest to taste. Kept as
// constants (not props) so the call site below stays a single readable line.
// Each star slot shows PNG art `ui/assets/star_{n}.png`; the slot index is
// clamped to STAR_IMG_COUNT, so the 100-coin/7th slot reuses star_6.
const STAR_IMG_COUNT = 6;    // star_1.png .. star_6.png in ui/assets/
const STAR_DIM_IDLE = true;  // false = every star equally bright

// Course split-icon art (t.starIcons === "course", the settings-drawer
// "Star icons" preference): ui/assets/star_icons/{prefix}{slot+1}.png, one
// per main-course star INCLUDING the 100-coin 7th slot. Index = course_id-1
// (catalog order, pinned against the assets by tests/test_star_icons.py).
const COURSE_ICON_PREFIXES = ["bob", "wf", "jrb", "ccm", "bbh", "hmc", "lll",
                              "ssl", "ddd", "sl", "wdw", "ttm", "thi", "ttc",
                              "rr"];

const genericStarSrc = (slot) =>
  `/ui/assets/star_${Math.min(slot + 1, STAR_IMG_COUNT)}.png`;

// A load failure (missing/corrupt icon) degrades to the generic star art;
// dropping `courseicon` also removes the opaque-square styling.
function fallbackToGenericStar(event, slot) {
  const img = event.target;
  if (img.src.includes("/star_icons/")) {
    img.classList.remove("courseicon");
    img.src = genericStarSrc(slot);
  }
}

function StarRow({ t, v, stage }) {
  const course = v.catalog.courses.find((c) => c.id === stage.course_id);
  if (!course) return html`<${StagePlaceholder} />`;

  const tgt = v.target || {};
  const iconPrefix = t.starIcons === "course"
    ? COURSE_ICON_PREFIXES[stage.course_id - 1] : null;
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

  return html`<section class="practice-card selector-card stagebanner">
    <div class="shead"><b>▸ ${course.name}</b>
      <span class="meta">tap a star to practice</span></div>
    <div class="starrow">
      ${course.stars.map((name, i) => {
        const active = tgt.kind !== "segment"
          && tgt.course_id === stage.course_id && tgt.star_id === i;
        const strat = lastStratFor(i);
        const rank = rankFor(i);
        return html`<button key=${`${stage.course_id}:${i}`}
                            class="starcell ${active ? "active-star" : ""}"
                            title=${name} onclick=${() => pick(i)}>
          <span class="starholder">
            <img class="starimg ${iconPrefix ? "courseicon" : ""} ${STAR_DIM_IDLE && !active ? "dim" : ""}"
                 src=${iconPrefix
                   ? `/ui/assets/star_icons/${iconPrefix}${i + 1}.png`
                   : genericStarSrc(i)}
                 onerror=${(e) => fallbackToGenericStar(e, i)}
                 alt="" draggable="false" />
          </span>
          <span class="starrank">
            ${rank ? html`<${Medal} rank=${rank} size=${16} />` : "–"}</span>
          <span class="starname">${name}</span>
          <span class="starsub">
            <span class="strat ${strat ? "" : "none"}">${strat || "—"}</span>
          </span>
        </button>`;
      })}
    </div>
  </section>`;
}

// BitDW/BitFS/BitS: the "reds" 8-coin star + the level's "no reds" pipe-entry
// segment(s). Picking flips the pipe segment's enabled flag (mutual exclusion).
function BowserCourseRow({ t, v, stage }) {
  const course = v.catalog.courses.find((c) => c.id === stage.course_id);
  if (!course) return html`<${StagePlaceholder} />`;
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

  return html`<section class="practice-card selector-card stagebanner">
    <div class="shead"><b>▸ ${course.name}</b>
      <span class="meta">reds (8-coin star) · or the pipe-entry skip (no reds)</span></div>
    <div class="stagebanner-row">
      <button class="stagebtn ${redsActive ? "active-star" : ""}"
              onclick=${pickReds}>
        <span class="stagebtn-name">Reds</span>
        <span class="stagebtn-sub meta">${course.stars[0] || "8 Red Coins"}</span>
      </button>
      ${pipes.map((s) => {
        const active = tgt.kind === "segment" && tgt.segment_id === s.segment_id;
        const armed = t.armedSegs.has(s.segment_id);
        return html`<button key=${`seg:${s.segment_id}`}
                            class="stagebtn ${active ? "active-star" : ""} ${armed ? "armed" : ""}"
                            onclick=${() => pickNoReds(s)}>
          <span class="stagebtn-name">No reds</span>
          <span class="stagebtn-sub meta">${s.name}</span>
          ${armed && html`<span class="stagebtn-sub chip good">⏱ running</span>`}
        </button>`;
      })}
    </div>
  </section>`;
}

// Bowser 1/2/3 arena: the single fight segment, auto-selected on entry.
function ArenaRow({ t, v, stage }) {
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

  if (!fights.length) return html`<${StagePlaceholder} />`;

  async function pick(s) {
    if (!s.enabled)
      await send("PUT", `/api/segments/${s.segment_id}`, { enabled: true });
    await send("POST", "/api/target", { kind: "segment", segment_id: s.segment_id });
    t.refresh();
  }

  return html`<section class="practice-card selector-card stagebanner">
    <div class="shead"><b>▸ Bowser Fight</b>
      <span class="meta">auto-selected — tap to re-arm</span></div>
    <div class="stagebanner-row">
      ${fights.map((s) => {
        const active = tgt.kind === "segment" && tgt.segment_id === s.segment_id;
        const armed = t.armedSegs.has(s.segment_id);
        return html`<button key=${`seg:${s.segment_id}`}
                            class="stagebtn ${active ? "active-star" : ""} ${armed ? "armed" : ""}"
                            onclick=${() => pick(s)}>
          <span class="stagebtn-name">${s.name}</span>
          ${armed && html`<span class="stagebtn-sub chip good">⏱ running</span>`}
        </button>`;
      })}
    </div>
  </section>`;
}

function SegmentRow({ t, v, stage }) {
  const tgt = v.target || {};
  const segs = (v.segment_targets || []).filter((s) =>
    s.enabled &&
    s.start_areas.some((a) => a[0] === stage.level && a[1] === stage.area));
  if (!segs.length) return html`<${StagePlaceholder} />`;

  async function pick(segId) {
    await send("POST", "/api/target", { kind: "segment", segment_id: segId });
    t.refresh();
  }

  return html`<section class="practice-card selector-card stagebanner">
    <div class="shead"><b>▸ Castle ${CASTLE_AREA_NAMES[stage.area]}</b>
      <span class="meta">tap a segment to practice</span></div>
    <div class="stagebanner-row">
      ${segs.map((s) => {
        const active = tgt.kind === "segment" && tgt.segment_id === s.segment_id;
        const armed = t.armedSegs.has(s.segment_id);
        return html`<button key=${`seg:${s.segment_id}`}
                            class="stagebtn ${active ? "active-star" : ""} ${armed ? "armed" : ""}"
                            onclick=${() => pick(s.segment_id)}>
          <span class="stagebtn-name">${s.name}</span>
          ${armed && html`<span class="stagebtn-sub chip good">⏱ running</span>`}
        </button>`;
      })}
    </div>
  </section>`;
}
