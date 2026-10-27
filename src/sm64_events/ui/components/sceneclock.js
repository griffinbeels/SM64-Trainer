// src/sm64_events/ui/components/sceneclock.js -- the recording's own clock (M4, lab only).
//
// Play keeps the recording's time: each picture stays up for as long as the
// recording showed it, so a level load held for 817 ms shows its picture for
// 817 ms while its sound plays through. The server sends when the recording
// showed each picture (frames[k].recorded_ms, ms after its first); a package
// without those times plays one picture per game frame (scene.fps). Stepping
// and seeking still move one picture at a time. SceneLink's Play and the
// sound (sceneaudio.js) both read time from here.

// When the recording showed each picture, ms after the first.
export function recordedTimes(scene) {
  const frames = scene?.frames || [], fps = scene?.fps || 30;
  const recorded = frames.length > 0 && frames.every((frame) => Number.isFinite(frame.recorded_ms));
  return Float64Array.from(frames, (frame, k) => (recorded ? frame.recorded_ms : k * 1000 / fps));
}

// The picture on screen `elapsed` ms after a play from picture `from` began:
// the last one whose recorded time has come.
export function pictureAt(times, from, elapsed) {
  const target = times[from] + elapsed;
  let lo = from, hi = times.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (times[mid] <= target) lo = mid; else hi = mid - 1;
  }
  return lo;
}

// How long a play from `from` lasts: to the last picture, then one game frame of it.
export function playLength(times, from, fps = 30) {
  return times[times.length - 1] - times[from] + 1000 / fps;
}

// How far into picture `frame` a play's sound had got when it stopped at `now`: {session, frame,
// ms}, or null when it had not reached that picture. Play on the same picture goes on from there
// (SceneLink.play's resumeMs), so a held picture never plays its sound twice. A play that had not
// started sounding yet keeps the point it resumed from.
export function heardInto(play, frame, now, session) {
  const heard = play.times[play.from] + Math.max(play.resumeMs || 0, now - play.start);
  const end = frame + 1 < play.times.length ? play.times[frame + 1] : play.times[play.from] + play.length;
  const into = Math.min(heard, end) - play.times[frame];
  return into > 0 ? { session, frame, ms: into } : null;
}
