// @vitest-environment jsdom
// The Scene viewer page (ui/components/sceneviewer.js) against a scripted
// server: what it asks for, when, and what it shows. No renderer, GPU or
// browser: the socket, fetch, canvas, pointer lock and animation frames are
// stand-ins. The camera's math is components/scenecamera.js; the numbers it
// feels with are pinned to REFERENCE below, never to the shipped defaults
// (the Tune controls panel rewrites those).
import { h } from "preact";
import { render, cleanup, fireEvent, waitFor, within } from "@testing-library/preact";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { DRAG_DEAD_ZONE, HUD_PREFERENCE, LOCK_NOTES, LOOP_PREFERENCE, MOVING_WINDOW, PICTURES_IN_FLIGHT, SHARED_WAIT, STILL_MS,
  STAGE_WORDS, SceneViewer, hintFor, movingSummary, pictureFraction, sceneTitle, speedWords, throttledNotes,
} from "../../src/sm64_events/ui/components/sceneviewer.js";
import { cameraOf, dollied, easeInOut, flightSpeed, flown, flyToTarget, glideAt, lookingAt, looked, orbitFromPose, slid,
  speedStepped, turned, worldPoint } from "../../src/sm64_events/ui/components/scenecamera.js";
import { SCENE_CAMERA_DEFAULTS, setSceneCameraTuning } from "../../src/sm64_events/ui/scenecameratuning.js";
import { stageDefault, targetQuery } from "../../src/sm64_events/ui/components/scenestage.js";
import { SEARCH_WAIT } from "../../src/sm64_events/ui/components/sceneghosts.js";

const REFERENCE = { orbitPerPixel: 0.01, steepestDegrees: 80, levelPerPixel: 0.1, panPerPixel: 0.002,
  dollyPerWheel: 0.002, closestDistance: 40, farthestDistance: 20000, flightSpeed: 1000, lookPerPixel: 0.004,
  fastMultiplier: 5, slowMultiplier: 0.2, speedPerNotch: 1.5, levelPerSecond: 30, flyToStandoff: 300, flyToMs: 200,
  focusDistance: 600, marioHeight: 100 };
const FRAMES = Array.from({ length: 5 }, (_, i) => ({ level: 9, area: 1, game_frame: 1000 + i }));
const POSE = { focus: [10, 200, -30], position: [10, 500, 1170], roll: 0, fov: 45, mario: [40, 80, -60] };
const PER_DEGREE = 65536 / 360;
let sockets, opened, fetches, frames, locked, lockRequests;
// A test's own answers by URL prefix, asked before the scripted server's (the selector and ghost tests).
let routes;
// The page's clock is the test's: performance.now() reads `clock.now`, which only tick() (an
// animation frame) and the test itself move, as one clock serves both in a browser. A step's
// time is then the test's and never the machine's, so a loaded machine cannot change a result.
const clock = { now: 1000 };

class FakeSocket {
  static OPEN = 1;
  constructor(url) { this.url = url; this.sent = []; this.readyState = 0; sockets.push(this); }
  send(text) { this.sent.push(JSON.parse(text)); }
  close() { this.readyState = 3; }
  open() { this.readyState = 1; this.onopen?.(); }
  picture(request, extra = {}) {
    const header = new TextEncoder().encode(JSON.stringify({ id: request.id, session: request.session, frame: request.frame,
      width: 4, height: 3, encoding: "raw", camera: request.camera ? "free" : "recorded", reason: null,
      timings: {}, server_ms: 3, ...extra }));
    const buffer = new ArrayBuffer(4 + header.length + 36);
    new DataView(buffer).setUint32(0, header.length, true);
    new Uint8Array(buffer, 4).set(header);
    return this.onmessage({ data: buffer });
  }
  text(message) { this.onmessage({ data: JSON.stringify(message) }); }
  last() { return this.sent[this.sent.length - 1]; }
  pictures() { return this.sent.filter((message) => !message.kind); }
}

function respond(body, status = 200) {
  return Promise.resolve({ ok: status < 300, status, json: () => Promise.resolve(body) });
}

beforeEach(() => {
  localStorage.removeItem(LOOP_PREFERENCE);
  localStorage.removeItem(HUD_PREFERENCE);
  clock.now = 1000;
  vi.spyOn(performance, "now").mockImplementation(() => clock.now);
  sockets = [];
  fetches = [];
  frames = [];
  locked = null;
  lockRequests = null;
  setSceneCameraTuning(REFERENCE);
  opened = { session: 1, name: "bob-minute.sm64scene", fps: 30, frames: FRAMES, renderer_pid: 7,
    free_camera: { available: true, reason: null } };
  globalThis.WebSocket = FakeSocket;
  globalThis.requestAnimationFrame = (step) => { frames.push(step); return frames.length; };
  routes = new Map();
  globalThis.fetch = vi.fn((url, options = {}) => {
    fetches.push([url, options.method || "GET", options.body]);
    for (const [prefix, answer] of routes) if (url.startsWith(prefix)) return answer(url, options);
    if (url === "/api/local-access") return respond({}, 404);
    if (url === "/api/scene-lab/status") return respond({ ready: true, problems: [], hold: null, session: null,
      ended: null, packages: [{ name: "bob-minute.sm64scene", bytes: 11 << 20 }] });
    if (url === "/api/scene-lab/open") return respond(opened);
    if (url === "/api/scene-lab/close") return respond({ closed: true });
    if (url === "/api/tuning/scenecamera") return respond({ written: Object.keys(JSON.parse(options.body).values).length });
    return respond({}, 404);
  });
  HTMLCanvasElement.prototype.getContext = () => ({
    createImageData: (w, hgt) => ({ data: new Uint8ClampedArray(w * hgt * 4), width: w, height: hgt }),
    putImageData: () => {}, drawImage: () => {},
  });
  HTMLCanvasElement.prototype.getBoundingClientRect = () => ({ left: 0, top: 0, width: 640, height: 480 });
  HTMLCanvasElement.prototype.setPointerCapture = vi.fn();
  HTMLCanvasElement.prototype.releasePointerCapture = vi.fn();
  // Granted at once, as a quick browser does; a test that sets lockRequests = [] grants each
  // request itself, late: the lock lands (pointerlockchange), then the request's promise resolves.
  HTMLCanvasElement.prototype.requestPointerLock = vi.fn(function lock() {
    const grant = () => { locked = this; document.dispatchEvent(new Event("pointerlockchange")); };
    if (!lockRequests) { grant(); return Promise.resolve(); }
    return new Promise((resolve) => { lockRequests.push(() => { grant(); resolve(); }); });
  });
  Object.defineProperty(document, "pointerLockElement", { configurable: true, get: () => locked });
  document.exitPointerLock = vi.fn(() => { locked = null; document.dispatchEvent(new Event("pointerlockchange")); });
});
afterEach(() => { cleanup(); setSceneCameraTuning(null); vi.restoreAllMocks(); SEARCH_WAIT.ms = 250; });

// Turns of the event loop until `check` passes, then its value: like waitFor, with no deadline
// on the wall clock, so a busy machine only makes it slower, never wrong.
async function until(check, turns = 200) {
  for (let turn = 0; ; turn++) {
    try {
      return check();
    } catch (error) {
      if (turn >= turns) throw error;
    }
    await new Promise((resolve) => { setTimeout(resolve, 0); });
  }
}

// A few turns of the event loop: whatever the page would do by itself has happened.
const settled = async (turns = 5) => {
  for (let turn = 0; turn < turns; turn++) await new Promise((resolve) => { setTimeout(resolve, 0); });
};

async function openScene() {
  const page = render(h(SceneViewer, {}));
  const button = await until(() => {
    const found = page.getByRole("button", { name: "Open" });
    expect(found.disabled).toBe(false);
    return found;
  });
  fireEvent.click(button);
  await until(() => expect(sockets.length).toBe(1));
  const socket = sockets[0];
  socket.open();
  await until(() => expect(socket.sent.length).toBe(1));
  const root = page.container.querySelector(".scene-lab");
  return { page, socket, root, canvas: root.querySelector("canvas") };
}

// Free camera from `pose`: answers the pose question and the first picture.
async function enterFree(page, socket, pose = POSE) {
  await socket.picture(socket.sent[0]);
  fireEvent.click(page.getByRole("button", { name: "Free camera" }));
  await until(() => expect(socket.last()).toMatchObject({ kind: "pose" }));
  socket.text({ id: socket.last().id, kind: "pose", pose, reason: null });
  await until(() => expect(socket.last().camera).toBeTruthy());
  await socket.picture(socket.last());
  return socket.last().camera;
}

// The page follows the pointer on pointerrawupdate where the browser has it (jsdom does).
const move = (canvas, init) => fireEvent(canvas, new PointerEvent("pointerrawupdate", { bubbles: true, ...init }));
const tick = async (at) => {
  clock.now = at;
  const due = frames.splice(0);
  due.forEach((step) => step(at));
  await new Promise((resolve) => { setTimeout(resolve, 0); });
};
const close = (a, b, digits = 6) => a.forEach((value, i) => expect(value).toBeCloseTo(b[i], digits));

// ------------------------------------------------------------------ requests

test("opening names a listed package, then asks for the first picture from the recorded camera", async () => {
  const { socket } = await openScene();
  const open = fetches.find(([url]) => url === "/api/scene-lab/open");
  expect(open[1]).toBe("POST");
  expect(JSON.parse(open[2])).toEqual({ package: "bob-minute.sm64scene" });
  expect(socket.url).toMatch(/\/api\/scene-lab\/ws$/);
  expect(socket.sent[0]).toMatchObject({ session: 1, frame: 0, camera: null, encoding: "jpeg" });
  expect(HTMLCanvasElement.prototype.requestPointerLock).not.toHaveBeenCalled();     // opening captures nothing
  expect(HTMLCanvasElement.prototype.setPointerCapture).not.toHaveBeenCalled();
});

// The Practice log's Ghost button (app.js hands its attempt over as `t.sceneLab.intent`).
const fromGhostButton = (attempt, clearIntent = () => {}) => ({ t: { sceneLab: { intent: { attempt }, clearIntent } } });

async function openAttempt(attempt = 12) {
  const clearIntent = vi.fn();
  const page = render(h(SceneViewer, fromGhostButton(attempt, clearIntent)));
  await until(() => expect(sockets.length).toBe(1));
  const socket = sockets[0];
  socket.open();
  await until(() => expect(socket.sent.length).toBe(1));
  return { page, socket, clearIntent };
}

test("an attempt from the Practice log opens and plays from its first frame once that picture is drawn", async () => {
  const { page, socket, clearIntent } = await openAttempt(12);
  expect(clearIntent).toHaveBeenCalledTimes(1);
  const asked = fetches.filter(([url]) => url.startsWith("/api/scene-lab/")).map(([url, method, body]) => [url, method, body]);
  expect(asked[0]).toEqual(["/api/scene-lab/open", "POST", JSON.stringify({ attempt: 12 })]);  // never the old scene first
  expect(socket.sent[0]).toMatchObject({ frame: 0, camera: null });
  expect(page.getByRole("button", { name: "Play" })).toBeTruthy();     // nothing plays before a picture is up
  await socket.picture(socket.sent[0]);
  await until(() => expect(page.getByRole("button", { name: "Pause" })).toBeTruthy());
  expect(socket.pictures().map((request) => request.frame)).toEqual([0, 0]);   // the play's own first picture
  fireEvent.click(page.getByRole("button", { name: "Pause" }));
});

test("a scene's slow first picture does not make Play jump past the attempt's opening frames", async () => {
  const { page, socket } = await openAttempt(12);
  clock.now += 300;                                   // the session's cold start, not a picture's usual time
  await socket.picture(socket.sent[0]);
  await until(() => expect(page.getByRole("button", { name: "Pause" })).toBeTruthy());
  await tick(clock.now);
  await until(() => expect(socket.last().frame).toBe(1));   // asked READY_GUESS ahead: frame 1, not the last
  fireEvent.click(page.getByRole("button", { name: "Pause" }));
});

test("stepping before the first picture keeps an attempt's scene paused where the step put it", async () => {
  const { page, socket } = await openAttempt(12);
  fireEvent.keyDown(page.container.querySelector(".scene-lab"), { key: "ArrowRight" });
  await socket.picture(socket.sent[0]);
  await settled();
  expect(page.getByRole("button", { name: "Play" })).toBeTruthy();
  expect(socket.pictures().map((request) => request.frame)).toEqual([0, 1]);
});

test("an attempt the lab refuses shows the server's reason and plays nothing", async () => {
  const refusal = "This attempt was played before capture started, so it has no scene.";
  const answer = globalThis.fetch;
  globalThis.fetch = vi.fn((url, options = {}) => (url === "/api/scene-lab/open"
    ? (fetches.push([url, options.method, options.body]), respond({ detail: refusal }, 409)) : answer(url, options)));
  const page = render(h(SceneViewer, fromGhostButton(99)));
  await until(() => expect(page.container.querySelector(".scene-lab-error")?.textContent).toBe(refusal));
  await settled();
  expect(sockets.length).toBe(0);
  expect(page.getByRole("button", { name: "Open" })).toBeTruthy();       // the packages are still there to pick
});

test("two pictures are in flight at a time and the newest step waiting behind them wins", async () => {
  expect(PICTURES_IN_FLIGHT).toBe(2);
  const { page, socket, root } = await openScene();
  fireEvent.keyDown(root, { key: "ArrowRight" });
  await waitFor(() => expect(socket.sent.length).toBe(2));
  for (let i = 0; i < 2; i++) fireEvent.keyDown(root, { key: "ArrowRight" });
  await new Promise((resolve) => { setTimeout(resolve, 20); });
  expect(socket.sent.map((message) => message.frame)).toEqual([0, 1]);   // frame 1 went out beside frame 0
  await socket.picture(socket.sent[0]);
  await waitFor(() => expect(socket.sent.length).toBe(3));
  expect(socket.sent[2].frame).toBe(3);                // frame 2 waited, and frame 3 replaced it
  await new Promise((resolve) => { setTimeout(resolve, 20); });
  expect(socket.sent.length).toBe(3);                  // nothing else is waiting
  await socket.picture(socket.sent[1]);
  await socket.picture(socket.sent[2]);
  await waitFor(() => expect(page.container.textContent).toContain("Frame 4 of 5"));
  expect(page.container.textContent).toContain("level 9, area 1, game frame 1003");
  fireEvent.keyDown(root, { key: "End" });
  await waitFor(() => expect(socket.sent.length).toBe(4));
  expect(socket.sent[3].frame).toBe(4);                // stepping stops at the last frame
  expect(page.container.querySelector(".scene-lab-busy")).not.toBeNull();
  await socket.picture(socket.sent[3]);
  await waitFor(() => expect(page.container.querySelector(".scene-lab-busy")).toBeNull());
});

// JPEG pictures whose decodes finish when the test says, drawn onto a canvas that remembers them.
function heldDecodes() {
  const decodes = [], drawn = [];
  globalThis.createImageBitmap = vi.fn((blob) => new Promise((resolve) => {
    decodes.push(() => resolve({ blob, close: () => {} }));
  }));
  HTMLCanvasElement.prototype.getContext = () => ({ drawImage: (bitmap) => drawn.push(bitmap.blob) });
  return { decodes, drawn };
}

