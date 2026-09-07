// @vitest-environment jsdom
import { afterEach, expect, test, vi } from "vitest";
import { watchReplayKeys } from "../../src/sm64_events/ui/replaykeys.js";

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

test("unmount stops the hold and returns shortcuts to the remaining player", () => {
  vi.useFakeTimers();
  const first = player(), second = player();
  key("keydown");
  stops.pop()(); second.root.remove();
  vi.advanceTimersByTime(1000);
  expect(second.step).toHaveBeenCalledTimes(1);
  key("keydown"); key("keyup");
  expect(first.step).toHaveBeenCalledTimes(1);
});
