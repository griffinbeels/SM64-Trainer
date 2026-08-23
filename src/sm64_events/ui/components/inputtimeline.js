// src/sm64_events/ui/components/inputtimeline.js
// One attempt's inputs, drawn as lanes over frames -- and, when a template is
// active, that template drawn BEHIND them so the gap is something you look at.
//
// It reports what each track was doing on a frame and names nothing as the
// reason. His ruling, 2026-08-20: "I think the user can deduce the corrections
// they need to make based on the data. If we try to prescribe solutions,
// that's a totally different problem." So there are no generated corrections,
// and -- one level deeper -- no attempt to MATCH your presses to the
// template's either: deciding "this A corresponds to that A" is an inference,
// and a wrong match is a confidently wrong number wearing a measurement's
// clothes.
//
// Lanes are HTML boxes at percentage widths rather than SVG, so they stretch
// with the drawer at any width with no aspect arithmetic. The stick is the one
// SVG, and it sets preserveAspectRatio explicitly -- the default is `slice`,
// which CROPS whatever the container's aspect does not cover.
import { h } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import htm from "htm";
import { Icon } from "./icons.js";
import { fmtIgtShort } from "../format.js";
import { ControllerPanel, FacingDial, stickPhrase } from "./controllerpanel.js";

const html = htm.bind(h);

const STICK_HEIGHT = 46;
const SPEED_HEIGHT = 30;

// A run is `{start, length, buttons, stick_x, stick_y, yaw, speed}` on the
// capture axis (zero-based, sorted), with holes between runs where capture
// stopped. The field names are the payload's own, so a field added on the
// server is readable here the moment it arrives.
export function frameAt(runs, frame) {
  // A frame inside a hole has no reading and answers null -- never the
  // neighbouring run's, which would interpolate across exactly the gap the
  // format exists to preserve.
  let low = 0;
  let high = runs.length - 1;
  while (low <= high) {
    const mid = (low + high) >> 1;
    const run = runs[mid];
    if (frame < run.start) high = mid - 1;
    else if (frame >= run.start + run.length) low = mid + 1;
    else return run;
  }
  return null;
}

export function actionAt(spans, frame) {
  for (const span of spans || []) {
    if (frame >= span.start && frame < span.start + span.length) return span;
  }
  return null;
}

export function lanesOf(runs, table) {
  // One lane per button that appears anywhere in the track. A lane nobody
  // pressed is a row of nothing, and drawing fourteen of them would bury the
  // three that carry the run.
  return (table || []).map(([bit, name]) => {
    const bars = [];
    for (const run of runs) {
      if (!(run.buttons & bit)) continue;
      const last = bars[bars.length - 1];
      if (last && last.start + last.length === run.start) last.length += run.length;
      else bars.push({ start: run.start, length: run.length });
    }
    return { bit, name, bars };
  }).filter((lane) => lane.bars.length > 0);
}

// A step line: one value held across each run, drawn as a horizontal segment
// from the run's start to its end. `valueOf` picks the field; `scale` maps it
// into the lane's height.
function stepPath(runs, valueOf, scale) {
  const points = [];
  for (const run of runs) {
    const y = scale(valueOf(run)).toFixed(2);
    points.push(`${run.start},${y}`, `${run.start + run.length},${y}`);
  }
  return points.join(" ");
}

// Speed is drawn against the fastest value in THIS track, not a fixed cap:
// what he asked for is "where there are opportunities to go faster", which is
// a comparison within one run. A fixed ceiling would flatten a whole slow
// segment into a line at the bottom and hide exactly that.
//
// With a template behind it, BOTH curves share the faster track's peak: two
// curves on two scales would put the slower run's top at the same height as
// the faster run's, which is the opposite of the comparison he asked for.
function speedPeak(...tracks) {
  let peak = 0;
  for (const runs of tracks) {
    for (const run of runs || []) peak = Math.max(peak, Math.abs(run.speed));
  }
  return peak > 0 ? peak : 1;
}

function speedPath(runs, peak) {
  return stepPath(runs, (run) => Math.abs(run.speed),
    (speed) => SPEED_HEIGHT - (speed / peak) * (SPEED_HEIGHT - 2));
}

