// @vitest-environment jsdom
import { afterEach, expect, test, vi } from "vitest";
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/preact";
import { h } from "preact";
import { InputTimeline } from "../../src/sm64_events/ui/components/inputtimeline.js";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function timeline({ map, times, igts = null, stretches = [[0,100,3]], alignment = null, inputSpan = undefined,
                          data = {}, agreement = null }) {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({
    attempt_id: 42, fps: 30, frames: 3, attempt_frames: 3, stretches,
    buttons: [[32768,"A"], [16384,"B"]], stick_max: 84, dead_zone: 8,
    angle_units: 65536, actions: [], markers: [], template: null,
    runs: [
      {start:0, length:1, buttons:32768, stick_x:40, stick_y:80, yaw:0, speed:5},
      {start:1, length:1, buttons:16384, stick_x:-40, stick_y:80, yaw:100, speed:6},
      {start:2, length:1, buttons:32768, stick_x:60, stick_y:80, yaw:200, speed:7},
    ], ...data,
  }) })));
  let callback;
  const video = {currentTime: times[0] + .001, duration: times.at(-1) + .1,
    paused: true, pause() {},
    addEventListener() {}, removeEventListener() {},
    requestVideoFrameCallback(fn) { callback = fn; return 1; },
    cancelVideoFrameCallback() {},
  };
  const props = {attemptId:42, video,
    frameMap: map, pictureIgt: igts, inputAlignment: alignment, inputSpan,
    padAgreement: agreement,
    frameMapSource: map ? "plugin" : null, clock:{times}};
  const view = render(h(InputTimeline, props));
  await waitFor(() => expect(view.container.querySelector(".input-inspector")).not.toBeNull());
  await waitFor(() => expect(typeof callback).toBe("function"));
  return { ...view, video, refreshClock: async () => {
    await act(async () => view.rerender(h(InputTimeline, {...props, clock:{times}})));
  }, present: async (slot) => {
    // The presented picture and requested seek may disagree. Consumers must
    // retain the callback's slot, rather than looking up a raw number again.
    await act(async () => callback(0, {mediaTime:times[slot]}));
  }};
}

test("the captured span prevents an old held picture from stretching the track", async () => {
  await timeline({map:[10,10,100,101,102], times:[0,2,2.1,2.2,2.3], inputSpan:[100,102]});
  expect(fetch).toHaveBeenCalledWith("/api/attempts/42/inputs?from_frame=100&to_frame=102");
});

test("a held-only clip requests the attempt alone", async () => {
  await timeline({map:[10,10], times:[0,2], inputSpan:null});
  expect(fetch).toHaveBeenCalledWith("/api/attempts/42/inputs");
});

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

test("an unverified replay explains the missing association without offering setup as a repair", async () => {
  const view = await timeline({map:null, times:[0,.1,.2],
    alignment:{status:"unverified", reason:"missing_source_clock"}});
  const note = view.container.querySelector(".input-frame-map-note");
  expect(note.textContent).toContain("Input alignment could not be verified for this replay.");
  expect(note.querySelector("button")).toBeNull();
});

test("clicking input without a map cannot seek video by an assumed offset", async () => {
  const view = await timeline({map:null, times:[0,.1,.2]});
  view.video.currentTime = .15;
  fireEvent.click(view.container.querySelector("button.input-bar"));
  expect(view.video.currentTime).toBe(.15);
});

test("clicking mapped input still seeks its encoded picture", async () => {
  const view = await timeline({map:[100,101,102], times:[0,.1,.2]});
  view.video.currentTime = .15;
  fireEvent.click(view.container.querySelector("button.input-bar"));
  expect(view.video.currentTime).toBeGreaterThanOrEqual(0);
  expect(view.video.currentTime).toBeLessThan(.1);
});

test("discrepancies seek their picture slot and display both buttons and attempt frames", async () => {
  const view = await timeline({map:[100,101,100], times:[0,.1,.2],
    data:{lead_frames:1},
    agreement:{pictures:3, agree:2, disagreements:[[2,100,[0,0,32768],[0,0,16384]]]}});
  fireEvent.click(view.container.querySelector(".input-screen-check"));
  const row = view.container.querySelector(".input-screen-check-row");
  expect(row.textContent).toContain("frame -1");
  expect(row.textContent).toContain("game neutral · B");
  expect(row.textContent).toContain("timeline neutral · A");
  fireEvent.click(row);
  expect(view.video.currentTime).toBeGreaterThan(.2);
  expect(view.video.currentTime).toBeLessThan(.3);
});

test("padded action, input and moment labels share the attempt's zero", async () => {
  const view = await timeline({map:[160,190], times:[0,.1], stretches:[[0,100,100]],
    data:{frames:100, lead_frames:60, attempt_frames:40,
      runs:[{start:90,length:1,buttons:32768,stick_x:40,stick_y:80,yaw:0,speed:5}],
      actions:[{start:90,length:1,action:1,label:"Jump",group:"airborne"}],
      markers:[{frame:90,label:"Grabbed the pole",type:"pole"}],
      template:{name:"Example",frames:40,author:"Other player",
        runs:[{start:90,length:1,buttons:32768,stick_x:40,stick_y:80,yaw:0,speed:5}],
        actions:[{start:90,length:1,action:1,label:"Jump",group:"airborne"}]}}});
  await view.present(1);
  for (const selector of [".input-bar", ".action-span", ".moment-mark"]) {
    for (const element of view.container.querySelectorAll(selector)) {
      expect(element.title).toContain('01"00');
      expect(element.title).not.toContain('03"00');
    }
  }
  expect(view.container.querySelector(".input-inspector-moment").textContent).toContain('at 01"00');
});
