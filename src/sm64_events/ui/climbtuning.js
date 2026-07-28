// src/sm64_events/ui/climbtuning.js — THE registry of every number that
// decides how a rank-up climb FEELS.
//
// Why a registry rather than constants in the four modules that use them
// (user, 2026-07-27): "for any animations / any 'feel' based things like this
// in the future… like how I would work with an Inspector in Godot, I want to
// be able to tune all of the variables that determine the animation, get it to
// how it feels good for ME, and then tell you what we want to codify."
//
// So every value below is (a) the SHIPPED default, exactly what the constant
// it replaced said, and (b) a row the inspector at /ui/tune.html renders a
// control for with no code of its own. Adding a tunable is ONE row here plus
// reading it where it belongs — the same contract ui/celebrations.js has for
// adding an effect, and for the same reason.
//
// Import-free, like its neighbours climbcurve.js / climbplan.js / caps.js, so
// node can execute it directly (tests/test_ui_climbtuning.py).
//
// ---- A row ---------------------------------------------------------------
//
//   group   which section of the inspector it appears under
//   label   what the control is called
//   value   THE SHIPPED DEFAULT. With every default in force the climb must be
//           byte-identical to the one with no tuning system at all, which is
//           what tests/test_ui_climbtuning.py asserts against the real plan.
//   min/max/step  the control's range. Deliberately wider than anything
//           sensible: this is a tuning rig, and a value has to be pushed past
//           good to find out where good was.
//   unit    "ms" | "x" | "" — display only.
//   why     one line, shown as the control's tooltip. If a number cannot be
//           explained in one line it is probably two numbers.

