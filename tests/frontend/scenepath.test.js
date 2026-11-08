// @vitest-environment jsdom
// The Scene viewer's camera path (ui/components/scenepath.js: the math; scenepathbar.js: the block and
// SceneLink's side). The math against hand-made keys: each key met exactly on its frame, smooth
// between, ease, the recorded camera's blend, a tilt and a lens. Then the block on the real page
// against a scripted lab (GET and PUT /api/scene-lab/camera-paths are a Map here; the socket, canvas
// and animation frames are stand-ins as in sceneviewer.test.js): + Key, the path on screen, a key's
// jump, and a Play and a recording following the path.
import { h } from "preact";
import { render, cleanup, fireEvent, within } from "@testing-library/preact";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { HOLD, makeKey, orderedKeys, pathCamera, pathClock } from "../../src/sm64_events/ui/components/scenepath.js";
import { SceneViewer } from "../../src/sm64_events/ui/components/sceneviewer.js";
import { setSceneCameraTuning } from "../../src/sm64_events/ui/scenecameratuning.js";

const A = { focus: [0, 0, 0], position: [0, 0, 1000], roll: 0, fov: 45 };
const B = { focus: [100, 0, 0], position: [1000, 200, 0], roll: 0, fov: 45 };
const C = { focus: [200, 50, 0], position: [1000, 400, -1000], roll: 0, fov: 45 };
const TIMES = Float64Array.from({ length: 31 }, (_, k) => k * 1000 / 30);     // one second at 30 a second
const near = (a, b, within = 1e-6) => a.every((value, i) => Math.abs(value - b[i]) < within);
const same = (camera, key) => near(camera.focus, key.focus) && near(camera.position, key.position)
  && camera.roll === key.roll && Math.abs(camera.fov - key.fov) < 1e-9;

function path(...keys) {
  const ordered = orderedKeys(keys.map(makeKey), TIMES.length), clock = pathClock(TIMES, ordered);
  return { keys: ordered, clock, at: (q) => pathCamera(ordered, clock.keyQ, q) };
}

test("each key is met exactly on its frame, and the camera moves smoothly between", () => {
  const p = path({ frame: 3, camera: A }, { frame: 12, camera: B }, { frame: 24, camera: C });
  expect(p.clock.keyQ).toEqual([TIMES[3], TIMES[12], TIMES[24]]);
  for (const [j, key] of [A, B, C].entries()) expect(same(p.at(p.clock.qStart(p.keys[j].frame)), key)).toBe(true);
  // Sampled every millisecond: no step jumps (the most any one moves is a few units), and the camera
  // passes everywhere between A and B, never beyond them.
  let last = p.at(0), most = 0;
  for (let q = 1; q <= TIMES[30]; q += 1) {
    const camera = p.at(q);
    most = Math.max(most, Math.hypot(...camera.position.map((value, i) => value - last.position[i])));
    last = camera;
  }
  expect(most).toBeLessThan(10);                                      // A to B peaks near 7 units a ms
  const middle = p.at((TIMES[3] + TIMES[12]) / 2).position;
  expect(middle[0]).toBeGreaterThan(0);
  expect(middle[0]).toBeLessThan(1000);
});

test("before the first key and after the last, the camera stays on them", () => {
  const p = path({ frame: 5, camera: A }, { frame: 20, camera: B });
  expect(same(p.at(0), A)).toBe(true);
  expect(same(p.at(TIMES[30]), B)).toBe(true);
  expect(path({ frame: 5, camera: A }).at(TIMES[25])).toEqual(path({ frame: 5, camera: A }).at(0));
  expect(path().at(100)).toBe(null);
});

test("ease slows the camera into and out of a key; with ease off it passes through", () => {
  const speedAt = (p, q) => Math.hypot(...p.at(q + 0.5).position.map((value, i) => value - p.at(q - 0.5).position[i]));
  const eased = path({ frame: 0, camera: A }, { frame: 15, camera: B }, { frame: 30, camera: C });
  const through = path({ frame: 0, camera: A }, { frame: 15, camera: B, ease: false }, { frame: 30, camera: C });
  const middle = TIMES[15];
  expect(speedAt(eased, middle)).toBeLessThan(0.01);                 // stopped at B
  expect(speedAt(through, middle)).toBeGreaterThan(1);               // still moving at B
  expect(same(through.at(middle), B)).toBe(true);                    // and still meets it
  // Leaving the first key and arriving at the last, both slow down to rest.
  expect(speedAt(through, 0.5)).toBeLessThan(0.05);
  expect(speedAt(through, TIMES[30] - 0.5)).toBeLessThan(0.05);
});

