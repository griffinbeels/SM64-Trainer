// @vitest-environment jsdom
// Record in the Scene viewer (ui/components/scenerecord.js, M4 round 6, item 3): the timeline the page
// keeps while Record is on, and the Record row's flow from Record to "Show in folder". The link is
// a stand-in with SceneLink's fields; animation frames, fetch and the poll's wait are the test's.
import { h } from "preact";
import { render, cleanup, fireEvent, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { RECORDING, RecordBar, SceneRecorder, cameraUsed, playPosition } from "../../src/sm64_events/ui/components/scenerecord.js";
import { SceneLink } from "../../src/sm64_events/ui/components/sceneviewer.js";
import { startRig } from "../../src/sm64_events/ui/components/scenerigs.js";
import { sceneCameraTuning } from "../../src/sm64_events/ui/scenecameratuning.js";

const POSE = { focus: [1, 2, 3], position: [4, 5, 6], roll: 0, fov: 45 };
let frames, fetches, statuses;

function fakeLink(fields = {}) {
  return { scene: { session: 4 }, frame: 3, playback: null, hud: true, ghosts: [{ ref: "a", label: "YOUR PB" }],
    sound: { music: true, effects: true, volume: 0.5, muted: false, ghostMutes: new Set() }, shown: null,
    camera: null, cameraNow() { return this.camera; }, ...fields };
}

// Runs the waiting animation frame at `time`.
function tick(time) {
  const step = frames.shift();
  step?.(time);
}

beforeEach(() => {
  frames = [];
  fetches = [];
  statuses = [];
  RECORDING.pollMs = 0;
  globalThis.fetch = vi.fn((url, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : null;
    fetches.push({ url, method: options.method || "GET", body });
    const answer = url === "/api/local-access" ? { request_token: "local" }
      : url === "/api/scene-lab/export" ? { job: "j1" }
      : url === "/api/scene-lab/export/j1" ? statuses.shift() ?? { state: "running", progress: 0.5, message: "Drawing..." }
        : url === "/api/scene-lab/export/j1/cancel" ? { state: "running" } : { ok: true };
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(answer) });
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  RECORDING.pollMs = 300;
  RECORDING.maxMs = 30 * 60 * 1000;
});

test("a paused frame, then a play: each display frame keeps the frame, camera and sound then", () => {
  const link = fakeLink();
  const recorder = new SceneRecorder({ animate: (step) => frames.push(step) });
  recorder.start(link);
  tick(1000);
  link.camera = POSE;                              // the free camera moved during the pause
  tick(1016);
  const times = [0, 33.3, 66.7, 100, 133.3];
  // Play from picture 1: its sound starts at 1040 (the lead), and a play's picture shows on the
  // display frame it is due, the one after the frame that drew it (16 ms here).
  Object.assign(link, { frame: 1, camera: null, playback: { from: 1, start: 1040, times, length: 166.6, frameMs: 16 } });
  link.sound.ghostMutes.add("a");
  tick(1020);                                      // before the play's sound starts: its first picture, silent
  tick(1048);                                      // 24 ms into the play when shown: still picture 1
  tick(1080);                                      // 56 ms: picture 2
  const timeline = recorder.entries;
  expect(timeline.map((entry) => entry.t_ms)).toEqual([0, 16, 20, 48, 80]);
  expect(timeline.map((entry) => entry.scene_frame)).toEqual([3, 3, 1, 1, 2]);
  expect(timeline.slice(0, 3).map((entry) => entry.play_ms)).toEqual([null, null, null]);
  expect(timeline[3].play_ms).toBeCloseTo(33.3 + 24);
  expect(timeline[4].play_ms).toBeCloseTo(33.3 + 56);
  expect(timeline[1].camera).toEqual(POSE);
  expect(timeline[0].sound).toEqual({ music: true, effects: true, volume: 0.5, muted: false, ghost_mutes: [] });
  expect(timeline[3].sound.ghost_mutes).toEqual(["a"]);
  // An array that did not change is the entry before's: a long recording keeps one copy.
  expect(timeline[1].ghosts).toBe(timeline[0].ghosts);
  expect(timeline[4].sound.ghost_mutes).toBe(timeline[3].sound.ghost_mutes);
  const recorded = recorder.stop(1100);
  expect(recorded).toEqual({ session: 4, timeline, duration_ms: 100 });
  tick(1200);
  expect(recorder.entries.length).toBe(5);         // stopped: no more entries
});

test("a play's position: none before its sound starts or after its last picture", () => {
  const play = { from: 0, start: 100, times: [0, 40, 80], length: 113.3, frameMs: 10 };
  expect(playPosition(play, 80)).toBe(null);
  expect(playPosition(play, 90)).toEqual({ frame: 0, playMs: 0 });
  expect(playPosition(play, 135)).toEqual({ frame: 1, playMs: 45 });
  expect(playPosition(play, 204)).toBe(null);
  expect(playPosition(null, 100)).toBe(null);
});

test("camera_used is the server's camera only for the picture that answers this frame and camera", () => {
  const rig = { rig: { kind: "orbit", target: "stage" } };
  expect(cameraUsed({ frame: 2, sentCamera: rig, camera_used: POSE }, 2, rig)).toEqual(POSE);
  expect(cameraUsed({ frame: 2, sentCamera: rig, camera_used: POSE }, 3, rig)).toBe(null);       // another frame
  expect(cameraUsed({ frame: 2, sentCamera: null, camera: "recorded" }, 2, rig)).toBe(null);      // another camera
  expect(cameraUsed({ frame: 2, sentCamera: { ghost: "a" }, camera: "free", view: { ghost: "a", camera: POSE } },
    2, { ghost: "a" })).toEqual(POSE);
  expect(cameraUsed({ frame: 2, sentCamera: POSE, camera: "free" }, 2, { ...POSE })).toEqual(POSE);
  expect(cameraUsed({ frame: 2, sentCamera: null, camera: "recorded" }, 2, null)).toBe(null);
});

test("a scene that closes ends the recording with nothing to make; the longest makes itself", () => {
  const link = fakeLink();
  const recorder = new SceneRecorder({ animate: (step) => frames.push(step) });
  recorder.start(link);
  tick(0);
  link.scene = { session: 5 };
  tick(16);
  expect(recorder.on).toBe(false);
  expect(recorder.stoppedBy).toBe("closed");
  expect(recorder.stop()).toBe(null);
  RECORDING.maxMs = 30;
  const long = new SceneRecorder({ animate: (step) => frames.push(step) });
  const made = [];
  long.reachedLongest = (recorded) => made.push(recorded);
  long.start(fakeLink());
  [100, 116, 132].forEach(tick);
  expect(long.on).toBe(false);
  expect(made.length).toBe(1);
  expect(made[0].timeline.length).toBe(3);
});

test("Record, Stop, the video's progress, then Show in folder opens Explorer at the file", async () => {
  const link = fakeLink();
  const recorder = new SceneRecorder({ animate: (step) => frames.push(step) });
  const page = render(h(RecordBar, { link, recorder }));
  fireEvent.click(page.getByText("Record"));
  tick(500);
  tick(516);
  await waitFor(() => page.getByText(/Recording 0:00/));
  statuses.push({ state: "running", progress: 0.4, message: "Drawing picture 4 of 10..." },
    { state: "done", progress: 1, message: "Saved Bob - Star 2026-10-27 14.05.33.mp4.",
      path: "C:\\data\\replays\\scene clips\\Bob - Star 2026-10-27 14.05.33.mp4" });
  fireEvent.click(page.getByText("Stop"));
  await waitFor(() => page.getByText("Show in folder"));
  const posted = fetches.find((call) => call.url === "/api/scene-lab/export");
  expect(posted.method).toBe("POST");
  expect(posted.body.fps).toBe(60);
  expect(posted.body.version).toBe(1);                 // the recording's format, which a reader checks
  expect(posted.body.session).toBe(4);
  expect(posted.body.timeline.map((entry) => entry.t_ms)).toEqual([0, 16]);
  page.getByText(/Saved Bob - Star 2026-10-27 14.05.33.mp4/);
  fireEvent.click(page.getByText("Show in folder"));
  await waitFor(() => expect(fetches.some((call) => call.url === "/api/replay/reveal")).toBe(true));
  expect(fetches.find((call) => call.url === "/api/replay/reveal").body).toEqual(
    { path: "C:\\data\\replays\\scene clips\\Bob - Star 2026-10-27 14.05.33.mp4" });
});

test("Cancel asks the lab to stop, and a failure says the lab's own sentence", async () => {
  const link = fakeLink();
  const recorder = new SceneRecorder({ animate: (step) => frames.push(step) });
  const page = render(h(RecordBar, { link, recorder }));
  fireEvent.click(page.getByText("Record"));
  tick(0);
  RECORDING.pollMs = 20;
  fireEvent.click(page.getByText("Stop"));
  await waitFor(() => page.getByText("Cancel"));
  expect(page.getByText("Record").closest("button").disabled).toBe(true);    // one video at a time
  fireEvent.click(page.getByText("Cancel"));
  await waitFor(() => expect(fetches.some((call) => call.url === "/api/scene-lab/export/j1/cancel"
    && call.method === "POST")).toBe(true));
  statuses.push({ state: "error", progress: 0.4, message: "The video encoder failed: no space left." });
  await waitFor(() => page.getByText("The video encoder failed: no space left."));
});

test("Pause holds the play's clock: Play on that picture goes on from there, a step starts it over", () => {
  let now = 1000;
  vi.spyOn(performance, "now").mockImplementation(() => now);
  const animation = globalThis.requestAnimationFrame;
  globalThis.requestAnimationFrame = () => 0;
  try {
    const link = new SceneLink({ draw: () => 0, changed: () => {}, ended: () => {} });
    link.scene = { session: 7, fps: 30, frames: Array.from({ length: 10 }, (_, k) => ({ recorded_ms: k * 40 })) };
    link.frame = 2;
    link.play();
    expect(link.playback.start).toBe(1000);
    now = 1025;                                    // 25 ms into picture 2's 40
    link.pause();
    expect(link.pausedOn).toEqual({ session: 7, frame: 2, ms: 25 });
    now = 5000;
    link.play();                                   // the same picture: on from 25 ms, never its first 25 again
    expect(link.playback.resumeMs).toBe(25);
    expect(link.playback.start).toBe(4975);
    expect(playPosition(link.playback, 4980)).toBe(null);           // what was heard before Pause stays silent
    expect(playPosition(link.playback, 4990)).toEqual({ frame: 2, playMs: 80 + 15 + 1000 / 60 });
    now = 5010;
    link.pause();
    expect(link.pausedOn).toEqual({ session: 7, frame: 2, ms: 35 });
    link.step(1);                                  // another picture: Play starts it over
    expect(link.pausedOn).toBe(null);
    link.play();
    expect(link.playback.resumeMs).toBe(0);
    expect(link.playback.start).toBe(5010);
  } finally {
    globalThis.requestAnimationFrame = animation;
  }
});

test("a preset cut while recording is in the timeline: the camera the page asks for from then on", () => {
  const animation = globalThis.requestAnimationFrame;
  globalThis.requestAnimationFrame = () => 0;
  try {
    const link = new SceneLink({ draw: () => 0, changed: () => {}, ended: () => {} });
    link.scene = { session: 3, fps: 30, free_camera: { available: true },
      frames: Array.from({ length: 4 }, (_, k) => ({ recorded_ms: k * 33.3 })) };
    const recorder = new SceneRecorder({ animate: (step) => frames.push(step) });
    recorder.start(link);
    tick(0);
    link.cutTo(POSE, 10);                          // key 3, say: a saved camera, cut to as a free camera
    tick(16);
    const [before, after] = recorder.entries;
    expect(before.camera).toBe(null);
    expect(after.camera).toEqual(link.cameraNow());
    expect(after.camera.focus.map((value) => Math.round(value * 1000) / 1000)).toEqual(POSE.focus);
    expect(after.camera.position.map((value) => Math.round(value * 1000) / 1000)).toEqual(POSE.position);
    expect(after.camera.fov).toBeCloseTo(POSE.fov);
  } finally {
    globalThis.requestAnimationFrame = animation;
  }
});

// Round 7, item 2: "this allows us to take a freeze frame and grab an orbital loop around mario while frozen".
test("an orbit circling a paused picture records that picture with the orbit's clock each display frame, silent", () => {
  let now = 1000;
  vi.spyOn(performance, "now").mockImplementation(() => now);
  const animation = globalThis.requestAnimationFrame;
  globalThis.requestAnimationFrame = () => 0;
  try {
    const orbiting = [];
    const link = new SceneLink({ draw: () => 0, changed: () => {}, ended: () => {}, animate: (step) => orbiting.push(step) });
    link.scene = { session: 5, fps: 30, free_camera: { available: true },
      frames: Array.from({ length: 6 }, (_, k) => ({ recorded_ms: k * 40 })) };
    link.ws = { readyState: WebSocket.OPEN, send: () => {} };
    link.frame = 2;
    link.rig = link.lastRig = startRig("orbit", null, sceneCameraTuning());
    link.shown = { frame: 2, camera: "free", rig: { kind: "orbit" }, camera_used: POSE };     // the lab drew the orbit
    link.holdOrbit();
    const recorder = new SceneRecorder({ animate: (step) => frames.push(step) });
    recorder.start(link);
    for (const at of [1000, 1016.7, 1033.4, 1050.1]) {
      now = at;
      tick(at);
    }
    const held = recorder.entries;
    expect(held.map((entry) => entry.scene_frame)).toEqual([2, 2, 2, 2]);
    expect(held.map((entry) => entry.play_ms)).toEqual([null, null, null, null]);             // a held picture is silent
    held.forEach((entry, k) => expect(entry.camera.rig.clock_ms).toBeCloseTo(80 + [0, 16.7, 33.4, 50.1][k], 6));
    expect(new Set(held.map((entry) => entry.camera)).size).toBe(4);                      // a new camera every frame

    // Play: the clock goes into the phase, so the next entry asks without one from the same angle.
    now = 1060;
    link.play();
    expect(link.cameraNow().rig.clock_ms).toBeUndefined();
    const angle = (rig, ms) => (((rig.phase + rig.speed * ms / 1000) % 360) + 360) % 360;
    expect(angle(link.cameraNow().rig, 80)).toBeCloseTo(angle(held[0].camera.rig, 80 + 60), 6);
  } finally {
    globalThis.requestAnimationFrame = animation;
  }
});
