// src/sm64_events/ui/marelotuning.js — THE registry of every number that
// decides how an OVERALL rank-up feels.
//
// Same contract as ui/climbtuning.js, and for the same reason (user,
// 2026-07-27: "like how I would work with an Inspector in Godot"). Adding a
// tunable is ONE row here plus reading it in
// ui/components/marelocelebrate.js, which is the only module that does.
//
// Import-free so node can execute it directly (tests/test_ui_marelotuning.py).
//
// A row: group / label / value (THE SHIPPED DEFAULT) / min / max / step /
// unit ("ms" | "x" | "px" | "") / why (one line, the control's tooltip).

export const MARELO_TUNABLES = {
  // ---- The flight --------------------------------------------------------
  flyOutMs: {
    group: "Flight", label: "Fly out", value: 720,
    min: 200, max: 3000, step: 20, unit: "ms",
    why: "How long the card takes to travel from the header to the centre of the screen.",
  },
  flyBackMs: {
    group: "Flight", label: "Fly back", value: 720,
    min: 200, max: 3000, step: 20, unit: "ms",
    why: "How long it takes to return. Shorter than the way out: the payoff is over and the card is going home.",
  },
  flyEaseIn: {
    group: "Flight", label: "Ease in", value: 0.24,
    min: 0, max: 1, step: 0.01, unit: "x",
    why: "First control point of the flight curve. The second is pinned at 0 so the card STARTS FROM REST -- a curve judged by watching is judged from its middle, and 35% travelled by the first frame reads as a hop, not a launch.",
  },
  flyEaseOut: {
    group: "Flight", label: "Ease out", value: 0.8,
    min: 0, max: 1, step: 0.01, unit: "x",
    why: "Third control point. The fourth is pinned at 1 so the card COMES TO REST rather than stopping while still moving.",
  },
  centreScale: {
    group: "Flight", label: "Centre size", value: 2,
    min: 1, max: 5, step: 0.05, unit: "x",
    why: "How much bigger the card is at the centre than in the header. This is most of what makes an overall rank-up outrank a star's.",
  },

  // ---- The beats ---------------------------------------------------------
  beforeHoldMs: {
    group: "Beats", label: "Hold the BEFORE rank", value: 140,
    min: 0, max: 2000, step: 20, unit: "ms",
    why: "How long the card sits at the rank you HAD once it reaches the centre, before climbing. Without it there is no before-state to animate from.",
  },
  holdMs: {
    group: "Beats", label: "Hold the new rank", value: 3250,
    min: 0, max: 8000, step: 50, unit: "ms",
    why: "How long the finished rank stays at the centre. Click dismisses early.",
  },
  divisionScale: {
    group: "Beats", label: "Division-up · share of a tier-up", value: 0.55,
    min: 0.1, max: 1, step: 0.05, unit: "x",
    why: "A division-up runs the SAME sequence at this fraction of the flight and hold times. One implementation, one named difference.",
  },

  // ---- The backdrop ------------------------------------------------------
  backdropOpacity: {
    group: "Backdrop", label: "Backdrop darkness", value: 0.8,
    min: 0, max: 1, step: 0.05, unit: "x",
    why: "How far the page behind the card is dimmed. 0 removes the backdrop entirely.",
  },
  backdropTint: {
    group: "Backdrop", label: "Backdrop tint", value: 0.74,
    min: 0, max: 1, step: 0.02, unit: "x",
    why: "How much of the rank's own colour is mixed into the backdrop. It cross-fades with the tier during the climb and must never vanish -- a surface that goes away mid-transition is a second bug, not a fix for the first.",
  },
  // Report 1 (2026-07-28): the backdrop's OWN colour transition, separate
  // from --fly-ms -- that variable paces the FLIGHT (the card's transform,
  // the backdrop's opacity), and a tier crossing can land mid-climb, long
  // after the flight itself has finished moving. Reusing --fly-ms would tie
  // "how fast a colour crosses over" to "how fast the card travels" for no
  // reason other than the numbers happening to be similar today.
  tierFadeMs: {
    group: "Backdrop", label: "Tier colour cross-fade", value: 500,
    min: 100, max: 2000, step: 20, unit: "ms",
    why: "How long the backdrop takes to ease into a new tier's colour when the climb crosses one, so a crossing eases rather than cuts.",
  },

  // An "Ambient tint" group (tintStrength / tintCrossfadeMs) lived here for
  // part of 2026-07-28, backing an always-on `body::after` layer, and was
  // deleted the same day: "It should NOT be tinted by default. It should only
  // tint during the animation" (user). The page's only tint is the
  // celebration BACKDROP, whose own colour walk is `tierFadeMs` above.

  // ---- Intensity ---------------------------------------------------------
  tierAmplify: {
    group: "Intensity", label: "Tier beats amplified", value: 1.2,
    min: 1, max: 3, step: 0.05, unit: "x",
    why: "Multiplies the climb's own flash, shake and burst for THIS surface only, so the overall rank-up reads as bigger than the star banner running the identical registry.",
  },
  // A `shakePx` row lived here until 2026-07-28 and was DELETED rather than
  // shipped at 0. It wrote `--shake-px` and no CSS rule read it, so it did
  // nothing at ANY value -- which is not "off by default", it is a broken
  // control, and dragging it to 24px would have been indistinguishable from
  // dragging it to 0. It satisfied `test_every_tunable_is_actually_read`
  // because that guard only sees the JS side; the CSS-consumer guard beside
  // it exists because of this row. Screen shake is a real feature someone can
  // add later, with a frame trace proving it moves.

  // ---- The route SWAP -----------------------------------------------------
  //
  // User, 2026-07-28: "when we swap to a different route, since sometimes we
  // change ranks, we should reuse the squash / pop animation for swapping
  // between the different routes... This should all happen with the same
  // timing." A swap is an EXCHANGE, never a climb -- ui/routeswap.js is the
  // reader (not marelocelebrate.js, which owns the FLIGHT overlay for an
  // EARNED rank-up, a different event entirely). Only three rows: the squash
  // depth and the exchange point are this surface's own knobs; the widen/
  // burst-floor/overshoot shape is borrowed straight from climbtuning.js's
  // own Wind-up/Burst rows (ui/celebrations.js's tierAnticipate/tierBurst,
  // called directly) so the swap squashes like the same material rather than
  // a second hand-tuned version of it -- "no second implementation".
  swapMs: {
    group: "Swap", label: "Swap duration", value: 460,
    min: 120, max: 2000, step: 10, unit: "ms",
    why: "Total time for the icon to squash down and pop back up as the new route's rank -- the same clock the rank text, route name and progress bar all move on.",
  },
  swapSquash: {
    group: "Swap", label: "Squash depth", value: 0.82,
    min: 0, max: 0.99, step: 0.01, unit: "x",
    why: "How flat the icon gets at the midpoint, where it swaps to the new route's rank. Lighter than a tier crossing's own squash -- a scope change is not an achievement.",
  },
  swapExchangeAt: {
    group: "Swap", label: "Exchange point", value: 0.5,
    min: 0.1, max: 0.9, step: 0.05, unit: "x",
    why: "How far through the swap the flattest frame lands -- where the icon, the rank text, the route name and the bar all cross from the old route to the new one.",
  },
};

