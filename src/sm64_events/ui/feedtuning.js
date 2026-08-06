// src/sm64_events/ui/feedtuning.js — THE registry of every number that decides
// how the practice log MOVES: a new card arriving in the feed, the cards it
// pushes down, and any disclosure opening or closing.
//
// Griffin, 2026-08-05: "I want to figure out how to animate all of this.
// Specifically, we need to animate (1) the newest practice entry card
// appearing, (2) the other cards moving down. I think this is basically just a
// feed... It should feel like the card is a physical object entering the
// space, occupied by the other physical objects we're moving." And, separately:
// "a better animation for opening the card... animating the size of the
// containing card and interpolating between the beginning and end positions,
// rather than it just appearing. It needs to be pretty snappy... Same idea for
// the rank standards dropdown (I think we may need a general way to animate
// drop downs opening like this with best practices?)."
//
// He also said, in the same breath, "I'm being intentionally vague because I'm
// not too sure what would look best here" — which is precisely why this file
// exists before any of the values in it are defended. The rig is the
// deliverable; the numbers are its output (`.claude/skills/tuning-demo`, and
// his own 2026-07-27 ruling: "like how I would work with an Inspector in
// Godot").
//
// Same contract as climbtuning.js / marelotuning.js / selectortuning.js.
// Adding a tunable is ONE row here plus reading it in ui/disclosure.js, which
// is the only module that does. Import-free so node can execute it directly
// (tests/test_ui_feedtuning.py).
//
// ONE CURVE FOR EVERY DISPLACEMENT, deliberately. A card arriving and a card
// being pushed down are the same physical event seen from two sides, and two
// curves is how they end up disagreeing about it. Two of his standing rulings
// are already numbers here rather than prose: "we NEVER want to abruptly stop"
// (so the curve's exit must decelerate), and a displacement judged at 16ms
// rather than from its middle — "it should just start moving towards its new
// destination", after a curve that was 35% travelled by the first frame read
// as a jump (2026-07-27). `c1y: 0` is what "starts moving" means as a number.
//
// A row: group / label / value (THE SHIPPED DEFAULT) / min / max / step /
// unit ("ms" | "px" | "") / why (one line, the control's tooltip).