test("the next picture is asked for while one decodes, and a late decode never covers a newer picture", async () => {
  const { decodes, drawn } = heldDecodes();
  const { page, socket, root } = await openScene();
  fireEvent.keyDown(root, { key: "ArrowRight" });                  // out beside picture 0
  await waitFor(() => expect(socket.sent.length).toBe(2));
  fireEvent.keyDown(root, { key: "ArrowRight" });                  // waits behind both
  await new Promise((resolve) => { setTimeout(resolve, 20); });
  expect(socket.sent.length).toBe(2);
  socket.picture(socket.sent[0], { encoding: "jpeg" });             // arrives; its decode waits
  await waitFor(() => expect(socket.sent.length).toBe(3));         // asked for before picture 0 drew
  expect(decodes.length).toBe(1);
  expect(drawn).toEqual([]);
  socket.picture(socket.sent[1], { encoding: "jpeg" });            // decode 1 waits too
  await waitFor(() => expect(decodes.length).toBe(2));
  decodes[1]();                                                    // the newer one finishes first
  await waitFor(() => expect(drawn.length).toBe(1));
  decodes[0]();
  await new Promise((resolve) => { setTimeout(resolve, 20); });
  expect(drawn.length).toBe(1);                                    // picture 0 never covered picture 1
  expect(createImageBitmap.mock.calls[1][0]).toBe(drawn[0]);
  await waitFor(() => expect(page.container.textContent).toContain("Frame 3 of 5"));
  expect(page.container.querySelector(".scene-lab-readout").textContent).toContain("input to picture");
});

test("pictures whose decodes finish in order are all drawn, oldest first", async () => {
  const { decodes, drawn } = heldDecodes();
  const { socket, root } = await openScene();
  fireEvent.keyDown(root, { key: "ArrowRight" });
  await waitFor(() => expect(socket.sent.length).toBe(2));
  socket.picture(socket.sent[0], { encoding: "jpeg" });
  socket.picture(socket.sent[1], { encoding: "jpeg" });            // arrives while picture 0 decodes
  await waitFor(() => expect(decodes.length).toBe(2));
  decodes[0]();
  await waitFor(() => expect(drawn.length).toBe(1));
  decodes[1]();
  await waitFor(() => expect(drawn.length).toBe(2));
  expect(drawn).toEqual(createImageBitmap.mock.calls.map(([blob]) => blob));   // 0, then 1 over it
});

// ------------------------------------------------------------------ Play on time

// Opens a scene whose first picture took 40 ms from asked to drawn, so a play asks 40 ms plus a
// display frame ahead; `drawn` gets the page's clock at each draw.
async function openTimed() {
  const drawn = [];
  HTMLCanvasElement.prototype.getContext = () => ({ putImageData: () => drawn.push(clock.now),
    createImageData: (w, hgt) => ({ data: new Uint8ClampedArray(w * hgt * 4), width: w, height: hgt }) });
  const opened = await openScene();
  clock.now += 40;
  await opened.socket.picture(opened.socket.sent[0]);
  await until(() => expect(drawn.length).toBe(1));
  return { ...opened, drawn };
}

test("Play asks for each picture ahead of its time and draws it on the display frame it is due", async () => {
  const { page, socket, drawn } = await openTimed();
  clock.now = 10000;
  fireEvent.click(page.getByRole("button", { name: "Play" }));
  await settled();
  expect(socket.last()).toMatchObject({ frame: 0 });                 // the play's first picture, due at once
  await tick(10000);
  await until(() => expect(socket.last().frame).toBe(1));          // due at 10033 ms, asked for at 10000
  await socket.picture(socket.pictures().at(-2));                  // frame 0 draws now: its time has come
  expect(drawn.at(-1)).toBe(10000);
  clock.now = 10005;
  const held = socket.picture(socket.last());                      // frame 1, ready 28 ms early: it waits
  await settled();
  expect(drawn.length).toBe(2);
  await tick(10016.7);                                             // the display frame before it is due
  await held;
  expect(drawn).toEqual([1040, 10000, 10016.7]);
  expect(page.container.textContent).toContain("Frame 2 of 5");
  fireEvent.click(page.getByRole("button", { name: "Pause" }));
});

test("a scene still being written grows under the slider, and Play waits at its newest picture, then goes on", async () => {
  opened = { ...opened, frames: FRAMES.slice(0, 3), growing: true };
  const { page, socket } = await openScene();
  await socket.picture(socket.sent[0]);
  await until(() => expect(page.container.textContent).toContain("Frame 1 of 3 so far"));
  socket.text({ kind: "grown", session: 2, from: 3, frames: FRAMES.slice(3), growing: false });   // another scene's
  socket.text({ kind: "grown", session: 1, from: 3, frames: FRAMES.slice(3, 4), growing: true });
  await until(() => expect(page.container.textContent).toContain("Frame 1 of 4 so far"));
  clock.now = 10000;
  fireEvent.click(page.getByRole("button", { name: "Play" }));
  const answer = async () => {             // each asked picture arrives (a held one draws on its due frame)
    for (const request of socket.pictures().filter((r) => !r.answered)) {
      request.answered = true;
      socket.picture(request);
    }
    await settled();
  };
  for (let at = 10000; at <= 10400; at += 16.7) {         // well past the four frames' 133 ms
    await tick(at);
    await answer();
  }
  expect(page.getByRole("button", { name: "Pause" })).toBeTruthy();   // waiting, not finished
  expect(Math.max(...socket.pictures().map((request) => request.frame))).toBe(3);
  socket.text({ kind: "grown", session: 1, from: 4, frames: FRAMES.slice(4), growing: false });
  for (let at = 10417; at <= 10600; at += 16.7) {
    await tick(at);
    await answer();
  }
  expect(socket.pictures().some((request) => request.frame === 4)).toBe(true);   // went on, frame by frame
  await until(() => expect(page.getByRole("button", { name: "Play" })).toBeTruthy());   // whole: it ends
  expect(page.container.textContent).toContain("Frame 5 of 5");
  expect(page.container.textContent).not.toContain("so far");
});

test("a picture a play asked for ahead is never shown once the play has stopped", async () => {
  const { page, socket, drawn } = await openTimed();
  clock.now = 10000;
  fireEvent.click(page.getByRole("button", { name: "Play" }));
  await settled();
  await tick(10000);
  await until(() => expect(socket.last().frame).toBe(1));
  await socket.picture(socket.pictures().at(-2));                  // frame 0, due at once
  clock.now = 10005;
  const waiting = socket.picture(socket.last());                   // frame 1, due at 10033: it waits
  await settled();
  expect(drawn.length).toBe(2);
  fireEvent.click(page.getByRole("button", { name: "Pause" }));    // stopped while it waits
  await waiting;
  await tick(10050);
  await settled();
  expect(drawn.length).toBe(2);                                    // frame 1 was never shown
  expect(page.container.textContent).toContain("Frame 1 of 5");
  await tick(10100);
  expect(socket.pictures().length).toBe(3);                        // and a stopped play asks for nothing more
  clock.now = 20000;                                               // again, stopped before its pictures come
  fireEvent.click(page.getByRole("button", { name: "Play" }));
  await settled();
  await tick(20000);
  await tick(20020);                                               // pictures now come 5 ms after asked
  await until(() => expect(socket.last().frame).toBe(1));
  fireEvent.click(page.getByRole("button", { name: "Pause" }));
  await socket.picture(socket.pictures().at(-2));
  await socket.picture(socket.last());
  await settled();
  expect(drawn.length).toBe(2);
  expect(page.container.textContent).toContain("Frame 1 of 5");
});

// ------------------------------------------------------------------ Restart, Loop, , and ., and the camera never pausing
// His round 4 (2026-10-27), items 4-7: "a loop button (when enabled, the gameplay loops)"; "a restart
// button ... if the playback is actively playing, then restarting also immediately starts from the
// beginning; if it's paused, then we go to the beginning frame but don't start"; "It should not pause
// the gameplay when I adjust the camera, unless I explicitly paused"; "If I press "." it should move the
// frames forward by one. "," should move it backwards by one frame."

// Every picture asked for and not yet answered arrives (a play's waits for its due frame).
const answerAll = async (socket) => {
  for (const request of socket.pictures().filter((r) => !r.answered)) {
    request.answered = true;
    socket.picture(request);
  }
  await settled();
};
// The display frames from `from` to `to`, every picture answered as it is asked.
async function playThrough(socket, from, to) {
  for (let at = from; at <= to; at += 16.7) {
    await tick(at);
    await answerAll(socket);
  }
}
const playing = (page) => !!page.queryByRole("button", { name: "Pause" });
const frameText = (page) => page.container.querySelector(".scene-lab-frame").textContent.match(/Frame \d+ of \d+/)[0];

test("Restart goes to the first frame: paused it stays paused there, playing it plays on from there at once", async () => {
  const { page, socket, root } = await openScene();
  await socket.picture(socket.sent[0]);
  for (let i = 0; i < 3; i++) {
    fireEvent.keyDown(root, { key: "ArrowRight" });
    await until(() => expect(socket.last().frame).toBe(i + 1));
    await answerAll(socket);
  }
  await until(() => expect(frameText(page)).toBe("Frame 4 of 5"));
  fireEvent.click(page.getByRole("button", { name: "Restart" }));
  await until(() => expect(socket.last()).toMatchObject({ frame: 0, camera: null }));
  await answerAll(socket);
  await tick(2000);
  expect(playing(page)).toBe(false);                               // paused: it stays paused
  expect(frameText(page)).toBe("Frame 1 of 5");
  expect(socket.pictures().at(-1).frame).toBe(0);                  // and asks for nothing more

  clock.now = 10000;
  fireEvent.click(page.getByRole("button", { name: "Play" }));
  await playThrough(socket, 10000, 10090);
  const before = socket.pictures().length;
  expect(Math.max(...socket.pictures().map((request) => request.frame))).toBeGreaterThanOrEqual(2);
  fireEvent.click(page.getByRole("button", { name: "Restart" }));  // playing: from the first frame, at once
  expect(playing(page)).toBe(true);
  await until(() => expect(socket.last().frame).toBe(0));
  await playThrough(socket, clock.now, clock.now + 60);
  const after = socket.pictures().slice(before).map((request) => request.frame);
  expect(after[0]).toBe(0);
  expect(after.at(-1)).toBeGreaterThan(0);                         // and goes on playing from there
  expect(playing(page)).toBe(true);
  fireEvent.click(page.getByRole("button", { name: "Pause" }));
});

test("Loop plays on from the first frame after the last, and off again Play stops on the last", async () => {
  const { page, socket } = await openScene();
  await socket.picture(socket.sent[0]);
  const loop = page.getByRole("button", { name: "Loop" });
  expect(loop.getAttribute("aria-pressed")).toBe("false");
  fireEvent.click(loop);
  expect(loop.getAttribute("aria-pressed")).toBe("true");
  clock.now = 10000;
  fireEvent.click(page.getByRole("button", { name: "Play" }));
  await playThrough(socket, 10000, 10400);                         // the five frames last 167 ms
  const asked = socket.pictures().map((request) => request.frame);
  const last = asked.indexOf(4);
  expect(last).toBeGreaterThan(0);
  expect(asked.slice(last)).toContain(0);                          // after the last frame, the first again
  expect(asked.slice(asked.indexOf(0, last))).toContain(4);        // and it played through once more
  expect(playing(page)).toBe(true);
  fireEvent.click(loop);                                           // off: this pass ends on the last frame
  await playThrough(socket, 10417, 10800);
  expect(playing(page)).toBe(false);
  expect(frameText(page)).toBe("Frame 5 of 5");
});

test("Loop stays as it was left for the next scene", async () => {
  const first = await openScene();
  fireEvent.click(first.page.getByRole("button", { name: "Loop" }));
  expect(localStorage.getItem(LOOP_PREFERENCE)).toBe("on");
  cleanup();
  sockets = [];
  const second = await openScene();
  expect(second.page.getByRole("button", { name: "Loop" }).getAttribute("aria-pressed")).toBe("true");
});

test("on a scene still being written, Loop waits at the newest picture and loops only once the scene is whole", async () => {
  opened = { ...opened, frames: FRAMES.slice(0, 3), growing: true };
  const { page, socket } = await openScene();
  await socket.picture(socket.sent[0]);
  fireEvent.click(page.getByRole("button", { name: "Loop" }));
  clock.now = 10000;
  fireEvent.click(page.getByRole("button", { name: "Play" }));
  await playThrough(socket, 10000, 10400);
  expect(playing(page)).toBe(true);                                // waiting at frame 3 of 3 so far
  const waiting = socket.pictures().map((request) => request.frame);
  expect(waiting.slice(waiting.indexOf(2))).not.toContain(0);      // never back to the start while it grows
  socket.text({ kind: "grown", session: 1, from: 3, frames: FRAMES.slice(3), growing: false });
  await playThrough(socket, 10417, 10700);
  const asked = socket.pictures().map((request) => request.frame);
  expect(asked.slice(asked.indexOf(4))).toContain(0);              // whole: on from the first frame
  expect(playing(page)).toBe(true);
  fireEvent.click(page.getByRole("button", { name: "Pause" }));
});

// Two seconds of frames: long enough to play through every move below.
const LONG = Array.from({ length: 60 }, (_, i) => ({ level: 9, area: 1, game_frame: 1000 + i }));

test("moving the camera never pauses Play: a drag from the recorded camera, a slide, the wheel and a fly-to", async () => {
  opened = { ...opened, frames: LONG };
  const { page, socket, canvas } = await openScene();
  await socket.picture(socket.sent[0]);
  clock.now = 10000;
  fireEvent.click(page.getByRole("button", { name: "Play" }));
  await playThrough(socket, 10000, 10040);
  // A left drag in the recorded camera: the free camera starts from the picture on screen.
  fireEvent.pointerDown(canvas, { button: 0, buttons: 1, pointerId: 1, clientX: 300, clientY: 200 });
  move(canvas, { pointerId: 1, buttons: 1, clientX: 330, clientY: 200 });
  await until(() => expect(socket.last()).toMatchObject({ kind: "pose" }));
  socket.text({ id: socket.last().id, kind: "pose", pose: POSE, reason: null });
  await until(() => expect(socket.last().camera).toBeTruthy());
  fireEvent.pointerUp(canvas, { button: 0, buttons: 0, pointerId: 1 });
  expect(playing(page)).toBe(true);
  await playThrough(socket, 10057, 10090);
  const moves = [
    () => {                                                        // Shift-drag slides
      fireEvent.pointerDown(canvas, { button: 0, buttons: 1, pointerId: 2, clientX: 300, clientY: 200 });
      move(canvas, { pointerId: 2, buttons: 1, clientX: 340, clientY: 210, shiftKey: true });
      fireEvent.pointerUp(canvas, { button: 0, buttons: 0, pointerId: 2 });
    },
    () => {                                                        // a middle drag slides too
      fireEvent.pointerDown(canvas, { button: 1, buttons: 4, pointerId: 3, clientX: 300, clientY: 200 });
      move(canvas, { pointerId: 3, buttons: 4, clientX: 280, clientY: 220 });
      fireEvent.pointerUp(canvas, { button: 1, buttons: 0, pointerId: 3 });
    },
    () => fireEvent.wheel(canvas, { deltaY: -120, deltaMode: 0 }),
  ];
  for (const moved of moves) {
    const camera = socket.pictures().at(-1).camera;
    moved();
    await until(() => expect(socket.pictures().at(-1).camera).not.toEqual(camera));
    expect(playing(page)).toBe(true);
    await playThrough(socket, clock.now + 16.7, clock.now + 34);
  }
  const before = socket.pictures().at(-1).camera;                  // a double-click flies there
  fireEvent.dblClick(canvas, { button: 0, clientX: 320, clientY: 240 });
  await until(() => expect(socket.last()).toMatchObject({ kind: "pick" }));
  socket.text({ id: socket.last().id, kind: "pick", point: [0, 0, -900], mario: [40, 80, -60], reason: null });
  await until(() => expect(socket.pictures().at(-1).camera).not.toEqual(before));
  await playThrough(socket, clock.now + 16.7, clock.now + 250);
  expect(playing(page)).toBe(true);
  const frames = socket.pictures().map((request) => request.frame);
  expect(Math.max(...frames)).toBeGreaterThan(10);                 // the play went on through every move
  fireEvent.click(page.getByRole("button", { name: "Pause" }));
});

