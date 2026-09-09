import { h, Fragment } from "preact";
import { useMemo } from "preact/hooks";
import htm from "htm";
import { spanInWindow } from "../timelinereview.js";
import { STICK_HEIGHT, SPEED_HEIGHT, spanLabel, timeLabel } from "./inputtimelinemodel.js";

const html = htm.bind(h);

// Both action tracks occupy the SAME lane. The template's outlined band is
// taller, so identical timing still leaves an amber edge around your action.
function ActionRow({ name, spans, templateSpans = [], view, seek, lead = 0 }) {
  const draw = (span, ghost) => {
    const style = spanInWindow(span.start, span.length, view);
    if (!style) return null;
    return html`
    <button class=${`action-span group-${span.group} ${ghost ? "is-template" : ""}`}
        key=${`${ghost ? "t" : "a"}${span.start}`}
        style=${style}
        onclick=${(event) => { event.stopPropagation(); seek(Math.max(view.start, span.start)); }}
        title=${`${ghost ? "Template — " : ""}${span.label} — ${spanLabel(span.start, span.length, lead)} (${span.length}f)`}
        aria-label=${`${ghost ? "Template " : ""}${span.label} from ${spanLabel(span.start, span.length, lead)}`}>
      <span class="action-span-name">${span.label}</span>
    </button>`;
  };
  return html`<div class=${`input-lane is-actions ${templateSpans.length ? "has-template" : ""}`}>
    <span class="input-lane-name">${name}</span>
    <div class="input-lane-track">
      ${templateSpans.map((span) => draw(span, true))}
      ${spans.map((span) => draw(span, false))}
    </div>
  </div>`;
}

// The journal's MOMENTS on the same axis -- a pole grabbed, a bob-omb picked
// up, a switch pressed, the star itself -- each a tick at the frame it
// happened, with the recorder's own sentence beside it. Nothing here is
// captured; it is the journal joined onto the track by frame (server side,
// inputs/markers.py), so this row and the segment recorder can never name
// one thing two ways. A label gets the room up to the next tick and no more,
// so two moments a few frames apart read as two ticks rather than one
// smeared word; the tooltip carries the whole sentence.
// `lead` is the lead-in's length: a marker's own frame is on the AXIS, and
// the time it states must be on the ATTEMPT's clock, so a moment inside the
// lead reads negative rather than pretending the attempt started earlier.
function MomentRow({ markers, total, view, seek, lead = 0 }) {
  return html`<div class="input-lane is-moments">
    <span class="input-lane-name">Moments</span>
    <div class="input-lane-track">
      ${markers.map((marker, index) => {
        if (marker.frame < view.start || marker.frame >= view.end) return null;
        const next = index + 1 < markers.length ? markers[index + 1].frame : total;
        const room = Math.max(next - marker.frame, 1);
        return html`
          <button class=${`moment-mark type-${marker.type}`} key=${`${marker.frame}-${index}`}
                  data-frame=${marker.frame}
                  style=${spanInWindow(marker.frame, room, view)}
                  onclick=${(event) => { event.stopPropagation(); seek(marker.frame); }}
                  title=${`${marker.label} — ${timeLabel(marker.frame - lead)}`}
                  aria-label=${`${marker.label} at ${timeLabel(marker.frame - lead)}`}>
            <span class="moment-mark-tick"></span>
            <span class="moment-mark-name">${marker.label}</span>
          </button>`;
      })}
    </div>
  </div>`;
}

function ButtonLanes({ lanes, templateLanes, overlayVisible, view, seek, lead }) {
  // ONE lane per button, with the template's bars drawn BEHIND yours inside
  // it — which is what "drawn behind your own" means, and what two stacked
  // rows of identically-named lanes did not mean.
  const byBit = new Map(lanes.map((lane) => [lane.bit, lane]));
  const ghostByBit = new Map(templateLanes.map((lane) => [lane.bit, lane]));
  const bits = [...new Set([...byBit.keys(), ...ghostByBit.keys()])];
  const laneRow = (bit) => {
    const mine = byBit.get(bit);
    const ghost = overlayVisible(`button:${bit}`) ? ghostByBit.get(bit) : null;
    const name = (mine || ghostByBit.get(bit)).name;
    return html`<div class="input-lane" key=${bit}>
      <span class="input-lane-name">${name}</span>
      <div class="input-lane-track">
        ${(ghost ? ghost.bars : []).filter((bar) => spanInWindow(bar.start, bar.length, view)).map((bar) => html`
          <span class="input-bar is-template" key=${`t${bar.start}`}
                style=${spanInWindow(bar.start, bar.length, view)}
                title=${`Template — ${name} ${spanLabel(bar.start, bar.length, lead)} (${bar.length}f)`} />`)}
        ${(mine ? mine.bars : []).filter((bar) => spanInWindow(bar.start, bar.length, view)).map((bar) => html`
          <button class="input-bar" key=${bar.start}
                  style=${spanInWindow(bar.start, bar.length, view)}
                  onclick=${(event) => { event.stopPropagation(); seek(Math.max(view.start, bar.start)); }}
                  title=${`${name} ${spanLabel(bar.start, bar.length, lead)} (${bar.length}f)`}
                  aria-label=${`${name} held from ${spanLabel(bar.start, bar.length, lead)}, ${bar.length} frames`} />`)}
      </div>
    </div>`;
  };

  return html`<${Fragment}>${bits.map((bit) => laneRow(bit))}</${Fragment}>`;
}

