// src/sm64_events/ui/holdrepeat.js
// Press-and-hold on a step button: one step on the press, then a steady
// run of steps while it is held, and nothing after release. His rule,
// 2026-08-23: "If the user clicks a single time, it advances by a single
// frame. If the user presses and holds after, say, a 0.25 second debounce
// period, then it starts going backwards / forwards on a loop UNTIL the
// user releases their button, then continues playing back as expected."
//
// THE HOLD'S STATE LIVES ON THE ELEMENT, never in a closure. The first
// version kept its timers in the closure `holdRepeat()` returned -- and a
// component calls it on every render, so the press landed in one closure
// and the release in a fresh one that had nothing to clear. A tap's delay
// timer then fired anyway ("triggers the Hold action prematurely") and a
// held run could not be stopped ("it doesn't STOP GOING BACKWARDS"). The
// element outlives every render, so whichever handlers are current can
// always find the run that is going.
//
// `resume` says what to do on release: a video that was playing when the
// press began plays again; one that was paused stays where the steps put
// it. "Continues playing back as expected" is that, and a single click on a
// paused clip stays a single frame.
//
// Import-free, so a node test drives the schedule with a fake clock.

export const HOLD_DELAY_MS = 250;     // a press shorter than this is one step
export const HOLD_INTERVAL_MS = 66;   // ~15 steps/s once the hold has begun

const STATE = "__holdRepeat";

function stopOn(element, timers) {
  const state = element[STATE];
  if (!state) return;
  if (state.delay !== null) timers.clearTimeout(state.delay);
  if (state.interval !== null) timers.clearInterval(state.interval);
  element[STATE] = null;
  if (state.resume) state.resume();
}

// Returns the handlers to spread onto a <button>. The step fires on
// POINTERDOWN (not click) so the hold can start from the same gesture; a
// keyboard activation arrives as a click with `detail === 0` and steps once.
// `onPress` runs once at the start of a press and may return a function to
// run on release (the resume).
export function holdRepeat(step, { onPress = null, timers = globalThis } = {}) {
  const stop = (event) => stopOn(event.currentTarget, timers);
  return {
    onpointerdown: (event) => {
      if (event.button !== undefined && event.button !== 0) return;
      if (event.preventDefault) event.preventDefault();   // no text selection on a long press
      const element = event.currentTarget;
      stopOn(element, timers);
      // The release can land off the button; capture makes it ours anyway.
      if (element.setPointerCapture && event.pointerId !== undefined) {
        try { element.setPointerCapture(event.pointerId); } catch (_) { /* not capturable */ }
      }
      const resume = onPress ? onPress() : null;
      const state = { delay: null, interval: null, resume };
      element[STATE] = state;
      step();
      state.delay = timers.setTimeout(() => {
        state.delay = null;
        state.interval = timers.setInterval(step, HOLD_INTERVAL_MS);
      }, HOLD_DELAY_MS);
    },
    onpointerup: stop,
    onpointerleave: stop,          // only reachable when capture was refused
    onpointercancel: stop,
    onlostpointercapture: stop,
    onclick: (event) => { if (event.detail === 0) step(); },
    oncontextmenu: (event) => { if (event.preventDefault) event.preventDefault(); },
  };
}
