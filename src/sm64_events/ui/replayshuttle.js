// HTML video cannot reliably play backwards. Reverse shuttle issues bounded
// seeks; the existing presented-picture observer remains the timeline clock.
import { playReview } from "./reviewcommands.js";
import { pauseReviewSource, seekReviewSource } from "./reviewsource.js";
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
    const nextSpeed = direction === next ? Math.min(8, speed * 2) : 1;
    if (!direction) originalRate = video.playbackRate;
    if (timer !== null) clearInterval(timer);
    timer = null; direction = next; speed = nextSpeed;
    announce();
    if (next > 0) {
      video.playbackRate = speed;
      playReview(video)?.catch(stop);
    } else {
      pauseReviewSource(video); last = performance.now();
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
  const paused = () => { if (direction > 0 && video.paused) stop(); };
  video?.addEventListener("play", played);
  video?.addEventListener("pause", paused);
  video?.addEventListener("reviewshuttlestop", stop);
  return { run, stop, pause: () => { stop(); pauseReviewSource(video); },
    dispose: () => {
      stop(); video?.removeEventListener("play", played); video?.removeEventListener("pause", paused);
      video?.removeEventListener("reviewshuttlestop", stop);
    } };
}