export const TUNABLES = {
  // ---- Pace: how long each kind of step lasts (ui/climbcurve.js) ---------
  barSweepFullMs: {
    group: "Pace", label: "Bar sweep · full division", value: 900,
    min: 100, max: 4000, step: 10, unit: "ms",
    why: "How long the bar takes to travel a WHOLE division. Shorter sweeps scale by sqrt of the distance.",
  },
  barSweepMinMs: {
    group: "Pace", label: "Bar sweep · floor", value: 200,
    min: 0, max: 1500, step: 10, unit: "ms",
    why: "Shortest any bar sweep may be. Below this a few percent of a division reads as a flicker.",
  },
  ladderStepMs: {
    group: "Pace", label: "Ladder step", value: 100,
    min: 60, max: 1500, step: 10, unit: "ms",
    why: "One rank-up with the bar pinned full — also how long a whole condensed tier gets.",
  },
  ladderBudgetMs: {
    group: "Pace", label: "Ladder budget", value: 1000,
    min: 500, max: 12000, step: 100, unit: "ms",
    why: "Total shared by every ladder step in one climb. A climb with many steps shortens each.",
  },
  ladderStepMinMs: {
    group: "Pace", label: "Ladder step · floor", value: 100,
    min: 40, max: 800, step: 10, unit: "ms",
    why: "Shortest a ladder step may be squeezed to. Below this a rank-up reads as a stutter.",
  },
  tierDwellMs: {
    group: "Pace", label: "Tier crossing · crossing one tier", value: 650,
    min: 100, max: 5000, step: 50, unit: "ms",
    why: "Build-up plus payoff when the climb crosses ONE tier — the biggest moment in the feature, and the longest a crossing ever gets.",
  },
  tierDwellMinMs: {
    group: "Pace", label: "Tier crossing · crossing many", value: 200,
    min: 50, max: 2500, step: 25, unit: "ms",
    why: "What each crossing shortens to once the climb is crossing 'many' tiers. Reached at the fall-off count below.",
  },
  tierDwellMinAt: {
    group: "Pace", label: "Tier crossing · fall-off count", value: 7,
    min: 2, max: 8, step: 1, unit: "",
    why: "How many tier crossings it takes to reach the short duration. Anything between one and this interpolates; the ladder tops out at 8.",
  },
  finalTierOverlap: {
    group: "Pace", label: "Final tier · ladder overlap", value: 0.6,
    min: 0, max: 1, step: 0.05, unit: "x",
    why: "When the climb lands in the tier it FINISHES in, how much of that crossing's release the ladder climb plays over the top of. 0 waits for the crossing to finish first; 1 starts the ladder the instant the new cap appears. A tier the climb only passes through is never overlapped — it has no ladder of its own.",
  },
  tierDwellCurve: {
    group: "Pace", label: "Tier crossing · fall-off curve", value: 1,
    min: 0.2, max: 4, step: 0.05, unit: "",
    why: "1 is a straight line from long to short. Below 1 shortens hard on the first extra tier and levels off; above 1 stays long and drops late.",
  },
  anticipateShare: {
    group: "Pace", label: "Build-up share of crossing", value: 0.1,
    min: 0.1, max: 0.9, step: 0.01, unit: "x",
    why: "How much of a tier crossing is the wind-up rather than the release. Anticipation is what makes the release land.",
  },
  laneGapMs: {
    group: "Pace", label: "Gap between the two banners", value: 0,
    min: 0, max: 1500, step: 10, unit: "ms",
    why: "The strategy rank climbs, then the star's. This is the pause between them.",
  },

  // ---- The crossing flash ------------------------------------------------
  levelFlashMs: {
    group: "Flash", label: "Flash length", value: 1500,
    min: 0, max: 1500, step: 10, unit: "ms",
    why: "A bloom on the bar and the rank name that decays rather than a class that has to be taken off again.",
  },
  levelFlashTier: {
    group: "Flash", label: "Flash · tier crossing", value: 2,
    min: 0, max: 2, step: 0.05, unit: "x",
    why: "How bright the flash is when a whole new cap is reached.",
  },
  levelFlashDivision: {
    group: "Flash", label: "Flash · division", value: 1,
    min: 0, max: 2, step: 0.05, unit: "x",
    why: "How bright the flash is for an ordinary rank-up inside a tier.",
  },

  // ---- The digit reel ----------------------------------------------------
  digitRollMs: {
    group: "Digit", label: "Digit roll", value: 200,
    min: 0, max: 1500, step: 10, unit: "ms",
    why: "The numeral sliding to the next one. Never longer than the step it belongs to.",
  },
  digitRollOvershoot: {
    group: "Digit", label: "Digit overshoot", value: 0,
    min: 0, max: 5, step: 0.05, unit: "x",
    why: "How far past its slot the digit travels before settling — a slot reel, not a slide. 0 removes the bounce.",
  },

  // ---- Wings -------------------------------------------------------------
  wingGrowScale: {
    group: "Wings", label: "Wing grow · share of step", value: 1,
    min: 0.1, max: 3, step: 0.05, unit: "x",
    why: "The grow fills this much of its own step, so it stays in time however hard a long climb compresses things.",
  },
  wingFlapMs: {
    group: "Wings", label: "Wing flap", value: 1500,
    min: 0, max: 4000, step: 20, unit: "ms",
    why: "One flap cycle after the wings finish growing.",
  },
  wingFlapDelayScale: {
    group: "Wings", label: "Flap starts at · share of step", value: 0.5,
    min: 0, max: 3, step: 0.05, unit: "x",
    why: "When the flap begins, as a share of the step. 1 means exactly where the grow ends.",
  },

  // ---- Tier anticipation: the wind-up ------------------------------------
  anticipateSquash: {
    group: "Wind-up", label: "Squash depth", value: 0.99,
    min: 0, max: 0.99, step: 0.01, unit: "x",
    why: "How flat the cap gets before it springs. The further it compresses, the further it is obviously about to go.",
  },
  anticipateWiden: {
    group: "Wind-up", label: "Squash widening", value: 0.7,
    min: 0, max: 2, step: 0.05, unit: "x",
    why: "How much the cap spreads sideways as it flattens. 0 squashes without bulging.",
  },
  shakeFrequency: {
    group: "Wind-up", label: "Shake frequency", value: 72,
    min: 0, max: 160, step: 1, unit: "",
    why: "How fast the cap rattles during the wind-up. It accelerates on its own as the build-up progresses.",
  },
  shakeAmplitude: {
    group: "Wind-up", label: "Shake amplitude", value: 4.4,
    min: 0, max: 20, step: 0.1, unit: "px",
    why: "How far the cap rattles at the very end of the wind-up.",
  },
  shakeRamp: {
    group: "Wind-up", label: "Shake ramp", value: 1.3,
    min: 0.2, max: 6, step: 0.1, unit: "",
    why: "How late the shake gets loud. Higher keeps it quiet longer and then piles it on.",
  },

  // ---- Tier burst: the release -------------------------------------------
  burstFloor: {
    group: "Burst", label: "Burst starts at", value: 0.06,
    min: 0, max: 1, step: 0.01, unit: "x",
    why: "How flat the cap is on the first frame of the release. Low means it bursts out of a line.",
  },
  burstOvershoot: {
    group: "Burst", label: "Burst overshoot", value: 3.5,
    min: 0, max: 6, step: 0.05, unit: "x",
    why: "How far past full size the cap swells before settling. 0 makes it arrive rather than land.",
  },

  // ---- The landing -------------------------------------------------------
  settleMs: {
    group: "Landing", label: "Settle", value: 0,
    min: 0, max: 2500, step: 10, unit: "ms",
    why: "A short settle on the whole surface so a climb ENDS on something rather than just stopping.",
  },
  nextRevealMs: {
    group: "Landing", label: "Next-step wipe-in", value: 0,
    min: 0, max: 2500, step: 10, unit: "ms",
    why: "The 'X.XXs to rank up' line wiping in left-to-right once the climb lands.",
  },
};

