// src/sm64_events/ui/components/sceneviewer.js -- the Scene viewer (M4 E3, lab only).
//
// One recording in its reconstructed stage: the game's own pictures from the
// recorded camera, and a free camera the server redraws by running the game's
// drawing code (server/scene_lab_api.py, tools/scene_lab/scene_session.py).
// app.js imports this module only after GET /api/scene-lab answers, so with
// the lab switched off none of it loads. Browser and desktop share it whole.
//
// The page never redraws on its own: SceneLink keeps up to PICTURES_IN_FLIGHT
// requests out over the socket (the server runs the game's code for the next
// while it draws and encodes the one before), and a newer step, seek or camera
// move replaces the one request waiting behind them (latest wins; the server
// does the same). A picture stops being in flight when it arrives: the next
// request goes out while it decodes and draws. Pictures draw as they finish
// decoding, never over a newer one already on the canvas. In the desktop app's
// window the pictures skip JPEG: the server hands over the exact pixels through
// WebView2 shared memory (SHARED_WAIT has the details).
//
// While the free camera moves, the page sums up its own numbers every
// MOVING_WINDOW.ms (input to picture, pictures a second, decode, the server's
// stages): the readout shows them, and the UI observation log records them
// (readSceneViewer), so a report carries the person's real numbers.
//
// The free camera (components/scenecamera.js owns its math) starts from the
// picture on screen -- the recorded camera's place, screen tilt and field of
// view -- and stays in the game's world coordinates while you step or seek.
// W A S D and Q E fly whenever the page has the keyboard (Shift faster, Alt
// slower; the first press in the recorded camera enters the free camera from
// the picture on screen); holding the right button adds looking around with
// the mouse, and the wheel then sets the speed. Left drag turns around the
// focus point, Shift-drag or a middle-button drag slides, the wheel moves
// closer. A double-click flies to the surface under the pointer and looks at
// Mario. F looks at Mario, Set focus turns to a point on the stage, Reset view
// goes back to where Free camera began, R returns to the recorded camera. A
// press that moves under DRAG_DEAD_ZONE pixels is a click, never a drag; any
// input stops a double-click's flight where it is. One keyboard owner (onKey):
// arriving on the page gives it the keyboard, a mouse click on its controls
// hands it back, and a field that types keeps its keys. Opening a scene
// captures no pointer; Esc, leaving the window or the page, and Close each stop
// every movement, let go of every key and give the pointer back.
//
// The recording's sound (sceneaudio.js) follows the picture clock: SceneLink
// tells it when Play starts, each picture it draws, and every stop.
//
// Play stops only when the person pauses (Pause, Space, a step, the slider):
// moving the camera, by any of the ways above, keeps it playing. Restart goes
// to the first frame and keeps playing or stays paused; Loop plays on from the
// first frame after the last (a scene still being written loops once it is
// whole).
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { Icon } from "./icons.js";
import { getJSON, send } from "../api.js";
import { displayName } from "../entitysection.js";
import { ControlGroups } from "../tunecontrols.js";
import { postObservation } from "../uilog.js";
import { SCENE_CAMERA_DEFAULTS, SCENE_CAMERA_GROUPS, SCENE_CAMERA_TUNABLES, sceneCameraTuning,
  setSceneCameraTuning } from "../scenecameratuning.js";
import { cameraOf, dollied, flightSpeed, flown, flyToTarget, focusedOn, glideAt, lookingAt, looked,
  orbitFromPose, slid, speedStepped, turned, worldPoint } from "./scenecamera.js";
import { SceneSound, SoundBar } from "./sceneaudio.js";
import { pictureAt, playLength, recordedTimes } from "./sceneclock.js";

const html = htm.bind(h);
const ROOT = "/api/scene-lab";

if (!document.querySelector("link[data-scene-lab]")) {
  const sheet = Object.assign(document.createElement("link"), { rel: "stylesheet", href: "/ui/scenelab.css" });
  sheet.dataset.sceneLab = "";
  document.head.append(sheet);
}

const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
const sameOrbit = (a, b) => !!a && !!b && a.yaw === b.yaw && a.pitch === b.pitch && a.dist === b.dist
  && a.roll === b.roll && a.fov === b.fov && a.focus.every((value, i) => value === b.focus[i]);

// The flight keys, by physical key so they sit under the left hand on any layout.
export const FLIGHT_KEYS = { KeyW: ["forward", 1], KeyS: ["forward", -1], KeyD: ["right", 1], KeyA: ["right", -1],
  KeyE: ["up", 1], KeyQ: ["up", -1] };
const idleFlight = () => ({ keys: new Set(), fast: false, slow: false, ticking: false, last: 0, input: null });
function movementOf(keys) {
  const move = { forward: 0, right: 0, up: 0 };
  for (const code of keys) { const [axis, sign] = FLIGHT_KEYS[code]; move[axis] += sign; }
  return move;
}
const WHEEL_LINE = 40, WHEEL_PAGE = 800, WHEEL_NOTCH = 100;   // deltaMode 1 and 2 in pixels; Chromium's notch
const GLIDE_LEAD_MS = 40;     // a double-click's flight is at most this far along when its first picture goes out
const wheelPixels = (event) => event.deltaY * (event.deltaMode === 1 ? WHEEL_LINE : event.deltaMode === 2 ? WHEEL_PAGE : 1);
// Requests out at once: the server draws one while it begins the next (server/scene_lab_api.py
// PICTURES_IN_FLIGHT holds the same number).
export const PICTURES_IN_FLIGHT = 2;
const WANTS = { play: 0, input: 1, move: 2 };
// How long a picture takes from asked to ready to draw, until the page has measured its own (the
// median of its last READY_WINDOW): a play asks this much plus a display frame ahead.
const READY_GUESS_MS = 40, READY_WINDOW = 15;
// How long the page sums up its numbers while the camera moves before showing and recording them.
export const MOVING_WINDOW = { ms: 2000 };

function percentile(values, p) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.round(p * (sorted.length - 1)))];
}
function median(values) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b), mid = sorted.length >> 1;
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}
const tenths = (value) => (value == null || !Number.isFinite(value) ? null : Math.round(value * 10) / 10);
const spread = (values) => {
  const present = values.filter((value) => value != null && Number.isFinite(value));
  return present.length ? { p50: tenths(percentile(present, 0.5)), p95: tenths(percentile(present, 0.95)) } : null;
};

// One window of drawn pictures as the readout and the observation log show it: null without a
// picture an input to the free camera asked for. A picture's `at` is when it was drawn. Pictures a
// second count only while the camera moved: a gap longer than STILL_MS between two pictures is
// the camera standing still, and neither its time nor its picture counts.
export const STILL_MS = 150;
export function movingSummary(samples, skipped = 0) {
  const moved = samples.filter((sample) => sample.input_ms != null);
  if (!moved.length) return null;
  const gaps = samples.slice(1).map((sample, i) => sample.at - samples[i].at).filter((gap) => gap <= STILL_MS);
  const seconds = gaps.reduce((sum, gap) => sum + gap, 0) / 1000, last = samples[samples.length - 1];
  const stage = (name) => spread(samples.map((sample) => sample.timings?.[name]));
  return { pictures: samples.length, seconds: Math.round(seconds * 100) / 100,
    pictures_per_second: seconds > 0 ? tenths(gaps.length / seconds) : null,
    input_to_drawn_ms: spread(moved.map((sample) => sample.input_ms)),
    decode_ms: spread(samples.map((sample) => sample.decode_ms)),
    trip_ms: spread(samples.map((sample) => sample.trip_ms)),
    server_ms: spread(samples.map((sample) => sample.server_ms)),
    evaluate_ms: stage("evaluate_ms"), host_ms: stage("host_ms"), encode_ms: stage("encode_ms"),
    encoding: last.encoding, width: last.width, height: last.height, in_flight: PICTURES_IN_FLIGHT, skipped };
}

// Collects drawn pictures from the first one an input to the free camera asked for, and after
// MOVING_WINDOW.ms hands their summary to `publish`. Pictures drawn while nothing moves are not
// kept, so a still page publishes nothing.
export class MovingTimings {
  constructor(publish) { Object.assign(this, { publish, samples: [], skipped: 0, timer: null }); }

  drawn(sample, moving) {
    if (!this.timer && !moving) return;
    this.samples.push(sample);
    if (!this.timer) this.timer = setTimeout(() => this.close(), MOVING_WINDOW.ms);
  }

  skip() { if (this.timer) this.skipped += 1; }