test("into and out of the recorded camera: a smooth blend, and the recorded camera itself beyond", () => {
  const p = path({ frame: 6, camera: null }, { frame: 18, camera: A }, { frame: 27, camera: null });
  expect(p.at(TIMES[2])).toBe(null);                                  // before a recorded first key
  expect(p.at(TIMES[29])).toBe(null);                                 // after a recorded last key
  expect(p.at(TIMES[6])).toBe(null);
  expect(same(p.at(TIMES[18]), A)).toBe(true);
  // Out of the recorded camera toward A: the weight falls from 1 to 0, slowly at each end.
  const weights = Array.from({ length: 13 }, (_, k) => p.at(TIMES[6] + (TIMES[18] - TIMES[6]) * k / 12)?.recorded ?? 0);
  expect(weights.slice(1, -1).every((weight, k) => weight < (k === 0 ? 1 : weights[k]))).toBe(true);
  expect(weights[1]).toBeGreaterThan(0.95);
  expect(weights[11]).toBeLessThan(0.05);
  const blend = p.at((TIMES[18] + TIMES[27]) / 2);                   // halfway back into it
  expect(blend.recorded).toBeCloseTo(0.5, 6);
  expect(same({ ...blend }, A)).toBe(true);                          // the lab blends A with the recorded camera
});

test("a tilt turns the short way round, and the lens zooms evenly", () => {
  const left = { ...A, roll: 32000 }, right = { ...B, roll: -32000 };
  const p = path({ frame: 0, camera: left }, { frame: 30, camera: right });
  const rolls = Array.from({ length: 11 }, (_, k) => p.at(TIMES[30] * k / 10).roll);
  expect(rolls.every((roll) => Math.abs(roll) >= 32000)).toBe(true);  // through 32768, never through 0
  const wide = { ...A, fov: 90 }, tight = { ...B, fov: 10 };
  const lens = path({ frame: 0, camera: wide, ease: false }, { frame: 30, camera: tight, ease: false });
  const half = lens.at(TIMES[30] / 2).fov, tan = (fov) => Math.tan(fov * Math.PI / 360);
  expect(tan(half) ** 2).toBeCloseTo(tan(90) * tan(10), 6);         // halfway in log(tan): the geometric mean
});

test("keys play in frame order; keys past the scene's end stay out", () => {
  const keys = orderedKeys([makeKey({ frame: 20, camera: B }), makeKey({ frame: 4, camera: A }),
    makeKey({ frame: 400, camera: C })], TIMES.length);
  expect(keys.map((key) => key.frame)).toEqual([4, 20]);
  expect(keys.every((key) => key.hold === 0)).toBe(true);
  expect(HOLD.ms).toBeGreaterThan(0);
});

// ---------------------------------------------------------------- the block on the page

const FRAMES = Array.from({ length: 31 }, (_, i) => ({ level: 9, area: 1, game_frame: 1000 + i, recorded_ms: TIMES[i] }));
const POSE = { focus: [10, 200, -30], position: [10, 500, 1170], roll: 0, fov: 45, mario: [40, 80, -60] };
const ROUTE = "/api/scene-lab/camera-paths";
const REFERENCE = { orbitPerPixel: 0.01, steepestDegrees: 80, levelPerPixel: 0.1, panPerPixel: 0.002,
  dollyPerWheel: 0.002, closestDistance: 40, farthestDistance: 20000, flightSpeed: 1000, lookPerPixel: 0.004,
  fastMultiplier: 5, slowMultiplier: 0.2, speedPerNotch: 1.5, levelPerSecond: 30, flyToStandoff: 300, flyToMs: 200,
  marioHeight: 100 };
let sockets, fetches, frames, saved;
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
      camera_used: request.camera && request.camera.focus ? request.camera : null, timings: {}, server_ms: 3, ...extra }));
    const buffer = new ArrayBuffer(4 + header.length + 36);
    new DataView(buffer).setUint32(0, header.length, true);
    new Uint8Array(buffer, 4).set(header);
    return this.onmessage({ data: buffer });
  }
  text(message) { this.onmessage({ data: JSON.stringify(message) }); }
  pictures() { return this.sent.filter((message) => !message.kind); }
}

