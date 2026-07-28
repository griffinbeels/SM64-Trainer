// src/sm64_events/ui/climbplan.js — a level-up climb, as an ordered list of
// steps. Import-free on purpose, the same convention as ui/climbcurve.js and
// ui/components/caps.js, so node can execute it directly
// (tests/test_ui_climbplan.py).
//
// WHY a plan and not a curve (spec 2026-07-27-multi-rank-climb §"Why the
// current engine cannot do this"). The climb used to move ONE number --
// ladder position -- continuously, and derive everything from it: the bar was
// its fractional part and the rank was `rankAt(floor(position))`. That
// coupling is what killed the original backwards-bar bug, and it is also what
// made this feature impossible: the bar cannot STAY FULL across several
// rank-ups without the position lying about which rank is on screen.
//
//   "The bar should STAY FULL WHILE RANKING UP GOING FORWARD. ONCE IT'S FULL,
//    AND IF WE HAVE MORE RANKS TO FULLY LEVEL UP THROUGH, IT STAYS FULL."
//    (user, 2026-07-27)
//
// So the bar is its own value and a climb is a SEQUENCE. "The bar stays full
// while ranks remain" is then not a rule anyone has to remember -- every step
// between the first rank-up and the arrival is simply built with
// `barFrom === barTo === 1`.
//
// This file speaks only in NUMBERS: levels, fills, how many divisions there
// are in a tier. It never learns what a tier is called or what a cap looks
// like, which is what keeps it testable without a registry -- caps.js owns
// which rank a level IS, and ui/celebrations.js owns what a crossing looks
// like.

// ---- What a step is ------------------------------------------------------
//
//   kind      "approach" | "division" | "anticipate" | "tier" | "tierskip"
//             | "arrive" | "fill"
//   level     the ladder level SHOWN for the whole step. A rank-changing step
//             shows its DESTINATION from its first frame -- the rank ticks
//             over and the celebration then plays on the new rank, which is
//             what "rank up, THEN the wings grow" means.
//   barFrom
//   barTo     the bar at each end. Equal on every pinned step.
//   beat      `{fromLevel, toLevel}` for a step the engine should announce to
//             the celebration registry, else null. Explicit rather than
//             derived from `level`, because "anticipate" shows the OLD rank
//             while announcing the one it is about to cross into.
//   ms        filled in by the second pass below.
//   at        ms from the start of the climb.

const DIVISION = "division";
const TIER = "tier";
const TIER_SKIP = "tierskip";
const ANTICIPATE = "anticipate";

/** The step kinds that move the bar rather than the rank. */
export const SWEEP_KINDS = ["approach", "arrive", "fill"];

/** The step kinds that cost a shared ladder budget (see climbcurve.js). */
export const LADDER_KINDS = [DIVISION, TIER_SKIP];

/**
 * Build the plan for a climb from `(fromLevel, fromFill)` to
 * `(toLevel, toFill)`.
 *
 * `skipStyle` decides what a fully-traversed tier looks like — see the spec,
 * and `CLIMB_SKIP_STYLES` in ui/celebrations.js for the user-facing switch:
 *
 *   "pop"    the crossing lands on division V and a `tierskip` step
 *            immediately takes it to division I, growing all four wings.
 *   "chain"  the crossing lands straight on division I. The wings never come
 *            off and the digit never leaves 1, so several crossings read as
 *            one chain of caps.
 *
 * `timings({crossings, ladder})` returns `{barSweepMs, ladderMs,
 * anticipateMs, payoffMs}`. It is called AFTER the structure exists, so the
 * counts it budgets against are read off the real plan rather than predicted
 * by a second copy of the rules below.
 */
