// src/sm64_events/ui/offscreenloops.js — freeze endless animations nobody can see.
//
// A CSS animation that is not a transform or an opacity runs on the main
// thread: every display frame it recalculates style and repaints, whether the
// element is on screen or scrolled a page away. Measured 2026-09-27 on the UI
// fixture with a practice log of 44 rows (perf audit 2026-09-25, UI-4): the
// idle Practice page cost 53% of a renderer core at 60 Hz, 34% once only the
// loops in view kept running. His display runs at 240 Hz.
//
// LOOPS names every element that carries such a loop. One IntersectionObserver
// marks each one `data-offscreen` while it is more than MARGIN outside the
// viewport, and index.html pauses a marked element's animations (its ::before
// and ::after too). A paused animation carries on from the same frame when it
// comes back, and it resumes before it reaches the screen, so nothing he can
// see changes. A MutationObserver picks up loops as the app renders them.
// tests/test_ui_idle_animations.py fails when an endless main-thread loop is
// running that this list does not cover.
export const LOOPS = ".pb-gold, .needs-strat, .daily-star-foil, .pb-glow";
const MARGIN = "96px";

export function watchOffscreenLoops(root = document.body) {
  if (typeof IntersectionObserver === "undefined") return () => {};
  const watched = new WeakSet();
  const sight = new IntersectionObserver((entries) => {
    for (const entry of entries) entry.target.toggleAttribute("data-offscreen", !entry.isIntersecting);
  }, { rootMargin: MARGIN });
  const each = (node, fn) => {
    if (node.nodeType !== 1) return;
    if (node.matches(LOOPS)) fn(node);
    node.querySelectorAll(LOOPS).forEach(fn);
  };
  const watch = (el) => { if (!watched.has(el)) { watched.add(el); sight.observe(el); } };
  // A removed row would otherwise stay observed for the life of the page.
  const forget = (el) => { if (watched.has(el) && !el.isConnected) { watched.delete(el); sight.unobserve(el); } };
  each(root, watch);
  const changes = new MutationObserver((records) => {
    for (const record of records) {
      if (record.type === "attributes") {
        if (record.target.matches(LOOPS)) watch(record.target);
        continue;
      }
      record.removedNodes.forEach((node) => each(node, forget));
      record.addedNodes.forEach((node) => each(node, watch));
    }
  });
  changes.observe(root, { subtree: true, childList: true, attributes: true, attributeFilter: ["class"] });
  return () => { changes.disconnect(); sight.disconnect(); };
}
