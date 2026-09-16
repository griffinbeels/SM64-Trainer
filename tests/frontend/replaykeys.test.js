// @vitest-environment jsdom
import { afterEach, expect, test, vi } from "vitest";
import { watchReplayKeys } from "../../src/sm64_events/ui/replaykeys.js";
import { setReplaySpeed, stopShuttle } from "../../src/sm64_events/ui/replayshuttle.js";
import { watchReviewCommands, playReview } from "../../src/sm64_events/ui/reviewcommands.js";

const stops = [];
afterEach(() => {
  stops.splice(0).forEach(stop => stop());
  document.body.replaceChildren();
  vi.useRealTimers();
});

function player() {
  const root = document.createElement("div"), button = document.createElement("button");
  root.append(button); document.body.append(root);
  const step = vi.fn(), toStart = vi.fn(), resume = vi.fn();
  stops.push(watchReplayKeys(root, {step,toStart,onPress:()=>resume}));
  button.focus();
  return {root,button,step,toStart,resume};
}

function key(type, target = window, options = {}) {
  target.dispatchEvent(new KeyboardEvent(type, {key:"ArrowRight",bubbles:true,cancelable:true,...options}));
}

test("only the opened or subsequently focused player receives arrows and holds", () => {
  vi.useFakeTimers();
  const first = player(), second = player();
  key("keydown");
  expect(first.step).not.toHaveBeenCalled();
  expect(second.step).toHaveBeenCalledTimes(1);
  vi.advanceTimersByTime(400);
  expect(second.step.mock.calls.length).toBeGreaterThan(1);
  first.button.focus();
  expect(second.resume).toHaveBeenCalledTimes(1);
  const count = second.step.mock.calls.length;
  vi.advanceTimersByTime(200);
  expect(second.step).toHaveBeenCalledTimes(count);
  key("keyup"); key("keydown",first.button); key("keyup");
  expect(first.step).toHaveBeenCalledTimes(1);
  expect(first.resume).toHaveBeenCalledTimes(1);
  key("keydown",first.button,{key:"ArrowDown"});
  expect(first.toStart).toHaveBeenCalledTimes(1);
  expect(second.toStart).not.toHaveBeenCalled();
});

test("pointer interaction changes ownership and fields or dialogs retain their arrows", () => {
  const first = player(), second = player();
  first.root.dispatchEvent(new Event("pointerdown",{bubbles:true}));
  key("keydown"); key("keyup");
  expect(first.step).toHaveBeenCalledTimes(1);
  expect(second.step).not.toHaveBeenCalled();
  const input = document.createElement("input"); first.root.append(input);
  key("keydown",input);
  const dialog = document.createElement("div"), button = document.createElement("button");
  dialog.setAttribute("role","dialog"); dialog.append(button); document.body.append(dialog);
  key("keydown",button);
  key("keydown",window,{ctrlKey:true});
  expect(first.step).toHaveBeenCalledTimes(1);
});

test("unmount stops the hold without handing keys to an unrelated remaining player", () => {
  vi.useFakeTimers();
  const first = player(), second = player();
  key("keydown");
  stops.pop()(); second.root.remove();
  vi.advanceTimersByTime(1000);
  expect(second.step).toHaveBeenCalledTimes(1);
  key("keydown"); key("keyup");
  expect(first.step).not.toHaveBeenCalled();
  first.button.focus();
  key("keydown"); key("keyup");
  expect(first.step).toHaveBeenCalledTimes(1);
});

test("JKL shuttles only the active video, contains repeats and leaves fields alone", () => {
  vi.useFakeTimers();
  const root = document.createElement("div"), video = document.createElement("video");
  const input = document.createElement("input");
  root.append(video, input); document.body.append(root);
  Object.defineProperty(video, "duration", { value: 20 });
  video.currentTime = 10;
  video.play = vi.fn().mockResolvedValue(); video.pause = vi.fn();
  stops.push(watchReplayKeys(root, { video, step: vi.fn(), toStart: vi.fn() }));
  root.dispatchEvent(new Event("pointerdown", {bubbles:true}));
  key("keydown", video, { key: "l" });
  key("keydown", video, { key: "l" });
  expect(video.playbackRate).toBe(1.5);
  key("keydown", input, { key: "k" });
  expect(video.pause).not.toHaveBeenCalled();
  key("keydown", video, { key: "k" });
  expect(video.pause).toHaveBeenCalledTimes(1);
  expect(video.playbackRate).toBe(1);
  key("keyup", video, { key: "k" });
  key("keydown", video, { key: "j" });
  vi.advanceTimersByTime(200);
  expect(video.currentTime).toBeLessThan(10);
  const repeated = new KeyboardEvent("keydown", { key: "ArrowRight", repeat: true, bubbles: true, cancelable: true });
  video.dispatchEvent(repeated);
  expect(repeated.defaultPrevented).toBe(true);
  stopShuttle(video); // Explicit stepping/scrubbing owns the next picture.
  const stopped = video.currentTime;
  vi.advanceTimersByTime(500);
  expect(video.currentTime).toBe(stopped);
  const other = document.createElement("button"); document.body.append(other);
  other.focus();
  const plays = video.play.mock.calls.length;
  key("keydown", other, { key: "l" });
  expect(video.play).toHaveBeenCalledTimes(plays);
});