function TimelineActions({ data, template, overlayVisible, view, seek, lead }) {
  return html`<${Fragment}>
      ${((data.actions || []).length > 0 || (template && template.actions?.length > 0)) && html`
        <${ActionRow} name="Mario" spans=${data.actions || []} view=${view}
            templateSpans=${template && overlayVisible("actions") ? template.actions || [] : []}
            seek=${seek} lead=${lead} />`}
  </${Fragment}>`;
}

function StaticLanes({ data, template, lanes, templateLanes, curves, overlayVisible, view, seek, lead, total }) {
  const markers = data.markers || [];
  return html`<${Fragment}>
      <div class="input-lane is-stick">
        <span class="input-lane-name">Stick</span>
        <div class="input-lane-track">
          <svg viewBox=${`${view.start} 0 ${view.end - view.start} ${STICK_HEIGHT}`} height=${STICK_HEIGHT}
               preserveAspectRatio="none" aria-hidden="true">
            <line x1="0" y1=${STICK_HEIGHT / 2} x2=${total} y2=${STICK_HEIGHT / 2}
                  class="stick-axis" vector-effect="non-scaling-stroke" />
            ${template && overlayVisible("stick") && html`
              <path class="stick-line is-x is-template" vector-effect="non-scaling-stroke"
                    d=${curves.template.x} />
              <path class="stick-line is-y is-template" vector-effect="non-scaling-stroke"
                    d=${curves.template.y} />`}
            <path class="stick-line is-x" vector-effect="non-scaling-stroke"
                  d=${curves.mine.x} />
            <path class="stick-line is-y" vector-effect="non-scaling-stroke"
                  d=${curves.mine.y} />
          </svg>
        </div>
      </div>
      <${TimelineActions} data=${data} template=${template} overlayVisible=${overlayVisible}
        view=${view} seek=${seek} lead=${lead} />
      ${markers.length > 0 && html`
        <${MomentRow} markers=${markers} total=${total} view=${view}
            seek=${seek} lead=${lead} />`}
      <div class="input-lane is-speed">
        <span class="input-lane-name">Speed</span>
        <div class="input-lane-track">
          <svg viewBox=${`${view.start} 0 ${view.end - view.start} ${SPEED_HEIGHT}`} height=${SPEED_HEIGHT}
               preserveAspectRatio="none" aria-hidden="true">
            ${template && overlayVisible("speed") && html`
              <path class="speed-line is-template" vector-effect="non-scaling-stroke"
                    d=${curves.template.speed} />`}
            <path class="speed-line" vector-effect="non-scaling-stroke"
                  d=${curves.mine.speed} />
          </svg>
        </div>
      </div>
      <${ButtonLanes} lanes=${lanes} templateLanes=${templateLanes}
        overlayVisible=${overlayVisible} view=${view} seek=${seek} lead=${lead} />
  </${Fragment}>`;
}

// The overlay hook returns a new reader each render. Its finite row values,
// not that closure's identity, describe whether the static drawing changed.
export function useStaticTimelineLanes(props) {
  const { data, template, lanes, templateLanes, curves, overlayVisible, view, seek, lead, total } = props;
  const rows = ["stick", "actions", "speed", ...(data?.buttons || []).map(([bit]) => `button:${bit}`)];
  const visibility = rows.map((key) => overlayVisible(key) ? "1" : "0").join("");
  // Retain the entire VDOM subtree between delivered pictures. A new seek
  // callback invalidates this cache when its map, clock, or source changes.
  return useMemo(() => data ? html`<${StaticLanes} ...${props} />` : null,
    [data, template, lanes, templateLanes, curves, visibility, view, seek, lead, total]);
}

export function TimelineTrack({ trackColumn, selectedRange, lead, view, loopWindow, review, frame }) {
  const percent = (value) => `${((value - view.start) / (view.end - view.start)) * 100}%`;
  return html`
      <div class="input-track-column" ref=${trackColumn}>
        ${selectedRange && html`<div class="input-selection-shade" style=${spanInWindow(selectedRange.start, selectedRange.end - selectedRange.start, view)}></div>`}
        ${spanInWindow(0, lead, view) && html`<div class="input-lead-shade"
            style=${spanInWindow(0, lead, view)}></div>`}
        ${loopWindow && spanInWindow(loopWindow.start, loopWindow.end - loopWindow.start, view) && html`
          <div class=${`input-loop-shade ${review.loop.enabled ? "is-enabled" : ""}`}
              style=${spanInWindow(loopWindow.start, loopWindow.end - loopWindow.start, view)}></div>`}
        ${frame != null && frame >= view.start && frame < view.end && html`
          <div class="input-playhead" style=${`left:${percent(frame)}`}></div>`}
      </div>
  `;
}
