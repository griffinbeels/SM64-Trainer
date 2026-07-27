// src/sm64_events/ui/rankclimb.js — the level-up climb, as one hook.
//
// THE fix for task 0012. A rank is three values that move together (tier,
// division, and how far through that division you are) and the surfaces used
// to animate the third alone, through useTween — so crossing a boundary sent
// the server's within-division `fill` from .95 to .05 and the bar ran
// BACKWARDS on the one event it exists to celebrate.
//
// This hook animates caps.js's `rankPosition` instead: one monotone number
// where 1.0 is one division. The bar is its fractional part, the rank is
// `rankAt(floor(...))`, and a level-up is that floor incrementing. The bar
// cannot decrease during a rise because the position cannot.
//
// Three collaborators, each replaceable on its own:
//   ui/climbcurve.js    HOW FAST (import-free, node-tested)
//   ui/celebrations.js  WHAT IT LOOKS LIKE (the registry — add effects there)
//   caps.js             WHICH RANK a position is (import-free, pinned to Python)
// This file owns only the loop and the bookkeeping between them.
import { useEffect, useRef, useState } from "preact/hooks";
import { rankPosition, rankAt, rankFrame, rankColor, DIVISIONS_PER_TIER } from "./components/caps.js";
import { climbPosition, climbDurationBetween } from "./climbcurve.js";
import { activeEffects, makeBeat, celebrationTailMs, celebrationsEnabled }
  from "./celebrations.js";
import { prefersReducedMotion } from "./useTween.js";

const now = () => performance.now();

// ---- The celebration HOLD ------------------------------------------------
//
// User requirement, 2026-07-27: "if the celebration occurs, and then for some
// reason the user changes stages… we should prevent the practice UI from
// transitioning to the next stage until the celebration is completed."
//
// Grabbing the star and immediately walking out is the NORMAL way to end a
// run, so without this the reward is routinely cut off one frame after it
// starts — the card that is celebrating gets replaced by the next stage's.
//
// A module-level set rather than context or a store slot: the climbs are
// started by leaf components (each RankBanner, the MareloBar) and the thing
// that must wait is an ancestor of some of them and a stranger to the rest.
// Same shape as rankicon.js's active-style slot, for the same reason.
const liveClimbs = new Set();
const holdListeners = new Set();
let nextClimbToken = 0;

// A frozen practice page is a far worse failure than a clipped animation, so
// the hold can never outlive this even if a climb somehow fails to retire its
// token. Comfortably past climbcurve's own 7s ceiling plus the longest
// celebration tail.
const HOLD_CEILING_MS = 12000;

function setClimbing(token, running) {
  const wasHolding = liveClimbs.size > 0;
  if (running) liveClimbs.add(token);
  else liveClimbs.delete(token);
  const holding = liveClimbs.size > 0;
  if (wasHolding !== holding) holdListeners.forEach((notify) => notify(holding));
}

/** True while any rank on screen is mid-climb. */
export const isCelebrating = () => liveClimbs.size > 0;

export function useCelebrating() {
  const [celebrating, setCelebrating] = useState(isCelebrating());
  useEffect(() => {
    holdListeners.add(setCelebrating);
    setCelebrating(isCelebrating());
    return () => holdListeners.delete(setCelebrating);
  }, []);
  return celebrating;
}

/**
 * `value`, frozen at whatever it was when a celebration began, released the
 * moment the last climb finishes.
 *
 * The value that gets frozen is the one that ARRIVED WITH the rank-up — the
 * climb only starts once the new rank has rendered — so the attempt that
 * earned it is already on screen. What waits is everything after: the stage
 * change, the target moving, the sections re-picking.
 */
export function useHeldWhileCelebrating(value) {
  const celebrating = useCelebrating();
  const [expired, setExpired] = useState(false);
  const held = useRef(value);
  useEffect(() => {
    if (!celebrating) { setExpired(false); return undefined; }
    const timer = setTimeout(() => setExpired(true), HOLD_CEILING_MS);
    return () => clearTimeout(timer);
  }, [celebrating]);
  if (!celebrating || expired) held.current = value;
  return held.current;
}

function renderState(position, beats, atMs) {
  // rankFrame, never `position - Math.floor(position)`: at the top of the
  // ladder a maxed rank is position 45, whose fractional part is zero, and
  // the bar would empty at the highest rank in the game (caps.js has the
  // full note).
  const { tier, division, fill } = rankFrame(position);
  const { vars, icon } = activeEffects(beats, atMs);
  return {
    tier, division, fill,
    // The registry's tierColor entry overrides this mid-crossing; the rest of
    // the time the surface just wears its own tier's colour. One name, so a
    // caller never has to know whether a celebration is running.
    vars: { "--climb-color": rankColor(tier), ...vars },
    icon,
  };
}

