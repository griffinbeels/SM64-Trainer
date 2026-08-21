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
import { ControllerPanel, FacingDial, stickPhrase } from "./controllerpanel.js";

const html = htm.bind(h);

const FPS = 30;
const STICK_HEIGHT = 46;
const SPEED_HEIGHT = 30;

export function frameAt(runs, frame) {
  // Runs are [start, length, buttons, stickX, stickY], zero-based and sorted,
  // with holes between them where capture stopped. A frame inside a hole has
  // no reading and answers null -- never the neighbouring run's, which would
  // interpolate across exactly the gap the format exists to preserve.
  let low = 0;
  let high = runs.length - 1;
  while (low <= high) {
    const mid = (low + high) >> 1;
    const [start, length, buttons, stickX, stickY] = runs[mid];
    if (frame < start) high = mid - 1;
    else if (frame >= start + length) low = mid + 1;
    else return { buttons, stickX, stickY, yaw: runs[mid][5] || 0,
                  speed: runs[mid][6] || 0 };
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
    for (const [start, length, buttons] of runs) {
      if (!(buttons & bit)) continue;
      const last = bars[bars.length - 1];
      if (last && last.start + last.length === start) last.length += length;
      else bars.push({ start, length });
    }
    return { bit, name, bars };
  }).filter((lane) => lane.bars.length > 0);
}

// Speed is drawn against the fastest value in THIS track, not a fixed cap:
// what he asked for is "where there are opportunities to go faster", which is
// a comparison within one run. A fixed ceiling would flatten a whole slow
// segment into a line at the bottom and hide exactly that.
function speedPath(runs, frames) {
  if (!runs.length || !frames) return "";
  let peak = 0;
  for (const run of runs) peak = Math.max(peak, Math.abs(run[6] || 0));
  if (peak <= 0) peak = 1;
  const points = [];
  for (const run of runs) {
    const y = SPEED_HEIGHT - (Math.abs(run[6] || 0) / peak) * (SPEED_HEIGHT - 2);
    points.push(`${run[0]},${y.toFixed(2)}`);
    points.push(`${run[0] + run[1]},${y.toFixed(2)}`);
  }
  return points.join(" ");
}

function stickPath(runs, frames, axis, height) {
  if (!runs.length || !frames) return "";
  const points = [];
  for (const run of runs) {
    const value = axis === "x" ? run[3] : run[4];
    const y = height / 2 - (value / 84) * (height / 2 - 2);
    points.push(`${run[0]},${y.toFixed(2)}`);
    points.push(`${run[0] + run[1]},${y.toFixed(2)}`);
  }
  return points.join(" ");
}

// Times read as SECONDS, not as a frame count. His round-32 ask, quoting
// k8ehops: "instead of using 30f or 35f, use xx.xx format instead, since thats
// what most people read times as". The notation is the project's own display
// form (`fmtIgtShort`, `ui/format.js`) rather than a fourth spelling invented
// here -- same information, one notation across the whole app.
function timeLabel(frame) {
  const total = Math.abs(frame) / FPS;
  const seconds = Math.floor(total);
  const centis = Math.round((total - seconds) * 100);
  return `${frame < 0 ? "-" : ""}${seconds}"${String(centis).padStart(2, "0")}`;
}

function spanLabel(start, length) {
  return length === 1
    ? timeLabel(start)
    : `${timeLabel(start)}–${timeLabel(start + length - 1)}`;
}