const respond = (body, status = 200) => Promise.resolve({ ok: status < 300, status, json: () => Promise.resolve(body) });
const puts = () => fetches.filter(([url, method]) => url.startsWith(ROUTE) && method === "PUT")
  .map(([, , body]) => JSON.parse(body).keys);

beforeEach(() => {
  clock.now = 1000;
  vi.spyOn(performance, "now").mockImplementation(() => clock.now);
  Object.assign(globalThis, { WebSocket: FakeSocket });
  globalThis.requestAnimationFrame = (step) => { frames.push(step); return frames.length; };
  setSceneCameraTuning(REFERENCE);
  sockets = []; fetches = []; frames = [];
  saved = new Map([["9:1", []]]);
  globalThis.fetch = vi.fn((url, options = {}) => {
    fetches.push([url, options.method || "GET", options.body]);
    if (url === "/api/local-access") return respond({}, 404);
    if (url === "/api/scene-lab/status") return respond({ ready: true, problems: [], hold: null, session: null,
      ended: null, packages: [{ name: "bob-minute.sm64scene", bytes: 11 << 20 }] });
    if (url === "/api/scene-lab/open") return respond({ session: 1, name: "bob-minute.sm64scene", fps: 30, frames: FRAMES,
      renderer_pid: 7, free_camera: { available: true, reason: null } });
    if (url.startsWith(ROUTE) && options.method === "PUT") {
      const keys = JSON.parse(options.body).keys;
      saved.set("9:1", keys);
      return respond({ keys, problem: null });
    }
    if (url.startsWith(ROUTE)) return respond({ keys: saved.get("9:1") || [], problem: null });
    if (url.startsWith("/api/scene-lab/camera-presets")) return respond({ slots: [], problem: null });
    return respond({}, 404);
  });
  HTMLCanvasElement.prototype.getContext = () => ({
    createImageData: (w, hgt) => ({ data: new Uint8ClampedArray(w * hgt * 4), width: w, height: hgt }),
    putImageData: () => {}, drawImage: () => {},
  });
  HTMLCanvasElement.prototype.getBoundingClientRect = () => ({ left: 0, top: 0, width: 640, height: 480 });
});
afterEach(() => { cleanup(); setSceneCameraTuning(null); vi.restoreAllMocks(); });

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

// Answers every picture the page asks for (and every pose, with POSE) until none waits.
async function answerAll(socket) {
  for (let turn = 0; turn < 40; turn++) {
    const waiting = socket.sent.filter((message) => !message.answered);
    if (!waiting.length) return;
    for (const message of waiting) {
      message.answered = true;
      if (message.kind === "pose") socket.text({ id: message.id, kind: "pose", pose: POSE, reason: null });
      else if (!message.kind) socket.picture(message);
    }
    await new Promise((resolve) => { setTimeout(resolve, 0); });
  }
}

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
  await answerAll(socket);
  const block = await until(() => page.getByRole("group", { name: "Camera path" }));
  await until(() => expect(within(block).getByRole("button", { name: "Key" }).disabled).toBe(false));
  return { page, socket, block: within(block) };
}

const step = async (socket, page, key) => {
  fireEvent.keyDown(page.container.querySelector(".scene-lab"), { key });
  await answerAll(socket);
};

test("+ Key keeps the frame and the camera on screen; from the Recorded camera, the recorded camera itself", async () => {
  const { page, socket, block } = await openScene();
  fireEvent.click(block.getByRole("button", { name: "Key" }));      // the Recorded camera on frame 1
  await until(() => expect(puts().length).toBe(1));
  expect(puts()[0]).toEqual([{ frame: 0, camera: null, ease: true, hold: 0, speed: 1 }]);
  fireEvent.click(page.getByRole("button", { name: "Free camera" }));
  await answerAll(socket);
  for (let k = 0; k < 10; k++) await step(socket, page, "ArrowRight");
  fireEvent.click(block.getByRole("button", { name: "Key" }));
  await until(() => expect(puts().length).toBe(2));
  expect(puts()[1]).toEqual([{ frame: 0, camera: null, ease: true, hold: 0, speed: 1 },
    { frame: 10, camera: { focus: POSE.focus, position: POSE.position, roll: 0, fov: 45 }, ease: true, hold: 0, speed: 1 }]);
  await until(() => expect(block.getByRole("button", { name: "Frame 11" })).toBeTruthy());
  expect(page.container.querySelector(".scene-lab-path-keys .is-here")?.textContent).toContain("Frame 11");
});

