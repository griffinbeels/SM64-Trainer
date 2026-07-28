// src/sm64_events/ui/climbcurve.js — how long each part of a level-up climb
// lasts, and the one easing a progress bar is allowed to use. Pure arithmetic,
// import-free on purpose, the same convention as ui/climbplan.js and
// ui/components/caps.js, so node can execute it directly
// (tests/test_ui_climb.js via tests/test_ui_climb.py).
//
// This file used to own a TRAPEZOID (accelerate → cruise → decelerate) for one
// continuous sweep across the whole ladder: an ease-in-out's peak speed scales
// with distance, so a twelve-division jump blurred through its own middle and
// no celebration in there was readable. The climb is a PLAN of steps now
// (ui/climbplan.js, spec 2026-07-27-multi-rank-climb) and the bar never sweeps
// more than ONE division — it is pinned full across every rank-up in between —
// so the cruise phase became unreachable and came out. What is left is the
// short-climb law it always had at that end of the range, plus the per-step
// budgets the plan asks for.

// Every duration below is a ROW in ui/climbtuning.js rather than a constant
// here, so the inspector at /ui/tune.html can drive them (user, 2026-07-27:
// "I want to be able to tune all of the variables that determine the
// animation"). Each function takes its tuning as an argument and defaults to
// the shipped values, which keeps this module pure and node-testable and
// means a caller that knows nothing about tuning gets exactly the behaviour
// that shipped.
import { DEFAULTS } from "./climbtuning.js";

/**
 * `wanted`, kept off the floor but never pushed past the ceiling.
 *
 * THE bug this exists to kill (live report, 2026-07-27): all three durations
 * below were written `max(floor, min(ceiling, wanted))`, with the floor
 * OUTSIDE. That reads as "never shorter than the floor" — but the floor is
 * there to stop a crowded climb SQUEEZING a step into a stutter, and putting
 * it outside let it silently override the ceiling the user had just set. With
 * a ladder step of 100ms against the shipped 220ms floor, the inspector's
 * slider said 100 and every step ran 220, at every step count; the whole
 * session had been tuned against a number no control on screen was showing.
 *
 * Inside, the floor bounds only the squeeze, and a value can never come out
 * above what was asked for. At the shipped defaults (floor below ceiling in
 * all three) the two spellings agree everywhere — asserted, not assumed, in
 * tests/test_ui_climb.py.
 */
const floored = (wanted, floor, ceiling) =>
  Math.min(ceiling, Math.max(floor, wanted));

// ---- Bar sweeps ----------------------------------------------------------

/**
 * Wall-clock for a bar sweep of `divisions` (0..1), in ms.
 *
 * `full × sqrt(d)`: square-root rather than linear because the reward for a
 * bigger move should be a longer sweep, not a proportionally longer one. `d`
 * is at most 1 by construction, so at the shipped numbers this spans 450ms (a
 * twitch) to 1500ms (a whole empty division filling) — and the floor also
 * covers the arrival of a rank you only just scraped into, where the bar has
 * almost nothing to travel but the moment still has to land.
 */
export function barSweepMs(divisions, tuning = DEFAULTS) {
  const distance = Math.max(0, Math.min(1, divisions || 0));
  const full = tuning.barSweepFullMs;
  return floored(full * Math.sqrt(distance), tuning.barSweepMinMs, full);
}

/**
 * THE progress-bar easing: smoothstep, 0 → 1, monotone, landing exactly on
 * its target.
 *
 * Monotone-and-exact is a hard style rule rather than a taste (user,
 * 2026-07-27): "we should NEVER overshoot in a progress bar. It reads as
 * annoying and an error -- you gave me progress and then took it away!!!!"
 * An `easeOutBack` overshoot belongs to things that are physical objects
 * springing — the digit reel, the cap squash, both in ui/celebrations.js —
 * never to a length that reads as progress being claimed and then revoked.
 *
 * Zero velocity at both ends is the "slow crawl, easy ease into the pace …
 * and then it eventually easy ease and slow down" the original climb spec
 * asked for, and it survives the plan rewrite unchanged.
 */
