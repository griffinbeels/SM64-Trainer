// @vitest-environment jsdom
// The Ghost Lab page (the Scene viewer until round 9, ui/components/sceneviewer.js) against a scripted
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
import { cameraOf, dollied, easeInOut, flightSpeed, flown, flyToTarget, focusedOn, fovOf, glideAt, lensOf, lookingAt,
  looked, orbitFromPose, rollOf, slid, speedStepped, tiltAndLens, tiltOf, tilted, turned, wheelLens, worldPoint,
} from "../../src/sm64_events/ui/components/scenecamera.js";
import { lensAt, lensPosition } from "../../src/sm64_events/ui/components/scenelens.js";
import { SCENE_CAMERA_DEFAULTS, sceneCameraTuning, setSceneCameraTuning } from "../../src/sm64_events/ui/scenecameratuning.js";
import { stageDefault, targetQuery } from "../../src/sm64_events/ui/components/scenestage.js";
import { rigRequest, rigTuning, startRig } from "../../src/sm64_events/ui/components/scenerigs.js";
import { SEARCH_WAIT, viewName } from "../../src/sm64_events/ui/components/sceneghosts.js";

const REFERENCE = { orbitPerPixel: 0.01, steepestDegrees: 80, levelPerPixel: 0.1, panPerPixel: 0.002,
  dollyPerWheel: 0.002, closestDistance: 40, farthestDistance: 20000, flightSpeed: 1000, lookPerPixel: 0.004,
  fastMultiplier: 5, slowMultiplier: 0.2, speedPerNotch: 1.5, levelPerSecond: 30, flyToStandoff: 300, flyToMs: 200,
  marioHeight: 100, tiltPerSecond: 60, lensPerSecond: 3, lensPerWheel: 0.002 };
const FRAMES = Array.from({ length: 5 }, (_, i) => ({ level: 9, area: 1, game_frame: 1000 + i }));
const POSE = { focus: [10, 200, -30], position: [10, 500, 1170], roll: 0, fov: 45, mario: [40, 80, -60] };
const PER_DEGREE = 65536 / 360;
// The running game's nine Usamune slots as GET /api/scene-lab/slot-sets answers outside a course, all of them
// empty: the save-state strip asks for it while it shows.
const SLOTS_EMPTY = { context: null, game: { build: "us" }, slots: Array.from({ length: 9 }, (_, i) => ({
  slot: i + 1, set: null, state: null, translated: null, under: null, picture: null, loaded_last: false })) };
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
    if (url === "/api/scene-lab/slot-sets") return respond(SLOTS_EMPTY);
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

// The bar's More menu (the lab's recordings, picture quality, the numbers, the keys), open.
function openMore(page) {
  const more = page.getByRole("button", { name: "More" });
  if (more.getAttribute("aria-expanded") !== "true") fireEvent.click(more);
  return within(page.container.querySelector(".ghost-lab-more-pop"));
}

// The tray's Camera tab, chosen: every camera control is in it.
function cameraTab(page) {
  fireEvent.click(page.getByRole("tab", { name: /Camera/ }));
  return page;
}

