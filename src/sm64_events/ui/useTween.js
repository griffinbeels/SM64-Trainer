// src/sm64_events/ui/useTween.js — the one number-animation primitive.
//
// Live report 2026-07-25: "any time we ever improve our ranking... there
// needs to be animations that tween the progress over time from current
// progress to new progress." Every numeric surface that can celebrate a
// rank change (division-fill bars, the header MARELO track, the rating
// number itself) routes through this ONE hook — ranks.js, marelo.js and
// rankpage.js all import it rather than rolling their own requestAnimationFrame
// loop. Two of them easing differently is what would make the app feel
// broken instead of alive, not more polished.
import { useEffect, useRef, useState } from "preact/hooks";

export const prefersReducedMotion = () =>
  typeof matchMedia === "function"
  && matchMedia("(prefers-reduced-motion: reduce)").matches;

// ease-out cubic: fast start, settling into place — a bar filling or a
// number counting up should read as ARRIVING, not still under its own power
// right up to the last frame.
const easeOutCubic = (fraction) => 1 - (1 - fraction) ** 3;

const DEFAULT_DURATION_MS = 700;

/**
 * Animates FROM the previously displayed value TO `value` over
 * `durationMs`, via requestAnimationFrame — never a CSS transition, so a
 * caller can read the live interpolated number on every frame (a bar's
 * width AND its printed percentage move together instead of drifting apart,
 * which is exactly the kind of per-component inconsistency this hook exists
 * to rule out).
 *
 * `value == null` passes straight through as `null` (no animation, no
 * crash) so a caller can feed this directly from an as-yet-unloaded fetch.
 * The FIRST real value a caller ever sees is shown immediately, not tweened
 * from nothing — there is no "previous" value to climb from on initial
 * load, only on a later change.
 *
 * `prefers-reduced-motion: reduce` snaps straight to `value` — the app runs
 * on screen while streaming and some viewers turn motion off entirely.
 *
 * A `value` change mid-flight re-targets from wherever the animation
 * CURRENTLY is (read off `displayRef`, updated every tick) rather than
 * restarting from the value that began the last animation — two rank
 * updates landing close together must not visibly snap back before
 * continuing.
 */
export function useTween(value, durationMs = DEFAULT_DURATION_MS) {
  const [displayValue, setDisplayValue] = useState(value);
  const displayRef = useRef(value);
  const frameRef = useRef(null);

  useEffect(() => {
    if (frameRef.current != null) cancelAnimationFrame(frameRef.current);

    if (value == null) {
      displayRef.current = null;
      setDisplayValue(null);
      return undefined;
    }

    const from = displayRef.current == null ? value : displayRef.current;
    if (from === value || prefersReducedMotion()) {
      displayRef.current = value;
      setDisplayValue(value);
      return undefined;
    }

    const startTime = performance.now();
    const tick = (now) => {
      const fraction = Math.min(1, (now - startTime) / durationMs);
      const next = from + (value - from) * easeOutCubic(fraction);
      displayRef.current = next;
      setDisplayValue(next);
      if (fraction < 1) frameRef.current = requestAnimationFrame(tick);
    };
    frameRef.current = requestAnimationFrame(tick);
    return () => { if (frameRef.current != null) cancelAnimationFrame(frameRef.current); };
  }, [value, durationMs]);

  return displayValue;
}
