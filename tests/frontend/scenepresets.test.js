// @vitest-environment jsdom
// The Scene viewer's Cameras strip (ui/components/scenepresets.js, drawn by sceneviewer.js) against a
// scripted lab: saving the picture's camera into a slot, naming, renaming, deleting, dragging a slot
// onto another, keys 1-9 and who keeps them, cutting while Play goes on, and a scene moving into
// another area. The lab's preset routes are a Map here (GET and PUT /api/scene-lab/camera-presets);
// the socket, canvas and animation frames are stand-ins as in sceneviewer.test.js.
import { h } from "preact";
import { render, cleanup, fireEvent } from "@testing-library/preact";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { SceneViewer } from "../../src/sm64_events/ui/components/sceneviewer.js";
import { cameraOf, orbitFromPose } from "../../src/sm64_events/ui/components/scenecamera.js";
import { placeOf, sameCamera } from "../../src/sm64_events/ui/components/scenepresets.js";
import { setSceneCameraTuning } from "../../src/sm64_events/ui/scenecameratuning.js";

const REFERENCE = { orbitPerPixel: 0.01, steepestDegrees: 80, levelPerPixel: 0.1, panPerPixel: 0.002,
  dollyPerWheel: 0.002, closestDistance: 40, farthestDistance: 20000, flightSpeed: 1000, lookPerPixel: 0.004,
  fastMultiplier: 5, slowMultiplier: 0.2, speedPerNotch: 1.5, levelPerSecond: 30, flyToStandoff: 300, flyToMs: 200,
  marioHeight: 100 };
// Frames 0-2 in area 1 of level 9, a fade between areas that names none, then area 2.
const FRAMES = [0, 1, 2].map((i) => ({ level: 9, area: 1, game_frame: 1000 + i }))
  .concat([{ level: null, area: null, game_frame: 1003 }], [4, 5].map((i) => ({ level: 9, area: 2, game_frame: 1000 + i })));
const POSE = { focus: [10, 200, -30], position: [10, 500, 1170], roll: 0, fov: 45, mario: [40, 80, -60] };
const WIDE = { focus: [100, 200, -300], position: [100, 900, 1700], roll: 0, fov: 45 };
const LOW = { focus: [-50, 0, 10], position: [400, 20, 10], roll: -1200, fov: 30.5 };
const CANNON = { focus: [0, 0, 0], position: [0, 3000, 10], roll: 0, fov: 60 };
const ROUTE = "/api/scene-lab/camera-presets";
let sockets, fetches, frames, saved, putAnswer;
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

const respond = (body, status = 200) => Promise.resolve({ ok: status < 300, status, json: () => Promise.resolve(body) });
const placeOfUrl = (url) => { const query = new URL(url, "http://lab").searchParams; return `${query.get("level")}:${query.get("area")}`; };
const puts = () => fetches.filter(([url, method]) => url.startsWith(ROUTE) && method === "PUT")
  .map(([url, , body]) => ({ place: placeOfUrl(url), slots: JSON.parse(body).slots }));

