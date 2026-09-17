// Review commands on the native <video>. An exact seek replaces any queued
// scrub, a drag keeps only its latest destination, and an explicit pause is
// announced even when the element is already paused at its end.
import { cancelReviewSeek, flushReviewSeek, queueReviewSeek } from "./reviewseek.js";

export function seekReviewSource(video, time) {
  cancelReviewSeek(video);
  video.currentTime = time;
}

export function scrubReviewSource(video, time) {
  queueReviewSeek(video, time, target => { video.currentTime = target; });
}

export function pauseReviewSource(video) {
  // At EOS the element is already paused, so pause() emits no new event.
  // Explicit K/step/click intent must still cancel a queued loop restart.
  video?.dispatchEvent(new Event("reviewpause"));
  video?.pause();
}

export function prepareReviewPlayback(video) {
  flushReviewSeek(video);
}
