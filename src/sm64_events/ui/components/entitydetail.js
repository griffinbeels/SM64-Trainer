// src/sm64_events/ui/components/entitydetail.js
//
// The analysis card and the detail drawer, for ONE entity, whichever kind.
//
// They lived inside StarSection and SegmentSection as two hand-written
// copies. Since 2026-08-03 they are PAGE-LEVEL surfaces that follow whichever
// entity the practice log has in focus, so a second copy would not merely be
// duplication -- there would be no single thing for the page to re-point.
// Both components take `sec` as an arbitrary section handed in as a prop,
// including `null` -- EntityAnalysis draws the "nothing selected" empty
// state then, and EntityDrawer renders nothing at all (there is no wipe
// button, no standards ladder and no failure compilation for an entity
// nobody has picked).
import { h } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { displayName, entityKey, entityNoun, isSegment,
        sectionClock } from "../entitysection.js";
import { Timeline } from "./timeline.js";
import { Progress, hasProgressPoints } from "./progress.js";
import { StandardsPanel } from "./standards.js";
import { FailureCompilation } from "./failcomp.js";
import { EmptyState } from "./emptystate.js";
import { CollapseToggle, cardClass, useCollapsed } from "./collapsible.js";
import { DUST_STAT_KEYS } from "./statmenu.js";

const html = htm.bind(h);

// Validity-bounds chip (spec 2026-07-23): the section's effective min/max
// completion time — successes outside the range are auto-ignored server-side
// (auto-cleared into the hidden bucket; stats/PBs/graphs/runs skip them).
// Dimmed while on the implicit 0.5s default. Edited in SECONDS, stored as
// frames (x30). Stars persist via PUT/DELETE /api/stars/{c}/{s}/time-filter;
// segments rewrite their def's min_time/max_time guard rows through
// PUT /api/segments/{id} — both paths reproject, so history reflags
// immediately. Blank min = the 0.5s default; typed 0 = no minimum; blank
// max = no max.
function TimeFilterChip({ sec, t }) {
  const [open, setOpen] = useState(false);
  const [minS, setMinS] = useState("");
  const [maxS, setMaxS] = useState("");
  const tf = sec.time_filter;
  if (!tf) return null;
  const isSeg = sec.segment_id != null;
  const fmtS = (f) => (f % 30 === 0 ? String(f / 30) : (f / 30).toFixed(2));
  const label = tf.max_frames != null
    ? `⏱ ${fmtS(tf.min_frames)}–${fmtS(tf.max_frames)}s`
    : `⏱ ≥ ${fmtS(tf.min_frames)}s`;

  function openEditor() {
    setMinS(fmtS(tf.min_frames));
    setMaxS(tf.max_frames != null ? fmtS(tf.max_frames) : "");
    setOpen(true);
  }

  async function putSegGuards(minF, maxF) {
    // RMW the def's guard list: time rows replaced, other guards untouched
    const defs = await getJSON("/api/segments");
    const d = defs.find((x) => x.id === sec.segment_id);
    if (!d) return;
    const guards = (d.guards || []).filter(
      (g) => g.type !== "min_time" && g.type !== "max_time");
    if (minF != null) guards.push({ type: "min_time", frames: minF });
    if (maxF != null) guards.push({ type: "max_time", frames: maxF });
    await send("PUT", `/api/segments/${sec.segment_id}`, { guards });
  }

  async function save() {
    const minF = minS === "" ? null : Math.round(Number(minS) * 30);
    const maxF = maxS === "" ? null : Math.round(Number(maxS) * 30);
    if (isSeg) await putSegGuards(minF, maxF);
    // 15 mirrors projection.DEFAULT_MIN_FRAMES (blank min = keep the default)
    else await send("PUT",
      `/api/stars/${sec.course_id}/${sec.star_id}/time-filter`,
      { min_frames: minF == null ? 15 : minF, max_frames: maxF });
    setOpen(false);
    t.refresh();
  }

  async function reset() {
    if (isSeg) await putSegGuards(null, null);
    else await send("DELETE",
      `/api/stars/${sec.course_id}/${sec.star_id}/time-filter`);
    setOpen(false);
    t.refresh();
  }

  if (!open) return html`<button class="meta" style=${tf.is_default ? "opacity:.55" : ""}
      title="valid-time bounds — successes outside this range are ignored"
      onclick=${openEditor}>${label}</button>`;
  return html`<span class="meta">
    min <input type="number" min="0" step="0.1" style="width:4rem"
      value=${minS} oninput=${(e) => setMinS(e.target.value)} />s
    max <input type="number" min="0" step="0.1" style="width:4rem"
      value=${maxS} placeholder="∞" oninput=${(e) => setMaxS(e.target.value)} />s
    <button onclick=${save}>save</button>
    <button onclick=${reset} title="back to the 0.5s default">reset</button>
    <button onclick=${() => setOpen(false)}>cancel</button>
  </span>`;
}

