// Review coordinates are input-axis frames. Only the template is translated;
// zoom crops the view and never changes the recorded extent or capture gaps.
import { h } from "preact";
import { useRef } from "preact/hooks";
import htm from "htm";

const html = htm.bind(h);

export function templateReviewKey(template) {
  return template?.id == null ? null : `${template.id}:${template.source?.revision || "legacy"}`;
}

export function templateOnAxis(template, lead, offset) {
  if (!template || template.error) return null;
  const source = template.source || template;
  const shift = offset + (template.source ? lead : 0);
  const translate = (spans) => (spans || []).map((span) => ({ ...span, start: span.start + shift }));
  return { ...template, runs: translate(source.runs), actions: translate(source.actions) };
}

export function timelineWindow(zoom, total) {
  if (!Number.isFinite(zoom?.start) || !Number.isFinite(zoom?.end)) return { start: 0, end: total };
  const start = Math.max(0, Math.min(total - 1, Math.floor(zoom.start)));
  const end = Math.max(start + 1, Math.min(total, Math.ceil(zoom.end)));
  return { start, end };
}

export function zoomTimeline(view, total, anchor, factor) {
  const size = Math.max(1, Math.min(total, Math.round((view.end - view.start) * factor)));
  if (size === total) return null;
  const center = Number.isFinite(anchor) ? anchor : (view.start + view.end) / 2;
  const start = Math.max(0, Math.min(total - size, Math.round(center - size / 2)));
  return { start, end: start + size };
}

export function spanInWindow(start, length, view) {
  const left = Math.max(view.start, start);
  const right = Math.min(view.end, start + length);
  if (right <= left) return null;
  const size = view.end - view.start;
  return `left:${(left - view.start) / size * 100}%;width:${(right - left) / size * 100}%`;
}

export function TimelineReviewControls({ templateKey, hasTemplate, offset,
    view, total, frame, lead, loopWindow, trackColumn, loading, onShift, onChange }) {
  const shiftDrag = useRef(null);
  const beginShift = (event) => {
    if (event.button !== 0 || !templateKey || loading) return;
    const width = trackColumn.current?.getBoundingClientRect().width;
    if (!width) return;
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    shiftDrag.current = { pointer: event.pointerId, x: event.clientX, offset, templateKey,
      framesPerPixel: (view.end - view.start) / width };
  };
  const moveShift = (event) => {
    const drag = shiftDrag.current;
    if (!drag || event.pointerId !== drag.pointer || drag.templateKey !== templateKey) return;
    event.preventDefault();
    onShift(drag.offset + (event.clientX - drag.x) * drag.framesPerPixel);
  };
  const endShift = (event) => {
    if (event.pointerId !== shiftDrag.current?.pointer) return;
    shiftDrag.current = null;
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };
  return html`<div class="input-review-controls" aria-label="Timeline review controls" aria-busy=${loading}>
    ${hasTemplate && html`<div class="input-template-shift" role="group" aria-label="Template alignment">
      <button class="input-template-drag" type="button" disabled=${loading || !templateKey}
          title="Drag left or right to shift only the template; arrow keys move one frame"
          onpointerdown=${beginShift} onpointermove=${moveShift}
          onpointerup=${endShift} onpointercancel=${endShift} onlostpointercapture=${endShift}
          onkeydown=${(event) => {
            if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
            event.preventDefault(); event.stopPropagation();
            onShift(offset + (event.key === "ArrowLeft" ? -1 : 1));
          }}>Shift template</button>
      <button type="button" disabled=${loading || !templateKey}
          aria-label="Shift template earlier one frame" title="Template earlier by 1 frame"
          onclick=${() => onShift(offset - 1)}>−1f</button>
      <output class="input-template-offset" aria-label="Template offset">${offset > 0 ? "+" : ""}${offset}f</output>
      <button type="button" disabled=${loading || !templateKey}
          aria-label="Shift template later one frame" title="Template later by 1 frame"
          onclick=${() => onShift(offset + 1)}>+1f</button>
      <button type="button" disabled=${loading || !templateKey || offset === 0}
          aria-label="Reset template shift" onclick=${() => onShift(0)}>Reset</button>
      ${!loading && !templateKey && html`<span class="meta">Alignment unavailable for this template.</span>`}
    </div>`}
    <div class="input-zoom-controls" role="group" aria-label="Timeline zoom">
      <button type="button" aria-label="Zoom out timeline" disabled=${loading || view.end - view.start >= total}
          onclick=${() => onChange({ zoom: zoomTimeline(view, total, frame, 2) })}>−</button>
      <button type="button" aria-label="Zoom in timeline" title="Zoom around the presented frame"
          disabled=${loading || view.end - view.start <= 1}
          onclick=${() => onChange({ zoom: zoomTimeline(view, total, frame, .5) })}>+</button>
      <button type="button" disabled=${loading || view.end - view.start >= total}
          onclick=${() => onChange({ zoom: null })}>Fit</button>
      <button type="button" disabled=${loading || !loopWindow}
          title=${loopWindow ? "Show the loop's inputs" : "Set A and B on mapped pictures to zoom to a loop"}
          onclick=${() => onChange({ zoom: loopWindow })}>Zoom to loop</button>
      <span class="input-zoom-range">Frames ${view.start - lead}–${view.end - lead - 1}</span>
      ${view.end - view.start < total && html`<input type="range" min="0"
          max=${total - (view.end - view.start)} step="1" value=${view.start}
          aria-label="Visible timeline start" disabled=${loading}
          oninput=${(event) => {
            const start = Number(event.target.value);
            onChange({ zoom: { start, end: start + view.end - view.start } });
          }} />`}
    </div>
  </div>`;
}