// A lab package opened from the More menu (closed again after), its first picture asked for; the tray on
// `tab` ("camera": every camera control is in it, "ghosts": the tray's default).
async function openScene({ tab = "camera" } = {}) {
  const page = render(h(SceneViewer, {}));
  const button = await until(() => {
    const found = openMore(page).getByRole("button", { name: "Open" });
    expect(found.disabled).toBe(false);
    return found;
  });
  fireEvent.click(button);
  await until(() => expect(sockets.length).toBe(1));
  const socket = sockets[0];
  socket.open();
  await until(() => expect(socket.sent.length).toBe(1));
  fireEvent.click(page.getByRole("button", { name: "More" }));          // shut again
  if (tab === "camera") cameraTab(page);
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
  expect(openMore(page).getByRole("button", { name: "Open" })).toBeTruthy();   // the packages are still there to pick
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
  expect(page.container.querySelector(".scene-lab-frame").title).toContain("level 9, area 1, game frame 1003");
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
  await until(() => expect(page.container.textContent).toContain("Frame 1 of 3+"));
  socket.text({ kind: "grown", session: 2, from: 3, frames: FRAMES.slice(3), growing: false });   // another scene's
  socket.text({ kind: "grown", session: 1, from: 3, frames: FRAMES.slice(3, 4), growing: true });
  await until(() => expect(page.container.textContent).toContain("Frame 1 of 4+"));
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
  expect(page.container.querySelector(".scene-lab-frame").textContent).not.toContain("+");   // whole now
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
  fireEvent.click(openMore(page).getByRole("button", { name: "Tune controls" }));
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
  // The bar says whose run it is (a star, the flag, who, the time); the scene's whole title is its hover.
  const title = () => lab.page.container.querySelector(".ghost-lab-watching")?.title;
  await until(() => expect(title()).toBe("Recording"));
  // The bar itself, without the More menu (which lists the lab's own recordings by file, as their picker).
  const bar = lab.page.container.querySelector(".ghost-lab-top").cloneNode(true);
  bar.querySelector(".ghost-lab-more-pop").remove();
  expect(lab.page.container.querySelector(".ghost-lab-watching").textContent).toBe("★Lab recording");
  expect(bar.textContent).not.toContain("sm64scene");
  expect(bar.textContent).not.toContain("Scene viewer");                          // no title row
  expect(lab.page.container.textContent).not.toContain("Scene viewer");
  cleanup();
  sockets = [];
  opened = { ...opened, attempt: 12 };
  const view = { catalog: { courses: [] }, segments: [], stars: [{ course_name: "Whomp's Fortress",
    star_name: "Shoot into the Wild Blue", attempts: [{ id: 11, igt: "12\"40" },
      { id: 12, igt: "11\"03", rta: "11\"60", strat_tag: "Single Jump" }] }] };
  const fallback = render(h(SceneViewer, { t: { ...fromGhostButton(12).t, view } }));
  await until(() => expect(sockets.length).toBe(1));
  await until(() => expect(fallback.container.querySelector(".ghost-lab-watching")?.title)
    .toBe("Whomp's Fortress · Shoot into the Wild Blue · Single Jump · 11\"03"));
  expect(fallback.container.querySelector(".ghost-lab-watching").textContent).toBe("★You11\"03");   // his run, its time
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

// What the picture says while you act on it (the cue): its keys while looking around, the click while
// choosing a point. The Ghost Lab has no hint paragraph; the More menu lists every key.
const hint = (page) => page.container.querySelector(".scene-lab-cue")?.textContent ?? "";
const LOOKING = "W A S D move · Q E down, up";

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
  expect(hint(page)).not.toContain(LOOKING);
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
  fireEvent.click(openMore(page).getByRole("button", { name: "Tune controls" }));
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

// His report, 2026-10-27: "It should only change the camera look direction, NOT the camera position.
// Same position, just looking at mario." From the recorded camera too: it enters Free camera at the
// recorded place (no jump) and turns there; its roll and field of view stay.
test("Look at Mario turns the camera to face him where it stands, keeping its roll and field of view", async () => {
  const { page, socket } = await openScene();
  await socket.picture(socket.sent[0]);
  const tilted = { ...POSE, roll: 900, fov: 52 };
  fireEvent.click(page.getByRole("button", { name: "Look at Mario" }));
  await until(() => expect(socket.last()).toMatchObject({ kind: "pose", frame: 0 }));
  const entry = socket.last().id;
  socket.text({ id: entry, kind: "pose", pose: tilted, reason: null });
  await until(() => expect([socket.last().kind, socket.last().id > entry]).toEqual(["pose", true]));
  socket.text({ id: socket.last().id, kind: "pose", pose: tilted, reason: null });
  await until(() => expect(socket.last().camera?.focus).toEqual([40, 80 + REFERENCE.marioHeight, -60]));
  const camera = socket.last().camera, target = [40, 80 + REFERENCE.marioHeight, -60];
  close(camera.position, POSE.position);
  const facing = [0, 1, 2].map((i) => camera.focus[i] - camera.position[i]);
  const toMario = [0, 1, 2].map((i) => target[i] - POSE.position[i]);
  close(facing.map((value) => value / Math.hypot(...facing)), toMario.map((value) => value / Math.hypot(...toMario)));
  expect([camera.roll, camera.fov]).toEqual([900, 52]);
  await socket.picture(socket.last(), { camera: "recorded", reason: "This frame has no 3D scene to move around in." });
  await waitFor(() => expect(page.container.textContent).toContain("This frame has no 3D scene to move around in."));
});

test("lookingAt keeps the camera's place, roll and field of view, faces the point, and stays put on it", () => {
  const orbit = { ...orbitFromPose(POSE.focus, POSE.position, { roll: -300, fov: 60 }) };
  const turnedTo = lookingAt(orbit, [500, -40, 900], REFERENCE), camera = cameraOf(turnedTo);
  close(camera.position, cameraOf(orbit).position);
  close(camera.focus, [500, -40 + REFERENCE.marioHeight, 900]);
  expect([camera.roll, camera.fov]).toEqual([-300, 60]);
  const onHim = lookingAt(orbit, [POSE.position[0], POSE.position[1] - REFERENCE.marioHeight, POSE.position[2]], REFERENCE);
  expect(onHim).toBe(orbit);
});

test("Set focus asks where the clicked point is and turns the camera to orbit it, staying where it is", async () => {
  const { page, socket, canvas } = await openScene();
  const first = await enterFree(page, socket);
  fireEvent.click(page.getByRole("button", { name: "Set focus" }));
  expect(page.getByRole("button", { name: "Set focus" }).getAttribute("aria-pressed")).toBe("true");
  expect(hint(page)).toContain("Click a surface");                    // the cue on the picture says what to do
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

// Round 9, item 5 (his words: "no AI slop overexplanations"): no paragraph of keys under the camera. The
// More menu lists every key, and the picture's cue says what applies while you act on it.
test("the keys: the More menu lists every one, the cue on the picture says what applies while you act, and hintFor teaches each camera", async () => {
  const { page, socket, canvas } = await openScene();
  expect(page.container.textContent).not.toContain("W A S D move the camera from this picture");   // no hint paragraph
  const keys = openMore(page);
  for (const does of ["Play or pause", "Save this frame in that slot", "Cut to that saved view", "Fly (Shift faster, Alt slower)",
    "Look around while held", "Look at Mario", "Recorded camera", "Mute"]) {
    expect(keys.getByText(does)).toBeTruthy();
  }
  expect(keys.getByText("Shift 1 to 9").closest("div").textContent).toBe("Shift 1 to 9Cut to that saved view");
  fireEvent.click(page.getByRole("button", { name: "More" }));                   // shut again
  expect(hint(page)).toBe("");                                                  // nothing being done: nothing said
  await enterFree(page, socket);
  expect(hint(page)).toBe("");
  fireEvent.pointerDown(canvas, { button: 2, buttons: 2, pointerId: 3 });
  await waitFor(() => expect(hint(page)).toContain(LOOKING));
  const recorded = { freeAvailable: true, picking: false, looking: false, free: false, pathOn: false, rig: null, view: null,
    ghosts: [], scene: { free_camera: { available: true, reason: null } } };
  expect(hintFor(recorded)).toContain("W A S D move the camera from this picture");
  expect(hintFor({ ...recorded, free: true })).toContain("Drag to turn around the focus point");
  expect(hintFor({ ...recorded, looking: true })).toContain("Move the mouse to look around");
  expect(hintFor({ freeAvailable: false, scene: { free_camera: { reason: "No state." } } })).toContain("No state.");
});

test("without a free camera the controls say why instead of doing nothing", async () => {
  opened.free_camera = { available: false, reason: "This recording carries no scene state." };
  const { page, socket, root, canvas } = await openScene();
  expect(page.getByRole("button", { name: "Free camera" }).disabled).toBe(true);
  expect(page.getByRole("button", { name: "Look at Mario" }).disabled).toBe(true);
  expect(page.getByRole("button", { name: "Set focus" }).disabled).toBe(true);
  expect(page.container.querySelector(".ghost-lab-camera-note").textContent)   // the Camera tab's one line
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
// The stage picker's words, and the bar's one line (the Ghost Lab says why a stage is missing there).
const selectorWords = (page) => {
  const root = page.container.querySelector(".scene-lab-stage-picker");
  return { name: root.querySelector(".scene-lab-target-words strong")?.textContent,
    course: root.querySelector(".scene-lab-target-words small")?.textContent ?? null,
    strat: root.querySelector("select")?.value ?? null,
    note: page.container.querySelector(".ghost-lab-top-note")?.textContent || null };
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
  expect(page.queryByRole("button", { name: "Open" })).toBeNull();                 // the lab's file list stays in More
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
  expect(page.container.querySelector(".scene-lab-player")).toBeNull();
  expect(openMore(page).getByRole("button", { name: "Open" })).toBeTruthy();        // the lab's own recordings, in More
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

// Round 9, item 8, his words: "We also need ghost buttons next to all library entries that have a ghost,
// so that it loads directly into the scene viewer." A Library row's Ghost button hands the viewer
// {recording, entity, strat}; `answer` is the lab's GET /ghost. Since item 5 the Ghost Lab WATCHES that
// ghost: its star turns up in the picker and the run becomes the scene (his attempt's scene when it is
// one of his, else made from the ghost, POST /open {ghost}); no stage of his is needed for it.
const KIM = { ref: "lib-kim", player: "Kim", time: "10\"66", strat: "TJ Owlless", version: "jp", rank: 1, more: 0 };
function libraryGhost(ghost, answer = { item: KIM }) {
  routes.set("/api/target/strategies", () => respond({ strategies: [{ name: "Single Jump" }, { name: "TJ Owlless" }] }));
  routes.set("/api/scene-lab/open", (url, options) => respond({ ...opened, session: ++sessionSerial,
    attempt: JSON.parse(options.body).attempt ?? null }));
  routes.set("/api/scene-lab/ghost", () => respond(answer));
  return render(h(SceneViewer, { t: { view: VIEW, sceneLab: { intent: { ghost }, clearIntent: () => {} } } }));
}
const LIBRARY_KIM = { recording: "a".repeat(64), entity: "star:2:1", strat: "TJ Owlless" };
const watchingWords = (page) => page.container.querySelector(".ghost-lab-watching")?.textContent ?? null;

test("a Library ghost is watched: its star in the picker, the lab's row for it, and the run made into the scene", async () => {
  const page = libraryGhost(LIBRARY_KIM);
  const socket = await sceneUp(1);
  expect(queries("/api/scene-lab/ghost?")).toEqual([{ recording: "a".repeat(64), course: "2", star: "1" }]);
  expect(opens()).toEqual([{ ghost: "lib-kim" }]);                                 // not one of his: made from the ghost
  expect(queries("/api/scene-lab/stage")).toEqual([]);                              // no stage of his is needed
  await until(() => expect(watchingWords(page)).toBe("★Kim10\"66"));               // the bar: whose run, its time
  expect(page.container.querySelector(".ghost-lab-watching .region-flag").alt).toBe("JP");
  expect(selectorWords(page)).toMatchObject({ strat: "TJ Owlless", note: null });
  const watched = within(page.getByRole("region", { name: "In the scene" })).getAllByRole("listitem")[0];
  expect([watched.dataset.ref, watched.classList.contains("is-watched"), watched.textContent]).toEqual(
    ["lib-kim", true, "★Kim10\"66"]);
  expect(ghostsSent(socket)).toEqual([]);                                           // it IS the scene, not beside it
  expect(page.getByRole("tab", { name: /Ghosts/ }).textContent).toContain("Watching Kim");
});

test("a Library ghost that is one of his opens his own scene of it; one the lab cannot find, or whose star is unknown, says why on the bar", async () => {
  const mine = libraryGhost(LIBRARY_KIM, { item: { ...KIM, ref: "lib-mine", player: "You", attempt: 44 } });
  await sceneUp(1);
  expect(opens()).toEqual([{ attempt: 44 }]);
  await until(() => expect(watchingWords(mine)).toBe("★You10\"66"));
  cleanup();
  fetches.length = 0;
  const lost = libraryGhost(LIBRARY_KIM, { item: null, reason: "That ghost could not be found. Open it from the Library again." });
  await until(() => expect(selectorWords(lost).note).toBe("That ghost could not be found. Open it from the Library again."));
  await settled();
  expect(opens()).toEqual([]);
  expect(lost.container.querySelector(".scene-lab-player")).toBeNull();
  cleanup();
  fetches.length = 0;
  const unknown = libraryGhost({ ...LIBRARY_KIM, entity: "route:7" });
  await until(() => expect(selectorWords(unknown).note).toBe(STAGE_WORDS.unplaced));
  await settled();
  expect([queries("/api/scene-lab/ghost?"), opens()]).toEqual([[], []]);
});

// His words (2026-11-09): "I want to be able to see all of my past PB ghosts ... I want to be able to open
// all of these in the Ghost Lab." A Practice log row whose attempt has no recorded take but a ghost: the
// take is asked for first (quietly), then the lab's ghost row for that attempt (GET /ghost?attempt=), which
// is watched like any ghost row -- made into the scene from its ghost (POST /open {ghost}) -- and plays.
const MY_PB = { ref: "mine-77", player: "You", time: "10\"10", strat: "TJ Owlless", attempt: 77, frames: 303,
  rank: null, more: 0, yours: true };
const NO_TAKE = "This attempt has no recorded scene.";
function attemptWithoutTake(answer = { item: MY_PB }) {
  routes.set("/api/target/strategies", () => respond({ strategies: [{ name: "TJ Owlless" }] }));
  routes.set("/api/scene-lab/open", (url, options) => (JSON.parse(options.body).attempt != null
    ? respond({ detail: NO_TAKE }, 404) : respond({ ...opened, session: ++sessionSerial, attempt: null })));
  routes.set("/api/scene-lab/ghost", () => respond(answer));
  return render(h(SceneViewer, { t: { view: VIEW, sceneLab: { intent: { attempt: 77 }, clearIntent: () => {} } } }));
}
const labAsks = () => fetches.filter(([url]) => url === "/api/scene-lab/open" || url.startsWith("/api/scene-lab/ghost?"))
  .map(([url, , body]) => (body ? JSON.parse(body) : url));

test("a Practice log attempt with no recorded take is made from its ghost and plays from its first frame", async () => {
  const page = attemptWithoutTake();
  const socket = await sceneUp(1);
  const asks = labAsks();
  expect(asks.slice(0, 2)).toEqual([{ attempt: 77 }, "/api/scene-lab/ghost?attempt=77"]);
  expect(asks.at(-1)).toEqual({ ghost: "mine-77" });                               // made from the ghost
  await until(() => expect(page.getByRole("button", { name: "Pause" })).toBeTruthy());
  expect(socket.pictures().map((request) => request.frame)).toEqual([0, 0]);       // from its first frame
  await until(() => expect(watchingWords(page)).toBe("★You10\"10"));
  expect(selectorWords(page).note).toBeNull();                                       // the quiet take said nothing
  fireEvent.click(page.getByRole("button", { name: "Pause" }));
});

test("a Practice log attempt with neither a take nor a ghost shows the lab's reason as a refusal and opens nothing", async () => {
  const reason = "This run has no ghost recording.";
  const page = attemptWithoutTake({ item: null, reason });
  await until(() => expect(page.container.querySelector(".scene-lab-error")?.textContent).toBe(reason));
  await settled();
  expect(opens()).toEqual([{ attempt: 77 }]);
  expect(sockets.length).toBe(0);
  expect(page.container.querySelector(".scene-lab-player")).toBeNull();
});

// The lab's lists: 23 of yours (the first is the stage's own run), 12 top ghosts per strategy (All
// strategies: the fastest twelve happen to be Single Jump's), one player found, and that player's
// three. Top ghosts carry their strategy and, in turn, a US, a JP and no ROM.
const TOP_ROMS = ["us", "jp", null];
function ghostRoutes() {
  const yours = Array.from({ length: 23 }, (_, i) => ({ ref: `yours-${i}`, player: "You", time: `${11 + i}"03`,
    frames: 330 + i, rank: null, recorded_utc: `2026-10-${String(1 + i).padStart(2, "0")}T12:00:00Z`, more: 0,
    yours: true, attempt: i === 0 ? 40 : 900 + i }));
  routes.set("/api/scene-lab/ghosts", (url) => {
    const query = new URL(url, "http://page").searchParams, page = Number(query.get("page"));
    const strat = query.get("strategies") === "all" ? "Single Jump" : query.get("strat");
    const rows = {
      yours: () => (query.get("sort") === "newest" ? [...yours].reverse() : yours),
      top: () => Array.from({ length: 12 }, (_, i) => ({ ref: `top-${strat}-${i}`, player: `Player ${i + 1}`,
        time: `10"${String(i).padStart(2, "0")}`, rank: i + 1, more: 0, yours: false, strat, version: TOP_ROMS[i % 3] })),
      search: () => [{ ref: "best-sully", player: "SullyLikesBigChungus", time: "10\"50", rank: null, more: 2 }],
      player: () => [0, 1, 2].map((i) => ({ ref: `${query.get("player")}-${i}`, player: query.get("player"),
        time: `10"5${i}`, rank: null, more: 0 })),
    }[query.get("section")]();
    return respond({ items: rows.slice((page - 1) * 10, page * 10), page,
      pages: Math.max(1, Math.ceil(rows.length / 10)), reason: null });
  });
}
// The Ghosts tab (round 9, item 5): "In the scene" (the run you watch, then each ghost shown beside it) and the
// list beside it, Top or Yours one at a time, or what Find a player found. A row of that list, a row of "In
// the scene", a list row's eye (show beside the run you watch, or hide), and the list chosen.
const ghostRow = (page, ref) => page.container.querySelector(`.ghost-lab-lists li[data-ref="${ref}"]`);
const sceneRow = (page, ref) => page.container.querySelector(`.ghost-lab-scene-list li[data-ref="${ref}"]`);
const eye = (page, ref) => ghostRow(page, ref).querySelector("button.ghost-lab-eye");
const showList = (page, words) => fireEvent.click(within(page.getByRole("group", { name: "Ghost list" }))
  .getByRole("button", { name: words }));
function ghostsTab(page) {
  fireEvent.click(page.getByRole("tab", { name: /Ghosts/ }));
  return page;
}
const ghostsSent = (socket) => socket.sent.filter((message) => message.kind === "ghosts");
const asked = (section) => queries("/api/scene-lab/ghosts").filter((query) => query.section === section);

test("the ghost panel lists Top or Yours, one at a time: Top first on All strategies; Yours pages, sorts, and marks the run you watch", async () => {
  ghostRoutes();
  const page = arrive();
  await sceneUp(1);
  // Round 9, item 7: Top ghosts first lists every strategy at once, each row naming its strategy, and
  // each row (item 11) wears the flag of the ROM its recording names, none where it names none.
  const top = await until(() => page.getByRole("region", { name: "Top ghosts" }));
  await until(() => expect(top.querySelectorAll("li").length).toBe(10));
  expect(asked("top")).toEqual([{ section: "top", strategies: "all", course: "2", star: "1", page: "1" }]);
  const lists = within(page.getByRole("group", { name: "Ghost list" }));
  expect([pressed(lists.getByRole("button", { name: "Top" })), pressed(lists.getByRole("button", { name: "Yours" }))])
    .toEqual(["true", "false"]);
  expect([page.queryByRole("region", { name: "Your ghosts" }), asked("yours")]).toEqual([null, []]);   // one at a time
  const picker = within(top).getByRole("combobox", { name: "Strategy for top ghosts" });
  expect([picker.value, picker.options[0].textContent]).toEqual(["", "All strategies"]);
  expect([...top.querySelectorAll(".scene-lab-ghost-rank")].map((rank) => rank.textContent).slice(0, 3)).toEqual(["#1", "#2", "#3"]);
  expect([...top.querySelectorAll(".scene-lab-ghost-strat")].map((line) => line.textContent)).toEqual(
    Array(10).fill("Single Jump"));
  expect([...top.querySelectorAll("li")].slice(0, 3).map((row) => row.querySelector(".region-flag")?.alt ?? null))
    .toEqual(["US", "JP", null]);
  fireEvent.change(picker, { target: { value: "TJ Owlless" } });
  await until(() => expect(top.querySelector("li").dataset.ref).toBe("top-TJ Owlless-0"));
  expect(asked("top").at(-1)).toMatchObject({ strat: "TJ Owlless", page: "1" });
  expect(asked("top").at(-1).strategies).toBeUndefined();
  expect(top.querySelector(".scene-lab-ghost-strat")).toBeNull();                       // one strategy: no strategy line
  expect(selectorWords(page).strat).toBe("Single Jump");                              // the stage keeps its strategy

  showList(page, "Yours");
  await until(() => expect(ghostRow(page, "yours-1")).not.toBeNull());
  expect(page.queryByRole("region", { name: "Top ghosts" })).toBeNull();
  expect(asked("yours")).toEqual([{ section: "yours", course: "2", star: "1", strat: "Single Jump", sort: "fastest", page: "1" }]);
  // The stage's own run (attempt 40) is the run you watch: pressed, with a star, and no eye (it is the scene).
  const stage = ghostRow(page, "yours-0");
  const watching = within(stage).getByRole("button", { name: "Watching You 11\"03" });
  expect([stage.classList.contains("is-watched"), pressed(watching), watching.disabled,
    stage.querySelector(".ghost-lab-star").textContent, stage.querySelectorAll("button").length]).toEqual([true, "true", true, "★", 1]);
  const other = within(ghostRow(page, "yours-1")).getByRole("button", { name: "Watch You 12\"03" });
  expect([pressed(other), other.disabled, eye(page, "yours-1").getAttribute("aria-label")])
    .toEqual(["false", false, "Show You beside the run you watch"]);
  const yours = page.getByRole("region", { name: "Your ghosts" });
  expect(yours.textContent).toContain("Page 1");
  fireEvent.click(within(yours).getByRole("button", { name: "Next" }));
  await until(() => expect(yours.querySelector("li").dataset.ref).toBe("yours-10"));
  expect(asked("yours").at(-1)).toMatchObject({ page: "2", sort: "fastest" });
  fireEvent.change(within(yours).getByRole("combobox", { name: "Sort your ghosts" }), { target: { value: "newest" } });
  await until(() => expect(yours.querySelector("li").dataset.ref).toBe("yours-22"));
  expect(asked("yours").at(-1)).toMatchObject({ page: "1", sort: "newest" });
  expect(queries("/api/scene-lab/stage").length).toBe(1);
});

// His rule for this round (event d8d7a140): keep database interactions cheap. The panel asks for a
// page only when he changes something, and each page once in a scene's life.
test("each page is asked once in a scene's life: paging back, sorting back, another strategy and back, and coming back ask nothing", async () => {
  ghostRoutes();
  const first = arrive();
  await sceneUp(1);
  const top = await until(() => first.getByRole("region", { name: "Top ghosts" }));
  await until(() => expect(top.querySelectorAll("li").length).toBe(10));
  fireEvent.change(within(top).getByRole("combobox"), { target: { value: "TJ Owlless" } });
  await until(() => expect(top.querySelector("li").dataset.ref).toBe("top-TJ Owlless-0"));
  fireEvent.change(within(top).getByRole("combobox"), { target: { value: "" } });          // All strategies again
  await until(() => expect(top.querySelector("li").dataset.ref).toBe("top-Single Jump-0"));
  showList(first, "Yours");
  const yours = await until(() => first.getByRole("region", { name: "Your ghosts" }));
  await until(() => expect(yours.querySelector("li")?.dataset.ref).toBe("yours-0"));
  fireEvent.click(within(yours).getByRole("button", { name: "Next" }));
  await until(() => expect(yours.querySelector("li").dataset.ref).toBe("yours-10"));
  fireEvent.click(within(yours).getByRole("button", { name: "Previous" }));
  await until(() => expect(yours.querySelector("li").dataset.ref).toBe("yours-0"));
  const sort = within(yours).getByRole("combobox", { name: "Sort your ghosts" });
  fireEvent.change(sort, { target: { value: "newest" } });
  await until(() => expect(yours.querySelector("li").dataset.ref).toBe("yours-22"));
  fireEvent.change(sort, { target: { value: "fastest" } });
  await until(() => expect(yours.querySelector("li").dataset.ref).toBe("yours-0"));
  showList(first, "Top");                                                            // each list again: from what it holds
  await until(() => expect(ghostRow(first, "top-Single Jump-0")).not.toBeNull());
  showList(first, "Yours");
  await until(() => expect(ghostRow(first, "yours-0")).not.toBeNull());
  expect(asked("yours").map((query) => [query.sort, query.page])).toEqual([["fastest", "1"], ["fastest", "2"], ["newest", "1"]]);
  expect(asked("top").map((query) => query.strategies ?? query.strat)).toEqual(["all", "TJ Owlless"]);
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
  await until(() => expect(ghostRow(again, "top-Single Jump-1")).not.toBeNull());
  showList(again, "Yours");
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

// His words on the first concepts (round 9, item 5): "the "add" and "open" are a bit confusing. I didn't
// understand the difference on first glance". A row's eye now shows its ghost beside the run you watch.
test("the eye shows a ghost beside the run you watch and hides it: the whole list goes, the lab's answer marks rows, a refusal says why, and the picture is asked again", async () => {
  ghostRoutes();
  const page = arrive();
  const socket = await sceneUp(1);
  await until(() => expect(ghostRow(page, "top-Single Jump-1")).not.toBeNull());
  const pictures = socket.pictures().length;
  const shownWords = (ref) => [eye(page, ref).getAttribute("aria-label"), pressed(eye(page, ref)),
    ghostRow(page, ref).classList.contains("is-shown")];
  expect(shownWords("top-Single Jump-0")).toEqual(["Show Player 1 beside the run you watch", "false", false]);
  fireEvent.click(eye(page, "top-Single Jump-0"));
  await until(() => expect(ghostsSent(socket).length).toBe(1));
  expect(ghostsSent(socket)[0]).toMatchObject({ session: sessionSerial, set: [{ ref: "top-Single Jump-0", label: "Player 1" }] });
  expect([...shownWords("top-Single Jump-0"), eye(page, "top-Single Jump-0").disabled])   // on its way until answered
    .toEqual(["Showing Player 1...", "false", false, true]);
  expect(sceneRow(page, "top-Single Jump-0")).not.toBeNull();                            // "In the scene", on its way
  await settled();
  expect(socket.pictures().length).toBe(pictures);                                       // nothing redrawn before
  socket.text({ id: ghostsSent(socket)[0].id, kind: "ghosts", shown: ["top-Single Jump-0"], refused: [] });
  await until(() => expect(socket.pictures().length).toBe(pictures + 1));
  expect(socket.pictures().at(-1)).toMatchObject({ frame: 0 });                            // the picture on screen again
  await socket.picture(socket.pictures().at(-1));
  expect(shownWords("top-Single Jump-0")).toEqual(["Hide Player 1", "true", true]);
  expect(page.getByRole("tab", { name: /Ghosts/ }).textContent).toContain("1 beside you");

  fireEvent.click(eye(page, "top-Single Jump-1"));
  await until(() => expect(ghostsSent(socket).length).toBe(2));
  expect(ghostsSent(socket)[1].set.map((ghost) => ghost.ref)).toEqual(["top-Single Jump-0", "top-Single Jump-1"]);
  socket.text({ id: ghostsSent(socket)[1].id, kind: "ghosts", shown: ["top-Single Jump-0"],
    refused: [{ ref: "top-Single Jump-1", reason: "Recorded on another version of the course." }] });
  await until(() => expect(ghostRow(page, "top-Single Jump-1").textContent).toContain("Recorded on another version of the course."));
  expect(shownWords("top-Single Jump-1")).toEqual(["Show Player 2 beside the run you watch", "false", false]);
  expect(sceneRow(page, "top-Single Jump-1")).toBeNull();

  fireEvent.click(eye(page, "top-Single Jump-0"));                                       // hide one
  fireEvent.click(eye(page, "top-Single Jump-2"));                                       // and show another before the answer
  await until(() => expect(ghostsSent(socket).length).toBe(4));
  expect(ghostsSent(socket)[2].set).toEqual([]);
  expect(ghostsSent(socket)[3].set.map((ghost) => ghost.ref)).toEqual(["top-Single Jump-2"]);
  socket.text({ id: ghostsSent(socket)[3].id, kind: "ghosts", shown: ["top-Single Jump-2"], refused: [] });
  socket.text({ id: ghostsSent(socket)[2].id, kind: "ghosts", shown: [], refused: [] });     // an older list's, late
  await settled();
  expect(ghostRow(page, "top-Single Jump-2").classList.contains("is-shown")).toBe(true);
  expect(ghostRow(page, "top-Single Jump-0").classList.contains("is-shown")).toBe(false);
  expect([...page.getByRole("region", { name: "In the scene" }).querySelectorAll("li")].map((row) => row.dataset.ref))
    .toEqual(["stage", "top-Single Jump-2"]);                                            // the run you watch, then beside it
  expect(page.getByRole("tab", { name: /Ghosts/ }).textContent).toContain("1 beside you");
  fireEvent.click(page.getByRole("button", { name: "Hide all" }));
  await until(() => expect(ghostsSent(socket).at(-1).set).toEqual([]));
});

test("a ghost in the scene has its own Mute; a recording without sound says its ghosts play silent too", async () => {
  ghostRoutes();
  opened = { ...opened, audio: { available: true, rate: 48000, block_samples: 9600, total_samples: 4 * 9600, blocks: 4,
    codec: "stored", shift_ms: 0, samples: FRAMES.map((_, k) => k * 1600), lost: [] } };
  routes.set("/api/scene-lab/audio/", () => new Promise(() => {}));        // its blocks on their way
  const page = arrive();
  const socket = await sceneUp(1);
  showList(page, "Yours");
  await until(() => expect(ghostRow(page, "yours-2")).not.toBeNull());
  expect(within(ghostRow(page, "yours-2")).queryByRole("button", { name: /^Mute/ })).toBeNull();   // a list row shows and watches
  expect(sceneRow(page, "yours-2")).toBeNull();                                                   // not in the scene
  fireEvent.click(eye(page, "yours-2"));
  await until(() => expect(ghostsSent(socket).length).toBe(1));
  socket.text({ id: ghostsSent(socket)[0].id, kind: "ghosts", shown: ["yours-2"], refused: [] });
  const mute = await until(() => within(sceneRow(page, "yours-2")).getByRole("button", { name: /^Mute/ }));
  expect([mute.getAttribute("aria-label"), mute.getAttribute("aria-pressed"), mute.disabled, mute.title]).toEqual(
    ["Mute You 13\"03", "false", false, "Mute this ghost's sound effects"]);
  fireEvent.click(mute);
  await until(() => expect(within(sceneRow(page, "yours-2")).getByRole("button", { name: /^Mute/ })
    .getAttribute("aria-pressed")).toBe("true"));
  expect(fetches.some(([url]) => url.includes("track=ghost%3Ayours-2"))).toBe(true);
  routes.set("/api/scene-lab/audio/", () => respond({ detail: "This ghost's sound could not be made." }, 404));
  fireEvent.click(eye(page, "yours-2"));
  await until(() => expect(ghostsSent(socket).length).toBe(2));
  socket.text({ id: ghostsSent(socket)[1].id, kind: "ghosts", shown: [], refused: [] });
  await until(() => expect(sceneRow(page, "yours-2")).toBeNull());
  fireEvent.click(eye(page, "yours-3"));
  await until(() => expect(ghostsSent(socket).length).toBe(3));
  socket.text({ id: ghostsSent(socket)[2].id, kind: "ghosts", shown: ["yours-3"], refused: [] });
  const failed = await until(() => {                                          // a ghost whose sound failed says so
    const button = within(sceneRow(page, "yours-3")).getByRole("button", { name: /^Mute/ });
    expect(button.disabled).toBe(true);
    return button;
  });
  expect(failed.title).toBe("This ghost's sound could not be played.");
});

// His words (round 5, item 5): "we need one button per active ghost in the scene to follow their
// recorded POV."
const GHOST_CAMERA = { focus: [100, 200, -300], position: [600, 700, 1200], roll: -700, fov: 50 };
// Players 1 and 2 of Top ghosts shown beside the run you watch (their eyes, on the Ghosts tab).
async function twoGhostsIn(page, socket) {
  await until(() => expect(ghostRow(page, "top-Single Jump-1")).not.toBeNull());
  for (const [ref, player] of [["top-Single Jump-0", "Player 1"], ["top-Single Jump-1", "Player 2"]]) {
    fireEvent.click(within(ghostRow(page, ref)).getByRole("button", { name: `Show ${player} beside the run you watch` }));
    await until(() => expect(within(ghostRow(page, ref)).getByRole("button", { name: `Showing ${player}...` })).toBeTruthy());
  }
  const pictures = socket.pictures().length;
  socket.text({ id: ghostsSent(socket).at(-1).id, kind: "ghosts", shown: ["top-Single Jump-0", "top-Single Jump-1"],
    refused: [] });
  await until(() => expect(ghostRow(page, "top-Single Jump-1").classList.contains("is-shown")).toBe(true));
  await until(() => expect(socket.pictures().length).toBe(pictures + 1));       // the picture on screen, with them
  await socket.picture(socket.pictures().at(-1));
}
const tag = (page) => page.container.querySelector(".scene-lab-tag").textContent;
const pressed = (button) => button.getAttribute("aria-pressed");

test("each ghost in the scene has a view button: the stage from its own camera, through steps, into Free camera", async () => {
  ghostRoutes();
  const page = arrive();
  const socket = await sceneUp(1);
  await twoGhostsIn(page, socket);
  const rowView = (ref, name) => within(sceneRow(page, ref)).getByRole("button", { name: `Their view: ${name}` });
  expect(pressed(rowView("top-Single Jump-1", "Player 2's view"))).toBe("false");        // "In the scene" has one each too
  cameraTab(page);
  const bar = within(page.container.querySelector('[aria-label="Camera"]'));
  const theirs = bar.getByRole("button", { name: /^(US |JP )?Player 2's view$/ });
  expect(bar.getByRole("button", { name: /^(US |JP )?Player 1's view$/ })).toBeTruthy();
  // Each view button wears its ghost's ROM flag (round 9, item 11): Player 1's US, Player 2's JP.
  expect([1, 2].map((n) => bar.getByRole("button", { name: new RegExp(`Player ${n}'s view$`) })
    .querySelector(".region-flag")?.alt)).toEqual(["US", "JP"]);
  expect(pressed(bar.getByRole("button", { name: "Recorded camera" }))).toBe("true");

  fireEvent.click(theirs);
  await until(() => expect(socket.pictures().at(-1).camera).toEqual({ ghost: "top-Single Jump-1" }));
  expect([pressed(theirs), pressed(bar.getByRole("button", { name: "Recorded camera" }))]).toEqual(["true", "false"]);
  expect(tag(page)).toBe("Player 2's view");
  ghostsTab(page);
  expect(pressed(rowView("top-Single Jump-1", "Player 2's view"))).toBe("true");
  cameraTab(page);
  await socket.picture(socket.pictures().at(-1), { camera: "free",
    view: { ghost: "top-Single Jump-1", camera: GHOST_CAMERA } });

  // It follows the ghost: a step asks for the next frame from that ghost's camera, and a reason
  // the lab gives (a moment its recording has no camera for) is shown.
  fireEvent.click(page.getByRole("button", { name: "Forward 1" }));
  await until(() => expect(socket.pictures().at(-1)).toMatchObject({ frame: 1, camera: { ghost: "top-Single Jump-1" } }));
  await socket.picture(socket.pictures().at(-1), { camera: "free", reason: "This ghost's recording has ended, so this is the last view it had.",
    view: { ghost: "top-Single Jump-1", camera: GHOST_CAMERA } });
  await until(() => expect(page.container.textContent).toContain("This ghost's recording has ended"));

  // Free camera starts where the ghost's camera is, with no pose asked and no jump.
  const before = socket.sent.length;
  fireEvent.click(bar.getByRole("button", { name: "Free camera" }));
  await until(() => expect(socket.pictures().at(-1).camera?.position).toBeTruthy());
  expect(socket.sent.slice(before).some((message) => message.kind === "pose")).toBe(false);
  const free = socket.pictures().at(-1).camera;
  close(free.position, GHOST_CAMERA.position);
  close(free.focus, GHOST_CAMERA.focus);
  expect([free.roll, free.fov]).toEqual([GHOST_CAMERA.roll, GHOST_CAMERA.fov]);
  expect([tag(page), pressed(theirs)]).toEqual(["Free camera", "false"]);
  await socket.picture(socket.pictures().at(-1));

  // The row's own button ("In the scene"), then Recorded camera (R) back to the stage's own view.
  ghostsTab(page);
  fireEvent.click(rowView("top-Single Jump-0", "Player 1's view"));
  await until(() => expect(socket.pictures().at(-1).camera).toEqual({ ghost: "top-Single Jump-0" }));
  expect(tag(page)).toBe("Player 1's view");
  await socket.picture(socket.pictures().at(-1), { camera: "free", view: { ghost: "top-Single Jump-0", camera: GHOST_CAMERA } });
  fireEvent.keyDown(document.body, { key: "r" });
  await until(() => expect(socket.pictures().at(-1).camera).toBeNull());
  expect(tag(page)).toBe("Recorded camera");
});

test("removing the ghost whose view is on screen goes back to the recorded camera", async () => {
  ghostRoutes();
  const page = arrive();
  const socket = await sceneUp(1);
  await twoGhostsIn(page, socket);
  cameraTab(page);
  const bar = within(page.container.querySelector('[aria-label="Camera"]'));
  fireEvent.click(bar.getByRole("button", { name: /^(US |JP )?Player 1's view$/ }));
  await until(() => expect(socket.pictures().at(-1).camera).toEqual({ ghost: "top-Single Jump-0" }));
  await socket.picture(socket.pictures().at(-1), { camera: "free", view: { ghost: "top-Single Jump-0", camera: GHOST_CAMERA } });
  ghostsTab(page);
  fireEvent.click(within(ghostRow(page, "top-Single Jump-0")).getByRole("button", { name: "Hide Player 1" }));
  await until(() => expect(socket.pictures().at(-1).camera).toBeNull());
  expect(tag(page)).toBe("Recorded camera");
  socket.text({ id: ghostsSent(socket).at(-1).id, kind: "ghosts", shown: ["top-Single Jump-1"], refused: [] });
  cameraTab(page);
  await until(() => expect(bar.queryByRole("button", { name: /^(US |JP )?Player 1's view$/ })).toBeNull());
  expect(bar.getByRole("button", { name: /^(US |JP )?Player 2's view$/ })).toBeTruthy();
});

test("view buttons name whose camera it is, with the time for yours and where two ghosts share a name", () => {
  const ghosts = [{ ref: "a", player: "Sully", time: "11\"03" }, { ref: "b", player: "Sully", time: "11\"40" },
    { ref: "c", player: "Bob", time: "10\"50" }, { ref: "d", label: "YOUR PB" }, { ref: "e", player: "You", time: "12\"00" }];
  expect(["a", "b", "c", "d", "e", "gone"].map((ref) => viewName(ghosts, ref))).toEqual(["Sully's view · 11\"03",
    "Sully's view · 11\"40", "Bob's view", "YOUR PB's view", "Your view · 12\"00", "A ghost's view"]);
});

test("without the free camera the view buttons stay, disabled, with the reason", async () => {
  opened = { ...opened, free_camera: { available: false, reason: "The lab config names no symbol map." } };
  ghostRoutes();
  const page = arrive();
  const socket = await sceneUp(1);
  await twoGhostsIn(page, socket);
  cameraTab(page);
  const view = within(page.container.querySelector('[aria-label="Camera"]')).getByRole("button", { name: /^(US |JP )?Player 1's view$/ });
  expect([view.disabled, view.title]).toEqual([true, "The lab config names no symbol map."]);
  const sent = socket.pictures().length;
  fireEvent.click(view);
  await settled();
  expect(socket.pictures().length).toBe(sent);
});

// His words (round 6, item 1): a dolly "right in front of mario, aimed at him (...) with close ups /
// wideshots / medium", "off to the side for a side profile shot", "a rotating microwave effect".
// The lab draws each rig's camera (tests/test_scene_lab_rigs.py); the page asks for it whole.
const RIG_CAMERA = { focus: [100, 220, -300], position: [100, 220, 200], roll: 0, fov: 45 };
const rigBar = (page) => within(page.container.querySelector('[aria-label="Cinematic cameras"]'));
const rigOptions = (page) => within(page.container.querySelector('[aria-label="Cinematic camera settings"]'));
const rigAsked = (socket) => socket.pictures().at(-1).camera?.rig;
// The lab's answer to the newest rig request: drawn from RIG_CAMERA (the page keeps two in flight).
const rigDrawn = (socket) => socket.picture(socket.pictures().at(-1), { camera: "free", camera_used: RIG_CAMERA,
  rig: { kind: rigAsked(socket).kind, target: rigAsked(socket).target, shot: rigAsked(socket).shot, side: null, pulled: 1 } });

test("a cinematic camera is one request the lab draws: Front, its shot and height, through steps, into Free camera", async () => {
  const { page, socket } = await openScene();
  await socket.picture(socket.sent[0]);
  const front = rigBar(page).getByRole("button", { name: "Front" });
  fireEvent.click(front);
  await until(() => expect(rigAsked(socket)).toBeTruthy());
  const tuning = sceneCameraTuning();
  expect(socket.pictures().at(-1).camera).toEqual({ rig: { kind: "front", target: "stage", shot: "medium",
    height: tuning.marioHeight, radius: tuning.rigOrbitRadius, speed: tuning.rigOrbitSpeed, phase: 0, side: "auto",
    roll: 0, fov: tuning.rigFov, tuning: rigTuning(tuning) } });
  const camera = within(page.container.querySelector('[aria-label="Camera"]'));
  expect([pressed(front), pressed(camera.getByRole("button", { name: "Recorded camera" })), tag(page)])
    .toEqual(["true", "false", "Front · Mario"]);
  await socket.picture(socket.pictures().at(-1), { camera: "free", camera_used: RIG_CAMERA,
    rig: { kind: "front", target: "stage", shot: "medium", side: null, pulled: 1 } });

  fireEvent.click(rigOptions(page).getByRole("button", { name: "Wide" }));
  await until(() => expect(rigAsked(socket).shot).toBe("wide"));
  await rigDrawn(socket);
  fireEvent.input(rigOptions(page).getByRole("slider", { name: "Height" }), { target: { value: "400" } });
  await until(() => expect(rigAsked(socket).height).toBe(400));
  await rigDrawn(socket);
  fireEvent.click(rigOptions(page).getByRole("button", { name: "Eye level" }));
  await until(() => expect(rigAsked(socket).height).toBe(tuning.marioHeight));
  await rigDrawn(socket);
  fireEvent.click(page.getByRole("button", { name: "Forward 1" }));
  await until(() => expect(socket.pictures().at(-1)).toMatchObject({ frame: 1, camera: { rig: { kind: "front", shot: "wide" } } }));
  await socket.picture(socket.pictures().at(-1), { camera: "free", camera_used: RIG_CAMERA,
    rig: { kind: "front", target: "stage", shot: "wide", side: null, pulled: 1 } });

  // Free camera starts where the rig's camera is, with no pose asked: no jump.
  const before = socket.sent.length;
  fireEvent.click(camera.getByRole("button", { name: "Free camera" }));
  await until(() => expect(socket.pictures().at(-1).camera?.position).toBeTruthy());
  expect(socket.sent.slice(before).some((message) => message.kind === "pose")).toBe(false);
  const free = socket.pictures().at(-1).camera;
  close(free.position, RIG_CAMERA.position);
  close(free.focus, RIG_CAMERA.focus);
  expect([free.roll, free.fov, tag(page), pressed(front)]).toEqual([0, 45, "Free camera", "false"]);
  await socket.picture(socket.pictures().at(-1));

  // Side has its own choice of side; R goes back to the recorded camera.
  fireEvent.click(rigBar(page).getByRole("button", { name: "Side" }));
  await until(() => expect(rigAsked(socket)?.kind).toBe("side"));
  expect(rigAsked(socket).shot).toBe("wide");                                  // the shot carries over
  await rigDrawn(socket);
  fireEvent.change(rigOptions(page).getByRole("combobox"), { target: { value: "left" } });
  await until(() => expect(rigAsked(socket).side).toBe("left"));
  fireEvent.keyDown(document.body, { key: "r" });
  await until(() => expect(socket.pictures().at(-1).camera).toBeNull());
  expect(page.container.querySelector('[aria-label="Cinematic camera settings"]')).toBeNull();
});

test("Orbit starts where the camera on screen stands and keeps its angle when its speed changes", async () => {
  const { page, socket } = await openScene();
  await socket.picture(socket.sent[0]);
  fireEvent.click(page.getByRole("button", { name: "Forward 1" }));
  fireEvent.click(page.getByRole("button", { name: "Forward 1" }));
  await until(() => expect(socket.pictures().at(-1).frame).toBe(2));
  fireEvent.click(rigBar(page).getByRole("button", { name: "Front" }));
  await until(() => expect(rigAsked(socket)).toBeTruthy());
  await socket.picture(socket.pictures().at(-1), { camera: "free", camera_used: RIG_CAMERA,
    rig: { kind: "front", target: "stage", shot: "medium", side: null, pulled: 1 } });
  // The lab's angle at a picture is phase + speed x its time (FRAMES carry no times: frame / fps).
  const angleAt = (rig, frame) => ((rig.phase + rig.speed * frame / 30) % 360 + 360) % 360;
  fireEvent.click(rigBar(page).getByRole("button", { name: "Orbit" }));
  await until(() => expect(rigAsked(socket)?.kind).toBe("orbit"));
  const start = rigAsked(socket);
  // RIG_CAMERA stands 500 units on Mario's +z side: the lab's angle 0.
  expect(angleAt(start, 2)).toBeCloseTo(0, 6);
  expect(socket.sent.some((message) => message.kind === "pose")).toBe(false);
  await rigDrawn(socket);
  fireEvent.input(rigOptions(page).getByRole("slider", { name: "Speed" }), { target: { value: "-90" } });
  await until(() => expect(rigAsked(socket).speed).toBe(-90));
  expect(angleAt(rigAsked(socket), 2)).toBeCloseTo(angleAt(start, 2), 6);
  await rigDrawn(socket);
  fireEvent.input(rigOptions(page).getByRole("slider", { name: "Radius" }), { target: { value: "1500" } });
  await until(() => expect(rigAsked(socket).radius).toBe(1500));
  await rigDrawn(socket);

  // From the recorded camera, Orbit asks the recorded pose and starts at its camera, seen from Mario.
  fireEvent.keyDown(document.body, { key: "r" });
  await until(() => expect(socket.pictures().at(-1).camera).toBeNull());
  await socket.picture(socket.pictures().at(-1));
  fireEvent.click(rigBar(page).getByRole("button", { name: "Orbit" }));
  await until(() => expect(socket.last()).toMatchObject({ kind: "pose", frame: 2 }));
  socket.text({ id: socket.last().id, kind: "pose", pose: POSE, reason: null });
  await until(() => expect(rigAsked(socket)?.kind).toBe("orbit"));
  const bearing = Math.atan2(POSE.position[0] - POSE.mario[0], POSE.position[2] - POSE.mario[2]) * 180 / Math.PI;
  expect(angleAt(rigAsked(socket), 2)).toBeCloseTo((bearing + 360) % 360, 6);
});

// His words (round 7, item 2): "while paused, the orbit camera should be able to be moving around in a
// circle around mario. This should be a setting (orbit while paused)." The lab turns an orbit to
// phase + speed x clock_ms when the request carries one (interface H), else x the picture's own time.
const turnOf = (degrees) => ((degrees % 360) + 360) % 360;
const orbitAngle = (rig, frame) => turnOf(rig.phase + rig.speed * (rig.clock_ms ?? frame * 1000 / 30) / 1000);
const SHOWS_2 = 2000 / 30;                          // frame 2's time on the scene's clock (FRAMES: frame / fps)

// Answers every picture request not answered yet: an orbit's as the lab draws an orbit.
async function drawnAll(socket, answered, extra = {}) {
  for (const request of socket.pictures()) {
    if (answered.has(request.id)) continue;
    answered.add(request.id);
    const rig = request.camera?.rig;
    await socket.picture(request, rig ? { camera: "free", camera_used: RIG_CAMERA,
      rig: { kind: rig.kind, target: rig.target, shot: rig.shot, side: null, pulled: 1 }, ...extra } : extra);
  }
}

// An orbit on frame 2, paused, drawn by the lab at clock.now 1000 (the held orbit's clock starts there).
async function heldOrbit() {
  const opened = await openScene(), { page, socket } = opened, answered = new Set();
  await drawnAll(socket, answered);
  for (const _ of [1, 2]) {
    fireEvent.click(page.getByRole("button", { name: "Forward 1" }));
    await settled();
    await drawnAll(socket, answered);
  }
  fireEvent.click(rigBar(page).getByRole("button", { name: "Front" }));
  await until(() => expect(rigAsked(socket)?.kind).toBe("front"));
  await drawnAll(socket, answered);
  fireEvent.click(rigBar(page).getByRole("button", { name: "Orbit" }));
  await until(() => expect(rigAsked(socket)?.kind).toBe("orbit"));
  expect(rigAsked(socket).clock_ms).toBeUndefined();        // nothing circles until the lab has drawn the orbit
  await drawnAll(socket, answered);
  return { ...opened, answered };
}

test("Orbit while paused circles the held picture once a display frame on its own clock, and Play goes on from that angle", async () => {
  const { page, socket, answered } = await heldOrbit();
  const toggle = rigOptions(page).getByRole("button", { name: "Orbit while paused" });
  expect(toggle.getAttribute("aria-pressed")).toBe("true");                // on unless turned off
  const first = rigAsked(socket);
  await tick(1016.7);
  expect(socket.pictures().at(-1)).toMatchObject({ frame: 2 });
  expect(rigAsked(socket).clock_ms).toBeCloseTo(SHOWS_2 + 16.7, 6);
  expect(orbitAngle(rigAsked(socket), 2)).toBeCloseTo(turnOf(orbitAngle(first, 2) + first.speed * 0.0167), 6);
  await drawnAll(socket, answered);
  // At most PICTURES_IN_FLIGHT out; display frames meanwhile leave one request waiting, which goes out
  // with the orbit's clock at the moment a slot frees, never one per display frame missed.
  const before = socket.pictures().length;
  for (const at of [1033.4, 1050.1, 1066.8, 1083.5]) await tick(at);
  expect(socket.pictures().length).toBe(before + PICTURES_IN_FLIGHT);
  clock.now = 1090;
  const oldest = socket.pictures().at(-2);
  answered.add(oldest.id);
  await socket.picture(oldest, { camera: "free", camera_used: RIG_CAMERA,
    rig: { kind: "orbit", target: "stage", shot: "medium", side: null, pulled: 1 } });
  await until(() => expect(socket.pictures().length).toBe(before + PICTURES_IN_FLIGHT + 1));
  expect(rigAsked(socket).clock_ms).toBeCloseTo(SHOWS_2 + 90, 6);
  await drawnAll(socket, answered);

  // A new speed while it circles keeps the angle it is at, and the clock runs on.
  clock.now = 1100;
  const going = { ...rigAsked(socket), clock_ms: SHOWS_2 + 100 };
  fireEvent.input(rigOptions(page).getByRole("slider", { name: "Speed" }), { target: { value: "-90" } });
  await until(() => expect(rigAsked(socket).speed).toBe(-90));
  expect(rigAsked(socket).clock_ms).toBeCloseTo(SHOWS_2 + 100, 6);
  expect(orbitAngle(rigAsked(socket), 2)).toBeCloseTo(orbitAngle(going, 2), 6);
  await drawnAll(socket, answered);
  fireEvent.input(rigOptions(page).getByRole("slider", { name: "Radius" }), { target: { value: "1500" } });
  await until(() => expect(rigAsked(socket).radius).toBe(1500));
  expect(orbitAngle(rigAsked(socket), 2)).toBeCloseTo(orbitAngle(going, 2), 6);        // no time passed
  await drawnAll(socket, answered);

  // Play at 1200: its first picture is frame 2 without a clock, the paused turn folded into the phase.
  clock.now = 1200;
  const paused = { ...rigAsked(socket), clock_ms: SHOWS_2 + 200 };
  fireEvent.click(page.getByRole("button", { name: "Play" }));
  await until(() => expect(socket.pictures().at(-1).camera?.rig?.clock_ms).toBeUndefined());
  const played = rigAsked(socket);
  expect(socket.pictures().at(-1).frame).toBe(2);
  expect(orbitAngle(played, 2)).toBeCloseTo(orbitAngle(paused, 2), 6);
  // Pause again (nothing drawn of the play: the orbit's picture is still on screen): it circles on from
  // there, the clock starting at the picture's own time.
  clock.now = 1300;
  fireEvent.click(page.getByRole("button", { name: "Pause" }));
  await tick(1316.7);
  await until(() => expect(rigAsked(socket).clock_ms).toBeCloseTo(SHOWS_2 + 16.7, 6));
  expect(orbitAngle(rigAsked(socket), 2)).toBeCloseTo(turnOf(orbitAngle(played, 2) + played.speed * 0.0167), 6);
});

test("Orbit while paused off holds the camera where it is; on again while paused it circles on from there", async () => {
  const { page, socket, answered } = await heldOrbit();
  await tick(1016.7);
  await drawnAll(socket, answered);
  clock.now = 1100;
  const circling = { ...rigAsked(socket), clock_ms: SHOWS_2 + 100 };
  fireEvent.click(rigOptions(page).getByRole("button", { name: "Orbit while paused" }));
  await until(() => expect(rigAsked(socket).clock_ms).toBeUndefined());
  expect(orbitAngle(rigAsked(socket), 2)).toBeCloseTo(orbitAngle(circling, 2), 6);       // stopped where it was
  expect(rigOptions(page).getByRole("button", { name: "Orbit while paused" }).getAttribute("aria-pressed")).toBe("false");
  await drawnAll(socket, answered);
  const asked = socket.pictures().length;
  for (const at of [1116.7, 1133.4, 1150.1]) await tick(at);
  expect(socket.pictures().length).toBe(asked);                                         // held still: nothing asked

  clock.now = 1200;
  const still = rigAsked(socket);
  fireEvent.click(rigOptions(page).getByRole("button", { name: "Orbit while paused" }));
  await settled();
  await drawnAll(socket, answered);
  await tick(1216.7);
  expect(rigAsked(socket).clock_ms).toBeCloseTo(SHOWS_2 + 16.7, 6);
  expect(orbitAngle(rigAsked(socket), 2)).toBeCloseTo(turnOf(orbitAngle(still, 2) + still.speed * 0.0167), 6);
});

test("a step while it circles circles on in the new picture; a picture the lab could not draw from the orbit stops the asking", async () => {
  const { page, socket, answered } = await heldOrbit();
  await tick(1016.7);
  await drawnAll(socket, answered);
  clock.now = 1100;
  fireEvent.click(page.getByRole("button", { name: "Forward 1" }));
  await until(() => expect(socket.pictures().at(-1).frame).toBe(3));
  expect(rigAsked(socket).clock_ms).toBeCloseTo(3000 / 30 + 100, 6);
  await drawnAll(socket, answered);
  await tick(1116.7);
  expect(socket.pictures().at(-1)).toMatchObject({ frame: 3 });
  expect(rigAsked(socket).clock_ms).toBeCloseTo(3000 / 30 + 116.7, 6);
  // Mario's place unknown in this picture: the lab draws the recorded camera and says why.
  await socket.picture(socket.pictures().at(-1), { camera: "recorded", camera_used: null,
    rig: { kind: "orbit", target: "stage", shot: "medium", side: null, pulled: null }, reason: "Mario's place is unknown" });
  answered.add(socket.pictures().at(-1).id);
  const asked = socket.pictures().length;
  for (const at of [1133.4, 1150.1, 1166.8]) await tick(at);
  expect(socket.pictures().length).toBe(asked);
});

test("the cinematic cameras follow a ghost in the scene, and Mario again once it leaves", async () => {
  ghostRoutes();
  const page = arrive();
  const socket = await sceneUp(1);
  await twoGhostsIn(page, socket);
  cameraTab(page);
  const follow = rigBar(page).getByRole("combobox");
  expect([...follow.options].map((option) => option.textContent)).toEqual(["Mario", "Player 1", "Player 2"]);
  fireEvent.change(follow, { target: { value: "top-Single Jump-1" } });
  fireEvent.click(rigBar(page).getByRole("button", { name: "Front" }));
  await until(() => expect(rigAsked(socket)?.target).toBe("top-Single Jump-1"));
  expect(tag(page)).toBe("Front · Player 2");
  await socket.picture(socket.pictures().at(-1), { camera: "free", camera_used: RIG_CAMERA,
    rig: { kind: "front", target: "top-Single Jump-1", shot: "medium", side: null, pulled: 1 } });
  ghostsTab(page);
  fireEvent.click(within(ghostRow(page, "top-Single Jump-1")).getByRole("button", { name: "Hide Player 2" }));
  socket.text({ id: ghostsSent(socket).at(-1).id, kind: "ghosts", shown: ["top-Single Jump-0"], refused: [] });
  await until(() => expect(rigAsked(socket)?.target).toBe("stage"));
  expect([follow.value, tag(page)]).toEqual(["stage", "Front · Mario"]);
});

test("a cinematic picture that came back before Mario's path was read is asked for again once it is", async () => {
  const { page, socket } = await openScene();
  await socket.picture(socket.sent[0]);
  fireEvent.click(rigBar(page).getByRole("button", { name: "Front" }));
  await until(() => expect(rigAsked(socket)).toBeTruthy());
  const question = socket.sent.find((message) => message.kind === "rig-path");
  expect(question).toMatchObject({ session: 1 });
  const pending = "Getting the path ready for this camera, so this picture shows the recorded camera.";
  await socket.picture(socket.pictures().at(-1), { camera: "recorded", reason: pending, camera_used: null,
    rig: { kind: "front", target: "stage", shot: "medium", side: null, pulled: null, raised: null } });
  await until(() => expect(page.container.querySelector(".scene-lab-reason").textContent).toBe(pending));
  const asked = socket.pictures().length;
  socket.text({ id: question.id, kind: "rig-path", ready: true, reason: null });
  await until(() => expect(socket.pictures().length).toBe(asked + 1));
  expect(socket.pictures().at(-1)).toMatchObject({ frame: 0, camera: { rig: { kind: "front" } } });
  fireEvent.click(rigBar(page).getByRole("button", { name: "Side" }));              // asked once a scene
  await until(() => expect(rigAsked(socket).kind).toBe("side"));
  expect(socket.sent.filter((message) => message.kind === "rig-path").length).toBe(1);
});

test("without the free camera the cinematic cameras stay, disabled, with the reason", async () => {
  opened = { ...opened, free_camera: { available: false, reason: "The lab config names no symbol map." } };
  const { page, socket } = await openScene();
  const front = rigBar(page).getByRole("button", { name: "Front" });
  expect([front.disabled, front.title]).toEqual([true, "The lab config names no symbol map."]);
  const sent = socket.pictures().length;
  fireEvent.click(front);
  await settled();
  expect(socket.pictures().length).toBe(sent);
});

test("the same scene left and come back to draws its ghosts again; a new stage starts with none", async () => {
  ghostRoutes();
  const first = arrive();
  const socket = await sceneUp(1);
  showList(first, "Yours");
  await until(() => expect(ghostRow(first, "yours-2")).not.toBeNull());
  fireEvent.click(eye(first, "yours-2"));
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
  await until(() => expect(sceneRow(again, "yours-2")).not.toBeNull());                 // "In the scene" again
  showList(again, "Yours");
  await until(() => expect(ghostRow(again, "yours-2")?.classList.contains("is-shown")).toBe(true));
  routes.delete("/api/scene-lab/status");
  fireEvent.change(await until(() => again.getByRole("combobox", { name: "Strategy" })), { target: { value: "TJ Owlless" } });
  const fresh = await sceneUp(3);
  await settled();
  expect(ghostsSent(fresh)).toEqual([]);
  await until(() => expect(ghostRow(again, "yours-2")).not.toBeNull());
  expect(ghostRow(again, "yours-2").classList.contains("is-shown")).toBe(false);
  expect(sceneRow(again, "yours-2")).toBeNull();
});

// ------------------------------------------------------------------ the Ghost Lab's watch, strip, keys, tray and More
// Round 9, item 5. His words: "Number keys sounds good. I think we should prioritize 1-9 for save states, and
// then Shift 1-9 for camera views, since save stating is more likely as an action." The strip under the play
// bar is Usamune's nine slots (GET /slots); a press saves the frame on screen there: POST /moment builds the
// save state, POST /moment/{id}/write puts it in the slot. `write(body)` answers the write.
const BUILT = { id: "m1", exact: true, build: "us" };
function momentRoutes(write = (body) => respond({ written: true, slot: body.slot, build: "us" })) {
  routes.set("/api/scene-lab/moment/", (url, options) => write(JSON.parse(options.body)));    // before its prefix
  routes.set("/api/scene-lab/moment", (url, options) => respond({ ...BUILT, frame: JSON.parse(options.body).frame }));
}
const moments = () => fetches.filter(([url]) => url.startsWith("/api/scene-lab/moment"))
  .map(([url, , body]) => [url, JSON.parse(body)]);
const stripWords = (page) => page.container.querySelector(".ghost-lab-strip-line").textContent;
const numbered = (rows) => rows.map((row, i) => ({ ...row, package_frame: i }));   // the recording's own frame numbers
const late = (body) => ({ ok: true, status: 200, json: () => Promise.resolve(body) });

test("1 to 9 save the frame on screen into that Usamune slot, as a tile's click does; the line under the tiles says what happened", async () => {
  opened = { ...opened, frames: numbered(FRAMES) };
  const writes = [];
  momentRoutes((body) => new Promise((resolve) => {
    writes.push((answer = { written: true, slot: body.slot, build: "us" }) => resolve(late(answer)));
  }));
  const { page, socket, root } = await openScene({ tab: "ghosts" });
  await socket.picture(socket.sent[0]);
  const strip = within(page.getByRole("group", { name: "Save states" }));
  const tile = (n) => strip.getByRole("button", { name: new RegExp(`^Slot ${n},`) });
  await until(() => expect(tile(3).getAttribute("aria-label")).toBe("Slot 3, empty. Save this frame here (3)."));
  // The first frame cannot be a save state: every tile waits, the line says why, a key asks nothing.
  expect([tile(3).disabled, stripWords(page)]).toEqual([true, "This is the first frame. Pick a later frame."]);
  fireEvent.keyDown(root, { key: "3", code: "Digit3" });
  await settled();
  expect(moments()).toEqual([]);
  fireEvent.keyDown(root, { key: "ArrowRight" });
  await until(() => expect(socket.pictures().at(-1)).toMatchObject({ frame: 1 }));
  await socket.picture(socket.pictures().at(-1));
  await until(() => expect([tile(3).disabled, stripWords(page)]).toEqual([false, ""]));

  fireEvent.keyDown(root, { key: "3", code: "Digit3" });                             // the key: slot 3
  await until(() => expect(writes.length).toBe(1));
  expect(moments()).toEqual([["/api/scene-lab/moment", { package: "bob-minute.sm64scene", frame: 1 }],
    ["/api/scene-lab/moment/m1/write", { slot: 3 }]]);                        // the lab draws its picture
  expect(stripWords(page)).toBe("Saving frame 2 in slot 3...");                     // on its way: every tile waits
  expect([page.container.querySelector('[data-slot="3"]').classList.contains("is-busy"), tile(7).disabled]).toEqual([true, true]);
  const slotsRead = fetches.filter(([url]) => url === "/api/scene-lab/slot-sets").length;
  writes[0]();
  await until(() => expect(stripWords(page)).toBe("Frame 2 is in slot 3."));
  expect(page.container.querySelector(".ghost-lab-strip-line .region-flag").alt).toBe("US");   // the build it came from
  await until(() => expect(fetches.filter(([url]) => url === "/api/scene-lab/slot-sets").length).toBeGreaterThan(slotsRead));

  fireEvent.click(tile(7));                                                         // a tile: the same press
  await until(() => expect(writes.length).toBe(2));
  expect(moments().at(-1)).toEqual(["/api/scene-lab/moment/m1/write", { slot: 7 }]);
  writes[1]({ refused: "place", reason: "The game is in Whomp's Fortress. Go to Bob-omb Battlefield, then press again." });
  await until(() => expect(stripWords(page)).toBe("The game is in Whomp's Fortress. Go to Bob-omb Battlefield, then press again."));
  expect(page.container.querySelector(".ghost-lab-strip-line").classList.contains("is-bad")).toBe(true);
  fireEvent.keyDown(root, { key: "4", code: "Digit4", altKey: true });               // Alt with a number is not a save
  await settled();
  expect(moments().length).toBe(4);
});

test("Shift with 1 to 9 cuts to the camera saved in that slot for this place, never a save; an empty slot says so", async () => {
  const SAVED_CAMERA = { focus: [100, 200, -300], position: [600, 700, 1200], roll: 0, fov: 45 };
  routes.set("/api/scene-lab/camera-presets", () => respond({ slots: [{ slot: 4, name: "Camera 4", camera: SAVED_CAMERA }] }));
  opened = { ...opened, frames: numbered(FRAMES) };
  momentRoutes();
  const { page, socket, root } = await openScene();
  await socket.picture(socket.sent[0]);
  fireEvent.keyDown(root, { key: "ArrowRight" });                                   // a frame that could be saved
  await until(() => expect(socket.pictures().at(-1)).toMatchObject({ frame: 1 }));
  await socket.picture(socket.pictures().at(-1));
  await until(() => expect(queries("/api/scene-lab/camera-presets")).toEqual([{ level: "9", area: "1" }]));
  await settled();
  fireEvent.keyDown(root, { key: "$", code: "Digit4", shiftKey: true });             // event.code: Shift+4 is still Digit4
  await until(() => expect(socket.pictures().at(-1).camera?.position).toBeTruthy());
  close(socket.pictures().at(-1).camera.position, SAVED_CAMERA.position, 3);
  close(socket.pictures().at(-1).camera.focus, SAVED_CAMERA.focus, 3);
  expect(tag(page)).toBe("Free camera");
  fireEvent.keyDown(root, { key: "%", code: "Digit5", shiftKey: true });
  await until(() => expect(page.container.querySelector(".scene-lab-notice")?.textContent).toBe("No camera saved in slot 5 here yet."));
  await settled();
  expect(moments()).toEqual([]);
});

test("a row's watch makes its run the scene: his attempt's scene, else one made from the ghost, while its row and the play bar show the progress", async () => {
  ghostRoutes();
  opened = { ...opened, frames: numbered(FRAMES) };
  const page = arrive();
  await sceneUp(1);
  showList(page, "Yours");
  await until(() => expect(ghostRow(page, "yours-3")).not.toBeNull());
  const made = [];
  routes.set("/api/scene-lab/open", (url, options) => (JSON.parse(options.body).attempt != null
    ? respond({ detail: "This attempt's scene was deleted." }, 404)
    : new Promise((resolve) => { made.push(() => resolve(late({ ...opened, session: ++sessionSerial, attempt: null }))); })));
  routes.set("/api/scene-lab/status", () => respond({ ready: true, problems: [], hold: null, session: null, ended: null,
    packages: [], resim: { what: "scene", stage: "re-simulating", done: 131, total: 318 } }));
  fireEvent.click(within(ghostRow(page, "yours-3")).getByRole("button", { name: "Watch You 14\"03" }));
  await until(() => expect(made.length).toBe(1));
  expect(opens()).toEqual([{ attempt: 40 }, { attempt: 903 }, { ghost: "yours-3" }]);   // his scene of it first
  // Meanwhile the row and the play bar show /status's progress and the old scene plays on; another row may
  // still take over (the lab ends the older build), this one waits.
  const row = ghostRow(page, "yours-3");
  const progress = await until(() => {
    const bar = within(row).getByRole("progressbar", { name: "Making your scene" });
    expect(bar.getAttribute("aria-valuenow")).toBe("41");                           // 131 of 318
    return bar;
  });
  expect([row.classList.contains("is-building"), progress.getAttribute("aria-valuetext")]).toEqual([true, "Re-making the run"]);
  const playing = page.container.querySelector(".ghost-lab-play .ghost-lab-build");
  expect([playing.textContent.includes("Making your scene"), playing.textContent.includes("41%")]).toEqual([true, true]);
  expect([within(ghostRow(page, "yours-1")).getByRole("button", { name: /^Watch/ }).disabled,
    within(row).getByRole("button", { name: /^Watch/ }).disabled]).toEqual([false, true]);
  expect([sockets.length, page.container.querySelector(".scene-lab-player") != null]).toEqual([1, true]);
  expect(page.container.querySelector(".ghost-lab-top-note").textContent).toBe("");   // his scene's refusal is quiet
  made[0]();
  const socket = await sceneUp(2);
  await until(() => expect(ghostRow(page, "yours-3").classList.contains("is-watched")).toBe(true));
  const watched = within(ghostRow(page, "yours-3")).getByRole("button", { name: "Watching You 14\"03" });
  expect([pressed(watched), ghostRow(page, "yours-3").querySelector(".ghost-lab-star").textContent]).toEqual(["true", "★"]);
  expect(ghostRow(page, "yours-0").classList.contains("is-watched")).toBe(false);
  expect([page.container.querySelector('[role="progressbar"]'), watchingWords(page)]).toEqual([null, "★You14\"03"]);
  // Its save states come from it: the ghost's own re-made scene.
  momentRoutes();
  fireEvent.keyDown(page.container.querySelector(".scene-lab"), { key: "ArrowRight" });
  await until(() => expect(socket.pictures().at(-1)).toMatchObject({ frame: 1 }));
  await socket.picture(socket.pictures().at(-1));
  fireEvent.keyDown(page.container.querySelector(".scene-lab"), { key: "2", code: "Digit2" });
  await until(() => expect(moments().length).toBe(2));
  expect(moments()[0]).toEqual(["/api/scene-lab/moment", { ghost: "yours-3", frame: 1 }]);
});

// The lead's frozen contract for fast ghost loading (2026-11-08): POST /open {ghost} answers once the first
// frames are built, "growing" with its "resim" table; each grown message adds frames and the newest table, and
// the last one has none that says "building". The share built is frames / (records - first_record).
const BUILDING = { start: "x", building: true, records: 11, first_record: 1, built_to: 3, parting_frame: null,
  save_states: { allowed: true, reason: null, until: null, check: "load" } };
const STILL_BUILDING = "This part of the run is still being built.";
const frameSlider = (page) => page.container.querySelector('.ghost-lab-play input[aria-label="Frame"]');
const frameWords = (page) => page.container.querySelector(".ghost-lab-play .scene-lab-frame").textContent;

test("a watched run opens at its first frames and grows: the bar fills, a drag stops at the last frame built, and Stop keeps what it has", async () => {
  ghostRoutes();
  opened = { ...opened, frames: numbered(FRAMES) };
  const page = arrive();
  await sceneUp(1);
  showList(page, "Yours");
  await until(() => expect(ghostRow(page, "yours-3")).not.toBeNull());
  let session = null;
  routes.delete("/api/scene-lab/open");
  routes.set("/api/scene-lab/open/cancel", () => respond({ cancelled: true }));
  routes.set("/api/scene-lab/open", (url, options) => {
    if (JSON.parse(options.body).attempt != null) return respond({ detail: "This attempt's scene was deleted." }, 404);
    session = ++sessionSerial;
    return respond({ ...opened, frames: numbered(FRAMES).slice(0, 3), growing: true, resim: BUILDING, session, attempt: null });
  });
  fireEvent.click(within(ghostRow(page, "yours-3")).getByRole("button", { name: "Watch You 14\"03" }));
  const socket = await sceneUp(2);
  // 3 of its 10 frames: the play bar and the row say 30%, the slider spans all 10, unfilled past frame 3.
  const bar = await until(() => {
    const found = page.container.querySelector(".ghost-lab-play .ghost-lab-build");
    expect(found.getAttribute("aria-valuenow")).toBe("30");
    return found;
  });
  expect([bar.textContent.includes("Making your scene"), ghostRow(page, "yours-3").className]).toEqual(
    [true, "scene-lab-ghost is-watched is-building"]);
  expect([frameSlider(page).max, frameWords(page).startsWith("Frame 1 of 10")]).toEqual(["9", true]);
  expect(Number(page.container.querySelector(".ghost-lab-unbuilt").dataset.reach)).toBeCloseTo(2 / 9, 3);
  // A drag past the part built stops at its last frame.
  fireEvent.input(frameSlider(page), { target: { value: "8" } });
  await until(() => expect(frameWords(page).startsWith("Frame 3 of 10")).toBe(true));
  expect(frameSlider(page).value).toBe("2");
  // Two more frames built: 50%. A press on a frame the lab will not save yet says why on the strip's line.
  socket.text({ kind: "grown", session, from: 3, frames: numbered(FRAMES).slice(3), growing: true,
    resim: { ...BUILDING, built_to: 5 } });
  await until(() => expect(bar.getAttribute("aria-valuenow")).toBe("50"));
  expect(Number(page.container.querySelector(".ghost-lab-unbuilt").dataset.reach)).toBeCloseTo(4 / 9, 3);
  routes.set("/api/scene-lab/moment", () => respond({ refused: "building", reason: STILL_BUILDING }));
  fireEvent.keyDown(page.container.querySelector(".scene-lab"), { key: "4", code: "Digit4" });
  await until(() => expect(stripWords(page)).toBe(STILL_BUILDING));
  // Stop: the lab ends the build, the scene keeps its 5 frames and is whole.
  fireEvent.click(page.getByRole("button", { name: "Stop making the scene" }));
  await until(() => expect(fetches.filter(([url, method]) => url === "/api/scene-lab/open/cancel" && method === "POST")).toHaveLength(1));
  socket.text({ kind: "grown", session, from: 5, frames: [], growing: false,
    resim: { ...BUILDING, built_to: 5, building: false, cancelled: true } });
  await until(() => expect(page.container.querySelector('[role="progressbar"]')).toBeNull());
  expect([frameSlider(page).max, frameWords(page).startsWith("Frame 3 of 5"),
    page.container.querySelector(".ghost-lab-unbuilt")]).toEqual(["4", true, null]);
});

test("a newer watch takes over from one still on its way: the older's answer neither opens nor speaks", async () => {
  ghostRoutes();
  opened = { ...opened, frames: numbered(FRAMES) };
  const page = arrive();
  await sceneUp(1);
  showList(page, "Yours");
  await until(() => expect(ghostRow(page, "yours-1")).not.toBeNull());
  const answers = [];
  routes.delete("/api/scene-lab/open");
  routes.set("/api/scene-lab/open", (url, options) => (JSON.parse(options.body).attempt != null
    ? respond({ detail: "This attempt's scene was deleted." }, 404)
    : new Promise((resolve) => { answers.push(resolve); })));
  fireEvent.click(within(ghostRow(page, "yours-3")).getByRole("button", { name: /^Watch/ }));
  await until(() => expect(answers).toHaveLength(1));
  fireEvent.click(within(ghostRow(page, "yours-1")).getByRole("button", { name: /^Watch/ }));
  await until(() => expect(answers).toHaveLength(2));
  expect(["yours-3", "yours-1"].map((ref) => ghostRow(page, ref).classList.contains("is-building"))).toEqual([false, true]);
  answers[0]({ ok: false, status: 409, json: () => Promise.resolve({ detail: "cancelled" }) });   // the lab ended it
  await settled();
  expect([page.container.querySelector(".ghost-lab-top-note").textContent, sockets.length]).toEqual(["", 1]);
  answers[1](late({ ...opened, session: ++sessionSerial, attempt: null }));
  await sceneUp(2);
  await until(() => expect(ghostRow(page, "yours-1").classList.contains("is-watched")).toBe(true));
  expect(ghostRow(page, "yours-3").classList.contains("is-watched")).toBe(false);
});

test("the tray opens on Ghosts, says what each tab holds, and the arrow keys move between its tabs", async () => {
  ghostRoutes();
  const page = arrive();
  await sceneUp(1);
  const ghosts = page.getByRole("tab", { name: /Ghosts/ }), camera = page.getByRole("tab", { name: /Camera/ });
  expect([ghosts.getAttribute("aria-selected"), camera.getAttribute("aria-selected")]).toEqual(["true", "false"]);
  expect([ghosts.textContent, camera.textContent]).toEqual(["GhostsWatching you", "CameraRecorded camera"]);
  expect(page.getByRole("tabpanel").id).toBe("ghost-lab-panel-ghosts");             // the Camera tab's panel is hidden
  fireEvent.keyDown(ghosts, { key: "ArrowRight" });
  expect([camera.getAttribute("aria-selected"), document.activeElement]).toEqual(["true", camera]);
  expect(page.getByRole("tabpanel").id).toBe("ghost-lab-panel-camera");
  expect(page.getByRole("button", { name: "Free camera" })).toBeTruthy();
  fireEvent.keyDown(camera, { key: "ArrowRight" });                                  // round again
  expect(ghosts.getAttribute("aria-selected")).toBe("true");
});

// The page's own rule (onKey): "a focused control [keeps] the keys it acts on itself ... so only a control
// reached with Tab keeps them". A tab reached with Tab acts on Left and Right (it moves to the next tab), so
// those keys are the tab's alone, as a slider keeps its arrows (ownKey). On 944d69d3 the arrow also reached the
// keyboard owner and stepped the scene a frame.
test("an arrow key on a focused tab moves the tab and steps no frame", async () => {
  ghostRoutes();
  const page = arrive();
  const socket = await sceneUp(1);
  await settled();
  const ghosts = page.getByRole("tab", { name: /Ghosts/ });
  ghosts.focus();
  fireEvent.keyDown(ghosts, { key: "ArrowRight" });
  expect(page.getByRole("tab", { name: /Camera/ }).getAttribute("aria-selected")).toBe("true");
  await settled();
  expect(socket.pictures().map((picture) => picture.frame)).toEqual([0]);           // only the first picture
});

test("More holds what is used rarely: the lab's recordings, Pictures, Tune controls, the numbers and every key; Esc or a press elsewhere shuts it", async () => {
  const { page, socket } = await openScene();
  await socket.picture(socket.sent[0]);
  const more = page.getByRole("button", { name: "More" }), pop = page.container.querySelector(".ghost-lab-more-pop");
  expect([more.getAttribute("aria-expanded"), pop.hidden]).toEqual(["false", true]);
  expect(page.queryByRole("button", { name: "Tune controls" })).toBeNull();          // none of it on the page itself
  const menu = openMore(page);
  expect([more.getAttribute("aria-expanded"), pop.hidden]).toEqual(["true", false]);
  const pictures = pop.querySelector(".scene-lab-quality select");
  expect([pop.querySelector(".scene-lab-quality").textContent.startsWith("Pictures"),
    [...pictures.options].map((option) => option.textContent)]).toEqual([true, ["Faster (JPEG)", "Exact pixels (slower)"]]);
  expect(menu.getByRole("button", { name: "Tune controls" })).toBeTruthy();
  expect(pop.querySelector(".ghost-lab-numbers").textContent).toContain("input to picture");
  expect(pop.querySelectorAll(".ghost-lab-keys > div").length).toBe(15);
  expect([menu.getByRole("button", { name: "Open" }) != null, pop.textContent.includes("bob-minute.sm64scene")]).toEqual([true, true]);
  fireEvent.keyDown(document, { key: "Escape" });
  await until(() => expect(pop.hidden).toBe(true));
  openMore(page);
  fireEvent.pointerDown(document.body);
  await until(() => expect(pop.hidden).toBe(true));
  expect(more.getAttribute("aria-expanded")).toBe("false");
});

test("a closed scene shows the server's reason and offers Open again", async () => {
  const { page, socket } = await openScene();
  socket.text({ id: socket.sent[0].id, error: "Closed after a while without use, to free the renderer.", closed: true });
  await waitFor(() => expect(page.container.querySelector(".ghost-lab-top-note").textContent)   // the bar's one line
    .toContain("Closed after a while without use"));
  expect(openMore(page).getByRole("button", { name: "Open" })).toBeTruthy();
});

test("Tune controls changes how the camera moves on the next movement and SAVE sends only what changed", async () => {
  const { page, socket, canvas } = await openScene();
  await enterFree(page, socket);
  fireEvent.click(openMore(page).getByRole("button", { name: "Tune controls" }));       // in the bar's More menu
  fireEvent.click(page.getByRole("button", { name: "More" }));                          // shut again
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

// ------------------------------------------------------------------ Tilt and lens (round 7, item 3)
// His words: "I want to be able to allow users to basically do whatever angle they want / tilt the
// camera / adjust focal length / etc". The lab draws roll and fov as given (tests/test_scene_lab_rigs.py;
// the renderer's own pictures are the round's GPU sheet); the page owns the words and the keys.

const lensRow = (page) => within(page.container.querySelector('[aria-label="Tilt and lens"]'));
const lensWords = (page) => page.container.querySelector(".scene-lab-lens").textContent;

test("Tilt is the game's roll with its sign turned, Lens the 35 mm focal length by the picture's diagonal", () => {
  // Measured on the renderer 2026-10-28: roll +5461 turned HMC's picture 30 degrees counterclockwise.
  expect([rollOf(30), rollOf(-30), rollOf(15)]).toEqual([-5461, 5461, -2731]);
  expect(tiltOf(5461)).toBeCloseTo(-30, 2);
  expect(lensOf(45)).toBeCloseTo(31.34, 2);                       // the game's own lens
  expect(fovOf(14)).toBeCloseTo(85.67, 2);
  expect(fovOf(100)).toBeCloseTo(14.79, 2);
  expect(fovOf(lensOf(45))).toBe(45);                             // exactly the game's, not a rounding of it
  for (const mm of [14, 24, 50, 100, 200]) expect(lensAt(lensPosition(mm))).toBeCloseTo(mm, 0);
  // A tilt you chose stays through a turn, a flight and Set focus; a recorded one levels as before.
  const chosen = tilted(orbitFromPose(POSE.focus, POSE.position, { fov: 45 }), 20);
  expect(turned(chosen, 30, 0, REFERENCE).roll).toBe(rollOf(20));
  expect(flown(chosen, { forward: 1 }, 0.5, 100, REFERENCE).roll).toBe(rollOf(20));
  expect(focusedOn(chosen, [0, 0, 0]).roll).toBe(rollOf(20));
  const recorded = orbitFromPose(POSE.focus, POSE.position, { roll: 1000, fov: 45 });
  expect(turned(recorded, 30, 0, REFERENCE).roll).toBeLessThan(1000);
  expect(focusedOn(recorded, [0, 0, 0]).roll).toBe(0);
  // A slide grabs the scene across a tilted picture: turned a quarter clockwise, dragging right moves
  // the scene the way dragging up does on a level one.
  const level = orbitFromPose(POSE.focus, POSE.position, { fov: 45 });
  close(slid(tilted(level, 90), 10, 0, REFERENCE).focus, slid(level, 0, -10, REFERENCE).focus);
});

test("Tilt and Lens turn and zoom the free camera; Z C X and - = do it from the keyboard; Reset view puts both back", async () => {
  const { page, socket, root, canvas } = await openScene();
  const first = await enterFree(page, socket);                                  // POSE: level, the game's 45 degrees
  expect(lensWords(page)).toContain("0°");
  expect(lensWords(page)).toContain("31 mm · game's");
  fireEvent.input(lensRow(page).getByRole("slider", { name: "Tilt" }), { target: { value: "15" } });
  await until(() => expect(socket.last().camera.roll).toBe(-2731));
  expect([socket.last().camera.fov, lensWords(page).includes("15°")]).toEqual([45, true]);
  await socket.picture(socket.last());
  fireEvent.input(lensRow(page).getByRole("slider", { name: "Lens" }), { target: { value: String(lensPosition(100)) } });
  await until(() => expect(socket.last().camera.fov).toBeCloseTo(14.79, 1));
  expect([socket.last().camera.roll, lensWords(page).includes("100 mm")]).toEqual([-2731, true]);
  await socket.picture(socket.last());
  // A turn keeps the tilt you set (only a recorded one levels).
  fireEvent.pointerDown(canvas, { button: 0, pointerId: 1, clientX: 100, clientY: 100 });
  move(canvas, { pointerId: 1, clientX: 140, clientY: 100 });
  await until(() => expect(socket.last().camera.position).not.toEqual(first.position));
  expect(socket.last().camera.roll).toBe(-2731);
  fireEvent.pointerUp(canvas, { pointerId: 1 });
  await socket.picture(socket.last());
  // C held a tenth of a second turns it on clockwise at tiltPerSecond; X levels it.
  clock.now = 5000;
  fireEvent.keyDown(root, { key: "c", code: "KeyC" });
  await tick(5100);
  await until(() => expect(socket.last().camera.roll).toBe(rollOf(tiltOf(-2731) + 0.1 * REFERENCE.tiltPerSecond)));
  fireEvent.keyUp(root, { key: "c", code: "KeyC" });
  await socket.picture(socket.last());
  fireEvent.keyDown(root, { key: "x", code: "KeyX" });
  await until(() => expect(socket.last().camera.roll).toBe(0));
  await socket.picture(socket.last());
  // = held lengthens the lens by lensPerSecond a second; Ctrl and the wheel widen it.
  const before = lensOf(socket.last().camera.fov);
  clock.now = 6000;
  fireEvent.keyDown(root, { key: "=", code: "Equal" });
  await tick(6100);
  await until(() => expect(lensOf(socket.last().camera.fov)).toBeCloseTo(before * REFERENCE.lensPerSecond ** 0.1, 6));
  fireEvent.keyUp(root, { key: "=", code: "Equal" });
  await socket.picture(socket.last());
  const zoomed = lensOf(socket.last().camera.fov), standing = socket.last().camera.position;
  expect(fireEvent.wheel(canvas, { deltaY: 100, ctrlKey: true })).toBe(false);  // never the browser's own zoom
  await until(() => expect(lensOf(socket.last().camera.fov)).toBeCloseTo(zoomed * Math.exp(-100 * REFERENCE.lensPerWheel), 6));
  expect(socket.last().camera.position).toEqual(standing);                     // a zoom, never a move closer
  await socket.picture(socket.last());
  fireEvent.click(page.getByRole("button", { name: "Reset view" }));
  await until(() => expect(socket.last().camera).toEqual(first));
});

test("from the recorded camera, Tilt starts Free camera from the picture on screen, then turns it", async () => {
  const { page, socket } = await openScene();
  await socket.picture(socket.sent[0]);
  fireEvent.input(lensRow(page).getByRole("slider", { name: "Tilt" }), { target: { value: "-30" } });
  await until(() => expect(socket.last()).toMatchObject({ kind: "pose", frame: 0 }));
  socket.text({ id: socket.last().id, kind: "pose", pose: { ...POSE, fov: 60 }, reason: null });
  await until(() => expect(socket.last().camera?.roll).toBe(5461));
  close(socket.last().camera.position, POSE.position, 9);
  expect([socket.last().camera.fov, tag(page)]).toEqual([60, "Free camera"]);
});

test("a cinematic camera carries its own Tilt and Lens; Z turns it without leaving it; Side and Free camera keep them", async () => {
  const { page, socket, root, canvas } = await openScene();
  await socket.picture(socket.sent[0]);
  fireEvent.click(rigBar(page).getByRole("button", { name: "Front" }));
  await until(() => expect(rigAsked(socket)).toBeTruthy());
  expect([rigAsked(socket).roll, rigAsked(socket).fov]).toEqual([0, sceneCameraTuning().rigFov]);
  await rigDrawn(socket);
  fireEvent.input(lensRow(page).getByRole("slider", { name: "Tilt" }), { target: { value: "-30" } });
  await until(() => expect(rigAsked(socket).roll).toBe(5461));
  await rigDrawn(socket);
  fireEvent.input(lensRow(page).getByRole("slider", { name: "Lens" }), { target: { value: String(lensPosition(85)) } });
  const fov = fovOf(lensAt(lensPosition(85)));
  await until(() => expect(rigAsked(socket).fov).toBe(fov));
  await rigDrawn(socket);
  clock.now = 5000;
  fireEvent.keyDown(root, { key: "z", code: "KeyZ" });
  await tick(5100);
  await until(() => expect(rigAsked(socket).roll).toBe(rollOf(tiltOf(5461) - 0.1 * REFERENCE.tiltPerSecond)));
  fireEvent.keyUp(root, { key: "z", code: "KeyZ" });
  expect(tag(page)).toBe("Front · Mario");                                      // still the cinematic camera
  expect(socket.sent.some((message) => message.kind === "pose")).toBe(false);
  await rigDrawn(socket);
  const roll = rigAsked(socket).roll;
  fireEvent.click(rigBar(page).getByRole("button", { name: "Side" }));
  await until(() => expect(rigAsked(socket)?.kind).toBe("side"));
  expect([rigAsked(socket).roll, rigAsked(socket).fov]).toEqual([roll, fov]);
  // Free camera starts from the picture's camera_used, its tilt and lens with it, and keeps the tilt.
  await socket.picture(socket.pictures().at(-1), { camera: "free", camera_used: { ...RIG_CAMERA, roll, fov },
    rig: { kind: "side", target: "stage", shot: "medium", side: "left", pulled: 1 } });
  fireEvent.click(page.getByRole("button", { name: "Free camera" }));
  await until(() => expect(socket.pictures().at(-1).camera?.position).toBeTruthy());
  expect([socket.pictures().at(-1).camera.roll, socket.pictures().at(-1).camera.fov]).toEqual([roll, fov]);
  await socket.picture(socket.pictures().at(-1));
  fireEvent.pointerDown(canvas, { button: 0, pointerId: 1, clientX: 100, clientY: 100 });
  move(canvas, { pointerId: 1, clientX: 140, clientY: 100 });
  await until(() => expect(socket.last().camera.position).not.toEqual(RIG_CAMERA.position));
  expect(socket.last().camera.roll).toBe(roll);
});

test("Tripod stands where the camera on screen stands, tilt and lens too; Chase and Overhead ask for themselves", async () => {
  const { page, socket } = await openScene();
  const free = await enterFree(page, socket);
  fireEvent.input(lensRow(page).getByRole("slider", { name: "Tilt" }), { target: { value: "10" } });
  await until(() => expect(socket.last().camera.roll).toBe(rollOf(10)));
  await socket.picture(socket.last());
  fireEvent.click(rigBar(page).getByRole("button", { name: "Tripod" }));
  await until(() => expect(rigAsked(socket)?.kind).toBe("tripod"));
  close(rigAsked(socket).position, free.position, 9);
  expect([rigAsked(socket).roll, rigAsked(socket).fov, tag(page)]).toEqual([rollOf(10), 45, "Tripod · Mario"]);
  expect(rigOptions(page).queryByRole("slider", { name: "Height" })).toBeNull();
  expect(page.container.querySelector(".scene-lab-rig-note").textContent).toMatch(/choose Tripod again/);
  await rigDrawn(socket);
  // From the recorded camera it asks the recorded pose and stands at its camera.
  fireEvent.keyDown(document.body, { key: "r" });
  await until(() => expect(socket.pictures().at(-1).camera).toBeNull());
  await socket.picture(socket.pictures().at(-1));
  fireEvent.click(rigBar(page).getByRole("button", { name: "Tripod" }));
  await until(() => expect(socket.last()).toMatchObject({ kind: "pose" }));
  socket.text({ id: socket.last().id, kind: "pose", pose: { ...POSE, roll: -300, fov: 52 }, reason: null });
  await until(() => expect(rigAsked(socket)?.kind).toBe("tripod"));
  expect([rigAsked(socket).position, rigAsked(socket).roll, rigAsked(socket).fov]).toEqual([POSE.position, -300, 52]);
  await rigDrawn(socket);
  // Chase: behind him, with shot sizes; no tripod place.
  fireEvent.click(rigBar(page).getByRole("button", { name: "Chase" }));
  await until(() => expect(rigAsked(socket)?.kind).toBe("chase"));
  expect("position" in rigAsked(socket)).toBe(false);
  expect(rigOptions(page).getByRole("button", { name: "Wide" })).toBeTruthy();
  await rigDrawn(socket);
  // Overhead starts at its own height and keeps it to itself.
  const tuning = sceneCameraTuning();
  fireEvent.click(rigBar(page).getByRole("button", { name: "Overhead" }));
  await until(() => expect(rigAsked(socket)?.kind).toBe("overhead"));
  expect(rigAsked(socket).height).toBe(tuning.rigOverheadHeight);
  expect(rigOptions(page).queryByRole("button", { name: "Wide" })).toBeNull();
  await rigDrawn(socket);
  fireEvent.input(rigOptions(page).getByRole("slider", { name: "Height" }), { target: { value: "3000" } });
  await until(() => expect(rigAsked(socket).height).toBe(3000));
  await rigDrawn(socket);
  fireEvent.click(rigBar(page).getByRole("button", { name: "Front" }));
  await until(() => expect(rigAsked(socket)?.kind).toBe("front"));
  expect(rigAsked(socket).height).toBe(tuning.marioHeight);                    // his eye level, not 3000 up
});

test("Tilt and Lens turn and zoom an orbit circling a paused picture, and it goes on circling", async () => {
  const { page, socket, answered } = await heldOrbit();
  await tick(1016.7);
  await drawnAll(socket, answered);
  clock.now = 1100;
  fireEvent.input(lensRow(page).getByRole("slider", { name: "Tilt" }), { target: { value: "20" } });
  await until(() => expect(rigAsked(socket).roll).toBe(rollOf(20)));
  expect(rigAsked(socket).clock_ms).toBeCloseTo(SHOWS_2 + 100, 6);            // still on the paused orbit's clock
  await drawnAll(socket, answered);
  const fov = fovOf(lensAt(lensPosition(100)));
  fireEvent.input(lensRow(page).getByRole("slider", { name: "Lens" }), { target: { value: String(lensPosition(100)) } });
  await until(() => expect(rigAsked(socket).fov).toBe(fov));
  await drawnAll(socket, answered);
  await tick(1116.7);
  expect(rigAsked(socket)).toMatchObject({ roll: rollOf(20), fov });
  expect(rigAsked(socket).clock_ms).toBeCloseTo(SHOWS_2 + 116.7, 6);
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
    ["marioHeight", (t) => lookingAt(orbit, [0, 0, 0], t)],
    ["flyToStandoff", (t) => flyToTarget(orbit, [100, 0, -200], [0, 0, 0], t)],
    ["flyToMs", (t) => glideAt(orbit, lookingAt(orbit, [0, 0, 0]), 150, t)],
    ["tiltPerSecond", (t) => tiltAndLens(orbit, { tilt: 1 }, 0.1, 1, t)],
    ["lensPerSecond", (t) => tiltAndLens(orbit, { zoom: 1 }, 0.1, 1, t)],
    ["lensPerWheel", (t) => wheelLens(45, 100, t)],
    // The cinematic cameras' rows travel with every request: the lab draws what the panel shows
    // (their effect on the camera: tests/test_scene_lab_rigs.py).
    ...["rigCloseDistance", "rigMediumDistance", "rigWideDistance", "rigFov", "rigSmoothFrames", "rigAimFrames",
      "rigStillSpeed", "rigSideLead", "rigSideHoldFrames", "rigSideStayFrames", "rigWallStandoff", "rigPullFrames", "rigCraneDistance", "rigOrbitRadius",
      "rigOrbitSpeed", "rigLensKeepsSize", "rigOverheadHeight", "rigOverheadAngle"].map((key) => [key,
      (t) => rigRequest(startRig("orbit", null, t), t)]),
  ];
  expect(new Set(cases.map(([key]) => key))).toEqual(new Set(Object.keys(SCENE_CAMERA_DEFAULTS)));
  for (const [key, run] of cases) {
    expect(run(undefined)).toEqual(run(SCENE_CAMERA_DEFAULTS));
    const nudged = { ...SCENE_CAMERA_DEFAULTS, [key]: SCENE_CAMERA_DEFAULTS[key] * 0.9 + (key === "steepestDegrees" ? 0 : 0.001) };
    expect(run(nudged), key).not.toEqual(run(SCENE_CAMERA_DEFAULTS));
  }
});