test("pausing while the camera moved asks for the camera again at the frame on screen", async () => {
  opened = { ...opened, frames: LONG };
  const { page, socket, canvas } = await openScene();
  const entry = await enterFree(page, socket);
  clock.now = 10000;
  fireEvent.click(page.getByRole("button", { name: "Play" }));
  await playThrough(socket, 10000, 10040);
  await tick(10050);                                               // frame 2 asked for, not drawn yet
  await until(() => expect(socket.pictures().at(-1).frame).toBe(2));
  fireEvent.pointerDown(canvas, { button: 0, buttons: 1, pointerId: 1, clientX: 300, clientY: 200 });
  move(canvas, { pointerId: 1, buttons: 1, clientX: 330, clientY: 200 });
  fireEvent.pointerUp(canvas, { button: 0, buttons: 0, pointerId: 1 });
  await until(() => expect(socket.pictures().at(-1).camera).not.toEqual(entry));
  const moved = socket.pictures().at(-1);
  expect(moved.frame).toBe(2);                                     // the move asked for the play's next frame
  fireEvent.click(page.getByRole("button", { name: "Pause" }));
  await answerAll(socket);
  expect(frameText(page)).toBe("Frame 2 of 60");                   // which a paused play never shows
  await until(() => expect(socket.pictures().at(-1)).toMatchObject({ frame: 1, camera: moved.camera }));
});

test(", and . step one frame like the arrows; a slider reached with Tab gives them to the viewer, a field that types keeps them", async () => {
  const { page, socket, root } = await openScene();
  await socket.picture(socket.sent[0]);
  for (const [key, code, frame] of [[".", "Period", 1], [".", "Period", 2], [",", "Comma", 1]]) {
    expect(fireEvent.keyDown(root, { key, code })).toBe(false);
    await until(() => expect(socket.last().frame).toBe(frame));
    await answerAll(socket);
  }
  await until(() => expect(frameText(page)).toBe("Frame 2 of 5"));
  clock.now = 10000;
  fireEvent.click(page.getByRole("button", { name: "Play" }));     // while playing, . pauses and steps, as Right does
  await playThrough(socket, 10000, 10040);
  fireEvent.keyDown(root, { key: ".", code: "Period" });
  expect(playing(page)).toBe(false);
  await answerAll(socket);
  const slider = page.getByRole("slider", { name: "Frame" });
  slider.focus();                                                  // reached with Tab
  const at = socket.last().frame;
  expect(fireEvent.keyDown(slider, { key: "ArrowRight", code: "ArrowRight" })).toBe(true);   // the slider's own
  expect(fireEvent.keyDown(slider, { key: ".", code: "Period" })).toBe(false);               // the viewer's
  await until(() => expect(socket.last().frame).toBe(at + 1));
  await answerAll(socket);
  fireEvent.click(page.getByRole("button", { name: "Tune controls" }));
  const field = page.getByRole("region", { name: "Tune controls" }).querySelector("#flightSpeed");
  field.focus();
  const sent = socket.sent.length;
  for (const [key, code] of [[".", "Period"], [",", "Comma"]]) {
    expect(fireEvent.keyDown(field, { key, code })).toBe(true);    // typed, not taken
  }
  await settled();
  expect(socket.sent.length).toBe(sent);
});

// His round 4, item 9: "We should have a toggle for enabling / disabling the HUD."
test("HUD off asks for every picture without the HUD, the one on screen at once, and stays off for the next scene", async () => {
  const { page, socket, root } = await openScene();
  await socket.picture(socket.sent[0]);
  expect(socket.sent[0]).not.toHaveProperty("hud");                               // on: the server's own default
  const hud = page.getByRole("button", { name: "HUD" });
  expect(hud.getAttribute("aria-pressed")).toBe("true");
  fireEvent.click(hud);
  await until(() => expect(socket.pictures().length).toBe(2));
  expect(socket.pictures()[1]).toMatchObject({ frame: 0, hud: false });
  expect(hud.getAttribute("aria-pressed")).toBe("false");
  expect(localStorage.getItem(HUD_PREFERENCE)).toBe("off");
  await socket.picture(socket.pictures()[1], { hud: true, hud_reason: "This picture's HUD could not be left out." });
  await until(() => expect(page.container.textContent).toContain("This picture's HUD could not be left out."));
  fireEvent.keyDown(root, { key: "ArrowRight" });
  await until(() => expect(socket.pictures().at(-1)).toMatchObject({ frame: 1, hud: false }));
  await socket.picture(socket.pictures().at(-1), { hud: false });
  await until(() => expect(page.container.textContent).not.toContain("could not be left out"));
  cleanup();
  sockets = [];
  const again = await openScene();
  expect(again.socket.sent[0]).toMatchObject({ frame: 0, hud: false });          // remembered
  await again.socket.picture(again.socket.sent[0]);
  fireEvent.click(again.page.getByRole("button", { name: "HUD" }));
  await until(() => expect(again.socket.pictures().length).toBe(2));
  expect(again.socket.pictures()[1]).not.toHaveProperty("hud");
  expect(localStorage.getItem(HUD_PREFERENCE)).toBe("on");
});

test("the header names the scene, never its file: the server's title, else the attempt the Practice log shows, else Recording", async () => {
  const lab = await openScene();
  await lab.socket.picture(lab.socket.sent[0]);
  const title = () => lab.page.container.querySelector(".scene-lab-title")?.textContent;
  await until(() => expect(title()).toBe("Recording"));
  expect(lab.page.container.querySelector(".workshop-hero").textContent).not.toContain("sm64scene");
  cleanup();
  sockets = [];
  opened = { ...opened, attempt: 12 };
  const view = { catalog: { courses: [] }, segments: [], stars: [{ course_name: "Whomp's Fortress",
    star_name: "Shoot into the Wild Blue", attempts: [{ id: 11, igt: "12\"40" },
      { id: 12, igt: "11\"03", rta: "11\"60", strat_tag: "Single Jump" }] }] };
  const fallback = render(h(SceneViewer, { t: { ...fromGhostButton(12).t, view } }));
  await until(() => expect(sockets.length).toBe(1));
  await until(() => expect(fallback.container.querySelector(".scene-lab-title")?.textContent)
    .toBe("Whomp's Fortress · Shoot into the Wild Blue · Single Jump · 11\"03"));
  // A title naming the target wins; a plain one ("Lab recording", "Attempt 12") yields to the attempt
  // the log knows, and stays when the log does not know it.
  expect(sceneTitle({ attempt: 12, title: "Bob-omb Battlefield · Big Bob-omb · Standard · 40\"10",
    target: { course_id: 1 } }, view)).toBe("Bob-omb Battlefield · Big Bob-omb · Standard · 40\"10");
  expect(sceneTitle({ name: "take-9731-46.sm64scene", attempt: 12, title: "Lab recording" }, view))
    .toBe("Whomp's Fortress · Shoot into the Wild Blue · Single Jump · 11\"03");
  expect(sceneTitle({ name: "take-9731-46.sm64scene", attempt: 99, title: "Attempt 99" }, view)).toBe("Attempt 99");
  expect(sceneTitle({ name: "take-9731-46.sm64scene", attempt: 99 }, view)).toBe("Recording");   // not in the log
  expect(sceneTitle({ name: "take-9731-46.sm64scene", attempt: 5 }, { segments: [{ id: 3, kind: "segment", name: "LBLJ",
    attempts: [{ id: 5, segment_id: 3, rta: "4\"20", igt: null }] }] })).toBe("Segment · LBLJ · 4\"20");
});

// ------------------------------------------------------------------ the desktop window's shared memory

// The desktop window's WebView2 as a page sees it: sharedbufferreceived events and releaseBuffer.
function desktopWindow() {
  const view = Object.assign(new EventTarget(), { releaseBuffer: vi.fn() });
  globalThis.chrome = { webview: view };
  const post = (id, session = 1) => {
    const buffer = new Uint8Array(36).map((_, i) => id * 10 + i).buffer;
    const event = Object.assign(new Event("sharedbufferreceived"), { additionalData: { id, session },
      getBuffer: () => buffer });
    view.dispatchEvent(event);
    return buffer;
  };
  return { view, post };
}

test("in the desktop window the page asks for shared pictures and draws their exact pixels, whichever comes first", async () => {
  const drawn = [];
  HTMLCanvasElement.prototype.getContext = () => ({ putImageData: (image) => drawn.push([...image.data.slice(0, 4)]),
    createImageData: (w, hgt) => ({ data: new Uint8ClampedArray(w * hgt * 4), width: w, height: hgt }) });
  opened.shared_pictures = true;
  const { view, post } = desktopWindow();
  try {
    const { page, socket, root } = await openScene();
    expect(socket.sent[0].encoding).toBe("shared");
    expect(page.container.querySelector("select option[value=shared]")).not.toBeNull();
    const first = post(socket.sent[0].id);                            // the pixels before their header
    await socket.picture(socket.sent[0], { encoding: "shared" });
    await waitFor(() => expect(drawn.length).toBe(1));
    const topRow = new Uint8Array(first, 2 * 12, 3);                 // BGR rows bottom-up: the top row is last
    expect(drawn[0]).toEqual([topRow[2], topRow[1], topRow[0], 255]);
    expect(view.releaseBuffer).toHaveBeenCalledWith(first);
    fireEvent.keyDown(root, { key: "ArrowRight" });
    await waitFor(() => expect(socket.sent.length).toBe(2));
    const header = socket.picture(socket.sent[1], { encoding: "shared" });   // the header before its pixels
    await new Promise((resolve) => { setTimeout(resolve, 20); });
    expect(drawn.length).toBe(1);
    const second = post(socket.sent[1].id);
    await header;
    await waitFor(() => expect(drawn.length).toBe(2));
    expect(view.releaseBuffer).toHaveBeenCalledWith(second);
    post(99, 7);                                                      // another scene's: let go at once
    expect(view.releaseBuffer).toHaveBeenCalledTimes(3);
    expect(page.container.querySelector(".scene-lab-readout").textContent).toContain("exact pixels through shared memory");
  } finally {
    delete globalThis.chrome;
  }
});

test("a shared picture whose pixels never come is let go, and a JPEG answer draws as a JPEG", async () => {
  SHARED_WAIT.ms = 40;
  opened.shared_pictures = true;
  desktopWindow();
  try {
    const { page, socket } = await openScene();
    socket.picture(socket.sent[0], { encoding: "shared" });
    await new Promise((resolve) => { setTimeout(resolve, 80); });
    expect(page.container.querySelector(".scene-lab-readout")).toBeNull();       // nothing drawn, nothing stuck
    expect(page.container.querySelector(".scene-lab-busy")).toBeNull();
  } finally {
    SHARED_WAIT.ms = 2000;
    delete globalThis.chrome;
  }
});

test("a browser page never asks for shared pictures, even when the server offers them", async () => {
  opened.shared_pictures = true;
  const { page, socket } = await openScene();
  expect(socket.sent[0].encoding).toBe("jpeg");
  expect(page.container.querySelector("select option[value=shared]")).toBeNull();
});

// ------------------------------------------------------------------ the page's own numbers

const observations = () => fetches.filter(([url, method]) => url === "/api/uilog" && method === "POST")
  .map(([, , body]) => JSON.parse(body)).filter((body) => body.surface === "scene_viewer");

test("while the camera moves the page sums up its numbers once a window, and posts nothing while still", async () => {
  MOVING_WINDOW.ms = 80;
  try {
    const { page, socket, root, canvas } = await openScene();
    await enterFree(page, socket);
    await new Promise((resolve) => { setTimeout(resolve, 160); });
    expect(observations()).toEqual([]);                            // entering is not moving
    fireEvent.pointerDown(canvas, { button: 0, buttons: 1, pointerId: 1, clientX: 100, clientY: 100 });
    for (let step = 1; step <= 4; step++) {
      const sent = socket.sent.length;
      clock.now += 10;                                             // a picture every 10 ms
      move(canvas, { pointerId: 1, buttons: 1, clientX: 100 + 10 * step, clientY: 100 });
      await waitFor(() => expect(socket.sent.length).toBe(sent + 1));
      await socket.picture(socket.last(), { server_ms: 12, timings: { evaluate_ms: 5, host_ms: 3, encode_ms: 4 } });
    }
    fireEvent.pointerUp(canvas, { pointerId: 1 });
    await waitFor(() => expect(observations().length).toBe(1));
    const [summary] = observations();
    expect(summary).toMatchObject({ pictures: 4, in_flight: 2, skipped: 0, width: 4, height: 3, encoding: "raw",
      server_ms: { p50: 12, p95: 12 }, evaluate_ms: { p50: 5, p95: 5 }, host_ms: { p50: 3, p95: 3 },
      encode_ms: { p50: 4, p95: 4 } });
    expect(Object.keys(summary.input_to_drawn_ms)).toEqual(["p50", "p95"]);   // jsdom's event clock is not the page's
    expect(summary.pictures_per_second).toBeGreaterThan(0);
    expect(page.container.querySelector(".scene-lab-moving").textContent).toContain("Last time the camera moved");
    await new Promise((resolve) => { setTimeout(resolve, 240); });
    expect(observations().length).toBe(1);                        // still: nothing more
    fireEvent.keyDown(root, { key: "r" });                        // the recorded camera stepping is not moving
    await waitFor(() => expect(socket.last()).toMatchObject({ camera: null }));
    await socket.picture(socket.last());
    fireEvent.keyDown(root, { key: "ArrowRight" });
    await waitFor(() => expect(socket.last()).toMatchObject({ frame: 1, camera: null }));
    await socket.picture(socket.last());
    await new Promise((resolve) => { setTimeout(resolve, 240); });
    expect(observations().length).toBe(1);
  } finally {
    MOVING_WINDOW.ms = 2000;
  }
});