  close() {
    const summary = movingSummary(this.samples, this.skipped);
    this.stop();
    if (summary) this.publish(summary);
  }

  stop() {
    clearTimeout(this.timer);
    Object.assign(this, { samples: [], skipped: 0, timer: null });
  }
}

// The pointer lock's records (SceneLink.noteLock, about four a right-button hold) reach the UI
// log at most LOCK_NOTES.records per LOCK_NOTES.ms: a page caught granting and exiting cannot
// flood it, and the next record that goes says how many did not.
export const LOCK_NOTES = { records: 40, ms: 10000 };
export function throttledNotes(post, now = () => performance.now()) {
  let sent = [], dropped = 0;
  return (record) => {
    const at = now();
    sent = sent.filter((time) => at - time < LOCK_NOTES.ms);
    if (sent.length >= LOCK_NOTES.records) { dropped += 1; return; }
    sent.push(at);
    post(dropped ? { ...record, dropped } : record);
    dropped = 0;
  };
}
const ms = (value) => (value == null ? "-" : `${Math.round(value)} ms`);
const megabytes = (bytes) => `${(bytes / 1048576).toFixed(1)} MB`;
// Three figures, whole numbers with one place: 0.64x, 1.0x, 1.25x, 15.3x.
export function speedWords(multiplier) {
  const shown = Number(multiplier.toPrecision(3));
  return `${Number.isInteger(shown) ? shown.toFixed(1) : shown}x`;
}

function drawRaw(context, header, bytes) {  // ReadScreen's BGR rows, bottom-up
  const { width, height } = header, picture = context.createImageData(width, height);
  const target = new Uint32Array(picture.data.buffer), row = width * 3;
  for (let y = 0; y < height; y++) {
    let s = (height - 1 - y) * row, d = y * width;
    for (let x = 0; x < width; x++, s += 3) target[d++] = 0xff000000 | (bytes[s] << 16) | (bytes[s + 1] << 8) | bytes[s + 2];
  }
  context.putImageData(picture, 0, 0);
}

// Draws the picture once `ready()` lets it (a play's picture waits for the
// display frame it is due; false: never) and unless `claim()` refuses (a newer
// one is already drawn: decodes can finish out of order). Returns how long
// decoding and drawing took, without the wait, or null when it did not draw.
async function paint(canvas, header, bytes, claim = () => true, ready = null) {
  const context = canvas?.getContext("2d");
  if (!context) return null;
  const started = performance.now();
  const bitmap = header.encoding === "jpeg"
    ? await createImageBitmap(new Blob([bytes], { type: "image/jpeg" })) : null;
  const decoded = performance.now();
  if ((ready && !(await ready())) || !claim()) { bitmap?.close(); return null; }
  const drawing = performance.now();
  if (canvas.width !== header.width || canvas.height !== header.height) {
    canvas.width = header.width;
    canvas.height = header.height;
  }
  if (!bitmap) drawRaw(context, header, bytes);
  else {
    context.drawImage(bitmap, 0, 0);
    bitmap.close();
  }
  return decoded - started + performance.now() - drawing;
}

// Where a pointer sits on the picture, 0 to 1 across and down, or null off it:
// the canvas letterboxes its picture (object-fit: contain).
export function pictureFraction(canvas, clientX, clientY) {
  const box = canvas.getBoundingClientRect(), fit = Math.min(box.width / canvas.width, box.height / canvas.height);
  if (!(fit > 0)) return null;
  const left = box.left + (box.width - canvas.width * fit) / 2, top = box.top + (box.height - canvas.height * fit) / 2;
  const u = (clientX - left) / (canvas.width * fit), v = (clientY - top) / (canvas.height * fit);
  return u >= 0 && u <= 1 && v >= 0 && v <= 1 ? { u, v } : null;
}

// The desktop window's WebView2 (null in a browser). There the server can hand
// over the renderer's exact pixels through shared memory instead of a JPEG
// (tools/scene_lab/webview_pictures.py): a "shared" picture's header comes over
// the socket with no pixels, and the pixels as a sharedbufferreceived event
// tagged with the request's id. Either can arrive first; a header whose pixels
// never come is dropped after SHARED_WAIT.ms.
const webview = () => globalThis.chrome?.webview ?? null;
export const SHARED_WAIT = { ms: 2000 };

// Loop stays as the person left it, across scenes and visits (this browser only).
export const LOOP_PREFERENCE = "sm64.sceneLoop";
function savedLoop() {
  try { return localStorage.getItem(LOOP_PREFERENCE) === "on"; } catch { return false; }
}

// The heading for the open scene, never its file name: the server's title when it names the
// scene's target, else the attempt the Practice log already shows (course, star, strategy and
// time), else the server's plain title ("Lab recording"), else "Recording".
export function sceneTitle(scene, view) {
  if (scene?.title && scene.target) return scene.title;
  const found = scene?.attempt != null ? attemptIn(view, scene.attempt) : null;
  if (!found) return scene?.title || "Recording";
  const { section, attempt } = found, named = displayName(section, view?.catalog?.courses || []);
  const time = attempt.segment_id != null ? attempt.rta : attempt.igt ?? attempt.rta;
  return [named.context, named.name, attempt.strat_tag, time].filter(Boolean).join(" · ");
}

function attemptIn(view, id) {
  for (const section of [...(view?.stars || []), ...(view?.segments || [])]) {
    const attempt = (section.attempts || []).find((row) => row.id === id);
    if (attempt) return { section, attempt };
  }
  return null;
}

// The open scene's requests and camera: up to PICTURES_IN_FLIGHT pictures in
// flight, the newest input waiting behind them, input time kept from the
// EARLIEST input so input-to-picture stays honest (until that picture is
// drawn). `changed` re-renders the page.
export class SceneLink {
  constructor({ draw, changed, ended, animate = (step) => requestAnimationFrame(step), observe = () => {} }) {
    Object.assign(this, { draw, changed, ended, animate, observe });
    Object.assign(this, { lockSerial: 0, lockTarget: null, lockPromised: null, lockExit: null });
    Object.assign(this, { scene: null, frame: 0, free: false, orbit: null, entry: null, encoding: "jpeg",
      playing: false, shown: null, notice: null, error: null, drawing: false, ws: null, inFlight: new Map(),
      wanted: null, sequence: 0, questions: new Map(), latencies: [], pending: [0, 0, false], drag: null,
      entering: null, arrived: 0, drawnSeq: 0, looking: null, flight: idleFlight(), speed: 1, picking: false,
      finding: false, glide: null, serial: 0, noticeTimer: null, moving: null, shared: new Map(), playback: null,
      readies: [], coldId: 0, autoplay: false, loop: savedLoop(),
      sound: new SceneSound({ changed: () => this.changed() }) });
    this.timings = new MovingTimings((summary) => { this.moving = summary; this.changed(); });
    this.onShared = (event) => this.sharedArrived(event);
  }

  get freeAvailable() { return !!this.scene?.free_camera?.available; }

  get sharedAvailable() { return !!this.scene?.shared_pictures && !!webview(); }

  get moved() { return this.free && !sameOrbit(this.orbit, this.entry); }

