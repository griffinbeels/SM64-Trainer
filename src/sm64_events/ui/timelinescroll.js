import { h } from "preact";
import { useEffect, useId, useRef } from "preact/hooks";
import htm from "htm";
const html = htm.bind(h);

export function TimelineScroll({ view, total, trackColumn, loading, onChange }) {
  const bar = useRef(null), current = useRef(null);
  const drag = useRef(null), controlledId = useId();
  current.current = { view, total, loading, onChange };
  const size = view.end - view.start;
  function panTo(value) {
    const { view: v, total: count, loading: busy, onChange: change } = current.current;
    if (busy) return;
    const width = v.end - v.start;
    const start = Math.max(0, Math.min(count - width, Math.round(value)));
    if (start === v.start) return;
    change({ zoom: width === count ? null : { start, end: start + width } });
  }
  useEffect(() => {
    const node = bar.current;
    const pan = event => {
      event.preventDefault(); event.stopPropagation();
      node.focus({ preventScroll: true });
      const { view: v } = current.current;
      panTo(v.start + (event.deltaX || event.deltaY) / node.clientWidth * (v.end - v.start));
    };
    node.addEventListener("wheel", pan, { passive: false });
    return () => node.removeEventListener("wheel", pan);
  }, []);
  useEffect(() => {
    const lanes = trackColumn.current?.closest(".input-lanes");
    if (!lanes) return;
    lanes.id = controlledId;
    function wheel(event) {
      // Cancel even at the zoom limits: a saturated timeline must not suddenly
      // hand the same gesture to the document or the browser's page zoom.
      event.preventDefault(); event.stopPropagation();
      lanes.focus({ preventScroll: true });
      const { view: v, total: count, loading: busy, onChange: change } = current.current;
      if (busy || !event.deltaY) return;
      const rect = trackColumn.current.getBoundingClientRect();
      if (!rect.width) return;
      const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
      const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? rect.height : 1);
      const width = Math.max(1, Math.min(count, Math.round((v.end - v.start) * Math.exp(Math.max(-1, Math.min(1, delta * .003))))));
      const anchor = v.start + ratio * (v.end - v.start);
      const start = Math.max(0, Math.min(count - width, Math.round(anchor - ratio * width)));
      if (start === v.start && width === v.end - v.start) return;
      change({ zoom: width === count ? null : { start, end: start + width } });
    }
    lanes.addEventListener("wheel", wheel, { passive: false });
    return () => lanes.removeEventListener("wheel", wheel);
  }, [trackColumn]);
  function finish(event) {
    if (drag.current && event.pointerId !== drag.current.pointer) return;
    drag.current = null;
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  }
  return html`<div class="timeline-scroll" ref=${bar} tabindex="0"
    role="scrollbar" aria-orientation="horizontal" aria-controls=${controlledId}
    aria-valuemin="0" aria-valuemax=${Math.max(0, total - size)} aria-valuenow=${view.start}
    aria-valuetext=${`Frames ${view.start} to ${view.end - 1} of ${total}`} aria-disabled=${loading}
    aria-label="Timeline position" title="Scroll to pan; wheel over the lanes to zoom"
    onpointerdown=${event => {
      if (event.button !== 0 || loading) return;
      event.preventDefault(); event.stopPropagation();
      event.currentTarget.focus({ preventScroll: true });
      const rect = event.currentTarget.getBoundingClientRect();
      const thumb = event.currentTarget.firstElementChild.getBoundingClientRect();
      const scale = (total - size) / Math.max(1, rect.width - thumb.width);
      const start = event.target === event.currentTarget
        ? Math.max(0, Math.min(total - size, (event.clientX - rect.left - thumb.width / 2) * scale)) : view.start;
      drag.current = { pointer: event.pointerId, x: event.clientX, start, scale };
      event.currentTarget.setPointerCapture(event.pointerId); panTo(start);
    }} onpointermove=${event => {
      if (drag.current?.pointer === event.pointerId) panTo(drag.current.start + (event.clientX - drag.current.x) * drag.current.scale);
    }} onpointerup=${finish} onpointercancel=${finish} onlostpointercapture=${finish}
    onkeydown=${event => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault(); event.stopPropagation();
      if (loading) return;
      const start = event.key === "Home" ? 0 : event.key === "End" ? total - size
        : Math.max(0, Math.min(total - size, view.start + (event.key === "ArrowLeft" ? -1 : 1) * Math.max(1, Math.round(size / 10))));
      panTo(start);
    }}><div class="timeline-scroll-thumb" style=${`width:max(12px,${size / total * 100}%);left:calc(${view.start / Math.max(1, total - size)} * (100% - max(12px,${size / total * 100}%)))`}></div></div>`;
}