test("a moving summary reports the pictures it drew, what it skipped, and nothing without movement", () => {
  const sample = (at, input) => ({ at, input_ms: input, decode_ms: 3, trip_ms: 15, server_ms: 12,
    timings: { evaluate_ms: 5 }, encoding: "jpeg", width: 1600, height: 1200 });
  expect(movingSummary([sample(0, null), sample(10, null)])).toBeNull();
  const summary = movingSummary([sample(0, 20), sample(10, 30), sample(20, null), sample(1000, 25)], 2);
  expect(summary).toMatchObject({ pictures: 4, seconds: 0.02, pictures_per_second: 100, skipped: 2,
    input_to_drawn_ms: { p50: 25, p95: 30 }, evaluate_ms: { p50: 5, p95: 5 }, width: 1600, encoding: "jpeg" });
  expect(STILL_MS).toBeLessThan(980);                              // the still second is not moving time
  expect(movingSummary([sample(0, 20)]).pictures_per_second).toBeNull();
});

// ------------------------------------------------------------------ entering Free camera

test("the free camera starts from the recorded camera, keeps its place across steps, and R returns", async () => {
  const { page, socket, root } = await openScene();
  const first = await enterFree(page, socket);
  close(first.focus, POSE.focus);
  close(first.position, POSE.position);
  expect(page.container.querySelector(".scene-lab-tag").textContent).toBe("Free camera");
  fireEvent.keyDown(root, { key: "ArrowRight" });
  await waitFor(() => expect(socket.last().frame).toBe(1));
  expect(socket.last().camera).toEqual(first);                    // same world camera, next frame
  await socket.picture(socket.last());
  fireEvent.keyDown(root, { key: "r" });
  await waitFor(() => expect(socket.last()).toMatchObject({ frame: 1, camera: null }));
});

test.each([
  ["tilted by a shake", { ...POSE, roll: -224 }],
  ["nearly straight down", { ...POSE, focus: [0, 0, 0], position: [3, 1000, 2] }],
  ["closer than the wheel allows", { ...POSE, focus: [0, 100, 0], position: [0, 110, 12] }],
  ["zoomed out to 80 degrees", { ...POSE, fov: 80, roll: 37 }],
])("entering from a camera %s draws exactly that camera: no jump", async (_label, pose) => {
  const { page, socket } = await openScene();
  const first = await enterFree(page, socket, pose);
  close(first.focus, pose.focus, 9);
  close(first.position, pose.position, 9);
  expect(first.roll).toBe(pose.roll);
  expect(first.fov).toBe(pose.fov);
});

test("orbit math keeps the camera it was given and clamps only what a movement asks for", () => {
  const steep = orbitFromPose([0, 0, 0], [3, 1000, 2], { roll: 0, fov: 45 });
  const limit = REFERENCE.steepestDegrees * Math.PI / 180;
  expect(steep.pitch).toBeGreaterThan(limit);
  expect(turned(steep, 0, 50, REFERENCE).pitch).toBe(steep.pitch);           // never steeper than it began
  expect(turned(steep, 0, -1, REFERENCE).pitch).toBeLessThan(steep.pitch);   // and comes back without a snap
  expect(turned(steep, 0, -1, REFERENCE).pitch).toBeGreaterThan(limit);
  const level = orbitFromPose(POSE.focus, POSE.position);
  expect(turned(level, 0, 1e6, REFERENCE).pitch).toBeCloseTo(limit, 12);
  expect(dollied(level, 1e6, REFERENCE).dist).toBe(REFERENCE.farthestDistance);
  expect(dollied(level, -1e6, REFERENCE).dist).toBe(REFERENCE.closestDistance);
  const near = orbitFromPose([0, 100, 0], [0, 110, 12]);
  expect(dollied(near, -1, REFERENCE).dist).toBeLessThan(REFERENCE.closestDistance);   // kept, still closer
  expect(orbitFromPose([1, 2, 3], [1, 2, 3])).toBeNull();
});

test("a tilted view levels as you turn or fly, never on entry, a step, a slide or the wheel; Reset view brings it back", async () => {
  const { page, socket, root, canvas } = await openScene();
  const first = await enterFree(page, socket, { ...POSE, roll: 1000 });
  expect(first.roll).toBe(1000);
  fireEvent.keyDown(root, { key: "ArrowRight" });
  await waitFor(() => expect(socket.last().frame).toBe(1));
  expect(socket.last().camera.roll).toBe(1000);
  await socket.picture(socket.last());
  fireEvent.wheel(canvas, { deltaY: 100 });
  await waitFor(() => expect(socket.last().camera.position).not.toEqual(first.position));
  expect(socket.last().camera.roll).toBe(1000);
  await socket.picture(socket.last());
  fireEvent.pointerDown(canvas, { button: 0, pointerId: 1, clientX: 100, clientY: 100 });
  move(canvas, { pointerId: 1, clientX: 110, clientY: 100, shiftKey: true });
  await waitFor(() => expect(socket.last().camera.focus).not.toEqual(first.focus));
  expect(socket.last().camera.roll).toBe(1000);                                 // a slide keeps the tilt
  await socket.picture(socket.last());
  move(canvas, { pointerId: 1, clientX: 120, clientY: 100 });
  await waitFor(() => expect(socket.last().camera.roll).toBe(Math.round(1000 - 10 * REFERENCE.levelPerPixel * PER_DEGREE)));
  await socket.picture(socket.last());
  fireEvent.pointerUp(canvas, { pointerId: 1 });
  fireEvent.click(page.getByRole("button", { name: "Reset view" }));
  await waitFor(() => expect(socket.last().camera).toEqual(first));
});

// ------------------------------------------------------------------ flight: W A S D and Q E, no button needed

const hint = (page) => page.container.querySelector(".scene-lab-hint").textContent;
const LOOKING = "Move the mouse to look around";

// Holding the right button adds looking around with the mouse.
async function startLooking(page, socket, canvas) {
  await enterFree(page, socket);
  fireEvent.pointerDown(canvas, { button: 2, buttons: 2, pointerId: 3 });
  await until(() => expect(hint(page)).toContain(LOOKING));
}

test("W flies with no button held: a constant speed where the camera looks, and letting go stops it", async () => {
  const { page, socket, root } = await openScene();
  const start = await enterFree(page, socket);
  clock.now = 5000;                                                             // the key goes down at 5 s
  expect(fireEvent.keyDown(root, { key: "w", code: "KeyW" })).toBe(false);     // the camera's: nothing else gets it
  await tick(5050);                                                             // the first step, 50 ms on
  await until(() => expect(socket.last().camera.position).not.toEqual(start.position));
  const first = socket.last().camera;
  await socket.picture(socket.last());
  await tick(5100);                                                             // 50 ms later
  await until(() => expect(socket.last().camera.position).not.toEqual(first.position));
  const moved = socket.last().camera, view = [0, 1, 2].map((i) => start.focus[i] - start.position[i]);
  const length = Math.hypot(...view), travelled = [0, 1, 2].map((i) => moved.position[i] - first.position[i]);
  close(travelled, view.map((value) => value / length * REFERENCE.flightSpeed * 0.05), 6);
  await socket.picture(socket.last());
  fireEvent.keyUp(root, { key: "w", code: "KeyW" });
  await tick(5150);
  const after = socket.sent.length;
  await tick(6000);
  expect(frames.length).toBe(0);                                                // no coasting: the loop ended
  expect(socket.sent.length).toBe(after);
  expect(HTMLCanvasElement.prototype.requestPointerLock).not.toHaveBeenCalled(); // the pointer stays his
  expect(page.container.querySelector(".scene-lab-cue")).toBeNull();
});

test("keys held together combine through the one flight step: W and D go diagonally, no faster than straight", async () => {
  const { page, socket, root } = await openScene();
  await enterFree(page, socket);
  clock.now = 5000;
  fireEvent.keyDown(root, { key: "w", code: "KeyW" });
  fireEvent.keyDown(root, { key: "d", code: "KeyD" });
  await tick(5050);
  await until(() => expect(socket.last().camera.position).not.toEqual(POSE.position));
  const first = socket.last().camera;
  await socket.picture(socket.last());
  await tick(5100);
  await until(() => expect(socket.last().camera.position).not.toEqual(first.position));
  const expected = cameraOf(flown(orbitFromPose(first.focus, first.position, { roll: 0, fov: first.fov }),
    { forward: 1, right: 1 }, 0.05, REFERENCE.flightSpeed, REFERENCE));
  close(socket.last().camera.position, expected.position, 6);
  expect(Math.hypot(...[0, 1, 2].map((i) => socket.last().camera.position[i] - first.position[i])))
    .toBeCloseTo(REFERENCE.flightSpeed * 0.05, 6);
  fireEvent.keyUp(root, { key: "w", code: "KeyW" });
  fireEvent.keyUp(root, { key: "d", code: "KeyD" });
});

test("in the recorded camera the first movement key enters the free camera from exactly the picture on screen and moves on that press", async () => {
  const shown = { ...POSE, roll: -2000, fov: 80 };
  const { page, socket, root } = await openScene();
  await socket.picture(socket.sent[0]);
  clock.now = 5000;
  fireEvent.keyDown(root, { key: "w", code: "KeyW" });                         // no click on Free camera first
  await until(() => expect(socket.last()).toMatchObject({ kind: "pose", frame: 0 }));
  socket.text({ id: socket.last().id, kind: "pose", pose: shown, reason: null });
  await until(() => expect(socket.last().camera).toBeTruthy());
  const entry = socket.last().camera;                                          // the picture does not jump
  close(entry.focus, shown.focus, 9);
  close(entry.position, shown.position, 9);
  expect(entry.roll).toBe(shown.roll);
  expect(entry.fov).toBe(shown.fov);
  expect(page.container.querySelector(".scene-lab-tag").textContent).toBe("Free camera");
  await socket.picture(socket.last());
  await tick(5050);                                                             // the same press, still held
  await until(() => expect(socket.last().camera.position).not.toEqual(entry.position));
  const expected = cameraOf(flown(orbitFromPose(shown.focus, shown.position, { roll: shown.roll, fov: shown.fov }),
    { forward: 1 }, 0.05, REFERENCE.flightSpeed, REFERENCE));
  close(socket.last().camera.position, expected.position, 6);
  expect(socket.last().camera.roll).toBe(expected.roll);
  fireEvent.keyUp(root, { key: "w", code: "KeyW" });
});

test("holding the right button looks around with the mouse where the camera stands, and the picture says how to fly", async () => {
  const { page, socket, canvas } = await openScene();
  await startLooking(page, socket, canvas);
  expect(HTMLCanvasElement.prototype.requestPointerLock).toHaveBeenCalledTimes(1);
  expect(page.container.querySelector(".scene-lab-cue").textContent).toContain("W A S D move");
  const start = socket.last().camera;
  move(canvas, { pointerId: 3, buttons: 2, movementX: 25, movementY: -10 });
  await waitFor(() => expect(socket.last().camera.focus).not.toEqual(start.focus));
  const turnedView = socket.last().camera;
  close(turnedView.position, start.position, 6);                                   // it turned where it stands
  const before = orbitFromPose(start.focus, start.position), after = orbitFromPose(turnedView.focus, turnedView.position);
  expect(after.yaw - before.yaw).toBeCloseTo(-25 * REFERENCE.lookPerPixel, 9);
  expect(after.pitch - before.pitch).toBeCloseTo(-10 * REFERENCE.lookPerPixel, 9);
  await socket.picture(socket.last());
  move(canvas, { pointerId: 3, buttons: 0, movementX: 5, movementY: 0 });            // the button came up elsewhere
  await waitFor(() => expect(page.container.querySelector(".scene-lab-cue")).toBeNull());
  expect(locked).toBeNull();
});

test("the speed reads in three figures", () => {
  expect([0.64, 1, 1.25, 1.5625, 15.26, 20].map(speedWords)).toEqual(["0.64x", "1.0x", "1.25x", "1.56x", "15.3x", "20.0x"]);
});

test("Shift flies faster, Alt slower, and the wheel sets the speed while the right button is held instead of moving closer", async () => {
  const { page, socket, root, canvas } = await openScene();
  await startLooking(page, socket, canvas);
  const sentBefore = socket.sent.length;
  fireEvent.wheel(canvas, { deltaY: -100 });                                    // one notch up: faster
  await settled();
  expect(socket.sent.length).toBe(sentBefore);                                  // no dolly picture
  expect(page.getByRole("group", { name: "Flight speed" }).textContent).toContain(speedWords(REFERENCE.speedPerNotch));
  const start = socket.last().camera;
  clock.now = 5000;
  fireEvent.keyDown(root, { key: "e", code: "KeyE", shiftKey: true });
  await tick(5050);
  await until(() => expect(socket.last().camera.focus[1]).not.toBe(start.focus[1]));
  const first = socket.last().camera;
  await socket.picture(socket.last());
  await tick(5150);                                                             // 100 ms later
  await until(() => expect(socket.last().camera.focus[1]).not.toBe(first.focus[1]));
  const fast = socket.last().camera;
  expect(fast.focus[1] - first.focus[1])
    .toBeCloseTo(REFERENCE.flightSpeed * REFERENCE.speedPerNotch * REFERENCE.fastMultiplier * 0.1, 6);
  await socket.picture(socket.last());
  fireEvent.keyDown(root, { key: "Alt", code: "AltLeft", shiftKey: false, altKey: true });
  await tick(5250);
  await until(() => expect(socket.last().camera.focus[1]).not.toBe(fast.focus[1]));
  expect(socket.last().camera.focus[1] - fast.focus[1])
    .toBeCloseTo(REFERENCE.flightSpeed * REFERENCE.speedPerNotch * REFERENCE.slowMultiplier * 0.1, 6);
  fireEvent.click(page.getByRole("button", { name: "Reset" }));
  expect(page.getByRole("group", { name: "Flight speed" }).textContent).toContain("1.0x");
});

test("letting go of the right button gives the pointer back, and a key still held flies on until it is let go", async () => {
  const { page, socket, root, canvas } = await openScene();
  await startLooking(page, socket, canvas);
  clock.now = 5000;
  fireEvent.keyDown(root, { key: "d", code: "KeyD" });
  await tick(5050);
  await until(() => expect(frames.length).toBe(1));
  fireEvent.pointerUp(canvas, { button: 2, buttons: 0, pointerId: 3 });
  expect(locked).toBeNull();
  expect(hint(page)).not.toContain(LOOKING);
  await socket.picture(socket.last());
  const before = socket.last().camera;
  await tick(5100);
  await until(() => expect(socket.last().camera.position).not.toEqual(before.position));   // D still moves
  fireEvent.keyUp(root, { key: "d", code: "KeyD" });
  await tick(5150);
  expect(frames.length).toBe(0);
});

