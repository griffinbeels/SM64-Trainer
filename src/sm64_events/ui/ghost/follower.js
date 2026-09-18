// src/sm64_events/ui/ghost/follower.js — THE GHOST IS A FUNCTION OF THE CLOCK.
//
// The overlay does not play a video. Every game frame it shows the template
// picture whose game timer equals the live timer plus the player's nudge.
// That one rule answers all five things the feature had to detect, with no
// detector anywhere:
//
//   paused / Usamune menu -> the counter stops -> the ghost holds
//   reset                 -> the counter returns to zero -> the ghost does too
//   save-state load       -> the game restores its counter -> the ghost jumps
//   timing nudge          -> nudge += n, and the same rule retargets
//   emulator lag          -> nothing here runs on wall time, so nothing drifts
//
// This module is PURE and import-light on purpose: it takes the clip's view
// payload, clock samples and `nudge_frames`, and answers with decisions
// (`play` at a rate, `hold`, `seek` to a clip time). Nothing in it touches a
// DOM element, so node can drive it over a trace and score its error in game
// frames -- which is the only way to claim "never more than a frame out".
//
// ALL slot <-> clip-time arithmetic goes through `ui/frame.js`, the owner: a
// picture-feed clip is variable rate with no grid at all, a constant-rate clip
// counts from the cut's own `video_start_s`, and every seek lands in the
// MIDDLE of a picture. What this module adds is the one lookup nothing owned
// yet -- the game's own timer per picture (`picture_igt`) to a slot, the
// reverse of `inputtimelinemodel.js::inspectorClock`. It is not a second copy
// of the FRAME map: that map keys on the emulator-wide frame counter, this one
// on Usamune's attempt counter, which is the number the live snapshot carries.
import { clipClock, slotAtTime, timeOfSlot } from "../frame.js";

export const DEFAULTS = {
  // Beyond this many counter frames in one sample, the clock did not get here
  // by PLAYING. Play adds one frame per game frame (two under a hiccup); a
  // reset lands near zero and a state load lands anywhere.
  jumpFrames: 4,
  // Drift the ghost may absorb by changing speed. Past it, seek: catching up
  // over seconds is exactly the "it is out of step" the feature exists to
  // remove.
  seekFrames: 6,
  // Seconds for the rate correction to close the remaining error.
  correctionTau: 0.5,
  minRate: 0.5,
  maxRate: 1.6,
  // No sample for this long and the game is not running (the emulator itself
  // is paused, or the server went away): hold rather than run on wall time.
  starveMs: 200,
};

const HOLD = (reason, time = null) => ({ action: "hold", rate: 0, time, reason });

// -- the template -----------------------------------------------------------

/**
 * Index a clip's view payload by the game's own timer.
 *
 * `picture_igt[slot]` is the counter Usamune held when the capture layer drew
 * that picture, so the index is just its ascending non-null entries. A
 * template whose counter goes BACKWARD is refused by name: an area warp or a
 * state load inside the recording makes one counter value name two different
 * pictures, and picking one of them silently is how a ghost ends up confidently
 * wrong. That is the prototype's stated scope -- single-area stars.
 */
export function ghostTemplate(view) {
  const stamps = view && view.picture_igt;
  if (!Array.isArray(stamps) || !stamps.length)
    return { ok: false, reason: "no_picture_timer" };
  const clock = clipClock(view);
  const igts = [];
  const slots = [];
  for (let slot = 0; slot < stamps.length; slot += 1) {
    const igt = stamps[slot];
    if (igt == null) continue;
    if (igts.length && igt < igts[igts.length - 1])
      return { ok: false, reason: "counter_restarts" };
    igts.push(igt);
    slots.push(slot);
  }
  if (!igts.length) return { ok: false, reason: "no_picture_timer" };

  // The first entry at or after `igt`. A counter the capture missed answers
  // with the next picture the template would have shown -- the same rule
  // `inputtimelinemodel.js::mappedTimeAtFrame` uses for a skipped frame.
  const entryAt = (igt) => {
    let low = 0, high = igts.length;
    while (low < high) {
      const mid = (low + high) >> 1;
      if (igts[mid] < igt) low = mid + 1; else high = mid;
    }
    return low;
  };

  const firstTime = timeOfSlot(slots[0], clock);
  const lastTime = timeOfSlot(slots[slots.length - 1], clock);

  return {
    ok: true, reason: null, clock, firstTime, lastTime,
    firstIgt: igts[0], lastIgt: igts[igts.length - 1], pictures: igts.length,

    /** Where in the clip the picture for this counter value sits, and
     *  whether the template even reaches that far. */
    timeAtIgt(igt) {
      if (igt < igts[0]) return { time: firstTime, clamped: "before" };
      const at = entryAt(igt);
      if (at >= slots.length) return { time: lastTime, clamped: "after" };
      return { time: timeOfSlot(slots[at], clock), clamped: null };
    },

    /** Which counter value the picture at this clip time carries -- how the
     *  ghost's error is measured, in game frames. */
    igtAtTime(seconds) {
      const raw = slotAtTime(seconds, clock);
      let slot = Math.max(0, Math.min(stamps.length - 1, raw));
      while (slot >= 0 && stamps[slot] == null) slot -= 1;
      return slot < 0 ? igts[0] : stamps[slot];
    },
  };
}

