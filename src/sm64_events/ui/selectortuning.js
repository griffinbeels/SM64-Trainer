// src/sm64_events/ui/selectortuning.js — THE registry of every number that
// decides how the practice selector EXCHANGES one set of options for another.
//
// Live report 2026-08-02: "when we invalidate / add / remove cards from the
// menu here, it feels more like a bug / error than intentional… they should see
// their old options fade away, and their new options appear, no intermediate."
// Internally a single walk through a door can change the set three or four
// times within ~100 ms (the level edge, the target retiring, several
// topological cancels landing on the frame heartbeat, then a view refetch).
// Every one of those was a repaint, and a row of cards reshuffling four times
// in a tenth of a second is the flicker he is describing.
//
// Same contract as ui/climbtuning.js and ui/marelotuning.js, and for the same
// reason (user, 2026-07-27: "like how I would work with an Inspector in
// Godot"). Adding a tunable is ONE row here plus reading it in ui/exchange.js,
// which is the only module that does.
//
// Import-free so node can execute it directly (tests/test_ui_selectortuning.py).
//
// A row: group / label / value (THE SHIPPED DEFAULT) / min / max / step /
// unit ("ms" | "x" | "px" | "") / why (one line, the control's tooltip).

export const SELECTOR_TUNABLES = {
  outMs: {
    group: "Exchange", label: "Old options fade out", value: 150,
    min: 0, max: 1200, step: 10, unit: "ms",
    why: "How long the set he was looking at takes to reach zero. This is ALSO the window every further change is absorbed into, so it is what makes an intermediate set unobservable rather than merely unlikely — shorten it and a burst of validations can outrun it.",
  },
  gapMs: {
    group: "Exchange", label: "Beat between them", value: 60,
    min: 0, max: 800, step: 10, unit: "ms",
    why: "Empty time after the old set is gone and before the new one starts arriving. His wording is sequential — 'old options fade away, and then their new options appear' — and a beat is what reads as one deliberate swap instead of a crossfade.",
  },
  inMs: {
    group: "Exchange", label: "New options appear", value: 220,
    min: 0, max: 1200, step: 10, unit: "ms",
    why: "How long the replacement set takes to come up. Longer than the fade out: leaving is not an event, arriving is.",
  },
  maxHoldMs: {
    group: "Exchange", label: "Longest it may stay hidden", value: 900,
    min: 0, max: 4000, step: 50, unit: "ms",
    why: "Every answer arriving mid-fade restarts the wait, so the row stays hidden until it has stopped changing — his rule: figure out the result while it is invisible, then show the final one, so the animation only happens once. This is the ceiling on that. Past it the next answer is shown immediately: a window that can be held open forever is a selector that never comes back.",
  },
};

export const SELECTOR_DEFAULTS = Object.freeze(Object.fromEntries(
  Object.entries(SELECTOR_TUNABLES).map(([key, row]) => [key, row.value])));

export const SELECTOR_GROUPS = [...new Set(
  Object.values(SELECTOR_TUNABLES).map((row) => row.group))];

// A tunable a caller did not name falls back to the shipped default rather than
// to undefined: a NaN duration is a timer that never fires, which here means a
// row stuck invisible. Unknown keys are DROPPED — the inspector persists its
// working draft to localStorage, so a browser that touched the page before a
// row was renamed would otherwise carry the dead key into SAVE and fail a
// legitimate write with an error naming a tunable he has never heard of
// (marelotuning.js paid for that one on 2026-07-28).
export const withSelectorDefaults = (values) =>
  ({ ...SELECTOR_DEFAULTS,
     ...Object.fromEntries(Object.entries(values || {})
       .filter(([key]) => key in SELECTOR_DEFAULTS)) });

let active = withSelectorDefaults(null);
export const selectorTuning = () => active;
export function setSelectorTuning(values) {
  active = withSelectorDefaults(values);
  return active;
}