export function InputTimeline({ attemptId, video, compact = false }) {
  const [state, setState] = useState({ phase: "loading" });
  const [frame, setFrame] = useState(0);
  const [following, setFollowing] = useState(true);
  const laneBox = useRef(null);

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

  // ONE CLOCK. The video is the clock whenever there is one -- the timeline
  // follows it rather than running a second one beside it, which is what keeps
  // "both stay in sync" structural instead of a thing we keep re-fixing.
  useEffect(() => {
    if (!video || !following) return undefined;
    let raf = 0;
    const tick = () => {
      const at = Math.floor((video.currentTime || 0) * FPS + 1e-4);
      setFrame((current) => (current === at ? current : at));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [video, following]);

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
    setFollowing(false);
    setFrame(clamped);
    if (video) {
      if (!video.paused) video.pause();
      video.currentTime = (clamped + 0.5) / FPS;
    }
  };
  const seekFromPointer = (event) => {
    const box = laneBox.current;
    if (!box) return;
    const rect = box.getBoundingClientRect();
    if (rect.width <= 0) return;
    seek(Math.round(((event.clientX - rect.left) / rect.width) * total));
  };

  const here = frameAt(data.runs, frame);
  const nowDoing = actionAt(data.actions, frame);
  const there = data.template ? frameAt(data.template.runs, frame) : null;
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
        <h4>${timeLabel(total)}${" "}·${" "}${total} frames${" "}·${" "}${FPS} fps</h4>
      </div>
      <div class="input-timeline-actions">
        ${video && html`<button class="icon-button" onclick=${() => setFollowing(true)}
            title="Follow the video again" aria-label="Follow the video again"
            disabled=${following}><${Icon} name="play" size=${14} /></button>`}
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

    <div class="input-lanes" ref=${laneBox}
         onpointerdown=${seekFromPointer}
         onpointermove=${(event) => { if (event.buttons & 1) seekFromPointer(event); }}
         role="group" aria-label="Input lanes">
      <div class="input-playhead" style=${`left:${percent(frame)}`}></div>
      <div class="input-lane is-stick">
        <span class="input-lane-name">Stick</span>
        <div class="input-lane-track">
          <svg viewBox=${`0 0 ${total} ${STICK_HEIGHT}`} height=${STICK_HEIGHT}
               preserveAspectRatio="none" aria-hidden="true">
            <line x1="0" y1=${STICK_HEIGHT / 2} x2=${total} y2=${STICK_HEIGHT / 2}
                  class="stick-axis" vector-effect="non-scaling-stroke" />
            ${data.template && html`
              <polyline class="stick-line is-x is-template" vector-effect="non-scaling-stroke"
                        points=${stickPath(data.template.runs, total, "x", STICK_HEIGHT)} />
              <polyline class="stick-line is-y is-template" vector-effect="non-scaling-stroke"
                        points=${stickPath(data.template.runs, total, "y", STICK_HEIGHT)} />`}
            <polyline class="stick-line is-x" vector-effect="non-scaling-stroke"
                      points=${stickPath(data.runs, total, "x", STICK_HEIGHT)} />
            <polyline class="stick-line is-y" vector-effect="non-scaling-stroke"
                      points=${stickPath(data.runs, total, "y", STICK_HEIGHT)} />
          </svg>
        </div>
      </div>
      ${(data.actions || []).length > 0 && html`
        <div class="input-lane is-actions">
          <span class="input-lane-name">Mario</span>
          <div class="input-lane-track">
            ${data.actions.map((span) => html`
              <button class=${`action-span group-${span.group}`}
                      key=${span.start}
                      style=${`left:${percent(span.start)};width:${percent(span.length)}`}
                      onclick=${(event) => { event.stopPropagation(); seek(span.start); }}
                      title=${`${span.label} — ${spanLabel(span.start, span.length)} (${span.length}f)`}
                      aria-label=${`${span.label} from ${spanLabel(span.start, span.length)}`}>
                <span class="action-span-name">${span.label}</span>
              </button>`)}
          </div>
        </div>`}
      <div class="input-lane is-speed">
        <span class="input-lane-name">Speed</span>
        <div class="input-lane-track">
          <svg viewBox=${`0 0 ${total} ${SPEED_HEIGHT}`} height=${SPEED_HEIGHT}
               preserveAspectRatio="none" aria-hidden="true">
            <polyline class="speed-line" vector-effect="non-scaling-stroke"
                      points=${speedPath(data.runs, total)} />
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
      <${FacingDial} yaw=${here ? here.yaw : 0}
          angleUnits=${data.angle_units} speed=${here ? here.speed : 0}
          label="Mario faces" />
      ${data.template && !data.template.error && html`
        <${ControllerPanel} frame=${there} buttons=${data.buttons}
            stickMax=${data.stick_max} deadZone=${data.dead_zone}
            label=${data.template.name} />`}
      <div class="input-inspector-read">
        ${here
          ? html`<span>Stick ${stickPhrase(here.stickX, here.stickY,
              data.dead_zone, data.stick_max)}</span>`
          : html`<span class="is-error">No capture on this frame</span>`}
        ${nowDoing && html`<span class="input-inspector-action">
          ${nowDoing.label}</span>`}
      </div>
    </footer>
  </div>`;
}