  // `play`: Play starts from the first frame once its picture is drawn (the
  // Practice log's Ghost button), unless the person steps or plays first.
  connect(scene, { play = false } = {}) {
    this.disconnect();
    this.autoplay = play;
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${ROOT}/ws`);
    ws.binaryType = "arraybuffer";
    ws.onmessage = (event) => (typeof event.data === "string"
      ? this.onText(JSON.parse(event.data)) : this.onPicture(event.data));
    ws.onopen = () => this.want("input");
    ws.onclose = () => { if (this.ws === ws) this.end("The connection to the scene viewer closed."); };
    // A scene's first picture carries the session's cold start (a seek from nothing), so it never
    // counts towards how far ahead a play asks: a Play started on it would skip the opening frames.
    Object.assign(this, { ws, scene, frame: 0, free: false, orbit: null, entry: null, inFlight: new Map(),
      wanted: null, notice: null, error: null, speed: 1, moving: null, readies: [], coldId: this.sequence + 1 });
    if (this.sharedAvailable) {
      webview().addEventListener("sharedbufferreceived", this.onShared);
      this.encoding = "shared";
    } else if (this.encoding === "shared") this.encoding = "jpeg";
    this.sound.load(scene);
    this.changed();
  }

  disconnect() {
    this.interrupt();
    this.autoplay = false;
    this.stopPlay();
    this.playing = false;
    this.sound.stop();
    this.timings.stop();
    webview()?.removeEventListener("sharedbufferreceived", this.onShared);
    for (const [id, waiting] of this.shared) {
      this.shared.delete(id);
      if (waiting.buffer) webview()?.releaseBuffer(waiting.buffer);
      else { clearTimeout(waiting.timer); waiting.resolve(null); }
    }
    this.stopLook("scene closed");
    Object.assign(this, { picking: false, drag: null });
    this.releaseKeys();
    if (this.ws) { this.ws.onclose = null; this.ws.close(); this.ws = null; }
  }

  // A shared picture's pixels: handed to its waiting header, else kept until the header comes.
  sharedArrived(event) {
    const { id, session } = event.additionalData || {}, buffer = event.getBuffer();
    const waiting = this.shared.get(id);
    if (session !== this.scene?.session || waiting?.buffer) return webview()?.releaseBuffer(buffer);
    if (!waiting) return this.shared.set(id, { buffer });
    this.shared.delete(id);
    clearTimeout(waiting.timer);
    waiting.resolve(buffer);
  }

  // A picture's pixels: after its header in the socket message, or (encoding "shared") the shared
  // buffer posted for it; null when that never came.
  async pixelsOf(header, buffer, offset) {
    if (header.encoding !== "shared") return { pixels: new Uint8Array(buffer, offset) };
    const shared = await this.sharedPixels(header.id);
    return shared && { pixels: new Uint8Array(shared), shared };
  }

  // The pixels for header `id`: at once if they came first, else when they come (null: they did not).
  sharedPixels(id) {
    const stored = this.shared.get(id);
    if (stored?.buffer) {
      this.shared.delete(id);
      return Promise.resolve(stored.buffer);
    }
    return new Promise((resolve) => {
      const timer = setTimeout(() => { this.shared.delete(id); resolve(null); }, SHARED_WAIT.ms);
      this.shared.set(id, { resolve, timer });
    });
  }

  end(message) {
    this.disconnect();
    this.sound.load(null);
    Object.assign(this, { scene: null, free: false, notice: null, error: message, inFlight: new Map(), wanted: null });
    this.changed();
    this.ended();
  }

  // kind: "play" (the playback clock), "input" (the person: a step, a seek, a camera switch) or
  // "move" (the person moved the free camera); a waiting request takes the strongest that asked.
  // `frame` is the play's: the picture it asks for ahead of the one on screen.
  want(kind = "input", inputTime = performance.now(), frame = null) {
    if (!this.wanted) this.wanted = { kind, inputTime, frame };
    else {
      if (WANTS[kind] > WANTS[this.wanted.kind]) this.wanted.kind = kind;
      if (frame != null) this.wanted.frame = frame;
    }
    queueMicrotask(() => this.pump());
  }

  // During a play every request is for the play's frame and is due when the
  // recording showed it; otherwise for the frame on screen.
  pump() {
    const ready = this.ws && this.ws.readyState === WebSocket.OPEN && this.scene;
    if (this.inFlight.size >= PICTURES_IN_FLIGHT || !this.wanted || !ready) return;
    const play = this.playback, frame = this.wanted.frame ?? (play ? play.asked : this.frame);
    const id = ++this.sequence, camera = this.free && this.orbit ? cameraOf(this.orbit) : null;
    this.inFlight.set(id, { ...this.wanted, id, frame, free: !!camera, sent: performance.now(), play,
      due: play ? play.dueOf(frame) : null });
    if (play && this.wanted.kind !== "play") play.moved = true;
    this.wanted = null;
    this.ws.send(JSON.stringify({ id, session: this.scene.session, frame, camera, encoding: this.encoding }));
    this.drawing = true;
    this.changed();
  }

  // When a picture asked for now is ready to draw: the median of recent ones, ask to decoded.
  readyMs() { return median(this.readies) ?? READY_GUESS_MS; }

  // A play's picture waits until the display frame it is due has begun, so it shows on that frame:
  // true then (at once when that frame has already begun), false when its play stopped first.
  holdUntil(due, play) {
    if (this.playback !== play) return false;
    if (play.frameAt != null && due <= play.frameAt + play.frameMs) return true;
    return new Promise((resolve) => { play.holds.push({ due, resolve }); });
  }

  // What paint awaits once a picture has decoded: how long that took feeds how far ahead a play
  // asks; a play's picture then waits for its display frame, and one from a stopped play is not shown.
  readyFor(request) {
    return () => {
      if (request && request.id !== this.coldId) {
        this.readies = [...this.readies.slice(-(READY_WINDOW - 1)), performance.now() - request.sent];
      }
      return request?.play ? this.holdUntil(request.due, request.play) : true;
    };
  }

  // A camera moved during the play asked for the play's next frame, which a stopped play never
  // shows: the camera is asked for again at the frame on screen.
  stopPlay() {
    const play = this.playback;
    if (!play) return;
    Object.assign(this, { playback: null, playing: false });
    for (const hold of play.holds.splice(0)) hold.resolve(false);
    if (this.wanted?.frame != null) {
      if (this.wanted.kind === "play") this.wanted = null;
      else this.wanted.frame = null;
    }
    if (play.moved) this.want("input");
    this.sound.stop();
  }

  async onPicture(buffer) {
    const received = performance.now(), arrived = ++this.arrived;
    const length = new DataView(buffer).getUint32(0, true);
    const header = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 4, length)));
    const request = this.inFlight.get(header.id) || null;
    if (request) {
      this.inFlight.delete(header.id);
      this.pump();                    // the next goes out while this one decodes
    }
    // Drawn unless a newer picture is on the canvas already: decodes can finish out of order.
    const claim = () => {
      if (arrived <= this.drawnSeq) return false;
      this.drawnSeq = arrived;
      return true;
    };
    const got = await this.pixelsOf(header, buffer, 4 + length);
    const decodeMs = got ? await this.draw(header, got.pixels, claim, this.readyFor(request)) : null;
    if (got?.shared) webview()?.releaseBuffer(got.shared);
    this.drawing = this.inFlight.size > 0;
    if (decodeMs == null || arrived !== this.drawnSeq) {
      this.timings.skip();
      this.changed();
      return;
    }
    this.pictureDrawn(header, request, received, decodeMs);
  }

  // A picture is on the canvas: the frame on screen during a play, the sound, the page's numbers.
  pictureDrawn(header, request, received, decodeMs) {
    const drawnAt = performance.now();
    if (request?.play) {
      this.frame = header.frame;
      this.sound.shown(header.frame);
    }
    if (request) {
      header.trip_ms = received - request.sent;
      header.input_ms = drawnAt - request.inputTime;
      if (request.kind !== "play") this.latencies = [...this.latencies.slice(-99), header.input_ms];
    }
    const moving = request?.kind === "move";
    this.timings.drawn({ at: drawnAt, input_ms: moving ? header.input_ms : null, decode_ms: decodeMs,
      trip_ms: header.trip_ms, server_ms: header.server_ms, timings: header.timings, encoding: header.encoding,
      width: header.width, height: header.height }, moving);
    Object.assign(this, { shown: header, notice: header.reason || null });
    this.changed();
    this.pump();
    if (this.autoplay) this.play();
  }

  onText(message) {
    if (message.kind === "grown") return this.grown(message);
    const resolve = this.questions.get(message.id);
    this.questions.delete(message.id);
    if (resolve) resolve(message.kind ? message : { pose: null, point: null, reason: message.error });
    if (message.closed) return this.end(message.error);
    if (resolve) return;
    this.inFlight.delete(message.id);
    if (message.error) this.error = message.error;
    this.drawing = this.inFlight.size > 0;
    this.changed();
    this.pump();
  }

  // The open scene is still being written (an attempt scene whose fade-out has not reached the
  // recording yet): the server's frames replace this scene's from `from` on, the slider grows
  // with them, and a Play waiting at the newest picture goes on. `growing` false: it is whole.
  grown(message) {
    const scene = this.scene;
    if (!scene || message.session !== scene.session) return;
    scene.frames.splice(message.from, Infinity, ...message.frames);
    scene.growing = !!message.growing;
    if (message.audio) scene.audio = message.audio;
    this.frame = clamp(this.frame, 0, Math.max(0, scene.frames.length - 1));
    this.sound.grew(scene);
    if (this.playback) this.playback.grew(scene);
    this.changed();
  }

  // A pose or a pick: answered in order, before any picture.
  ask(kind, fields) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN || !this.scene) return Promise.resolve({ reason: null });
    const id = ++this.sequence;
    return new Promise((resolve) => {
      this.questions.set(id, resolve);
      this.ws.send(JSON.stringify({ id, session: this.scene.session, kind, ...fields }));
    });
  }

  pose(frame) { return this.ask("pose", { frame }); }

  say(notice) { this.notice = notice; this.changed(); }

  // A notice that clears itself unless something replaced it.
  sayBriefly(notice, ms = 2500) {
    this.say(notice);
    clearTimeout(this.noticeTimer);
    this.noticeTimer = setTimeout(() => { if (this.notice === notice) this.say(null); }, ms);
  }

  // Any new input: a fly-to in progress (or still asking where to go) stops where it is.
  interrupt() {
    this.serial += 1;
    this.glide = null;
  }

  fail(error) { this.error = error; this.changed(); }

  // Enter the free camera from the camera on screen -- its place, tilt and
  // field of view, so the picture does not move -- and remember it for Reset
  // view; drags made while the recorded camera is read are applied once it
  // arrives. Every caller waits for the same entry.
  enterFree() {
    if (this.free || !this.freeAvailable) return Promise.resolve();
    if (!this.entering) this.entering = this.readEntry().finally(() => { this.entering = null; });
    return this.entering;
  }

  async readEntry() {
    const answer = await this.pose(this.frame);
    const pose = answer.pose, orbit = pose && orbitFromPose(pose.focus, pose.position, { roll: pose.roll ?? 0,
      fov: pose.fov ?? null });
    if (!orbit) return this.say(answer.reason || "The free camera is unavailable for this frame.");
    const [dx, dy, sliding] = this.pending, tuning = sceneCameraTuning();
    Object.assign(this, { pending: [0, 0, false], free: true, entry: orbit,
      orbit: dx || dy ? (sliding ? slid : turned)(orbit, dx, dy, tuning) : orbit });
    this.want(dx || dy ? "move" : "input");
    this.flightOn(performance.now());       // a movement key held through the entry moves from here
  }

  recorded(time) {
    this.interrupt();
    this.stopLook("recorded camera");
    this.releaseKeys();
    Object.assign(this, { free: false, notice: null, picking: false });
    this.want("input", time);
  }

  resetView(time) {
    this.interrupt();
    if (!this.free || !this.entry) return;
    this.orbit = this.entry;
    this.want("move", time);
  }

  async lookAtMario(time) {
    this.interrupt();
    if (!this.freeAvailable) return;
    await this.enterFree();
    const answer = this.free ? await this.pose(this.frame) : null;
    if (!answer?.pose) return answer && this.say(answer.reason);
    this.orbit = lookingAt(this.orbit, answer.pose.mario, sceneCameraTuning());
    this.want("move", time);
  }

  // A drag: it never pauses Play (his report, 2026-10-27: "It should not pause the gameplay when I
  // adjust the camera, unless I explicitly paused").
  move(dx, dy, sliding, time) {
    this.interrupt();
    if (!this.free) {
      this.pending = [this.pending[0] + dx, this.pending[1] + dy, sliding];
      return this.enterFree();
    }
    this.orbit = (sliding ? slid : turned)(this.orbit, dx, dy, sceneCameraTuning());
    this.want("move", time);
  }

  async dolly(wheel, time) {
    this.interrupt();
    await this.enterFree();
    if (!this.free) return;
    this.orbit = dollied(this.orbit, wheel, sceneCameraTuning());
    this.want("move", time);
  }

  // ---- Set focus: the next click on the picture puts the focus point on what it shows
  armPick(on = !this.picking) { this.picking = on && this.freeAvailable; this.changed(); }

  async pickAt(u, v, time) {
    if (this.finding) return;
    this.interrupt();
    this.finding = true;
    this.changed();
    try {
      const hit = await this.pickWorld(u, v);
      if (!hit) return;
      if (!hit.world) return this.say(hit.reason || "Nothing is drawn there to focus on.");
      this.picking = false;
      this.orbit = focusedOn(this.orbit, hit.world);
      this.notice = null;
      this.want("move", time);
    } finally {
      this.finding = false;
      this.changed();
    }
  }

  // The one way the page turns a place in the picture into a point in the
  // world (Set focus and fly-to both): the server finds where this frame's
  // list drew it from the camera on screen, in that camera's axes, and the
  // camera that asked places it. null when the free camera cannot start.
  async pickWorld(u, v) {
    await this.enterFree();
    if (!this.free) return null;
    const used = this.orbit;
    const answer = await this.ask("pick", { frame: this.frame, camera: cameraOf(used), u, v });
    return { world: answer.point ? worldPoint(used, answer.point) : null, mario: answer.mario || null,
      reason: answer.reason };
  }

  // ---- double-click: fly to the surface under the pointer, then look at Mario.
  // One round trip: the pick's answer carries Mario's place in the frame too.
  async flyTo(u, v, time) {
    this.interrupt();
    const serial = this.serial;
    const hit = await this.pickWorld(u, v);
    if (!hit || serial !== this.serial) return;
    if (!hit.world) return this.sayBriefly("Nothing there to fly to.");
    const mario = hit.mario || (await this.pose(this.frame)).pose?.mario;
    if (serial !== this.serial) return;
    const tuning = sceneCameraTuning();
    const end = mario && flyToTarget(this.orbit, hit.world, mario, tuning);
    if (!end) return this.sayBriefly("Nothing there to fly to.");
    this.notice = null;
    this.glideTo(end, tuning, time);
  }

  // An eased flight from the camera now to `end` (scenecamera.glideAt), one
  // picture asked for per animation frame; any input (interrupt) leaves it
  // where it is. Its clock starts at the click, but never more than
  // GLIDE_LEAD_MS back, so a slow answer cannot skip the gentle start, and its
  // first step runs at once: the first picture that moves goes out the moment
  // the destination is known.
  glideTo(end, tuning, time = performance.now()) {
    const now = performance.now(), from = this.orbit;
    const glide = { started: clamp(time, now - GLIDE_LEAD_MS, now) };
    this.glide = glide;
    let input = time;
    const step = (at) => {
      if (this.glide !== glide) return;
      const place = glideAt(from, end, at - glide.started, tuning);
      this.orbit = place.orbit;
      this.want("move", Math.min(input, at));
      input = Infinity;
      if (place.done) this.glide = null; else this.animate(step);
      this.changed();
    };
    step(now);
  }

  // ---- flight: W A S D and Q E whenever the page has the keyboard
  // A movement key (with Shift or Alt) went down or up: true when it is the
  // camera's. The first press in the recorded camera enters the free camera
  // from the picture on screen, and the key moves it once it is in. A key let
  // go of by Esc, R or leaving the page stays still until pressed again: its
  // held-down repeats never restart it.
  moveKey(event) {
    const flight = this.flight, code = event.code;
    Object.assign(flight, { fast: event.shiftKey, slow: event.altKey });
    if (!FLIGHT_KEYS[code]) return false;
    if (event.type !== "keydown") {
      flight.keys.delete(code);
      return true;
    }
    if (!this.scene || !this.freeAvailable) return false;
    if (event.repeat) return true;
    this.interrupt();
    if (flight.input == null) flight.input = event.timeStamp;
    flight.keys.add(code);
    if (this.free) this.flightOn(performance.now());
    else this.enterFree();
    return true;
  }

  releaseKeys() { this.flight = idleFlight(); }

  // One step of flight per animation frame, at a constant speed for the time
  // that passed (a stall moves at most a tenth of a second); stops the frame
  // the last key is let go, or once releaseKeys has let go of this flight.
  flightOn(time) {
    const flight = this.flight;
    if (flight.ticking || !this.free || !flight.keys.size) return;
    Object.assign(flight, { ticking: true, last: time });
    const tick = (now) => {
      if (this.flight !== flight) return;
      if (!this.free || !flight.keys.size) { flight.ticking = false; return; }
      const seconds = clamp((now - flight.last) / 1000, 0, 0.1), tuning = sceneCameraTuning();
      flight.last = now;
      this.orbit = flown(this.orbit, movementOf(flight.keys), seconds, flightSpeed(this.speed, flight, tuning), tuning);
      this.want("move", Math.min(flight.input ?? now, performance.now()));
      flight.input = null;
      this.animate(tick);
    };
    this.animate(tick);
  }

  // ---- looking around: only while the right button is held on the picture
  async startLook() {
    if (!this.freeAvailable || this.looking) return;
    this.interrupt();
    const look = { locked: false, token: null };
    Object.assign(this, { looking: look, picking: false });
    this.changed();
    await this.enterFree();
    if (!this.free && this.looking === look) this.stopLook("no free camera");
  }

  // Every way a look ends: `why` goes into the pointer lock's records.
  stopLook(why) {
    const was = this.looking;
    this.looking = null;
    if (was) this.noteLock("ended", why);
    this.exitLock(why);
    if (was) this.changed();
  }

  // ---- the pointer lock a look asks for. Chromium on Windows keeps the cursor
  // inside the page while a lock is held, so a lock no look is using walls the
  // cursor in (his report, 2026-10-26: "as if there's an invisible wall
  // blocking its movement"). Each request carries a token: a lock stays only
  // while the look that asked for it is in progress, the page shows and the
  // window has focus, and is exited the moment it lands otherwise (released
  // before the grant, a stale request, a hidden page, another window in front).
  // Every end of a look exits the lock it holds, and a request still pending
  // then has no look to land on. noteLock records each step in the UI log:
  // requested, granted, ended (the look, and why), exited (and why: what asked,
  // or the browser), error.
  requestLock(canvas) {
    const token = ++this.lockSerial;
    if (this.looking) this.looking.token = token;
    this.lockTarget = canvas;
    this.noteLock("requested", null, token);
    let asked;
    try { asked = canvas.requestPointerLock?.(); } catch { return this.noteLock("error", "request threw", token); }
    if (typeof asked?.then !== "function") return;
    this.lockPromised = token;
    asked.then(() => this.lockGranted(token), () => {});   // a refusal arrives as pointerlockerror
  }

  // Why a lock may not stay, or null: the look that asked is in progress, the page shows, the window has focus.
  unwelcome(token = this.looking?.token) {
    if (!this.looking) return "no look";
    if (token !== this.looking.token) return "stale request";
    if (document.hidden) return "page hidden";
    if (!document.hasFocus()) return "window unfocused";
    return null;
  }

  holdsLock() { return !!this.lockTarget && document.pointerLockElement === this.lockTarget; }

  // Request `token` granted (its promise): kept, or exited at once.
  lockGranted(token) {
    if (!this.holdsLock()) return;
    const why = this.unwelcome(token);
    if (why) this.exitLock(why);
    else this.lockKept(token);
  }

  lockKept(token) {
    if (this.looking.locked) return;
    this.looking.locked = true;
    this.noteLock("granted", null, token);
  }

  // pointerlockchange. A lock landing with no look to use it is exited; one whose request
  // promised an answer is judged by that answer (lockGranted), which knows its token. True
  // when the browser itself ended a lock a look was using (its own Esc): every movement ends.
  lockChanged() {
    if (this.holdsLock()) {
      const why = this.unwelcome();
      if (why) this.exitLock(why);
      else if (this.lockPromised !== this.looking.token) this.lockKept(this.looking.token);
      return false;
    }
    const asked = this.lockExit, used = !!this.looking?.locked;
    if (asked == null && !used) return false;
    this.lockExit = null;
    if (this.looking) this.looking.locked = false;
    this.noteLock("exited", asked ?? "the browser");
    return asked == null;
  }

  // Exits the lock this page holds, if it holds one; the browser's pointerlockchange confirms it.
  exitLock(why) {
    if (this.looking) this.looking.locked = false;
    if (!this.holdsLock()) return;
    this.lockExit = why;
    document.exitPointerLock?.();
  }

  noteLock(event, why = null, request = this.lockSerial) {
    this.observe({ surface: "scene_viewer", kind: "pointer_lock", event, why, request, look: !!this.looking,
      focused: document.hasFocus(), hidden: document.hidden });
  }

  look(dx, dy, time) {
    if (!this.looking || !this.free || (!dx && !dy)) return;
    this.orbit = looked(this.orbit, dx, dy, sceneCameraTuning());
    this.want("move", time);
  }

  stepSpeed(notches) { this.speed = speedStepped(this.speed, notches, sceneCameraTuning()); this.changed(); }

  resetSpeed() { this.speed = 1; this.changed(); }

  // Esc, letting go of the window, leaving the page, Close: every movement stops and every key is let go.
  release(why) {
    this.interrupt();
    Object.assign(this, { drag: null, picking: false });
    this.stopLook(why);
    this.releaseKeys();
    this.changed();
  }

  seek(next, kind = "input", time) {
    if (kind === "input") this.interrupt();
    this.frame = clamp(next, 0, (this.scene?.frames.length || 1) - 1);
    this.changed();
    this.want(kind, time);
  }

  step(delta, time) { this.pause(); this.seek(this.frame + delta, "input", time); }

  pause() {
    this.autoplay = false;
    if (this.playing) { this.stopPlay(); this.changed(); }
  }

  toggle() { this.interrupt(); if (this.playing) this.pause(); else this.play(); }

  // To the first frame: a play goes on from there at once, a pause stays paused on it.
  restart(time) {
    if (!this.playing) return this.seek(0, "input", time);
    this.interrupt();
    this.playFromStart();
  }

  // Restart while playing, and Loop at the end: the play begins again on the first frame.
  playFromStart() {
    this.stopPlay();
    this.frame = 0;
    this.play();
  }

  setLoop(on) {
    this.loop = on;
    try { localStorage.setItem(LOOP_PREFERENCE, on ? "on" : "off"); } catch { /* kept for this visit only */ }
    this.changed();
  }

  // The recording's own clock (sceneclock.js): each picture stays up as long
  // as the recording showed it. A play asks for each picture ahead, as long
  // before it is due as a picture takes to be ready plus a display frame, and
  // draws it on the display frame it is due, so it shows on time instead of
  // being asked for once due (which showed every picture one to three display
  // frames late, measured 2026-10-26). A newer request still replaces a
  // waiting one, so frames a slow server cannot keep up with are skipped. The
  // clock starts once the sound can start with it (sound.lead()), so the
  // first picture of a play holds that much longer. The frame on screen
  // (this.frame) is the last one drawn.
  //
  // A scene still being written plays on as it grows: at its newest picture the clock waits
  // (heldAt) and goes on from there when more arrives (grew), never skipping ahead to catch up.
  play() {
    const scene = this.scene;
    this.autoplay = false;
    if (!scene) return;
    this.sound.arm();
    const from = this.frame >= scene.frames.length - 1 && !scene.growing ? 0 : this.frame, times = recordedTimes(scene);
    const play = { from, start: performance.now() + this.sound.lead(), times, length: playLength(times, from, scene.fps),
      asked: from, frameAt: null, frameMs: 1000 / 60, gaps: [], holds: [], heldAt: null,
      dueOf: (frame) => play.start + play.times[frame] - play.times[from],
      grew: (grown) => {
        play.times = recordedTimes(grown);
        play.length = playLength(play.times, from, grown.fps);
        if (play.heldAt != null && play.times.length > play.asked + 1) {
          play.start += performance.now() - play.heldAt;
          play.heldAt = null;
          if (this.playback === play) this.sound.play(from, play.start);   // the sound follows the wait
        }
      } };
    Object.assign(this, { playback: play, playing: true, frame: from });
    const tick = (frameTime) => {
      if (this.playback !== play) return;
      if (play.frameAt != null) {
        play.gaps = [...play.gaps.slice(-7), frameTime - play.frameAt];
        play.frameMs = clamp(median(play.gaps), 4, 50);
      }
      play.frameAt = frameTime;
      if (frameTime - play.start >= play.length) {
        if (!scene.growing) return this.loop ? this.playFromStart() : this.finishPlay(scene);
        play.heldAt ??= frameTime;
      }
      for (const hold of play.holds.splice(0)) {
        if (hold.due <= frameTime + play.frameMs) hold.resolve(true);
        else play.holds.push(hold);
      }
      const ahead = pictureAt(play.times, from, Math.max(0, frameTime - play.start + this.readyMs() + play.frameMs));
      if (ahead !== play.asked) {
        play.asked = ahead;
        this.want("play", performance.now(), ahead);
      }
      requestAnimationFrame(tick);
    };
    this.want("play", performance.now(), from);
    this.changed();
    this.sound.play(from, play.start);
    requestAnimationFrame(tick);
  }

  // A play ran to its end: it stops on the last picture.
  finishPlay(scene) {
    const last = scene.frames.length - 1;
    this.pause();
    if (this.frame !== last) this.seek(last, "play");
  }
}

const KEYS = {
  ArrowLeft: (link, event) => link.step(-1, event.timeStamp),
  ArrowRight: (link, event) => link.step(1, event.timeStamp),
  ",": (link, event) => link.step(-1, event.timeStamp),
  ".": (link, event) => link.step(1, event.timeStamp),
  Home: (link, event) => link.step(-Infinity, event.timeStamp),
  End: (link, event) => link.step(Infinity, event.timeStamp),
  " ": (link) => link.toggle(),
  f: (link, event) => link.lookAtMario(event.timeStamp),
  r: (link, event) => link.recorded(event.timeStamp),
  m: (link) => link.sound.toggleMute(),
};

// A control that is clicked rather than typed into, and the keys it acts on
// itself while it has focus: Space and Enter press it, a slider's arrows move it.
const PRESSED = new Set(["button", "submit", "reset", "checkbox", "radio"]);
const SLIDER_KEYS = new Set(["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "PageUp", "PageDown"]);
function ownKey(target, key) {
  const type = target?.tagName === "INPUT" ? target.type : null;
  if (target?.tagName === "BUTTON" || PRESSED.has(type)) return key === " " || key === "Enter";
  return type === "range" && SLIDER_KEYS.has(key);
}

// A field that takes typing keeps every key: text and number fields, selects, contenteditable.
export function typesInto(target) {
  const tag = target?.tagName;
  return !!target?.isContentEditable || tag === "TEXTAREA" || tag === "SELECT"
    || (tag === "INPUT" && target.type !== "range" && !PRESSED.has(target.type));
}

// The page's one keyboard owner: each keydown on the viewer, or with nothing
// on the page focused (SceneViewer). The movement keys first, then the
// transport and camera keys. A field that types keeps every key, a focused
// control the keys it acts on itself (a mouse click hands the keyboard back to
// the viewer, so only a control reached with Tab keeps them), and Ctrl or Meta
// combinations are the browser's: reload, copy, devtools. Keys are let go
// wherever focus has gone since (useSceneLink).
function onKey(link, event, canvas) {
  if (event.ctrlKey || event.metaKey || typesInto(event.target)) return;
  const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
  if (key === "Escape") return endLook(link, canvas, "Esc", true);
  if (link.moveKey(event)) return event.preventDefault();
  if (key === "Alt" && link.freeAvailable) return event.preventDefault();   // Alt alone would open the window menu
  const action = KEYS[key];
  if (!action || !link.scene || ownKey(event.target, key)) return;
  event.preventDefault();
  action(link, event);
}

// Stops looking around (and a drag; and Set focus and every key when
// `everything`) and gives the pointer back: the pointer lock (stopLook) and
// the capture. `why` goes into the pointer lock's record.
function endLook(link, canvas, why, everything = false) {
  const drag = link.drag;
  if (everything) link.release(why); else { link.stopLook(why); link.drag = null; }
  for (const id of [drag?.id, link.capture].filter((value) => value != null)) {
    try { canvas?.releasePointerCapture(id); } catch { /* already released */ }
  }
  link.capture = null;
}

// pointerrawupdate reaches the page when the pointer moves; Chromium holds
// pointermove for the next animation frame, up to a frame later. It exists in
// secure contexts (the Trainer's localhost page is one); elsewhere pointermove
// carries the drag.
const MOVE_EVENT = typeof window !== "undefined" && "onpointerrawupdate" in window
  ? "onpointerrawupdate" : "onpointermove";

// A press that moves less than this is a click (a double-click's two halves
// among them), never a drag: the camera does not move until the pointer has.
export const DRAG_DEAD_ZONE = 4;

// Hold the right button: the viewer takes the pointer (lock, else capture) and the mouse looks around.
function takeOff(link, surface, event) {
  const canvas = event.currentTarget;
  event.preventDefault();
  surface.current?.focus({ preventScroll: true });
  canvas.setPointerCapture(event.pointerId);
  link.capture = event.pointerId;
  link.startLook();
  link.requestLock(canvas);          // refused, the capture still holds
}

function pointerHandlers(link, surface) {
  const movable = () => link.scene && link.freeAvailable;
  return {
    onpointerdown: (event) => {
      if (!movable()) return;
      const canvas = event.currentTarget;
      if (event.button === 2 && !link.drag) return takeOff(link, surface, event);
      if ((event.button !== 0 && event.button !== 1) || link.looking) return;
      if (event.button === 1) event.preventDefault();  // the middle button slides, never autoscrolls
      if (event.button === 0 && link.picking) {
        const at = pictureFraction(canvas, event.clientX, event.clientY);
        if (at) link.pickAt(at.u, at.v, event.timeStamp);
        return;
      }
      canvas.setPointerCapture(event.pointerId);
      link.drag = { x: event.clientX, y: event.clientY, id: event.pointerId, sliding: event.button === 1,
        moving: false };
    },
    [MOVE_EVENT]: (event) => {
      if (link.looking) {
        if (!(event.buttons & 2)) return endLook(link, event.currentTarget, "button up elsewhere");
        return link.look(event.movementX, event.movementY, event.timeStamp);
      }
      const drag = link.drag;
      if (!drag || drag.id !== event.pointerId) return;
      const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
      if (!drag.moving && Math.hypot(dx, dy) < DRAG_DEAD_ZONE) return;
      link.drag = { ...drag, x: event.clientX, y: event.clientY, moving: true };
      if (dx || dy) link.move(dx, dy, drag.sliding || event.shiftKey, event.timeStamp);
    },
    onmousedown: (event) => { if (event.button === 1 && movable()) event.preventDefault(); },
    onauxclick: (event) => { if (event.button === 1 && movable()) event.preventDefault(); },
    ondblclick: (event) => {
      if (!movable() || link.looking || event.button !== 0) return;
      const at = pictureFraction(event.currentTarget, event.clientX, event.clientY);
      if (at) link.flyTo(at.u, at.v, event.timeStamp);
    },
    onpointerup: (event) => {
      if (link.looking && !(event.buttons & 2)) endLook(link, event.currentTarget, "button up");
      link.drag = null;
    },
    onpointercancel: (event) => endLook(link, event.currentTarget, "pointer cancelled"),
    onlostpointercapture: () => { link.drag = null; },
    oncontextmenu: (event) => { if (movable()) event.preventDefault(); },
    onwheel: (event) => {
      if (!movable()) return;
      event.preventDefault();
      if (link.looking) link.stepSpeed(-wheelPixels(event) / WHEEL_NOTCH);
      else link.dolly(wheelPixels(event), event.timeStamp);
    },
  };
}

function openerReason(status) {
  if (!status) return "Reading the lab setup...";
  if (status.problems?.length) return status.problems.join(" ");
  if (status.hold) return `The renderer is on hold: ${status.hold}`;
  return status.packages?.length ? null : "No scene packages in the lab's folder yet.";
}

function Opener({ status, opening, open }) {
  const [choice, setChoice] = useState("");
  const packages = status?.packages || [];
  const picked = packages.some((row) => row.name === choice) ? choice : packages[0]?.name || "";
  const why = openerReason(status);
  return html`<div class="scene-lab-open">
    <label>Recording
      <select value=${picked} disabled=${!packages.length || opening}
          onchange=${(event) => setChoice(event.currentTarget.value)}>
        ${packages.map((row) => html`<option key=${row.name} value=${row.name}>
          ${row.name} (${megabytes(row.bytes)})</option>`)}
      </select>
    </label>
    <button type="button" class="primary" disabled=${!!why || opening || !picked}
        title=${why || "Start the renderer and show this recording"} onclick=${() => open({ package: picked })}>
      ${opening ? "Opening..." : "Open"}</button>
    ${why && html`<p class="scene-lab-reason">${why}</p>`}
  </div>`;
}

function frameWords(frame) {
  if (!frame) return "";
  return [frame.level != null && `level ${frame.level}`, frame.area != null && `area ${frame.area}`,
    frame.game_frame != null && `game frame ${frame.game_frame}`].filter(Boolean).join(", ");
}

function Transport({ link }) {
  const frames = link.scene.frames;
  return html`<div class="scene-lab-bar" aria-label="Playback">
    <button type="button" onclick=${(event) => link.restart(event.timeStamp)}
        title=${link.playing ? "Play again from the first frame" : "Go to the first frame"}>
      <${Icon} name="restart" size=${15} /> Restart</button>
    <button type="button" onclick=${(event) => link.step(-1, event.timeStamp)} title="Back one frame (Left arrow or ,)">
      <${Icon} name="stepBack" size=${15} /> Back 1</button>
    <button type="button" class="primary" onclick=${() => link.toggle()} title="Play or pause (Space)">
      <${Icon} name=${link.playing ? "pause" : "play"} size=${16} /> ${link.playing ? "Pause" : "Play"}</button>
    <button type="button" onclick=${(event) => link.step(1, event.timeStamp)} title="Forward one frame (Right arrow or .)">
      <${Icon} name="stepForward" size=${15} /> Forward 1</button>
    <button type="button" aria-pressed=${link.loop} onclick=${() => link.setLoop(!link.loop)}
        title="When on, Play goes on from the first frame after the last">
      <${Icon} name="loop" size=${15} /> Loop</button>
    <input type="range" min="0" max=${Math.max(0, frames.length - 1)} value=${link.frame} aria-label="Frame"
      oninput=${(event) => { link.pause(); link.seek(Number(event.currentTarget.value), "input", event.timeStamp); }} />
    <span class="scene-lab-frame">Frame ${link.frame + 1} of ${frames.length}${link.scene.growing ? " so far" : ""}
      <small>${frameWords(frames[link.frame])}</small></span>
  </div>`;
}

// The hint teaches only what applies right now.
export function hintFor(link) {
  if (!link.freeAvailable) return `Only the recorded camera is available: ${link.scene.free_camera.reason}`;
  if (link.picking) return "Click a surface in the picture to turn around it. Esc cancels.";
  if (link.looking) return "Move the mouse to look around. W A S D move, Q and E go down and up, Shift is faster, Alt slower, the wheel sets the speed. Let go of the right button to stop looking.";
  if (link.free) return "W A S D move, Q and E go down and up, Shift is faster, Alt slower; hold the right button to look around. Drag to turn around the focus point, Shift-drag or middle-drag to slide, the wheel moves closer, double-click a surface to fly there. F looks at Mario, R returns to the recorded camera.";
  return "W A S D move the camera from this picture, Q and E go down and up; dragging, scrolling, double-clicking or holding the right button move it too. Left and Right (or , and .) step one frame, Space plays.";
}

// The same lesson on the picture itself while it is what you are acting on:
// looking around with the right button, or choosing a point.
export function cueFor(link) {
  if (link.picking) return "Click a surface to turn around it · Esc cancels";
  if (link.looking) return `W A S D move · Q E down, up · Shift faster · Alt slower · wheel: speed ${speedWords(link.speed)}`;
  return null;
}

function SpeedControl({ link }) {
  const tuning = sceneCameraTuning(), unitsPerSecond = Math.round(flightSpeed(link.speed, {}, tuning));
  return html`<div class="scene-lab-speed" role="group" aria-label="Flight speed"
      title=${`W A S D move ${unitsPerSecond} units a second; the wheel changes it while you hold the right button`}>
    <span>Flight speed</span>
    <button type="button" aria-label="Slower" onclick=${() => link.stepSpeed(-1)}>−</button>
    <output aria-live="polite">${speedWords(link.speed)}</output>
    <button type="button" aria-label="Faster" onclick=${() => link.stepSpeed(1)}>+</button>
    <button type="button" disabled=${link.speed === 1} onclick=${() => link.resetSpeed()}
      title="Back to the flight speed at 1x">Reset</button>
  </div>`;
}

function CameraBar({ link, tuning, setTuning }) {
  const available = link.freeAvailable, reason = link.scene.free_camera.reason;
  return html`<div class="scene-lab-bar" aria-label="Camera">
    <button type="button" aria-pressed=${!link.free} onclick=${(event) => link.recorded(event.timeStamp)}
        title="Show what the player saw (R)">Recorded camera</button>
    <button type="button" aria-pressed=${link.free} disabled=${!available}
        title=${available ? "Move the camera yourself, starting from the picture on screen" : reason}
        onclick=${() => link.enterFree()}>Free camera</button>
    <button type="button" disabled=${!available} onclick=${(event) => link.lookAtMario(event.timeStamp)}
        title=${available ? "Point the free camera at Mario (F)" : reason}>Look at Mario</button>
    <button type="button" aria-pressed=${link.picking} disabled=${!available || link.finding}
        onclick=${() => link.armPick()}
        title=${available ? "Then click a surface in the picture: the camera turns around that point" : reason}>
      ${link.finding ? "Finding..." : "Set focus"}</button>
    <button type="button" disabled=${!link.moved} onclick=${(event) => link.resetView(event.timeStamp)}
        title=${link.free ? "Back to where Free camera began" : "Available once you move the free camera"}>
      Reset view</button>
  </div>
  <div class="scene-lab-bar" aria-label="Flight and pictures">
    ${available && html`<${SpeedControl} link=${link} />`}
    <label class="scene-lab-quality">Pictures
      <select value=${link.encoding} onchange=${(event) => { link.encoding = event.currentTarget.value; link.want("input"); }}>
        ${link.sharedAvailable && html`<option value="shared">Exact pixels, fastest (shared memory)</option>`}
        <option value="jpeg">Faster (JPEG)</option>
        <option value="raw">Exact pixels (slower)</option>
      </select></label>
    ${available && html`<button type="button" aria-pressed=${tuning} onclick=${() => setTuning(!tuning)}
        title="Adjust how the free camera feels, and save the numbers as the defaults">Tune controls</button>`}
  </div>
  <p class="scene-lab-hint">${hintFor(link)}</p>`;
}

const ENCODING_WORDS = { jpeg: "JPEG", raw: "exact pixels", shared: "exact pixels through shared memory" };

function Readout({ link }) {
  const shown = link.shown, timings = shown?.timings || {}, moving = link.moving;
  if (!shown) return null;
  const rate = moving && (moving.pictures_per_second == null ? `${moving.pictures} picture`
    : `${Math.round(moving.pictures_per_second)} pictures a second`);
  return html`<p class="scene-lab-readout">
    ${shown.width}x${shown.height} ${ENCODING_WORDS[shown.encoding] || shown.encoding},
    ${" "}input to picture ${ms(percentile(link.latencies, 0.5))} (p95 ${ms(percentile(link.latencies, 0.95))}),
    ${" "}server ${ms(shown.server_ms)}: game code ${ms(timings.evaluate_ms)}, renderer ${ms(timings.host_ms)},
    ${" "}encode ${ms(timings.encode_ms)}
  </p>
  ${moving && html`<p class="scene-lab-readout scene-lab-moving" data-summary=${JSON.stringify(moving)}>
    Last time the camera moved: ${rate}, input to picture ${ms(moving.input_to_drawn_ms?.p50)}
    ${" "}(p95 ${ms(moving.input_to_drawn_ms?.p95)}), decode ${ms(moving.decode_ms?.p50)},
    ${" "}server ${ms(moving.server_ms?.p50)}</p>`}`;
}

// The UI observation log's reader for this page (ui/uilog.js posts it; core/uilog.py keeps the
// "scene_viewer" surface): the summary the readout shows for the last time the camera moved.
export function readSceneViewer(root) {
  const line = root?.querySelector(".scene-lab-moving");
  if (!line) return null;
  try {
    return { surface: "scene_viewer", ...JSON.parse(line.dataset.summary) };
  } catch {
    return null;
  }
}

// The camera's tuning inspector: one control per registry row, live on the
// next movement; SAVE writes the changed numbers into scenecameratuning.js as
// the shipped defaults (server/tuning_api.py, a source checkout only).
export function TunePanel() {
  const [values, setValues] = useState(() => ({ ...sceneCameraTuning() }));
  const [shipped, setShipped] = useState(() => ({ ...SCENE_CAMERA_DEFAULTS }));
  const [status, setStatus] = useState(null);
  const changed = Object.fromEntries(Object.entries(values).filter(([key, value]) => value !== shipped[key]));
  const apply = (next) => { setValues(next); setSceneCameraTuning(next); };
  async function save() {
    setStatus({ ok: true, text: "Saving..." });
    try {
      const answer = await send("POST", "/api/tuning/scenecamera", { values: changed });
      setShipped({ ...values });
      setStatus({ ok: true, text: answer.written ? `Saved ${answer.written} as the shipped defaults.`
        : "Nothing to save: every number is already the shipped default." });
    } catch (problem) { setStatus({ ok: false, text: problem.message }); }
  }
  return html`<section class="practice-card scene-lab-tune" aria-label="Tune controls">
    <div class="scene-lab-tune-head">
      <h3>Tune controls</h3>
      <button type="button" disabled=${!Object.keys(changed).length} onclick=${() => apply({ ...shipped })}
        title="Put every number back to the shipped default">Shipped values</button>
      <button type="button" class="primary" disabled=${!Object.keys(changed).length} onclick=${save}
        title="Write these numbers into the repo as the new defaults">SAVE</button>
    </div>
    <p class="scene-lab-hint">Changes apply on your next movement. Hover a label for what it does.
      ${" "}${Object.keys(changed).length ? `${Object.keys(changed).length} changed from the shipped values.` : ""}</p>
    ${status && html`<p class=${`scene-lab-hint ${status.ok ? "" : "is-bad"}`} role="status">${status.text}</p>`}
    <div class="scene-lab-tune-groups">
      <${ControlGroups} groups=${SCENE_CAMERA_GROUPS} rows=${SCENE_CAMERA_TUNABLES} values=${values}
        defaults=${shipped} onChange=${(name, value) => apply({ ...values, [name]: value })} />
    </div>
  </section>`;
}

function Player({ link, canvas, surface }) {
  const [tuning, setTuning] = useState(false);
  const classes = [link.freeAvailable && "is-movable", link.picking && "is-picking", link.looking && "is-flying"];
  return html`<section class="practice-card scene-lab-player">
    <div class="scene-lab-stage">
      <canvas ref=${canvas} width="640" height="480" class=${classes.filter(Boolean).join(" ")}
        aria-label="The recording's picture" ...${pointerHandlers(link, surface)}></canvas>
      <span class=${`scene-lab-tag ${link.free ? "is-free" : ""}`}>${link.free ? "Free camera" : "Recorded camera"}</span>
      ${link.drawing && html`<span class="scene-lab-busy">Drawing...</span>`}
      ${cueFor(link) && html`<p class="scene-lab-cue" aria-hidden="true">${cueFor(link)}</p>`}
    </div>
    ${link.notice && html`<p class="scene-lab-reason" role="status">${link.notice}</p>`}
    <${Transport} link=${link} />
    <${SoundBar} sound=${link.sound} frame=${link.frame} />
    <${CameraBar} link=${link} tuning=${tuning} setTuning=${setTuning} />
    <${Readout} link=${link} />
  </section>
  ${tuning && html`<${TunePanel} />`}`;
}

function useSceneLink(canvas, refresh) {
  const [, setRevision] = useState(0);
  const link = useRef(null);
  if (!link.current) {
    link.current = new SceneLink({
      draw: (header, bytes, claim, ready) => paint(canvas.current, header, bytes, claim, ready),
      changed: () => setRevision((revision) => revision + 1), ended: () => refresh.current(),
      observe: throttledNotes(postObservation) });
  }
  useEffect(() => {
    const scene = link.current, release = (why) => endLook(scene, canvas.current, why, true);
    const hidden = () => { if (document.hidden) { scene.pause(); release("window hidden"); } };
    const blurred = () => release("window blur");
    const lock = () => { if (scene.lockChanged()) release("the browser"); };
    const refused = () => scene.noteLock("error");
    // A key let go of anywhere is let go of: focus may have left the viewer since it went down.
    const keyUp = (event) => {
      scene.moveKey(event);
      if (event.key === "Alt" && scene.freeAvailable && event.target.closest?.(".scene-lab")) event.preventDefault();
    };
    document.addEventListener("visibilitychange", hidden);
    document.addEventListener("pointerlockchange", lock);
    document.addEventListener("pointerlockerror", refused);
    window.addEventListener("blur", blurred);
    window.addEventListener("keyup", keyUp);
    return () => {
      release("left the page");
      document.removeEventListener("visibilitychange", hidden);
      document.removeEventListener("pointerlockchange", lock);
      document.removeEventListener("pointerlockerror", refused);
      window.removeEventListener("blur", blurred);
      window.removeEventListener("keyup", keyUp);
      // The server frees the renderer once no page has shown the scene for a
      // little while, so a tab switch and back keeps it open.
      scene.disconnect();
      scene.sound.close();
    };
  }, []);
  return link.current;
}

// `t.sceneLab.intent` (app.js): the Practice log's Ghost button names an
// attempt; the viewer opens that attempt's scene and plays it, once per click.
export function SceneViewer({ t }) {
  const lab = t?.sceneLab, intent = lab?.intent;
  const [status, setStatus] = useState(null);
  const [opening, setOpening] = useState(false);
  const canvas = useRef(null), surface = useRef(null), refresh = useRef(null), pointed = useRef(null);
  const link = useSceneLink(canvas, refresh);
  refresh.current = async () => {
    try {
      const next = await getJSON(`${ROOT}/status`);
      setStatus(next);
      return next;
    } catch (problem) { link.fail(problem.message); return null; }
  };
  useEffect(() => {
    if (intent) return;              // the attempt below replaces whatever scene was open
    refresh.current().then((next) => { if (next?.session) link.connect(next.session); });
  }, []);
  useEffect(() => {
    if (!intent) return;
    lab.clearIntent();
    open({ attempt: intent.attempt }, { play: true });
  }, [intent]);
  // Arriving gives the viewer the keyboard (from the sidebar, or the Ghost button's open), and a
  // key pressed while nothing on the page has focus (a clicked control went away) is its too.
  useEffect(() => {
    surface.current?.focus({ preventScroll: true });
    const stray = (event) => { if (event.target === document.body) onKey(link, event, canvas.current); };
    document.addEventListener("keydown", stray);
    return () => document.removeEventListener("keydown", stray);
  }, []);
  // One observation per summary, read off the page once it shows it. Not useUiLog: that runs on
  // every render, and this page renders every picture, which would claim the practice page's
  // latency marks (ui/latency.js) dozens of times a second.
  useEffect(() => {
    const observation = link.moving && readSceneViewer(surface.current);
    if (observation) postObservation(observation);
  }, [link.moving]);
  // `body`: {package} from the picker, or {attempt} from the Ghost button. A refusal shows the
  // server's own sentence either way.
  async function open(body, { play = false } = {}) {
    setOpening(true);
    link.fail(null);
    try {
      link.connect(await send("POST", `${ROOT}/open`, body), { play });
      surface.current?.focus();
    } catch (problem) { link.fail(problem.message); }
    setOpening(false);
    refresh.current();
  }
  async function close() {
    endLook(link, canvas.current, "closed", true);
    link.disconnect();
    link.scene = null;
    link.changed();
    try { await send("POST", `${ROOT}/close`); } catch (problem) { link.fail(problem.message); }
    refresh.current();
  }
  const keys = (event) => {
    pointed.current = null;          // a key reaching the page: whatever has focus is used from the keyboard
    onKey(link, event, canvas.current);
  };
  // A mouse click on one of the page's controls hands the keyboard back to the viewer, so Space
  // plays after clicking Free camera; a control pressed from the keyboard (detail 0) keeps it.
  // A select's choice made with the mouse does the same (its letters would otherwise pick an
  // option by its first letter: E and S switch Pictures to "Exact..."); one reached with Tab
  // keeps its keys.
  const clicked = (event) => {
    if (event.detail > 0 && !typesInto(event.target)) surface.current?.focus({ preventScroll: true });
  };
  const pointedAt = (event) => { pointed.current = event.target; };
  const chosen = (event) => {
    if (event.target.tagName === "SELECT" && pointed.current === event.target) surface.current?.focus({ preventScroll: true });
  };
  // Focus leaving for the rest of the page, or for a field that types, lets go of every key.
  const left = (event) => {
    const next = event.relatedTarget;
    if (!next || !surface.current?.contains(next) || typesInto(next)) link.releaseKeys();
  };
  return html`<div class="workshop-page scene-lab" ref=${surface} tabindex="-1" onkeydown=${keys}
      onclick=${clicked} onpointerdown=${pointedAt} onchange=${chosen} onfocusout=${left}>
    <header class="practice-card workshop-hero">
      <div class="workshop-title">
        <span class="workshop-title-icon"><${Icon} name="ghost" size=${22} /></span>
        <div><span class="eyebrow">Lab</span><h2>Scene viewer</h2></div>
      </div>
      ${link.scene ? html`<div class="scene-lab-open">
          <span class="scene-lab-title" title=${sceneTitle(link.scene, t?.view)}>${sceneTitle(link.scene, t?.view)}</span>
          <button type="button" onclick=${close} title="Close this recording and stop the renderer">Close</button>
        </div>`
        : html`<${Opener} status=${status} opening=${opening} open=${open} />`}
    </header>
    ${link.error && html`<p class="scene-lab-error" role="status">${link.error}</p>`}
    ${!link.scene && status?.ended && !link.error && html`<p class="scene-lab-reason">${status.ended.message}</p>`}
    ${link.scene && html`<${Player} link=${link} canvas=${canvas} surface=${surface} />`}
  </div>`;
}
