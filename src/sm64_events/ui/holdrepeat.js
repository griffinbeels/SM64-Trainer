// src/sm64_events/ui/holdrepeat.js
// Press-and-hold on a step button: one step on the press, then a steady
// run of steps while it is held. His ask, 2026-08-22: "I press -1F a single
// tap, it moves back one frame; I press and hold -1F it moves multiple
// frames per second backwards. Same for the Back 1 / Forward 1 display
// under the video replay."
//
// ONE implementation for both the timeline's -1f/+1f and the player's
// Back 1/Forward 1 -- the two sets of buttons step the same clock, and two
// hold loops with two cadences would feel like two controls.
//
// Import-free, so a node test can drive the schedule with a fake clock.

export const HOLD_DELAY_MS = 350;     // a tap that lasts less than this is one step
export const HOLD_INTERVAL_MS = 66;   // ~15 steps/s once the hold has begun

// Returns the handlers to spread onto a <button>. The step fires on
// POINTERDOWN (not click) so the hold can start from the same gesture; a
// keyboard activation arrives as a click with `detail === 0` and steps once.
export function holdRepeat(step, timers = globalThis) {
  let delay = null;
  let interval = null;
  const stop = () => {
    if (delay !== null) { timers.clearTimeout(delay); delay = null; }
    if (interval !== null) { timers.clearInterval(interval); interval = null; }
  };
  return {
    onpointerdown: (event) => {
      if (event.button !== undefined && event.button !== 0) return;
      if (event.preventDefault) event.preventDefault();   // no text selection on a long press
      stop();
      step();
      delay = timers.setTimeout(() => {
        delay = null;
        interval = timers.setInterval(step, HOLD_INTERVAL_MS);
      }, HOLD_DELAY_MS);
    },
    onpointerup: stop,
    onpointerleave: stop,
    onpointercancel: stop,
    onclick: (event) => { if (event.detail === 0) step(); },
    oncontextmenu: (event) => { if (event.preventDefault) event.preventDefault(); },
  };
}