beforeEach(() => {
  clock.now = 1000;
  vi.spyOn(performance, "now").mockImplementation(() => clock.now);
  Object.assign(globalThis, { WebSocket: FakeSocket });
  globalThis.requestAnimationFrame = (step) => { frames.push(step); return frames.length; };
  setSceneCameraTuning(REFERENCE);
  sockets = []; fetches = []; frames = []; putAnswer = null;
  saved = new Map([["9:1", { slots: [{ slot: 1, name: "Wide hall", camera: WIDE }, { slot: 2, name: "Low run", camera: LOW }],
    problem: null }], ["9:2", { slots: [{ slot: 1, name: "Cannon", camera: CANNON }], problem: null }]]);
  globalThis.fetch = vi.fn((url, options = {}) => {
    fetches.push([url, options.method || "GET", options.body]);
    if (url === "/api/local-access") return respond({}, 404);
    if (url === "/api/scene-lab/status") return respond({ ready: true, problems: [], hold: null, session: null,
      ended: null, packages: [{ name: "bob-minute.sm64scene", bytes: 11 << 20 }] });
    if (url === "/api/scene-lab/open") return respond({ session: 1, name: "bob-minute.sm64scene", fps: 30, frames: FRAMES,
      renderer_pid: 7, free_camera: { available: true, reason: null } });
    if (url.startsWith(ROUTE) && options.method === "PUT") {
      if (putAnswer) return putAnswer();
      const slots = JSON.parse(options.body).slots;
      saved.set(placeOfUrl(url), { slots, problem: null });
      return respond({ slots, problem: null });
    }
    if (url.startsWith(ROUTE)) return respond(saved.get(placeOfUrl(url)) || { slots: [], problem: null });
    return respond({}, 404);
  });
  HTMLCanvasElement.prototype.getContext = () => ({
    createImageData: (w, hgt) => ({ data: new Uint8ClampedArray(w * hgt * 4), width: w, height: hgt }),
    putImageData: () => {}, drawImage: () => {},
  });
  HTMLCanvasElement.prototype.getBoundingClientRect = () => ({ left: 0, top: 0, width: 640, height: 480 });
  HTMLCanvasElement.prototype.setPointerCapture = vi.fn();
  HTMLCanvasElement.prototype.releasePointerCapture = vi.fn();
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
const settled = async (turns = 5) => {
  for (let turn = 0; turn < turns; turn++) await new Promise((resolve) => { setTimeout(resolve, 0); });
};

async function openScene({ free = true, filled = free } = {}) {
  if (!free) {
    const answer = globalThis.fetch;
    globalThis.fetch = vi.fn((url, options = {}) => (url === "/api/scene-lab/open"
      ? (fetches.push([url, "POST", options.body]), respond({ session: 1, name: "x", fps: 30, frames: FRAMES, renderer_pid: 7,
        free_camera: { available: false, reason: "This recording has no symbol map, so only its own camera shows." } }))
      : answer(url, options)));
  }
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
  await socket.picture(socket.sent[0]);
  const root = page.container.querySelector(".scene-lab");
  const strip = root.querySelector(".scene-lab-cameras");
  if (filled) await until(() => expect(strip.querySelector('[aria-label="Wide hall: camera 1"]')).toBeTruthy());
  return { page, socket, root, strip, canvas: root.querySelector("canvas") };
}

const slotOf = (strip, number) => strip.querySelector(`.scene-lab-slot[data-slot="${number}"]`);
const names = (strip) => [...strip.querySelectorAll(".scene-lab-slot")].map((slot) =>
  slot.querySelector(".scene-lab-slot-cut .scene-lab-slot-name")?.textContent ?? null);
const pressed = (strip) => [...strip.querySelectorAll('.scene-lab-slot-cut[aria-pressed="true"]')]
  .map((button) => Number(button.closest(".scene-lab-slot").dataset.slot));
const key = (root, name, init = {}) => fireEvent.keyDown(root, { key: name, ...init });
// The camera a cut to `camera` sends: the free camera rebuilt from it.
const cutCamera = (camera) => cameraOf(orbitFromPose(camera.focus, camera.position, { roll: camera.roll, fov: camera.fov }));
const close = (a, b) => a.forEach((value, i) => expect(value).toBeCloseTo(b[i], 6));

async function enterFree(page, socket, pose = POSE) {
  fireEvent.click(page.getByRole("button", { name: "Free camera" }));
  await until(() => expect(socket.last()).toMatchObject({ kind: "pose" }));
  socket.text({ id: socket.last().id, kind: "pose", pose, reason: null });
  await until(() => expect(socket.last().camera?.focus).toBeTruthy());
  await socket.picture(socket.last());
  return socket.last().camera;
}

// ------------------------------------------------------------------ where a set belongs

test("a frame's place is its own course and area, else the nearest earlier frame that names both", () => {
  expect(placeOf(FRAMES, 1)).toMatchObject({ level: 9, area: 1 });
  expect(placeOf(FRAMES, 3)).toMatchObject({ level: 9, area: 1 });     // the fade between areas keeps the last
  expect(placeOf(FRAMES, 4)).toMatchObject({ level: 9, area: 2 });
  expect(placeOf([{ level: null, area: null }, { level: 6, area: 3 }], 0)).toMatchObject({ level: 6, area: 3 });
  expect(placeOf([{ level: null, area: null }], 0)).toBe(null);
});

test("the strip shows the saved cameras of the area on screen, in slot order, and asks once per area", async () => {
  const { strip, root, socket } = await openScene();
  expect(names(strip)).toEqual(["Wide hall", "Low run", null, null, null, null, null, null, null]);
  expect(strip.querySelectorAll(".scene-lab-slot-save").length).toBe(7);
  expect(strip.querySelector(".scene-lab-hint").textContent).toMatch(/Keys 1-9 cut/);
  key(root, "ArrowRight");
  await socket.picture(socket.last());
  await settled();
  expect(fetches.filter(([url]) => url.startsWith(ROUTE)).map(([url]) => placeOfUrl(url))).toEqual(["9:1"]);
});

// ------------------------------------------------------------------ saving

test("an empty slot saves the free camera on screen for this area and asks for its name at once", async () => {
  const { page, socket, strip } = await openScene();
  const camera = await enterFree(page, socket);
  fireEvent.click(page.getByRole("button", { name: "Save current view in slot 4" }));
  await until(() => expect(puts().length).toBe(1));
  expect(puts()[0].place).toBe("9:1");
  const added = puts()[0].slots.find((entry) => entry.slot === 4);
  expect(added).toEqual({ slot: 4, name: "Camera 4", camera: { focus: camera.focus, position: camera.position,
    roll: camera.roll, fov: camera.fov } });
  expect(puts()[0].slots.map((entry) => entry.slot)).toEqual([1, 2, 4]);
  const field = await until(() => strip.querySelector(".scene-lab-slot-name-field"));
  await until(() => expect(document.activeElement).toBe(field));
  key(field, "3");                                     // a field that types keeps the number keys
  await settled();
  expect(socket.last().camera).toEqual(camera);
  fireEvent.input(field, { target: { value: "  Over   the bridge " } });
  key(field, "Enter");
  await until(() => expect(puts().length).toBe(2));
  expect(puts()[1].slots.find((entry) => entry.slot === 4).name).toBe("Over the bridge");
  expect(names(strip)[3]).toBe("Over the bridge");
  expect(pressed(strip)).toEqual([4]);                 // the camera stands on it
});

test("the recorded camera saves the pose the lab says it draws, without Mario", async () => {
  const { page, socket } = await openScene();
  fireEvent.click(page.getByRole("button", { name: "Save current view in slot 9" }));
  await until(() => expect(socket.last()).toMatchObject({ kind: "pose", frame: 0 }));
  socket.text({ id: socket.last().id, kind: "pose", pose: POSE, reason: null });
  await until(() => expect(puts().length).toBe(1));
  expect(puts()[0].slots.find((entry) => entry.slot === 9).camera).toEqual(
    { focus: POSE.focus, position: POSE.position, roll: POSE.roll, fov: POSE.fov });
});

test("a camera the lab worked out saves from the picture's camera_used (a ghost's view, a rig)", async () => {
  const { page, socket } = await openScene();
  const used = { focus: [5, 6, 7], position: [500, 600, 700], roll: 12, fov: 50 };
  key(page.container.querySelector(".scene-lab"), "ArrowRight");
  await socket.picture(socket.last(), { camera: "free", camera_used: used });
  fireEvent.click(page.getByRole("button", { name: "Save current view in slot 5" }));
  await until(() => expect(puts().length).toBe(1));
  expect(puts()[0].slots.find((entry) => entry.slot === 5).camera).toEqual(used);
  expect(socket.sent.some((message) => message.kind === "pose")).toBe(false);
});

// ------------------------------------------------------------------ cutting

test("keys 1-9 cut to that slot's camera as a free camera, and Play keeps playing", async () => {
  const { page, socket, strip, root, canvas } = await openScene();
  fireEvent.click(page.getByRole("button", { name: "Play" }));
  await until(() => expect(page.getByRole("button", { name: "Pause" })).toBeTruthy());
  key(root, "2");
  await until(() => expect(socket.last().camera?.focus).toBeTruthy());
  const cut = socket.last().camera;
  close(cut.focus, LOW.focus);
  close(cut.position, LOW.position);
  expect(cut).toMatchObject({ roll: LOW.roll, fov: LOW.fov });
  expect(page.getByRole("button", { name: "Pause" })).toBeTruthy();       // still playing
  expect(root.querySelector(".scene-lab-tag").textContent).toBe("Free camera");
  socket.picture(socket.last());                      // a play's picture waits for its display frame: not awaited
  expect(pressed(strip)).toEqual([2]);
  key(root, "1");                                                           // straight to another
  await until(() => expect(socket.last().camera.position[1]).toBeCloseTo(WIDE.position[1], 6));
  expect(pressed(strip)).toEqual([1]);
  socket.picture(socket.last());
  fireEvent.pointerDown(canvas, { button: 0, buttons: 1, clientX: 100, clientY: 100, pointerId: 1 });
  fireEvent(canvas, new PointerEvent("pointerrawupdate", { bubbles: true, buttons: 1, clientX: 140, clientY: 100, pointerId: 1 }));
  fireEvent.pointerUp(canvas, { button: 0, pointerId: 1 });
  await until(() => expect(pressed(strip)).toEqual([]));                    // the camera moved off it
  socket.picture(socket.last());
  fireEvent.click(page.getByRole("button", { name: "Reset view" }));        // back to the cut
  await until(() => expect(pressed(strip)).toEqual([1]));
  fireEvent.click(page.getByRole("button", { name: "Pause" }));
});

test("a click on a slot cuts like its key, and a key on an empty slot says so", async () => {
  const { page, socket, strip, root } = await openScene();
  fireEvent.click(page.getByRole("button", { name: "Wide hall: camera 1" }));
  await until(() => expect(socket.last().camera?.focus).toBeTruthy());
  expect(sameCamera(socket.last().camera, cutCamera(WIDE))).toBe(true);
  key(root, "7");
  await settled();
  expect(root.querySelector(".scene-lab-reason").textContent).toBe("No camera saved in slot 7 here yet.");
  expect(pressed(strip)).toEqual([1]);
});

test("a cut made while Free camera still waits for the recorded camera's pose stays where it cut", async () => {
  const { page, socket } = await openScene();
  fireEvent.click(page.getByRole("button", { name: "Free camera" }));
  await until(() => expect(socket.last()).toMatchObject({ kind: "pose" }));
  const question = socket.last();
  key(page.container.querySelector(".scene-lab"), "1");
  await until(() => expect(socket.last().camera?.focus).toBeTruthy());
  socket.text({ id: question.id, kind: "pose", pose: POSE, reason: null });     // the late answer
  await settled();
  expect(sameCamera(socket.pictures().at(-1).camera, cutCamera(WIDE))).toBe(true);
  expect(socket.pictures().filter((request) => request.camera && sameCamera(request.camera, cutCamera(POSE)))).toEqual([]);
});

test("the number keys belong to typing fields and selects, not to a focused button or slider, and never with Ctrl", async () => {
  const { page, socket, root } = await openScene();
  const pictures = root.querySelector(".scene-lab-quality select");
  const before = socket.sent.length;
  key(pictures, "2");
  key(root, "2", { ctrlKey: true });
  await settled();
  expect(socket.sent.length).toBe(before);
  const slider = root.querySelector('input[type="range"]');
  slider.focus();
  key(slider, "2");                                    // a slider's own keys are its arrows
  await until(() => expect(socket.last().camera?.position).toBeTruthy());
  expect(sameCamera(socket.last().camera, cutCamera(LOW))).toBe(true);
  const restart = page.getByRole("button", { name: /Restart/ });
  restart.focus();
  key(restart, "1");                                   // a button's own keys are Space and Enter
  await until(() => expect(sameCamera(socket.last().camera, cutCamera(WIDE))).toBe(true));
});

// ------------------------------------------------------------------ rename, delete, move

test("rename in place: Enter keeps the new name, Esc the old one", async () => {
  const { page, strip } = await openScene();
  fireEvent.click(page.getByRole("button", { name: "Rename Low run" }));
  let field = await until(() => strip.querySelector(".scene-lab-slot-name-field"));
  expect(field.value).toBe("Low run");
  fireEvent.input(field, { target: { value: "Under the bridge" } });
  key(field, "Escape");
  await settled();
  expect(puts()).toEqual([]);
  expect(names(strip)[1]).toBe("Low run");
  fireEvent.click(page.getByRole("button", { name: "Rename Low run" }));
  field = await until(() => strip.querySelector(".scene-lab-slot-name-field"));
  fireEvent.input(field, { target: { value: "Under the bridge" } });
  fireEvent.blur(field);                              // leaving the field keeps it too
  await until(() => expect(puts().length).toBe(1));
  expect(puts()[0].slots).toEqual([{ slot: 1, name: "Wide hall", camera: WIDE }, { slot: 2, name: "Under the bridge", camera: LOW }]);
});

// The page draws again for every picture (30 a second during Play) and for the save's own answer: none
// of that may put a name back while it is being typed.
test("a name being typed keeps its letters while pictures and the lab's answers draw the page", async () => {
  const { page, socket, strip, root } = await openScene();
  fireEvent.click(page.getByRole("button", { name: "Save current view in slot 4" }));
  await until(() => expect(socket.last()).toMatchObject({ kind: "pose" }));
  socket.text({ id: socket.last().id, kind: "pose", pose: POSE, reason: null });
  const field = await until(() => {
    const found = strip.querySelector(".scene-lab-slot-name-field");
    expect(found).toBeTruthy();
    return found;
  });
  fireEvent.input(field, { target: { value: "Over the" } });          // typed so far
  await until(() => expect(puts().length).toBe(1));                   // the save's answer arrives meanwhile
  await settled();
  key(root, "ArrowRight");                                            // and a picture is asked for and drawn
  await socket.picture(socket.last());
  await settled();
  expect(field.value).toBe("Over the");
  fireEvent.input(field, { target: { value: "Over the bridge" } });
  key(field, "Enter");
  await until(() => expect(puts().length).toBe(2));
  expect(puts()[1].slots.find((entry) => entry.slot === 4).name).toBe("Over the bridge");
});

test("a name being typed when the scene moves into another area is kept for the slot it was typed into", async () => {
  const { page, strip, root } = await openScene();
  fireEvent.click(page.getByRole("button", { name: "Rename Low run" }));
  const field = await until(() => {
    const found = strip.querySelector(".scene-lab-slot-name-field");
    expect(found).toBeTruthy();
    return found;
  });
  fireEvent.input(field, { target: { value: "Under the bridge" } });
  key(root, "End");                                                   // Play, or a step, into area 2
  await until(() => expect(names(strip)[0]).toBe("Cannon"));
  await until(() => expect(puts().length).toBe(1));
  expect(puts()[0]).toEqual({ place: "9:1", slots: [{ slot: 1, name: "Wide hall", camera: WIDE },
    { slot: 2, name: "Under the bridge", camera: LOW }] });
});

test("delete empties the slot, and Undo puts the camera back", async () => {
  const { page, strip } = await openScene();
  fireEvent.click(page.getByRole("button", { name: "Delete Wide hall" }));
  await until(() => expect(puts().length).toBe(1));
  expect(puts()[0].slots).toEqual([{ slot: 2, name: "Low run", camera: LOW }]);
  expect(names(strip)[0]).toBe(null);
  expect(strip.querySelector(".scene-lab-cameras-undo").textContent).toMatch(/Deleted "Wide hall" from slot 1/);
  fireEvent.click(page.getByRole("button", { name: "Undo" }));
  await until(() => expect(puts().length).toBe(2));
  expect(puts()[1].slots).toEqual([{ slot: 1, name: "Wide hall", camera: WIDE }, { slot: 2, name: "Low run", camera: LOW }]);
  expect(strip.querySelector(".scene-lab-cameras-undo")).toBe(null);
});

test("dragging a slot onto an empty one moves it there, and onto a filled one swaps them", async () => {
  const { strip } = await openScene();
  const drag = async (from, to) => {
    const data = { setData: vi.fn(), effectAllowed: null, dropEffect: null };
    fireEvent.dragStart(slotOf(strip, from), { dataTransfer: data });
    await settled(1);
    fireEvent.dragOver(slotOf(strip, to), { dataTransfer: data });
    await settled(1);
    expect(slotOf(strip, to).classList.contains("is-target")).toBe(true);
    fireEvent.drop(slotOf(strip, to), { dataTransfer: data });
    fireEvent.dragEnd(slotOf(strip, from), { dataTransfer: data });
    await settled(1);
  };
  expect(slotOf(strip, 1).draggable).toBe(true);
  expect(slotOf(strip, 6).draggable).toBe(false);      // an empty slot has nothing to drag
  await drag(1, 6);
  await until(() => expect(puts().length).toBe(1));
  expect(puts()[0].slots).toEqual([{ slot: 2, name: "Low run", camera: LOW }, { slot: 6, name: "Wide hall", camera: WIDE }]);
  expect(names(strip)).toEqual([null, "Low run", null, null, null, "Wide hall", null, null, null]);
  await drag(2, 6);
  await until(() => expect(puts().length).toBe(2));
  expect(puts()[1].slots).toEqual([{ slot: 2, name: "Wide hall", camera: WIDE }, { slot: 6, name: "Low run", camera: LOW }]);
  expect(strip.querySelector(".is-target")).toBe(null);
});

// ------------------------------------------------------------------ areas and the lab's answers

test("a scene moving into another area shows that area's cameras, and the keys cut to them", async () => {
  const { socket, strip, root } = await openScene();
  key(root, "End");
  await socket.picture(socket.last());
  await until(() => expect(names(strip)[0]).toBe("Cannon"));
  expect(names(strip)).toEqual(["Cannon", null, null, null, null, null, null, null, null]);
  key(root, "1");
  await until(() => expect(sameCamera(socket.last().camera, cutCamera(CANNON))).toBe(true));
  key(root, "2");
  await settled();
  expect(root.querySelector(".scene-lab-reason").textContent).toBe("No camera saved in slot 2 here yet.");
  key(root, "Home");                                   // back in area 1: its own set, read once
  await socket.picture(socket.last());
  await until(() => expect(names(strip)[1]).toBe("Low run"));
  expect(fetches.filter(([url]) => url.startsWith(ROUTE)).map(([url]) => placeOfUrl(url))).toEqual(["9:1", "9:2"]);
});

test("another scene opened over this one reads its area's cameras again", async () => {
  const { page } = await openScene();
  saved.set("9:1", { slots: [{ slot: 3, name: "Saved since", camera: WIDE }], problem: null });
  page.rerender(h(SceneViewer, { t: { sceneLab: { intent: { attempt: 12 }, clearIntent: () => {} } } }));
  await until(() => expect(sockets.length).toBe(2));
  sockets[1].open();
  await until(() => expect(sockets[1].sent.length).toBeGreaterThan(0));
  const strip = page.container.querySelector(".scene-lab-cameras");
  await until(() => expect(names(strip)).toEqual([null, null, "Saved since", null, null, null, null, null, null]));
});

test("a file the lab could not read shows its sentence, and the slots start empty", async () => {
  saved.set("9:1", { slots: [], problem: "The saved cameras could not be read, so none are shown." });
  const { strip } = await openScene({ filled: false });
  await until(() => expect(strip.querySelector(".scene-lab-hint.is-bad")?.textContent)
    .toBe("The saved cameras could not be read, so none are shown."));
  expect(strip.querySelectorAll(".scene-lab-slot-save:not([disabled])").length).toBe(9);
});

test("a write the lab refuses shows its words, and the slots come back as the lab keeps them", async () => {
  const { page, strip } = await openScene();
  putAnswer = () => respond({ detail: "camera fov must be 1 to 170 degrees" }, 422);
  fireEvent.click(page.getByRole("button", { name: "Delete Low run" }));
  await until(() => expect(strip.querySelector(".scene-lab-hint.is-bad")?.textContent).toBe("camera fov must be 1 to 170 degrees"));
  await until(() => expect(names(strip)[1]).toBe("Low run"));
});

// ------------------------------------------------------------------ with a cinematic camera on (scenerigs.js)

const RIG_CAMERA = { focus: [100, 220, -300], position: [100, 640, 160], roll: 0, fov: 45 };
async function frontOn(page, socket) {
  fireEvent.click(page.container.querySelector('[aria-label="Cinematic cameras"] button[title*="in front of him"]'));
  await until(() => expect(socket.last().camera?.rig?.kind).toBe("front"));
  await socket.picture(socket.last(), { camera: "free", camera_used: RIG_CAMERA,
    rig: { kind: "front", target: "stage", shot: "medium", side: null, pulled: 1, raised: 30 } });
}

test("a preset cut ends a cinematic camera: the cut is a still free camera", async () => {
  const { page, socket, strip, root } = await openScene();
  await frontOn(page, socket);
  expect(root.querySelector(".scene-lab-tag").textContent).toBe("Front · Mario");
  key(root, "2");
  await until(() => expect(socket.last().camera?.focus).toBeTruthy());
  expect(socket.last().camera.rig).toBeUndefined();
  expect(sameCamera(socket.last().camera, cutCamera(LOW))).toBe(true);
  expect(root.querySelector(".scene-lab-tag").textContent).toBe("Free camera");
  expect(root.querySelector('[aria-label="Cinematic cameras"] [aria-pressed="true"]')).toBeNull();
  expect(root.querySelector('[aria-label="Cinematic camera settings"]')).toBeNull();
  await socket.picture(socket.last());
  fireEvent.click(page.getByRole("button", { name: "Forward 1" }));                     // and it stays still
  await until(() => expect(socket.last().frame).toBe(1));
  expect(sameCamera(socket.last().camera, cutCamera(LOW))).toBe(true);
  expect(pressed(strip)).toEqual([2]);
});

test("a preset saved while a cinematic camera is on keeps the camera it drew (camera_used), not its request", async () => {
  const { page, socket } = await openScene();
  await frontOn(page, socket);
  fireEvent.click(page.getByRole("button", { name: "Save current view in slot 9" }));
  await until(() => expect(puts().length).toBe(1));
  expect(puts()[0].slots.find((entry) => entry.slot === 9).camera).toEqual(RIG_CAMERA);
  expect(socket.sent.some((message) => message.kind === "pose")).toBe(false);
});

test("without the free camera the slots are disabled and the strip says why, asking the lab nothing", async () => {
  const { strip } = await openScene({ free: false });
  await settled();
  expect(strip.querySelector(".scene-lab-hint").textContent).toBe("This recording has no symbol map, so only its own camera shows.");
  expect([...strip.querySelectorAll("button")].every((button) => button.disabled)).toBe(true);
  expect(fetches.some(([url]) => url.startsWith(ROUTE))).toBe(false);
});
