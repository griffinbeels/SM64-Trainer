// src/sm64_events/ui/frame.js — shared game-frame video controls.
// SM64 logic is 30 fps; steps move in GAME frames regardless of encode rate.
// Seek to the MIDDLE of the target frame so float rounding never straddles a
// boundary (the fix from replay.js: stepping 1/encode-fps only changed the
// image every 2nd press). Used by the replay player and the compare sync layer.
import { presentedVideoTime } from "./videopicture.js";

export function gameFrameOf(video, gameFps = 30) {
  return Math.floor((video.currentTime || 0) * gameFps + 1e-4);
}

// THE CLIP CLOCK: how a time in the <video> and a video frame index (a
// "slot") convert into each other. Built once from the replay view.
//
// A CFR clip (the pre-2026-09-02 ring) sits on a grid: slot k spans
// [start + k/fps, start + (k+1)/fps), where `start` is the clip's OWN first
// video timestamp -- a cut leaves its sub-frame remainder on the first
// picture (clip 5782: frames at k/60 + 0.011 s), so a seek to (k + 0.5)/60
// lands 2.7 ms BEFORE frame k begins and Chromium presents k-1. Measured on
// the real clip (2026-09-01): every step landed one picture early; with the
// offset, every step landed on its slot.
//
// A picture-feed clip (item 38) is VFR: one video frame per captured
// picture, each at its own timestamp, so there is no grid at all and
// `times[k]` IS the moment frame k begins. Slot k spans [times[k],
// times[k+1]); a seek lands mid-span. Everything that turns a time into a
// slot, or a slot into a time, goes through these two functions -- no
// caller may write k / fps itself.
export function clipClock(view) {
  const times = view && Array.isArray(view.frame_times) && view.frame_times.length
    ? view.frame_times : null;
  return { fps: (view && view.fps) || 60,
           start: (view && view.video_start_s) || 0,
           times,
           duration: (view && view.duration_s) || null,
           identities: (view && view.picture_ids) || null };
}

export function slotAtTime(seconds, clock) {
  if (clock && clock.times) {
    const times = clock.times;
    // The last frame whose start is at or before `seconds` (binary search).
    let lo = 0, hi = times.length - 1;
    if (seconds < times[0]) return -1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (times[mid] <= seconds) lo = mid; else hi = mid - 1;
    }
    return lo;
  }
  const fps = (clock && clock.fps) || 60;
  const start = (clock && clock.start) || 0;
  return Math.floor((seconds - start) * fps + 1e-4);
}

export function timeOfSlot(slot, clock) {
  if (clock && clock.times) {
    const times = clock.times;
    const at = Math.max(0, Math.min(times.length - 1, slot));
    // The final picture may last less than 1/30 s. Its interval ends at
    // the media duration, never at a reconstructed constant-rate boundary.
    const nextStart = at + 1 < times.length ? times[at + 1] : times[at] + 1 / 30;
    const next = clock.duration > times[at]
      ? (at + 1 < times.length ? Math.min(nextStart, clock.duration) : clock.duration)
      : nextStart;
    return (times[at] + next) / 2;
  }
  const fps = (clock && clock.fps) || 60;
  const start = (clock && clock.start) || 0;
  return start + (slot + 0.5) / fps;
}

// A step lands INSIDE a frame, never on the clip's own edge. Seeking to
// exactly `duration` is past the last frame's interval -- the element
// reports itself ended and presents whatever it likes, and a panel reading
// the presented frame then answers with something far from the end (his
// report 2026-08-31: frame 770 of 771, right arrow, and the timeline
// jumped to 591). His rule: "simply move to the last frame in the video
// and not allow the user to move forward (if at the end) or backward (if
// at the beginning)" -- so both ends clamp to the middle of the last and
// first frames, and a step that would leave the clip stays put.
export function stepGameFrame(video, dir, gameFps = 30,
                              frameMap = null, clock = null) {
  if (!video) return;
  if (!video.paused) video.pause();
  const presented = presentedVideoTime(video);
  if (presented === null) return; // the decoder has not displayed a picture yet
  const boundedClock = { ...clock, duration: Number.isFinite(video.duration)
    ? video.duration : clock && clock.duration };
  const mapped = nextMappedTime(presented ?? (video.currentTime || 0), frameMap, boundedClock, dir);
  if (mapped !== null) {
    video.currentTime = mapped;
    return;
  }
  // A known picture sequence at its boundary stays put. A time-based
  // fallback here could jump to a different picture or into a capture gap.
  if ((frameMap && frameMap.length) || (clock && clock.times && clock.times.length)) return;
  // Legacy video without a picture clock retains the 30 Hz controls.
  const n = gameFrameOf(video, gameFps);
  video.currentTime = clampToFrames(
    (n + dir + 0.5) / gameFps, video.duration || 0, gameFps);
}

// Walk encoded slots in capture order. Raw game counters can go backward
// or be visited again after a save-state load. New clips carry picture IDs
// scoped to this clip, so only adjacent copies of that picture are skipped.
// Older maps can identify adjacent equal counters; unknown slots each remain
// a selectable picture. Null means no sequence or no neighbor in that direction.
export function nextMappedTime(seconds, frameMap, clock, dir) {
  const times = clock && clock.times;
  const count = times && times.length || frameMap && frameMap.length || 0;
  if (!count || !dir) return null;
  const ids = clock && clock.identities || frameMap || [];
  const at = Math.max(0, Math.min(count - 1, slotAtTime(seconds, clock)));
  const samePicture = (a, b) => ids[a] != null && ids[a] === ids[b];
  const step = dir > 0 ? 1 : -1;
  let slot = at + step;
  while (slot >= 0 && slot < count && samePicture(at, slot)) slot += step;
  if (slot < 0 || slot >= count) return null;
  // Backward stepping lands on the start of this neighboring hold, not an
  // earlier visit with the same raw counter somewhere else in the clip.
  if (step < 0) {
    while (slot > 0 && samePicture(slot, slot - 1)) slot -= 1;
  }
  return timeOfSlot(slot, clock);
}

export function clampToFrames(seconds, duration, gameFps = 30) {
  const half = 0.5 / gameFps;
  const last = Math.max(half, (duration || 0) - half);
  return Math.min(Math.max(seconds, half), last);
}

export function jumpToStart(video, startSeconds = 0) {
  if (!video) return;
  if (!video.paused) video.pause();
  video.currentTime = Math.max(0, startSeconds);
}