export function buildClimbPlan({ fromLevel, fromFill, toLevel, toFill,
                                 divisionsPerTier, skipStyle = "pop", timings }) {
  const steps = [];
  const push = (kind, level, barFrom, barTo, beat = null) =>
    steps.push({ kind, level, barFrom, barTo, beat, ms: 0, at: 0 });

  if (toLevel <= fromLevel) {
    // No rank change at all: the bar simply moves to where it now is. This is
    // the ordinary "you improved your time but stayed put" case, and it is
    // also the only step that can move the bar DOWN — a drop never gets here,
    // the hook snaps instead.
    push("fill", toLevel, fromFill, toFill);
  } else {
    // 1. Fill up the division you were standing in. Skipped when the bar is
    //    already full, which is how a climb INTERRUPTED by a second PB picks
    //    up where it was rather than replaying an approach it already did.
    if (fromFill < 1) push("approach", fromLevel, fromFill, 1);

    // 2. The ladder, bar pinned full for all of it.
    let shown = fromLevel;
    while (shown < toLevel) {
      const next = shown + 1;
      // A tier boundary is exactly a level divisible by the tier size --
      // caps.js's `rankAt` reads the division off `level % DIVISIONS_PER_TIER`
      // with the bottom division first, so the two statements are the same
      // one. Pinned against caps.js itself by tests/test_ui_climbplan.py
      // rather than trusted.
      if (next % divisionsPerTier !== 0) {
        push(DIVISION, next, 1, 1, { fromLevel: shown, toLevel: next });
        shown = next;
        continue;
      }
      // The build-up shows the rank being LEFT (its division I, bar full) and
      // announces the one it is about to reach.
      push(ANTICIPATE, shown, 1, 1, { fromLevel: shown, toLevel: next });
      const top = next + divisionsPerTier - 1;   // division I of the new tier
      // "fully traversed" — we both enter this tier and leave it, so nothing
      // that happens inside it is progress the player is going to keep. The
      // tier the climb STARTS in can never match (we never cross INTO it) and
      // the destination tier cannot either, which is why both are still
      // climbed division by division.
      const traversed = toLevel > top;
      const landsOn = (traversed && skipStyle === "chain") ? top : next;
      push(TIER, landsOn, 1, 1, { fromLevel: shown, toLevel: landsOn });
      // Landing in the tier the climb FINISHES in, with rungs still to climb:
      // the ladder plays OVER this crossing's release rather than queueing
      // behind it (user, 2026-07-27: "DURING THE TIER CROSSING ANIMATION, IT
      // SHOULD ALSO BE DOING THE LADDER STEP ANIMATION… we would see the
      // squashing and shaking, AND ALSO, it would do the ladder steps").
      //
      // Nothing about the beats changes, and that is why this is a one-flag
      // edit: a beat's effect runs for its OWN window wherever the walker has
      // got to (ui/celebrations.js merges every live beat), so simply letting
      // the next step start early puts the wing-grow and the digit roll on
      // screen while the cap is still bursting.
      //
      // A tier the climb only PASSES THROUGH never gets this — it has no
      // ladder of its own to overlap, which is the user's own carve-out:
      // "during Capless 5 -> Toad 1, we skip that, because it's NOT the final
      // rank we're leveling up to."
      steps[steps.length - 1].overlapNext = !traversed && toLevel > landsOn;
      shown = landsOn;
      if (traversed && skipStyle !== "chain") {
        push(TIER_SKIP, top, 1, 1, { fromLevel: shown, toLevel: top });
        shown = top;
      }
    }

    // 3. The arrival, and the ONE place the bar restarts: "we finally leveled
    //    up to where we need to be, so let's show you how close you are to
    //    actually ranking up to the next tier -> animate from empty to our
    //    actual progress" (user, 2026-07-27). No beat — the rank stopped
    //    moving a step ago; this is the bar catching up to it.
    //
    //    `ownsLevel: false` because this step exists ONLY to move the bar. Once
    //    `finalBarOverlap` pulls it back into the end of the climb it runs
    //    ALONGSIDE ranks that are still ticking, and a step that owned the
    //    level there would jump the cap straight to the destination — see the
    //    second pass below, and rankclimb.js, which resolves level and bar from
    //    different steps for exactly this reason.
    push("arrive", toLevel, 0, toFill);
    steps[steps.length - 1].ownsLevel = false;
  }

  // ---- Second pass: how long each step gets ------------------------------
  const crossings = steps.filter((step) => step.kind === TIER).length;
  const ladder = steps.filter((step) => LADDER_KINDS.includes(step.kind)).length;
  const resolved = timings({ crossings, ladder });
  const { barSweepMs, ladderMs, anticipateMs, payoffMs,
          finalTierOverlap = 0, finalBarOverlap = 0 } = resolved;
  let at = 0;
  for (const step of steps) {
    step.ms = step.kind === ANTICIPATE ? anticipateMs
      : step.kind === TIER ? payoffMs
      : LADDER_KINDS.includes(step.kind) ? ladderMs
      : barSweepMs(Math.abs(step.barTo - step.barFrom));
    step.at = at;
    // `handoffMs` is when the NEXT step starts, which is the step's own length
    // everywhere except an overlapped crossing. Keeping them as two numbers is
    // what lets a step go on ANIMATING for its full `ms` after the one behind
    // it has already begun.
    step.handoffMs = step.overlapNext ? step.ms * (1 - finalTierOverlap) : step.ms;
    at += step.handoffMs;
  }
  // ---- Pull the closing bar sweep back into the ending -------------------
  //
  // "during the final tier crossing / OR ladder step crossing animation, we
  // should ALSO be lerping the bar from 0 -> the final position… otherwise
  // right now the bar happens *after* all of that finishes. Instead, it should
  // happen WHILE it's finishing the final step." (user, 2026-07-27)
  //
  // This is the one place the bar deliberately stops being pinned full while
  // ranks are still ticking — his own earlier rule, revised by him for the
  // final stretch only. The ANCHOR is the last tier crossing when the climb has
  // one (his example names it: "the Toadsworth -> Waluigi threshold"), else the
  // last rank-changing step, so a climb that never leaves its tier still gets
  // the same treatment on its own last rung.
  const arrive = steps.find((step) => step.kind === "arrive");
  if (arrive && finalBarOverlap > 0) {
    const endsAt = steps.reduce(
      (end, step) => (step === arrive ? end : Math.max(end, step.at + step.ms)), 0);
    const crossings2 = steps.filter((step) => step.kind === TIER);
    const ranked = steps.filter((step) => step.beat);
    const anchor = (crossings2.length ? crossings2[crossings2.length - 1]
      : ranked[ranked.length - 1]);
    if (anchor) arrive.at = endsAt - (endsAt - anchor.at) * finalBarOverlap;
  }

  // The climb ends when the LAST THING STILL MOVING stops — which is not
  // necessarily the last step to start, once one of them overlaps.
  const totalMs = steps.reduce((end, step) => Math.max(end, step.at + step.ms), 0);
  // Sorted by START time, because an overlapped step no longer arrives in
  // construction order and the engine walks this list by `at`. A stable sort,
  // so steps sharing a start time keep the order they were built in.
  steps.sort((one, two) => one.at - two.at);
  // `timings` comes back out so the caller never has to resolve it a second
  // time to learn how long a dwell or a ladder step ended up being — the
  // celebration tail is measured against exactly the numbers the plan used.
  return { steps, totalMs, crossings, ladder, timings: resolved };
}