export function barEase(fraction) {
  const at = Math.max(0, Math.min(1, fraction));
  return at * at * (3 - 2 * at);
}

// ---- Ladder steps: one rank-up each --------------------------------------
//
// A step is long enough for the wings to grow out and be seen (ui/
// celebrations.js's wingGrow fills exactly one step — it reads `beat.stepMs`,
// so these two can never drift apart).
//
// Steps share a budget the way tier dwells below do. A climb can hold at most
// 15 of them (≤4 climbing out of the tier you started in, ≤4 into the one you
// land in, ≤7 whole tiers passed through), and fifteen unhurried ones on top
// of eight tier dwells is a celebration nobody wants to sit through twice.

/** How long each of `steps` ladder steps gets. */
export function ladderStepMs(steps, tuning = DEFAULTS) {
  if (steps <= 0) return 0;
  return floored(tuning.ladderBudgetMs / steps,
                 tuning.ladderStepMinMs, tuning.ladderStepMs);
}

// ---- Tier crossings: the climb STOPS ------------------------------------
//
// Crossing into a new cap is the moment worth the whole feature, and cruising
// through it at 3 divisions a second threw it away (live report, 2026-07-27:
// "it needs to feel EXTRA juicy… we make it an AMAZING celebration to go from
// one division to the next"). So the climb halts at every tier boundary:
// anticipation first (the cap shaking harder and harder, squashing toward a
// flat line), then the crossing itself, then a beat to look at it.
//
// The pause is BEFORE the boundary, not after, because that is where
// anticipation belongs — the bar sits full on the last subdivision of the old
// tier while the pressure builds, and the release IS the crossing.
// A climb through eight tiers must not hold the UI for thirteen seconds, so a
// crossing gets shorter the more of them there are. This used to be a shared
// BUDGET (each = total / crossings), which is a 1/n curve nobody chose: it
// collapsed almost all of its fall-off between one crossing and three, and the
// only handles on it were a total and a floor — neither of which is the
// question being asked. The user asked the question directly (2026-07-27):
//
//   "a single climb should be the max duration… when we have, say, 7 ranks to
//    climb, it should scale down to some minimum, like 200. And then the
//    number of tiers along the way would interpolate between that min / max."
//
// So it interpolates between two ENDPOINTS over an explicit count, with a
// curve knob for how the fall-off is distributed. Every part of that sentence
// is now a control, and the budget is gone rather than left to fight it —
// two mechanisms deciding one duration is the bug this file just fixed.

/** `{anticipateMs, payoffMs}` for each of `crossings` tier boundaries. */
export function tierDwell(crossings, tuning = DEFAULTS) {
  if (crossings <= 0) return { anticipateMs: 0, payoffMs: 0 };
  // 0 at one crossing, 1 once there are `tierDwellMinAt` of them.
  const span = Math.max(1, tuning.tierDwellMinAt - 1);
  const along = Math.min(1, Math.max(0, (crossings - 1) / span));
  const each = tuning.tierDwellMs + (tuning.tierDwellMinMs - tuning.tierDwellMs)
    * along ** Math.max(0.01, tuning.tierDwellCurve);
  const anticipateMs = Math.round(each * tuning.anticipateShare);
  return { anticipateMs, payoffMs: Math.round(each) - anticipateMs };
}

/**
 * The whole timing table for one climb, in the shape `buildClimbPlan` asks
 * for. Called with the counts read off the plan's own structure, so there is
 * no second copy of "how many crossings will this have" to go stale.
 */
export function climbTimings({ crossings, ladder }, tuning = DEFAULTS) {
  return {
    barSweepMs: (divisions) => barSweepMs(divisions, tuning),
    ladderMs: ladderStepMs(ladder, tuning),
    ...tierDwell(crossings, tuning),
  };
}
