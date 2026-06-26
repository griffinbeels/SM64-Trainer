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
  if (!v || !stage) return null;
  switch (stage.mode) {
    case "stars":         return html`<${StarRow} t=${t} v=${v} stage=${stage} />`;
    case "bowser_course": return html`<${BowserCourseRow} t=${t} v=${v} stage=${stage} />`;
    case "arena":         return html`<${ArenaRow} t=${t} v=${v} stage=${stage} />`;
    case "castle":        return html`<${SegmentRow} t=${t} v=${v} stage=${stage} />`;
    default:              return null;
  }
}

// segments offered for the current whole level (Bowser banners) — the pipe-entry
// segments (course levels) or fight segments (arenas). Disabled ones are kept;
// the Bowser banner shows them so its "no reds" click can enable them.
const segsForLevel = (v, level) =>
  (v.segment_targets || []).filter((s) => (s.start_levels || []).includes(level));

function StarRow({ t, v, stage }) {
  const course = v.catalog.courses.find((c) => c.id === stage.course_id);
  if (!course) return null;

  const tgt = v.target || {};
  const lastStratFor = (i) =>
    v.last_strat_by_star[`${stage.course_id}:${i}`] || "";
  // Rank under that star's ACTIVE strat (server-graded, parallel to
  // last_strat_by_star). Changing the strat refreshes the view and swaps the
  // medal here automatically — see tracking/views.py rank_by_star.
  const rankFor = (i) =>
    (v.rank_by_star || {})[`${stage.course_id}:${i}`];

  async function pick(i) {
    await send("POST", "/api/target", {
      course_id: stage.course_id, star_id: i,
      strat_tag: lastStratFor(i) || null,
    });
    t.refresh();
  }

  return html`<div class="starsec stagebanner">
    <div class="shead"><b>▸ ${course.name}</b>
      <span class="meta">tap a star to practice</span></div>
    <div class="stagebanner-row">
      ${course.stars.map((name, i) => {
        const active = tgt.kind !== "segment"
          && tgt.course_id === stage.course_id && tgt.star_id === i;
        const strat = lastStratFor(i);
        const rank = rankFor(i);
        return html`<button key=${`${stage.course_id}:${i}`}
                            class="stagebtn ${active ? "active-star" : ""}"
                            onclick=${() => pick(i)}>
          <span class="stagebtn-name">${name}</span>
          <span class="stagebtn-sub meta">
            ${rank ? html`<${Medal} rank=${rank} size=${13} />` : ""}
            <span>${strat || "—"}</span>
          </span>
        </button>`;
      })}
    </div>
  </div>`;
}

// BitDW/BitFS/BitS: the "reds" 8-coin star + the level's "no reds" pipe-entry
// segment(s). Picking flips the pipe segment's enabled flag (mutual exclusion).
function BowserCourseRow({ t, v, stage }) {
  const course = v.catalog.courses.find((c) => c.id === stage.course_id);
  if (!course) return null;
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

  return html`<div class="starsec stagebanner">
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
        return html`<button key=${`seg:${s.segment_id}`}
                            class="stagebtn ${active ? "active-star" : ""}"
                            onclick=${() => pickNoReds(s)}>
          <span class="stagebtn-name">No reds</span>
          <span class="stagebtn-sub meta">${s.name}</span>
        </button>`;
      })}
    </div>
  </div>`;
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

  if (!fights.length) return null;   // no fight segment defined -> nothing to offer

  async function pick(s) {
    if (!s.enabled)
      await send("PUT", `/api/segments/${s.segment_id}`, { enabled: true });
    await send("POST", "/api/target", { kind: "segment", segment_id: s.segment_id });
    t.refresh();
  }

  return html`<div class="starsec stagebanner">
    <div class="shead"><b>▸ Bowser Fight</b>
      <span class="meta">auto-selected — tap to re-arm</span></div>
    <div class="stagebanner-row">
      ${fights.map((s) => {
        const active = tgt.kind === "segment" && tgt.segment_id === s.segment_id;
        return html`<button key=${`seg:${s.segment_id}`}
                            class="stagebtn ${active ? "active-star" : ""}"
                            onclick=${() => pick(s)}>
          <span class="stagebtn-name">${s.name}</span>
        </button>`;
      })}
    </div>
  </div>`;
}

function SegmentRow({ t, v, stage }) {
  const tgt = v.target || {};
  const segs = (v.segment_targets || []).filter((s) =>
    s.enabled &&
    s.start_areas.some((a) => a[0] === stage.level && a[1] === stage.area));
  if (!segs.length) return null;   // no segments start here -> nothing to offer

  async function pick(segId) {
    await send("POST", "/api/target", { kind: "segment", segment_id: segId });
    t.refresh();
  }

  return html`<div class="starsec stagebanner">
    <div class="shead"><b>▸ Castle ${CASTLE_AREA_NAMES[stage.area]}</b>
      <span class="meta">tap a segment to practice</span></div>
    <div class="stagebanner-row">
      ${segs.map((s) => {
        const active = tgt.kind === "segment" && tgt.segment_id === s.segment_id;
        return html`<button key=${`seg:${s.segment_id}`}
                            class="stagebtn ${active ? "active-star" : ""}"
                            onclick=${() => pick(s.segment_id)}>
          <span class="stagebtn-name">${s.name}</span>
        </button>`;
      })}
    </div>
  </div>`;
}
