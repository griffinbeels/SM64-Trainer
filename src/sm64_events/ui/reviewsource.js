// Experimental opt-in, same-clock fragmented media. Out is enforced by the decoder's
// admitted samples, before native presentation; timers cannot veto a picture.
import { appendReviewStream } from "./reviewstream.js";
import { cancelReviewSeek, flushReviewSeek, queueReviewSeek } from "./reviewseek.js";
const sources = new WeakMap();

export function hasBoundedReview(video) {
  return sources.has(video);
}

export function setReviewSourceLoop(video, loop) {
  sources.get(video)?.setLoop(loop);
}

export function reviewDuration(video) {
  return sources.get(video)?.duration ?? video?.duration;
}

export function reviewPictureTime(video, time) {
  const source = sources.get(video);
  return source ? source.pictureTime(time) : time;
}

export function seekReviewSource(video, time) {
  cancelReviewSeek(video);
  applySeek(video, time);
}

function applySeek(video, time) {
  const source = sources.get(video);
  if (source) source.seek(time);
  else video.currentTime = time;
}

export function scrubReviewSource(video, time) {
  queueReviewSeek(video, time, target => applySeek(video, target));
}

export function pauseReviewSource(video) {
  sources.get(video)?.pause();
  // At EOS the element is already paused, so pause() emits no new event.
  // Explicit K/step/click intent must still cancel a queued loop restart.
  video?.dispatchEvent(new Event("reviewpause"));
  video?.pause();
}

export function reviewSourceContinuing(video) {
  return sources.get(video)?.continuing ?? false;
}

export function prepareReviewPlayback(video) {
  flushReviewSeek(video);
  sources.get(video)?.play();
}

function finishSource(source, end) {
  source.duration = end;
  source.endOfStream();
}

function supportsReviewSource(source, times) {
  // Negative decoder preroll needs a logical/media clock adapter first. Never
  // silently add an offset: the inspector and saved marks use this same clock.
  return source && typeof source.url === "string" && source.timestamp_offset_s === 0
      && source.video_timescale === 90000 && Array.isArray(times) && times.length > 0
      && Number.isFinite(source.visible_start_s) && Number.isFinite(source.visible_end_s)
      && source.visible_start_s >= 0 && source.visible_end_s > source.visible_start_s
      && globalThis.MediaSource?.isTypeSupported(source.mime_type);
}

function pictureClock(times, scale) {
  const clock = new Map();
  let previous = -1;
  for (const time of times) {
    const tick = Math.round(time * scale);
    if (!Number.isFinite(time) || tick <= previous) return null;
    clock.set(tick, time); previous = tick;
  }
  return clock;
}

function containsTime(buffer, time) {
  const ranges = buffer.buffered;
  for (let i = 0; i < ranges.length; i++)
    if (ranges.start(i) <= time && ranges.end(i) > time) return true;
  return false;
}

export function attachReviewSource(video, source, fallback, onError, times) {
  if (!supportsReviewSource(source, times)) return () => {};
  // MSE truncates presentation seconds to microseconds, where native MP4 rounds.
  // Recover the encoded 90 kHz identity before readers use the original clock.
  // A timestamp absent from the declared picture sequence supplies no identity.
  const pictureTimes = pictureClock(times, source.video_timescale);
  if (!pictureTimes) return () => {};
  let loop = null, end = source.visible_end_s, generation = 0, disposed = false;
  let objectUrl = null, resume = !video.paused, pending = false, position = video.currentTime || 0;
  let request = null;
  const played = () => { if (!video.paused) resume = true; };
  const paused = () => { if (!pending && !video.ended) resume = false; };
  video.addEventListener("play", played);
  video.addEventListener("pause", paused);
  const release = () => {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = null;
  };
  const rebuild = (destination) => {
    const ticket = ++generation;
    request?.abort();
    const currentRequest = new AbortController();
    request = currentRequest;
    position = destination; pending = true;
    release();
    const currentSource = new MediaSource();
    objectUrl = URL.createObjectURL(currentSource);
    const failed = error => {
      if (disposed || ticket !== generation) return;
      if (!pending) position = video.currentTime;
      currentRequest.abort();
      sources.delete(video); release();
      pending = false;
      video.src = fallback;
      video.addEventListener("loadedmetadata", () => {
        if (!disposed && ticket === generation) {
          video.currentTime = position;
          if (resume) video.play().catch(() => {});
        }
      }, { once: true });
      onError?.(`Exact loop media is unavailable. Playing the original recording. ${error.message}`);
    };
    currentSource.addEventListener("sourceopen", async () => {
      if (disposed || ticket !== generation || currentRequest.signal.aborted) return;
      try {
        const positionWhenReady = (buffer) => {
          if (disposed || ticket !== generation || !pending) return;
          const target = Math.max(source.visible_start_s, position);
          if (containsTime(buffer, target)) {
            pending = false;
            video.currentTime = target;
            if (resume) video.play().catch(() => {});
          }
        };
        await appendReviewStream(currentSource, source, end, currentRequest.signal, positionWhenReady);
        if (disposed || ticket !== generation) return;
        // A finalized tail is seekable immediately; buffered coverage alone
        // does not make Chromium settle a seek near an unfinished MSE tail.
        finishSource(currentSource, end);
        if (pending) {
          pending = false;
          video.currentTime = Math.max(source.visible_start_s, position);
          if (resume) video.play().catch(() => {});
        }
      } catch (error) { failed(error); }
    }, { once: true });
    video.src = objectUrl;
  };
  const rangeEnd = () => loop?.enabled ? Math.min(source.visible_end_s, loop.end) : source.visible_end_s;
  const controller = {
    duration: source.visible_end_s,
    get continuing() { return resume && (pending || video.ended); },
    pictureTime: time => pictureTimes.get(Math.round(time * source.video_timescale)) ?? null,
    pause: () => { resume = false; },
    setLoop(next) {
      loop = next;
      if (rangeEnd() === end) return;
      if (!pending) { position = video.currentTime; resume = !video.paused; }
      end = rangeEnd();
      if (loop?.enabled && (position < loop.start || position >= end)) position = loop.start;
      rebuild(position);
    },
    seek(time) {
      position = time;
      // Paused review retains every original picture, even beyond Out. The
      // next Play restores the bounded source before allowing it to advance.
      if (time >= end && end < source.visible_end_s) {
        resume = false; end = source.visible_end_s; rebuild(time);
      } else if (!pending) video.currentTime = time;
    },
    play() {
      if (!pending) position = video.currentTime;
      resume = true;
      if (loop?.enabled && (position < loop.start || position >= loop.end)) position = loop.start;
      if (end !== rangeEnd()) { end = rangeEnd(); rebuild(position); }
      else if (!pending && video.currentTime !== position) video.currentTime = position;
    },
  };
  sources.set(video, controller);
  rebuild(video.currentTime || source.visible_start_s);
  return () => {
    disposed = true; generation++; request?.abort();
    cancelReviewSeek(video); sources.delete(video); release();
    video.removeEventListener("play", played);
    video.removeEventListener("pause", paused);
  };
}
