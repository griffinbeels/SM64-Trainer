import { useEffect, useState } from "preact/hooks";

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
 * Callback ref for the element whose children should be capped to the
 * remaining viewport height. Re-measures on resize and on any layout change
 * that moves the element (hero wrapping, a notice appearing above it).
 *
 * A CALLBACK ref held in state, not a useRef — and that distinction is the
 * whole bug it was written to fix. Both library pages render a loading state
 * until their fetches land, so on the first mount the capped element does not
 * exist yet; a `useRef` + `[]`-deps effect reads `null`, bails, and never runs
 * again once the real DOM arrives. Keying the effect on the NODE means it runs
 * exactly when the node appears (live-verified 2026-07-25: --pane-cap was
 * unset in the running app, and the CSS fallback was doing all the work).
 */
export function usePaneCap() {
  const [element, setElement] = useState(null);
  useEffect(() => {
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
      const write = (value) => {
        if (value === lastCap) return;   // no write, so the observer can't loop
        lastCap = value;
        element.style.setProperty("--pane-cap", value);
      };
      write(`${Math.round(available)}px`);
      // Second pass: if the document STILL overflows, whatever sits below the
      // pane is bigger than we thought. Take the difference off permanently.
      // This must run even when the cap itself did not change — the overflow
      // only appears once a group is EXPANDED and the pane actually reaches
      // its cap, and an early return on an unchanged cap skipped it (live
      // 2026-07-25: 12px of overflow survived the first fix for exactly this).
      const overflow = document.documentElement.scrollHeight - window.innerHeight;
      if (overflow > 0 && available > MIN_CAP_PX) {
        spaceBelowPane += overflow;
        write(`${Math.round(Math.max(MIN_CAP_PX, available - overflow))}px`);
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
  }, [element]);
  return setElement;
}
