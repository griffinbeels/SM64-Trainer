// src/sm64_events/ui/components/sceneviewer.js -- the Scene viewer (M4 E3, lab only).
//
// One recording in its reconstructed stage: the game's own pictures from the
// recorded camera, and a free camera the server redraws by running the game's
// drawing code (server/scene_lab_api.py, tools/scene_lab/scene_session.py).
// app.js imports this module only after GET /api/scene-lab answers, so with
// the lab switched off none of it loads. Browser and desktop share it whole.
//
// The page never redraws on its own: SceneLink asks for one picture at a time
// over the socket, and a newer step, seek or camera move replaces the request
// waiting behind the one being drawn (latest wins; the server does the same).
// A picture stops being in flight when it arrives: the next request goes out
// while it decodes and draws, so decoding never holds up the server.
// The free camera lives in the game's world coordinates, so it stays where
// you put it while you step or seek. Controls (plan section 7 prototype):
// drag turns around the focus point, Shift-drag slides, the wheel moves
// closer or back, F looks at Mario, R returns to the recorded camera.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { Icon } from "./icons.js";
import { getJSON, send } from "../api.js";

const html = htm.bind(h);
const ROOT = "/api/scene-lab";
// Feel numbers, untuned: Griffin judges them before they harden.
export const SCENE_CAMERA = { turnPerPixel: 0.006, slidePerPixel: 0.0015, dollyPerWheel: 0.001,
  minDistance: 50, maxDistance: 30000, pitchLimit: 1.45, marioHeight: 120 };

if (!document.querySelector("link[data-scene-lab]")) {
  const sheet = Object.assign(document.createElement("link"), { rel: "stylesheet", href: "/ui/scenelab.css" });
  sheet.dataset.sceneLab = "";
  document.head.append(sheet);
}

const sub = (a, b) => a.map((value, i) => value - b[i]);
const add = (a, b) => a.map((value, i) => value + b[i]);
const scale = (a, s) => a.map((value) => value * s);
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const unit = (a) => { const n = Math.hypot(...a) || 1; return scale(a, 1 / n); };
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));

// An orbit around `focus`: yaw about the world's up axis, pitch above the horizon.
export function orbitFromPose(focus, position) {
  const d = sub(position, focus), dist = Math.max(SCENE_CAMERA.minDistance, Math.hypot(...d));
  return { focus: [...focus], yaw: Math.atan2(d[0], d[2]), pitch: Math.asin(clamp(d[1] / dist, -1, 1)), dist };
}
export function cameraOf(orbit) {
  const { focus, yaw, pitch, dist } = orbit;
  const offset = [dist * Math.cos(pitch) * Math.sin(yaw), dist * Math.sin(pitch), dist * Math.cos(pitch) * Math.cos(yaw)];
  return { focus: [...focus], position: add(focus, offset) };
}
export function turned(orbit, dx, dy) {
  const limit = SCENE_CAMERA.pitchLimit;
  return { ...orbit, yaw: orbit.yaw - dx * SCENE_CAMERA.turnPerPixel,
    pitch: clamp(orbit.pitch + dy * SCENE_CAMERA.turnPerPixel, -limit, limit) };
}
export function slid(orbit, dx, dy) {        // grab and drag: the scene follows the pointer
  const { focus, position } = cameraOf(orbit);
  const forward = unit(sub(focus, position)), right = unit(cross(forward, [0, 1, 0])), up = cross(right, forward);
  const step = orbit.dist * SCENE_CAMERA.slidePerPixel;
  return { ...orbit, focus: add(focus, add(scale(right, -dx * step), scale(up, dy * step))) };
}
export function dollied(orbit, wheel) {
  return { ...orbit, dist: clamp(orbit.dist * Math.exp(wheel * SCENE_CAMERA.dollyPerWheel),
    SCENE_CAMERA.minDistance, SCENE_CAMERA.maxDistance) };
}

function percentile(values, p) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.round(p * (sorted.length - 1)))];
}
const ms = (value) => (value == null ? "-" : `${Math.round(value)} ms`);
const megabytes = (bytes) => `${(bytes / 1048576).toFixed(1)} MB`;

