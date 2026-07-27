// src/sm64_events/ui/celebrations.js — THE celebration registry.
//
// The climb engine (ui/rankclimb.js) knows only that a boundary was crossed
// and what was on each side of it. Everything a crossing LOOKS like lives
// here, one entry per effect, so adding the next celebration — or iterating
// on one of these — is a single entry plus its CSS, with no edit to the
// engine, the hook, or any of the surfaces that render a rank
// (user requirement, 2026-07-26: "make sure the system is flexible so that we
// can add new celebrations / iterate on this easily as we go").
//
// Same shape as the registries this codebase already runs on: ICON_STYLES
// (components/rankicon.js), MARKERS (components/timeline.js), CAP (caps.js).
//
// ---- An entry ------------------------------------------------------------
//
//   on          which beat kinds fire it: "division" | "tier" | "settle",
//               one or a list. ("settle" fires once when the climb lands.)
//   ms          how long it runs.
//   delay       optional ms after the beat before it starts.
//   when(beat)  optional gate — return false and the effect is skipped for
//               this beat. Use it for intensity (`beat.tiersGained >= 2`) or
//               for a state the effect needs (`beat.wingsAfter > 0`).
//   vars        CSS custom properties for the SURFACE (the banner, the bar):
//               an object, or (beat, progress) => object.
//   icon        props merged into the rank icon: an object, or
//               (beat, progress) => object. Whatever a style doesn't
//               understand it simply never reads (rankicon.js's contract).
//
// `progress` runs 0 -> 1 across `ms`. EVERY effect here is a continuous
// number rather than a CSS keyframe class, and that is deliberate: the engine
// already runs a rAF loop, and numbers compose (two overlapping flashes), get
// interrupted cleanly (a second rank-up mid-climb), and need no remount trick
// to re-trigger on the next crossing. Keyframes stay for the things that are
// genuinely one-shot and self-contained — the scope overlay's own animations,
// which this file does not touch.
import { rankColor, wingTiers, DIVISION_NUMERALS, divisionDigit, CAP } from "./components/caps.js";

// A slot reel settles like a slot reel: slightly past, then back.
const easeOutBack = (fraction) => {
  const overshoot = 1.9;
  const back = fraction - 1;
  return 1 + back * back * ((overshoot + 1) * back + overshoot);
};

const easeOutCubic = (fraction) => 1 - (1 - fraction) ** 3;
const easeInCubic = (fraction) => fraction ** 3;

// The user's "Celebrations" switch (header.js's settings drawer). It lives
// with the registry rather than with the overlays because BOTH celebration
// systems answer to it -- the scope overlays in components/celebrate.js and
// the level-up climb in ui/rankclimb.js -- and the climb importing
// celebrate.js would close an import cycle.
const PREF = "sm64.celebrate";
export const celebrationsEnabled = () =>
  typeof localStorage === "undefined" || localStorage.getItem(PREF) !== "0";
export const setCelebrationsEnabled = (on) =>
  localStorage.setItem(PREF, on ? "1" : "0");


// What the sign field reads at a given rank -- the tier's own glyph where it
// has one (Mario's "M"), else the division digit. Lifted out so the reel can
// ask it about the OUTGOING rank as easily as the incoming one.
export const rankGlyph = (tier, numeral) =>
  ((CAP[tier] || {}).glyph) || divisionDigit(numeral);

