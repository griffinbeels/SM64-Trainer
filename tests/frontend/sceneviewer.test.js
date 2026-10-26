// @vitest-environment jsdom
// The Scene viewer page (ui/components/sceneviewer.js) against a scripted
// server: what it asks for, when, and what it shows. No renderer, GPU or
// browser: the socket, fetch, canvas, pointer lock and animation frames are
// stand-ins. The camera's math is components/scenecamera.js; the numbers it
// feels with are pinned to REFERENCE below, never to the shipped defaults
// (the Tune controls panel rewrites those).
import { h } from "preact";
import { render, cleanup, fireEvent, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { DRAG_DEAD_ZONE, SceneViewer, hintFor, pictureFraction, speedWords } from
  "../../src/sm64_events/ui/components/sceneviewer.js";
import { cameraOf, dollied, easeInOut, flightSpeed, flown, flyToTarget, glideAt, lookingAt, looked, orbitFromPose, slid,
  speedStepped, turned, worldPoint } from "../../src/sm64_events/ui/components/scenecamera.js";
import { SCENE_CAMERA_DEFAULTS, setSceneCameraTuning } from "../../src/sm64_events/ui/scenecameratuning.js";

const REFERENCE = { orbitPerPixel: 0.01, steepestDegrees: 80, levelPerPixel: 0.1, panPerPixel: 0.002,
  dollyPerWheel: 0.002, closestDistance: 40, farthestDistance: 20000, flightSpeed: 1000, lookPerPixel: 0.004,
  fastMultiplier: 5, slowMultiplier: 0.2, speedPerNotch: 1.5, levelPerSecond: 30, flyToStandoff: 300, flyToMs: 200,
  focusDistance: 600, marioHeight: 100 };
const FRAMES = Array.from({ length: 5 }, (_, i) => ({ level: 9, area: 1, game_frame: 1000 + i }));
const POSE = { focus: [10, 200, -30], position: [10, 500, 1170], roll: 0, fov: 45, mario: [40, 80, -60] };
const PER_DEGREE = 65536 / 360;
let sockets, opened, fetches, frames, locked;

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
  sockets = [];
  fetches = [];
  frames = [];
  locked = null;
  setSceneCameraTuning(REFERENCE);
  opened = { session: 1, name: "bob-minute.sm64scene", fps: 30, frames: FRAMES, renderer_pid: 7,
    free_camera: { available: true, reason: null } };
  globalThis.WebSocket = FakeSocket;
  globalThis.requestAnimationFrame = (step) => { frames.push(step); return frames.length; };
  globalThis.fetch = vi.fn((url, options = {}) => {
    fetches.push([url, options.method || "GET", options.body]);
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
  HTMLCanvasElement.prototype.requestPointerLock = vi.fn(function lock() {
    locked = this;
    document.dispatchEvent(new Event("pointerlockchange"));
    return Promise.resolve();
  });
  Object.defineProperty(document, "pointerLockElement", { configurable: true, get: () => locked });
  document.exitPointerLock = vi.fn(() => { locked = null; document.dispatchEvent(new Event("pointerlockchange")); });
});
afterEach(() => { cleanup(); setSceneCameraTuning(null); });

async function openScene() {
  const page = render(h(SceneViewer, {}));
  const button = await waitFor(() => {
    const found = page.getByRole("button", { name: "Open" });
    expect(found.disabled).toBe(false);
    return found;
  });
  fireEvent.click(button);
  await waitFor(() => expect(sockets.length).toBe(1));
  const socket = sockets[0];
  socket.open();
  await waitFor(() => expect(socket.sent.length).toBe(1));
  const root = page.container.querySelector(".scene-lab");
  return { page, socket, root, canvas: root.querySelector("canvas") };
}

// Free camera from `pose`: answers the pose question and the first picture.
async function enterFree(page, socket, pose = POSE) {
  await socket.picture(socket.sent[0]);
  fireEvent.click(page.getByRole("button", { name: "Free camera" }));
  await waitFor(() => expect(socket.last()).toMatchObject({ kind: "pose" }));
  socket.text({ id: socket.last().id, kind: "pose", pose, reason: null });
  await waitFor(() => expect(socket.last().camera).toBeTruthy());
  await socket.picture(socket.last());
  return socket.last().camera;
}

// The page follows the pointer on pointerrawupdate where the browser has it (jsdom does).
const move = (canvas, init) => fireEvent(canvas, new PointerEvent("pointerrawupdate", { bubbles: true, ...init }));
const tick = async (at) => {
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

test("one picture is in flight at a time and the newest step wins", async () => {
  const { page, socket, root } = await openScene();
  for (let i = 0; i < 3; i++) fireEvent.keyDown(root, { key: "ArrowRight" });
  await new Promise((resolve) => { setTimeout(resolve, 20); });
  expect(socket.sent.length).toBe(1);                  // still waiting on frame 0
  await socket.picture(socket.sent[0]);
  await waitFor(() => expect(socket.sent.length).toBe(2));
  expect(socket.sent[1].frame).toBe(3);                // frames 1 and 2 were never asked for
  await socket.picture(socket.sent[1]);
  await waitFor(() => expect(page.container.textContent).toContain("Frame 4 of 5"));
  expect(page.container.textContent).toContain("level 9, area 1, game frame 1003");
  fireEvent.keyDown(root, { key: "End" });
  await waitFor(() => expect(socket.sent.length).toBe(3));
  expect(socket.sent[2].frame).toBe(4);                // stepping stops at the last frame
});

test("the next picture is asked for while one decodes, and a late decode never covers a newer picture", async () => {
  const decodes = [], drawn = [];
  globalThis.createImageBitmap = vi.fn((blob) => new Promise((resolve) => {
    decodes.push(() => resolve({ blob, close: () => {} }));
  }));
  HTMLCanvasElement.prototype.getContext = () => ({ drawImage: (bitmap) => drawn.push(bitmap.blob) });
  const { page, socket, root } = await openScene();
  fireEvent.keyDown(root, { key: "ArrowRight" });                  // waits behind picture 0
  await new Promise((resolve) => { setTimeout(resolve, 20); });
  expect(socket.sent.length).toBe(1);
  socket.picture(socket.sent[0], { encoding: "jpeg" });             // arrives; its decode waits
  await waitFor(() => expect(socket.sent.length).toBe(2));         // asked for before picture 0 drew
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
  await waitFor(() => expect(page.container.textContent).toContain("Frame 2 of 5"));
  expect(page.container.querySelector(".scene-lab-readout").textContent).toContain("input to picture");
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

// ------------------------------------------------------------------ flight

async function startFlying(page, socket, canvas) {
  await enterFree(page, socket);
  fireEvent.pointerDown(canvas, { button: 2, buttons: 2, pointerId: 3 });
  await waitFor(() => expect(page.container.querySelector(".scene-lab-hint").textContent).toContain("W A S D"));
}

test("holding the right button flies: W moves at a constant speed where the camera looks, and letting go stops it", async () => {
  const { page, socket, root, canvas } = await openScene();
  const keyBefore = socket.sent.length;
  fireEvent.keyDown(root, { key: "w", code: "KeyW" });                         // not flying: W is nobody's
  fireEvent.keyUp(root, { key: "w", code: "KeyW" });
  await new Promise((resolve) => { setTimeout(resolve, 20); });
  expect(socket.sent.length).toBe(keyBefore);
  await startFlying(page, socket, canvas);
  expect(HTMLCanvasElement.prototype.requestPointerLock).toHaveBeenCalledTimes(1);
  const start = socket.last().camera;
  fireEvent.keyDown(root, { key: "w", code: "KeyW" });
  await tick(5000);                                                             // the first step, from the key
  await waitFor(() => expect(socket.last().camera.position).not.toEqual(start.position));
  const first = socket.last().camera;
  await socket.picture(socket.last());
  await tick(5050);                                                             // 50 ms later
  await waitFor(() => expect(socket.last().camera.position).not.toEqual(first.position));
  const moved = socket.last().camera, view = [0, 1, 2].map((i) => start.focus[i] - start.position[i]);
  const length = Math.hypot(...view), travelled = [0, 1, 2].map((i) => moved.position[i] - first.position[i]);
  close(travelled, view.map((value) => value / length * REFERENCE.flightSpeed * 0.05), 6);
  await socket.picture(socket.last());
  fireEvent.keyUp(root, { key: "w", code: "KeyW" });
  await tick(5100);
  const after = socket.sent.length;
  await tick(6000);
  expect(frames.length).toBe(0);                                                // no coasting: the loop ended
  expect(socket.sent.length).toBe(after);
  fireEvent.pointerUp(canvas, { button: 2, buttons: 0, pointerId: 3 });
  expect(document.exitPointerLock).toHaveBeenCalled();
  expect(page.container.querySelector(".scene-lab-hint").textContent).not.toContain("W A S D");
});

test("moving the mouse while flying turns the view where the camera stands, and the picture says how to fly", async () => {
  const { page, socket, canvas } = await openScene();
  await startFlying(page, socket, canvas);
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

test("Shift flies faster, Alt slower, and the wheel sets the speed while flying instead of moving closer", async () => {
  const { page, socket, root, canvas } = await openScene();
  await startFlying(page, socket, canvas);
  const sentBefore = socket.sent.length;
  fireEvent.wheel(canvas, { deltaY: -100 });                                    // one notch up: faster
  await new Promise((resolve) => { setTimeout(resolve, 20); });
  expect(socket.sent.length).toBe(sentBefore);                                  // no dolly picture
  expect(page.getByRole("group", { name: "Flight speed" }).textContent).toContain(speedWords(REFERENCE.speedPerNotch));
  const start = socket.last().camera;
  fireEvent.keyDown(root, { key: "e", code: "KeyE", shiftKey: true });
  await tick(5000);
  await waitFor(() => expect(socket.last().camera.focus[1]).not.toBe(start.focus[1]));
  const first = socket.last().camera;
  await socket.picture(socket.last());
  await tick(5100);
  await waitFor(() => expect(socket.last().camera.focus[1]).not.toBe(first.focus[1]));
  const fast = socket.last().camera;
  expect(fast.focus[1] - first.focus[1])
    .toBeCloseTo(REFERENCE.flightSpeed * REFERENCE.speedPerNotch * REFERENCE.fastMultiplier * 0.1, 6);
  await socket.picture(socket.last());
  fireEvent.keyDown(root, { key: "Alt", code: "AltLeft", shiftKey: false, altKey: true });
  await tick(5200);
  await waitFor(() => expect(socket.last().camera.focus[1]).not.toBe(fast.focus[1]));
  expect(socket.last().camera.focus[1] - fast.focus[1])
    .toBeCloseTo(REFERENCE.flightSpeed * REFERENCE.speedPerNotch * REFERENCE.slowMultiplier * 0.1, 6);
  fireEvent.click(page.getByRole("button", { name: "Reset" }));
  expect(page.getByRole("group", { name: "Flight speed" }).textContent).toContain("1.0x");
});

test.each([
  ["Esc", (root) => fireEvent.keyDown(root, { key: "Escape" })],
  ["leaving the window", () => window.dispatchEvent(new Event("blur"))],
  ["letting go of the right button", (root, canvas) => fireEvent.pointerUp(canvas, { button: 2, buttons: 0, pointerId: 3 })],
  ["the browser ending the pointer lock", () => { locked = null; document.dispatchEvent(new Event("pointerlockchange")); }],
])("%s stops every movement and gives the pointer back", async (_label, end) => {
  const { page, socket, root, canvas } = await openScene();
  await startFlying(page, socket, canvas);
  fireEvent.keyDown(root, { key: "d", code: "KeyD" });
  await tick(0);
  await tick(50);
  await waitFor(() => expect(frames.length).toBe(1));
  end(root, canvas);
  await tick(100);
  const after = socket.sent.length;
  await tick(400);
  expect(socket.sent.length).toBe(after);
  expect(frames.length).toBe(0);
  expect(locked).toBeNull();
  expect(page.container.querySelector(".scene-lab-hint").textContent).not.toContain("W A S D");
});

test("arrows still step and Space still plays while flying: one keyboard owner", async () => {
  const { page, socket, root, canvas } = await openScene();
  await startFlying(page, socket, canvas);
  fireEvent.keyDown(root, { key: "ArrowRight", code: "ArrowRight" });
  await waitFor(() => expect(socket.last().frame).toBe(1));
  expect(socket.last().camera).toBeTruthy();
  await socket.picture(socket.last());
  fireEvent.keyDown(root, { key: " ", code: "Space" });
  await waitFor(() => expect(page.getByRole("button", { name: "Pause" })).toBeTruthy());
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
  const hint = () => page.container.querySelector(".scene-lab-hint").textContent;
  expect(hint()).toContain("hold the right button");
  await enterFree(page, socket);
  expect(hint()).toContain("Drag to turn around the focus point");
  fireEvent.pointerDown(canvas, { button: 2, buttons: 2, pointerId: 3 });
  await waitFor(() => expect(hint()).toContain("W A S D"));
  expect(hintFor({ freeAvailable: false, scene: { free_camera: { reason: "No state." } } })).toContain("No state.");
});

test("without a free camera the controls say why instead of doing nothing", async () => {
  opened.free_camera = { available: false, reason: "This recording carries no scene state." };
  const { page, canvas } = await openScene();
  expect(page.getByRole("button", { name: "Free camera" }).disabled).toBe(true);
  expect(page.getByRole("button", { name: "Look at Mario" }).disabled).toBe(true);
  expect(page.getByRole("button", { name: "Set focus" }).disabled).toBe(true);
  expect(page.container.querySelector(".scene-lab-hint").textContent)
    .toContain("Only the recorded camera is available: This recording carries no scene state.");
  fireEvent.pointerDown(canvas, { button: 2, buttons: 2, pointerId: 3 });
  expect(HTMLCanvasElement.prototype.requestPointerLock).not.toHaveBeenCalled();
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