test("the path on screen draws each frame from the path's camera; a key's button jumps to it", async () => {
  saved.set("9:1", [makeKey({ frame: 4, camera: A }), makeKey({ frame: 20, camera: B })]);
  const { page, socket, block } = await openScene();
  await until(() => expect(block.getByRole("button", { name: "Path" }).disabled).toBe(false));
  fireEvent.click(block.getByRole("button", { name: "Path" }));
  await answerAll(socket);
  expect(page.container.querySelector(".scene-lab-tag").textContent).toBe("Camera path");
  expect(socket.pictures().at(-1).camera).toEqual({ ...A, fov: 45 });  // frame 1: before the first key
  const p = path({ frame: 4, camera: A }, { frame: 20, camera: B });
  for (let k = 0; k < 12; k++) await step(socket, page, "ArrowRight");
  const asked = socket.pictures().at(-1);
  expect(asked.frame).toBe(12);
  expect(near(asked.camera.position, p.at(TIMES[12]).position)).toBe(true);
  fireEvent.click(block.getByRole("button", { name: "Frame 21" }));
  await answerAll(socket);
  expect(socket.pictures().at(-1).frame).toBe(20);
  expect(same(socket.pictures().at(-1).camera, B)).toBe(true);
  fireEvent.keyDown(page.container.querySelector(".scene-lab"), { key: "r" });
  await answerAll(socket);
  expect(socket.pictures().at(-1).camera).toBe(null);
  expect(block.getByRole("button", { name: "Path" }).getAttribute("aria-pressed")).toBe("false");
});

// The moment on the path's clock whose camera stands at `x` (A to B moves x one way only).
function momentOf(p, x, low, high) {
  for (let turn = 0; turn < 80; turn++) {
    const mid = (low + high) / 2;
    if (p.at(mid).position[0] < x) low = mid; else high = mid;
  }
  return (low + high) / 2;
}

// Display frames at 60 a second (the page measures their length and asks that far ahead).
async function displayFrames(socket, count) {
  for (let k = 0; k < count; k++) {
    clock.now += 1000 / 60;
    for (const run of frames.splice(0)) run(clock.now);
    await answerAll(socket);
  }
}

test("Play follows the path every display frame, and a recording keeps the path's camera on its own clock", async () => {
  saved.set("9:1", [makeKey({ frame: 0, camera: A }), makeKey({ frame: 30, camera: B })]);
  const { page, socket, block } = await openScene();
  await until(() => expect(block.getByRole("button", { name: "Path" }).disabled).toBe(false));
  fireEvent.click(block.getByRole("button", { name: "Path" }));
  await answerAll(socket);
  fireEvent.click(page.getByRole("button", { name: "Record" }));
  fireEvent.click(page.getByRole("button", { name: "Play" }));
  await answerAll(socket);
  const before = socket.pictures().length;
  await displayFrames(socket, 20);
  // Every display frame asks the picture again from the path's camera then.
  const moved = socket.pictures().slice(before);
  expect(moved.length).toBeGreaterThanOrEqual(19);
  expect(new Set(moved.map((message) => JSON.stringify(message.camera))).size).toBe(moved.length);
  fireEvent.click(page.getByRole("button", { name: "Stop" }));
  const sent = await until(() => {
    const found = fetches.find(([url, method]) => url === "/api/scene-lab/export" && method === "POST");
    expect(found).toBeTruthy();
    return JSON.parse(found[2]);
  });
  // The recording's cameras are the path's: one constant ties each entry's time to the path's clock,
  // so the export draws the path again exactly (it draws each entry's own camera, test_scene_export.py).
  const p = path({ frame: 0, camera: A }, { frame: 30, camera: B });
  const playing = sent.timeline.filter((entry) => entry.camera && entry.camera.position[0] > 1e-6);
  expect(playing.length).toBeGreaterThanOrEqual(15);
  const offset = momentOf(p, playing[0].camera.position[0], 0, TIMES[30]) - playing[0].t_ms;
  for (const entry of playing) {
    const camera = p.at(entry.t_ms + offset);
    expect(near(entry.camera.position, camera.position, 1e-3) && near(entry.camera.focus, camera.focus, 1e-3)).toBe(true);
    const q = entry.t_ms + offset, frame = entry.scene_frame;        // and its picture is the one showing then
    expect(q).toBeGreaterThan(TIMES[frame] - 1e-6);
    expect(q).toBeLessThan((TIMES[frame + 1] ?? Infinity) + 1e-6);
  }
});
