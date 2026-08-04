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
//   on          which beat kinds fire it: "division" | "tierskip" | "tier"
//               | "anticipate" | "settle", one or a list. ("settle" fires once
//               when the climb lands.) The kinds are declared by the plan —
//               ui/climbplan.js — and pinned against it by the tests.
//   ms          how long it runs; may be a function of the beat.
//   delay       optional ms after the beat before it starts; may be a function
//               of the beat, for the same reason `ms` may be — an effect that
//               follows another one inside a single step has to know how long
//               that step is.
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
import { DEFAULTS, tuning, setTuning } from "./climbtuning.js";

// A slot reel settles like a slot reel: slightly past, then back. `overshoot`
// is a tunable per caller — the digit and the cap burst want different amounts
// of bounce, and 0 removes it entirely (which is what a progress BAR must
// always use; see climbcurve.js::barEase for why that one is not this).
const easeOutBack = (fraction, overshoot) => {
  const back = fraction - 1;
  return 1 + back * back * ((overshoot + 1) * back + overshoot);
};

const easeOutCubic = (fraction) => 1 - (1 - fraction) ** 3;
const easeInCubic = (fraction) => fraction ** 3;
const smoothstep = (fraction) => fraction * fraction * (3 - 2 * fraction);

/**
 * An amplitude window over an effect's own `progress`: 0 at both ends, 1
 * across the middle, ramping in and out over `ramp` of the run each side.
 *
 * "The wing animation should EASE IN and EASE OUT btw. Basically all of these
 * animation should generally ease in and ease out… we NEVER want to abruptly
 * stop." (user, 2026-07-27)
 *
 * The distinction that matters is VALUE versus SPEED. Most effects here
 * already land on their end value gently — `easeOutCubic` and `(1-p)**2` both
 * have zero slope at the finish. The wing flap does not: `sin(2*pi*p)` returns
 * to zero having never slowed down, so the wings are travelling at full speed
 * on the frame the beat expires and simply cease. Ending at the right value is
 * not the same as coming to rest, and only the second one reads as easing.
 *
 * A window rather than a different oscillator because the flap's SHAPE is
 * already right — one clean cycle. What was missing was somewhere for it to
 * start from and settle into.
 */
const envelope = (fraction, ramp) => {
  if (!(ramp > 0)) return 1;
  const width = Math.min(0.5, ramp);
  return smoothstep(Math.min(1, fraction / width))
    * smoothstep(Math.min(1, (1 - fraction) / width));
};

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

// What a tier the climb passes ENTIRELY through looks like. Both are built
// because the user asked to judge them live rather than on paper -- "I'd like
// to see both because I genuinely don't know which would feel better"
// (2026-07-27) -- and this registry is expected to lose an entry once one
// wins, the same way the eight-house-styles switch was meant to.
//
// Registry-shaped, like ICON_STYLES: the header's select is built from it, so
// adding or removing a style touches no component.
export const CLIMB_SKIP_STYLES = {
  pop: { label: "Pop the wings out" },
  chain: { label: "Keep the wings, chain the caps" },
};
const SKIP_PREF = "sm64.climbSkip";
export const DEFAULT_SKIP_STYLE = DEFAULTS.skipStyle;
export const climbSkipStyle = () => {
  if (typeof localStorage === "undefined") return DEFAULT_SKIP_STYLE;
  const stored = localStorage.getItem(SKIP_PREF);
  return CLIMB_SKIP_STYLES[stored] ? stored : DEFAULT_SKIP_STYLE;
};
export const setClimbSkipStyle = (style) => {
  const chosen = CLIMB_SKIP_STYLES[style] ? style : DEFAULT_SKIP_STYLE;
  localStorage.setItem(SKIP_PREF, chosen);
  setTuning({ ...tuning(), skipStyle: chosen });
};
// The ACTIVE tuning is the single door the climb reads (rankclimb.js), so the
// stored preference seeds it once at load rather than being consulted as a
// second opinion mid-climb. Doing it here, beside the pref itself, is what
// keeps rankclimb.js from having to know a preference exists at all — and the
// inspector at /ui/tune.html can then override the whole tuning, this key
// included, without fighting localStorage.
setTuning({ ...tuning(), skipStyle: climbSkipStyle() });


// What the sign field reads at a given rank -- the tier's own glyph where it
// has one (Mario's "M"), else the division digit. Lifted out so the reel can
// ask it about the OUTGOING rank as easily as the incoming one.
export const rankGlyph = (tier, numeral) =>
  ((CAP[tier] || {}).glyph) || divisionDigit(numeral);

