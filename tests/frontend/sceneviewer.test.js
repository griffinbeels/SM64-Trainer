// @vitest-environment jsdom
// The Scene viewer page (ui/components/sceneviewer.js) against a scripted
// server: what it asks for, when, and what it shows. No renderer, GPU or
// browser: the socket, fetch and canvas are stand-ins.
import { h } from "preact";
import { render, cleanup, fireEvent, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { SceneViewer, SCENE_CAMERA, cameraOf, dollied, orbitFromPose, turned } from
  "../../src/sm64_events/ui/components/sceneviewer.js";

const FRAMES = Array.from({ length: 5 }, (_, i) => ({ level: 9, area: 1, game_frame: 1000 + i }));
const POSE = { focus: [10, 200, -30], position: [10, 500, 1170], mario: [40, 80, -60] };
let sockets, opened, fetches;

class FakeSocket {
  static OPEN = 1;
  constructor(url) { this.url = url; this.sent = []; this.readyState = 0; sockets.push(this); }
  send(text) { this.sent.push(JSON.parse(text)); }
  close() { this.readyState = 3; }
  open() { this.readyState = 1; this.onopen?.(); }
  picture(request, extra = {}) {
    const header = new TextEncoder().encode(JSON.stringify({ id: request.id, session: request.session, frame: request.frame,
      width: 2, height: 1, encoding: "raw", camera: request.camera ? "free" : "recorded", reason: null,
      timings: {}, server_ms: 3, ...extra }));
    const buffer = new ArrayBuffer(4 + header.length + 6);
    new DataView(buffer).setUint32(0, header.length, true);
    new Uint8Array(buffer, 4).set(header);
    return this.onmessage({ data: buffer });
  }
  text(message) { this.onmessage({ data: JSON.stringify(message) }); }
}

function respond(body, status = 200) {
  return Promise.resolve({ ok: status < 300, status, json: () => Promise.resolve(body) });
}

beforeEach(() => {
  sockets = [];
  fetches = [];
  opened = { session: 1, name: "bob-minute.sm64scene", fps: 30, frames: FRAMES, renderer_pid: 7,
    free_camera: { available: true, reason: null } };
  globalThis.WebSocket = FakeSocket;
  globalThis.fetch = vi.fn((url, options = {}) => {
    fetches.push([url, options.method || "GET", options.body]);
    if (url === "/api/local-access") return respond({}, 404);
    if (url === "/api/scene-lab/status") return respond({ ready: true, problems: [], hold: null, session: null,
      ended: null, packages: [{ name: "bob-minute.sm64scene", bytes: 11 << 20 }] });
    if (url === "/api/scene-lab/open") return respond(opened);
    if (url === "/api/scene-lab/close") return respond({ closed: true });
    return respond({}, 404);
  });
  HTMLCanvasElement.prototype.getContext = () => ({
    createImageData: (w, hgt) => ({ data: new Uint8ClampedArray(w * hgt * 4), width: w, height: hgt }),
    putImageData: () => {}, drawImage: () => {},
  });
});
afterEach(cleanup);

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
  return { page, socket, root: page.container.querySelector(".scene-lab") };
}