test("K chords step, loop commands share transport ownership, play respects the range", () => {
  const root = document.createElement("div"), video = document.createElement("video");
  const input = document.createElement("input"); root.append(video, input); document.body.append(root);
  video.pause = vi.fn(); video.play = vi.fn().mockResolvedValue();
  const step = vi.fn(), commands = { in: vi.fn(), out: vi.fn(), clear: vi.fn(), start: vi.fn(), range: { start: 2, end: 4, enabled: true } };
  stops.push(watchReplayKeys(root, { video, step, toStart: vi.fn() }));
  root.dispatchEvent(new Event("pointerdown", {bubbles:true}));
  stops.push(watchReviewCommands(video, () => commands));
  key("keydown", video, { key: "k" });
  for (const [letter, direction] of [["j", -1], ["l", 1]]) {
    key("keydown", video, { key: letter }); key("keyup", video, { key: letter });
    expect(step).toHaveBeenLastCalledWith(direction);
  }
  expect(step).toHaveBeenCalledTimes(2);
  key("keyup", video, { key: "k" });
  for (const letter of ["i", "o", "x"]) key("keydown", video, { key: letter });
  key("keydown", video, { key: "I", shiftKey: true });
  for (const name of ["in", "out", "clear", "start"]) expect(commands[name]).toHaveBeenCalledTimes(1);
  key("keydown", input, { key: "x" }); expect(commands.clear).toHaveBeenCalledTimes(1);
  for (const [position, expected] of [[0, 2], [3, 3], [4, 2]]) {
    video.currentTime = position; playReview(video); expect(video.currentTime).toBe(expected);
  }
});


function mediaPlayer() {
  const root = document.createElement("div"), video = document.createElement("video");
  root.append(video); document.body.append(root);
  Object.defineProperty(video, "duration", { value: 100 });
  video.currentTime = 50;
  video.play = vi.fn().mockResolvedValue(); video.pause = vi.fn();
  const step = vi.fn();
  stops.push(watchReplayKeys(root, { video, step, toStart: vi.fn() }));
  root.dispatchEvent(new Event("pointerdown", { bubbles: true }));
  return { root, video, step };
}

function tap(video, letter) {
  key("keydown", video, { key: letter }); key("keyup", video, { key: letter });
}

test.each([.25, .75, 3])("JKL uses the exact shuttle ladder from manual %sx", rate => {
  vi.useFakeTimers();
  const { video } = mediaPlayer();
  video.playbackRate = rate;
  for (const [letter, name] of [["j", "Reverse"], ["l", "Forward"], ["j", "Reverse"]]) {
    for (const speed of [1, 1.5, 2, 3, 4, 4]) {
      tap(video, letter);
      expect(video.dataset.reviewShuttle).toBe(`${name} ${speed}×`);
      key("keydown", video, { key: letter, repeat: true });
      expect(video.dataset.reviewShuttle).toBe(`${name} ${speed}×`);
    }
  }
  tap(video, "k");
  expect(video.playbackRate).toBe(1);
  expect(video.dataset.reviewShuttle).toBe("");
  const position = video.currentTime;
  vi.advanceTimersByTime(1000);
  expect(video.currentTime).toBe(position);
  tap(video, "l");
  expect(video.playbackRate).toBe(1);
});

test.each([.25, 4])("K resets a manual %sx rate even without a shuttle", rate => {
  const { video } = mediaPlayer();
  video.playbackRate = rate;
  tap(video, "k");
  expect(video.playbackRate).toBe(1);
  expect(video.pause).toHaveBeenCalledTimes(1);
});

test.each([["j", "ArrowLeft", -1], ["l", "ArrowRight", 1]])(
  "held K+%s uses the arrow hold schedule and stops on either release", (letter, arrow, direction) => {
    vi.useFakeTimers();
    const { video, step } = mediaPlayer();
    const counts = [];
    for (const chord of [false, true]) {
      step.mockClear();
      if (chord) key("keydown", video, { key: "k" });
      key("keydown", video, { key: chord ? letter : arrow });
      expect(step).toHaveBeenCalledExactlyOnceWith(direction);
      const trace = [step.mock.calls.length];
      for (const elapsed of [249, 1, 66, 198]) {
        vi.advanceTimersByTime(elapsed); trace.push(step.mock.calls.length);
      }
      counts.push(trace);
      key("keyup", video, { key: chord ? letter : arrow });
      vi.advanceTimersByTime(1000);
      expect(step).toHaveBeenCalledTimes(trace.at(-1));
      if (chord) key("keyup", video, { key: "k" });
    }
    expect(counts[1]).toEqual(counts[0]);
    expect(counts[0].at(-1)).toBeGreaterThan(1);
    key("keydown", video, { key: "k" });
    key("keydown", video, { key: letter });
    vi.advanceTimersByTime(400);
    key("keyup", video, { key: "k" });
    const count = step.mock.calls.length;
    vi.advanceTimersByTime(500);
    expect(step).toHaveBeenCalledTimes(count);
    expect(video.play).not.toHaveBeenCalled();
    key("keyup", video, { key: letter });
  });


test("manual speed selection cancels reverse and starts the next shuttle at one", () => {
  vi.useFakeTimers();
  const { video } = mediaPlayer();
  tap(video, "j"); tap(video, "j");
  setReplaySpeed(video, .5);
  const position = video.currentTime;
  vi.advanceTimersByTime(1000);
  expect(video.currentTime).toBe(position);
  expect(video.playbackRate).toBe(.5);
  expect(video.dataset.reviewShuttle).toBe("");
  tap(video, "j");
  expect(video.dataset.reviewShuttle).toBe("Reverse 1×");
});