test.each([
  ["Esc", ({ root }) => fireEvent.keyDown(root, { key: "Escape" })],
  ["leaving the window", () => window.dispatchEvent(new Event("blur"))],
  ["the window hiding", () => {
    vi.spyOn(Document.prototype, "hidden", "get").mockReturnValue(true);
    document.dispatchEvent(new Event("visibilitychange"));
  }],
  ["the browser ending the pointer lock", () => { locked = null; document.dispatchEvent(new Event("pointerlockchange")); }],
  ["leaving the page", ({ page }) => page.unmount()],
  ["R", ({ root }) => fireEvent.keyDown(root, { key: "r", code: "KeyR" })],
  ["Close", ({ page }) => fireEvent.click(page.getByRole("button", { name: "Close" }))],
])("%s stops every movement, lets go of every key and gives the pointer back", async (_label, end) => {
  const { page, socket, root, canvas } = await openScene();
  await startLooking(page, socket, canvas);
  clock.now = 5000;
  fireEvent.keyDown(root, { key: "d", code: "KeyD" });
  await tick(5000);
  await tick(5050);
  await until(() => expect(frames.length).toBe(1));
  end({ page, root });
  await tick(5100);
  const after = socket.sent.length;
  fireEvent.keyDown(root, { key: "d", code: "KeyD", repeat: true });            // D still down: its repeats stay still
  await tick(5400);
  await tick(5450);
  expect(socket.sent.length).toBe(after);
  expect(frames.length).toBe(0);
  expect(locked).toBeNull();
  const shown = page.container.querySelector(".scene-lab-hint");
  if (shown) expect(shown.textContent).not.toContain(LOOKING);
});

test("arrows still step and Space still plays while looking around: one keyboard owner", async () => {
  const { page, socket, root, canvas } = await openScene();
  await startLooking(page, socket, canvas);
  fireEvent.keyDown(root, { key: "ArrowRight", code: "ArrowRight" });
  await waitFor(() => expect(socket.last().frame).toBe(1));
  expect(socket.last().camera).toBeTruthy();
  await socket.picture(socket.last());
  fireEvent.keyDown(root, { key: " ", code: "Space" });
  await waitFor(() => expect(page.getByRole("button", { name: "Pause" })).toBeTruthy());
});

// ------------------------------------------------------------------ the pointer lock never outlives its look
// His report (2026-10-26): "it gets stuck in the middle of the screen, as if there's an invisible
// wall blocking its movement". On Windows a held pointer lock keeps the cursor inside the page.

// The page's pointer lock records, in the order they were posted to the UI log.
const lockNotes = () => fetches.filter(([url, method]) => url === "/api/uilog" && method === "POST")
  .map(([, , body]) => JSON.parse(body)).filter((record) => record.kind === "pointer_lock");
const steps = (records) => records.map((record) => [record.event, record.why]);

test("a lock granted after the right button was let go is exited the moment it lands, and the log says why", async () => {
  const { page, socket, canvas } = await openScene();
  await enterFree(page, socket);
  await settled();
  const from = lockNotes().length;
  lockRequests = [];
  fireEvent.pointerDown(canvas, { button: 2, buttons: 2, pointerId: 3 });
  await until(() => expect(hint(page)).toContain(LOOKING));
  fireEvent.pointerUp(canvas, { button: 2, buttons: 0, pointerId: 3 });       // let go before the browser grants
  expect(document.exitPointerLock).not.toHaveBeenCalled();                      // nothing held yet to exit
  lockRequests.shift()();                                                       // the late grant
  await settled();
  expect(document.exitPointerLock).toHaveBeenCalledTimes(1);
  expect(locked).toBeNull();
  expect(hint(page)).not.toContain(LOOKING);
  await until(() => expect(steps(lockNotes().slice(from))).toEqual([
    ["requested", null], ["ended", "button up"], ["exited", "no look"]]));
  expect(lockNotes().slice(from).map((record) => record.request)).toEqual([1, 1, 1]);
});

test.each([
  ["page hidden", () => vi.spyOn(Document.prototype, "hidden", "get").mockReturnValue(true)],
  ["window unfocused", () => vi.spyOn(document, "hasFocus").mockReturnValue(false)],
])("a lock that lands with the %s is exited at once; the look goes on with the pointer captured", async (why, away) => {
  const { page, socket, canvas } = await openScene();
  await enterFree(page, socket);
  await settled();
  const from = lockNotes().length;
  lockRequests = [];
  fireEvent.pointerDown(canvas, { button: 2, buttons: 2, pointerId: 3 });
  await until(() => expect(hint(page)).toContain(LOOKING));
  away();
  lockRequests.shift()();
  await settled();
  expect(document.exitPointerLock).toHaveBeenCalledTimes(1);
  expect(locked).toBeNull();
  expect(hint(page)).toContain(LOOKING);                                        // the button is still down
  await until(() => expect(steps(lockNotes().slice(from))).toEqual([["requested", null], ["exited", why]]));
});

test("a grant for a request an earlier look made is exited; the look in progress takes its own", async () => {
  const { page, socket, canvas } = await openScene();
  await enterFree(page, socket);
  await settled();
  const from = lockNotes().length;
  lockRequests = [];
  fireEvent.pointerDown(canvas, { button: 2, buttons: 2, pointerId: 3 });
  await until(() => expect(hint(page)).toContain(LOOKING));
  fireEvent.pointerUp(canvas, { button: 2, buttons: 0, pointerId: 3 });
  fireEvent.pointerDown(canvas, { button: 2, buttons: 2, pointerId: 3 });       // pressed again before any grant
  await until(() => expect(hint(page)).toContain(LOOKING));
  lockRequests.shift()();                                                       // the first request's grant
  await settled();
  expect(document.exitPointerLock).toHaveBeenCalledTimes(1);
  expect(locked).toBeNull();
  expect(hint(page)).toContain(LOOKING);
  lockRequests.shift()();                                                       // the second's: kept
  await settled();
  expect(locked).toBe(canvas);
  fireEvent.pointerUp(canvas, { button: 2, buttons: 0, pointerId: 3 });
  expect(locked).toBeNull();
  await until(() => expect(lockNotes().slice(from).map((record) => [record.event, record.why, record.request])).toEqual([
    ["requested", null, 1], ["ended", "button up", 1], ["requested", null, 2], ["exited", "stale request", 2],
    ["granted", null, 2], ["ended", "button up", 2], ["exited", "button up", 2]]));
});

test("a request the browser refuses is logged, and the look goes on with the pointer captured", async () => {
  const { page, socket, canvas } = await openScene();
  await enterFree(page, socket);
  await settled();
  const from = lockNotes().length;
  HTMLCanvasElement.prototype.requestPointerLock = vi.fn(() => {
    document.dispatchEvent(new Event("pointerlockerror"));
    return Promise.reject(new Error("refused"));
  });
  fireEvent.pointerDown(canvas, { button: 2, buttons: 2, pointerId: 3 });
  await until(() => expect(hint(page)).toContain(LOOKING));
  await settled();
  expect(canvas.setPointerCapture).toHaveBeenCalled();
  await until(() => expect(steps(lockNotes().slice(from))).toEqual([["requested", null], ["error", null]]));
});

test("the lock's records reach the log at most LOCK_NOTES.records a window, and the next says how many did not", () => {
  const posted = [], clock = { at: 0 };
  const note = throttledNotes((record) => posted.push(record), () => clock.at);
  for (let i = 0; i < LOCK_NOTES.records + 5; i++) note({ event: "requested", request: i });
  expect(posted.length).toBe(LOCK_NOTES.records);
  clock.at = LOCK_NOTES.ms;
  note({ event: "granted", request: 99 });
  expect(posted.at(-1)).toEqual({ event: "granted", request: 99, dropped: 5 });
  note({ event: "ended", request: 99 });
  expect(posted.at(-1)).toEqual({ event: "ended", request: 99 });
});

// ------------------------------------------------------------------ who has the keyboard

test("arriving gives the viewer the keyboard, from the sidebar and from the Ghost button", async () => {
  const sidebar = document.body.appendChild(document.createElement("button"));
  sidebar.focus();                                                              // the entry he clicked to get here
  const page = render(h(SceneViewer, {}));
  expect(document.activeElement).toBe(page.container.querySelector(".scene-lab"));
  cleanup();
  sidebar.focus();
  const ghost = await openAttempt(12);
  expect(document.activeElement).toBe(ghost.page.container.querySelector(".scene-lab"));
  sidebar.remove();
});

test("a mouse click on a control hands the keyboard back; a button pressed from the keyboard keeps Space and Enter", async () => {
  const { page, socket, root } = await openScene();
  await socket.picture(socket.sent[0]);
  const play = page.getByRole("button", { name: "Play" });
  play.focus();                                                                 // a mouse press focuses the button,
  fireEvent.click(play, { detail: 1 });                                         // its click plays,
  expect(document.activeElement).toBe(root);                                    // and the viewer has the keyboard again
  await until(() => expect(page.getByRole("button", { name: "Pause" })).toBeTruthy());
  fireEvent.keyDown(document.activeElement, { key: " ", code: "Space" });       // so Space pauses, once
  await until(() => expect(page.getByRole("button", { name: "Play" })).toBeTruthy());
  const slider = page.getByRole("slider", { name: "Frame" });
  slider.focus();
  fireEvent.click(slider, { detail: 1 });
  expect(document.activeElement).toBe(root);
  const free = page.getByRole("button", { name: "Free camera" });
  free.focus();                                                                 // reached with Tab
  expect(fireEvent.keyDown(free, { key: " ", code: "Space" })).toBe(true);      // Space is the button's own press,
  expect(fireEvent.keyDown(free, { key: "Enter", code: "Enter" })).toBe(true);
  await settled();
  expect(page.getByRole("button", { name: "Play" })).toBeTruthy();             // never Play as well
  fireEvent.click(free);                                                        // the browser's press: detail 0
  expect(document.activeElement).toBe(free);                                    // Tab walks on from here
  await until(() => expect(socket.last()).toMatchObject({ kind: "pose" }));
  socket.text({ id: socket.last().id, kind: "pose", pose: POSE, reason: null });
  await until(() => expect(socket.last().camera).toBeTruthy());
  const entry = socket.last().camera;
  await socket.picture(socket.last());
  clock.now = 5000;
  expect(fireEvent.keyDown(free, { key: "w", code: "KeyW" })).toBe(false);     // W still flies from the button
  await tick(5050);
  await until(() => expect(socket.last().camera.position).not.toEqual(entry.position));
  fireEvent.keyUp(free, { key: "w", code: "KeyW" });
});

test("a field that types keeps its keys, Ctrl and Meta combinations stay the browser's, and focus moving to a field lets go", async () => {
  const { page, socket, root } = await openScene();
  const entry = await enterFree(page, socket);
  fireEvent.click(page.getByRole("button", { name: "Tune controls" }));
  const field = page.getByRole("region", { name: "Tune controls" }).querySelector("#flightSpeed");
  const picker = root.querySelector(".scene-lab-quality select");
  const sent = socket.sent.length;
  for (const target of [field, picker]) {
    target.focus();
    for (const [key, code] of [["w", "KeyW"], ["e", "KeyE"], [" ", "Space"], ["r", "KeyR"], ["f", "KeyF"], ["ArrowRight", "ArrowRight"]]) {
      expect(fireEvent.keyDown(target, { key, code }), `${target.tagName} ${key}`).toBe(true);   // not taken
    }
  }
  root.focus();
  for (const modifier of [{ ctrlKey: true }, { metaKey: true }]) {
    for (const [key, code] of [["r", "KeyR"], ["w", "KeyW"], [" ", "Space"]]) {
      expect(fireEvent.keyDown(root, { key, code, ...modifier }), `${key} ${JSON.stringify(modifier)}`).toBe(true);
    }
  }
  await tick(1100);
  expect(socket.sent.length).toBe(sent);                                        // nothing moved, stepped or played
  expect(page.container.querySelector(".scene-lab-tag").textContent).toBe("Free camera");
  clock.now = 5000;
  fireEvent.keyDown(root, { key: "w", code: "KeyW" });
  await tick(5050);
  await until(() => expect(socket.last().camera.position).not.toEqual(entry.position));
  field.focus();                                                                // W still down, now typing into a field
  await socket.picture(socket.last());
  await tick(5100);
  const after = socket.sent.length;
  await tick(5200);
  expect(socket.sent.length).toBe(after);
  expect(frames.length).toBe(0);
});

test("a choice made with the mouse in a select hands the keyboard back; a select reached with Tab keeps its keys", async () => {
  const { page, socket, root } = await openScene();
  const entry = await enterFree(page, socket);
  const pictures = root.querySelector(".scene-lab-quality select");
  fireEvent.pointerDown(pictures, { button: 0, pointerId: 1 });                 // the mouse opens its list
  pictures.focus();
  fireEvent.change(pictures, { target: { value: "raw" } });                     // and picks
  expect(document.activeElement).toBe(root);
  await until(() => expect(socket.last().encoding).toBe("raw"));
  await socket.picture(socket.last());
  clock.now = 5000;
  expect(fireEvent.keyDown(document.activeElement, { key: "e", code: "KeyE" })).toBe(false);   // E flies up,
  await tick(5050);
  await until(() => expect(socket.last().camera.focus[1]).toBeGreaterThan(entry.focus[1]));
  fireEvent.keyUp(root, { key: "e", code: "KeyE" });
  expect(pictures.value).toBe("raw");                                           // never picks "Exact..." by its letter
  pictures.focus();                                                             // reached with Tab
  expect(fireEvent.keyDown(pictures, { key: "ArrowUp", code: "ArrowUp" })).toBe(true);        // its own key,
  fireEvent.change(pictures, { target: { value: "jpeg" } });                    // the browser's pick from it
  expect(document.activeElement).toBe(pictures);                                // and the keyboard stays there
  expect(fireEvent.keyDown(pictures, { key: "e", code: "KeyE" })).toBe(true);
});

test("a key pressed while nothing on the page has focus is the viewer's, and its release is heard wherever it lands", async () => {
  const { page, socket, root } = await openScene();
  const entry = await enterFree(page, socket);
  root.blur();
  expect(document.activeElement).toBe(document.body);
  clock.now = 5000;
  expect(fireEvent.keyDown(document.body, { key: "w", code: "KeyW" })).toBe(false);
  await tick(5050);
  await until(() => expect(socket.last().camera.position).not.toEqual(entry.position));
  fireEvent.keyUp(document.body, { key: "w", code: "KeyW" });
  await tick(5100);
  expect(frames.length).toBe(0);
});

// ------------------------------------------------------------------ focus

test("Look at Mario puts the focus point on him at a useful distance and a fallback picture shows its reason", async () => {
  const { page, socket } = await openScene();
  const first = await enterFree(page, socket);
  fireEvent.click(page.getByRole("button", { name: "Look at Mario" }));
  await waitFor(() => expect(socket.last()).toMatchObject({ kind: "pose", frame: 0 }));
  socket.text({ id: socket.last().id, kind: "pose", pose: POSE, reason: null });
  await waitFor(() => expect(socket.last().camera?.focus).toEqual([40, 80 + REFERENCE.marioHeight, -60]));
  const camera = socket.last().camera;
  expect(Math.hypot(...[0, 1, 2].map((i) => camera.position[i] - camera.focus[i])))
    .toBeCloseTo(Math.min(REFERENCE.focusDistance, Math.hypot(300, 1200)), 6);
  expect(first.roll).toBe(0);
  await socket.picture(socket.last(), { camera: "recorded", reason: "This frame has no 3D scene to move around in." });
  await waitFor(() => expect(page.container.textContent).toContain("This frame has no 3D scene to move around in."));
});

