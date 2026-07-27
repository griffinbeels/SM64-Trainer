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

  // A tier crossing lands on division V, which wears no wings — so the ones
  // on screen have to go somewhere. They tuck, they don't vanish (the user's
  // own framing for the fold this reuses: "booya, upgraded! Just gotta earn
  // the wings again").
  wingFold: {
    on: "tier", ms: 420,
    when: (beat) => beat.wingsBefore > 0,
    icon: (beat, progress) => ({ foldWings: beat.wingsBefore,
                                 foldProgress: easeOutCubic(progress) }),
  },

  // The cap can't lerp — Capless is a dashed outline, Metal carries a
  // highlight layer, Toad and Toadsworth carry spots — so the NEW cap swings
  // in edge-on instead. Deliberately not the overlay's turn-away-and-back:
  // the art here is driven by the climb's own position, so it has already
  // become the new tier's cap by the time this runs. A cap arriving is
  // honest about that; a cap turning away and back would be pretending to
  // hide a swap that already happened.
  capFlip: {
    on: "tier", ms: 520,
    icon: (_beat, progress) => ({ flip: 1 - easeOutCubic(progress) }),
  },

  // The one thing that CAN lerp: the flat surfaces. Colour is per-tier in
  // this system, so this is the only beat that changes it.
  tierColor: {
    on: "tier", ms: 700,
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
export function makeBeat({ kind, at, level, from, to, tiersGained, divisionsGained }) {
  return {
    kind, at, level,
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
      const elapsed = nowMs - beat.at - (entry.delay || 0);
      if (elapsed < 0 || elapsed > entry.ms) continue;
      const progress = entry.ms > 0 ? Math.min(1, elapsed / entry.ms) : 1;
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
export function celebrationTailMs(kind) {
  let tail = 0;
  for (const entry of Object.values(CELEBRATIONS)) {
    if (!kindsOf(entry).includes(kind)) continue;
    tail = Math.max(tail, (entry.delay || 0) + entry.ms);
  }
  return tail;
}

/** Every division numeral, bottom of the tier first — re-exported so a
 *  surface never re-derives the order to lay out a reel. */
export { DIVISION_NUMERALS };