export const CELEBRATIONS = {
  // The crossing itself: a bloom on the bar and the rank name that decays
  // rather than a class that has to be taken off again.
  levelFlash: {
    on: ["division", "tier"], ms: 360,
    vars: (beat, progress) => ({
      "--climb-flash": (1 - progress) ** 2 * (beat.kind === "tier" ? 1 : 0.55),
    }),
  },

  // The digit ticking over. Two cells and a slide between them, so a tier
  // crossing (1 -> 5) and a climb into Mario (1 -> M) are the same motion as
  // an ordinary 4 -> 3; there is no wrap to special-case.
  digitRoll: {
    on: ["division", "tier"], ms: 420,
    icon: (beat, progress) => ({
      roll: { from: rankGlyph(beat.fromTier, beat.fromDivision),
              to: rankGlyph(beat.tier, beat.division),
              progress: easeOutBack(progress) },
    }),
  },

  // "the wings are literally growing… out of the hat" — the existing fold
  // (index.html's .hat-fold, task 10) played backwards, on the same measured
  // pivots, so a wing arrives the way it leaves.
  wingGrow: {
    on: "division", ms: 460,
    when: (beat) => beat.wingsAfter > beat.wingsBefore,
    icon: (beat, progress) => ({
      growWings: beat.wingsAfter - beat.wingsBefore,
      growProgress: easeOutCubic(progress),
    }),
  },

  // "it should do a little flap after growing fully" — one cycle, starting
  // where the grow ends.
  wingFlap: {
    on: "division", ms: 1100, delay: 460,
    when: (beat) => beat.wingsAfter > 0,
    icon: (beat, progress) => ({ flapPhase: Math.sin(progress * Math.PI * 2) }),
  },

  // ---- The tier crossing: anticipation, then a slam ---------------------
  //
  // The climb STOPS at a tier boundary (ui/climbcurve.js::tierDwell, held by
  // the engine) and these three carry the pause. Cruising through it at three
  // divisions a second is what made the biggest moment in the feature read as
  // just another tick.

  // The build. Shake amplitude AND frequency both grow, and the cap squashes
  // toward a flat line -- anticipation in the animation-principles sense: the
  // further it compresses, the further it is obviously about to spring.
  tierAnticipate: {
    on: "anticipate", ms: (beat) => beat.anticipateMs,
    icon: (_beat, progress) => {
      const squashY = 1 - easeInCubic(progress) * 0.94;
      return {
        squashY, squashX: 1 + (1 - squashY) * 0.5,
        // p*p in the phase, p^2.2 in the amplitude: it reads as winding up
        // rather than as a constant vibration that happens to get louder.
        shake: Math.sin(progress * progress * 46) * progress ** 2.2 * 3.2,
      };
    },
    vars: (_beat, progress) => ({ "--climb-anticipate": progress.toFixed(3) }),
  },

  // The release. Out of the flat line with an overshoot, so it lands rather
  // than arrives -- and the cap it lands as is already the NEW tier's, which
  // is why this replaced the old edge-on flip: a flip pretends to hide a swap
  // that the climb's own position already made.
  tierBurst: {
    on: "tier", ms: (beat) => beat.payoffMs,
    icon: (_beat, progress) => {
      const squashY = 0.06 + 0.94 * easeOutBack(progress);
      return { squashY, squashX: 1 + (1 - squashY) * 0.5 };
    },
  },

  // "maybe a bit of star twinkling appears" -- four-point sparkles thrown out
  // of the cap on the slam, each one on its own short life so they pop in
  // sequence instead of blinking as one block.
  tierSparkle: {
    on: "tier", ms: (beat) => beat.payoffMs,
    icon: (_beat, progress) => ({ sparkle: progress }),
  },

  // A tier crossing lands on division V, which wears no wings -- so the ones
  // on screen have to go somewhere. They tuck during the ANTICIPATION, so the
  // cap is already bare by the time it flattens (the user's own framing for
  // the fold this reuses: "booya, upgraded! Just gotta earn the wings again").
  wingFold: {
    on: "anticipate", ms: (beat) => Math.min(420, beat.anticipateMs),
    when: (beat) => beat.wingsBefore > 0,
    icon: (beat, progress) => ({ foldWings: beat.wingsBefore,
                                 foldProgress: easeOutCubic(progress) }),
  },

  // The one thing that CAN lerp: the flat surfaces -- the bar, and the wash
  // behind the banner, which read the same `--climb-color`. Colour is
  // per-tier in this system, so this is the only beat that changes it, and it
  // runs across the slam so the whole row turns over together (user,
  // 2026-07-27: "all the colors should animate from the original coloring to
  // the new coloring").
  tierColor: {
    on: "tier", ms: (beat) => beat.payoffMs,
    vars: (beat, progress) => ({
      "--climb-color": `color-mix(in srgb, ${rankColor(beat.tier)} `
        + `${(easeOutCubic(progress) * 100).toFixed(1)}%, ${rankColor(beat.fromTier)})`,
    }),
  },

  // The landing. A short settle on the whole surface so a climb ENDS on
  // something rather than just stopping.
  settle: {
    on: "settle", ms: 520,
    vars: (_beat, progress) => ({ "--climb-settle": (1 - progress) ** 2 }),
  },
};