function drawRaw(context, header, bytes) {  // ReadScreen's BGR rows, bottom-up
  const { width, height } = header, picture = context.createImageData(width, height);
  const target = new Uint32Array(picture.data.buffer), row = width * 3;
  for (let y = 0; y < height; y++) {
    let s = (height - 1 - y) * row, d = y * width;
    for (let x = 0; x < width; x++, s += 3) target[d++] = 0xff000000 | (bytes[s] << 16) | (bytes[s + 1] << 8) | bytes[s + 2];
  }
  context.putImageData(picture, 0, 0);
}

// Draws the picture, unless `current()` says a newer one arrived while this
// one decoded.
async function paint(canvas, header, bytes, current = () => true) {
  const context = canvas?.getContext("2d");
  if (!context) return;
  const bitmap = header.encoding === "raw" ? null
    : await createImageBitmap(new Blob([bytes], { type: "image/jpeg" }));
  if (!current()) { bitmap?.close(); return; }
  if (canvas.width !== header.width || canvas.height !== header.height) {
    canvas.width = header.width;
    canvas.height = header.height;
  }
  if (!bitmap) return drawRaw(context, header, bytes);
  context.drawImage(bitmap, 0, 0);
  bitmap.close();
}

// The open scene's requests and camera: one picture in flight, the newest
// input waiting behind it, input time kept from the EARLIEST input so
// input-to-picture stays honest (until that picture is drawn). `changed`
// re-renders the page.
export class SceneLink {
  constructor({ draw, changed, ended }) {
    Object.assign(this, { draw, changed, ended });
    Object.assign(this, { scene: null, frame: 0, free: false, orbit: null, encoding: "jpeg", playing: false,
      shown: null, notice: null, error: null, drawing: false, ws: null, inFlight: null, wanted: null,
      sequence: 0, poses: new Map(), latencies: [], pending: [0, 0, false], drag: null, entering: false,
      arrived: 0 });
  }

  get freeAvailable() { return !!this.scene?.free_camera?.available; }

