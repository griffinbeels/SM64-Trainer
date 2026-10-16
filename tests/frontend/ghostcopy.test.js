// @vitest-environment jsdom
// "Show ghosts" swaps the replay to its ghost copy in place. The real player
// renders; only the replay answer and the browser's media loading are stood in.
import { h } from "preact";
import { render, cleanup, fireEvent, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { watchVideoPicture } from "../../src/sm64_events/ui/videopicture.js";

const reply = vi.hoisted(() => ({ current: null }));
vi.mock("../../src/sm64_events/ui/api.js", () => ({
  send: vi.fn(() => Promise.resolve(reply.current)),
  getJSON: vi.fn(() => new Promise(() => {})),
}));
vi.mock("../../src/sm64_events/ui/components/recordinglink.js", () => ({ RecordingLink: () => null }));
const { ReplayPlayer, placeOf } = await import("../../src/sm64_events/ui/components/replay.js");

const CLEAN = "/api/replay/clips/clip_attempt_42.mp4";
const GHOSTS = "/api/replay/ghostcopy/clips/ghostcopy_attempt_42.mp4";
const payload = (extra = {}) => ({ clip_url: CLEAN, ghost_copy_url: null, ghost_copy_reason: null,
  frame_times: [0, .1, .2, .3, .4], duration_s: 2.5, fps: 30, game_fps: 30,
  anchor_offset_s: 0, source: "buffer", ...extra });

// What a browser does to the element, which jsdom does not: a source finishes
// loading paused, at its start, at the default speed, then reports metadata.
const media = new WeakMap();
const of = video => {
  if (!media.has(video)) media.set(video, { readyState: 0, currentTime: 0, paused: true });
  return media.get(video);
};
function loaded(video) {
  Object.assign(of(video), { readyState: 1, currentTime: 0, paused: true });
  video.playbackRate = 1;
  video.dispatchEvent(new Event("loadedmetadata"));
}
const props = {
  readyState: { get() { return of(this).readyState; } },
  currentTime: { get() { return of(this).currentTime; }, set(value) { of(this).currentTime = value; } },
  paused: { get() { return of(this).paused; } },
  play: { value() { of(this).paused = false; this.dispatchEvent(new Event("play")); return Promise.resolve(); } },
  pause: { value() { of(this).paused = true; this.dispatchEvent(new Event("pause")); } },
};
const original = {};
beforeEach(() => {
  for (const [name, descriptor] of Object.entries(props)) {
    original[name] = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, name);
    Object.defineProperty(HTMLMediaElement.prototype, name, { ...descriptor, configurable: true });
  }
  localStorage.clear();
});
afterEach(() => {
  cleanup();
  for (const [name, descriptor] of Object.entries(original))
    Object.defineProperty(HTMLMediaElement.prototype, name, descriptor);
});

async function open(extra) {
  reply.current = payload(extra);
  const view = render(h(ReplayPlayer, { attemptId: 42 }));
  const video = await waitFor(() => {
    const element = view.container.querySelector("video");
    expect(element).not.toBeNull();
    return element;
  });
  const toggle = () => [...view.container.querySelectorAll("button")]
    .find(button => button.textContent.trim() === "Show ghosts") || null;
  return { view, video, toggle };
}

test("a replay without a ghost copy offers no Show ghosts", async () => {
  localStorage.setItem("sm64.replayShowGhosts", "1");
  const { video, toggle } = await open({ ghost_copy_reason: "Ghosts in recording was off" });
  loaded(video);
  expect(toggle()).toBeNull();
  expect(video.getAttribute("src")).toBe(CLEAN);
});

test("Show ghosts swaps to the copy on the same picture, still paused, at the same speed", async () => {
  const { video, toggle } = await open({ ghost_copy_url: GHOSTS });
  loaded(video); // the clean recording opens and plays from the attempt start
  expect(video.paused).toBe(false);
  expect(toggle().getAttribute("aria-pressed")).toBe("false");
  video.pause(); video.currentTime = 1.234; video.playbackRate = .5;
  fireEvent.click(toggle());
  await waitFor(() => expect(video.getAttribute("src")).toBe(GHOSTS));
  expect(toggle().getAttribute("aria-pressed")).toBe("true");
  expect(toggle().classList.contains("is-active")).toBe(true);
  expect(localStorage.getItem("sm64.replayShowGhosts")).toBe("1");
  loaded(video);
  expect([video.currentTime, video.paused, video.playbackRate]).toEqual([1.234, true, .5]);
  // And back: the clean recording returns to the same place.
  video.currentTime = 2.001;
  fireEvent.click(toggle());
  await waitFor(() => expect(video.getAttribute("src")).toBe(CLEAN));
  loaded(video);
  expect([video.currentTime, video.paused]).toEqual([2.001, true]);
  expect(toggle().getAttribute("aria-pressed")).toBe("false");
  expect(localStorage.getItem("sm64.replayShowGhosts")).toBe("0");
});

