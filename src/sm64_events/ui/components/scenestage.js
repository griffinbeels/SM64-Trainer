// src/sm64_events/ui/components/scenestage.js -- the Scene viewer's stage picker, its Course/Star/
// Strategy choice (M4 round 4, lab only; sceneviewer.js draws it in its header).
//
// His words (round 4, item 2): "We should see which stage / strategy was selected for the scene
// viewer. We should include our Course/Star/Strategy selector on this screen. By default, it opens
// to whatever the user currently has selected in the practice log (or how they got here), or if
// nothing."
//
// It is the Practice target picker's own grid (targetpicker.js's groups in entitymodal.js's
// PickerDialog) and the practice card's strategy dropdown (stratpicker.js), and it writes nothing to
// Practice: a choice asks the lab for its stage (GET /api/scene-lab/stage: your fastest attempt of
// that target and strategy that still has a saved scene, or why there is none), and the viewer
// opens it. Picking a star or segment asks with no strategy, and the lab answers with your active
// one, which the dropdown then shows.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON } from "../api.js";
import { displayName } from "../entitysection.js";
import { parseEntityKey, parseSegmentId, parseStarId } from "../entities.js";
import { PickerDialog } from "./entitymodal.js";
import { entityIconSrc, fallbackToGenericStar, optionIconSrc } from "./entityicons.js";
import { Icon } from "./icons.js";
import { StratPicker } from "./stratpicker.js";
import { targetCell, targetGroups } from "./targetpicker.js";

const html = htm.bind(h);

// The Practice log's row for attempt `id`, with the star or segment card it sits in.
export function attemptIn(view, id) {
  for (const section of [...(view?.stars || []), ...(view?.segments || [])]) {
    const attempt = (section.attempts || []).find((row) => row.id === id);
    if (attempt) return { section, attempt };
  }
  return null;
}

// The target of an open scene: the lab's own (the open answer's "target"), else the attempt the
// Ghost button named, read from the Practice log; null for a scene of neither (a lab package).
export function sceneTarget(scene, view) {
  if (scene?.target) return scene.target;
  const found = scene?.attempt != null ? attemptIn(view, scene.attempt) : null;
  if (!found) return null;
  const { section, attempt } = found, named = displayName(section, view?.catalog?.courses || []);
  const words = { course_name: named.context, star_name: named.name, strat: attempt.strat_tag ?? null };
  return section.kind === "segment" ? { segment_id: attempt.segment_id ?? section.segment_id, ...words }
    : { course_id: section.course_id, star_id: section.star_id, ...words };
}

// What the Practice log has selected: its target and that target's active strategy, or null.
export function practiceTarget(view) {
  const target = view?.target;
  if (!target) return null;
  if (target.kind === "segment") {
    return target.segment_id != null ? { segment_id: target.segment_id, strat: target.strat_tag ?? null } : null;
  }
  return target.course_id != null ? { course_id: target.course_id, star_id: target.star_id,
    course_name: target.course_name ?? null, star_name: target.star_name ?? null, strat: target.strat_tag ?? null }
    : null;
}

// What the stage picker starts on: the scene on screen's target (how he got here), else, with no scene
// open, what the Practice log has selected; null (a prompt) otherwise.
export function stageDefault(scene, view) {
  return scene ? sceneTarget(scene, view) : practiceTarget(view);
}

// The entity a target names, as the rest of the app keys it ("star:8:1", "segment:12"), or null.
export function targetKey(target) {
  const cell = targetCell(target);
  if (cell == null) return null;
  const segment = parseSegmentId(cell);
  return segment != null ? `segment:${segment}` : `star:${target.course_id}:${target.star_id}`;
}

// The target an entity key names ("star:8:1", "segment:12"; a Library ghost's), on `strat`, or null.
export function entityTarget(entity, strat = null) {
  const segment = parseSegmentId(entity);
  if (segment != null) return Number.isInteger(segment) ? { segment_id: segment, strat } : null;
  const { kind, id } = parseEntityKey(entity);
  const star = kind === "star" ? parseStarId(id) : null;
  return star && Number.isInteger(star.course) && Number.isInteger(star.star)
    ? { course_id: star.course, star_id: star.star, strat } : null;
}

