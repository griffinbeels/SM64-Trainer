// src/sm64_events/ui/climbcurve.js — the level-up climb's motion, as pure
// arithmetic. Import-free on purpose, the same convention as ui/entities.js
// and ui/components/caps.js, so node can execute it directly
// (tests/test_ui_climbcurve.js via tests/test_ui_climb.py).
//
// The coordinate this moves along is caps.js's `rankPosition` — tier +
// division + fill collapsed into one monotone number where 1.0 is one
// division. This file never imports it; it only ever sees a distance in
// divisions, which is what keeps it testable without a registry.
//
// WHY a trapezoid and not an ease-in-out (spec 2026-07-26-progression-
// celebration §2). The user asked for "a slow crawl, easy ease into the pace,
// and then reach a max progression speed… and then it eventually easy ease
// and slow down". An ease-in-out has no max: its peak speed is proportional
// to the distance, so a twelve-division jump would blur through the middle at
// four times the speed of a three-division one and no celebration in there
// would be readable. A trapezoid caps the middle by construction.

// ---- The two constants everything else is derived from -------------------
//
// Deriving rather than listing them is what stops the curve going incoherent
// when someone tunes it: change SHORT_MS and the ramp time, the cruise speed
// and the crossover all move together, still C¹-continuous at the join.

// A climb of `d` divisions, while short, takes SHORT_MS × sqrt(d). Square-root
// rather than linear because the reward for a bigger jump should be a longer
// climb, not a proportionally longer one — 5 divisions is a much bigger deal
// than 1, but it is not 5 times as much sitting and watching.
const SHORT_MS = 1500;
// Where the short law hands over to cruise-limited: one full tier. Not an
// arbitrary tuning number — it is the point past which a climb stops being
// "a rank-up" and starts being "a run of them", which is exactly when a speed
// ceiling starts to matter.
const CROSSOVER_DIVISIONS = 5;

// The slope of the short law AT the crossover, in ms per division — so the
// cruise phase continues at exactly the speed the short law was already
// travelling. This is what makes the two halves one curve rather than two.
const MS_PER_DIVISION = SHORT_MS / (2 * Math.sqrt(CROSSOVER_DIVISIONS));
// The fixed accelerate (and decelerate) TIME of a cruise-limited climb, again
// forced by continuity: T(crossover) must agree from both sides.
export const RAMP_MS = (SHORT_MS * Math.sqrt(CROSSOVER_DIVISIONS)) / 2;
// Divisions per second at cruise. Reported for tests and tuning; the profile
// below computes its own V so a capped climb can legitimately exceed it.
export const CRUISE_DIVISIONS_PER_S = 1000 / MS_PER_DIVISION;

// A jump big enough to hit this is climbing several tiers at once and is
// vanishingly rare; past here the climb gets FASTER rather than longer,
// because holding the UI for ten seconds stops being a reward.
const MAX_MS = 7000;
// Below this a "climb" is a few percent of one division and reads as a
// twitch rather than a move.
const MIN_MS = 450;

/** Total wall-clock for a climb of `divisions`, in ms. Monotone in distance. */
export function climbDuration(divisions) {
  const distance = Math.max(0, divisions);
  if (distance === 0) return 0;
  const raw = distance <= CROSSOVER_DIVISIONS
    ? SHORT_MS * Math.sqrt(distance)
    : RAMP_MS + distance * MS_PER_DIVISION;
  return Math.max(MIN_MS, Math.min(MAX_MS, raw));
}

/**
 * The accelerate → cruise → decelerate shape for a climb of `divisions`:
 * `rampMs` each side, `cruiseMs` between them, at `speed` divisions per ms.
 *
 * A short climb has `cruiseMs === 0` and is pure accelerate-then-decelerate —
 * which is the common one-division rank-up, and is why that case reads as
 * "crawls off the old fill, then settles" with no separate code path.
 */
export function climbProfile(divisions) {
  const distance = Math.max(0, divisions);
  const durationMs = climbDuration(distance);
  if (durationMs === 0) return { durationMs: 0, rampMs: 0, cruiseMs: 0, speed: 0, distance };
  const rampMs = Math.min(RAMP_MS, durationMs / 2);
  const cruiseMs = durationMs - 2 * rampMs;
  // Distance under one smoothstep ramp is exactly half of speed × rampMs
  // (∫₀¹ 3u²−2u³ du = ½), so the whole trip is speed × (rampMs + cruiseMs).
  return { durationMs, rampMs, cruiseMs, speed: distance / (rampMs + cruiseMs), distance };
}

// ∫₀^u of the smoothstep 3u²−2u³ — the distance covered by a ramp that is
// `u` of the way through it, as a fraction of speed × rampMs. Reaches ½ at
// u = 1, which is the half-of-a-rectangle a ramp is worth.
const rampDistance = (fraction) => fraction ** 3 - (fraction ** 4) / 2;

/**
 * How far along a climb of `divisions` we are `elapsedMs` in — in divisions
 * travelled, never overshooting `divisions` and never decreasing.
 *
 * That last property is the whole point of this feature: the bar is the
 * fractional part of the position, so a travelled distance that only ever
 * grows is a bar that can only ever fill. It is asserted directly, at 1ms
 * granularity, by tests/test_ui_climb.py.
 */
export function climbTravelled(divisions, elapsedMs) {
  const { durationMs, rampMs, cruiseMs, speed, distance } = climbProfile(divisions);
  if (durationMs === 0 || elapsedMs >= durationMs) return distance;
  if (elapsedMs <= 0) return 0;
  if (elapsedMs < rampMs) {
    return speed * rampMs * rampDistance(elapsedMs / rampMs);
  }
  const accelerated = (speed * rampMs) / 2;
  if (elapsedMs < rampMs + cruiseMs) {
    return accelerated + speed * (elapsedMs - rampMs);
  }
  const into = (elapsedMs - rampMs - cruiseMs) / rampMs;
  return accelerated + speed * cruiseMs
    + speed * rampMs * (0.5 - rampDistance(1 - into));
}

/**
 * Absolute ladder position `elapsedMs` into a climb from `from` to `to`.
 * A non-rise (`to <= from`) is not a climb and lands immediately — the app
 * never animates a regression, it just shows the new state.
 */
export function climbPosition(from, to, elapsedMs) {
  if (!(to > from)) return to;
  return from + climbTravelled(to - from, elapsedMs);
}

/** Wall-clock for the climb from `from` to `to`; 0 for a non-rise. */
export function climbDurationBetween(from, to) {
  return to > from ? climbDuration(to - from) : 0;
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