test("opening names a listed package, then asks for the first picture from the recorded camera", async () => {
  const { socket } = await openScene();
  const open = fetches.find(([url]) => url === "/api/scene-lab/open");
  expect(open[1]).toBe("POST");
  expect(JSON.parse(open[2])).toEqual({ package: "bob-minute.sm64scene" });
  expect(socket.url).toMatch(/\/api\/scene-lab\/ws$/);
  expect(socket.sent[0]).toMatchObject({ session: 1, frame: 0, camera: null, encoding: "jpeg" });
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

test("the free camera starts from the recorded camera, keeps its place across steps, and R returns", async () => {
  const { page, socket, root } = await openScene();
  await socket.picture(socket.sent[0]);
  fireEvent.click(page.getByRole("button", { name: "Free camera" }));
  await waitFor(() => expect(socket.sent.length).toBe(2));
  expect(socket.sent[1]).toMatchObject({ kind: "pose", frame: 0, session: 1 });
  socket.text({ id: socket.sent[1].id, kind: "pose", pose: POSE, reason: null });
  await waitFor(() => expect(socket.sent.length).toBe(3));
  const first = socket.sent[2].camera;
  POSE.focus.forEach((value, i) => expect(first.focus[i]).toBeCloseTo(value, 6));
  POSE.position.forEach((value, i) => expect(first.position[i]).toBeCloseTo(value, 6));
  await socket.picture(socket.sent[2]);
  await waitFor(() => expect(page.container.querySelector(".scene-lab-tag").textContent).toBe("Free camera"));
  fireEvent.keyDown(root, { key: "ArrowRight" });
  await waitFor(() => expect(socket.sent.length).toBe(4));
  expect(socket.sent[3]).toMatchObject({ frame: 1, camera: first });   // same world camera, next frame
  await socket.picture(socket.sent[3]);
  fireEvent.keyDown(root, { key: "r" });
  await waitFor(() => expect(socket.sent.length).toBe(5));
  expect(socket.sent[4]).toMatchObject({ frame: 1, camera: null });
});

test("Look at Mario points the camera at him and a fallback picture shows its reason", async () => {
  const { page, socket } = await openScene();
  await socket.picture(socket.sent[0]);
  fireEvent.click(page.getByRole("button", { name: "Free camera" }));
  await waitFor(() => expect(socket.sent.length).toBe(2));
  socket.text({ id: socket.sent[1].id, kind: "pose", pose: POSE, reason: null });
  await waitFor(() => expect(socket.sent.length).toBe(3));
  await socket.picture(socket.sent[2]);
  fireEvent.click(page.getByRole("button", { name: "Look at Mario" }));
  await waitFor(() => expect(socket.sent.length).toBe(4));
  expect(socket.sent[3]).toMatchObject({ kind: "pose", frame: 0 });
  socket.text({ id: socket.sent[3].id, kind: "pose", pose: POSE, reason: null });
  await waitFor(() => expect(socket.sent.length).toBe(5));
  const camera = socket.sent[4].camera;
  expect(camera.focus).toEqual([40, 80 + SCENE_CAMERA.marioHeight, -60]);
  await socket.picture(socket.sent[4], { camera: "recorded", reason: "This frame has no 3D scene to move around in." });
  await waitFor(() => expect(page.container.textContent).toContain("This frame has no 3D scene to move around in."));
});

test("without a free camera the controls say why instead of doing nothing", async () => {
  opened.free_camera = { available: false, reason: "This recording carries no scene state." };
  const { page } = await openScene();
  expect(page.getByRole("button", { name: "Free camera" }).disabled).toBe(true);
  expect(page.getByRole("button", { name: "Look at Mario" }).disabled).toBe(true);
  expect(page.container.querySelector(".scene-lab-hint").textContent)
    .toContain("Only the recorded camera is available: This recording carries no scene state.");
});

test("a closed scene shows the server's reason and offers Open again", async () => {
  const { page, socket } = await openScene();
  socket.text({ id: socket.sent[0].id, error: "Closed after a while without use, to free the renderer.", closed: true });
  await waitFor(() => expect(page.container.textContent).toContain("Closed after a while without use"));
  expect(page.getByRole("button", { name: "Open" })).toBeTruthy();
});

test("the orbit keeps the recorded camera's place and clamps what it must", () => {
  const orbit = orbitFromPose(POSE.focus, POSE.position);
  const camera = cameraOf(orbit);
  POSE.position.forEach((value, i) => expect(camera.position[i]).toBeCloseTo(value, 6));
  expect(turned(orbit, 0, 1e6).pitch).toBe(SCENE_CAMERA.pitchLimit);
  expect(dollied(orbit, 1e6).dist).toBe(SCENE_CAMERA.maxDistance);
  expect(dollied(orbit, -1e6).dist).toBe(SCENE_CAMERA.minDistance);
});
