// src/sm64_events/ui/frame.js — shared game-frame video controls.
// SM64 logic is 30 fps; steps move in GAME frames regardless of encode rate.
// Seek to the MIDDLE of the target frame so float rounding never straddles a
// boundary (the fix from replay.js: stepping 1/encode-fps only changed the
// image every 2nd press). Used by the replay player and the compare sync layer.

export function gameFrameOf(video, gameFps = 30) {
  return Math.floor((video.currentTime || 0) * gameFps + 1e-4);
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
                              frameMap = null, clipFps = 60, clipStart = 0) {
  if (!video) return;
  if (!video.paused) video.pause();
  const mapped = nextMappedTime(video.currentTime || 0, frameMap, clipFps,
                                dir, clipStart);
  if (mapped !== null) {
    video.currentTime = clampToFrames(mapped, video.duration || 0, gameFps);
    return;
  }
  // No map (an older clip), or already on the clip's first or last game
  // frame: the time arithmetic stands, and the clamp keeps it in the clip.
  const n = gameFrameOf(video, gameFps);
  video.currentTime = clampToFrames(
    (n + dir + 0.5) / gameFps, video.duration || 0, gameFps);
}

// STEP THROUGH THE MAP, not through time. The three clocks in this
// pipeline do NOT run at one rate, measured on his pyramid clip
// (2026-08-31): the video encodes at 59.987 fps while the GAME advanced at
// 29.800 -- 2.013 video slots per game frame, not 2.000 -- and the game's
// rate is not even a constant, it sags when the machine is loaded. So a
// step of 1/30 s is a step of slightly LESS than one game frame, which
// lands on the same picture now and then (his "press forward 1, it does
// nothing") and drifts over a long clip. The map already says which game
// frame each video frame shows, so stepping through IT is exact whatever
// the emulator was doing: find this slot's frame, take the first slot of
// the next distinct one. Returns null when the map cannot answer and the
// caller falls back to time.
//
// `clipStart` is the clip's OWN first video timestamp (2026-09-01, his
// Log Rolling report). A cut leaves its sub-frame remainder on the first
// picture -- clip 5782's frames sit at k/60 + 0.011 s -- so a seek to the
// middle of slot k, (k + 0.5)/60, lands 2.7 ms BEFORE frame k begins and
// Chromium presents k-1. Measured on the real clip: every step landed one
// picture early; with the offset added, every step landed on its slot, and
// on a clip whose first frame sits at 0.000 (5574) the two agree. The
// slot arithmetic therefore counts from `clipStart`, in both directions.
export function slotAtTime(seconds, clipFps, clipStart = 0) {
  return Math.floor((seconds - clipStart) * clipFps + 1e-4);
}
export function timeOfSlot(slot, clipFps, clipStart = 0) {
  return clipStart + (slot + 0.5) / clipFps;
}
export function nextMappedTime(seconds, frameMap, clipFps, dir, clipStart = 0) {
  if (!frameMap || !frameMap.length) return null;
  const at = Math.max(0, Math.min(frameMap.length - 1,
    slotAtTime(seconds, clipFps, clipStart)));
  const here = frameMap[at];
  if (here == null) return null;
  if (dir > 0) {
    for (let slot = at + 1; slot < frameMap.length; slot += 1) {
      const value = frameMap[slot];
      if (value != null && value > here) return timeOfSlot(slot, clipFps, clipStart);
    }
    return null;                       // already on the last game frame
  }
  let target = null;
  for (let slot = at - 1; slot >= 0; slot -= 1) {
    const value = frameMap[slot];
    if (value == null) continue;
    if (value < here) { target = value; break; }
  }
  if (target === null) return null;    // already on the first game frame
  for (let slot = 0; slot < frameMap.length; slot += 1) {
    if (frameMap[slot] === target) return timeOfSlot(slot, clipFps, clipStart);
  }
  return null;
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
