// src/sm64_events/ui/components/stagebanner.js
// Quick-select star row. When the player loads into one of the 15 main courses
// (t.stage.in_stage, set from the broadcast-only stage_changed event), surface
// that course's stars as one-click target buttons. 100% data-driven from the
// session view the store already holds: catalog.courses for names,
// last_strat_by_star for the subtext, target for the active highlight. One
// click POSTs /api/target carrying the star's last strategy -- the same
// endpoint the header TargetEditor uses, so the normal target_changed flow
// updates the header, the pinned active-star section, and this banner.
import { h } from "preact";
import htm from "htm";
import { send } from "../api.js";

const html = htm.bind(h);

export function StageBanner({ t }) {
  const v = t.view;
  const stage = t.stage;
  if (!v || !stage || !stage.in_stage) return null;

  const course = v.catalog.courses.find((c) => c.id === stage.course_id);
  if (!course) return null;

  const tgt = v.target || {};
  const lastStratFor = (i) =>
    v.last_strat_by_star[`${stage.course_id}:${i}`] || "";

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
        return html`<button key=${`${stage.course_id}:${i}`}
                            class="stagebtn ${active ? "active-star" : ""}"
                            onclick=${() => pick(i)}>
          <span class="stagebtn-name">${name}</span>
          <span class="stagebtn-sub meta">${strat || "—"}</span>
        </button>`;
      })}
    </div>
  </div>`;
}