// The lab's query for a target: course and star, or segment, and the strategy when one is chosen.
export function targetQuery(target) {
  const query = new URLSearchParams();
  if (targetKey(target)?.startsWith("segment:")) query.set("segment", target.segment_id);
  else {
    query.set("course", target.course_id);
    query.set("star", target.star_id);
  }
  if (target.strat) query.set("strat", target.strat);
  return query;
}

// The two lines the stage picker shows for a target: its star or segment, then its course.
function targetWords(target, t) {
  const segment = targetKey(target)?.startsWith("segment:");
  const known = segment ? (t?.segments || []).find((row) => row.id === Number(target.segment_id)) : null;
  const name = target.star_name || known?.name || (segment ? "Segment" : "Star");
  return { name, context: target.course_name || (segment ? "Segment" : "") };
}

// The strategy names recorded for an entity (the list the Practice target picker offers), or null
// while they load.
export function useStrategies(key) {
  const [answer, setAnswer] = useState({ key: null, names: null });
  useEffect(() => {
    if (!key) return undefined;
    let alive = true;
    getJSON(`/api/target/strategies?entity=${encodeURIComponent(key)}`)
      .then((data) => { if (alive) setAnswer({ key, names: (data.strategies || []).map((strat) => strat.name) }); })
      .catch(() => { if (alive) setAnswer({ key, names: [] }); });
    return () => { alive = false; };
  }, [key]);
  return answer.key === key ? answer.names : null;
}

// A picked cell as a target: no strategy yet, so the lab answers with the active one.
function pickedTarget(id) {
  const segment = parseSegmentId(id);
  if (segment != null) return { segment_id: segment, strat: null };
  const star = parseStarId(id);
  return { course_id: star.course, star_id: star.star, strat: null };
}

// `onChoose(target)` asks the lab for that target's stage and returns a promise of its answer;
// `strategies` are the target's (useStrategies); `note` is what the picker says beside it (the
// prompt, "Finding...", or the lab's reason).
export function StagePicker({ t, target, strategies, onChoose, note }) {
  const [picking, setPicking] = useState(false);
  const key = targetKey(target), choice = useRef(null);
  const words = key ? targetWords(target, t) : null, catalog = !!t?.view?.catalog;
  const strategy = (tag) => {
    choice.current = onChoose({ ...target, strat: tag || null });
    return Promise.resolve(null);
  };
  return html`<div class="scene-lab-stage-picker" role="group" aria-label="Course, star and strategy">
    <button type="button" class="scene-lab-target" disabled=${!catalog} onclick=${() => setPicking(true)}
        title="Choose a course, then a star or segment">
      ${key ? html`<img src=${entityIconSrc(t, key)} alt="" draggable="false"
          onerror=${(event) => fallbackToGenericStar(event, 0)} />` : html`<${Icon} name="target" size=${20} />`}
      <span class="scene-lab-target-words">
        ${words ? html`<strong>${words.name}</strong><small>${words.context}</small>` : html`<strong>Choose a star</strong>`}
      </span>
      <${Icon} name="chevron" size=${14} />
    </button>
    ${key && strategies?.length ? html`<${StratPicker} key=${key} entity=${key} strategies=${strategies}
        active=${target.strat ?? null} allowNew=${false} highlightUnset=${false} label="Strategy"
        blankLabel="— no strategy —" submit=${strategy} onChanged=${() => choice.current || Promise.resolve()} />`
      : null}
    ${note && html`<span class="scene-lab-stage-note" role="status">${note}</span>`}
    ${picking && catalog && html`<${PickerDialog} groups=${targetGroups(t)} value=${targetCell(target)}
        title="Choose a course" depth=${2} placeholder=${null}
        iconFor=${(id) => optionIconSrc(t, parseSegmentId(id) == null ? "star" : "segment", parseSegmentId(id) ?? id)}
        onClose=${() => setPicking(false)}
        onPick=${(id) => { setPicking(false); onChoose(pickedTarget(id)); }} />`}
  </div>`;
}
