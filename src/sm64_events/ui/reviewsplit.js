import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
const html = htm.bind(h);

export function ReviewSplit() {
  const [value, setValue] = useState(60);
  const [limits, setLimits] = useState({ min: 25, max: 80 });
  const dragging = useRef(null);
  const separator = useRef(null), current = useRef(60);
  function resize(node, next) {
    const root = node.closest(".attempt-drawer"), height = root.clientHeight;
    const controls = root.querySelector(".replay-controls")?.offsetHeight || 0;
    const actions = root.querySelector(".replay-actions")?.offsetHeight || 0;
    const min = Math.min(70, Math.ceil((controls + actions + 140) / height * 100));
    const max = Math.max(min, Math.min(85, Math.floor((height - 160) / height * 100)));
    const percent = Math.max(min, Math.min(max, next));
    root.style.setProperty("--review-video-share", `${percent}%`);
    current.current = percent;
    setLimits({ min, max });
    setValue(Math.round(percent));
  }
  useEffect(() => {
    const node = separator.current, root = node.closest(".attempt-drawer");
    const fit = () => { if (document.fullscreenElement === root) resize(node, current.current); };
    const observer = new ResizeObserver(fit);
    observer.observe(root);
    document.addEventListener("fullscreenchange", fit);
    return () => { observer.disconnect(); document.removeEventListener("fullscreenchange", fit); };
  }, []);
  function finish(event) {
    dragging.current = null;
    if (event.currentTarget.hasPointerCapture?.(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId);
  }
  return html`<div class="review-split" role="separator" tabindex="0" ref=${separator}
    aria-label="Resize gameplay and timeline" aria-orientation="horizontal"
    aria-valuemin=${limits.min} aria-valuemax=${limits.max} aria-valuenow=${value}
    title="Drag to resize gameplay and timeline; Up and Down adjust the split"
    onpointerdown=${event => {
      if (event.button !== 0) return;
      event.preventDefault(); event.stopPropagation();
      dragging.current = event.pointerId;
      event.currentTarget.setPointerCapture(event.pointerId);
    }}
    onpointermove=${event => {
      if (dragging.current !== event.pointerId) return;
      const rect = event.currentTarget.closest(".attempt-drawer").getBoundingClientRect();
      resize(event.currentTarget, (event.clientY - rect.top) / rect.height * 100);
    }} onpointerup=${finish} onpointercancel=${finish} onlostpointercapture=${finish}
    onkeydown=${event => {
      if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
      event.preventDefault(); event.stopPropagation();
      resize(event.currentTarget, event.key === "Home" ? limits.min : event.key === "End" ? limits.max
        : value + (event.key === "ArrowUp" ? -2 : 2));
    }}><span></span></div>`;
}
