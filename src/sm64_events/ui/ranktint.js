// src/sm64_events/ui/ranktint.js — the app-wide background tint. ONE
// property, TWO writers, never at once.
//
// User, 2026-07-28: "The background tint of the app should transition from
// the default background of the site -> the background of the current rank
// -> WHEN AN OVERALL RANK TRANSITION HAPPENS that's when you smoothly
// transition to the next background color... It should visually match what's
// being displayed. If we're still ticking up within Capless, that's the
// color we display everywhere." Before this the tint only existed inside the
// celebration overlay (`.marelo-celebrate`'s own backdrop), so simply
// ARRIVING at a rank's colour once a celebration ended was itself the jump
// the celebration had already been built to never make mid-flight -- the
// tint just needed a home OUTSIDE the celebration too, worn at rest.
//
// `useRankTint` (called once, from app.js, off the store's own
// `t.marelo.tier`) owns `--rank-tint-color` AT REST -- gated on the tier
// alone, so it never refires on an unrelated store poll (the app root
// re-renders on every /api/marelo tick while a celebration is up;
// marelocelebrate.js's own header comment already documents this for the
// same reason). `marelocelebrate.js`'s MareloCelebration TEMPORARILY drives
// the SAME property while it is mounted, using the identical formula
// (`restingTintColor`, i.e. `rankColor(shown.tier)`) its own backdrop already
// paints with -- so the two writers can never disagree about what a tier's
// colour IS, only about whose turn it is to say so -- and hands it back on
// unmount. `t.marelo.tier` does not itself change until a celebration acks
// (a fresh /api/marelo fetch brings the new tier), which is exactly why the
// celebration must be the one driving this while it plays: without it the
// app tint would sit on the FROM tier for the whole flight.
//
// Strength and cross-fade duration are read once here (ui/marelotuning.js's
// `tintStrength`/`tintCrossfadeMs`) and never touched by the celebration --
// how strong the tint is and how long a change takes do not change just
// because a celebration started, only the COLOUR needs a second writer.
import { useEffect } from "preact/hooks";
import { rankColor } from "./components/caps.js";
import { mareloTuning } from "./marelotuning.js";
import { prefersReducedMotion } from "./useTween.js";

// The one formula both owners share for "what colour is this tier's tint" --
// so "temporarily driving the same property" can never mean a DIFFERENT
// colour for the same tier depending on who is asking.
export const restingTintColor = (tier) => rankColor(tier);

export function setRankTintColor(color) {
  document.documentElement.style.setProperty("--rank-tint-color", color);
}

export function useRankTint(tier) {
  useEffect(() => {
    const tune = mareloTuning();
    document.documentElement.style.setProperty(
      "--rank-tint-strength", tune.tintStrength);
    // Same "compute 0 in JS rather than add a CSS override" contract
    // marelocelebrate.js's own --fly-ms already follows: one transition
    // declaration total, never a second reduced-motion rule that could drift
    // out of sync with it (`.claude/rules/ui-core.md`).
    document.documentElement.style.setProperty("--rank-tint-ms",
      `${prefersReducedMotion() ? 0 : tune.tintCrossfadeMs}ms`);
    setRankTintColor(restingTintColor(tier));
  }, [tier]);
}