/**
 * `rank` is `{tier, division, fill}` — identity only, the three values the
 * server already grades. `identity` is what the caller considers "the same
 * measurement": when it changes, the hook SNAPS instead of climbing.
 *
 * That gate is what stops a false celebration. Switching the active strategy
 * re-grades the banner on a different ladder, changing the rank mode
 * re-grades on a different basis, and picking a new target replaces the
 * entity outright — all three legitimately produce a higher rank without
 * anyone having earned anything, and all three would otherwise fire a
 * four-second level-up.
 *
 * Returns `{tier, division, fill, vars, icon, climbing}`. `vars` goes on the
 * surface's own style (it carries `--climb-color` and the effect variables);
 * `icon` is a ready-assembled prop bundle for `RankIcon`, spread at the call
 * site so no surface ever builds icon props itself.
 */
export function useRankClimb(rank, identity = null) {
  const target = rank && rank.tier
    ? rankPosition(rank.tier, rank.division, rank.fill || 0) : null;

  const climbToken = useRef(0);
  if (climbToken.current === 0) climbToken.current = ++nextClimbToken;
  const positionRef = useRef(null);
  const identityRef = useRef(identity);
  const beatsRef = useRef([]);
  const frameRef = useRef(null);
  const [state, setState] = useState(() =>
    (target == null ? null : renderState(target, [], now())));

  useEffect(() => {
    if (frameRef.current != null) cancelAnimationFrame(frameRef.current);
    frameRef.current = null;

    const identityChanged = identityRef.current !== identity;
    identityRef.current = identity;

    setClimbing(climbToken.current, false);

    if (target == null) {
      positionRef.current = null;
      beatsRef.current = [];
      setState(null);
      return undefined;
    }

    const from = positionRef.current;
    const snap = from == null              // first value ever: nothing to climb from
      || identityChanged                   // a different measurement entirely
      || target <= from                    // never animate a regression
      || !celebrationsEnabled()            // the user turned celebrations off
      || prefersReducedMotion();
    if (snap) {
      positionRef.current = target;
      beatsRef.current = [];
      setState(renderState(target, [], now()));
      return undefined;
    }

    // A climb already in flight retargets from where it IS (positionRef is
    // updated every tick), never from where the last one began -- two
    // attempts landing close together must not snap back before continuing.
    const startPosition = from;
    const startedAt = now();
    const durationMs = climbDurationBetween(startPosition, target);
    // Totals for the WHOLE climb, stamped onto every beat so an effect can
    // gate on how big a deal this was (`when: (beat) => beat.tiersGained >= 2`)
    // without the registry needing to see the climb itself.
    const divisionsGained = Math.floor(target) - Math.floor(startPosition);
    const tiersGained = Math.floor(Math.floor(target) / DIVISIONS_PER_TIER)
      - Math.floor(Math.floor(startPosition) / DIVISIONS_PER_TIER);

    beatsRef.current = [];
    let level = Math.floor(startPosition);
    let settled = false;

    const tick = () => {
      const at = now();
      const elapsed = at - startedAt;
      const position = climbPosition(startPosition, target, elapsed);
      positionRef.current = position;

      // Every level the frame passed gets its own beat, even if one frame
      // crossed two of them -- a dropped frame during a fast climb must not
      // silently swallow a celebration.
      while (Math.floor(position) > level) {
        level += 1;
        const before = rankAt(level - 1);
        const after = rankAt(level);
        beatsRef.current.push(makeBeat({
          kind: before.tier === after.tier ? "division" : "tier",
          at, level, from: before, to: after, tiersGained, divisionsGained,
        }));
      }

      const landed = elapsed >= durationMs;
      if (landed && !settled) {
        settled = true;
        const here = rankAt(target);
        beatsRef.current.push(makeBeat({
          kind: "settle", at, level: Math.floor(target),
          from: here, to: here, tiersGained, divisionsGained,
        }));
      }

      setState(renderState(position, beatsRef.current, at));

      // Keep ticking past the landing until the slowest effect has finished,
      // or the last flap would freeze mid-beat.
      const tail = beatsRef.current.length
        ? beatsRef.current[beatsRef.current.length - 1].at
          + Math.max(celebrationTailMs("division"), celebrationTailMs("tier"),
                     celebrationTailMs("settle"))
        : 0;
      if (!landed || at < tail) {
        frameRef.current = requestAnimationFrame(tick);
      } else {
        beatsRef.current = [];
        frameRef.current = null;
        setClimbing(climbToken.current, false);
        setState(renderState(target, [], at));
      }
    };
    // The hold opens HERE, with the first frame, and closes in the two
    // places the loop can end -- its own last tick, and this cleanup for an
    // unmount mid-climb. A token left behind would freeze the practice page.
    setClimbing(climbToken.current, true);
    frameRef.current = requestAnimationFrame(tick);
    return () => {
      if (frameRef.current != null) cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
      setClimbing(climbToken.current, false);
    };
  }, [target, identity]);

  return state && { ...state, climbing: frameRef.current != null };
}
