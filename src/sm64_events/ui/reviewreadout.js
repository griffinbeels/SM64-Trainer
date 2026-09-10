import { h, render } from "preact";
import { useLayoutEffect, useRef } from "preact/hooks";

// A single readout tree moves between its normal position and the fullscreen
// controls. Its container is owned here, outside the parent's reconciled DOM.
export function ReviewReadout({ children }) {
  const anchor = useRef(null), container = useRef(null);
  useLayoutEffect(() => {
    const node = document.createElement("div");
    node.className = "review-readout";
    container.current = node;
    const place = () => {
      const drawer = anchor.current.closest(".attempt-drawer");
      const player = drawer?.querySelector(".replay-player");
      const fullscreen = document.fullscreenElement === drawer && !!player;
      (fullscreen ? player : anchor.current).appendChild(node);
      node.classList.toggle("is-docked", fullscreen);
    };
    place();
    document.addEventListener("fullscreenchange", place);
    return () => {
      document.removeEventListener("fullscreenchange", place);
      render(null, node); node.remove(); container.current = null;
    };
  }, []);
  useLayoutEffect(() => { render(children, container.current); });
  return h("div", { ref: anchor, class: "review-readout-anchor" });
}
