import { useEffect, useRef } from "preact/hooks";

// MEASURE the space a pane has left instead of guessing it.
//
// The workshop panes are capped so the page itself never scrolls (a page
// scrollbar steals width and shifts the whole layout sideways). That cap used
// to be `calc(100vh - 205px)` — a constant standing in for "context bar + hero
// + gaps above me", which has now been wrong three times: the card bottom ran
// off-screen (2026-07-24), then the page still gained a scrollbar (2026-07-25,
// twice). It cannot be right in general: the hero wraps at narrow widths, the
// context bar reflows, browser chrome and OS zoom change the viewport, and the
// desktop shell has no chrome at all.
//
// So: measure the pane's own distance from the top of the document and publish
// what's left as `--pane-cap`. Custom properties inherit, so setting it on the
// workshop grid caps every pane inside it — one call site per page.
//
// The CSS keeps `calc(100vh - 205px)` as the fallback, so a pane is still
// bounded (just conservatively) before this runs or if it never does.

const BOTTOM_GAP_PX = 16;   // breathing room under the pane, matches the gutter
const MIN_CAP_PX = 240;     // never collapse a pane to nothing on a tiny window

/**
 * Ref for the element whose children should be capped to the remaining
 * viewport height. Re-measures on resize and on any layout change that moves
 * the element (hero wrapping, a notice appearing above it).
 */
export function usePaneCap() {
  const ref = useRef(null);
  useEffect(() => {
    const element = ref.current;
    if (!element || typeof ResizeObserver === "undefined") return undefined;
    let lastCap = null;
    // Space consumed BELOW the pane — the workspace's bottom padding and any
    // page margin. Learned rather than assumed: measured 28px here, but it is
    // exactly the kind of number that changes with a layout tweak, which is
    // how the constant this file replaces went wrong three times. It only ever
    // grows, so the correction cannot oscillate (recomputing the ideal cap
    // every pass and re-subtracting would shrink, then grow, then shrink).
    let spaceBelowPane = BOTTOM_GAP_PX;
    const applyCap = () => {
      // Document-relative top, so the measurement does not depend on how far
      // the page happens to be scrolled while we correct it.
      const documentTop = element.getBoundingClientRect().top + window.scrollY;
      const available = Math.max(
        MIN_CAP_PX, window.innerHeight - documentTop - spaceBelowPane);
      const cap = `${Math.round(available)}px`;
      if (cap === lastCap) return;   // no write, so the observer can't loop
      lastCap = cap;
      element.style.setProperty("--pane-cap", cap);
      // Second pass: if the document STILL overflows, whatever sits below the
      // pane is bigger than we thought. Take the difference off permanently.
      const overflow = document.documentElement.scrollHeight - window.innerHeight;
      if (overflow > 0 && available > MIN_CAP_PX) {
        spaceBelowPane += overflow;
        lastCap = `${Math.round(Math.max(MIN_CAP_PX, available - overflow))}px`;
        element.style.setProperty("--pane-cap", lastCap);
      }
    };
    applyCap();
    window.addEventListener("resize", applyCap);
    const observer = new ResizeObserver(applyCap);
    observer.observe(document.body);
    return () => {
      window.removeEventListener("resize", applyCap);
      observer.disconnect();
    };
  }, []);
  return ref;
}