test("Set focus asks where the clicked point is and turns the camera to orbit it, staying where it is", async () => {
  const { page, socket, canvas } = await openScene();
  const first = await enterFree(page, socket);
  fireEvent.click(page.getByRole("button", { name: "Set focus" }));
  expect(page.getByRole("button", { name: "Set focus" }).getAttribute("aria-pressed")).toBe("true");
  expect(page.container.querySelector(".scene-lab-hint").textContent).toContain("Click a surface");
  fireEvent.pointerDown(canvas, { button: 0, pointerId: 4, clientX: 160, clientY: 360 });
  await waitFor(() => expect(socket.last()).toMatchObject({ kind: "pick", frame: 0, u: 0.25, v: 0.75 }));
  expect(socket.last().camera).toEqual(first);
  socket.text({ id: socket.last().id, kind: "pick", point: null, distance: null, reason: "Nothing is drawn there." });
  await waitFor(() => expect(page.container.textContent).toContain("Nothing is drawn there."));
  expect(page.getByRole("button", { name: "Set focus" }).getAttribute("aria-pressed")).toBe("true");   // still armed
  fireEvent.pointerDown(canvas, { button: 0, pointerId: 4, clientX: 320, clientY: 240 });
  await waitFor(() => expect(socket.last()).toMatchObject({ kind: "pick", u: 0.5, v: 0.5 }));
  const point = [30, -40, -900];
  socket.text({ id: socket.last().id, kind: "pick", point, distance: Math.hypot(...point), reason: null });
  await waitFor(() => expect(socket.last().kind).toBeUndefined());
  const moved = socket.last().camera, orbit = orbitFromPose(first.focus, first.position);
  close(moved.focus, worldPoint(orbit, point), 6);
  close(moved.position, first.position, 6);
  expect(page.getByRole("button", { name: "Set focus" }).getAttribute("aria-pressed")).toBe("false");
});

// ------------------------------------------------------------------ middle drag, clicks, double-click fly-to

test("a middle-button drag slides exactly as Shift-drag does, never autoscrolls, and lets go like any drag", async () => {
  const { page, socket, root, canvas } = await openScene();
  const first = await enterFree(page, socket);
  expect(fireEvent.pointerDown(canvas, { button: 1, buttons: 4, pointerId: 6, clientX: 100, clientY: 100 })).toBe(false);
  expect(fireEvent.mouseDown(canvas, { button: 1, buttons: 4 })).toBe(false);          // no autoscroll
  move(canvas, { pointerId: 6, buttons: 4, clientX: 130, clientY: 90 });
  await waitFor(() => expect(socket.last().camera.focus).not.toEqual(first.focus));
  const expected = cameraOf(slid(orbitFromPose(first.focus, first.position), 30, -10, REFERENCE));
  close(socket.last().camera.focus, expected.focus, 9);
  close(socket.last().camera.position, expected.position, 9);
  await socket.picture(socket.last());
  fireEvent.keyDown(root, { key: "Escape" });                                              // Esc lets go
  const sent = socket.sent.length;
  move(canvas, { pointerId: 6, buttons: 4, clientX: 200, clientY: 90 });
  await new Promise((resolve) => { setTimeout(resolve, 20); });
  expect(socket.sent.length).toBe(sent);
  fireEvent.pointerDown(canvas, { button: 1, buttons: 4, pointerId: 6, clientX: 100, clientY: 100 });
  fireEvent.pointerUp(canvas, { button: 1, buttons: 0, pointerId: 6 });                    // so does letting go
  move(canvas, { pointerId: 6, buttons: 0, clientX: 160, clientY: 100 });
  await new Promise((resolve) => { setTimeout(resolve, 20); });
  expect(socket.sent.length).toBe(sent);
});

test("a press that moves under the dead zone is a click, not a drag; past it the whole movement counts", async () => {
  const { page, socket, canvas } = await openScene();
  const first = await enterFree(page, socket);
  const sent = socket.sent.length, tiny = DRAG_DEAD_ZONE - 1;
  for (let i = 0; i < 2; i++) {                                                              // the two halves of a double-click
    fireEvent.pointerDown(canvas, { button: 0, buttons: 1, pointerId: 1, clientX: 300, clientY: 200 });
    move(canvas, { pointerId: 1, buttons: 1, clientX: 300 + tiny, clientY: 200 });
    fireEvent.pointerUp(canvas, { button: 0, buttons: 0, pointerId: 1 });
  }
  await new Promise((resolve) => { setTimeout(resolve, 20); });
  expect(socket.sent.length).toBe(sent);
  fireEvent.pointerDown(canvas, { button: 0, buttons: 1, pointerId: 1, clientX: 300, clientY: 200 });
  move(canvas, { pointerId: 1, buttons: 1, clientX: 300 + DRAG_DEAD_ZONE + 6, clientY: 200 });
  await waitFor(() => expect(socket.sent.length).toBe(sent + 1));
  const expected = cameraOf(turned(orbitFromPose(first.focus, first.position), DRAG_DEAD_ZONE + 6, 0, REFERENCE));
  close(socket.last().camera.position, expected.position, 9);
});

// The flight's next animation frame, once the page has asked for one.
async function glideTick(at) {
  await waitFor(() => expect(frames.length).toBeGreaterThan(0));
  await tick(at);
}

async function doubleClick(canvas, x, y) {
  for (let i = 0; i < 2; i++) {
    fireEvent.pointerDown(canvas, { button: 0, buttons: 1, pointerId: 1, clientX: x, clientY: y });
    fireEvent.pointerUp(canvas, { button: 0, buttons: 0, pointerId: 1, clientX: x, clientY: y });
  }
  fireEvent.dblClick(canvas, { button: 0, clientX: x, clientY: y });
}

test("double-click flies to the clicked surface, stops short of it, and ends looking at Mario, eased all the way", async () => {
  const { socket, canvas } = await openScene();
  await socket.picture(socket.sent[0]);
  await doubleClick(canvas, 480, 120);                                                       // from the recorded camera
  await waitFor(() => expect(socket.last()).toMatchObject({ kind: "pose" }));                // entry first
  socket.text({ id: socket.last().id, kind: "pose", pose: POSE, reason: null });
  await waitFor(() => expect(socket.last()).toMatchObject({ kind: "pick", u: 0.75, v: 0.25 }));
  const used = orbitFromPose(POSE.focus, POSE.position, { roll: 0, fov: 45 });
  expect(socket.last().camera).toEqual(cameraOf(used));
  const pick = socket.last();
  await socket.picture(socket.pictures().at(-1));                                           // the entry's own picture
  const point = [200, 150, -1100], hit = worldPoint(used, point);
  const asked = socket.sent.length;
  socket.text({ id: pick.id, kind: "pick", point, distance: Math.hypot(...point), mario: POSE.mario, reason: null });
  const t0 = performance.now();
  await waitFor(() => expect(socket.sent.length).toBe(asked + 1));                         // the first picture goes out at once,
  expect(socket.sent.slice(asked).every((message) => !message.kind)).toBe(true);            // with no second question
  const end = cameraOf(flyToTarget(used, hit, POSE.mario, REFERENCE));
  const seen = [socket.last().camera];
  for (const at of [0.1, 0.5, 1.2]) {                                                        // early, middle, past the end
    await socket.picture(socket.last());
    await glideTick(t0 + at * REFERENCE.flyToMs);
    await waitFor(() => expect(socket.last().camera).not.toEqual(seen.at(-1)));
    seen.push(socket.last().camera);
  }
  close(seen[0].position, cameraOf(used).position, 0);                                     // it starts where it was
  const along = (camera) => Math.hypot(...[0, 1, 2].map((i) => camera.position[i] - seen[0].position[i]))
    / Math.hypot(...[0, 1, 2].map((i) => end.position[i] - cameraOf(used).position[i]));
  expect(along(seen[1])).toBeLessThan(0.03);                                               // eases out of the old view
  expect(along(seen[2])).toBeGreaterThan(0.3);
  expect(along(seen[2])).toBeLessThan(0.7);
  close(seen[3].position, end.position, 9);                                                // and lands on the target
  close(seen[3].focus, [POSE.mario[0], POSE.mario[1] + REFERENCE.marioHeight, POSE.mario[2]], 9);
  expect(Math.hypot(...[0, 1, 2].map((i) => end.position[i] - hit[i]))).toBeCloseTo(REFERENCE.flyToStandoff, 6);
  expect(frames.length).toBe(0);                                                             // and stops asking
  expect(easeInOut(0)).toBe(0);
  expect(easeInOut(1)).toBe(1);
  expect(easeInOut(0.01)).toBeLessThan(0.001);                                               // leaves at rest
  expect(1 - easeInOut(0.99)).toBeLessThan(0.001);                                           // arrives at rest
});

test("without Mario in the pick's answer the flight asks where he is first", async () => {
  const { page, socket, canvas } = await openScene();
  await enterFree(page, socket);
  await doubleClick(canvas, 320, 240);
  await waitFor(() => expect(socket.last()).toMatchObject({ kind: "pick" }));
  socket.text({ id: socket.last().id, kind: "pick", point: [0, 0, -1000], distance: 1000, mario: null, reason: null });
  await waitFor(() => expect(socket.last()).toMatchObject({ kind: "pose" }));
  socket.text({ id: socket.last().id, kind: "pose", pose: POSE, reason: null });
  await waitFor(() => expect(socket.last().kind).toBeUndefined());
});

test("double-clicking the sky says there is nothing there and does not move", async () => {
  const { page, socket, canvas } = await openScene();
  const first = await enterFree(page, socket);
  await doubleClick(canvas, 320, 10);
  await waitFor(() => expect(socket.last()).toMatchObject({ kind: "pick" }));
  socket.text({ id: socket.last().id, kind: "pick", point: null, distance: null, reason: "Nothing is drawn there." });
  await waitFor(() => expect(page.container.textContent).toContain("Nothing there to fly to."));
  await tick(performance.now() + 1000);
  expect(socket.pictures().at(-1).camera).toEqual(first);
});

test("any input stops a double-click's flight where it is", async () => {
  const { page, socket, canvas } = await openScene();
  const first = await enterFree(page, socket);
  await doubleClick(canvas, 320, 240);
  await waitFor(() => expect(socket.last()).toMatchObject({ kind: "pick" }));
  socket.text({ id: socket.last().id, kind: "pick", point: [0, 0, -1000], distance: 1000, mario: POSE.mario,
    reason: null });
  const t0 = performance.now();
  await waitFor(() => expect(socket.last().kind).toBeUndefined());
  await socket.picture(socket.last());
  await glideTick(t0 + REFERENCE.flyToMs * 0.4);
  await waitFor(() => expect(socket.last().camera?.position).not.toEqual(first.position));
  const midway = socket.last().camera;
  await socket.picture(socket.last());
  fireEvent.wheel(canvas, { deltaY: 100 });                                                   // the wheel interrupts
  await waitFor(() => expect(socket.last().camera.position).not.toEqual(midway.position));
  const dollyFromMidway = cameraOf(dollied(orbitFromPose(midway.focus, midway.position), 100, REFERENCE));
  close(socket.last().camera.position, dollyFromMidway.position, 6);                       // from where it stopped
  await socket.picture(socket.last());
  const sent = socket.sent.length;
  await tick(t0 + REFERENCE.flyToMs * 2);
  expect(socket.sent.length).toBe(sent);                                                     // the flight never resumed
});

test("a click maps onto the picture inside its letterbox", () => {
  const canvas = document.createElement("canvas");
  Object.assign(canvas, { width: 640, height: 480 });
  canvas.getBoundingClientRect = () => ({ left: 100, top: 50, width: 800, height: 480 });   // pillarboxed
  expect(pictureFraction(canvas, 150, 100)).toBeNull();                                   // in the bar
  expect(pictureFraction(canvas, 180, 50 + 480)).toEqual({ u: 0, v: 1 });
  expect(pictureFraction(canvas, 500, 290)).toEqual({ u: 0.5, v: 0.5 });
});

// ------------------------------------------------------------------ the rest of the page

test("the hint teaches only what applies now", async () => {
  const { page, socket, canvas } = await openScene();
  expect(hint(page)).toContain("W A S D move the camera from this picture");
  await enterFree(page, socket);
  expect(hint(page)).toContain("hold the right button to look around.");
  expect(hint(page)).toContain("Drag to turn around the focus point");
  fireEvent.pointerDown(canvas, { button: 2, buttons: 2, pointerId: 3 });
  await waitFor(() => expect(hint(page)).toContain(LOOKING));
  expect(hintFor({ freeAvailable: false, scene: { free_camera: { reason: "No state." } } })).toContain("No state.");
});

test("without a free camera the controls say why instead of doing nothing", async () => {
  opened.free_camera = { available: false, reason: "This recording carries no scene state." };
  const { page, socket, root, canvas } = await openScene();
  expect(page.getByRole("button", { name: "Free camera" }).disabled).toBe(true);
  expect(page.getByRole("button", { name: "Look at Mario" }).disabled).toBe(true);
  expect(page.getByRole("button", { name: "Set focus" }).disabled).toBe(true);
  expect(page.container.querySelector(".scene-lab-hint").textContent)
    .toContain("Only the recorded camera is available: This recording carries no scene state.");
  fireEvent.pointerDown(canvas, { button: 2, buttons: 2, pointerId: 3 });
  expect(HTMLCanvasElement.prototype.requestPointerLock).not.toHaveBeenCalled();
  const sent = socket.sent.length;
  expect(fireEvent.keyDown(root, { key: "w", code: "KeyW" })).toBe(true);     // W is nobody's: not taken
  await settled();
  expect(socket.sent.length).toBe(sent);
});

// ------------------------------------------------------------------ the Course/Star/Strategy selector and the ghost panel
// His round 4, items 2 and 3: "We should include our Course/Star/Strategy selector on this screen. By
// default, it opens to whatever the user currently has selected in the practice log (or how they got
// here)"; "A PAGINATED LIST OF GHOSTS THAT WE COULD LOAD INTO THIS SCENE ... If I add a ghost, it should
// immediately appear in the scene without needing to reload." The lab's routes and the socket's
// "ghosts" answer are scripted here with the agreed shapes (interfaces A and B).

const WHOMP = { course_id: 2, course_name: "Whomp's Fortress" };
const TOP_OF_FORTRESS = { ...WHOMP, star_id: 1, star_name: "To the Top of the Fortress" };
const VIEW = { catalog: { courses: [] }, segments: [],
  stars: [{ ...WHOMP, star_id: 4, star_name: "Fall onto the Caged Island",
    attempts: [{ id: 12, igt: "11\"43", strat_tag: "TJ Owlless" }] }],
  target: { kind: "star", ...TOP_OF_FORTRESS, segment_id: null, strat_tag: "Single Jump" } };
