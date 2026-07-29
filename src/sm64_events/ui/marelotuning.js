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
};

export const MARELO_DEFAULTS = Object.freeze(Object.fromEntries(
  Object.entries(MARELO_TUNABLES).map(([key, row]) => [key, row.value])));

export const MARELO_GROUPS = [...new Set(
  Object.values(MARELO_TUNABLES).map((row) => row.group))];

// A tunable a caller did not name falls back to the shipped default rather
// than to undefined: a NaN duration is an animation that never ends.
export const withMareloDefaults = (values) =>
  ({ ...MARELO_DEFAULTS, ...(values || {}) });

let active = withMareloDefaults(null);
export const mareloTuning = () => active;
export function setMareloTuning(values) {
  active = withMareloDefaults(values);
  return active;
}
