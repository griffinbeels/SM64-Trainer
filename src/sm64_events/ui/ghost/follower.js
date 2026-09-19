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

// The counter band that means "a run has just started". NOT a number invented
// here: `detectors/anchors.py::NEAR_ZERO_IGT` is this project's one definition
// of when the Usamune counter has restarted, shared by the anchor detector and
// `igt_clock.py` so there are not two opinions about it. This is its JS twin
// and `tests/test_ui_ghost_follower.py` fails if the two ever disagree.
export const NEAR_ZERO_IGT = 30;   // 30 frames = 1 s at 30 fps

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
 * that picture, so the index is its ascending non-null entries -- FROM THE
 * ATTEMPT'S OWN START.
 *
 * EVERY SAVED CLIP OF A STAR BEGINS BEFORE THE RESET THAT STARTED IT. The
 * recorder keeps a lead-in, and through the reload the counter still holds the
 * PREVIOUS run's final value, so a real clip opens with a stretch of pictures
 * stamped with a number from a run that is over and then drops to near zero.
 * (His first real template, star 6-0: 44 pictures frozen at 3029, then 3
 * climbing to 584.) Those lead-in pictures are PRE-ROLL: the live counter can
 * never name one, so the ghost never shows one, and the template's zero is the
 * last drop into the near-zero band -- `NEAR_ZERO_IGT`, the same band
 * `anchors.py` calls a restart.
 *
 * A counter that restarts AGAIN after that zero is still refused by name: an
 * area warp mid-run (Usamune restarts the counter per leg) or a state load
 * inside the recording makes one counter value name two different pictures, and
 * picking one of them silently is how a ghost ends up confidently wrong. So is
 * a single restart that does NOT land near zero, which is a load rather than a
 * start. One restart, at the start, into the band is the ORDINARY clip and the
 * refusal that rejected it rejected essentially every real one (his first try,
 * 2026-09-19: a black screen, no footage). The prototype's stated scope is
 * unchanged -- single-area stars.
 *
 * WHEN THE SOURCE KNOWS THE SPAN, IT WINS. His ruling: *"Ghost footage should
 * be clamped to the EXACT START FRAME of the attempt, and the exact ending
 * frame of the star grab... regardless of the user's recording settings."* So
 * the source may hand over `{first_picture, last_picture}` -- inclusive indices
 * into these same per-picture arrays, computed from the attempt's own game
 * frames through the clip's frame map. The span narrows WHICH PICTURES THE
 * TEMPLATE IS BUILT FROM and nothing else: everything above still applies
 * inside it, so an area crossing is still refused and an off-by-one that leaves
 * one picture of lead-in inside the span is absorbed rather than fatal. No
 * span (an older server, a clip whose frames cannot be resolved) falls back to
 * the timer rule for the start and the clip's own last picture for the end.
 */
function attemptPictures(stamps, span) {
  const bound = (value, fallback) =>
    (Number.isFinite(value) ? Math.max(0, Math.min(stamps.length - 1, Math.trunc(value)))
                            : fallback);
  const lo = bound(span && span.first_picture, 0);
  const hi = bound(span && span.last_picture, stamps.length - 1);
  // Every picture the capture actually stamped, inside the span. A gap (the
  // capture layer refuses a picture rather than guessing its frame) is
  // skipped, never interpolated: this list is what the counter is looked up
  // in, and nothing outside it is ever shown.
  const read = [];
  for (let slot = lo; slot <= hi; slot += 1)
    if (stamps[slot] != null) read.push(slot);
  if (!read.length)
    return { reason: span ? "span_has_no_pictures" : "no_picture_timer" };

  // Where the attempt starts. Repeated values are NOT restarts -- the capture
  // runs at 60 fps over a 30 fps game, so several pictures legitimately carry
  // one counter value, and the clip's tail holds the final value while the
  // star cutscene plays.
  const restarts = [];
  for (let k = 1; k < read.length; k += 1)
    if (stamps[read[k]] < stamps[read[k - 1]]) restarts.push(k);
  const zero = restarts.length ? restarts[restarts.length - 1] : 0;
  if (restarts.length > 1 || (restarts.length && stamps[read[zero]] > NEAR_ZERO_IGT))
    return { reason: "counter_restarts" };
  return { read, zero, reason: null };
}

