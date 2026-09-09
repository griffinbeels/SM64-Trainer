// A loading replay may claim focus only while its opening remains the last action.
let pending = null;

export function focusReplay(node) {
  const root = node?.closest(".attempt-drawer") || node?.closest(".replay-player");
  (root?.querySelector(".input-lanes") || root?.querySelector("video"))?.focus({ preventScroll: true });
}

export function watchReplayFocus(root) {
  if (!root) return () => {};
  pending?.();
  const cancel = () => {
    observer.disconnect();
    window.removeEventListener("pointerdown", pointed, true);
    window.removeEventListener("keydown", cancel, true);
    window.removeEventListener("focusin", moved, true);
    window.removeEventListener("blur", cancel);
    document.removeEventListener("visibilitychange", cancel);
    root.removeEventListener("loadeddata", ready, true);
    if (pending === cancel) pending = null;
  };
  const moved = event => { if (!root.contains(event.target)) cancel(); };
  const pointed = event => {
    if (!root.contains(event.target) || event.target.closest("input,select,textarea,[contenteditable]")) cancel();
  };
  const ready = () => {
    const video = root.querySelector("video");
    if (!document.hasFocus() || document.visibilityState !== "visible") { cancel(); return; }
    if (!video || video.readyState < 2) return;
    if (root.querySelector(".attempt-drawer-inputs") && !root.querySelector(
      ".input-lanes, .input-timeline.is-empty, .input-timeline.is-error")) return;
    cancel();
    focusReplay(video);
  };
  // Observe only during loading, not each picture during normal playback.
  const observer = new MutationObserver(ready);
  pending = cancel;
  observer.observe(root, { childList: true, subtree: true });
  window.addEventListener("pointerdown", pointed, true);
  window.addEventListener("keydown", cancel, true);
  window.addEventListener("focusin", moved, true);
  window.addEventListener("blur", cancel);
  document.addEventListener("visibilitychange", cancel);
  root.addEventListener("loadeddata", ready, true);
  const clicked = event => {
    if (!event.target.closest("button,a,input,select,textarea,[contenteditable],video,[tabindex]"))
      focusReplay(root.querySelector("video"));
  };
  root.addEventListener("click", clicked);
  ready();
  return () => { cancel(); root.removeEventListener("click", clicked); };
}
