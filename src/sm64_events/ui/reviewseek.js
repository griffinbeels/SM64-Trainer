// A drag supplies destinations faster than a decoder/network can seek. Keep one
// latest destination, not a queue of pictures the user has already passed over.
const scrubs = new WeakMap();
const SEEK_INTERVAL_MS = 33;
const SEEK_STALL_MS = 250;

export function cancelReviewSeek(video) {
  scrubs.get(video)?.dispose();
  scrubs.delete(video);
}

export function flushReviewSeek(video) {
  scrubs.get(video)?.flush(true);
}

export function queueReviewSeek(video, time, apply) {
  if (!video || !Number.isFinite(time)) return;
  let scrub = scrubs.get(video);
  if (!scrub) {
    scrub = createScrub(video, apply);
    scrubs.set(video, scrub);
  }
  scrub.seek(time);
}

function createScrub(video, apply) {
  let latest = null, timer = null, last = -Infinity;
  const schedule = () => {
    if (timer !== null || latest === null) return;
    const interval = video.seeking ? SEEK_STALL_MS : SEEK_INTERVAL_MS;
    timer = setTimeout(() => flush(), Math.max(0, last + interval - performance.now()));
  };
  function flush(force = false) {
    clearTimeout(timer); timer = null;
    if (latest === null) return;
    const interval = video.seeking ? SEEK_STALL_MS : SEEK_INTERVAL_MS;
    if (!force && performance.now() < last + interval) { schedule(); return; }
    const target = latest;
    latest = null; last = performance.now();
    apply(target);
  }
  const settled = () => { clearTimeout(timer); timer = null; schedule(); };
  video.addEventListener("seeked", settled);
  return {
    seek(time) { latest = time; schedule(); },
    flush,
    dispose() {
      clearTimeout(timer); latest = null;
      video.removeEventListener("seeked", settled);
    },
  };
}