const NO_SCENE = "None of your runs of this has a saved scene.";
const stageOf = (strat, attempt) => ({ target: { ...TOP_OF_FORTRESS, segment_id: null, strat, strat_name: strat },
  attempt, time: attempt ? "11\"03" : null, reason: attempt ? null : NO_SCENE, scenes: [] });
// The lab's sessions count up across the whole file, as one server's do: no two tests share a scene.
let sessionSerial = 100;

const queries = (path) => fetches.filter(([url]) => url.startsWith(path))
  .map(([url]) => Object.fromEntries(new URL(url, "http://page").searchParams));
const opens = () => fetches.filter(([url]) => url === "/api/scene-lab/open").map(([, , body]) => JSON.parse(body));
const selectorWords = (page) => {
  const root = page.container.querySelector(".scene-lab-stage-picker");
  return { name: root.querySelector(".scene-lab-target-words strong")?.textContent,
    course: root.querySelector(".scene-lab-target-words small")?.textContent ?? null,
    strat: root.querySelector("select")?.value ?? null,
    note: root.querySelector(".scene-lab-stage-note")?.textContent ?? null };
};

// The viewer arriving with Practice's `view`; the lab answers a stage per strategy (`stage`: Single
// Jump's fastest is attempt 40, TJ Owlless's 44, and Nothing saved has none) and opens any attempt as
// a new session.
const STAGES = { "Single Jump": 40, "TJ Owlless": 44, "Nothing saved": null };
function arrive(view = VIEW, stage = (strat) => stageOf(strat || "Single Jump", STAGES[strat || "Single Jump"])) {
  routes.set("/api/scene-lab/stage", (url) => respond(stage(new URL(url, "http://page").searchParams.get("strat"))));
  routes.set("/api/target/strategies", () => respond({ strategies: [{ name: "Single Jump" }, { name: "TJ Owlless" },
    { name: "Nothing saved" }] }));
  routes.set("/api/scene-lab/open", (url, options) => respond({ ...opened, session: ++sessionSerial,
    attempt: JSON.parse(options.body).attempt }));
  return render(h(SceneViewer, { t: { view } }));
}

// The newest scene's socket, open, its first picture drawn.
async function sceneUp(count) {
  await until(() => expect(sockets.length).toBe(count));
  const socket = sockets[count - 1];
  socket.open();
  await until(() => expect(socket.pictures().length).toBeGreaterThan(0));
  await socket.picture(socket.pictures()[0]);
  return socket;
}

test("the selector starts on the scene's own target, else the Ghost button's attempt, else Practice's, else nothing", () => {
  const lab = { ...WHOMP, star_id: 3, strat: "Lab strat" };
  expect(stageDefault({ attempt: 12, target: lab }, VIEW)).toBe(lab);
  expect(stageDefault({ attempt: 12 }, VIEW)).toEqual({ ...WHOMP, star_id: 4, star_name: "Fall onto the Caged Island",
    strat: "TJ Owlless" });
  expect(stageDefault({ name: "lab.sm64scene" }, VIEW)).toBeNull();                  // a lab recording: never Practice's
  expect(stageDefault(null, VIEW)).toEqual({ ...TOP_OF_FORTRESS, strat: "Single Jump" });
  expect(stageDefault(null, { target: { kind: "star", course_id: null, star_id: null } })).toBeNull();
  expect(stageDefault(null, { target: { kind: "segment", segment_id: 9, strat_tag: null } })).toEqual(
    { segment_id: 9, strat: null });
  expect(stageDefault(null, undefined)).toBeNull();
  expect(String(targetQuery({ course_id: 2, star_id: 1, strat: "Single Jump" }))).toBe("course=2&star=1&strat=Single+Jump");
  expect(String(targetQuery({ course_id: null, segment_id: 9, strat: null }))).toBe("segment=9");
});

test("arriving with no scene open opens the stage of what Practice has selected, paused, and the selector names it", async () => {
  const page = arrive();
  const socket = await sceneUp(1);
  expect(queries("/api/scene-lab/stage")).toEqual([{ course: "2", star: "1", strat: "Single Jump" }]);
  expect(opens()).toEqual([{ attempt: 40 }]);
  await until(() => expect(selectorWords(page)).toEqual({ name: "To the Top of the Fortress", course: "Whomp's Fortress",
    strat: "Single Jump", note: null }));
  expect(page.queryByRole("button", { name: "Open" })).toBeNull();                 // the lab's file list stays away
  expect(socket.pictures()[0]).toMatchObject({ frame: 0, camera: null });
  await settled();
  expect(page.getByRole("button", { name: "Play" })).toBeTruthy();
});

test("with nothing selected anywhere the selector asks for a choice and opens nothing", async () => {
  const page = arrive({ ...VIEW, target: { kind: "star", course_id: null, star_id: null } });
  await until(() => expect(selectorWords(page).note).toBe(STAGE_WORDS.prompt));
  await settled();
  expect(queries("/api/scene-lab/stage")).toEqual([]);
  expect(selectorWords(page).name).toBe("Choose a star");
  expect(page.getByRole("button", { name: "Open" })).toBeTruthy();                  // the lab's own recordings
});

test("a strategy picked in the selector opens its stage, or says why there is none and closes the other's scene", async () => {
  const page = arrive();
  await sceneUp(1);
  const strategy = await until(() => page.getByRole("combobox", { name: "Strategy" }));
  fireEvent.change(strategy, { target: { value: "Nothing saved" } });
  await until(() => expect(selectorWords(page).note).toBe(NO_SCENE));
  expect(queries("/api/scene-lab/stage").at(-1)).toEqual({ course: "2", star: "1", strat: "Nothing saved" });
  await until(() => expect(fetches.some(([url, method]) => url === "/api/scene-lab/close" && method === "POST")).toBe(true));
  expect(page.container.querySelector(".scene-lab-player")).toBeNull();
  expect(selectorWords(page).strat).toBe("Nothing saved");
  expect(page.queryByRole("button", { name: "Open" })).toBeNull();
  expect(page.container.textContent).not.toContain("closed");                       // only the lab's reason
  fireEvent.change(page.getByRole("combobox", { name: "Strategy" }), { target: { value: "TJ Owlless" } });
  await sceneUp(2);
  expect(opens()).toEqual([{ attempt: 40 }, { attempt: 44 }]);
  expect(selectorWords(page)).toMatchObject({ strat: "TJ Owlless", note: null });
});

