import { expect, test, vi } from "vitest";
import { watchVideoPicture, presentedVideoTime } from "../../src/sm64_events/ui/videopicture.js";
import { stepGameFrame } from "../../src/sm64_events/ui/frame.js";

function player() {
  const callbacks = new Map(), events = new Map();
  let handle = 0;
  return {currentTime:.25, duration:.4, paused:true, src:"one.mp4", pause() {},
    requestVideoFrameCallback(fn) { callbacks.set(++handle,fn); return handle; },
    cancelVideoFrameCallback(id) { callbacks.delete(id); },
    addEventListener(type,fn) { events.set(type,fn); },
    removeEventListener(type) { events.delete(type); },
    present(time) {
      const pending = [...callbacks.values()]; callbacks.clear();
      pending.forEach(fn => fn(0,{mediaTime:time}));
    }, empty() { events.get("emptied")(); }, pending: () => callbacks.size,
  };
}

test("late subscribers and refreshes keep the presented time, not the requested seek", () => {
  const video = player(), early = vi.fn(), late = vi.fn();
  const stopEarly = watchVideoPicture(video,early);
  expect(early).toHaveBeenLastCalledWith(null);
  expect(video.pending()).toBe(1);
  video.present(.1);
  const stopLate = watchVideoPicture(video,late);
  expect(late).toHaveBeenLastCalledWith(.1);
  expect(video.pending()).toBe(1);
  stepGameFrame(video,1,30,[100,101,99,100],{times:[0,.1,.2,.3]});
  expect(video.currentTime).toBe(.25); // next shown slot is 2, not slot 3
  stopLate();
  const stopAgain = watchVideoPicture(video,late);
  expect(late).toHaveBeenLastCalledWith(.1);
  video.empty();
  expect(late).toHaveBeenLastCalledWith(null);
  expect(presentedVideoTime(video)).toBeNull();
  stopEarly(); stopAgain();
  expect(video.pending()).toBe(0);
});

test("changing source after the last observer left cannot reuse the old picture", () => {
  const video = player(), read = vi.fn();
  const stop = watchVideoPicture(video,read);
  video.present(.2); stop();
  video.src="two.mp4";
  const stopNew = watchVideoPicture(video,read);
  expect(read).toHaveBeenLastCalledWith(null);
  stopNew();
});
