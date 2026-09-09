// Media review uses displayed picture intervals; it never supplies game timing.
import { useEffect, useRef, useState } from "preact/hooks";
import { slotAtTime } from "./frame.js";
import { presentedVideoTime, watchVideoPicture } from "./videopicture.js";
import { hasBoundedReview, reviewDuration, setReviewSourceLoop } from "./reviewsource.js";

export function pictureInterval(time, clock, step, duration) {
  if (!Number.isFinite(time) || !Number.isFinite(duration) || duration <= 0) return null;
  if (clock?.times?.length) {
    const slot = slotAtTime(time, clock);
    if (slot < 0) return null;
    return { start: clock.times[slot], end: Math.min(duration,
      clock.times[slot + 1] ?? duration) };
  }
  if (Number.isFinite(step) && step > 0) {
    const start = Math.floor(time / step + 1e-8) * step;
    return { start, end: Math.min(duration, start + step) };
  }
  return null;
}

export function loopSeekTime(loop) {
  // Playback begins at A itself. A midpoint is useful for paused stepping,
  // but would discard half a long hold (and its audio) on every loop.
  return loop.start;
}

export function useReviewMedia(video, { clock, step, loop } = {}) {
  const [media, setMedia] = useState({ time: 0, picture: null, duration: 0,
    rate: 1, volume: .3, muted: false, playing: false });
  const latest = useRef({ clock, step, loop });
  latest.current = { clock, step, loop };
  useEffect(() => {
    if (!video) return undefined;
    setReviewSourceLoop(video, loop);
    let timer = null;
    const update = () => setMedia({ time: video.currentTime || 0,
      picture: presentedVideoTime(video),
      duration: Number.isFinite(reviewDuration(video)) ? reviewDuration(video) : 0,
      rate: video.playbackRate, volume: video.volume, muted: video.muted,
      playing: !video.paused, shuttle: video.dataset.reviewShuttle || "" });
    const arm = () => {
      clearTimeout(timer);
      const { loop: range, clock: sourceClock, step: sourceStep } = latest.current;
      if (!range?.enabled || video.paused || video.seeking || !(range.end > range.start)) return;
      if (video.currentTime < range.start || video.currentTime >= range.end) {
        video.currentTime = loopSeekTime(range, sourceClock, sourceStep, video.duration);
        return;
      }
      if (hasBoundedReview(video)) return; // Its native duration is exactly Out.
      timer = setTimeout(() => {
        if (video.paused || !latest.current.loop?.enabled) return;
        // Media time can stop while the element still reports !paused.
        // A wall-clock timeout alone must never truncate a buffering loop.
        if (video.currentTime < latest.current.loop.end) { arm(); return; }
        video.currentTime = loopSeekTime(latest.current.loop, latest.current.clock,
          latest.current.step, video.duration);
        video.play().catch(() => {});
      }, Math.max(4, (range.end - video.currentTime) * 1000 / video.playbackRate));
    };
    const changed = () => { update(); arm(); };
    const seeking = () => { clearTimeout(timer); update(); };
    const ended = () => {
      const range = latest.current.loop;
      if (!range?.enabled) return;
      video.currentTime = loopSeekTime(range, latest.current.clock, latest.current.step, video.duration);
      video.play().catch(() => {});
    };
    const events = ["loadedmetadata", "durationchange", "timeupdate", "play", "pause",
      "ratechange", "volumechange", "seeked", "playing", "reviewshuttlechange"];
    events.forEach(name => video.addEventListener(name, changed));
    video.addEventListener("seeking", seeking);
    video.addEventListener("waiting", seeking);
    video.addEventListener("ended", ended);
    const stop = watchVideoPicture(video, changed);
    changed();
    return () => {
      clearTimeout(timer); stop();
      events.forEach(name => video.removeEventListener(name, changed));
      video.removeEventListener("seeking", seeking);
      video.removeEventListener("waiting", seeking);
      video.removeEventListener("ended", ended);
    };
  }, [video, loop?.start, loop?.end, loop?.enabled]);
  return media;
}