// The stick's reach is the pad's own, not the game's cap: his pad reaches 84
// where the game clamps at 64, so scaling to the cap would pin every full
// deflection to the lane's edge (the same call controllerpanel.js makes).
function stickPath(runs, axis, stickMax) {
  const valueOf = (run) => (axis === "x" ? run.stick_x : run.stick_y);
  let reach = stickMax;
  for (const run of runs) reach = Math.max(reach, Math.abs(valueOf(run)));
  return stepPath(runs, valueOf,
    (value) => STICK_HEIGHT / 2 - (value / reach) * (STICK_HEIGHT / 2 - 2));
}

// Times read as SECONDS, not as a frame count. His round-32 ask, quoting
// k8ehops: "instead of using 30f or 35f, use xx.xx format instead, since thats
// what most people read times as". `fmtIgtShort` is the project's own display
// form, so this surface cannot spell a time differently from the rest.
const timeLabel = fmtIgtShort;

function spanLabel(start, length) {
  return length === 1
    ? timeLabel(start)
    : `${timeLabel(start)}–${timeLabel(start + length - 1)}`;
}

// One row of Mario's actions. The template's is the same row, dimmed and
// beneath yours rather than behind it: two labelled spans stacked in one
// lane would read as one unreadable label, where two lanes read as "he was
// diving here and you were still running".
function ActionRow({ name, spans, percent, seek, ghost = false }) {
  return html`<div class=${`input-lane is-actions ${ghost ? "is-template" : ""}`}>
    <span class="input-lane-name">${name}</span>
    <div class="input-lane-track">
      ${spans.map((span) => html`
        <button class=${`action-span group-${span.group} ${ghost ? "is-template" : ""}`}
                key=${span.start}
                style=${`left:${percent(span.start)};width:${percent(span.length)}`}
                onclick=${(event) => { event.stopPropagation(); seek(span.start); }}
                title=${`${ghost ? "Template — " : ""}${span.label} — ${spanLabel(span.start, span.length)} (${span.length}f)`}
                aria-label=${`${ghost ? "Template " : ""}${span.label} from ${spanLabel(span.start, span.length)}`}>
          <span class="action-span-name">${span.label}</span>
        </button>`)}
    </div>
  </div>`;
}

// THE clock mapping, in both directions. `anchorOffsetS` is how far into
// the clip the attempt's anchor sits: the clip is cut a few seconds BEFORE
// the anchor (the replay pre-pad) while the track starts AT it. Without it
// every input landed three seconds early (his first live run, 2026-08-22:
// "the input reader shows a totally different angle and shows me pressing
// A/B"). Pure and exported so tests/test_ui_input_clock.py can drive them.
export const frameAtTime = (seconds, anchorOffsetS, fps, frames) => {
  const raw = Math.floor((seconds - anchorOffsetS) * fps + 1e-4);
  return Math.max(0, Math.min(Math.max(frames - 1, 0), raw));
};
export const timeAtFrame = (frame, anchorOffsetS, fps) =>
  anchorOffsetS + (frame + 0.5) / fps;

