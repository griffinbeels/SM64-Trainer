// Experimental opt-in, same-clock fragmented media. Out is enforced by the decoder's
// admitted samples, before native presentation; timers cannot veto a picture.
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
  const source = sources.get(video);
  if (source) source.seek(time);
  else video.currentTime = time;
}

export function pauseReviewSource(video) {
  sources.get(video)?.pause();
  video?.pause();
}

export function prepareReviewPlayback(video) {
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

async function appendPictures(mediaSource, source, data, end) {
  const buffer = mediaSource.addSourceBuffer(source.mime_type);
  buffer.timestampOffset = source.timestamp_offset_s;
  // Keep decoder preroll from zero: appendWindowStart could discard the GOP
  // needed by In. Out is the next picture's exact encoded tick, exclusive.
  buffer.appendWindowEnd = Math.round(end * source.video_timescale) / source.video_timescale;
  await new Promise((resolve, reject) => {
    buffer.addEventListener("updateend", resolve, { once: true });
    buffer.addEventListener("error", () => reject(new Error("Fragment decode failed.")), { once: true });
    buffer.appendBuffer(data);
  });
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
  const request = new AbortController();
  const bytes = fetch(source.url, { signal: request.signal }).then(response => {
    if (!response.ok) throw new Error(`Media request failed (${response.status}).`);
    return response.arrayBuffer();
  });
  // The request can finish before SourceOpen. Keep its rejection handled until
  // that generation consumes it, including after an unmount.
  bytes.catch(() => {});
  const played = () => { resume = true; };
  const paused = () => { if (!pending) resume = false; };
  video.addEventListener("play", played);
  video.addEventListener("pause", paused);
  const release = () => {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = null;
  };
  const rebuild = (destination) => {
    const ticket = ++generation;
    position = destination; pending = true;
    release();
    const currentSource = new MediaSource();
    objectUrl = URL.createObjectURL(currentSource);
    const failed = error => {
      if (disposed || ticket !== generation) return;
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
      try {
        const data = await bytes;
        if (disposed || ticket !== generation) return;
        await appendPictures(currentSource, source, data, end);
        if (disposed || ticket !== generation) return;
        // A finalized tail is seekable immediately; buffered coverage alone
        // does not make Chromium settle a seek near an unfinished MSE tail.
        finishSource(currentSource, end);
        pending = false;
        video.currentTime = Math.max(source.visible_start_s, position);
        if (resume) video.play().catch(() => {});
      } catch (error) { failed(error); }
    }, { once: true });
    video.src = objectUrl;
  };
  const rangeEnd = () => loop?.enabled ? Math.min(source.visible_end_s, loop.end) : source.visible_end_s;
  const controller = {
    duration: source.visible_end_s,
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
    disposed = true; generation++; request.abort();
    sources.delete(video); release();
    video.removeEventListener("play", played);
    video.removeEventListener("pause", paused);
  };
}
