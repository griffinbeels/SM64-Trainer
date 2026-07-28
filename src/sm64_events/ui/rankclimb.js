// src/sm64_events/ui/rankclimb.js — the level-up climb, as one hook.
//
// THE fix for task 0012. A rank is three values that move together (tier,
// division, and how far through that division you are) and the surfaces used
// to animate the third alone, through useTween — so crossing a boundary sent
// the server's within-division `fill` from .95 to .05 and the bar ran
// BACKWARDS on the one event it exists to celebrate.
//
// This hook walks a PLAN instead: an ordered list of steps, each naming the
// ladder level it shows and where the bar sits across it. A rank-up is a step
// boundary, and the bar is pinned FULL across every one of them until the
// destination is reached (user, 2026-07-27: "ONCE IT'S FULL, AND IF WE HAVE
// MORE RANKS TO FULLY LEVEL UP THROUGH, IT STAYS FULL"), which one continuous
// position could not express — see ui/climbplan.js's header for why.
//
// Four collaborators, each replaceable on its own:
//   ui/climbplan.js     WHAT HAPPENS, IN WHAT ORDER (import-free, node-tested)
//   ui/climbcurve.js    HOW FAST (import-free, node-tested)
//   ui/celebrations.js  WHAT IT LOOKS LIKE (the registry — add effects there)
//   caps.js             WHICH RANK a level is (import-free, pinned to Python)
// This file owns only the loop and the bookkeeping between them.
import { useEffect, useRef, useState } from "preact/hooks";
import { rankPosition, rankAt, rankColor, DIVISIONS_PER_TIER } from "./components/caps.js";
import { climbTimings, barEase } from "./climbcurve.js";
import { buildClimbPlan } from "./climbplan.js";
import { activeEffects, makeBeat, celebrationTailMs, celebrationsEnabled }
  from "./celebrations.js";
import { tuning } from "./climbtuning.js";
import { prefersReducedMotion } from "./useTween.js";

const now = () => performance.now();

// ---- Climb LANES: two banners on one card climb in turn -----------------
//
// "the 'Strategy' rank should rank up first. Then, the 'Star' rank should
// rank up. It should be sequential" (user, 2026-07-27). Both banners get the
// same lane (their entity) and an explicit order, and a climb simply starts
// when the lane is free rather than negotiating with its sibling: each one
// publishes the timestamp it will finish at, and the next order waits for it.
//
// A scheduler rather than a queue because it needs no coordination at all --
// a lone banner, or the MARELO bar, passes no lane and starts immediately,
// and a stale entry is harmless since the start time is always
// `max(now, laneFree)`.
const laneEnds = new Map();
// How many climb hooks are mounted in each lane, and when the lane's FIRST
// one appeared. Together these tell a card MOUNTING (page load, switching to
// a different star -- every banner on it must snap, or the whole page would
// animate on every refresh) from a banner ARRIVING LATE into a card that was
// already on screen, which is a rank being earned for the first time and is
// exactly what to celebrate.
//
// The member count is what makes the lane's start time reusable: it is
// cleared when the last banner in the lane unmounts, so navigating back to a
// star later reads as a fresh mount rather than as a very late arrival.
const laneMembers = new Map();
const laneFirstSeen = new Map();
// Anything within this of the lane's first banner is part of the same mount.
// Both banners of a card render in one tick; a banner that appears because a
// strategy was just picked is seconds away.
const LANE_MOUNT_GRACE_MS = 400;
// The gap between the two climbs is `laneGapMs` in ui/climbtuning.js — enough
// that the second reads as following the first rather than as the same
// animation continuing.


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
const HOLD_CEILING_MS = 20000;

function setClimbing(token, running) {
  const wasHolding = isCelebrating();
  if (running) liveClimbs.add(token);
  else liveClimbs.delete(token);
  if (!liveClimbs.size) holdReleased = false;   // the release lasts one climb
  const holding = isCelebrating();
  if (wasHolding !== holding) holdListeners.forEach((notify) => notify(holding));
}