export const FEED_TUNABLES = {
  // --- a card arriving, and the cards it displaces -------------------------
  enterMs: {
    group: "Feed", label: "New card arrives", value: 320,
    min: 0, max: 1500, step: 10, unit: "ms",
    why: "How long the newest entry takes to settle into place. It is the event; the cards it pushes are the consequence, so this is the one duration everything else is judged against.",
  },
  enterRisePx: {
    group: "Feed", label: "How far it travels", value: 18,
    min: 0, max: 160, step: 1, unit: "px",
    why: "Distance the arriving card covers on its way in. Small reads as a card settling into a slot; large reads as a card thrown at the list. Zero makes it a pure fade, which is the thing he said feels like a bug rather than an object.",
  },
  enterFadeMs: {
    group: "Feed", label: "...and fades in over", value: 200,
    min: 0, max: 1500, step: 10, unit: "ms",
    why: "Shorter than the travel on purpose: a physical object is fully opaque before it stops moving. Equal to the travel and it reads as a ghost sliding in.",
  },
  shiftMs: {
    group: "Feed", label: "Cards below move down", value: 320,
    min: 0, max: 1500, step: 10, unit: "ms",
    why: "How long the displaced cards take to reach their new positions. Matching the arrival makes it ONE gesture; differing makes it two things happening near each other, which is exactly the reading he rejected on the rank climb.",
  },
  shiftStaggerMs: {
    group: "Feed", label: "Stagger down the list", value: 14,
    min: 0, max: 200, step: 1, unit: "ms",
    why: "Extra delay per card further down. A little makes the push feel transmitted through the stack rather than applied to all of it at once; too much and the list ripples like liquid instead of moving like objects.",
  },

  // --- any disclosure opening or closing -----------------------------------
  openMs: {
    group: "Disclosure", label: "Open", value: 260,
    min: 0, max: 1500, step: 10, unit: "ms",
    why: "Card body or dropdown expanding from its collapsed height to its natural one. His bound is 'pretty snappy', so this is the number to push down until the interpolation stops reading and it snaps.",
  },
  closeMs: {
    group: "Disclosure", label: "Close", value: 200,
    min: 0, max: 1500, step: 10, unit: "ms",
    why: "Shorter than opening. Leaving is not an event; arriving is — the same asymmetry the selector exchange already ships.",
  },
  contentFadeMs: {
    group: "Disclosure", label: "Contents fade in over", value: 0,
    min: 0, max: 1000, step: 10, unit: "ms",
    why: "SHIPPED AT ZERO, on his own description of what this should feel like: 'it should feel like the text below is getting uncovered by the animation' (2026-08-05). Uncovered IS a moving edge over fully-opaque contents, which is what no fade means. Raise it and the contents APPEAR inside a growing box instead; the two read very differently, and the first version of this shipped at 140 and was the wrong one.",
  },
  contentFadeDelayMs: {
    group: "Disclosure", label: "...starting after", value: 0,
    min: 0, max: 800, step: 10, unit: "ms",
    why: "How long the box grows alone before its contents start appearing. This is the difference between 'it opened' and 'something appeared'.",
  },

  // --- the shared displacement curve --------------------------------------
  c1x: {
    group: "Curve", label: "Control 1 · x", value: 0.2,
    min: 0, max: 1, step: 0.01, unit: "",
    why: "First cubic-bezier handle, horizontal. Together with the three below this is the ONE curve every displacement uses — the arrival, the push down, and both directions of every disclosure.",
  },
  c1y: {
    group: "Curve", label: "Control 1 · y", value: 0,
    min: 0, max: 1.4, step: 0.01, unit: "",
    why: "First handle, vertical, and the single most consequential number here. Above zero the motion is already partway travelled on its FIRST frame, which he reported as a jump rather than a slide (2026-07-27, measured at 35% by 16ms). Zero is what 'starts moving toward its destination' means as a number.",
  },
  c2x: {
    group: "Curve", label: "Control 2 · x", value: 0.2,
    min: 0, max: 1, step: 0.01, unit: "",
    why: "Second handle, horizontal. Pulls the deceleration earlier or later in the run.",
  },
  c2y: {
    group: "Curve", label: "Control 2 · y", value: 1,
    min: 0, max: 1.4, step: 0.01, unit: "",
    why: "Second handle, vertical. At 1 the motion arrives already at rest, which is his standing rule — 'we NEVER want to abruptly stop'. Below 1 it is still moving when it lands, and he detects that at a 1.2% scale change lasting a single frame.",
  },
};

export const FEED_DEFAULTS = Object.freeze(Object.fromEntries(
  Object.entries(FEED_TUNABLES).map(([key, row]) => [key, row.value])));

export const FEED_GROUPS = [...new Set(
  Object.values(FEED_TUNABLES).map((row) => row.group))];

// A tunable a caller did not name falls back to the shipped default rather
// than to undefined: a NaN duration is an animation that never ends, which
// here means a card stuck mid-flight or a body stuck at height 0. Unknown keys
// are DROPPED — the inspector persists its working draft to localStorage, so a
// browser that touched the page before a row was renamed would otherwise carry
// the dead key into SAVE and fail a legitimate write with an error naming a
// tunable he has never heard of (marelotuning.js paid for that on 2026-07-28).
export const withFeedDefaults = (values) =>
  ({ ...FEED_DEFAULTS,
     ...Object.fromEntries(Object.entries(values || {})
       .filter(([key]) => key in FEED_DEFAULTS)) });

let active = withFeedDefaults(null);
export const feedTuning = () => active;
export function setFeedTuning(values) {
  active = withFeedDefaults(values);
  return active;
}
