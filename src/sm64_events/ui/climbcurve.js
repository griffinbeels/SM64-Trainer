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

// ---- Bar sweeps ----------------------------------------------------------

// A sweep of `d` divisions takes SHORT_MS × sqrt(d). Square-root rather than
// linear because the reward for a bigger move should be a longer sweep, not a
// proportionally longer one. `d` is at most 1 by construction, so this spans
// 450ms (a twitch) to 1500ms (a whole empty division filling).
const SHORT_MS = 1500;
// Below this a sweep is a few percent of a division and reads as a flicker
// rather than a move. Also the floor for the arrival of a rank you only just
// scraped into, where the bar has almost nothing to travel but the moment
// still has to land.
const MIN_MS = 450;

/** Wall-clock for a bar sweep of `divisions` (0..1), in ms. */
export function barSweepMs(divisions) {
  const distance = Math.max(0, Math.min(1, divisions || 0));
  return Math.max(MIN_MS, Math.min(SHORT_MS, SHORT_MS * Math.sqrt(distance)));
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
// Long enough for the wings to grow out and be seen (ui/celebrations.js's
// wingGrow fills exactly one step — it reads `beat.stepMs`, so these two can
// never drift apart).
const LADDER_STEP_MS = 460;
// Steps share a budget the way tier dwells below do. A climb can hold at most
// 15 of them (≤4 climbing out of the tier you started in, ≤4 into the one you
// land in, ≤7 whole tiers passed through), and fifteen unhurried ones on top
// of eight tier dwells is a celebration nobody wants to sit through twice.
const LADDER_BUDGET_MS = 3400;
const MIN_LADDER_STEP_MS = 220;

/** How long each of `steps` ladder steps gets. */
export function ladderStepMs(steps) {
  if (steps <= 0) return 0;
  return Math.max(MIN_LADDER_STEP_MS,
                  Math.min(LADDER_STEP_MS, LADDER_BUDGET_MS / steps));
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
const ANTICIPATE_SHARE = 0.56;
const FULL_DWELL_MS = 1600;
// A climb through eight tiers must not hold the UI for thirteen seconds, so
// the dwells share a budget. One crossing gets the full treatment; the rare
// multi-tier run trades length for pace, down to a floor that still reads as
// a pause rather than a stutter.
const DWELL_BUDGET_MS = 5200;
const MIN_DWELL_MS = 700;

/** `{anticipateMs, payoffMs}` for each of `crossings` tier boundaries. */
export function tierDwell(crossings) {
  if (crossings <= 0) return { anticipateMs: 0, payoffMs: 0 };
  const each = Math.max(MIN_DWELL_MS,
                        Math.min(FULL_DWELL_MS, DWELL_BUDGET_MS / crossings));
  const anticipateMs = Math.round(each * ANTICIPATE_SHARE);
  return { anticipateMs, payoffMs: Math.round(each) - anticipateMs };
}

/**
 * The whole timing table for one climb, in the shape `buildClimbPlan` asks
 * for. Called with the counts read off the plan's own structure, so there is
 * no second copy of "how many crossings will this have" to go stale.
 */
export function climbTimings({ crossings, ladder }) {
  return { barSweepMs, ladderMs: ladderStepMs(ladder), ...tierDwell(crossings) };
}