export const MARELO_DEFAULTS = Object.freeze(Object.fromEntries(
  Object.entries(MARELO_TUNABLES).map(([key, row]) => [key, row.value])));

export const MARELO_GROUPS = [...new Set(
  Object.values(MARELO_TUNABLES).map((row) => row.group))];

// A tunable a caller did not name falls back to the shipped default rather
// than to undefined: a NaN duration is an animation that never ends.
// Unknown keys are DROPPED, never carried through -- the same rule
// climbtuning.js's `decodeTuning` already states: "they are either a typo or a
// tunable that has since been codified away, and both want the current default
// rather than a value nothing reads."
//
// This registry did not have it, and the omission shipped a real bug the day
// two rows were deleted (2026-07-28). The inspector persists its working draft
// to localStorage, so a browser that had touched the page BEFORE the deletion
// kept `tintStrength`/`tintCrossfadeMs` in that draft forever. They survived
// the spread, were reported as "undefined -> 0.14" in the changed-from-shipped
// list, and then went to the server on SAVE, where rewrite_defaults correctly
// refused a key that no longer exists in the file -- so an unrelated,
// legitimate one-value save failed with an error naming a tunable the user had
// never heard of. Filtering here fixes it for every consumer at once rather
// than only on the load path, since the draft is re-read on every mount.
export const withMareloDefaults = (values) =>
  ({ ...MARELO_DEFAULTS,
     ...Object.fromEntries(Object.entries(values || {})
       .filter(([key]) => key in MARELO_DEFAULTS)) });

let active = withMareloDefaults(null);
export const mareloTuning = () => active;
export function setMareloTuning(values) {
  active = withMareloDefaults(values);
  return active;
}