const kindsOf = (entry) => (Array.isArray(entry.on) ? entry.on : [entry.on]);

const contribution = (value, beat, progress) =>
  (typeof value === "function" ? value(beat, progress) : value);

/**
 * Build one beat from a level crossing. `kind` is "tier" when the crossing
 * changed tier — which is the ONE bit most entries branch on — and the wing
 * counts are resolved here rather than in each entry, so the wing policy
 * stays where it lives (caps.js::wingTiers) no matter how many effects read
 * it.
 */
export function makeBeat({ kind, at, level, from, to, tiersGained, divisionsGained,
                           anticipateMs = 0, payoffMs = 0 }) {
  return {
    kind, at, level, anticipateMs, payoffMs,
    tier: to.tier, division: to.division,
    fromTier: from.tier, fromDivision: from.division,
    wingsBefore: wingTiers(from.tier, from.division),
    wingsAfter: wingTiers(to.tier, to.division),
    tiersGained, divisionsGained,
  };
}

/**
 * Merge every celebration currently in flight into one render state.
 *
 * `beats` is the climb's beats so far (each carrying its own `at` timestamp);
 * `nowMs` is this frame. Entries are applied in registry order, so a later
 * entry wins a key it shares with an earlier one — which is the ordering a
 * reader would guess, and the reason `tierColor` sits after `levelFlash`.
 */
export function activeEffects(beats, nowMs) {
  const vars = {};
  const icon = {};
  for (const [name, entry] of Object.entries(CELEBRATIONS)) {
    const kinds = kindsOf(entry);
    for (const beat of beats) {
      if (!kinds.includes(beat.kind)) continue;
      if (entry.when && !entry.when(beat)) continue;
      // `ms` may be a function of the beat: a tier dwell's length depends
      // on how many tiers THIS climb crosses (climbcurve.js::tierDwell), so
      // the effects that fill the dwell cannot be fixed-length constants.
      const windowMs = typeof entry.ms === "function" ? entry.ms(beat) : entry.ms;
      const elapsed = nowMs - beat.at - (entry.delay || 0);
      if (elapsed < 0 || elapsed > windowMs) continue;
      const progress = windowMs > 0 ? Math.min(1, elapsed / windowMs) : 1;
      if (entry.vars) Object.assign(vars, contribution(entry.vars, beat, progress));
      if (entry.icon) Object.assign(icon, contribution(entry.icon, beat, progress));
      // One beat per entry per frame: with several crossings in flight the
      // NEWEST is the one being celebrated, and two flashes fighting over
      // one variable would read as a stutter. `beats` is oldest-first, so
      // the loop keeps going and the last match wins.
    }
  }
  return { vars, icon };
}

/** How long after its beat the slowest effect for `kind` is still running. */
export function celebrationTailMs(kind, sample = { anticipateMs: 1600, payoffMs: 1600 }) {
  let tail = 0;
  for (const entry of Object.values(CELEBRATIONS)) {
    if (!kindsOf(entry).includes(kind)) continue;
    tail = Math.max(tail, (entry.delay || 0)
                    + (typeof entry.ms === "function" ? entry.ms(sample) : entry.ms));
  }
  return tail;
}

/** Every division numeral, bottom of the tier first — re-exported so a
 *  surface never re-derives the order to lay out a reel. */
export { DIVISION_NUMERALS };
