// @vitest-environment jsdom
import { afterEach, expect, test, vi } from "vitest";
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/preact";
import { h } from "preact";
import { InputTimeline, mappedLoopWindow } from "../../src/sm64_events/ui/components/inputtimeline.js";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function timeline({ map, times, igts = null, stretches = [[0,100,3]], alignment = null, inputSpan = undefined,
                          data = {}, agreement = null, states = null, review = undefined, reviewLoading = false }) {
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
  const video = Object.assign(new EventTarget(), {currentTime: times[0] + .001, duration: times.at(-1) + .1,
    paused: true, pause() {},
    requestVideoFrameCallback(fn) { callback = fn; return 1; },
    cancelVideoFrameCallback() {},
  });
  let props = {attemptId:42, video,
    frameMap: map, pictureIgt: igts, inputAlignment: alignment, inputSpan,
    padAgreement: agreement,
    pictureStates: states,
    frameMapSource: map ? "plugin" : null, clock:{times}};
  let view;
  if (review !== undefined || reviewLoading) {
    props = {...props, reviewState: reviewLoading ? null : review, onReviewState: (patch) => {
      props = {...props, reviewState: {...props.reviewState, ...patch}};
      view.rerender(h(InputTimeline, props));
    }};
  }
  view = render(h(InputTimeline, props));
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

function clickTimeline(view, x) {
  const lanes = view.container.querySelector(".input-lanes");
  lanes.setPointerCapture = vi.fn();
  lanes.hasPointerCapture = () => true;
  lanes.releasePointerCapture = vi.fn();
  for (const type of ["pointerdown", "pointerup"]) {
    const event = new Event(type, {bubbles:true,cancelable:true});
    Object.assign(event, {clientX:x,button:0,pointerId:1});
    fireEvent(lanes, event);
  }
}

test("the captured span prevents an old held picture from stretching the track", async () => {
  await timeline({map:[10,10,100,101,102], times:[0,2,2.1,2.2,2.3], inputSpan:[100,102]});
  expect(fetch).toHaveBeenCalledWith("/api/attempts/42/inputs?from_frame=100&to_frame=102");
});

test("presented capture wins over stale poller data without hiding unknown slots", async () => {
  const view = await timeline({map:[100,100,100], times:[0,.1,.2],
    states:[{stick_x:1,stick_y:1,buttons:16384,yaw:100,speed:32.1},
      {stick_x:-2,stick_y:3,buttons:32768,yaw:200,speed:12},null]});
  await view.present(0);
  expect(view.container.querySelector('.controller-buttons[aria-label="Holding B"]')).not.toBeNull();
  expect(view.container.querySelector('.controller-panel .stick-values').textContent).toBe("U1R1");
  view.video.currentTime = .201;
  await view.refreshClock();
  expect(view.container.querySelector('.controller-panel .stick-values').textContent).toBe("U1R1");
  await view.present(1);
  expect(view.container.querySelector('.controller-panel .stick-values').textContent).toBe("U3L2");
  await view.present(2);
  expect(view.container.querySelector('.controller-panel .stick-values').textContent).toContain("not captured");
  expect(view.container.querySelector('.controller-buttons').textContent).toContain("not captured");
});

test("lanes draw the picture's own pad, and the poll only fills frames with no picture", async () => {
  // The poll missed B on frame 1 (it filed A there); the picture of frame 1
  // carries B. Frame 2 has no picture at all. Frame 0's picture agrees.
  const view = await timeline({map:[100,101], times:[0,.1],
    states:[{stick_x:40,stick_y:80,buttons:32768,yaw:0,speed:5},
            {stick_x:-40,stick_y:80,buttons:16384,yaw:100,speed:6}],
    data:{runs:[
      {start:0, length:1, buttons:32768, stick_x:40, stick_y:80, yaw:0, speed:5},
      {start:1, length:1, buttons:32768, stick_x:40, stick_y:80, yaw:0, speed:5},
      {start:2, length:1, buttons:32768, stick_x:60, stick_y:80, yaw:200, speed:7},
    ]}});
  const bars = Array.from(view.container.querySelectorAll("button.input-bar"))
    .map((el) => [el.getAttribute("aria-label").replace(/ from \S+,/, ","),
                  el.style.left, el.classList.contains("is-polled")]);
  // A: frame 0 from the stamp, frame 2 polled (no picture). B: frame 1 from the stamp.
  expect(bars).toEqual([
    ["A held, 1 frames", "0%", false],
    ["A held, 1 frames, polled with no picture", "66.66666666666666%", true],
    ["B held, 1 frames", "33.33333333333333%", false],
  ]);
  expect(view.container.querySelector(".stick-line.is-polled")).not.toBeNull();
  expect(view.container.querySelector(".speed-line.is-polled")).not.toBeNull();
});

test("without exact capture the lanes are the polled track and nothing is marked polled", async () => {
  const view = await timeline({map:null, times:[0,.1,.2]});
  expect(view.container.querySelectorAll("button.input-bar").length).toBe(3);
  expect(view.container.querySelector(".input-bar.is-polled")).toBeNull();
  expect(view.container.querySelector(".stick-line.is-polled")).toBeNull();
});

const exampleTemplate = () => ({
  id: 7, name: "Long example", author: "Another player", frames: 8,
  runs: [{start:1,length:1,buttons:16384,stick_x:12,stick_y:18,yaw:90,speed:2}],
  actions: [],
  source: {revision:"abc123", frames:8,
    runs:[{start:0,length:1,buttons:16384,stick_x:12,stick_y:18,yaw:90,speed:2},
          {start:4,length:2,buttons:32768,stick_x:48,stick_y:30,yaw:900,speed:11}],
    actions:[{start:4,length:2,label:"Jump",group:"airborne"}]},
});

test("shifting restores the full template tail without moving actual input, timer or video", async () => {
  const view = await timeline({map:[100,101,102,103,104], times:[0,.1,.2,.3,.4],
    stretches:[[0,100,5]], igts:[0,1,2,3,4], review:{},
    data:{frames:5,lead_frames:1,attempt_frames:4,template:exampleTemplate()}});
  const actual = Array.from(view.container.querySelectorAll("button.input-bar"), (el) => el.outerHTML);
  const before = view.video.currentTime;
  expect(view.container.querySelector(".action-span.is-template")).toBeNull();
  fireEvent.click(view.getByRole("button", {name:"Shift template earlier one frame"}));
  fireEvent.click(view.getByRole("button", {name:"Shift template earlier one frame"}));
  await waitFor(() => expect(view.getByLabelText("Template offset").textContent).toBe("-2f"));
  expect(Array.from(view.container.querySelectorAll("button.input-bar"), (el) => el.outerHTML)).toEqual(actual);
  expect(view.container.querySelector(".action-span.is-template").style.left).toBe("60%");
  expect(view.video.currentTime).toBe(before);
  await view.present(3);
  expect(view.container.querySelector(".input-inspector-frame strong").textContent).toBe("2 / 3");
  expect(view.container.querySelector(".input-inspector-frame .is-stamped").textContent).toBe('00"10');
  expect(view.container.querySelector(".input-inspector-action.is-template").textContent).toContain("Jump");
  expect(view.container.querySelector(".input-inspector-read .is-error").textContent).toBe("No capture on this frame");
  fireEvent.click(view.getByRole("button", {name:"Reset template shift"}));
  await waitFor(() => expect(view.getByLabelText("Template offset").textContent).toBe("0f"));
  expect(view.container.querySelector(".action-span.is-template")).toBeNull();
});

test("template shifts use their source revision and controls wait for saved review state", async () => {
  const view = await timeline({map:[100,101,102],times:[0,.1,.2],
    review:{template_offsets:{"7:older":9,"7:abc123":1}}, data:{template:exampleTemplate()}});
  expect(view.getByLabelText("Template offset").textContent).toBe("+1f");
  view.unmount();
  const pending = await timeline({map:[100,101,102],times:[0,.1,.2],
    reviewLoading:true,data:{template:exampleTemplate()}});
  expect(pending.getByRole("button", {name:"Shift template later one frame"}).disabled).toBe(true);
  expect(pending.getByRole("button", {name:"Zoom in timeline"}).disabled).toBe(true);
});

test("the shift handle captures its own drag and keyboard gestures without seeking", async () => {
  const view = await timeline({map:[100,101,102],times:[0,.1,.2],review:{},
    data:{template:exampleTemplate()}});
  view.container.querySelector(".input-track-column").getBoundingClientRect = () => ({left:0,width:300});
  const handle = view.getByRole("button", {name:"Shift template"});
  handle.setPointerCapture = vi.fn();
  handle.hasPointerCapture = () => true;
  handle.releasePointerCapture = vi.fn();
  const pointer = (type, x) => {
    const event = new Event(type, {bubbles:true,cancelable:true});
    Object.assign(event, {clientX:x,button:0,pointerId:4});
    fireEvent(handle, event);
  };
  const before = view.video.currentTime;
  pointer("pointerdown", 20);
  pointer("pointermove", 220);
  pointer("pointerup", 220);
  await waitFor(() => expect(view.getByLabelText("Template offset").textContent).toBe("+2f"));
  expect(handle.setPointerCapture).toHaveBeenCalledWith(4);
  expect(handle.releasePointerCapture).toHaveBeenCalledWith(4);
  fireEvent.keyDown(handle, {key:"ArrowLeft"});
  await waitFor(() => expect(view.getByLabelText("Template offset").textContent).toBe("+1f"));
  expect(view.video.currentTime).toBe(before);
});

test("zoom follows the presented frame and scrubbing uses the cropped axis", async () => {
  const view = await timeline({map:[100,101,102,103,104,105,106,107],
    times:[0,.1,.2,.3,.4,.5,.6,.7],stretches:[[0,100,8]], data:{frames:8},review:{}});
  await view.present(6);
  Object.assign(view.video, {currentTime: .02}); // A requested seek is not the delivered picture.
  fireEvent.click(view.getByRole("button", {name:"Zoom in timeline"}));
  await waitFor(() => expect(view.container.querySelector(".input-zoom-range").textContent).toBe("Frames 4–7"));
  expect(view.container.querySelector(".is-stick svg").getAttribute("viewBox")).toBe("4 0 4 46");
  view.container.querySelector(".input-track-column").getBoundingClientRect = () => ({left:100,width:400});
  clickTimeline(view, 300);
  expect(view.video.currentTime).toBeGreaterThan(.6);
  expect(view.video.currentTime).toBeLessThan(.7);
  fireEvent.click(view.getByRole("button", {name:"Fit"}));
  await waitFor(() => expect(view.container.querySelector(".input-zoom-range").textContent).toBe("Frames 0–7"));
});

test("loop zoom excludes B's next picture and refuses an ambiguous reset", () => {
  const times = [0,2,2 + 1/90000,2.1];
  expect(mappedLoopWindow({start:2,end:times[2]}, [100,100,101,102],
    {times,duration:2.2}, [[0,100,3]], 3)).toEqual({start:0,end:1});
  expect(mappedLoopWindow({start:.3,end:.5}, [100,101,99,100,101],
    {times:[0,.1,.2,.3,.4],duration:.5}, [[0,100,3]], 3)).toBeNull();
});

test("clicking cropped input and action spans stays inside the zoomed view", async () => {
  const action = {start:0,length:8,label:"Walking",group:"moving"};
  const view = await timeline({map:[100,101,102,103,104,105,106,107],
    times:[0,.1,.2,.3,.4,.5,.6,.7],stretches:[[0,100,8]],review:{},
    data:{frames:8,actions:[action],
      runs:[{start:0,length:8,buttons:32768,stick_x:40,stick_y:20,yaw:0,speed:4}],
      template:{id:7,name:"Full action",frames:8,runs:[],actions:[],
        source:{revision:"abc123",frames:8,runs:[],actions:[action]}}}});
  await view.present(6);
  fireEvent.click(view.getByRole("button", {name:"Zoom in timeline"}));
  await waitFor(() => expect(view.container.querySelector(".input-zoom-range").textContent).toBe("Frames 4–7"));
  const spans = view.container.querySelectorAll("button.input-bar, .action-span");
  expect(spans.length).toBe(3);
  for (const span of spans) {
    view.video.currentTime = .75;
    fireEvent.click(span);
    expect(view.video.currentTime).toBeGreaterThan(.4);
    expect(view.video.currentTime).toBeLessThan(.5);
  }
});

test("the loop is a fixed input range while the template moves, and the zoom button opens it", async () => {
  const view = await timeline({map:[100,101,102,103,104],times:[0,.1,.2,.3,.4],
    stretches:[[0,100,5]],data:{frames:5,template:exampleTemplate()},
    review:{loop:{start:.1,end:.3,enabled:true}}});
  const shade = view.container.querySelector(".input-loop-shade");
  expect(shade.style.left).toBe("20%");
  expect(shade.style.width).toBe("40%");
  fireEvent.click(view.getByRole("button", {name:"Shift template later one frame"}));
  await waitFor(() => expect(view.getByLabelText("Template offset").textContent).toBe("+1f"));
  expect(shade.style.left).toBe("20%");
  expect(shade.style.width).toBe("40%");
  fireEvent.click(view.getByRole("button", {name:"Zoom to loop"}));
  await waitFor(() => expect(view.container.querySelector(".input-zoom-range").textContent).toBe("Frames 1–2"));
  view.container.querySelector(".input-track-column").getBoundingClientRect = () => ({left:0,width:200});
  clickTimeline(view, 200);
  expect(view.video.currentTime).toBeGreaterThan(.2);
  expect(view.video.currentTime).toBeLessThan(.3);
});

test("legacy template data remains visible but never writes a revisionless persisted shift", async () => {
  const template = exampleTemplate();
  delete template.source;
  const view = await timeline({map:[100,101,102],times:[0,.1,.2],review:{},data:{template}});
  expect(view.container.querySelector(".input-bar.is-template")).not.toBeNull();
  expect(view.getByRole("button", {name:"Shift template later one frame"}).disabled).toBe(true);
  expect(view.getByText("Alignment unavailable for this template.")).not.toBeNull();
});

test("presented pictures do not traverse every run to rebuild static curves", async () => {
  const readX = vi.fn(() => 40);
  const runs = Array.from({length:1000}, (_, start) => ({start,length:1,buttons:32768,
    get stick_x() { return readX(); }, stick_y:20,yaw:0,speed:4}));
  const view = await timeline({map:[100,101,102],times:[0,.1,.2],
    stretches:[[0,100,1000]],data:{frames:1000,runs}});
  expect(readX.mock.calls.length).toBeGreaterThan(1000);
  readX.mockClear();
  await view.present(1);
  await view.present(2);
  expect(readX.mock.calls.length).toBeLessThan(100);
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
