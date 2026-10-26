// @vitest-environment jsdom
// The Scene viewer's sound (ui/components/sceneaudio.js and its worklet):
// the frame table smoothed into runs, the clock steering, the worklet's
// rendering, and the controls in the real page against a scripted server.
// WebAudio, the socket and fetch are stand-ins; the rendered samples are the
// worklet's own arithmetic, run here sample by sample.
import { h } from "preact";
import { render, cleanup, fireEvent, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { SCENE_SOUND, soundMap, startClock, steerClock } from "../../src/sm64_events/ui/components/sceneaudio.js";
import { SceneSoundProcessor, sampleAt } from "../../src/sm64_events/ui/components/sceneaudioworklet.js";
import { SceneViewer } from "../../src/sm64_events/ui/components/sceneviewer.js";

const RATE = 48000, FPS = 30, NOMINAL = RATE / FPS;

function jittered(count, seed = 3) {         // presents ~33 ms apart, +-2 ms, like a real tap's
  let state = seed, sample = 9600;
  const random = () => { state = (state * 1103515245 + 12345) % 2147483648; return state / 2147483648; };
  return Array.from({ length: count }, () => {
    const at = Math.round(sample + (random() - 0.5) * 192);
    sample += NOMINAL;
    return at;
  });
}

test("the frame table becomes runs cut at holds and silence, each frame within 5 ms of its own sample", () => {
  const samples = jittered(300);
  for (let k = 120; k < 300; k++) samples[k] += 0.8 * RATE;      // a picture held 0.8 s (a level load) at 120
  for (let k = 200; k < 210; k++) samples[k] = -1;               // no sound for ten pictures
  const runs = soundMap(samples, RATE, FPS);
  expect(runs.map((run) => [run.p0, run.p1])).toEqual([[0, 120], [120, 200], [210, 300]]);
  for (const [k, sample] of samples.entries()) {
    const at = sampleAt(runs, k);
    if (sample < 0) { expect(at).toBe(null); continue; }
    expect(Math.abs(at.s - sample) / RATE * 1000).toBeLessThan(5);
  }
  for (const { kp, ks } of runs) {                                // the sound plays near 1x: no warble
    for (let j = 1; j < kp.length; j++) expect(Math.abs((ks[j] - ks[j - 1]) / (kp[j] - kp[j - 1]) / NOMINAL - 1)).toBeLessThan(0.01);
  }
  expect(Math.abs(sampleAt(runs, 119.5).s - samples[119] - NOMINAL / 2)).toBeLessThan(48);   // the last picture plays one
  expect(sampleAt(runs, 205)).toBe(null);
});

test("the clock starts where the pictures reach the play's frame, leans at most 1% and jumps a big miss", () => {
  const nominal = FPS / RATE;
  const first = startClock(9.5, 1000, nominal, 10);              // frame 10 is heard 0.5 picture from now
  expect(first.f0).toBe(1800);
  expect(first.p0).toBeCloseTo(10, 6);
  expect(startClock(10.2, 1000, nominal, 10)).toMatchObject({ f0: 1000, p0: 10.2, jump: true });
  const plan = { f0: 0, p0: 0, slope: nominal };
  const at = (seconds) => seconds * RATE;
  expect(steerClock(plan, 30 + 0.05, at(1), nominal, FPS)).toBe(null);          // 1.7 ms: inside the dead band
  const ahead = steerClock(plan, 30.3, at(1), nominal, FPS);                     // 10 ms behind the pictures
  expect(ahead.jump).toBe(false);
  expect(ahead.p0).toBeCloseTo(30, 6);
  expect(ahead.slope / nominal - 1).toBeCloseTo(0.01, 6);
  expect(steerClock(plan, 29.85, at(1), nominal, FPS).slope / nominal - 1).toBeCloseTo(-0.005, 6);
  expect(steerClock(plan, 33, at(1), nominal, FPS)).toMatchObject({ p0: 33, slope: nominal, jump: true });
  expect(steerClock({ ...plan, f0: at(2) }, 50, at(1), nominal, FPS)).toBe(null);   // not started yet
  expect(steerClock({ ...plan, slope: nominal * 1.01 }, 30.3 * 1.0, at(1), nominal, FPS).slope).toBe(nominal);
});

function processor(blockSamples = 4800) {
  const worklet = new SceneSoundProcessor();
  const ramp = (index) => Float32Array.from({ length: blockSamples }, (_, i) => (index * blockSamples + i) / 1e6);
  worklet.take({ type: "map", runs: soundMap([0, 1600, 3200, 4800, 6400, 8000], RATE, FPS), blockSamples });
  worklet.take({ type: "block", index: 0, channels: [ramp(0), ramp(0).map((value) => -value)] });
  worklet.take({ type: "block", index: 1, channels: [ramp(1), ramp(1).map((value) => -value)] });
  return worklet;
}

function quanta(worklet, from, count) {
  const left = [], right = [];
  for (let q = 0; q < count; q++) {
    const out = [new Float32Array(128), new Float32Array(128)];
    worklet.render(out, from + q * 128);
    left.push(...out[0]);
    right.push(...out[1]);
  }
  return { left, right };
}

test("the worklet plays the sample its picture position maps to, fading in, and Stop is silent within 64 frames", () => {
  const worklet = processor();
  worklet.take({ type: "clock", piece: { f0: 256, p0: 1, slope: FPS / RATE, jump: true } });
  const { left, right } = quanta(worklet, 0, 40);
  expect(left.slice(0, 256).every((value) => value === 0)).toBe(true);    // nothing before the clock starts
  expect(left[256]).toBeCloseTo(1600 / 1e6 / 64, 9);                      // fades in over 64 frames
  for (const f of [400, 1000, 4000]) {                                     // then frame f plays sample 1600 + f - 256
    expect(left[f]).toBeCloseTo((1600 + f - 256) / 1e6, 6);
    expect(right[f]).toBeCloseTo(-(1600 + f - 256) / 1e6, 6);
  }
  worklet.take({ type: "stop" });
  const after = quanta(worklet, 40 * 128, 2).left;
  expect(Math.abs(after[0])).toBeGreaterThan(0);
  expect(after.slice(64).every((value) => value === 0)).toBe(true);
});

test("the worklet steers without a seam, fades across a jump, and a missing block is silence", () => {
  const worklet = processor();
  worklet.take({ type: "clock", piece: { f0: 0, p0: 0, slope: FPS / RATE, jump: true } });
  quanta(worklet, 0, 4);
  worklet.take({ type: "clock", piece: { f0: 512, p0: 512 * FPS / RATE, slope: FPS / RATE * 1.01, jump: false } });
  const lean = quanta(worklet, 512, 1).left;
  expect(lean[0]).toBeCloseTo(512 / 1e6, 6);                               // same sample: no seam
  expect(lean[100]).toBeCloseTo((512 + 100 * 1.01) / 1e6, 6);
  worklet.take({ type: "clock", piece: { f0: 640, p0: 3, slope: FPS / RATE, jump: true } });
  const jump = quanta(worklet, 640, 2).left;
  expect(Math.abs(jump[63])).toBeLessThan(Math.abs(jump[0]) / 32);         // the old sound fades out
  expect(jump[130]).toBeCloseTo((4800 + 130) / 1e6, 6);                    // then the new one, on its own clock
  expect(jump[66]).toBeLessThan(jump[130] / 10);                           // after fading in
  worklet.take({ type: "keep", indices: [0] });                             // block 1 (samples 4800 on) is gone
  expect(quanta(worklet, 896, 4).left.every((value) => value === 0)).toBe(true);
  expect(worklet.starved).toBeGreaterThan(0);
});

// ---------------------------------------------------------------- the page

const FRAMES = Array.from({ length: 60 }, (_, i) => ({ level: 9, area: 1, game_frame: 1000 + i }));
const SAMPLES = FRAMES.map((_, i) => (i === 4 ? -1 : 9600 + 1600 * i));
let sockets, fetches, contexts, nodes, opened;

class FakeSocket {
  static OPEN = 1;
  constructor(url) { this.url = url; this.sent = []; this.readyState = 0; sockets.push(this); }
  send(text) { this.sent.push(JSON.parse(text)); }
  close() { this.readyState = 3; }
  open() { this.readyState = 1; this.onopen?.(); }
  picture(request) {
    const header = new TextEncoder().encode(JSON.stringify({ id: request.id, session: request.session,
      frame: request.frame, width: 2, height: 1, encoding: "raw", camera: "recorded", reason: null, timings: {} }));
    const buffer = new ArrayBuffer(4 + header.length + 6);
    new DataView(buffer).setUint32(0, header.length, true);
    new Uint8Array(buffer, 4).set(header);
    return this.onmessage({ data: buffer });
  }
}

class FakeContext {
  constructor(options) {
    Object.assign(this, { options, sampleRate: options.sampleRate, state: "running", currentTime: 1, destination: {},
      audioWorklet: { addModule: vi.fn(() => Promise.resolve()) } });
    contexts.push(this);
  }
  createGain() { return { gain: { value: 1, setTargetAtTime(value) { this.value = value; } }, connect: vi.fn() }; }
  resume() { this.state = "running"; return Promise.resolve(); }
  suspend() { this.state = "suspended"; return Promise.resolve(); }
  close() { this.state = "closed"; return Promise.resolve(); }
  getOutputTimestamp() { return { contextTime: 0.1, performanceTime: 100 }; }
}

class FakeNode {
  constructor(context, name) {
    Object.assign(this, { context, name, messages: [] });
    this.port = { postMessage: (message) => this.messages.push(message) };
    nodes.push(this);
  }
  connect() {}
}

function respond(body, status = 200) {
  return Promise.resolve({ ok: status < 300, status, json: () => Promise.resolve(body),
    arrayBuffer: () => Promise.resolve(new ArrayBuffer(8)) });
}

beforeEach(() => {
  sockets = []; fetches = []; contexts = []; nodes = [];
  localStorage.clear();
  opened = { session: 3, name: "sound.sm64scene", fps: 30, frames: FRAMES, renderer_pid: 7,
    free_camera: { available: false, reason: "No scene state." },
    audio: { available: true, rate: RATE, block_samples: 9216, total_samples: 13 * 9216, blocks: 13, codec: "flac",
      shift_ms: 0, samples: SAMPLES, lost: [2] } };
  globalThis.WebSocket = FakeSocket;
  globalThis.AudioContext = FakeContext;
  globalThis.AudioWorkletNode = FakeNode;
  globalThis.OfflineAudioContext = class {
    decodeAudioData() {
      return Promise.resolve({ length: 9216, numberOfChannels: 2, getChannelData: () => new Float32Array(9216) });
    }
  };
  globalThis.fetch = vi.fn((url) => {
    fetches.push(url);
    if (url === "/api/local-access") return respond({}, 404);
    if (url === "/api/scene-lab/status") return respond({ ready: true, problems: [], hold: null, session: null,
      ended: null, packages: [{ name: "sound.sm64scene", bytes: 1 << 20 }] });
    if (url === "/api/scene-lab/open") return respond(opened);
    if (url.startsWith("/api/scene-lab/audio/")) return respond({});
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
  sockets[0].open();
  await waitFor(() => expect(sockets[0].sent.length).toBe(1));
  return { page, socket: sockets[0], root: page.container.querySelector(".scene-lab") };
}

test("Mute and M silence the sound, the volume unmutes, and both persist for this viewer", async () => {
  localStorage.setItem(SCENE_SOUND.preference, JSON.stringify({ volume: 0.25, muted: false }));
  const { page, root } = await openScene();
  const mute = page.getByRole("button", { name: "Mute" }), volume = page.getByLabelText("Volume");
  expect(volume.value).toBe("0.25");
  expect(mute.getAttribute("aria-pressed")).toBe("false");
  fireEvent.keyDown(root, { key: "m" });
  await waitFor(() => expect(mute.getAttribute("aria-pressed")).toBe("true"));
  expect(JSON.parse(localStorage.getItem(SCENE_SOUND.preference))).toEqual({ volume: 0.25, muted: true });
  fireEvent.input(volume, { target: { value: "0.6" } });
  await waitFor(() => expect(mute.getAttribute("aria-pressed")).toBe("false"));
  expect(JSON.parse(localStorage.getItem(SCENE_SOUND.preference))).toEqual({ volume: 0.6, muted: false });
  fireEvent.click(mute);
  await waitFor(() => expect(mute.getAttribute("aria-pressed")).toBe("true"));
});

test("storage that throws leaves the controls working for this page", async () => {
  const getItem = Storage.prototype.getItem, setItem = Storage.prototype.setItem;
  Storage.prototype.getItem = () => { throw new Error("denied"); };
  Storage.prototype.setItem = () => { throw new Error("denied"); };
  try {
    const { page } = await openScene();
    const mute = page.getByRole("button", { name: "Mute" });
    expect(page.getByLabelText("Volume").value).toBe(String(SCENE_SOUND.volume));
    fireEvent.click(mute);
    await waitFor(() => expect(mute.getAttribute("aria-pressed")).toBe("true"));
  } finally {
    Object.assign(Storage.prototype, { getItem, setItem });
  }
});

test("a recording without sound says so instead of showing dead controls", async () => {
  opened.audio = { available: false, reason: "This recording has no sound." };
  const { page } = await openScene();
  expect(page.container.querySelector(".scene-lab-sound-none").textContent).toBe("This recording has no sound.");
  expect(page.queryByRole("button", { name: "Mute" })).toBe(null);
  expect(page.queryByLabelText("Volume")).toBe(null);
});

test("Play starts the sound inside the click, a play's picture starts its clock, and Pause stops it", async () => {
  const { page, socket } = await openScene();
  await socket.picture(socket.sent[0]);
  // Paused on frame 0 (sample 9600, in block 1), its sound is fetched before Play.
  await waitFor(() => expect(fetches.includes("/api/scene-lab/audio/1?session=3")).toBe(true));
  expect(fetches.includes("/api/scene-lab/audio/0?session=3")).toBe(false);
  expect(contexts.length).toBe(0);                                  // nothing plays before Play
  fireEvent.click(page.getByRole("button", { name: "Play" }));
  expect(contexts.length).toBe(1);                                   // made in the click itself
  expect(contexts[0].options.sampleRate).toBe(RATE);
  await waitFor(() => expect(nodes.length).toBe(1));
  const node = nodes[0];
  expect(node.messages[0]).toMatchObject({ type: "map", blockSamples: 9216 });
  await waitFor(() => expect(node.messages.some((message) => message.type === "block" && message.index === 1)).toBe(true));
  await waitFor(() => expect(socket.sent.length).toBe(2));
  await socket.picture(socket.sent[1]);                                // the play's first picture is drawn
  await waitFor(() => expect(node.messages.some((message) => message.type === "clock")).toBe(true));
  expect(node.messages.find((message) => message.type === "clock").piece).toMatchObject({ jump: true });
  fireEvent.click(page.getByRole("button", { name: "Pause" }));
  expect(node.messages.at(-1)).toEqual({ type: "stop" });
});

test("a step or a seek is silent, and a lost or sound-less picture says so", async () => {
  const { page, socket, root } = await openScene();
  await socket.picture(socket.sent[0]);
  fireEvent.click(page.getByRole("button", { name: "Play" }));
  await waitFor(() => expect(nodes.length).toBe(1));
  fireEvent.keyDown(root, { key: "ArrowRight" });
  expect(nodes[0].messages.at(-1)).toEqual({ type: "stop" });
  fireEvent.input(page.getByLabelText("Frame"), { target: { value: "2" } });
  await waitFor(() => expect(page.container.textContent).toContain("Sound lost here"));
  fireEvent.input(page.getByLabelText("Frame"), { target: { value: "4" } });
  await waitFor(() => expect(page.container.textContent).toContain("No sound was recorded for this picture."));
  expect(nodes[0].messages.filter((message) => message.type === "clock")).toEqual([]);
});

test("a browser without WebAudio says it cannot play the sound", async () => {
  delete globalThis.AudioWorkletNode;
  const { page, socket } = await openScene();
  await socket.picture(socket.sent[0]);
  fireEvent.click(page.getByRole("button", { name: "Play" }));
  await waitFor(() => expect(page.container.textContent).toContain("This browser cannot play the recording's sound."));
});
