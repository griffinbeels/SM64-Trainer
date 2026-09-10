// Explicitly imported by the profiling tool/DevTools, never by the app shell.
// rAF measures callback scheduling, not physical display FPS or game frames.
const LIMIT = 128;
const BUCKETS = [1, 2, 4, 8, 16.667, 25, 33.334, 50, 100, 250, 500, 1000, 5000, 300000];
let current = null;

function histogram() {
  const counts = Array(BUCKETS.length + 1).fill(0);
  let count = 0, total = 0, max = 0;
  return {
    add(ms) {
      if (!Number.isFinite(ms) || ms < 0) return;
      const at = BUCKETS.findIndex(limit => ms <= limit);
      counts[at < 0 ? BUCKETS.length : at]++;
      count++; total += ms; max = Math.max(max, ms);
    },
    snapshot() {
      const percentile = q => {
        if (!count) return null;
        let n = 0;
        const i = counts.findIndex(size => (n += size) >= Math.ceil(q * count));
        return BUCKETS[i] ?? max;
      };
      return { count, total_ms: total, max_ms: count ? max : null,
        p50_ms: percentile(.5), p95_ms: percentile(.95), p99_ms: percentile(.99),
        buckets_ms: BUCKETS, bucket_counts: [...counts], quantile_method: "histogram_upper_bound" };
    },
  };
}

function quality(video) {
  if (typeof video.getVideoPlaybackQuality !== "function") return null;
  const q = video.getVideoPlaybackQuality();
  return { total: q.totalVideoFrames, dropped: q.droppedVideoFrames };
}

function finishVideo(video, info) {
  const final = quality(video), initial = info.initial;
  const decreased = initial && final && (final.total < initial.total || final.dropped < initial.dropped);
  const reset = Boolean(info.sourceChanged || video.currentSrc !== info.initialSrc || decreased);
  const delta = !reset && initial && final
    ? { total: final.total - initial.total, dropped: final.dropped - initial.dropped } : null;
  for (const [name, listener] of info.listeners) video.removeEventListener(name, listener);
  if (info.pictureCallback !== null) video.cancelVideoFrameCallback(info.pictureCallback);
  return { id: info.id, counters_reset: reset, initial, final,
    seen_at_ms: info.seenAt, initial_ready_state: info.initialReadyState,
    total_frames_delta: delta?.total ?? null, dropped_frames_delta: delta?.dropped ?? null,
    ready_state: video.readyState, paused: video.paused, playback_rate: video.playbackRate };
}

export function start({ duration_s = 60 } = {}) {
  if (current?.running) throw new Error("Browser profiling is already active");
  if (!Number.isFinite(duration_s) || duration_s < 1 || duration_s > 300)
    throw new Error("duration_s must be between 1 and 300");
  const started = performance.now(), raf = histogram(), tasks = histogram();
  const observers = [], videos = new Map(), events = [], markers = [], resources = [];
  const supported = globalThis.PerformanceObserver?.supportedEntryTypes || [];
  let frameId, timer, lastFrame = null, result = null;
  let discarded = 0, hiddenAt = document.hidden ? started : null, hiddenMs = 0;
  const append = (list, item) => { if (list.length < LIMIT) list.push(item); else discarded++; };
  const relative = () => performance.now() - started;
  const mark = label => {
    append(markers, { label: String(label).slice(0, 120), at_ms: relative() });
  };
  const visibility = () => {
    lastFrame = null;
    if (document.hidden) hiddenAt = performance.now();
    else if (hiddenAt !== null) { hiddenMs += performance.now() - hiddenAt; hiddenAt = null; }
  };
  const tick = now => {
    if (!document.hidden && lastFrame !== null) raf.add(now - lastFrame);
    lastFrame = document.hidden ? null : now;
    frameId = requestAnimationFrame(tick);
  };
  const observe = (type, callback) => {
    if (!supported.includes(type)) return;
    const observer = new PerformanceObserver(list => callback(list.getEntries()));
    observer.observe({ type, buffered: false });
    observers.push([observer, callback]);
  };
  observe("longtask", entries => entries.forEach(entry => tasks.add(entry.duration)));
  observe("resource", entries => entries.forEach(entry => {
    const path = new URL(entry.name, location.href).pathname;
    if (path.startsWith("/api/")) append(resources, { path, start_ms: entry.startTime - started,
      duration_ms: entry.duration, transfer_bytes: entry.transferSize });
  }));
  const attach = video => {
      if (videos.has(video)) return;
      if (videos.size >= 16) { discarded++; return; }
      const info = { id: videos.size + 1, initial: quality(video), initialSrc: video.currentSrc,
        initialReadyState: video.readyState, seenAt: relative(),
        sourceChanged: false, listeners: [], pictureCallback: null };
      for (const name of ["loadeddata", "emptied", "waiting", "playing", "seeking", "seeked", "error"]) {
        const listener = () => {
          if (name === "emptied" && info.initialSrc) info.sourceChanged = true;
          if (name === "loadeddata" && !info.initialSrc) info.initialSrc = video.currentSrc;
          append(events, { video: info.id, event: name, at_ms: relative(), media_s: video.currentTime });
        };
        video.addEventListener(name, listener);
        info.listeners.push([name, listener]);
      }
      if (typeof video.requestVideoFrameCallback === "function") {
        info.pictureCallback = video.requestVideoFrameCallback((_now, metadata) => {
          append(events, { video: info.id, event: "first-observed-picture", at_ms: relative(), media_s: metadata.mediaTime });
        });
      }
      videos.set(video, info);
  };
  const discover = node => {
    if (node.nodeType !== 1) return;
    if (node.tagName === "VIDEO") attach(node);
    for (const video of node.querySelectorAll("video")) attach(video);
  };
  const mutations = new MutationObserver(records => {
    for (const record of records) for (const node of record.addedNodes) discover(node);
  });
  const stop = () => {
    if (result) return result;
    current.running = false;
    cancelAnimationFrame(frameId); clearTimeout(timer); mutations.disconnect();
    document.removeEventListener("visibilitychange", visibility);
    for (const [observer, callback] of observers) { callback(observer.takeRecords()); observer.disconnect(); }
    const ended = performance.now();
    const media = [];
    for (const [video, info] of videos) {
      media.push(finishVideo(video, info));
    }
    result = { version: 1, started_utc: new Date(performance.timeOrigin + started).toISOString(),
      duration_ms: ended - started, hidden_ms: hiddenMs + (hiddenAt === null ? 0 : ended - hiddenAt),
      raf_intervals: raf.snapshot(), long_tasks: supported.includes("longtask") ? tasks.snapshot() : null,
      long_tasks_supported: supported.includes("longtask"),
      resource_timing_supported: supported.includes("resource"), videos: media,
      video_events: events, markers, resources, discarded_records: discarded,
      user_agent: navigator.userAgent, viewport: { width: innerWidth, height: innerHeight, dpr: devicePixelRatio },
      memory: performance.memory ? { used_js_heap_bytes: performance.memory.usedJSHeapSize } : null };
    videos.clear();
    return result;
  };
  current = { running: true, mark, stop };
  document.addEventListener("visibilitychange", visibility);
  discover(document.documentElement);
  mutations.observe(document.documentElement, { childList: true, subtree: true });
  frameId = requestAnimationFrame(tick);
  timer = setTimeout(stop, duration_s * 1000);
  mark("capture-start");
  return { started_utc: new Date(performance.timeOrigin + started).toISOString(), duration_s };
}

export function mark(label) { if (current?.running) current.mark(label); }
export function stop() { return current?.stop() ?? null; }