export const CELEBRATIONS = {
  // The crossing itself: a bloom on the bar and the rank name that decays
  // rather than a class that has to be taken off again.
  levelFlash: {
    on: ["division", "tierskip", "tier"], ms: (_beat, tune) => tune.levelFlashMs,
    vars: (beat, progress, tune) => ({
      "--climb-flash": (1 - progress) ** 2
        * (beat.kind === "tier" ? tune.levelFlashTier : tune.levelFlashDivision),
    }),
  },

  // The digit ticking over. Two cells and a slide between them, so a tier
  // crossing (1 -> 5), a whole tier skipped at once (5 -> 1) and a climb into
  // Mario (1 -> M) are the same motion as an ordinary 4 -> 3; there is no wrap
  // and no multi-level case to special-case.
  //
  // Never longer than the step it belongs to: a climb big enough to compress
  // its ladder steps below 420ms must not have each digit still sliding while
  // the next one starts.
  digitRoll: {
    on: ["division", "tierskip", "tier"],
    ms: (beat, tune) => Math.min(tune.digitRollMs, beat.stepMs),
    icon: (beat, progress, tune) => ({
      roll: { from: rankGlyph(beat.fromTier, beat.fromDivision),
              to: rankGlyph(beat.tier, beat.division),
              progress: easeOutBack(progress, tune.digitRollOvershoot) },
    }),
  },

  // "the wings are literally growing… out of the hat" — the existing fold
  // (index.html's .hat-fold, task 10) played backwards, on the same measured
  // pivots, so a wing arrives the way it leaves.
  //
  // Fills exactly its own step, so the grow and the rank it belongs to begin
  // and end together however hard a long climb has compressed things.
  //
  // "tierskip" is a whole tier crossed at once, which grows all four at once —
  // "IMMEDIATELY TRANSITION INTO THE MAX RANK OF THE NEXT DIVISON" (user,
  // 2026-07-27). "tier" is here for the `chain` skip style, where a crossing
  // lands straight on division I and so is itself a wing gain; the `when`
  // guard makes it a no-op for an ordinary crossing, which SHEDS wings rather
  // than growing them.
  wingGrow: {
    on: ["division", "tierskip", "tier"],
    ms: (beat, tune) => beat.stepMs * tune.wingGrowScale,
    when: (beat) => beat.wingsAfter > beat.wingsBefore,
    icon: (beat, progress) => ({
      growWings: beat.wingsAfter - beat.wingsBefore,
      growProgress: easeOutCubic(progress),
    }),
  },

  // "it should do a little flap after growing fully" — one cycle, starting
  // where the grow ends, which is why the delay tracks the step length too.
  wingFlap: {
    on: ["division", "tierskip"],
    ms: (_beat, tune) => tune.wingFlapMs,
    delay: (beat, tune) => beat.stepMs * tune.wingFlapDelayScale,
    when: (beat) => beat.wingsAfter > 0,
    icon: (_beat, progress, tune) => ({
      flapPhase: Math.sin(progress * Math.PI * 2) * envelope(progress, tune.easeRamp),
    }),
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
    icon: (_beat, progress, tune) => {
      const squashY = 1 - easeInCubic(progress) * tune.anticipateSquash;
      return {
        squashY, squashX: 1 + (1 - squashY) * tune.anticipateWiden,
        // p*p in the phase, p^ramp in the amplitude: it reads as winding up
        // rather than as a constant vibration that happens to get louder.
        shake: Math.sin(progress * progress * tune.shakeFrequency)
          * progress ** tune.shakeRamp * tune.shakeAmplitude,
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
    icon: (_beat, progress, tune) => {
      const squashY = tune.burstFloor + (1 - tune.burstFloor)
        * easeOutBack(progress, tune.burstOvershoot);
      return { squashY, squashX: 1 + (1 - squashY) * tune.anticipateWiden };
    },
  },

  // "maybe a bit of star twinkling appears" -- four-point sparkles thrown out
  // of the cap on the slam, each one on its own short life so they pop in
  // sequence instead of blinking as one block.
  tierSparkle: {
    on: "tier", ms: (beat) => beat.payoffMs,
    icon: (_beat, progress) => ({ sparkle: progress }),
  },

  // NO wing fold during the anticipation, and that is a correction rather
  // than an omission (live report, 2026-07-27: "the wings briefly disappear,
  // and then reappear for the shake and squash… the wings should stay at the
  // highest tier for the duration of the animation, until we actually have
  // the new cap"). The fold ran for 420ms of a ~900ms build and then EXPIRED,
  // at which point `wingTiers` -- reading a position still on the old tier's
  // division I -- put all four wings straight back.
  //
  // They now simply STAY: the position does not cross the boundary until the
  // burst, so the wings squash along with the cap and are gone the instant
  // the new cap exists, which is exactly where the user drew the line. It
  // also lands on the flattest frame of the whole sequence, so nothing pops.
  // `foldWings`/`foldProgress` survive as props -- the scope overlay still
  // folds, on its own schedule.

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

  // "the amount of time required to level up to the next star should animate
  // in… it should say 'X.XXs to rank up' and this should all fade in from the
  // left most text to the right most text" (user, 2026-07-27). A mask wipe
  // rather than per-word spans: one element, no splitting of a string that
  // ellipsises, and the soft edge reads as a fade rather than a curtain.
  // ranks.js holds it at 0 for the rest of the climb.
  // `nextReveal` used to live here, wiping the "X.XXs to rank up" line in on
  // the SETTLE beat. It is gone, and the reason is worth keeping: a beat fires
  // at a moment, so its timing could only ever be tuned to line up with the
  // bar, never tied to it — and it did not line up. "it looks like the text
  // that appears while we're doing this is totally glitchy… While the bar is
  // loading and filling up, it should be fading away… While the final
  // animation step is playing, it should be fading back in, and it should
  // finish right when the bar finishes loading. These should be in sync."
  // (user, 2026-07-27).
  //
  // Being in sync with the bar means being a function of the same thing the
  // bar is a function of, so `--climb-reveal` is computed by the engine
  // (ui/rankclimb.js) off the very same step, from the very same start, on the
  // very same curve. No registry entry can express that, because no beat knows
  // where the bar has got to.
  //
  // The one thing it does NOT share is the bar's DURATION: a sweep's length
  // scales with the distance it travels and legibility does not, so the line's
  // clock carries a floor (`revealMinMs`). Identical whenever the step is
  // longer than the floor, which is every climb big enough to see.

  // The landing. A short settle on the whole surface so a climb ENDS on
  // something rather than just stopping.
  settle: {
    on: "settle", ms: (_beat, tune) => tune.settleMs,
    // The variable IS the scale delta, not a bare 0->1 decay, because the
    // AMPLITUDE is the control that was missing. It lived as a hardcoded
    // `* .012` in the stylesheet, so the only thing tunable about the landing
    // was its LENGTH -- and shortening it made the pop worse rather than
    // gentler, because the same 1.2% jump then had to happen in one frame.
    // Measured at settleMs 20: scale 1 -> 1.012 -> 1 across 32ms, the only
    // motion anywhere in the climb, which is the "little shake alll the way at
    // the end" (user, 2026-07-27). It defaults to 0 now, at his call.
    vars: (_beat, progress, tune) => ({
      "--climb-settle-scale": (1 - progress) ** 2 * tune.settleScale,
    }),
  },
};

const kindsOf = (entry) => (Array.isArray(entry.on) ? entry.on : [entry.on]);

const contribution = (value, beat, progress, tune) =>
  (typeof value === "function" ? value(beat, progress, tune) : value);

// `ms`, `delay` and `when` see (beat, tuning); `vars` and `icon` see
// (beat, progress, tuning). Both shapes put the tuning LAST, so an entry that
// does not care about it simply never declares the parameter.
const length = (value, beat, tune) =>
  (typeof value === "function" ? value(beat, tune) : (value || 0));

/**
 * Build one beat from a level crossing. `kind` is "tier" when the crossing
 * changed tier — which is the ONE bit most entries branch on — and the wing
 * counts are resolved here rather than in each entry, so the wing policy
 * stays where it lives (caps.js::wingTiers) no matter how many effects read
 * it.
 */
export function makeBeat({ kind, at, level, from, to, tiersGained, divisionsGained,
                           anticipateMs = 0, payoffMs = 0, stepMs = 0 }) {
  return {
    kind, at, level, anticipateMs, payoffMs,
    // How long the PLAN gave the step this beat announces (ui/climbplan.js).
    // An effect that is meant to FILL its step reads this instead of naming a
    // constant, so a climb long enough to compress its steps compresses the
    // effects with them rather than overrunning into the next rank.
    stepMs,
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
export function activeEffects(beats, nowMs, tune = DEFAULTS) {
  const vars = {};
  const icon = {};
  for (const [name, entry] of Object.entries(CELEBRATIONS)) {
    const kinds = kindsOf(entry);
    for (const beat of beats) {
      if (!kinds.includes(beat.kind)) continue;
      if (entry.when && !entry.when(beat, tune)) continue;
      // `ms` may be a function of the beat AND the tuning: a tier dwell's
      // length depends on how many tiers THIS climb crosses
      // (climbcurve.js::tierDwell), and every length here is a row in
      // ui/climbtuning.js the inspector can drive.
      const windowMs = length(entry.ms, beat, tune);
      const delayMs = length(entry.delay, beat, tune);
      const elapsed = nowMs - beat.at - delayMs;
      if (elapsed < 0 || elapsed > windowMs) continue;
      const progress = windowMs > 0 ? Math.min(1, elapsed / windowMs) : 1;
      if (entry.vars) Object.assign(vars, contribution(entry.vars, beat, progress, tune));
      if (entry.icon) Object.assign(icon, contribution(entry.icon, beat, progress, tune));
      // One beat per entry per frame: with several crossings in flight the
      // NEWEST is the one being celebrated, and two flashes fighting over
      // one variable would read as a stutter. `beats` is oldest-first, so
      // the loop keeps going and the last match wins.
    }
  }
  return { vars, icon };
}

/** How long after its beat the slowest effect for `kind` is still running. */
export function celebrationTailMs(kind,
                                  sample = { anticipateMs: 1600, payoffMs: 1600, stepMs: 460 },
                                  tune = DEFAULTS) {
  let tail = 0;
  for (const entry of Object.values(CELEBRATIONS)) {
    if (!kindsOf(entry).includes(kind)) continue;
    tail = Math.max(tail, length(entry.delay, sample, tune)
                    + length(entry.ms, sample, tune));
  }
  return tail;
}

/** Every division numeral, bottom of the tier first — re-exported so a
 *  surface never re-derives the order to lay out a reel. */
export { DIVISION_NUMERALS };