// -- the follower -----------------------------------------------------------

const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

/**
 * A follower for one template.
 *
 * `sample(clock, videoTime, nowMs)` takes a clock message
 * (`{global_timer, igt, level, area}`), where the ghost video currently sits,
 * and the page's own clock, and returns the decision for this game frame.
 * `idle(videoTime, nowMs)` is the same answer when no sample has arrived.
 *
 * `options.expect` ({level, area}) holds the ghost while the player is
 * somewhere the template never was; leave it out and the check is skipped.
 */
export function createFollower(view, options = {}) {
  const cfg = { ...DEFAULTS, ...options };
  const template = ghostTemplate(view);
  const expect = options.expect || null;
  let nudge = options.nudgeFrames || 0;
  let lastIgt = null;
  let lastSampleMs = null;
  //: why the next sample must PLACE the ghost rather than play it on: a
  //: reason string, or null when the follower is simply following. It holds
  //: the reason rather than a flag so a placement can say what caused it,
  //: which is the first thing a live diagnosis asks.
  let retarget = "first_sample";

  const away = (sample) =>
    expect && (sample.level !== expect.level
               || (expect.area != null && sample.area !== expect.area));

  // WHY the ghost is being placed rather than played. What the GAME did wins
  // over the follower's own bookkeeping: a counter that just returned to zero
  // is a reset whatever else was pending, and saying otherwise would send a
  // live diagnosis to the wrong half of the system.
  const placement = (moved, target, asked) => {
    if (moved === null) return "first_sample";
    if (moved < 0) return target <= template.firstIgt ? "reset" : "load_back";
    if (moved > cfg.jumpFrames) return "load_forward";
    return asked || "nudge";
  };

  // The counter is running normally: hold where it stopped, resync a drift
  // too big to walk off, else play at game speed and correct.
  const running = (moved, target, time, videoTime) => {
    const drift = template.igtAtTime(videoTime) - target;
    if (moved === 0) {
      // Pause screen, Usamune menu, a dialogue box, a reload sitting at
      // zero. Hold exactly where the rule says; only move the picture if
      // steady play had already drifted off it.
      return Math.abs(drift) > 1
        ? { action: "seek", rate: 0, time, reason: "stalled" }
        : HOLD("stalled", time);
    }
    if (Math.abs(drift) > cfg.seekFrames)
      return { action: "seek", rate: 1, time, reason: "resync" };
    // The clip's time axis IS the game's own wall clock -- one picture per
    // game frame at its own timestamp -- so rate 1 is game speed and the
    // correction only absorbs the emulator running slightly off 30.
    const rate = clamp(1 + (time - videoTime) / cfg.correctionTau,
                       cfg.minRate, cfg.maxRate);
    return { action: "play", rate, time, reason: "playing" };
  };

  // Every decision carries the target it was aimed at and the DRIFT it saw:
  // how many game frames the picture on screen was from the picture the rule
  // named, at the moment of deciding. Measuring that afterwards from outside
  // reads the video's own progress since, not the follower's error.
  const decide = (sample, videoTime) => {
    const decision = choose(sample, videoTime);
    if (!template.ok || away(sample)) return decision;
    const target = sample.igt + nudge;
    return { ...decision, target,
             drift: template.igtAtTime(videoTime) - target };
  };

  const choose = (sample, videoTime) => {
    if (!template.ok) return HOLD(template.reason);
    if (away(sample)) {
      // Coming back is a jump, whatever the counter did while away.
      retarget = "elsewhere";
      lastIgt = sample.igt;
      return HOLD("elsewhere");
    }
    const target = sample.igt + nudge;
    const { time, clamped } = template.timeAtIgt(target);
    const moved = lastIgt === null ? null : sample.igt - lastIgt;
    const asked = retarget;
    lastIgt = sample.igt;
    retarget = null;

    if (asked || moved === null || moved < 0 || moved > cfg.jumpFrames) {
      // Hold after placing it: the next sample says whether the clock is
      // running again, and one game frame of waiting beats guessing about a
      // reload that has not finished.
      return { action: "seek", rate: 0, time,
               reason: placement(moved, target, asked) };
    }
    if (clamped)
      return HOLD(clamped === "before" ? "before_template" : "past_template", time);
    return running(moved, target, time, videoTime);
  };

  return {
    template,
    get nudge() { return nudge; },
    setNudge(frames) {
      const next = Math.round(Number(frames) || 0);
      if (next === nudge) return nudge;
      nudge = next;
      retarget = "nudge";   // a deliberate gesture snaps, it does not drift in
      return nudge;
    },
    sample(clock, videoTime, nowMs) {
      lastSampleMs = nowMs;
      return decide(clock, videoTime);
    },
    /** No clock for a while: the emulator itself is paused, or the feed is
     *  gone. Either way the ghost must stop rather than run on wall time. */
    idle(videoTime, nowMs) {
      if (lastSampleMs === null || nowMs - lastSampleMs < cfg.starveMs) return null;
      retarget = "no_clock";
      return HOLD("no_clock");
    },
  };
}
