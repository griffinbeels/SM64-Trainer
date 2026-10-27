// @vitest-environment jsdom
// The Scene viewer's sound (ui/components/sceneaudio.js and its worklet) and
// the recording's own clock (sceneclock.js): the frame table as runs on the
// recorded-time axis, the clock steering, the worklet's rendering, and the
// controls in the real page against a scripted server. WebAudio, the socket
// and fetch are stand-ins; the rendered samples are the worklet's own
// arithmetic, run here sample by sample.
import { h } from "preact";
import { render, cleanup, fireEvent, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { NOT_SPLIT_HERE, SCENE_SOUND, SceneSound, soundMap, startClock, steerClock, trackFor } from
  "../../src/sm64_events/ui/components/sceneaudio.js";
import { SceneSoundProcessor, sampleAt } from "../../src/sm64_events/ui/components/sceneaudioworklet.js";
import { pictureAt, playLength, recordedTimes } from "../../src/sm64_events/ui/components/sceneclock.js";
import { SceneViewer } from "../../src/sm64_events/ui/components/sceneviewer.js";

const RATE = 48000, FPS = 30, PICTURE = 1000 / FPS, PER_MS = RATE / 1000 * (1 - 100e-6);   // a device 100 ppm slow

// A recording's table: presents ~33 ms apart (+-2 ms), each frame's sample the
// device sample sounding at its present, as the server's stored clock gives it.
function recording(count, seed = 3) {
  let state = seed;
  const random = () => { state = (state * 1103515245 + 12345) % 2147483648; return state / 2147483648; };
  const times = Array.from({ length: count }, (_, k) => k * PICTURE + (random() - 0.5) * 4);
  return { times, samples: times.map((t) => Math.round(9600 + t * PER_MS)) };
}

test("a hold keeps its sound running; a frame without sound or a jump in the stream cuts the run", () => {
  const { times, samples } = recording(300);
  for (let k = 120; k < 300; k++) {                              // picture 119 held 0.8 s (a level load)
    times[k] += 800;
    samples[k] = Math.round(9600 + times[k] * PER_MS);
  }
  for (let k = 200; k < 210; k++) samples[k] = -1;               // no sound for ten pictures
  for (let k = 250; k < 300; k++) samples[k] += 5000;            // a reopened capture: the stream jumps
  const runs = soundMap(samples, times, RATE, FPS);
  expect(runs.map((run) => run.p0)).toEqual([times[0], times[210], times[250]]);
  for (const [k, sample] of samples.entries()) {
    const at = sampleAt(runs, times[k]);
    if (sample < 0) { expect(at).toBe(null); continue; }
    expect(Math.abs(at.s - sample)).toBeLessThan(1e-6);           // each picture's own sample, exactly
  }
  const held = sampleAt(runs, times[119] + 400).s;                 // mid-hold the sound is still playing, at 1x
  expect(Math.abs(held - samples[119] - 400 * PER_MS)).toBeLessThan(1.5);
  expect(sampleAt(runs, times[205])).toBe(null);
  expect(sampleAt(runs, times[199] + PICTURE / 2).s).toBeCloseTo(samples[199] + PICTURE / 2 * RATE / 1000, 6);
});

test("Play keeps the recorded time: a held picture stays up its whole recorded hold", () => {
  const frames = [0, 33, 850, 883, 917].map((recorded_ms) => ({ recorded_ms }));
  const times = recordedTimes({ fps: FPS, frames });
  expect([...times]).toEqual([0, 33, 850, 883, 917]);
  expect([0, 32, 33, 400, 849, 850, 900].map((elapsed) => pictureAt(times, 0, elapsed))).toEqual([0, 0, 1, 1, 1, 2, 3]);
  expect(pictureAt(times, 2, 40)).toBe(3);                          // from the third picture
  expect(pictureAt(times, 1, -100)).toBe(1);                        // before the clock starts: the play's first
  expect(playLength(times, 0, FPS)).toBeCloseTo(917 + PICTURE, 6);
  frames[2] = {};                                                   // a package without the times: game frames
  expect([...recordedTimes({ fps: FPS, frames })]).toEqual([0, 1, 2, 3, 4].map((k) => k * PICTURE));
});

test("the clock starts where the recorded time reaches the play's first picture, leans at most 1% and jumps a big miss", () => {
  const nominal = 1000 / RATE;                                        // ms of recorded time per sample frame
  const first = startClock(990, 1000, nominal, 1000);                 // its first picture is heard 10 ms from now
  expect(first.f0).toBe(1480);
  expect(first.p0).toBeCloseTo(1000, 6);
  expect(startClock(1004, 1000, nominal, 1000)).toMatchObject({ f0: 1000, p0: 1004, jump: true });
  const plan = { f0: 0, p0: 0, slope: nominal };
  const at = (seconds) => seconds * RATE;
  expect(steerClock(plan, 1001.7, at(1), nominal)).toBe(null);        // 1.7 ms: inside the dead band
  const ahead = steerClock(plan, 1010, at(1), nominal);              // 10 ms behind the pictures
  expect(ahead.jump).toBe(false);
  expect(ahead.p0).toBeCloseTo(1000, 6);
  expect(ahead.slope / nominal - 1).toBeCloseTo(0.01, 6);
  expect(steerClock(plan, 995, at(1), nominal).slope / nominal - 1).toBeCloseTo(-0.005, 6);
  expect(steerClock(plan, 1100, at(1), nominal)).toMatchObject({ p0: 1100, slope: nominal, jump: true });
  expect(steerClock({ ...plan, f0: at(2) }, 50, at(1), nominal)).toBe(null);   // not started yet
  expect(steerClock({ ...plan, slope: nominal * 1.01 }, 1010, at(1), nominal).slope).toBe(nominal);
});

function processor(blockSamples = 4800) {
  const worklet = new SceneSoundProcessor();
  const ramp = (index) => Float32Array.from({ length: blockSamples }, (_, i) => (index * blockSamples + i) / 1e6);
  const samples = [0, 1600, 3200, 4800, 6400, 8000], times = samples.map((_, k) => k * PICTURE);
  worklet.take({ type: "map", runs: soundMap(samples, times, RATE, FPS), blockSamples });
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

const MS_PER_FRAME = 1000 / RATE;

test("the worklet plays the sample its recorded time maps to, fading in, and Stop is silent within 64 frames", () => {
  const worklet = processor();
  worklet.take({ type: "clock", piece: { f0: 256, p0: PICTURE, slope: MS_PER_FRAME, jump: true } });
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
  worklet.take({ type: "clock", piece: { f0: 0, p0: 0, slope: MS_PER_FRAME, jump: true } });
  quanta(worklet, 0, 4);
  worklet.take({ type: "clock", piece: { f0: 512, p0: 512 * MS_PER_FRAME, slope: MS_PER_FRAME * 1.01, jump: false } });
  const lean = quanta(worklet, 512, 1).left;
  expect(lean[0]).toBeCloseTo(512 / 1e6, 6);                               // same sample: no seam
  expect(lean[100]).toBeCloseTo((512 + 100 * 1.01) / 1e6, 6);
  worklet.take({ type: "clock", piece: { f0: 640, p0: 3 * PICTURE, slope: MS_PER_FRAME, jump: true } });
  const jump = quanta(worklet, 640, 2).left;
  expect(Math.abs(jump[63])).toBeLessThan(Math.abs(jump[0]) / 32);         // the old sound fades out
  expect(jump[130]).toBeCloseTo((4800 + 130) / 1e6, 6);                    // then the new one, on its own clock
  expect(jump[66]).toBeLessThan(jump[130] / 10);                           // after fading in
  worklet.take({ type: "keep", indices: [0] });                             // block 1 (samples 4800 on) is gone
  expect(quanta(worklet, 896, 4).left.every((value) => value === 0)).toBe(true);
  expect(worklet.starved).toBeGreaterThan(0);
});

test("the worklet keeps a playing clock and its blocks when a growing scene's map extends", () => {
  const worklet = processor();
  worklet.take({ type: "clock", piece: { f0: 0, p0: 0, slope: MS_PER_FRAME, jump: true } });
  quanta(worklet, 0, 4);
  const samples = [0, 1600, 3200, 4800, 6400, 8000, 9600], times = samples.map((_, k) => k * PICTURE);
  worklet.take({ type: "map", runs: soundMap(samples, times, RATE, FPS), blockSamples: 4800, extend: true });
  const after = quanta(worklet, 512, 1).left;
  expect(after[10]).toBeCloseTo((512 + 10) / 1e6, 6);                        // the same sound, on the same clock
  expect(worklet.starved).toBe(0);
});

test("a track switch fades out, swaps and fades in on the same clock; no track is silence", () => {
  const worklet = processor();
  const music = (index) => Float32Array.from({ length: 4800 }, (_, i) => 0.5 + (index * 4800 + i) / 1e6);
  worklet.take({ type: "block", track: "music", index: 0, channels: [music(0), music(0)] });
  worklet.take({ type: "block", track: "music", index: 1, channels: [music(1), music(1)] });
  worklet.take({ type: "clock", piece: { f0: 0, p0: 0, slope: MS_PER_FRAME, jump: true } });
  quanta(worklet, 0, 4);
  worklet.take({ type: "track", track: "music" });
  const swap = quanta(worklet, 512, 2).left;
  expect(swap[0]).toBeCloseTo(512 / 1e6 * 63 / 64, 6);                        // the recording, fading out
  expect(Math.abs(swap[63])).toBeLessThan(1e-4);
  expect(swap[200]).toBeCloseTo(0.5 + (512 + 200) / 1e6, 6);                  // then the music at the same sample
  worklet.take({ type: "track", track: null });
  const quiet = quanta(worklet, 768, 2).left;
  expect(quiet.slice(64).every((value) => value === 0)).toBe(true);
  const starved = worklet.starved;
  quanta(worklet, 1024, 4);
  expect(worklet.starved).toBe(starved);                                       // silence is not a missing block
  worklet.take({ type: "track", track: "mix" });
  expect(quanta(worklet, 1536, 2).left[200]).toBeCloseTo((1536 + 200) / 1e6, 6);
});

test("each ghost's own sound mixes under the stage on the same samples, a mute fades it out, and stage silence keeps it", () => {
  const worklet = processor();
  const ghost = (index, level) => Float32Array.from({ length: 4800 }, () => level);
  for (const index of [0, 1]) {
    worklet.take({ type: "block", track: "ghost:a", index, channels: [ghost(index, 0.25), ghost(index, 0.25)] });
    worklet.take({ type: "block", track: "ghost:b", index, channels: [ghost(index, 0.125), ghost(index, -0.125)] });
  }
  worklet.take({ type: "ghosts", tracks: [{ track: "ghost:a", gain: 1 }, { track: "ghost:b", gain: 1 }] });
  worklet.take({ type: "clock", piece: { f0: 0, p0: 0, slope: MS_PER_FRAME, jump: true } });
  const both = quanta(worklet, 0, 4);
  expect(both.left[300]).toBeCloseTo(300 / 1e6 + 0.375, 6);                    // the stage + both ghosts
  expect(both.right[300]).toBeCloseTo(-300 / 1e6 + 0.125, 6);
  worklet.take({ type: "ghosts", tracks: [{ track: "ghost:a", gain: 0 }, { track: "ghost:b", gain: 1 }] });
  const muted = quanta(worklet, 512, 2).left;
  expect(muted[0]).toBeGreaterThan(512 / 1e6 + 0.125 + 0.2);                    // a mute moves over 64 frames, no click
  expect(muted[100]).toBeCloseTo((512 + 100) / 1e6 + 0.125, 6);
  worklet.take({ type: "track", track: null });                                 // Music and Sound effects both off
  expect(quanta(worklet, 768, 2).left[200]).toBeCloseTo(0.125, 6);             // the ghost still plays
  worklet.take({ type: "ghosts", tracks: [] });                                 // removed: its blocks go too
  expect(worklet.tracks.has("ghost:b")).toBe(false);
});

test("a ghost in the scene fetches its own track on the recording's blocks, and Mute silences only it", () => {
  const sound = new SceneSound();
  const posted = [];
  sound.post = (message) => posted.push(message);
  const asked = [];
  globalThis.fetch = vi.fn((url) => { asked.push(url); return new Promise(() => {}); });
  const { times, samples } = recording(20);
  sound.load({ session: 4, fps: FPS, frames: times.map((t) => ({ recorded_ms: t })), audio: { available: true,
    rate: RATE, block_samples: 9216, total_samples: 4 * 9216, blocks: 4, codec: "stored", shift_ms: 0, samples, lost: [] } });
  sound.setGhosts(["attempt:7", "community:x y"]);
  expect(posted.at(-1)).toEqual({ type: "ghosts", tracks: [{ track: "ghost:attempt:7", gain: 1 },
    { track: "ghost:community:x y", gain: 1 }] });
  // one block each until the first comes back: that request makes the ghost's whole track on the server
  expect(asked.filter((url) => url.includes("track=ghost%3Aattempt%3A7")).length).toBe(1);
  expect(asked.some((url) => url.includes("track=ghost%3Acommunity%3Ax%20y"))).toBe(true);
  sound.toggleGhost("attempt:7");
  expect(sound.ghostMuted("attempt:7")).toBe(true);
  expect(posted.at(-1).tracks).toEqual([{ track: "ghost:attempt:7", gain: 0 }, { track: "ghost:community:x y", gain: 1 }]);
  sound.setGhosts(["attempt:7"]);                                               // removed: no track for it
  expect(posted.at(-1).tracks).toEqual([{ track: "ghost:attempt:7", gain: 0 }]);  // a mute stays with its ghost
});

test("the switches choose the track: the recording with both on, one track with one, nothing with neither", () => {
  expect(trackFor(true, true, true)).toBe("mix");
  expect(trackFor(true, true, false)).toBe("music");
  expect(trackFor(true, false, true)).toBe("effects");
  expect(trackFor(true, false, false)).toBe(null);
  expect(trackFor(false, false, false)).toBe("mix");                          // no split: the switches do nothing
});

// ---------------------------------------------------------------- the page

const FRAMES = Array.from({ length: 60 }, (_, i) => ({ level: 9, area: 1, game_frame: 1000 + i }));
FRAMES.forEach((frame, i) => { frame.recorded_ms = i * PICTURE; });
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

// A running audio device: its clock runs with the page's, renders 10 ms ahead
// and is heard 50 ms after rendering.
class FakeContext {
  constructor(options) {
    Object.assign(this, { options, sampleRate: options.sampleRate, state: "running", destination: {},
      born: performance.now(), audioWorklet: { addModule: vi.fn(() => Promise.resolve()) } });
    contexts.push(this);
  }
  get currentTime() { return (performance.now() - this.born) / 1000 + 1; }
  createGain() { return { gain: { value: 1, setTargetAtTime(value) { this.value = value; } }, connect: vi.fn() }; }
  resume() { this.state = "running"; return Promise.resolve(); }
  suspend() { this.state = "suspended"; return Promise.resolve(); }
  close() { this.state = "closed"; return Promise.resolve(); }
  getOutputTimestamp() { return { contextTime: this.currentTime - 0.06, performanceTime: performance.now() - 0.01 }; }
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
  expect(JSON.parse(localStorage.getItem(SCENE_SOUND.preference))).toEqual({ volume: 0.25, muted: true, music: true,
    effects: true });
  fireEvent.input(volume, { target: { value: "0.6" } });
  await waitFor(() => expect(mute.getAttribute("aria-pressed")).toBe("false"));
  expect(JSON.parse(localStorage.getItem(SCENE_SOUND.preference))).toEqual({ volume: 0.6, muted: false, music: true,
    effects: true });
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

test("Play starts the sound inside the click, on schedule from the play's first picture, and Pause stops it", async () => {
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
  // No picture of the play has been drawn: the sound is already scheduled, from frame 0's own time.
  await waitFor(() => expect(node.messages.some((message) => message.type === "clock")).toBe(true));
  const piece = node.messages.find((message) => message.type === "clock").piece;
  expect(piece.jump).toBe(true);
  expect(Math.abs(piece.p0)).toBeLessThan(MS_PER_FRAME);              // frame 0's own time, to a sample
  expect(piece.f0).toBeGreaterThan(contexts[0].currentTime * RATE);  // in the future: nothing of it is cut
  fireEvent.click(page.getByRole("button", { name: "Pause" }));
  expect(node.messages.at(-1)).toEqual({ type: "stop" });
});

test("the first picture of a play holds until the sound can start with it", async () => {
  const sound = new SceneSound();
  sound.load(opened);
  expect(sound.lead()).toBe(0);                                      // no audio device yet: nothing to wait for
  sound.arm();
  expect(sound.lead()).toBe(SCENE_SOUND.coldLeadMs);                 // a starting device has no clock to read
  for (let i = 0; i < 3; i++) sound.measureOutput();
  // Heard 40 ms of steering lead + 60 ms of output delay + a 5 ms margin from now. SceneLink asks
  // for a play's pictures ahead and draws each on the display frame it is due: half a frame late on
  // average, until a play has measured its own.
  expect(sound.lead()).toBeCloseTo(105 - SCENE_SOUND.tickMs / 2, 0);
  sound.shown(0);                                                    // paused: not a play's picture
  expect(sound.lead()).toBeCloseTo(105 - SCENE_SOUND.tickMs / 2, 0);
  sound.lastLate = 12;                                               // the last play's own lateness
  expect(sound.lead()).toBeCloseTo(105 - 12, 0);
  sound.close();
});

test("a growing scene's sound extends in place: its map grows and only a block that was short is fetched again", () => {
  const sound = new SceneSound();
  const posted = [];
  sound.post = (message) => posted.push(message);
  const { times, samples } = recording(40);
  const frames = (count) => times.slice(0, count).map((t) => ({ recorded_ms: t }));
  const table = (count, total) => ({ available: true, rate: RATE, block_samples: 9216, total_samples: total,
    blocks: Math.ceil(total / 9216), codec: "stored", shift_ms: 0, samples: samples.slice(0, count), lost: [] });
  const scene = { session: 1, fps: FPS, frames: frames(20), audio: table(20, 2 * 9216 + 100) };
  sound.load(scene);
  sound.decoded.set(0, []).set(1, []).set(2, []);                  // block 2 held only 100 samples
  const before = sound.table;
  Object.assign(scene, { frames: frames(40), audio: table(40, 4 * 9216) });
  sound.grew(scene);
  expect(sound.table).toBe(before);                                // fetches in flight still count
  expect(sound.table.samples.length).toBe(40);
  expect(sound.table.blocks).toBe(4);
  expect([...sound.decoded.keys()]).toEqual([0, 1]);               // the short block goes, the whole ones stay
  expect(posted.at(-1)).toMatchObject({ type: "map" });
  expect(sound.runs.at(-1).p1).toBeGreaterThan(times[38]);
  const quiet = new SceneSound();
  quiet.load({ session: 2, fps: FPS, frames: frames(20), audio: { available: false, reason: "No sound." } });
  quiet.playing = { from: 0 };                                     // sound never switches on in a play
  quiet.grew({ session: 2, fps: FPS, frames: frames(40), audio: table(40, 4 * 9216) });
  expect(quiet.available).toBe(false);
  quiet.playing = null;
  quiet.grew({ session: 2, fps: FPS, frames: frames(40), audio: table(40, 4 * 9216) });
  expect(quiet.available).toBe(true);                              // at the next one it does
});

test("a step or a seek is silent, and a lost or sound-less picture says so", async () => {
  const { page, socket, root } = await openScene();
  await socket.picture(socket.sent[0]);
  fireEvent.click(page.getByRole("button", { name: "Play" }));
  await waitFor(() => expect(nodes.length).toBe(1));
  fireEvent.keyDown(root, { key: "ArrowRight" });
  const stopped = nodes[0].messages.length;
  expect(nodes[0].messages.at(-1)).toEqual({ type: "stop" });
  fireEvent.input(page.getByLabelText("Frame"), { target: { value: "2" } });
  await waitFor(() => expect(page.container.textContent).toContain("Sound lost here"));
  fireEvent.input(page.getByLabelText("Frame"), { target: { value: "4" } });
  await waitFor(() => expect(page.container.textContent).toContain("No sound was recorded for this picture."));
  expect(nodes[0].messages.slice(stopped).filter((message) => message.type === "clock")).toEqual([]);
});

test("a scene without the split shows the switches disabled and says why, once", async () => {
  opened.audio.stems = { available: false, reason: "This scene was saved before music and sound effects could be split." };
  const { page, socket } = await openScene();
  await socket.picture(socket.sent[0]);
  const music = page.getByRole("button", { name: "Music" }), effects = page.getByRole("button", { name: "Sound effects" });
  expect(music.disabled && effects.disabled).toBe(true);
  expect(music.getAttribute("aria-pressed")).toBe("true");
  expect(page.container.querySelectorAll(".scene-lab-sound-note").length).toBe(1);
  expect(page.container.textContent).toContain("saved before music and sound effects could be split");
});

test("a refused split says the plain sentence; the stems layer's reason and numbers go to the log note", () => {
  const notes = [];
  const sound = new SceneSound({ observe: (note) => notes.push(note) });
  sound.post = () => {};
  const { times, samples } = recording(20);
  const plain = "This scene's music and sound effects can't be separated.";
  const detail = "The stem renderer's sound differs from what Project64 played by more than rounding (G1).";
  sound.load({ name: "take-1659-5", session: 3, fps: FPS, frames: times.map((t) => ({ recorded_ms: t })),
    audio: { available: true, rate: RATE, block_samples: 9216, total_samples: 9216, blocks: 1, codec: "stored",
      shift_ms: 0, samples, lost: [], stems: { available: false, reason: plain, detail, stats: { g1_snr_db: 4.25 } } } });
  expect(sound.splitReason).toBe(plain);
  expect(notes).toEqual([{ surface: "scene_viewer", kind: "sound_not_split", scene: "take-1659-5", reason: plain,
    detail, stats: { g1_snr_db: 4.25 } }]);
});

test("Music off plays the effects track from where the recording was, both off is silence, and the choice persists", async () => {
  opened.audio.stems = { available: true, tracks: ["music", "effects"], lost: [6] };
  const { page, socket } = await openScene();
  await socket.picture(socket.sent[0]);
  await waitFor(() => expect(fetches.includes("/api/scene-lab/audio/1?session=3")).toBe(true));
  expect(fetches.some((url) => url.includes("track="))).toBe(false);          // both on: the recording only
  fireEvent.click(page.getByRole("button", { name: "Play" }));
  await waitFor(() => expect(nodes.length).toBe(1));
  const node = nodes[0];
  await waitFor(() => expect(node.messages.some((message) => message.type === "clock")).toBe(true));
  const music = page.getByRole("button", { name: "Music" });
  fireEvent.click(music);
  await waitFor(() => expect(music.getAttribute("aria-pressed")).toBe("false"));
  await waitFor(() => expect(fetches.includes("/api/scene-lab/audio/1?session=3&track=effects")).toBe(true));
  await waitFor(() => expect(node.messages.some((message) => message.type === "track" && message.track === "effects"))
    .toBe(true));
  const swap = node.messages.findIndex((message) => message.type === "track" && message.track === "effects");
  const block = node.messages.findIndex((message) => message.type === "block" && message.track === "effects");
  expect(block).toBeGreaterThanOrEqual(0);
  expect(block).toBeLessThan(swap);                                          // its block first: no hole in the sound
  expect(JSON.parse(localStorage.getItem(SCENE_SOUND.preference))).toMatchObject({ music: false, effects: true });
  fireEvent.click(page.getByRole("button", { name: "Sound effects" }));
  await waitFor(() => expect(node.messages.at(-1)).toEqual({ type: "track", track: null }));
  fireEvent.click(page.getByRole("button", { name: "Pause" }));
  fireEvent.click(page.getByRole("button", { name: "Sound effects" }));        // effects alone again, paused
  fireEvent.input(page.getByLabelText("Frame"), { target: { value: "6" } });
  await waitFor(() => expect(page.container.textContent).toContain(NOT_SPLIT_HERE));
});

test("a browser without WebAudio says it cannot play the sound", async () => {
  delete globalThis.AudioWorkletNode;
  const { page, socket } = await openScene();
  await socket.picture(socket.sent[0]);
  fireEvent.click(page.getByRole("button", { name: "Play" }));
  await waitFor(() => expect(page.container.textContent).toContain("This browser cannot play the recording's sound."));
});
