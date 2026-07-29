// src/sm64_events/ui/routeswap.js — the route SWAP: an EXCHANGE, never a
// climb.
//
// User, 2026-07-28: "when we swap to a different route, since sometimes we
// change ranks, we should reuse the squash / pop animation for swapping
// between the different routes. So, I select a new rank, my current rank
// symbol squashes down, and then pops back up with the other route's rank
// symbol... This should all happen with the same timing."
//
// ui/rankclimb.js's `identity` gate exists so a scope change -- which
// legitimately shows a DIFFERENT rank nobody earned -- never fires a
// level-up climb. That stays exactly as it is: a route swap still changes
// `identity` (the label is part of it, components/header.js), so
// useRankClimb still SNAPS the instant the new marelo lands, with no beats
// and no celebration effects. This hook is a SEPARATE clock layered on top
// of that snap -- it never walks a ladder and must never read as an
// achievement: no flash, no sparkle, no tier burst, and the bar simply
// LERPS to wherever the new route sits, which may well be DOWN.
//
// It borrows exactly two entries from the registry it is not otherwise
// touching: CELEBRATIONS.tierAnticipate/.tierBurst (ui/celebrations.js)
// compute the squash a tier crossing already uses, called directly with a
// progress THIS file drives instead of a beat's -- the same squash, reused,
// never a second implementation of it.
import { useEffect, useRef, useState } from "preact/hooks";
import { CELEBRATIONS } from "./celebrations.js";
import { tuning as climbTuning } from "./climbtuning.js";
import { exchangeFade } from "./climbcurve.js";
import { prefersReducedMotion } from "./useTween.js";

// tierAnticipate/tierBurst's `icon` functions both ignore their `beat`
// argument -- they read only `progress` and `tune` -- which is what makes
// calling them OUTSIDE the beat/plan machinery safe: there is nothing in
// them that assumes a climb is running underneath.
function swapIcon(progress, exchangeAt, tune) {
  if (progress <= exchangeAt) {
    const local = exchangeAt > 0 ? progress / exchangeAt : 1;
    return CELEBRATIONS.tierAnticipate.icon(null, local, tune);
  }
  const span = 1 - exchangeAt;
  const local = span > 0 ? (progress - exchangeAt) / span : 1;
  return CELEBRATIONS.tierBurst.icon(null, local, tune);
}

/**
 * `swapKey` is what counts as "a genuinely different measurement to
 * cross-fade to" -- the route's own scope id, the one gesture this feature
 * covers (never the rank MODE, which still snaps exactly as it did before).
 *
 * `current` is the DISPLAY the card would show with no swap in flight --
 * `{tier, division, fill, label}`, primitives only. It is a dependency of
 * this hook's own effect on every field, not just `swapKey`, so the "from"
 * snapshot a swap starts from is always what was actually last on screen --
 * a route's own rating can keep moving between two picks, and a swap begun
 * later must not begin from a value captured at an earlier, unrelated
 * change.
 *
 * `tune` is marelotuning.js's own `mareloTuning()` slot, read once per swap
 * the same way rankclimb.js reads climbtuning.js's `tuning()` once per climb
 * -- a mid-flight tuning change must not retime an exchange already half
 * played.
 *
 * Returns `null` while no swap is in flight (render `current` as-is), or
 * `{progress, crossed, from, to, icon}` while one runs. `crossed` flips true
 * at `tune.swapExchangeAt` -- the flattest frame -- which is where a caller
 * swaps WHICH rank is drawn, the same instant a tier crossing already hides
 * its own cap swap there.
 */
export function useRouteSwap(swapKey, current, tune) {
  const priorRef = useRef({ key: swapKey, display: current });
  const frameRef = useRef(null);
  const [swap, setSwap] = useState(null);

  useEffect(() => {
    const prior = priorRef.current;
    const changed = prior.key !== swapKey;
    const from = prior.display;
    // Resync EVERY time this effect runs, whether or not a swap starts here
    // -- see the header comment on `current` above.
    priorRef.current = { key: swapKey, display: current };

    if (frameRef.current != null) {
      cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
    }

    const nothingToSwap = !changed || prior.key == null
      || !from || from.tier == null || !current || current.tier == null;
    if (nothingToSwap || prefersReducedMotion()) {
      setSwap(null);
      return undefined;
    }

    const startedAt = performance.now();
    const totalMs = Math.max(1, tune.swapMs);
    const exchangeAt = Math.min(0.95, Math.max(0.05, tune.swapExchangeAt));
    // Borrows the CLIMB's own widen/burst shape so this squashes like the
    // same material, not a second hand-tuned version of it -- only the
    // depth is this surface's own knob. Shake is switched off entirely: a
    // scope change is not a tier crossing and must not rattle.
    const squashTune = { ...climbTuning(),
      anticipateSquash: tune.swapSquash, shakeAmplitude: 0 };

    const tick = () => {
      const elapsed = performance.now() - startedAt;
      const progress = Math.min(1, elapsed / totalMs);
      setSwap({
        progress, crossed: progress >= exchangeAt, from, to: current,
        icon: swapIcon(progress, exchangeAt, squashTune),
        // Computed HERE, off this hook's own `exchangeAt`, so the icon, the
        // rank text and the route name all cross on the SAME frame. A caller
        // deriving its own pair from `progress` would be a second opinion
        // about when the exchange happens, and the two would drift the moment
        // swapExchangeAt was tuned.
        fade: exchangeFade(progress, exchangeAt),
      });
      if (progress < 1) {
        frameRef.current = requestAnimationFrame(tick);
      } else {
        frameRef.current = null;
        setSwap(null);
      }
    };
    // Frame 0 is set SYNCHRONOUSLY, here, rather than left to the first rAF
    // tick. useRankClimb's own effect (declared earlier in RouteRankCard)
    // snaps synchronously in the SAME commit -- if this hook only scheduled
    // its first paint via requestAnimationFrame, that snap would paint alone
    // for one frame first: a flash of the DESTINATION state (full bar, new
    // colour) immediately followed by a hard reset down to where the swap
    // actually starts from. Setting progress 0 here keeps both hooks' state
    // updates in the same batch, so the very first thing ever painted is the
    // swap's own start, not the climb's resting end.
    setSwap({ progress: 0, crossed: false, from, to: current,
      icon: swapIcon(0, exchangeAt, squashTune),
      fade: exchangeFade(0, exchangeAt) });
    frameRef.current = requestAnimationFrame(tick);
    return () => {
      if (frameRef.current != null) cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
    };
  }, [swapKey, current.tier, current.division, current.fill, current.label]);

  return swap;
}
