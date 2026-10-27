// Media review uses displayed picture intervals; it never supplies game timing.
import { useEffect, useRef, useState } from "preact/hooks";
import { slotAtTime } from "./frame.js";
import { presentedVideoTime, watchVideoPicture } from "./videopicture.js";

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

// Where the seek bar ends: the middle of the last picture, where a step or a
// pause on the final frame lands. Ending it at the duration left the thumb
// half a picture short of the bar's end on the final frame, which reads as a
// bar that cannot reach its end; a drag to the end now lands on that picture
// rather than past it. Without picture timing the duration stays the end.
export function lastPictureTime(clock, step, duration) {
  if (!Number.isFinite(duration) || duration <= 0) return null;
  const times = clock?.times;
  const lastStart = times?.length ? times[times.length - 1]
    : Number.isFinite(step) && step > 0 ? Math.max(0, Math.ceil(duration / step) - 1) * step : null;
  const interval = lastStart != null && lastStart < duration
    ? pictureInterval(lastStart, clock, step, duration) : null;
  return interval ? (interval.start + interval.end) / 2 : duration;
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
    let timer = null;
    let requestedPlay = !video.paused;
    const played = () => { if (!video.paused) requestedPlay = true; };
    const paused = () => { if (!video.ended) requestedPlay = false; };
    const pauseRequested = () => { requestedPlay = false; clearTimeout(timer); };
    const update = () => setMedia({ time: video.currentTime || 0,
      picture: presentedVideoTime(video),
      duration: Number.isFinite(video.duration) ? video.duration : 0,
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
      if (!range?.enabled || !requestedPlay) return;
      video.currentTime = loopSeekTime(range, latest.current.clock, latest.current.step, video.duration);
      video.play().catch(() => {});
    };
    const events = ["loadedmetadata", "durationchange", "timeupdate", "play", "pause",
      "ratechange", "volumechange", "seeked", "playing", "reviewshuttlechange"];
    events.forEach(name => video.addEventListener(name, changed));
    video.addEventListener("play", played);
    video.addEventListener("pause", paused);
    video.addEventListener("reviewpause", pauseRequested);
    video.addEventListener("seeking", seeking);
    video.addEventListener("waiting", seeking);
    video.addEventListener("ended", ended);
    const stop = watchVideoPicture(video, changed);
    changed();
    return () => {
      clearTimeout(timer); stop();
      events.forEach(name => video.removeEventListener(name, changed));
      video.removeEventListener("play", played);
      video.removeEventListener("pause", paused);
      video.removeEventListener("reviewpause", pauseRequested);
      video.removeEventListener("seeking", seeking);
      video.removeEventListener("waiting", seeking);
      video.removeEventListener("ended", ended);
    };
  }, [video, loop?.start, loop?.end, loop?.enabled]);
  return media;
}
