// src/sm64_events/ui/frame.js — shared game-frame video controls.
// SM64 logic is 30 fps; steps move in GAME frames regardless of encode rate.
// Seek to the MIDDLE of the target frame so float rounding never straddles a
// boundary (the fix from replay.js: stepping 1/encode-fps only changed the
// image every 2nd press). Used by the replay player and the compare sync layer.

export function gameFrameOf(video, gameFps = 30) {
  return Math.floor((video.currentTime || 0) * gameFps + 1e-4);
}

export function stepGameFrame(video, dir, gameFps = 30) {
  if (!video) return;
  if (!video.paused) video.pause();
  const n = gameFrameOf(video, gameFps);
  const t = (n + dir + 0.5) / gameFps;
  video.currentTime = Math.min(Math.max(t, 0), video.duration || 0);
}

export function jumpToStart(video, startSeconds = 0) {
  if (!video) return;
  if (!video.paused) video.pause();
  video.currentTime = Math.max(0, startSeconds);
}
