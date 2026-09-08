import { useRef, useState } from "preact/hooks";
import { stopShuttle } from "../replayshuttle.js";
import { loopFromFrames } from "./inputtimelinemodel.js";

export function useTimelineSelection({ trackColumn, view, video, frameMap, boundedClock,
                                       data, total, reviewLoading, updateReview, seek }) {
  const selection = useRef(null);
  const [selectedRange, setSelectedRange] = useState(null);
  const [selectionError, setSelectionError] = useState(null);
  const pointerFrame = (event) => {
    const box = trackColumn.current;
    if (!box) return null;
    const rect = box.getBoundingClientRect();
    if (rect.width <= 0) return null;
    const next = Math.floor(view.start + ((event.clientX - rect.left) / rect.width) * (view.end - view.start));
    return Math.max(view.start, Math.min(view.end - 1, next));
  };
  const beginSelection = (event) => {
    if (event.button !== 0) return;
    const first = pointerFrame(event);
    if (first == null) return;
    event.preventDefault(); event.stopPropagation();
    event.currentTarget.focus({ preventScroll: true });
    event.currentTarget.setPointerCapture(event.pointerId);
    selection.current = { pointer: event.pointerId, first, last: first, x: event.clientX, dragged: false };
    setSelectionError(null);
  };
  const moveSelection = (event) => {
    const drag = selection.current;
    if (event.pointerId !== drag?.pointer) return;
    drag.last = pointerFrame(event) ?? drag.last;
    drag.dragged ||= Math.abs(event.clientX - drag.x) >= 4;
    if (drag.dragged) setSelectedRange({ start: Math.min(drag.first, drag.last), end: Math.max(drag.first, drag.last) + 1 });
  };
  const finishSelection = (event) => {
    const drag = selection.current;
    if (event.pointerId !== drag?.pointer) return;
    selection.current = null; setSelectedRange(null);
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    if (event.type !== "pointerup") return;
    if (!drag.dragged) { seek(drag.first); return; }
    const loop = video && loopFromFrames(drag.first, drag.last, frameMap, boundedClock, data.stretches, total);
    if (loop && !reviewLoading) { stopShuttle(video); video.pause(); updateReview({ loop }); }
    else setSelectionError("That selection has no unambiguous video boundaries. Set In and Out on the video instead.");
  };

  return { selectedRange, selectionError, beginSelection, moveSelection, finishSelection };
}
