// @vitest-environment jsdom
import { afterEach, expect, test, vi } from "vitest";
import { act, cleanup, render, waitFor } from "@testing-library/preact";
import { h } from "preact";
import { InputTimeline } from "../../src/sm64_events/ui/components/inputtimeline.js";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function timeline({ map, times, igts = null, stretches = [[0,100,3]] }) {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({
    attempt_id: 42, fps: 30, frames: 3, attempt_frames: 3, stretches,
    buttons: [[32768,"A"], [16384,"B"]], stick_max: 84, dead_zone: 8,
    angle_units: 65536, actions: [], markers: [], template: null,
    runs: [
      {start:0, length:1, buttons:32768, stick_x:40, stick_y:80, yaw:0, speed:5},
      {start:1, length:1, buttons:16384, stick_x:-40, stick_y:80, yaw:100, speed:6},
      {start:2, length:1, buttons:32768, stick_x:60, stick_y:80, yaw:200, speed:7},
    ],
  }) })));
  let callback;
  const video = {currentTime: times[0] + .001, duration: times.at(-1) + .1,
    paused: true, pause() {},
    addEventListener() {}, removeEventListener() {},
    requestVideoFrameCallback(fn) { callback = fn; return 1; },
    cancelVideoFrameCallback() {},
  };
  const props = {attemptId:42, video,
    frameMap: map, pictureIgt: igts, frameMapSource:"plugin", clock:{times}};
  const view = render(h(InputTimeline, props));
  await waitFor(() => expect(view.container.querySelector(".input-inspector")).not.toBeNull());
  await waitFor(() => expect(typeof callback).toBe("function"));
  return { ...view, refreshClock: async () => {
    await act(async () => view.rerender(h(InputTimeline, {...props, clock:{times}})));
  }, present: async (slot) => {
    // The presented picture and requested seek may disagree. Consumers must
    // retain the callback's slot, rather than looking up a raw number again.
    await act(async () => callback(0, {mediaTime:times[slot]}));
  }};
}

test("a missing map slot clears the readings instead of inventing a timed input", async () => {
  const view = await timeline({map:[100,null,102], times:[0,.1,.2], igts:[1,null,3]});
  await view.present(0);
  expect(view.container.querySelector('.controller-buttons[aria-label="Holding A"]')).not.toBeNull();
  await view.present(1);
  expect(view.container.querySelector(".input-inspector-frame strong").textContent).toBe("— / 2");
  expect(view.container.querySelector('.controller-buttons[aria-label="Input not recorded"]')).not.toBeNull();
  expect(view.container.querySelector(".stick-box-dot:not(.is-template)")).toBeNull();
  expect(view.container.querySelector(".input-playhead")).toBeNull();
  await view.present(2);
  expect(view.container.querySelector('.controller-buttons[aria-label="Holding A"]')).not.toBeNull();
  expect(view.container.querySelector(".input-inspector-frame strong").textContent).toBe("2 / 2");
});

test("the clock retains the presented occurrence when a raw counter repeats", async () => {
  const view = await timeline({map:[100,101,99,100,101], times:[0,.1,.2,.3,.4], igts:[50,51,2,3,4]});
  await view.present(0);
  expect(view.container.querySelector(".input-inspector-frame .is-stamped").textContent.trim()).toBe('01"66');
  await view.present(3);
  expect(view.container.querySelector(".input-inspector-frame .is-stamped").textContent.trim()).toBe('00"10');
  await view.refreshClock();
  expect(view.container.querySelector(".input-inspector-frame .is-stamped").textContent.trim()).toBe('00"10');
});

test("video without a mapping never borrows the arithmetic timeline clock", async () => {
  const view = await timeline({map:null, times:[0,.1,.2]});
  await view.present(0);
  expect(view.container.querySelector(".input-inspector-frame strong").textContent).toBe("— / 2");
  expect(view.container.querySelector('.controller-buttons[aria-label="Input not recorded"]')).not.toBeNull();
});

test("known input with no stamped timer leaves the video's time unavailable", async () => {
  const view = await timeline({map:[100,101,102], times:[0,.1,.2]});
  await view.present(1);
  expect(view.container.querySelector('.controller-buttons[aria-label="Holding B"]')).not.toBeNull();
  expect(view.container.querySelector(".input-inspector-frame .meta").textContent).toBe("Time unavailable");
});
