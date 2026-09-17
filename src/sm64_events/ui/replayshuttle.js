// HTML video cannot reliably play backwards. Reverse shuttle issues bounded
// seeks; the existing presented-picture observer remains the timeline clock.
import { playReview } from "./reviewcommands.js";
import { pauseReviewSource, seekReviewSource } from "./reviewsource.js";
export const REPLAY_SPEEDS = [.25, .5, .75, 1, 1.5, 2, 3, 4];
const SHUTTLE_SPEEDS = REPLAY_SPEEDS.filter(rate => rate >= 1);

export function setReplaySpeed(video, rate) {
  stopShuttle(video);
  if (video) video.playbackRate = rate;
}

export function stopShuttle(video) {
  video?.dispatchEvent(new Event("reviewshuttlestop"));
}

export function replayShuttle(video) {
  let direction = 0, speed = 0, timer = null, last = 0, originalRate = 1;
  function announce() {
    if (!video) return;
    video.dataset.reviewShuttle = direction ? `${direction < 0 ? "Reverse" : "Forward"} ${speed}×` : "";
    video.dispatchEvent(new Event("reviewshuttlechange"));
  }
  function stop() {
    if (timer !== null) clearInterval(timer);
    timer = null;
    if (direction && video) video.playbackRate = originalRate;
    direction = 0; speed = 0;
    announce();
  }
  function run(next) {
    if (!video || !Number.isFinite(video.duration)) return;
    const nextSpeed = direction === next
      ? SHUTTLE_SPEEDS[Math.min(SHUTTLE_SPEEDS.length - 1, SHUTTLE_SPEEDS.indexOf(speed) + 1)] : 1;
    if (!direction) originalRate = video.playbackRate;
    // Finish the previous mode before publishing the new reverse intent.
    if (next < 0) pauseReviewSource(video);
    if (timer !== null) clearInterval(timer);
    timer = null; direction = next; speed = nextSpeed;
    video.playbackRate = speed;
    announce();
    if (next > 0) {
      playReview(video)?.catch(stop);
    } else {
      last = performance.now();
      timer = setInterval(() => {
        const now = performance.now();
        const elapsed = Math.min(.25, (now - last) / 1000);
        if (video.seeking) return;
        last = now;
        seekReviewSource(video, Math.max(0, video.currentTime - elapsed * speed));
        if (video.currentTime <= 0) stop();
      }, 66);
    }
  }
  const played = () => { if (direction < 0 && !video.paused) stop(); };
  const paused = () => {
    // Reaching the end (where a loop restarts) is not a request to leave
    // shuttle. Explicit pause still stops immediately, even when already at EOS.
    if (direction > 0 && video.paused && !video.ended) stop();
  };
  video?.addEventListener("play", played);
  video?.addEventListener("pause", paused);
  video?.addEventListener("reviewpause", stop);
  video?.addEventListener("reviewshuttlestop", stop);
  return { run, stop, pause: () => {
    stop();
    if (video) video.playbackRate = 1;
    pauseReviewSource(video);
  },
    dispose: () => {
      stop(); video?.removeEventListener("play", played); video?.removeEventListener("pause", paused);
      video?.removeEventListener("reviewpause", stop);
      video?.removeEventListener("reviewshuttlestop", stop);
    } };
}
