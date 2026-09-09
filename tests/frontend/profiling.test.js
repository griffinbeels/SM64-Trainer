// @vitest-environment jsdom
import { afterEach, expect, test, vi } from "vitest";
import { start, stop } from "../../src/sm64_events/ui/profiling.js";

afterEach(() => { stop(); document.body.replaceChildren(); vi.useRealTimers(); });

test("new replay readiness is observed before a polling interval would elapse", async () => {
  start({duration_s: 5});
  const video = document.createElement("video");
  let total = 0;
  video.getVideoPlaybackQuality = () => ({ totalVideoFrames: total, droppedVideoFrames: 0 });
  document.body.append(video);
  await Promise.resolve(); // The real MutationObserver discovery checkpoint.
  total = 2;
  Object.defineProperty(video, "currentSrc", {value: "http://localhost/replay.mp4", configurable: true});
  video.dispatchEvent(new Event("loadeddata"));
  video.dispatchEvent(new Event("playing"));
  const result = stop();
  expect(result.video_events.map(row => row.event)).toEqual(["loadeddata", "playing"]);
  expect(result.videos[0].total_frames_delta).toBe(2);
  expect(result.videos[0].counters_reset).toBe(false);
});

test("source resets never produce a misleading frame-count delta", async () => {
  const video = document.createElement("video");
  Object.defineProperty(video, "currentSrc", {value: "http://localhost/old.mp4", configurable: true});
  video.getVideoPlaybackQuality = () => ({totalVideoFrames: 10, droppedVideoFrames: 1});
  document.body.append(video);
  start({duration_s: 5});
  video.dispatchEvent(new Event("emptied"));
  Object.defineProperty(video, "currentSrc", {value: "http://localhost/new.mp4"});
  const result = stop();
  expect(result.videos[0].counters_reset).toBe(true);
  expect(result.videos[0].dropped_frames_delta).toBeNull();
});

test("expiry and explicit stop disconnect observers and release timers", async () => {
  vi.useFakeTimers();
  start({duration_s: 1});
  expect(() => start()).toThrow("already active");
  await vi.advanceTimersByTimeAsync(1100);
  const result = stop();
  document.body.append(document.createElement("video"));
  await Promise.resolve();
  expect(stop()).toBe(result);
  expect(result.videos).toHaveLength(0);
  expect(vi.getTimerCount()).toBe(0);
  expect(() => start({duration_s: 301})).toThrow("between 1 and 300");
});