/** Non-numeric knobs. Kept apart because they are choices, not amounts. */
export const CHOICES = {
  skipStyle: {
    group: "Structure", label: "Skipped ranks", value: "chain",
    options: { pop: "Pop the wings out", chain: "Keep wings, chain the caps" },
    why: "What a tier the climb passes ENTIRELY through looks like.",
  },
};

export const DEFAULTS = Object.freeze(Object.fromEntries([
  ...Object.entries(TUNABLES).map(([key, row]) => [key, row.value]),
  ...Object.entries(CHOICES).map(([key, row]) => [key, row.value]),
]));

/** Every group, in declaration order — the inspector's section order. */
export const GROUPS = [...new Set([
  ...Object.values(CHOICES).map((row) => row.group),
  ...Object.values(TUNABLES).map((row) => row.group),
])];

// A tunable a caller did not name falls back to the shipped default rather
// than to undefined: a NaN duration is an animation that never ends, and the
// hold ceiling would be the only thing that noticed.
export const withDefaults = (values) => ({ ...DEFAULTS, ...(values || {}) });

// ---- The ACTIVE tuning ---------------------------------------------------
//
// One module-level slot, read by ui/rankclimb.js at the moment a climb starts
// — the same shape (and the same reason) as rankicon.js's active-style slot.
// Nothing else reads it: every other module in the chain takes its tuning as
// an ARGUMENT and defaults to DEFAULTS, so they all stay pure and node-
// testable, and the inspector can drive them without touching this slot.
let active = withDefaults(null);
export const tuning = () => active;
export function setTuning(values) {
  active = withDefaults(values);
  return active;
}

// ---- The settings string -------------------------------------------------
//
// EVERY value, not just the ones that differ from the shipped defaults —
// "save a settings string that represents EXACTLY the settings that I used"
// (user, 2026-07-27). Encoding only the differences would silently re-point an
// old string at new defaults the day one of them is codified, which is the one
// moment the string exists to survive.
export const encodeTuning = (values) => JSON.stringify(
  Object.fromEntries(Object.keys(DEFAULTS).sort()
    .map((key) => [key, withDefaults(values)[key]])));

/** Parse a settings string. Unknown keys are DROPPED, not carried: they are
 *  either a typo or a tunable that has since been codified away, and both want
 *  the current default rather than a value nothing reads. */
export function decodeTuning(text) {
  const parsed = JSON.parse(text);
  if (!parsed || typeof parsed !== "object") throw new Error("not a settings object");
  return withDefaults(Object.fromEntries(
    Object.entries(parsed).filter(([key]) => key in DEFAULTS)));
}

/** Only what differs from the shipped defaults — the thing worth codifying. */
export const changedFromDefault = (values) =>
  Object.fromEntries(Object.entries(withDefaults(values))
    .filter(([key, value]) => value !== DEFAULTS[key]));
