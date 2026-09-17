import { afterEach, expect, test, vi } from "vitest";
import { scrubReviewSource, seekReviewSource,
  prepareReviewPlayback } from "../../src/sm64_events/ui/reviewsource.js";
import { cancelReviewSeek } from "../../src/sm64_events/ui/reviewseek.js";
import { pollJSON } from "../../src/sm64_events/ui/pollstate.js";

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

function decoder() {
  const video = new EventTarget();
  video.seeking = false;
  video.writes = [];
  let time = 0;
  Object.defineProperty(video, "currentTime", {
    get: () => time,
    set: next => { time = next; video.writes.push(next); video.seeking = true; },
  });
  video.settle = () => { video.seeking = false; video.dispatchEvent(new Event("seeked")); };
  return video;
}

test("a drag burst keeps only its last exact target and waits for the pending decode", async () => {
  vi.useFakeTimers();
  const video = decoder();
  for (let i = 0; i < 1000; i++) scrubReviewSource(video, i / 90000);
  await vi.advanceTimersByTimeAsync(0);
  expect(video.writes).toEqual([999 / 90000]);
  for (let i = 0; i < 1000; i++) scrubReviewSource(video, (5000 + i) / 90000);
  await vi.advanceTimersByTimeAsync(200);
  expect(video.writes).toHaveLength(1);
  video.settle();
  await vi.advanceTimersByTimeAsync(0);
  expect(video.writes).toEqual([999 / 90000, 5999 / 90000]);
  cancelReviewSeek(video);
});

test("a stalled seek can be replaced at a bounded rate and the final target is not dropped", async () => {
  vi.useFakeTimers();
  const video = decoder();
  for (let i = 0; i < 200; i++) {
    scrubReviewSource(video, i / 10);
    await vi.advanceTimersByTimeAsync(5);
  }
  await vi.advanceTimersByTimeAsync(250);
  expect(video.writes.length).toBeLessThanOrEqual(5);
  expect(video.currentTime).toBe(19.9);
  cancelReviewSeek(video);
});

test("an immediate step supersedes a queued drag, Play flushes it, and disposal cancels it", async () => {
  vi.useFakeTimers();
  const video = decoder();
  scrubReviewSource(video, 1);
  seekReviewSource(video, 2);
  await vi.advanceTimersByTimeAsync(1000);
  expect(video.writes).toEqual([2]);
  scrubReviewSource(video, 3);
  prepareReviewPlayback(video);
  expect(video.writes).toEqual([2, 3]);
  scrubReviewSource(video, 4);
  cancelReviewSeek(video);
  await vi.advanceTimersByTimeAsync(1000);
  expect(video.writes).toEqual([2, 3]);
});

test("pause observations stay serial through timeout and reject a stale response after disposal", async () => {
  vi.useFakeTimers();
  let inFlight = 0, peak = 0;
  const signals = [], changed = vi.fn();
  vi.stubGlobal("fetch", vi.fn((_url, { signal }) => {
    signals.push(signal); peak = Math.max(peak, ++inFlight);
    return new Promise((_resolve, reject) => {
      signal.addEventListener("abort", () => {
        inFlight--; reject(new DOMException("timeout", "AbortError"));
      });
    });
  }));
  const stop = pollJSON("/api/pause", changed);
  await vi.advanceTimersByTimeAsync(60000);
  expect(peak).toBe(1); expect(signals).toHaveLength(5);
  expect(changed).not.toHaveBeenCalled();
  stop(); await Promise.resolve();
  expect(inFlight).toBe(0);
  await vi.advanceTimersByTimeAsync(30000);
  expect(signals).toHaveLength(5);
  let respond;
  vi.stubGlobal("fetch", vi.fn(() => new Promise(resolve => { respond = resolve; })));
  const stopSecond = pollJSON("/api/pause", changed);
  stopSecond(); respond(new Response(JSON.stringify({ paused: true })));
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
  expect(changed).not.toHaveBeenCalled();
});