// The trend graph plots SUCCESSFUL attempts only, so it stays empty through a
// session of resets — the copy has to say "completed" or it reads as broken
// to someone who has been practising for an hour.
function TrendEmpty() {
  return html`<${EmptyState} headline="No completed attempts yet"
      hint="Finish a run and your times start charting here." />`;
}

// The stat chips. ONE component for both section kinds (rule 11) — the chips
// loop was pasted into StarSection and SegmentSection identically, and a
// second copy is how the two drift.
//
// The CONTROL that chooses which chips show is a separate component,
// StatMenuTrigger, which stays in practice.js — moved into the practice-log
// card's header on 2026-07-28 (user: "For the stats button, we should move it
// to be inside the practice log, to the left of the sort filter"), leaving
// the chips themselves here, unmoved.
function StatChipsRow({ sec, t }) {
  return html`<div class="chips stat-chips">
    ${sec.stats.filter((stat) => t.showDust || !DUST_STAT_KEYS.has(stat.key))
      .map((stat) => html`
      <span class="chip" title=${stat.key}>${stat.label} ${stat.display ?? "–"}</span>`)}
  </div>`;
}

// The card's heading names whose history it is drawing the moment it can
// show someone other than the active target (Task 6) -- exactly
// `displayName`'s own `.name`, the same string the objective card's <h2>
// already shows for this section.
function subjectLine(sec, t) {
  return displayName(sec, (t.view.catalog || {}).courses || []).name;
}

export function EntityAnalysis({ sec, t, onPick }) {
  const [fold, toggle] = useCollapsed("analysis");
  const clock = sec ? sectionClock(sec, t.clock) : t.clock;
  return html`<section class="practice-card analysis-card ${cardClass(fold)}">
    <div class="card-heading">
      <div><span class="eyebrow">Analysis</span><h3>Attempt history</h3>
        ${sec && html`<span class="meta analysis-subject">${subjectLine(sec, t)}</span>`}
      </div>
      <${CollapseToggle} collapsed=${fold} toggle=${toggle}
        label="the analysis card" />
    </div>
    ${!sec
      ? html`<${EmptyState} headline="Nothing selected to practice"
          hint=${"Pick a star or segment above, or click a card in the "
               + "practice log — its timeline and trend fill in here."} />`
      : html`<div class="analysis-block timeline-block">
          <h4>Attempt timeline <span class="hint" tabindex="0"
            data-tip="Every attempt in the selected scope, positioned by its completion or reset time">ⓘ</span></h4>
          ${!sec.broken
            ? html`<${Timeline} tl=${sec.timeline} sec=${sec} t=${t} />`
            : html`<div class="stable-empty compact">Timeline unavailable for a deleted definition.</div>`}
        </div>
        <div class="analysis-block trend-block">
          <h4>Performance trend <span class="hint" tabindex="0"
            data-tip="Successful attempts over time — gold dots are saved PBs; click a dot to jump to its row">ⓘ</span></h4>
          ${hasProgressPoints(sec.progress, clock)
            ? html`<${Progress} prog=${sec.progress} clock=${clock} onPick=${onPick} />`
            : html`<${TrendEmpty} />`}
        </div>`}
  </section>`;
}