  connect(scene) {
    this.disconnect();
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${ROOT}/ws`);
    ws.binaryType = "arraybuffer";
    ws.onmessage = (event) => (typeof event.data === "string"
      ? this.onText(JSON.parse(event.data)) : this.onPicture(event.data));
    ws.onopen = () => this.want("input");
    ws.onclose = () => { if (this.ws === ws) this.end("The connection to the scene viewer closed."); };
    Object.assign(this, { ws, scene, frame: 0, free: false, orbit: null, inFlight: null, wanted: null,
      notice: null, error: null });
    this.changed();
  }

  disconnect() {
    this.playing = false;
    if (this.ws) { this.ws.onclose = null; this.ws.close(); this.ws = null; }
  }

  end(message) {
    this.disconnect();
    Object.assign(this, { scene: null, free: false, notice: null, error: message, inFlight: null, wanted: null });
    this.changed();
    this.ended();
  }

  want(kind = "input", inputTime = performance.now()) {
    if (!this.wanted) this.wanted = { kind, inputTime };
    else if (kind === "input") this.wanted.kind = "input";
    queueMicrotask(() => this.pump());
  }

  pump() {
    const ready = this.ws && this.ws.readyState === WebSocket.OPEN && this.scene;
    if (this.inFlight || !this.wanted || !ready) return;
    const id = ++this.sequence, camera = this.free && this.orbit ? cameraOf(this.orbit) : null;
    this.inFlight = { ...this.wanted, id, sent: performance.now() };
    this.wanted = null;
    this.ws.send(JSON.stringify({ id, session: this.scene.session, frame: this.frame, camera, encoding: this.encoding }));
    this.drawing = true;
    this.changed();
  }

  async onPicture(buffer) {
    const received = performance.now(), arrived = ++this.arrived;
    const length = new DataView(buffer).getUint32(0, true);
    const header = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 4, length)));
    const request = this.inFlight && this.inFlight.id === header.id ? this.inFlight : null;
    if (request) {
      this.inFlight = null;
      this.pump();                    // the server draws the next while this one decodes
    }
    const current = () => arrived === this.arrived;
    await this.draw(header, new Uint8Array(buffer, 4 + length), current);
    if (!current()) return;           // a newer picture arrived while this one decoded
    if (request) {
      header.trip_ms = received - request.sent;
      header.input_ms = performance.now() - request.inputTime;
      if (request.kind === "input") this.latencies = [...this.latencies.slice(-99), header.input_ms];
    }
    Object.assign(this, { shown: header, notice: header.reason || null, drawing: !!this.inFlight });
    this.changed();
    this.pump();
  }

  onText(message) {
    const resolve = this.poses.get(message.id);
    this.poses.delete(message.id);
    if (resolve) resolve(message.kind === "pose" ? message : { pose: null, reason: message.error });
    if (message.closed) return this.end(message.error);
    if (resolve) return;
    if (this.inFlight && message.id === this.inFlight.id) this.inFlight = null;
    if (message.error) this.error = message.error;
    this.drawing = false;
    this.changed();
    this.pump();
  }

  pose(frame) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN || !this.scene) return Promise.resolve({ pose: null });
    const id = ++this.sequence;
    return new Promise((resolve) => {
      this.poses.set(id, resolve);
      this.ws.send(JSON.stringify({ id, session: this.scene.session, kind: "pose", frame }));
    });
  }

  say(notice) { this.notice = notice; this.changed(); }

  fail(error) { this.error = error; this.changed(); }

  // Enter the free camera from the camera on screen; drags made while the
  // recorded camera is being read are applied once it arrives.
  async enterFree() {
    if (this.free || this.entering || !this.freeAvailable) return;
    this.entering = true;
    const answer = await this.pose(this.frame);
    this.entering = false;
    if (!answer.pose) return this.say(answer.reason || "The free camera is unavailable for this frame.");
    const [dx, dy, sliding] = this.pending;
    const orbit = orbitFromPose(answer.pose.focus, answer.pose.position);
    Object.assign(this, { pending: [0, 0, false], free: true,
      orbit: dx || dy ? (sliding ? slid : turned)(orbit, dx, dy) : orbit });
    this.want("input");
  }

  recorded(time) {
    Object.assign(this, { free: false, notice: null });
    this.want("input", time);
  }

  async lookAtMario(time) {
    if (!this.freeAvailable) return;
    await this.enterFree();
    const answer = this.free ? await this.pose(this.frame) : null;
    if (!answer?.pose) return answer && this.say(answer.reason);
    const [x, y, z] = answer.pose.mario;
    this.orbit = { ...this.orbit, focus: [x, y + SCENE_CAMERA.marioHeight, z] };
    this.want("input", time);
  }

  move(dx, dy, sliding, time) {
    this.pause();
    if (!this.free) {
      this.pending = [this.pending[0] + dx, this.pending[1] + dy, sliding];
      return this.enterFree();
    }
    this.orbit = (sliding ? slid : turned)(this.orbit, dx, dy);
    this.want("input", time);
  }

  async dolly(wheel, time) {
    await this.enterFree();
    if (!this.free) return;
    this.orbit = dollied(this.orbit, wheel);
    this.want("input", time);
  }

  seek(next, kind = "input", time) {
    this.frame = clamp(next, 0, (this.scene?.frames.length || 1) - 1);
    this.changed();
    this.want(kind, time);
  }

  step(delta, time) { this.pause(); this.seek(this.frame + delta, "input", time); }

  pause() { if (this.playing) { this.playing = false; this.changed(); } }

  toggle() { if (this.playing) this.pause(); else this.play(); }

  // 30 fps against the clock: the frame due now is asked for whenever nothing
  // is in flight, and frames that passed while one was drawn are skipped.
  play() {
    const scene = this.scene;
    if (!scene) return;
    const from = this.frame >= scene.frames.length - 1 ? 0 : this.frame, start = performance.now();
    this.playing = true;
    const tick = () => {
      if (!this.playing || this.scene !== scene) return;
      const at = from + Math.floor((performance.now() - start) * scene.fps / 1000);
      if (at >= scene.frames.length) { this.seek(scene.frames.length - 1, "play"); this.pause(); return; }
      if (at !== this.frame) this.seek(at, "play");
      requestAnimationFrame(tick);
    };
    this.seek(from, "play");
    requestAnimationFrame(tick);
  }
}

const KEYS = {
  ArrowLeft: (link, event) => link.step(-1, event.timeStamp),
  ArrowRight: (link, event) => link.step(1, event.timeStamp),
  Home: (link, event) => link.step(-Infinity, event.timeStamp),
  End: (link, event) => link.step(Infinity, event.timeStamp),
  " ": (link) => link.toggle(),
  f: (link, event) => link.lookAtMario(event.timeStamp),
  r: (link, event) => link.recorded(event.timeStamp),
};

function onKey(link, event) {
  const tag = event.target.tagName, key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
  const action = KEYS[key];
  if (key === "Escape") return releaseDrag(link, event.currentTarget.querySelector("canvas"));
  const ownKey = tag === "BUTTON" && (key === " " || key === "Enter");
  if (!action || !link.scene || tag === "SELECT" || tag === "INPUT" || ownKey) return;
  event.preventDefault();
  action(link, event);
}

function releaseDrag(link, canvas) {
  const drag = link.drag;
  link.drag = null;
  try { if (drag) canvas?.releasePointerCapture(drag.id); } catch { /* already released */ }
}

// pointerrawupdate reaches the page when the pointer moves; Chromium holds
// pointermove for the next animation frame, up to a frame later. It exists in
// secure contexts (the Trainer's localhost page is one); elsewhere pointermove
// carries the drag.
const MOVE_EVENT = typeof window !== "undefined" && "onpointerrawupdate" in window
  ? "onpointerrawupdate" : "onpointermove";

function pointerHandlers(link) {
  const movable = () => link.scene && link.freeAvailable;
  return {
    onpointerdown: (event) => {
      if (!movable() || event.button !== 0) return;
      event.currentTarget.setPointerCapture(event.pointerId);
      link.drag = { x: event.clientX, y: event.clientY, id: event.pointerId };
    },
    [MOVE_EVENT]: (event) => {
      const drag = link.drag;
      if (!drag || drag.id !== event.pointerId) return;
      const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
      link.drag = { ...drag, x: event.clientX, y: event.clientY };
      if (dx || dy) link.move(dx, dy, event.shiftKey, event.timeStamp);
    },
    onpointerup: () => { link.drag = null; },
    onpointercancel: () => { link.drag = null; },
    onlostpointercapture: () => { link.drag = null; },
    onwheel: (event) => {
      if (!movable()) return;
      event.preventDefault();
      link.dolly(event.deltaY, event.timeStamp);
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
        title=${why || "Start the renderer and show this recording"} onclick=${() => open(picked)}>
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
    <button type="button" onclick=${(event) => link.step(-1, event.timeStamp)} title="Back one frame (Left arrow)">
      <${Icon} name="stepBack" size=${15} /> Back 1</button>
    <button type="button" class="primary" onclick=${() => link.toggle()} title="Play or pause (Space)">
      <${Icon} name=${link.playing ? "pause" : "play"} size=${16} /> ${link.playing ? "Pause" : "Play"}</button>
    <button type="button" onclick=${(event) => link.step(1, event.timeStamp)} title="Forward one frame (Right arrow)">
      <${Icon} name="stepForward" size=${15} /> Forward 1</button>
    <input type="range" min="0" max=${Math.max(0, frames.length - 1)} value=${link.frame} aria-label="Frame"
      oninput=${(event) => { link.pause(); link.seek(Number(event.currentTarget.value), "input", event.timeStamp); }} />
    <span class="scene-lab-frame">Frame ${link.frame + 1} of ${frames.length}
      <small>${frameWords(frames[link.frame])}</small></span>
  </div>`;
}

function CameraBar({ link }) {
  const available = link.freeAvailable, reason = link.scene.free_camera.reason;
  return html`<div class="scene-lab-bar" aria-label="Camera">
    <button type="button" aria-pressed=${!link.free} onclick=${(event) => link.recorded(event.timeStamp)}
        title="Show what the player saw (R)">Recorded camera</button>
    <button type="button" aria-pressed=${link.free} disabled=${!available}
        title=${available ? "Move the camera yourself" : reason} onclick=${() => link.enterFree()}>Free camera</button>
    <button type="button" disabled=${!available} onclick=${(event) => link.lookAtMario(event.timeStamp)}
        title=${available ? "Point the free camera at Mario (F)" : reason}>Look at Mario</button>
    <label class="scene-lab-quality">Pictures
      <select value=${link.encoding} onchange=${(event) => { link.encoding = event.currentTarget.value; link.want("input"); }}>
        <option value="jpeg">Faster (JPEG)</option>
        <option value="raw">Exact pixels (slower)</option>
      </select></label>
  </div>
  <p class="scene-lab-hint">${available
    ? "Drag to turn around the focus point, Shift-drag to slide, use the wheel to move closer or back. F looks at Mario, R returns to the recorded camera. Left and Right step one frame."
    : html`Only the recorded camera is available: ${reason}`}</p>`;
}

function Readout({ link }) {
  const shown = link.shown, timings = shown?.timings || {};
  if (!shown) return null;
  return html`<p class="scene-lab-readout">
    ${shown.width}x${shown.height} ${shown.encoding === "raw" ? "exact pixels" : "JPEG"},
    ${" "}input to picture ${ms(percentile(link.latencies, 0.5))} (p95 ${ms(percentile(link.latencies, 0.95))}),
    ${" "}server ${ms(shown.server_ms)}: game code ${ms(timings.evaluate_ms)}, renderer ${ms(timings.host_ms)},
    ${" "}encode ${ms(timings.encode_ms)}
  </p>`;
}

function Player({ link, canvas }) {
  return html`<section class="practice-card scene-lab-player">
    <div class="scene-lab-stage">
      <canvas ref=${canvas} width="640" height="480" class=${link.freeAvailable ? "is-movable" : ""}
        aria-label="The recording's picture" ...${pointerHandlers(link)}></canvas>
      <span class=${`scene-lab-tag ${link.free ? "is-free" : ""}`}>${link.free ? "Free camera" : "Recorded camera"}</span>
      ${link.drawing && html`<span class="scene-lab-busy">Drawing...</span>`}
    </div>
    ${link.notice && html`<p class="scene-lab-reason" role="status">${link.notice}</p>`}
    <${Transport} link=${link} />
    <${CameraBar} link=${link} />
    <${Readout} link=${link} />
  </section>`;
}

function useSceneLink(canvas, refresh) {
  const [, setRevision] = useState(0);
  const link = useRef(null);
  if (!link.current) {
    link.current = new SceneLink({ draw: (header, bytes, current) => paint(canvas.current, header, bytes, current),
      changed: () => setRevision((revision) => revision + 1), ended: () => refresh.current() });
  }
  useEffect(() => {
    const scene = link.current, release = () => releaseDrag(scene, canvas.current);
    const hidden = () => { if (document.hidden) scene.pause(); };
    document.addEventListener("visibilitychange", hidden);
    window.addEventListener("blur", release);
    return () => {
      document.removeEventListener("visibilitychange", hidden);
      window.removeEventListener("blur", release);
      // The server frees the renderer once no page has shown the scene for a
      // little while, so a tab switch and back keeps it open.
      scene.disconnect();
    };
  }, []);
  return link.current;
}

export function SceneViewer() {
  const [status, setStatus] = useState(null);
  const [opening, setOpening] = useState(false);
  const canvas = useRef(null), surface = useRef(null), refresh = useRef(null);
  const link = useSceneLink(canvas, refresh);
  refresh.current = async () => {
    try {
      const next = await getJSON(`${ROOT}/status`);
      setStatus(next);
      return next;
    } catch (problem) { link.fail(problem.message); return null; }
  };
  useEffect(() => { refresh.current().then((next) => { if (next?.session) link.connect(next.session); }); }, []);
  async function open(name) {
    setOpening(true);
    link.fail(null);
    try {
      link.connect(await send("POST", `${ROOT}/open`, { package: name }));
      surface.current?.focus();
    } catch (problem) { link.fail(problem.message); }
    setOpening(false);
    refresh.current();
  }
  async function close() {
    link.disconnect();
    link.scene = null;
    link.changed();
    try { await send("POST", `${ROOT}/close`); } catch (problem) { link.fail(problem.message); }
    refresh.current();
  }
  return html`<div class="workshop-page scene-lab" ref=${surface} tabindex="-1" onkeydown=${(event) => onKey(link, event)}>
    <header class="practice-card workshop-hero">
      <div class="workshop-title">
        <span class="workshop-title-icon"><${Icon} name="ghost" size=${22} /></span>
        <div><span class="eyebrow">Lab</span><h2>Scene viewer</h2></div>
      </div>
      ${link.scene ? html`<div class="scene-lab-open">
          <span class="scene-lab-name" title=${link.scene.name}>${link.scene.name}</span>
          <button type="button" onclick=${close} title="Close this recording and stop the renderer">Close</button>
        </div>`
        : html`<${Opener} status=${status} opening=${opening} open=${open} />`}
    </header>
    ${link.error && html`<p class="scene-lab-error" role="status">${link.error}</p>`}
    ${!link.scene && status?.ended && !link.error && html`<p class="scene-lab-reason">${status.ended.message}</p>`}
    ${link.scene && html`<${Player} link=${link} canvas=${canvas} />`}
  </div>`;
}
