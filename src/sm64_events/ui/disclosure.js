// src/sm64_events/ui/disclosure.js — THE motion plans for the practice log.
//
// Two of them, and they are deliberately in one module because they share the
// one curve (`ui/feedtuning.js`'s own header says why):
//
//   `disclosurePlan`  — anything opening or closing: a log card's body, the
//                       Rank standards dropdown, and whatever gets one next.
//                       Griffin asked for exactly this generality: "I think we
//                       may need a general way to animate drop downs opening
//                       like this with best practices?"
//   `feedPlan`        — a new card arriving in the log and the cards it pushes
//                       down. "It should feel like the card is a physical
//                       object entering the space, occupied by the other
//                       physical objects we're moving."
//
// Import-free and PURE: every consumer passes its tuning in and gets numbers
// back, so node drives both directly (tests/test_ui_feedtuning.py) and neither
// can read the active slot behind a caller's back. The wiring layer reads the
// slot ONCE per run — a tuning change mid-flight must not retime an animation
// that is already half-played (`.claude/skills/tuning-demo`).
//
// WHY THESE ARE PLANS RATHER THAN CSS TRANSITIONS. Both animate a distance the
// stylesheet cannot know: a body's natural height, and how far each card has
// actually moved. `height: auto` is not interpolable and never has been, and
// `display` toggles are not either — the two failure modes `.claude/rules/
// acceptance.md` already records as "lengthening the duration does nothing".
// So the caller measures, and these say how to play what was measured.

// A cubic-bezier timing string from the four tuned handles. ONE function, so
// the arrival, the push and both disclosure directions cannot drift onto
// different curves.
export function curve(tuning) {
  const { c1x, c1y, c2x, c2y } = tuning;
  return `cubic-bezier(${c1x}, ${c1y}, ${c2x}, ${c2y})`;
}

/**
 * How to play an open or a close.
 *
 * `heightPx` is what the caller MEASURED (`scrollHeight` at the moment of the
 * change), never a guess — the whole reason this is a plan and not a
 * stylesheet rule.
 *
 * The contents fade on their own, shorter than and offset into the box's own
 * growth, which is what separates "it opened" from "something appeared". On
 * the way CLOSED they do not fade at all: contents dissolving while the box
 * collapses reads as two things leaving, and the box is already saying it.
 */
export function disclosurePlan(open, heightPx, tuning) {
  const durationMs = open ? tuning.openMs : tuning.closeMs;
  const easing = curve(tuning);
  return {
    durationMs,
    easing,
    from: open ? 0 : Math.max(0, heightPx),
    to: open ? Math.max(0, heightPx) : 0,
    // Never longer than the box it happens inside: a fade still running after
    // the edge has stopped is the "still moving when it lands" shape he
    // reports at a single frame's worth of change.
    contentFadeMs: open ? Math.min(tuning.contentFadeMs, durationMs) : 0,
    contentDelayMs: open
      ? Math.min(tuning.contentFadeDelayMs,
                 Math.max(0, durationMs - Math.min(tuning.contentFadeMs, durationMs)))
      : 0,
  };
}

/**
 * How to play a feed insertion.
 *
 * `shifted` is the caller's FLIP measurement — one entry per card that moved,
 * `{ key, dy }`, where `dy` is how far it has ALREADY been displaced by the
 * time this runs (first position minus last position). The card is put back
 * where it was and released, which is what makes the push look like it was
 * caused by the arrival rather than scheduled alongside it.
 *
 * The stagger is applied in the order given, so the caller's own order is what
 * "further down the list" means — this module never re-sorts, because the only
 * correct order is the one on screen.
 */
export function feedPlan(shifted, tuning) {
  const easing = curve(tuning);
  return {
    enter: {
      durationMs: tuning.enterMs,
      easing,
      risePx: tuning.enterRisePx,
      fadeMs: Math.min(tuning.enterFadeMs, tuning.enterMs),
    },
    shifts: (shifted || []).map(({ key, dy }, index) => ({
      key,
      dy,
      durationMs: tuning.shiftMs,
      easing,
      delayMs: index * tuning.shiftStaggerMs,
    })),
  };
}

/**
 * The longest this whole gesture can still be running, from the frame it
 * starts. The wiring layer uses it as a hold ceiling, for the reason
 * `selectortuning.js::maxHoldMs` states in its own words: a window something
 * can hold open indefinitely is a surface that never comes back.
 */
export function feedSettleMs(shifted, tuning) {
  const plan = feedPlan(shifted, tuning);
  const lastShift = plan.shifts.length
    ? Math.max(...plan.shifts.map((s) => s.delayMs + s.durationMs)) : 0;
  return Math.max(plan.enter.durationMs, lastShift);
}