test("a playing replay keeps playing after the swap", async () => {
  const { video, toggle } = await open({ ghost_copy_url: GHOSTS });
  loaded(video);
  video.currentTime = 1.5;
  fireEvent.click(toggle());
  await waitFor(() => expect(video.getAttribute("src")).toBe(GHOSTS));
  loaded(video);
  expect([video.currentTime, video.paused]).toEqual([1.5, false]);
});

test("two changes while the copy loads keep the first place, not the loading zero", async () => {
  const { video, toggle } = await open({ ghost_copy_url: GHOSTS });
  loaded(video);
  video.pause(); video.currentTime = 1.75;
  fireEvent.click(toggle());
  await waitFor(() => expect(video.getAttribute("src")).toBe(GHOSTS));
  // The copy has its metadata but has not announced it yet.
  Object.assign(of(video), { readyState: 1, currentTime: 0 });
  fireEvent.click(toggle());
  await waitFor(() => expect(video.getAttribute("src")).toBe(CLEAN));
  loaded(video);
  expect([video.currentTime, video.paused]).toEqual([1.75, true]);
});

test("Show ghosts before the replay has loaded opens the copy at the attempt start", async () => {
  const { video, toggle } = await open({ ghost_copy_url: GHOSTS, anchor_offset_s: 1 });
  fireEvent.click(toggle());
  await waitFor(() => expect(video.getAttribute("src")).toBe(GHOSTS));
  loaded(video);
  expect([video.currentTime, video.paused]).toEqual([1, false]);
});

test("the choice carries to the next replay that has a copy", async () => {
  const first = await open({ ghost_copy_url: GHOSTS });
  loaded(first.video);
  fireEvent.click(first.toggle());
  await waitFor(() => expect(first.video.getAttribute("src")).toBe(GHOSTS));
  cleanup();
  const next = await open({ ghost_copy_url: "/api/replay/saved/43/ghosts", clip_url: "/api/replay/saved/43" });
  expect(next.video.getAttribute("src")).toBe("/api/replay/saved/43/ghosts");
  expect(next.toggle().getAttribute("aria-pressed")).toBe("true");
  loaded(next.video); // opens and plays from the attempt start, as a clean replay does
  expect(next.video.paused).toBe(false);
});

test("a ghost copy that cannot play falls back to the clean recording in place, and says so", async () => {
  const { view, video, toggle } = await open({ ghost_copy_url: GHOSTS });
  loaded(video);
  video.pause(); video.currentTime = .9;
  fireEvent.click(toggle());
  await waitFor(() => expect(video.getAttribute("src")).toBe(GHOSTS));
  video.dispatchEvent(new Event("error"));
  await waitFor(() => expect(video.getAttribute("src")).toBe(CLEAN));
  expect(view.container.querySelector(".replay-status-row").textContent)
    .toContain("The ghost copy could not be played");
  expect(toggle().disabled).toBe(true);
  expect(toggle().title).toBe("The ghost copy could not be played");
  loaded(video);
  expect([video.currentTime, video.paused]).toEqual([.9, true]);
});

test("paused, the place is the picture on screen even when its time rounds below its slot", () => {
  const callbacks = new Map();
  const video = Object.assign(new EventTarget(), { currentTime: .2, paused: true, playbackRate: 2, src: "x.mp4",
    requestVideoFrameCallback(fn) { callbacks.set(callbacks.size + 1, fn); return callbacks.size; },
    cancelVideoFrameCallback(id) { callbacks.delete(id); } });
  const stop = watchVideoPicture(video, () => {});
  [...callbacks.values()].forEach(fn => fn(0, { mediaTime: .1 - 5e-7 })); // slot 1, rounded down
  const clock = { times: [0, .1, .2, .3], duration: .4 };
  // currentTime already reads slot 2's start; the screen still shows slot 1.
  const place = placeOf(video, clock);
  expect(place.time).toBeCloseTo(.15, 9);
  expect([place.paused, place.rate]).toEqual([true, 2]);
  video.paused = false;
  expect(placeOf(video, clock)).toEqual({ time: .2, paused: false, rate: 2 });
  stop();
});