export function ghostTemplate(view, span = null) {
  const stamps = view && view.picture_igt;
  if (!Array.isArray(stamps) || !stamps.length)
    return { ok: false, reason: "no_picture_timer" };
  const clock = clipClock(view);
  const { read, zero, reason } = attemptPictures(stamps, span);
  if (reason) return { ok: false, reason };

  const igts = [];
  const slots = [];
  for (let k = zero; k < read.length; k += 1) {
    slots.push(read[k]);
    igts.push(stamps[read[k]]);
  }
  //: the first and last pictures the ghost may ever show. Outside them lies
  //: the run that ended before this one and the cutscene after the star.
  const zeroSlot = slots[0];
  const lastSlot = slots[slots.length - 1];

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
    //: how many stamped pictures the clip holds BEFORE the attempt's own
    //: start. A live diagnosis reads it through `window.ghostStatus()`: a
    //: template with none was cut after the reset (or clamped to a span that
    //: starts there), one with hundreds is a clip with a long lead-in.
    prerollPictures: zero, zeroSlot, lastSlot,
    //: how the run's bounds were decided: the source's exact span, or this
    //: module's own reading of the counter. It is the first thing to look at
    //: when a ghost shows a frame he did not expect.
    basis: span ? (span.basis || "span") : "counter_restart",

    /** Where in the clip the picture for this counter value sits, and
     *  whether the template even reaches that far. */
    timeAtIgt(igt) {
      if (igt < igts[0]) return { time: firstTime, clamped: "before" };
      const at = entryAt(igt);
      if (at >= slots.length) return { time: lastTime, clamped: "after" };
      return { time: timeOfSlot(slots[at], clock), clamped: null };
    },

    /** Which counter value the picture at this clip time carries -- how the
     *  ghost's error is measured, in game frames.
     *
     *  Clamped at the template's zero, so a video still sitting in the
     *  pre-roll answers with the first picture the ghost may show rather than
     *  with a number from the previous run. Unclamped, a clip that has loaded
     *  and not yet been placed reports his own template's 3029 -- a hundred
     *  seconds of game time that belong to a finished attempt, which reads as
     *  a broken clock rather than as a ghost that has not been placed. */
    igtAtTime(seconds) {
      const raw = slotAtTime(seconds, clock);
      let slot = Math.max(zeroSlot, Math.min(lastSlot, raw));
      while (slot > zeroSlot && stamps[slot] == null) slot -= 1;
      return stamps[slot];
    },

    /** Is the element sitting on a picture the ghost must never show -- the
     *  lead-in before the attempt, or the cutscene after the star?
     *
     *  Asked rather than inferred from the counter: `igtAtTime` CLAMPS, so a
     *  video that has run one frame past the last picture answers with the
     *  last picture's value and looks perfectly in step. This is the question
     *  "his ruling" turns on, so it is measured on the clip's own time axis. */
    outsideRun(seconds) {
      const raw = slotAtTime(seconds, clock);
      return raw < zeroSlot || raw > lastSlot;
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
 * `options.span` is the source's own answer for where the attempt starts and
 * ends; leave it out and the template reads that off the counter itself.
 * `options.grabEndsRun` is true for a STAR, whose run ends when he touches it.
 */
export function createFollower(view, options = {}) {
  const cfg = { ...DEFAULTS, ...options };
  const template = ghostTemplate(view, options.span || null);
  const expect = options.expect || null;
  // HIS OWN GRAB ENDS THE GHOST. His ruling: *"The ghost should close when I
  // grab the star (sometimes I might be faster than the ghost!)."* The clock
  // says `star_grab` while Mario is in an action a grab puts him in; a STAR's
  // run is over at that touch.
  //
  // A SEGMENT is not, and that is why this is a choice rather than a rule.
  // Some segments run through a star grab on their way somewhere, and taking
  // the ghost down there would delete the half he was practising. A segment's
  // own end is the segment engine's judgement and it is not on this socket, so
  // a segment keeps the end it already had -- the template's own last picture.
  const grabEndsRun = options.grabEndsRun !== false;
  let nudge = options.nudgeFrames || 0;
  let lastIgt = null;
  let lastSampleMs = null;
  //: LATCHED, not read per sample, and that is the whole behaviour. The action
  //: holds through the dance and then goes away while the player is still
  //: standing in the level with the counter running, so a per-sample test
  //: would put the ghost back up seconds after the star. It comes down at the
  //: touch and stays down until the live timer goes back INSIDE the run, which
  //: only a reset or a state load can do -- exactly what already brings it
  //: back when the ghost finishes first.
  let grabbed = false;
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

  // Does his own grab hold the ghost down this sample? Latch and release in
  // one place: the release is the interesting half and it is easy to read
  // wrong from two.
  //
  // THE RELEASE IS A BACKWARD COUNTER ONLY, and that is narrower than the
  // placement rule above on purpose. Going back INSIDE a run that is over is
  // something only a reset or a load back can do; a counter that is still
  // climbing is the same attempt continuing, whatever size its steps are.
  //
  // Reading a forward JUMP as a release is what the first version did, and a
  // real browser caught it: the clock feed deliberately collapses a burst to
  // its newest sample (`server/ghost_clock.py`), so an ordinary stretch of
  // play that the pump skipped arrives as one big forward step and is
  // indistinguishable from a state load. The ghost came straight back up while
  // he walked around after the star -- the exact overstaying this exists to
  // stop, reintroduced by the rule meant to end it.
  //
  // `clamped !== "after"` survives for the case it was written for: a load
  // back that still lands past the template's last picture has nothing left to
  // show, so it does not raise a ghost either.
  const grabHolds = (sample, moved, clamped) => {
    if (grabEndsRun && sample.star_grab === true) grabbed = true;
    else if (grabbed && moved !== null && moved < 0 && clamped !== "after")
      grabbed = false;
    return grabbed;
  };

  const choose = (sample, videoTime) => {
    if (!template.ok) return HOLD(template.reason);
    if (away(sample)) {
      // Coming back is a jump, whatever the counter did while away. The
      // marker is NOT "elsewhere": the placement that brings the ghost back
      // carries this reason, and a page that reads "elsewhere" keeps the
      // window down -- one frame of blink, for a ghost that is already right.
      retarget = "came_back";
      lastIgt = sample.igt;
      return HOLD("elsewhere");
    }
    const target = sample.igt + nudge;
    const { time, clamped } = template.timeAtIgt(target);
    const moved = lastIgt === null ? null : sample.igt - lastIgt;
    if (grabHolds(sample, moved, clamped)) {
      // Hold exactly where it is; the next reset places it. `retarget` is left
      // alone rather than consumed, so whatever was pending still places when
      // the ghost is allowed up again.
      lastIgt = sample.igt;
      return HOLD("grabbed", time);
    }
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
    if (clamped) {
      // Either end of the attempt's own span: the live counter has not
      // reached the template's first picture yet (the reload after a reset),
      // or it has run past its last (he is slower than the ghost was). Park
      // ON the boundary picture rather than wherever play left the element --
      // one frame past the last picture IS the cutscene the span excludes,
      // and his ruling is that nothing outside the attempt is ever shown.
      const reason = clamped === "before" ? "before_template" : "past_template";
      return template.outsideRun(videoTime)
        ? { action: "seek", rate: 0, time, reason }
        : HOLD(reason, time);
    }
    return running(moved, target, time, videoTime);
  };

  return {
    template,
    get nudge() { return nudge; },
    //: whether his own grab is currently holding the ghost down. Read back
    //: through `window.ghostStatus()`: "the ghost vanished and I do not know
    //: why" has two answers, and this is the one no other field shows.
    get grabbed() { return grabbed; },
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