test("the selector's course picker is Practice's grid: a star picked there opens its stage, and the dialog keeps its keys", async () => {
  const catalog = { courses: [{ id: 2, name: "Whomp's Fortress",
    stars: ["Chip off Whomp's Block", "To the Top of the Fortress", "Shoot into the Wild Blue"] }] };
  const page = arrive({ ...VIEW, catalog });
  const socket = await sceneUp(1);
  await until(() => expect(selectorWords(page).name).toBe("To the Top of the Fortress"));
  fireEvent.click(page.container.querySelector(".scene-lab-target"));
  const dialog = await until(() => page.getByRole("dialog"));
  const sent = socket.sent.length;
  const course = within(dialog).getByRole("button", { name: /Whomp's Fortress/ });
  course.focus();
  for (const [key, code] of [["w", "KeyW"], ["ArrowRight", "ArrowRight"], [".", "Period"], [" ", "Space"]]) {
    fireEvent.keyDown(course, { key, code });
  }
  await tick(clock.now + 100);
  expect(socket.sent.length).toBe(sent);                                            // no flight, no step, no Play
  fireEvent.click(course);
  fireEvent.click(await until(() => within(page.getByRole("dialog")).getByRole("button", { name: /Shoot into the Wild Blue/ })));
  await until(() => expect(queries("/api/scene-lab/stage").length).toBe(2));
  expect(queries("/api/scene-lab/stage")[1]).toEqual({ course: "2", star: "2" });   // no strategy: the lab's choice
  expect(page.queryByRole("dialog")).toBeNull();
});

test("from the Ghost button the selector names that attempt's target at once, then the lab's own", async () => {
  const lab = { ...WHOMP, star_id: 4, star_name: "Fall onto the Caged Island (lab)", segment_id: null, strat: "TJ Owlless" };
  routes.set("/api/scene-lab/open", () => respond({ ...opened, session: ++sessionSerial, attempt: 12, target: lab }));
  routes.set("/api/target/strategies", () => respond({ strategies: [{ name: "TJ Owlless" }] }));
  const page = render(h(SceneViewer, { t: { view: VIEW, sceneLab: { intent: { attempt: 12 }, clearIntent: () => {} } } }));
  expect(selectorWords(page)).toMatchObject({ name: "Fall onto the Caged Island", course: "Whomp's Fortress" });
  await sceneUp(1);
  await until(() => expect(selectorWords(page).name).toBe("Fall onto the Caged Island (lab)"));
  expect(queries("/api/scene-lab/stage")).toEqual([]);                              // the Ghost button's attempt is the stage
});

// The lab's lists: 23 of yours (the first is the stage's own run), 12 top ghosts per strategy, one
// player found, and that player's three.
function ghostRoutes() {
  const yours = Array.from({ length: 23 }, (_, i) => ({ ref: `yours-${i}`, player: "You", time: `${11 + i}"03`,
    frames: 330 + i, rank: null, recorded_utc: `2026-10-${String(1 + i).padStart(2, "0")}T12:00:00Z`, more: 0,
    yours: true, attempt: i === 0 ? 40 : 900 + i }));
  routes.set("/api/scene-lab/ghosts", (url) => {
    const query = new URL(url, "http://page").searchParams, page = Number(query.get("page"));
    const rows = {
      yours: () => (query.get("sort") === "newest" ? [...yours].reverse() : yours),
      top: () => Array.from({ length: 12 }, (_, i) => ({ ref: `top-${query.get("strat")}-${i}`, player: `Player ${i + 1}`,
        time: `10"${String(i).padStart(2, "0")}`, rank: i + 1, more: 0, yours: false })),
      search: () => [{ ref: "best-sully", player: "SullyLikesBigChungus", time: "10\"50", rank: null, more: 2 }],
      player: () => [0, 1, 2].map((i) => ({ ref: `${query.get("player")}-${i}`, player: query.get("player"),
        time: `10"5${i}`, rank: null, more: 0 })),
    }[query.get("section")]();
    return respond({ items: rows.slice((page - 1) * 10, page * 10), page,
      pages: Math.max(1, Math.ceil(rows.length / 10)), reason: null });
  });
}
const ghostRow = (page, ref) => page.container.querySelector(`li[data-ref="${ref}"]`);
const ghostsSent = (socket) => socket.sent.filter((message) => message.kind === "ghosts");
const asked = (section) => queries("/api/scene-lab/ghosts").filter((query) => query.section === section);

test("the ghost panel pages and sorts your ghosts, the stage's own run is the stage, and Top ghosts has its own strategy", async () => {
  ghostRoutes();
  const page = arrive();
  await sceneUp(1);
  await until(() => expect(ghostRow(page, "yours-1")).not.toBeNull());
  expect(asked("yours")).toEqual([{ section: "yours", course: "2", star: "1", strat: "Single Jump", sort: "fastest", page: "1" }]);
  expect(ghostRow(page, "yours-0").textContent).toContain("The stage");
  expect(ghostRow(page, "yours-0").querySelector("button")).toBeNull();
  const yours = page.getByRole("region", { name: "Your ghosts" });
  expect(yours.textContent).toContain("Page 1");
  fireEvent.click(within(yours).getByRole("button", { name: "Next" }));
  await until(() => expect(yours.querySelector("li").dataset.ref).toBe("yours-10"));
  expect(asked("yours").at(-1)).toMatchObject({ page: "2", sort: "fastest" });
  fireEvent.change(within(yours).getByRole("combobox", { name: "Sort your ghosts" }), { target: { value: "newest" } });
  await until(() => expect(yours.querySelector("li").dataset.ref).toBe("yours-22"));
  expect(asked("yours").at(-1)).toMatchObject({ page: "1", sort: "newest" });

  const top = page.getByRole("region", { name: "Top ghosts" });
  await until(() => expect(top.querySelectorAll("li").length).toBe(10));
  expect([...top.querySelectorAll(".scene-lab-ghost-rank")].map((rank) => rank.textContent).slice(0, 3)).toEqual(["#1", "#2", "#3"]);
  fireEvent.change(within(top).getByRole("combobox", { name: "Strategy for top ghosts" }), { target: { value: "TJ Owlless" } });
  await until(() => expect(top.querySelector("li").dataset.ref).toBe("top-TJ Owlless-0"));
  expect(asked("top").at(-1)).toMatchObject({ strat: "TJ Owlless", page: "1" });
  expect(selectorWords(page).strat).toBe("Single Jump");                              // the stage keeps its strategy
  expect(queries("/api/scene-lab/stage").length).toBe(1);
});

// His rule for this round (event d8d7a140): keep database interactions cheap. The panel asks for a
// page only when he changes something, and each page once in a scene's life.
test("each page is asked once in a scene's life: paging back, sorting back, another strategy and back, and coming back ask nothing", async () => {
  ghostRoutes();
  const first = arrive();
  await sceneUp(1);
  await until(() => expect(ghostRow(first, "yours-1")).not.toBeNull());
  await until(() => expect(asked("top").length).toBe(1));
  const yours = first.getByRole("region", { name: "Your ghosts" });
  fireEvent.click(within(yours).getByRole("button", { name: "Next" }));
  await until(() => expect(yours.querySelector("li").dataset.ref).toBe("yours-10"));
  fireEvent.click(within(yours).getByRole("button", { name: "Previous" }));
  await until(() => expect(yours.querySelector("li").dataset.ref).toBe("yours-0"));
  const sort = within(yours).getByRole("combobox", { name: "Sort your ghosts" });
  fireEvent.change(sort, { target: { value: "newest" } });
  await until(() => expect(yours.querySelector("li").dataset.ref).toBe("yours-22"));
  fireEvent.change(sort, { target: { value: "fastest" } });
  await until(() => expect(yours.querySelector("li").dataset.ref).toBe("yours-0"));
  const top = first.getByRole("region", { name: "Top ghosts" }), strategy = within(top).getByRole("combobox");
  fireEvent.change(strategy, { target: { value: "TJ Owlless" } });
  await until(() => expect(top.querySelector("li").dataset.ref).toBe("top-TJ Owlless-0"));
  fireEvent.change(within(top).getByRole("combobox"), { target: { value: "Single Jump" } });
  await until(() => expect(top.querySelector("li").dataset.ref).toBe("top-Single Jump-0"));
  expect(asked("yours").map((query) => [query.sort, query.page])).toEqual([["fastest", "1"], ["fastest", "2"], ["newest", "1"]]);
  expect(asked("top").map((query) => query.strat)).toEqual(["Single Jump", "TJ Owlless"]);
  const before = asked("yours").length + asked("top").length;
  await tick(clock.now + 60000);                                                     // nothing asks on a timer
  await settled();
  expect(asked("yours").length + asked("top").length).toBe(before);

  const scene = { ...opened, session: sessionSerial, attempt: 40 };
  cleanup();
  routes.set("/api/scene-lab/status", () => respond({ ready: true, problems: [], hold: null, session: scene, ended: null,
    packages: [] }));
  const again = render(h(SceneViewer, { t: { view: VIEW } }));                      // the same scene, come back to
  await until(() => expect(sockets.length).toBe(2));
  sockets[1].open();
  await until(() => expect(ghostRow(again, "yours-1")).not.toBeNull());
  await settled();
  expect(asked("yours").length + asked("top").length).toBe(before);
});

test("the lab's note on a list is one quiet line above it, and its own ghosts still list", async () => {
  ghostRoutes();
  const lists = routes.get("/api/scene-lab/ghosts");
  routes.set("/api/scene-lab/ghosts", async (url, options) => {
    const answer = await (await lists(url, options)).json();
    return respond({ ...answer, note: "Sign in to see other players' ghosts." });
  });
  const page = arrive();
  await sceneUp(1);
  const top = await until(() => page.getByRole("region", { name: "Top ghosts" }));
  await until(() => expect(within(top).getByRole("note").textContent).toBe("Sign in to see other players' ghosts."));
  expect(top.querySelectorAll("li").length).toBe(10);
});

test("Find a player asks once typing stops or on Enter, lists each player's best, and more opens their slower ghosts", async () => {
  ghostRoutes();
  const page = arrive();
  await sceneUp(1);
  SEARCH_WAIT.ms = 30;
  const find = await until(() => page.getByRole("searchbox", { name: "Find a player by name" }));
  for (const typed of ["s", "su", "sul"]) fireEvent.input(find, { target: { value: typed } });
  await until(() => expect(ghostRow(page, "best-sully")).not.toBeNull());
  expect(asked("search").map((query) => query.q)).toEqual(["sul"]);
  SEARCH_WAIT.ms = 60000;
  fireEvent.input(find, { target: { value: "sully" } });
  fireEvent.keyDown(find, { key: "Enter" });                                         // Enter asks at once
  await until(() => expect(asked("search").map((query) => query.q)).toEqual(["sul", "sully"]));
  fireEvent.input(find, { target: { value: "sul" } });
  fireEvent.keyDown(find, { key: "Enter" });
  await settled();
  expect(asked("search").length).toBe(2);                                            // a name asked before costs nothing
  expect(asked("search")[0]).toMatchObject({ course: "2", star: "1", strat: "Single Jump", page: "1" });
  fireEvent.click(within(ghostRow(page, "best-sully")).getByRole("button", { name: "2 more" }));
  await until(() => expect(ghostRow(page, "SullyLikesBigChungus-2")).not.toBeNull());
  expect(asked("player").at(-1)).toMatchObject({ player: "SullyLikesBigChungus", page: "1" });
  fireEvent.click(within(ghostRow(page, "best-sully")).getByRole("button", { name: "Fewer" }));
  await until(() => expect(ghostRow(page, "SullyLikesBigChungus-2")).toBeNull());
});

test("Add and Remove send the whole list; the lab's answer selects rows, gives a refusal its reason, and the picture is asked again", async () => {
  ghostRoutes();
  const page = arrive();
  const socket = await sceneUp(1);
  await until(() => expect(ghostRow(page, "top-Single Jump-1")).not.toBeNull());
  const pictures = socket.pictures().length;
  fireEvent.click(within(ghostRow(page, "top-Single Jump-0")).getByRole("button", { name: /^Add/ }));
  await until(() => expect(ghostsSent(socket).length).toBe(1));
  expect(ghostsSent(socket)[0]).toMatchObject({ session: sessionSerial, set: [{ ref: "top-Single Jump-0", label: "Player 1" }] });
  const adding = within(ghostRow(page, "top-Single Jump-0")).getByRole("button");         // loading until answered
  expect([adding.textContent.trim(), adding.disabled]).toEqual(["Adding...", true]);
  expect(ghostRow(page, "top-Single Jump-0").classList.contains("is-shown")).toBe(false);
  await settled();
  expect(socket.pictures().length).toBe(pictures);                                       // nothing redrawn before
  socket.text({ id: ghostsSent(socket)[0].id, kind: "ghosts", shown: ["top-Single Jump-0"], refused: [] });
  await until(() => expect(socket.pictures().length).toBe(pictures + 1));
  expect(socket.pictures().at(-1)).toMatchObject({ frame: 0 });                            // the picture on screen again
  await socket.picture(socket.pictures().at(-1));

  fireEvent.click(within(ghostRow(page, "top-Single Jump-1")).getByRole("button", { name: /^Add/ }));
  await until(() => expect(ghostsSent(socket).length).toBe(2));
  expect(ghostsSent(socket)[1].set.map((ghost) => ghost.ref)).toEqual(["top-Single Jump-0", "top-Single Jump-1"]);
  socket.text({ id: ghostsSent(socket)[1].id, kind: "ghosts", shown: ["top-Single Jump-0"],
    refused: [{ ref: "top-Single Jump-1", reason: "Recorded on another version of the course." }] });
  await until(() => expect(ghostRow(page, "top-Single Jump-1").textContent).toContain("Recorded on another version of the course."));
  expect(ghostRow(page, "top-Single Jump-1").classList.contains("is-shown")).toBe(false);
  expect(within(ghostRow(page, "top-Single Jump-1")).getByRole("button", { name: /^Add/ })).toBeTruthy();

  fireEvent.click(within(ghostRow(page, "top-Single Jump-0")).getByRole("button", { name: /^Remove/ }));
  fireEvent.click(within(ghostRow(page, "yours-2")).getByRole("button", { name: /^Add/ }));
  await until(() => expect(ghostsSent(socket).length).toBe(4));
  expect(ghostsSent(socket)[2].set).toEqual([]);
  expect(ghostsSent(socket)[3].set.map((ghost) => ghost.ref)).toEqual(["yours-2"]);
  socket.text({ id: ghostsSent(socket)[3].id, kind: "ghosts", shown: ["yours-2"], refused: [] });
  socket.text({ id: ghostsSent(socket)[2].id, kind: "ghosts", shown: [], refused: [] });     // an older list's, late
  await settled();
  expect(ghostRow(page, "yours-2").classList.contains("is-shown")).toBe(true);
  expect(ghostRow(page, "top-Single Jump-0").classList.contains("is-shown")).toBe(false);
  expect(page.getByRole("region", { name: "Ghosts" }).textContent).toContain("1 in the scene.");
  fireEvent.click(page.getByRole("button", { name: "Remove all" }));
  await until(() => expect(ghostsSent(socket).at(-1).set).toEqual([]));
});

test("a ghost in the scene has its own Mute; a recording without sound says its ghosts play silent too", async () => {
  ghostRoutes();
  opened = { ...opened, audio: { available: true, rate: 48000, block_samples: 9600, total_samples: 4 * 9600, blocks: 4,
    codec: "stored", shift_ms: 0, samples: FRAMES.map((_, k) => k * 1600), lost: [] } };
  routes.set("/api/scene-lab/audio/", () => new Promise(() => {}));        // its blocks on their way
  const page = arrive();
  const socket = await sceneUp(1);
  await until(() => expect(ghostRow(page, "yours-2")).not.toBeNull());
  expect(within(ghostRow(page, "yours-2")).queryByRole("button", { name: /^Mute/ })).toBeNull();   // not in the scene
  fireEvent.click(within(ghostRow(page, "yours-2")).getByRole("button", { name: /^Add/ }));
  await until(() => expect(ghostsSent(socket).length).toBe(1));
  socket.text({ id: ghostsSent(socket)[0].id, kind: "ghosts", shown: ["yours-2"], refused: [] });
  const mute = await until(() => within(ghostRow(page, "yours-2")).getByRole("button", { name: /^Mute/ }));
  expect([mute.getAttribute("aria-pressed"), mute.disabled, mute.title]).toEqual(["false", false,
    "Mute this ghost's sound effects"]);
  fireEvent.click(mute);
  await until(() => expect(within(ghostRow(page, "yours-2")).getByRole("button", { name: /^Mute/ })
    .getAttribute("aria-pressed")).toBe("true"));
  expect(fetches.some(([url]) => url.includes("track=ghost%3Ayours-2"))).toBe(true);
  routes.set("/api/scene-lab/audio/", () => respond({ detail: "This ghost's sound could not be made." }, 404));
  fireEvent.click(within(ghostRow(page, "yours-2")).getByRole("button", { name: /^Remove/ }));
  await until(() => expect(ghostsSent(socket).length).toBe(2));
  socket.text({ id: ghostsSent(socket)[1].id, kind: "ghosts", shown: [], refused: [] });
  fireEvent.click(within(ghostRow(page, "yours-3")).getByRole("button", { name: /^Add/ }));
  await until(() => expect(ghostsSent(socket).length).toBe(3));
  socket.text({ id: ghostsSent(socket)[2].id, kind: "ghosts", shown: ["yours-3"], refused: [] });
  const failed = await until(() => {                                          // a ghost whose sound failed says so
    const button = within(ghostRow(page, "yours-3")).getByRole("button", { name: /^Mute/ });
    expect(button.disabled).toBe(true);
    return button;
  });
  expect(failed.title).toBe("This ghost's sound could not be played.");
});

test("the same scene left and come back to draws its ghosts again; a new stage starts with none", async () => {
  ghostRoutes();
  const first = arrive();
  const socket = await sceneUp(1);
  await until(() => expect(ghostRow(first, "yours-2")).not.toBeNull());
  fireEvent.click(within(ghostRow(first, "yours-2")).getByRole("button", { name: /^Add/ }));
  await until(() => expect(ghostsSent(socket).length).toBe(1));
  socket.text({ id: ghostsSent(socket)[0].id, kind: "ghosts", shown: ["yours-2"], refused: [] });
  await until(() => expect(ghostRow(first, "yours-2").classList.contains("is-shown")).toBe(true));
  const scene = { ...opened, session: sessionSerial, attempt: 40 };
  cleanup();
  routes.set("/api/scene-lab/status", () => respond({ ready: true, problems: [], hold: null, session: scene, ended: null,
    packages: [] }));
  const again = render(h(SceneViewer, { t: { view: VIEW } }));
  await until(() => expect(sockets.length).toBe(2));
  sockets[1].open();
  await until(() => expect(ghostsSent(sockets[1]).length).toBe(1));
  expect(ghostsSent(sockets[1])[0].set.map((ghost) => ghost.ref)).toEqual(["yours-2"]);
  await until(() => expect(ghostRow(again, "yours-2")?.classList.contains("is-shown")).toBe(true));
  routes.delete("/api/scene-lab/status");
  fireEvent.change(await until(() => again.getByRole("combobox", { name: "Strategy" })), { target: { value: "TJ Owlless" } });
  const fresh = await sceneUp(3);
  await settled();
  expect(ghostsSent(fresh)).toEqual([]);
  await until(() => expect(ghostRow(again, "yours-2")).not.toBeNull());
  expect(ghostRow(again, "yours-2").classList.contains("is-shown")).toBe(false);
});

test("a closed scene shows the server's reason and offers Open again", async () => {
  const { page, socket } = await openScene();
  socket.text({ id: socket.sent[0].id, error: "Closed after a while without use, to free the renderer.", closed: true });
  await waitFor(() => expect(page.container.textContent).toContain("Closed after a while without use"));
  expect(page.getByRole("button", { name: "Open" })).toBeTruthy();
});

test("Tune controls changes how the camera moves on the next movement and SAVE sends only what changed", async () => {
  const { page, socket, canvas } = await openScene();
  await enterFree(page, socket);
  fireEvent.click(page.getByRole("button", { name: "Tune controls" }));
  const panel = page.getByRole("region", { name: "Tune controls" });
  const input = panel.querySelector("#orbitPerPixel");
  expect(Number(input.value)).toBe(REFERENCE.orbitPerPixel);
  fireEvent.change(input, { target: { value: "0.02" } });
  const before = orbitFromPose(socket.last().camera.focus, socket.last().camera.position);
  fireEvent.pointerDown(canvas, { button: 0, pointerId: 1, clientX: 100, clientY: 100 });
  move(canvas, { pointerId: 1, clientX: 110, clientY: 100 });
  await waitFor(() => expect(socket.last().camera.position).not.toEqual(cameraOf(before).position));
  const after = orbitFromPose(socket.last().camera.focus, socket.last().camera.position);
  expect(after.yaw - before.yaw).toBeCloseTo(-10 * 0.02, 9);
  fireEvent.click(page.getByRole("button", { name: "SAVE" }));
  await waitFor(() => expect(fetches.some(([url]) => url === "/api/tuning/scenecamera")).toBe(true));
  const [, method, body] = fetches.find(([url]) => url === "/api/tuning/scenecamera");
  expect(method).toBe("POST");
  expect(Object.keys(JSON.parse(body).values)).toContain("orbitPerPixel");
});

// ------------------------------------------------------------------ the tuning reaches every movement

test("every movement reads its tuning: the shipped values when none is given, a change moves the result", () => {
  const orbit = orbitFromPose(POSE.focus, POSE.position, { roll: 500, fov: 45 });
  const tilted = orbitFromPose(POSE.focus, POSE.position, { roll: 500, fov: 45 });
  const cases = [
    ["orbitPerPixel", (t) => turned(orbit, 7, 3, t)], ["steepestDegrees", (t) => turned(orbit, 0, 1e4, t)],
    ["levelPerPixel", (t) => turned(tilted, 1, 0, t)], ["panPerPixel", (t) => slid(orbit, 7, 3, t)],
    ["dollyPerWheel", (t) => dollied(orbit, 50, t)], ["closestDistance", (t) => dollied(orbit, -1e6, t)],
    ["farthestDistance", (t) => dollied(orbit, 1e6, t)], ["lookPerPixel", (t) => looked(orbit, 7, 3, t)],
    ["flightSpeed", (t) => flightSpeed(1, {}, t)], ["fastMultiplier", (t) => flightSpeed(1, { fast: true }, t)],
    ["slowMultiplier", (t) => flightSpeed(1, { slow: true }, t)], ["speedPerNotch", (t) => speedStepped(1, 1, t)],
    ["levelPerSecond", (t) => flown(tilted, { forward: 1 }, 0.01, 10, t)],
    ["focusDistance", (t) => lookingAt(orbit, [0, 0, 0], t)], ["marioHeight", (t) => lookingAt(orbit, [0, 0, 0], t)],
    ["flyToStandoff", (t) => flyToTarget(orbit, [100, 0, -200], [0, 0, 0], t)],
    ["flyToMs", (t) => glideAt(orbit, lookingAt(orbit, [0, 0, 0]), 150, t)],
  ];
  expect(new Set(cases.map(([key]) => key))).toEqual(new Set(Object.keys(SCENE_CAMERA_DEFAULTS)));
  for (const [key, run] of cases) {
    expect(run(undefined)).toEqual(run(SCENE_CAMERA_DEFAULTS));
    const nudged = { ...SCENE_CAMERA_DEFAULTS, [key]: SCENE_CAMERA_DEFAULTS[key] * 0.9 + (key === "steepestDegrees" ? 0 : 0.001) };
    expect(run(nudged), key).not.toEqual(run(SCENE_CAMERA_DEFAULTS));
  }
});
