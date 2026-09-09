// @vitest-environment jsdom
import { afterEach, expect, test, vi } from "vitest";
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/preact";
import { h } from "preact";
import { InputTimeline } from "../../src/sm64_events/ui/components/inputtimeline.js";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function longTimeline() {
  const markerLabel = vi.fn((frame) => `Moment ${frame}`);
  const frames = 1000;
  const data = { attempt_id:42, fps:30, frames, attempt_frames:frames,
    stretches:[[0,100,frames]], buttons:[[32768,"A"],[16384,"B"]],
    stick_max:84, dead_zone:8, angle_units:65536, actions:[], template:null,
    markers:Array.from({length:frames}, (_, frame) => ({frame, type:"pole",
      get label() { return markerLabel(frame); }})),
    runs:Array.from({length:frames}, (_, start) => ({start,length:1,
      buttons:start % 2 ? 16384 : 32768,stick_x:40,stick_y:20,yaw:0,speed:4})),
  };
  vi.stubGlobal("fetch", vi.fn(async () => ({ok:true,json:async () => data})));
  let callback;
  const times = [0,.1,.2];
  const video = Object.assign(new EventTarget(), {currentTime:0,duration:.3,paused:true,pause() {},
    requestVideoFrameCallback(fn) { callback=fn; return 1; },
    cancelVideoFrameCallback() {},
  });
  const props = {attemptId:42,video,clock:{times},frameMap:[100,101,102],frameMapSource:"plugin"};
  const view = render(h(InputTimeline,props));
  await waitFor(() => expect(view.container.querySelectorAll(".moment-mark").length).toBe(frames));
  await waitFor(() => expect(typeof callback).toBe("function"));
  return {...view,video,markerLabel,present:async (slot) => {
    await act(async () => callback(0,{mediaTime:times[slot]}));
  },replaceMap:async (frameMap) => {
    await act(async () => view.rerender(h(InputTimeline,{...props,frameMap})));
  }};
}

test("delivered pictures update the inspector without rebuilding the thousand timeline marks", async () => {
  const view=await longTimeline();
  expect(view.markerLabel.mock.calls.length).toBeGreaterThan(1000);
  view.markerLabel.mockClear();
  for (let picture=0;picture<20;picture+=1) await view.present(picture % 3);
  expect(view.container.querySelector(".input-inspector-frame strong").textContent).toBe("1 / 999");
  expect(view.markerLabel.mock.calls.length).toBeLessThan(100);
});

test("cached input bars seek the new mapping after the replay mapping changes", async () => {
  const view=await longTimeline();
  const button=view.container.querySelector(".input-lane:nth-last-child(1) button.input-bar");
  expect(button.getAttribute("aria-label")).toMatch(/^B held/);
  fireEvent.click(button);
  expect(view.video.currentTime).toBeGreaterThan(.1);
  expect(view.video.currentTime).toBeLessThan(.2);
  await view.replaceMap([100,102,101]);
  fireEvent.click(button);
  expect(view.video.currentTime).toBeGreaterThan(.2);
  expect(view.video.currentTime).toBeLessThan(.3);
});