export function InputTimeline({ attemptId, video, anchorOffsetS = 0,
                                compact = false }) {
  const [state, setState] = useState({ phase: "loading" });
  const [frame, setFrame] = useState(0);
  // The pointer and the playhead both work in the TRACK column's own box,
  // never the lane row's: the row starts with the label column, and a
  // playhead measured against it could be dragged over the words "Stick"
  // and "Mario" (his report, 2026-08-22).
  const trackColumn = useRef(null);

  useEffect(() => {
    let alive = true;
    setState({ phase: "loading" });
    fetch(`/api/attempts/${attemptId}/inputs`)
      .then((response) => (response.ok
        ? response.json()
        : response.text().then((text) => Promise.reject(new Error(text)))))
      .then((data) => { if (alive) setState({ phase: "ready", data }); })
      .catch((error) => { if (alive) setState({ phase: "error", error: String(error) }); });
    return () => { alive = false; };
  }, [attemptId]);

  // ONE CLOCK, ALWAYS. The video is the clock whenever there is one: the
  // timeline reads it every frame and never keeps a position of its own, so
  // dragging the video's scrubber moves the playhead and dragging the
  // playhead seeks the video. There is no "stop following" state -- that
  // was how the two drifted apart (his report, 2026-08-22: "if I drag the
  // video playhead itself, it should automatically move the input playback
  // system's playhead as well. We need both of these to always stay in
  // sync").
  useEffect(() => {
    if (!video || state.phase !== "ready") return undefined;
    let raf = 0;
    const { fps, frames } = state.data;
    const tick = () => {
      const at = frameAtTime(video.currentTime || 0, anchorOffsetS, fps, frames);
      setFrame((current) => (current === at ? current : at));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [video, state, anchorOffsetS]);

  const data = state.phase === "ready" ? state.data : null;
  const lanes = useMemo(
    () => (data ? lanesOf(data.runs, data.buttons) : []), [data]);
  const templateLanes = useMemo(
    () => (data && data.template ? lanesOf(data.template.runs, data.buttons) : []),
    [data]);

  if (state.phase === "loading") {
    return html`<div class="input-timeline is-loading">Reading inputs…</div>`;
  }
  if (state.phase === "error") {
    return html`<div class="input-timeline is-error">
      Could not read this attempt's inputs: ${state.error}</div>`;
  }
  if (!data.runs.length) {
    return html`<div class="input-timeline is-empty">
      <${Icon} name="feed" size=${18} />
      <div>
        <strong>No inputs recorded for this attempt.</strong>
        <p>It was played before input capture existed, or the trainer was not
           attached to the emulator at the time.</p>
      </div>
    </div>`;
  }

  const total = data.frames || 1;
  const seek = (next) => {
    const clamped = Math.max(0, Math.min(total - 1, next));
    setFrame(clamped);
    if (video) {
      // Seeking the video is how the timeline moves: the clock loop above
      // reads the new time back on the next frame, so the two cannot
      // disagree even for a frame.
      if (!video.paused) video.pause();
      video.currentTime = timeAtFrame(clamped, anchorOffsetS, data.fps);
    }
  };
  const seekFromPointer = (event) => {
    const box = trackColumn.current;
    if (!box) return;
    const rect = box.getBoundingClientRect();
    if (rect.width <= 0) return;
    seek(Math.round(((event.clientX - rect.left) / rect.width) * total));
  };

  const here = frameAt(data.runs, frame);
  const nowDoing = actionAt(data.actions, frame);
  const template = data.template && !data.template.error ? data.template : null;
  const there = template ? frameAt(template.runs, frame) : null;
  const thereDoing = template ? actionAt(template.actions, frame) : null;
  const peak = speedPeak(data.runs, template ? template.runs : []);
  const percent = (value) => `${(value / total) * 100}%`;

  // ONE lane per button, with the template's bars drawn BEHIND yours inside
  // it — which is what "drawn behind your own" means, and what two stacked
  // rows of identically-named lanes did not mean.
  const byBit = new Map(lanes.map((lane) => [lane.bit, lane]));
  const ghostByBit = new Map(templateLanes.map((lane) => [lane.bit, lane]));
  const bits = [...new Set([...byBit.keys(), ...ghostByBit.keys()])];
  const laneRow = (bit) => {
    const mine = byBit.get(bit);
    const ghost = ghostByBit.get(bit);
    const name = (mine || ghost).name;
    return html`<div class="input-lane" key=${bit}>
      <span class="input-lane-name">${name}</span>
      <div class="input-lane-track">
        ${(ghost ? ghost.bars : []).map((bar) => html`
          <span class="input-bar is-template" key=${`t${bar.start}`}
                style=${`left:${percent(bar.start)};width:${percent(bar.length)}`}
                title=${`Template — ${name} ${spanLabel(bar.start, bar.length)} (${bar.length}f)`} />`)}
        ${(mine ? mine.bars : []).map((bar) => html`
          <button class="input-bar" key=${bar.start}
                  style=${`left:${percent(bar.start)};width:${percent(bar.length)}`}
                  onclick=${(event) => { event.stopPropagation(); seek(bar.start); }}
                  title=${`${name} ${spanLabel(bar.start, bar.length)} (${bar.length}f)`}
                  aria-label=${`${name} held from ${spanLabel(bar.start, bar.length)}, ${bar.length} frames`} />`)}
      </div>
    </div>`;
  };

  return html`<div class=${`input-timeline ${compact ? "is-compact" : ""}`}>
    <header class="input-timeline-head">
      <div>
        <span class="eyebrow">Inputs</span>
        <h4>${timeLabel(total)}${" "}·${" "}${total} frames${" "}·${" "}${data.fps} fps</h4>
      </div>
      <div class="input-timeline-actions">
        <button class="icon-button" onclick=${() => seek(frame - 1)}
            title="Previous frame" aria-label="Previous frame">−1f</button>
        <button class="icon-button" onclick=${() => seek(frame + 1)}
            title="Next frame" aria-label="Next frame">+1f</button>
      </div>
    </header>

    ${data.template && html`<div class="input-template-note">
      <${Icon} name="bookmark" size=${13} />
      <span>Compared against${" "}<strong>${data.template.name}</strong>${
        data.template.error
          ? html` — <span class="is-error">that template no longer loads:${" "}
              ${data.template.error}</span>`
          : ", drawn behind your own. Both start at frame 0."}</span>
    </div>`}

    <div class="input-lanes"
         onpointerdown=${seekFromPointer}
         onpointermove=${(event) => { if (event.buttons & 1) seekFromPointer(event); }}
         role="group" aria-label="Input lanes">
      <div class="input-track-column" ref=${trackColumn}>
        <div class="input-playhead" style=${`left:${percent(frame)}`}></div>
      </div>
      <div class="input-lane is-stick">
        <span class="input-lane-name">Stick</span>
        <div class="input-lane-track">
          <svg viewBox=${`0 0 ${total} ${STICK_HEIGHT}`} height=${STICK_HEIGHT}
               preserveAspectRatio="none" aria-hidden="true">
            <line x1="0" y1=${STICK_HEIGHT / 2} x2=${total} y2=${STICK_HEIGHT / 2}
                  class="stick-axis" vector-effect="non-scaling-stroke" />
            ${data.template && html`
              <polyline class="stick-line is-x is-template" vector-effect="non-scaling-stroke"
                        points=${stickPath(data.template.runs, "x", data.stick_max)} />
              <polyline class="stick-line is-y is-template" vector-effect="non-scaling-stroke"
                        points=${stickPath(data.template.runs, "y", data.stick_max)} />`}
            <polyline class="stick-line is-x" vector-effect="non-scaling-stroke"
                      points=${stickPath(data.runs, "x", data.stick_max)} />
            <polyline class="stick-line is-y" vector-effect="non-scaling-stroke"
                      points=${stickPath(data.runs, "y", data.stick_max)} />
          </svg>
        </div>
      </div>
      ${(data.actions || []).length > 0 && html`
        <${ActionRow} name="Mario" spans=${data.actions} percent=${percent} seek=${seek} />`}
      ${template && (template.actions || []).length > 0 && html`
        <${ActionRow} name="Template" spans=${template.actions} percent=${percent}
            seek=${seek} ghost=${true} />`}
      <div class="input-lane is-speed">
        <span class="input-lane-name">Speed</span>
        <div class="input-lane-track">
          <svg viewBox=${`0 0 ${total} ${SPEED_HEIGHT}`} height=${SPEED_HEIGHT}
               preserveAspectRatio="none" aria-hidden="true">
            ${template && html`
              <polyline class="speed-line is-template" vector-effect="non-scaling-stroke"
                        points=${speedPath(template.runs, peak)} />`}
            <polyline class="speed-line" vector-effect="non-scaling-stroke"
                      points=${speedPath(data.runs, peak)} />
          </svg>
        </div>
      </div>
      ${bits.map((bit) => laneRow(bit))}
    </div>

    <footer class="input-inspector">
      <div class="input-inspector-frame">
        <span class="eyebrow">Frame</span>
        <strong>${frame} / ${total}</strong>
        <span class="meta">${timeLabel(frame)}</span>
      </div>
      <${ControllerPanel} frame=${here} buttons=${data.buttons}
          stickMax=${data.stick_max} deadZone=${data.dead_zone}
          label=${data.template ? "You pressed" : "Pressing"} />
      <${FacingDial} yaw=${here ? here.yaw : null}
          angleUnits=${data.angle_units} speed=${here ? here.speed : 0}
          label="Mario faces" />
      ${template && html`
        <${ControllerPanel} frame=${there} buttons=${data.buttons}
            stickMax=${data.stick_max} deadZone=${data.dead_zone}
            label=${template.name} />
        <${FacingDial} yaw=${there ? there.yaw : null}
            angleUnits=${data.angle_units} speed=${there ? there.speed : 0}
            label="Template faces" />`}
      <div class="input-inspector-read">
        ${here
          ? html`<span>Stick ${stickPhrase(here.stick_x, here.stick_y,
              data.dead_zone, data.stick_max)}</span>`
          : html`<span class="is-error">No capture on this frame</span>`}
        ${nowDoing && html`<span class="input-inspector-action">
          ${nowDoing.label}</span>`}
        ${thereDoing && html`<span class="input-inspector-action is-template"
            title="What the template was doing on this frame">
          template: ${thereDoing.label}</span>`}
      </div>
    </footer>
  </div>`;
}