// The confirm-and-wipe handler, kind-dispatched (POST /api/wipe). Merges the
// two hand-written wipeData closures that used to live inside StarSection and
// SegmentSection -- their confirm copy differed only in the noun and the
// parenthetical (a star keeps its markers and strategies; a segment keeps its
// definition and markers), both of which come from entityNoun(sec) and one
// kind branch, plus the POST body itself (course_id/star_id vs segment_id).
export async function wipeSection(sec, t) {
  const noun = entityNoun(sec).toLowerCase();
  const name = isSegment(sec) ? sec.name : `${sec.course_name} · ${sec.star_name}`;
  const keeps = isSegment(sec)
    ? "the definition and markers are kept"
    : "markers and strategies are kept";
  const msg = t.scope === "lifetime"
    ? `Wipe ALL data for ${name} across every session?\n`
      + `All attempts and PBs for this ${noun} are permanently removed `
      + `(${keeps}).\nThis cannot be undone.`
    : `Wipe this session's data for ${name}?\n`
      + "The session's attempts and any PBs saved from them are "
      + "permanently removed (earlier PBs are kept).\nThis cannot be undone.";
  if (!window.confirm(msg)) return;
  await send("POST", "/api/wipe", isSegment(sec)
    ? { kind: "segment", segment_id: sec.segment_id, scope: t.scope }
    : { kind: "star", course_id: sec.course_id, star_id: sec.star_id, scope: t.scope });
  t.refresh();
}

// The `<details>` drawer: tools row, stat chips, standards, failure
// compilation. `entity`/`family` below are NOT the section's own identity --
// a paired Bowser pipe segment deliberately grades its StandardsPanel against
// its star's ladder (`sec.pipe_star_entity`), and a Bowser reds star's own
// StandardsPanel passes `family="Star"` for the same pairing read the other
// way. Both call sites' exact prop expressions, preserved.
export function EntityDrawer({ sec, t }) {
  if (!sec) return null;
  const seg = isSegment(sec);
  const entity = seg ? (sec.pipe_star_entity || `segment:${sec.segment_id}`) : entityKey(sec);
  const family = seg ? (sec.pipe_star_entity ? "Pipe" : null)
    : (sec.pipe_segment_id != null ? "Star" : null);
  const identity = seg ? { segment_id: sec.segment_id }
    : { course_id: sec.course_id, star_id: sec.star_id };
  const noun = entityNoun(sec).toLowerCase();
  return html`<details class="practice-card detail-drawer" open>
    <summary>Stats, standards, and practice options</summary>
    <div class="detail-tools">
      ${!seg && html`<a href=${sec.links.ukikipedia} target="_blank">RTA Guide ↗</a>`}
      ${!seg && sec.links.example && html`<a href=${sec.links.example} target="_blank">Example ↗</a>`}
      ${!sec.broken && html`<${TimeFilterChip} sec=${sec} t=${t} />`}
      <button class="danger-text" onclick=${() => wipeSection(sec, t)}
        title=${t.scope === "lifetime"
          ? `Wipe this ${noun}'s data across all sessions`
          : `Wipe this ${noun}'s data in the current session`}>Clear data</button>
    </div>
    <${StatChipsRow} sec=${sec} t=${t} />
    <${StandardsPanel} entity=${entity}
        activeStrat=${sec.last_strat} strategies=${sec.strategies}
        sectionRank=${sec.rank} sectionPb=${sec.pb}
        family=${family}
        onChanged=${t.refresh} defaultOpen=${true} />
    <${FailureCompilation} identity=${identity} defaultOpen=${true} />
  </details>`;
}
