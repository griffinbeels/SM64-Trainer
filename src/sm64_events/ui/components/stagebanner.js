// src/sm64_events/ui/components/stagebanner.js
// Quick-select row, dual-mode — driven by t.stage (the broadcast-only
// stage_changed event):
//   STARS    : in one of the 15 main courses (stage.in_stage) -> that course's
//              stars (name + last-strategy subtext); click sets the star target.
//   SEGMENTS : in a Castle Inside subarea (level 6, area lobby/upstairs/
//              basement) -> the segments whose start triggers begin in that
//              subarea (v.segment_targets, derived server-side from the
//              definitions); click sets the segment target. Only subarea-scoped
//              segments appear, so e.g. LBLJ (lobby) never shows upstairs.
// Both paths POST /api/target -- the same endpoint the header uses -- so the
// normal target_changed flow updates the header, the pinned section, and this.
import { h } from "preact";
import htm from "htm";
import { send } from "../api.js";
import { Medal } from "./ranks.js";

const html = htm.bind(h);

const CASTLE_INSIDE = 6;
const CASTLE_AREA_NAMES = { 1: "Lobby", 2: "Upstairs", 3: "Basement" };

export function StageBanner({ t }) {
  const v = t.view;
  const stage = t.stage;
  if (!v || !stage) return null;
  if (stage.in_stage)   // main course -> stars (course_id is set whenever in_stage)
    return html`<${StarRow} t=${t} v=${v} stage=${stage} />`;
  if (stage.level === CASTLE_INSIDE && CASTLE_AREA_NAMES[stage.area])
    return html`<${SegmentRow} t=${t} v=${v} stage=${stage} />`;
  return null;
}

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

function SegmentRow({ t, v, stage }) {
  const tgt = v.target || {};
  const segs = (v.segment_targets || []).filter((s) =>
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