/** True while any rank on screen is mid-climb. */
// A user GESTURE beats the hold. The hold exists so the GAME cannot move the
// page on mid-celebration (walking out of the stage); it was never meant to
// stop the player clicking a different star (live report 2026-07-27: "it
// should let me immediately jump to the other star"). Sticky until the last
// climb ends, so the page does not re-freeze on the next frame; the abandoned
// climb simply unmounts, and coming back later shows the finished rank
// because a remount snaps.
let holdReleased = false;
export function releaseCelebrationHold() {
  if (!liveClimbs.size || holdReleased) return;
  holdReleased = true;
  holdListeners.forEach((notify) => notify(false));
}

export const isCelebrating = () => liveClimbs.size > 0 && !holdReleased;

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

// `level` and `bar` are independent now, which is the whole point: a pinned
// bar sits at 1 while the level keeps ticking. It also retires the old
// "position 45 has a fractional part of zero, so a maxed rank empties its own
// bar" trap outright — a maxed rank is level 44 with `bar` 1, and there is no
// arithmetic in between to get wrong.
function renderState(level, bar, beats, atMs, tune, reveal = 1, landing = false) {
  const { tier, division } = rankAt(level);
  const { vars, icon } = activeEffects(beats, atMs, tune);
  return {
    tier, division, level, fill: bar, reveal, landing,
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
export function useRankClimb(rank, identity = null,
                             { lane = null, order = 0, replayKey = null,
                               tune: tuneOverride = null } = {}) {
  // Level and fill are kept APART from here down. `rankPosition(..., 0)` is
  // exactly the level, and asking for it with a fill of 1 (which ranks.js
  // legitimately sends for a maxed rank) would return 45 — a level that does
  // not exist. Keeping them separate is what makes that unrepresentable
  // rather than clamped.
  const targetLevel = rank && rank.tier
    ? rankPosition(rank.tier, rank.division, 0) : null;
  const targetFill = targetLevel == null
    ? 0 : Math.max(0, Math.min(1, rank.fill || 0));
  // One number for the comparisons that only ever ask "is this the same
  // measurement / is it higher": the effect's dependency, the retarget check,
  // and the never-animate-a-regression guard.
  const target = targetLevel == null ? null : targetLevel + targetFill;

  const climbToken = useRef(0);
  if (climbToken.current === 0) climbToken.current = ++nextClimbToken;
  // Counts effect RUNS, not renders: the first run is a mount (snap) and a
  // later one with no prior position is a first rank being earned (climb).
  const runsRef = useRef(0);
  // `{level, bar}` as last drawn — what a retarget mid-climb resumes from. A
  // pinned bar resumes at 1, so a second PB landing mid-climb continues up the
  // ladder instead of replaying an approach it already finished.
  const shownRef = useRef(null);
  // Lane membership is tracked on true mount/unmount, so it must not ride the
  // main effect (which re-runs on every identity or target change).
  const arrivedLateRef = useRef(false);
  // What an in-flight climb is heading for, and whether one was in flight.
  // Cleared only when the loop ENDS -- never in the effect cleanup, which
  // runs before every re-entry and so cannot tell "interrupted" from "done".
  const climbingToRef = useRef(null);
  const replayKeyRef = useRef(replayKey);
  useEffect(() => {
    if (!lane) return undefined;
    laneMembers.set(lane, (laneMembers.get(lane) || 0) + 1);
    const firstSeen = laneFirstSeen.get(lane);
    if (firstSeen == null) laneFirstSeen.set(lane, now());
    else arrivedLateRef.current = now() - firstSeen > LANE_MOUNT_GRACE_MS;
    return () => {
      const left = (laneMembers.get(lane) || 1) - 1;
      if (left > 0) { laneMembers.set(lane, left); return; }
      laneMembers.delete(lane);
      laneFirstSeen.delete(lane);
    };
  }, [lane]);
  const identityRef = useRef(identity);
  const beatsRef = useRef([]);
  const frameRef = useRef(null);
  const [state, setState] = useState(() =>
    (targetLevel == null ? null : renderState(targetLevel, targetFill, [], now(), tuning())));

  useEffect(() => {
    if (frameRef.current != null) cancelAnimationFrame(frameRef.current);
    frameRef.current = null;

    // Read ONCE, here, and carried through the whole climb: a tuning change
    // mid-flight would retime an animation that is already half-played, and
    // the inspector at /ui/tune.html works by resetting and re-triggering
    // rather than by mutating a running one.
    //
    // A caller may override it (components/marelocelebrate.js, 2026-07-28):
    // the overall rank-up runs the SAME registry as the banners but louder,
    // and amplifying it is a multiplied tuning rather than a second set of
    // effects. Nothing else passes one.
    const tune = tuneOverride || tuning();

    const identityChanged = identityRef.current !== identity;
    identityRef.current = identity;

    setClimbing(climbToken.current, false);

    if (targetLevel == null) {
      shownRef.current = null;
      beatsRef.current = [];
      setState(null);
      return undefined;
    }

    // A banner that had NO RANK AT ALL and now has one climbs from the FLOOR
    // (user, 2026-07-27: recording a time with no strategy picked, then
    // picking one, "ends up skipping all animations… it should show capless 5
    // -> animate all the way to the achieved rank"). Two things had to be
    // told apart to do that, and both look like `from == null`:
    //
    //   * the FIRST run for this mount -- a page load, or switching to a
    //     different star -- which must snap, or every card on screen would
    //     animate on every refresh;
    //   * a later run on a banner that was showing a sentinel, which is a
    //     first rank being earned and is exactly what to celebrate.
    //
    // An identity change is deliberately IGNORED on that second path: going
    // from "no strategy" to a strategy is not a change of measurement, since
    // there was no measurement before.
    const isFirstRun = runsRef.current === 0;
    runsRef.current += 1;
    const from = shownRef.current;
    // Two shapes of "a first rank was just earned", both of which look like
    // `from == null`: the banner was on screen showing a sentinel and now has
    // a rank, or the banner did not exist at all and has just ARRIVED into a
    // card that was already up (picking a strategy for a star that already
    // has a time -- live report 2026-07-27, "it should show capless 5 ->
    // animate all the way to the achieved rank"). Neither is a page load.
    // "any time we select a strategy for the first time, its rank standard
    // banner should always start from capless 5, and then animate to whatever
    // the first entry is… if we change strats… that new strategy should
    // repeat the animation process" (user, 2026-07-27). A strategy's rank is
    // a measurement being SHOWN for the first time, so it is earned in the
    // same sense a first-ever rank is -- it just happens to already have a
    // number behind it. Only the STRATEGY banner passes a replayKey: the
    // star's own rank does not depend on which strategy is selected, so it
    // must sit still through the switch.
    const replayed = replayKeyRef.current !== replayKey;
    replayKeyRef.current = replayKey;
    const earnedFirstRank = target > 0
      && ((from == null && (!isFirstRun || arrivedLateRef.current))
          || (replayed && !isFirstRun));
    // A climb already heading for THIS target keeps going, whatever the
    // identity says. Picking a strategy changes a card's identity but not the
    // star's own rank, and snapping there cut the animation off at the exact
    // moment the user had just triggered it (live report 2026-07-27). An
    // identity change still snaps when the target actually moved -- that IS a
    // different measurement.
    const continuing = climbingToRef.current === target;
    const startLevel = earnedFirstRank ? 0 : (from ? from.level : null);
    const startFill = earnedFirstRank ? 0 : (from ? from.bar : 0);
    const startPosition = startLevel == null ? null : startLevel + startFill;
    const snap = (from == null && !earnedFirstRank)   // nothing to climb from
      || (!earnedFirstRank && !continuing && identityChanged)  // a different measurement
      || (startPosition != null && target <= startPosition)   // never a regression
      || !celebrationsEnabled()            // the user turned celebrations off
      || prefersReducedMotion();
    if (snap) {
      climbingToRef.current = null;
      shownRef.current = { level: targetLevel, bar: targetFill };
      beatsRef.current = [];
      setState(renderState(targetLevel, targetFill, [], now(), tuning()));
      return undefined;
    }

    // A climb already in flight retargets from where it IS (`shownRef` is
    // updated every tick), never from where the last one began -- two
    // attempts landing close together must not snap back before continuing.
    // (`startLevel`/`startFill` are resolved above, so a first-ever rank
    // starts at the floor rather than at nothing.)
    const plan = buildClimbPlan({
      fromLevel: startLevel, fromFill: startFill,
      toLevel: targetLevel, toFill: targetFill,
      divisionsPerTier: DIVISIONS_PER_TIER,
      skipStyle: tune.skipStyle,
      timings: (counts) => climbTimings(counts, tune),
    });
    const { anticipateMs, payoffMs, ladderMs } = plan.timings;
    // Totals for the WHOLE climb, stamped onto every beat so an effect can
    // gate on how big a deal this was (`when: (beat) => beat.tiersGained >= 2`)
    // without the registry needing to see the climb itself.
    const divisionsGained = targetLevel - startLevel;
    const tiersGained = Math.floor(targetLevel / DIVISIONS_PER_TIER)
      - Math.floor(startLevel / DIVISIONS_PER_TIER);

    // Wait for the lane, if this climb is in one and is not first in it.
    // Until `startedAt`, `elapsed` is negative, no step has been entered and
    // the banner simply renders the rank it already had -- no special
    // "waiting" state, and the hold below covers the wait as well as the climb.
    const tailSample = { anticipateMs, payoffMs, stepMs: ladderMs };
    const tailMs = Math.max(celebrationTailMs("division", tailSample, tune),
                            celebrationTailMs("tierskip", tailSample, tune),
                            celebrationTailMs("tier", tailSample, tune),
                            celebrationTailMs("settle", tailSample, tune));
    // The next banner starts when this one's POSITION lands, not when its
    // last sparkle fades: waiting out the effect tail as well made the gap
    // read as dead air (live report 2026-07-27, "the timing gap … is too
    // long"). The two tails overlap by design now -- the first banner is
    // settling while the second starts climbing, which is what makes them
    // read as one gesture in two parts rather than two animations.
    const totalMs = plan.totalMs;
    // Whether this climb ends with a bar sweep at all -- a plain fill
    // improvement has no rank change, so its next-step line must never fade.
    const hasArrive = plan.steps.some((step) => step.kind === "arrive");
    const laneFreeAt = lane ? (laneEnds.get(lane) || 0) : 0;
    const startedAt = (lane && order > 0)
      ? Math.max(now(), laneFreeAt + tune.laneGapMs) : now();
    const laneEndsAt = startedAt + totalMs;
    if (lane) laneEnds.set(lane, laneEndsAt);

    beatsRef.current = [];
    // The last step ENTERED. -1 while the lane is still busy, which is what
    // makes the wait render as the rank the banner already had.
    let stepIndex = -1;
    let settled = false;

    const tick = () => {
      const at = now();
      const elapsed = at - startedAt;

      // Enter every step whose start time has passed, even if one frame
      // swallowed several -- a dropped frame during a compressed climb must
      // not silently swallow a celebration.
      while (stepIndex + 1 < plan.steps.length
             && elapsed >= plan.steps[stepIndex + 1].at) {
        stepIndex += 1;
        const entered = plan.steps[stepIndex];
        if (!entered.beat) continue;
        beatsRef.current.push(makeBeat({
          kind: entered.kind, at, level: entered.level,
          from: rankAt(entered.beat.fromLevel), to: rankAt(entered.beat.toLevel),
          tiersGained, divisionsGained, anticipateMs, payoffMs,
          stepMs: entered.ms,
        }));
      }

      // Level and bar are resolved from DIFFERENT steps, because the closing
      // bar sweep runs alongside ranks that are still ticking (climbplan.js's
      // `finalBarOverlap`). The rank comes from the last step that owns one;
      // the bar from the last step that actually MOVES it, held at that step's
      // end value once its window closes.
      //
      // That "held" rule is what keeps the bar pinned full through the middle
      // with no branch for it: the approach ends at 1, every step after it is
      // `barFrom === barTo === 1`, so the last bar-MOVING step is still the
      // approach and its end value is 1 — the pin falls out of the arithmetic
      // rather than being a case anyone has to remember.
      let level = startLevel;
      let bar = startFill;
      let barStep = null;
      for (let index = 0; index <= stepIndex; index += 1) {
        const step = plan.steps[index];
        if (step.ownsLevel !== false) level = step.level;
        if (step.barFrom !== step.barTo) barStep = step;
      }
      let eased = 1;
      if (barStep) {
        const within = barStep.ms > 0
          ? Math.max(0, Math.min(1, (elapsed - barStep.at) / barStep.ms)) : 1;
        eased = barEase(within);
        bar = barStep.barFrom + (barStep.barTo - barStep.barFrom) * eased;
      } else if (stepIndex >= 0) {
        bar = plan.steps[stepIndex].barFrom;
      }
      // The "X.XXs to rank up" line fades out as the bar fills at the START and
      // back in as it fills at the END, off the SAME step and the SAME eased
      // progress the bar just used -- so "these should be in sync" (user,
      // 2026-07-27) is structural rather than a duration someone matched by
      // hand. It used to be a celebration beat fired on settle, which could
      // only ever be tuned to line up, and did not.
      //
      // Between the two it sits at 0 without a rule of its own: mid-climb the
      // last bar-MOVING step is the finished approach, whose eased progress is
      // 1, so `1 - eased` is 0. A climb with no approach at all (the bar was
      // already full) has no bar step to read yet, which is the one case that
      // needs saying out loud.
      const arriving = barStep && barStep.kind === "arrive";
      const reveal = !hasArrive ? 1               // no rank change: never hide it
        : arriving ? eased                        // fading back in with the bar
        : (barStep && barStep.kind === "approach") ? 1 - eased
        : 0;
      shownRef.current = { level, bar };

      const landed = elapsed >= totalMs;
      if (landed && !settled) {
        settled = true;
        const here = rankAt(targetLevel);
        beatsRef.current.push(makeBeat({
          kind: "settle", at, level: targetLevel,
          from: here, to: here, tiersGained, divisionsGained,
          anticipateMs, payoffMs, stepMs: ladderMs,
        }));
      }

      setState(renderState(level, bar, beatsRef.current, at, tune, reveal, !!arriving));

      // Keep ticking past the landing until the slowest effect has finished,
      // or the last flap would freeze mid-beat.
      const tail = beatsRef.current.length
        ? beatsRef.current[beatsRef.current.length - 1].at
          + tailMs
        : 0;
      if (!landed || at < tail) {
        frameRef.current = requestAnimationFrame(tick);
      } else {
        beatsRef.current = [];
        frameRef.current = null;
        climbingToRef.current = null;
        setClimbing(climbToken.current, false);
        shownRef.current = { level: targetLevel, bar: targetFill };
        setState(renderState(targetLevel, targetFill, [], at, tune));
      }
    };
    // The hold opens HERE, with the first frame, and closes in the two
    // places the loop can end -- its own last tick, and this cleanup for an
    // unmount mid-climb. A token left behind would freeze the practice page.
    setClimbing(climbToken.current, true);
    climbingToRef.current = target;
    frameRef.current = requestAnimationFrame(tick);
    return () => {
      if (frameRef.current != null) cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
      setClimbing(climbToken.current, false);
      // Free the lane if this climb still owns it -- an abandoned climb must
      // not keep its sibling waiting for a slot it will never use.
      if (lane && laneEnds.get(lane) === laneEndsAt) laneEnds.delete(lane);
    };
  }, [target, identity, lane, order, replayKey, tuneOverride]);

  return state && { ...state, climbing: frameRef.current != null };
}
