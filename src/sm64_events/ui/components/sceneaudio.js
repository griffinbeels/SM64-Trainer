// src/sm64_events/ui/components/sceneaudio.js -- the Scene viewer's sound (M4 E4-audio, lab only).
//
// Plays the sound the runner heard -- the package's source-audio layer --
// with the pictures during Play. Pictures are the truth: SceneLink's picture
// clock says which frame is due, and the sound follows it. The server maps
// every frame to the sample sounding as its picture appeared
// (tools/scene_lab/scene_audio.py); here soundMap smooths that table into
// runs, the sound starts when the play's first picture is drawn, and steering
// keeps the sample heard as each picture appears at that picture's own,
// leaning the sound's rate by at most SCENE_SOUND.bend (a big miss jumps).
// Stepping, seeking and Pause are silent at once, and a lost stretch plays as
// silence and says so. sceneaudioworklet.js renders it on the audio thread.
// Mute (M) and the volume persist per viewer. The recording is one mixed
// stream, so there is one stem; per-stem volume (music, effects) would add a
// gain per stem between the worklet and the master gain.
import { h } from "preact";
import { useEffect } from "preact/hooks";
import htm from "htm";
import { Icon } from "./icons.js";
import { sampleAt } from "./sceneaudioworklet.js";

const html = htm.bind(h);
const ROOT = "/api/scene-lab";
const WORKLET = new URL("./sceneaudioworklet.js", import.meta.url).href;
export const NO_SOUND = "This recording has no sound.";
// Steering numbers; knot spacing and the lean were checked on a synthetic
// click track (snd page check), the rest are untuned.
export const SCENE_SOUND = {
  preference: "sm64.sceneSound", volume: 0.5,   // the localStorage entry: {volume, muted}
  knotPictures: 30,          // the table is smoothed over a second: present jitter would warble
  maxStepPictures: 2,        // a picture held longer than this is a cut: only its first picture of sound plays
  bend: 0.01, convergeSeconds: 1, deadMs: 3, jumpMs: 80,
  leadSeconds: 0.04,         // a steer takes effect this far ahead of the audio clock
  startMs: 20, steerMs: 100, latencyWindow: 9, aheadSeconds: 6, keepBlocks: 8, cueMs: 150, syncWindow: 600,
  idleMs: 30000,             // the audio device is let go this long after the last play
};

const clamp = (value, low, high) => Math.max(low, Math.min(high, value));

function median(values) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b), mid = sorted.length >> 1;
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

function percentile(values, p) {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.round(p * (sorted.length - 1)))];
}

function storedSound() {
  try {
    const saved = JSON.parse(localStorage.getItem(SCENE_SOUND.preference));
    const volume = Number(saved?.volume);
    return { volume: volume >= 0 && volume <= 1 ? volume : SCENE_SOUND.volume, muted: saved?.muted === true };
  } catch { return { volume: SCENE_SOUND.volume, muted: false }; }
}

function storeSound({ volume, muted }) {
  try { localStorage.setItem(SCENE_SOUND.preference, JSON.stringify({ volume, muted })); }
  catch { /* storage unavailable: the choice lasts this page */ }
}

// The frame table as runs the worklet plays: a run is frames whose samples
// rise by at most maxStepPictures pictures' worth each, so a gap, a sound-less
// frame or a picture the game held for a level load cuts it. Knots: the run's
// first frame exactly, then one a second at the median of its neighbours
// projected at the nominal rate, then the last frame's end one picture on.
export function soundMap(samples, rate, fps) {
  const nominal = rate / fps, limit = SCENE_SOUND.maxStepPictures * nominal, runs = [];
  for (let a = 0; a < samples.length;) {
    if (samples[a] < 0) { a++; continue; }
    let b = a;
    while (b + 1 < samples.length && samples[b + 1] > samples[b] && samples[b + 1] - samples[b] <= limit) b++;
    const every = SCENE_SOUND.knotPictures, half = every >> 1, end = samples[b] + nominal;
    const kp = [a], ks = [samples[a]];
    for (let k = a + every; k <= b + 1 - half; k += every) {
      const near = [];
      for (let j = Math.max(a, k - half); j <= Math.min(b, k + half); j++) near.push(samples[j] - (j - k) * nominal);
      const s = median(near);
      if (s > ks[ks.length - 1] && s < end) { kp.push(k); ks.push(s); }
    }
    kp.push(b + 1);
    ks.push(end);
    runs.push({ p0: a, p1: b + 1, kp: Float64Array.from(kp), ks: Float64Array.from(ks) });
    a = b + 1;
  }
  return runs;
}

// The play's first clock piece: it starts where the pictures reach `from`,
// or now (at `f`) when that has passed. `want` is the picture position the
// pictures say is heard at frame `f`; `nominal` pictures per sample frame.
export function startClock(want, f, nominal, from) {
  const at = f + (from - want) / nominal;
  if (at <= f) return { f0: f, p0: want, slope: nominal, jump: true };
  const f0 = Math.ceil(at);
  return { f0, p0: from + (f0 - at) * nominal, slope: nominal, jump: true };
}

// The next clock piece, or null to keep `plan`: a miss past jumpMs jumps
// (the worklet fades across it); a smaller one leans the rate toward the
// pictures, at most `bend`, until it is inside deadMs.
export function steerClock(plan, want, f, nominal, fps) {
  if (f < plan.f0) return null;                       // the sound has not started yet
  const now = plan.p0 + (f - plan.f0) * plan.slope, missMs = (want - now) * 1000 / fps;
  const { bend, convergeSeconds, deadMs, jumpMs } = SCENE_SOUND;
  if (Math.abs(missMs) > jumpMs) return { f0: f, p0: want, slope: nominal, jump: true };
  const lean = Math.abs(missMs) <= deadMs ? 0 : clamp(missMs / 1000 / convergeSeconds, -bend, bend);
  const slope = nominal * (1 + lean);
  return Math.abs(slope - plan.slope) <= nominal * 1e-6 ? null : { f0: f, p0: now, slope, jump: false };
}

function decodeBlock(bytes, rate) {
  const Offline = globalThis.OfflineAudioContext ?? globalThis.webkitOfflineAudioContext;
  if (!Offline) return Promise.reject(new Error("this browser cannot decode it"));
  return new Offline(2, 1, rate).decodeAudioData(bytes);
}

async function readBlock(response) {
  if (response.ok) return response.arrayBuffer();
  let detail = `HTTP ${response.status}`;
  try { detail = (await response.json()).detail || detail; } catch { /* no JSON body */ }
  throw new Error(detail);
}

// The open scene's sound. SceneLink calls load (a scene opened or ended),
// arm then play (Play: its picture clock started with frame `from` due at
// performance time `start`), shown (a play's picture was drawn) and stop
// (Pause, a step, a seek, a closed scene); the page reads the rest.
export class SceneSound {
  constructor({ changed } = {}) {
    const saved = storedSound();
    Object.assign(this, { changed: changed || (() => {}), volume: saved.volume, muted: saved.muted, table: null,
      runs: [], reason: null, session: null, fps: 30, ctx: null, master: null, node: null, playing: null,
      lastLate: null, offsets: [], reports: [], pending: [], sync: [], decoded: new Map(), loading: new Map(),
      failed: new Set(), notice: null, broken: null, steerTimer: null, cueTimer: null, idleTimer: null });
  }

  get available() { return !!this.table; }

  load(scene) {
    this.stop();
    const audio = scene?.audio;
    Object.assign(this, { session: scene?.session ?? null, fps: scene?.fps || 30, decoded: new Map(),
      loading: new Map(), failed: new Set(), notice: null, sync: [], pending: [], reports: [] });
    if (audio?.available) {
      this.table = { rate: audio.rate, blockSamples: audio.block_samples, totalSamples: audio.total_samples,
        blocks: audio.blocks, samples: Int32Array.from(audio.samples), lost: new Set(audio.lost) };
      this.runs = soundMap(this.table.samples, audio.rate, this.fps);
      this.reason = null;
      if (this.ctx && this.ctx.sampleRate !== audio.rate) this.close();
      this.post({ type: "map", runs: this.runs, blockSamples: audio.block_samples });
    } else {
      Object.assign(this, { table: null, runs: [], reason: scene ? audio?.reason || NO_SOUND : null });
    }
    this.changed();
  }

  // What frame `frame`'s sound is: "sound", "lost" (silence, marked) or "none".
  at(frame) {
    const table = this.table;
    if (!table || !(table.samples[frame] >= 0)) return "none";
    return table.lost.has(frame) ? "lost" : "sound";
  }

  level() { return this.muted ? 0 : this.volume; }

  applyLevel() {
    if (this.master) this.master.gain.setTargetAtTime(this.level(), this.ctx.currentTime, 0.005);
    storeSound(this);
    this.changed();
  }

  toggleMute() { this.muted = !this.muted; this.applyLevel(); }

  setVolume(volume) {
    this.volume = clamp(Number(volume) || 0, 0, 1);
    this.muted = false;
    this.applyLevel();
  }

  post(message) { this.node?.port.postMessage(message); }

  // The audio graph, made inside the Play gesture that first needs it.
  open() {
    if (this.broken) return false;
    const Context = globalThis.AudioContext ?? globalThis.webkitAudioContext;
    if (!Context || !globalThis.AudioWorkletNode) {
      this.broken = "This browser cannot play the recording's sound.";
      this.changed();
      return false;
    }
    if (this.ctx) return true;
    const ctx = this.ctx = new Context({ sampleRate: this.table.rate, latencyHint: "interactive" });
    this.master = ctx.createGain();
    this.master.gain.value = this.level();
    this.master.connect(ctx.destination);
    ctx.audioWorklet.addModule(WORKLET).then(() => {
      if (this.ctx !== ctx) return;
      const node = new AudioWorkletNode(ctx, "scene-sound", { numberOfInputs: 0, outputChannelCount: [2] });
      node.port.onmessage = (event) => this.heard(event.data);
      node.connect(this.master);
      this.node = node;
      this.post({ type: "map", runs: this.runs, blockSamples: this.table.blockSamples });
      for (const [index, channels] of this.decoded) this.post({ type: "block", index, channels });
      this.steer();
    }, (error) => {
      this.broken = `The sound could not start: ${error.message}`;
      this.changed();
    });
    return true;
  }

  close() {
    this.stop();
    clearTimeout(this.cueTimer);
    clearTimeout(this.idleTimer);
    this.ctx?.close().catch(() => {});
    Object.assign(this, { ctx: null, master: null, node: null });
  }

  // Before the picture clock starts: make or wake the audio graph inside the
  // Play gesture, so its first picture is not held up by the audio device.
  arm() {
    if (!this.table || !this.open()) return;
    clearTimeout(this.idleTimer);
    if (this.ctx.state === "suspended") this.offsets = [];    // its output clock restarts
    this.ctx.resume?.().catch(() => {});
  }

  play(from, start) {
    if (!this.table) return;
    this.stop();
    this.arm();
    if (!this.ctx) return;
    this.playing = { from, start, late: [], plan: null };
    this.fetchFrom(from);
    this.steer();
    this.steerTimer = setInterval(() => this.steer(), SCENE_SOUND.startMs);
  }

  stop() {
    clearInterval(this.steerTimer);
    this.steerTimer = null;
    if (!this.playing) return;
    if (this.playing.late.length) this.lastLate = median(this.playing.late);
    this.playing = null;
    Object.assign(this, { pending: [], reports: [] });
    this.post({ type: "stop" });
    const ctx = this.ctx;                    // a while after the last play, free the audio device
    clearTimeout(this.idleTimer);
    this.idleTimer = setTimeout(() => { if (!this.playing && ctx?.state === "running") ctx.suspend(); },
      SCENE_SOUND.idleMs);
    this.changed();
  }

  // A picture of this play was drawn; it shows from the next compositor frame.
  shown(frame, sent) {
    const play = this.playing;
    if (!play || frame < play.from || !(sent >= play.start)) return;
    const next = globalThis.requestAnimationFrame ?? ((callback) => setTimeout(() => callback(performance.now()), 0));
    next((visible) => {
      if (this.playing !== play) return;
      play.late.push(visible - (play.start + (frame - play.from) * 1000 / this.fps));
      if (play.late.length > SCENE_SOUND.latencyWindow) play.late.shift();
      this.pending.push({ frame, visible });
      this.steer();
    });
  }

  // When the output plays what it renders: performance time minus context time,
  // from getOutputTimestamp once the device reports it (a starting device's
  // first readings wander, so the start waits for three that agree).
  measureOutput() {
    const ctx = this.ctx;
    if (ctx.state !== "running" || !(ctx.currentTime > 0)) return;
    const stamp = ctx.getOutputTimestamp?.();
    if (!stamp) {
      const latency = (ctx.baseLatency || 0) + (ctx.outputLatency || 0);
      this.offsets.push(performance.now() - (ctx.currentTime - latency) * 1000);
    } else if (stamp.contextTime > 0 && stamp.performanceTime > 0) {
      this.offsets.push(stamp.performanceTime - stamp.contextTime * 1000);
    }
    if (this.offsets.length > 15) this.offsets.shift();
  }

  outputOffset() {
    const recent = this.offsets.slice(-3);
    if (recent.length < 3 || Math.max(...recent) - Math.min(...recent) > 2) return null;
    return median(this.offsets);
  }

  // Bring the sound to the pictures: the position heard at a frame is where
  // the picture clock is then, less how late its pictures appear (this play's
  // pictures, or the last play's until one is drawn).
  steer() {
    const play = this.playing, ctx = this.ctx;
    if (!play || !this.node) return;
    this.measureOutput();
    const offset = this.outputOffset();
    const late = play.late.length ? median(play.late) : this.lastLate;
    if (offset == null || late == null) return;
    const rate = ctx.sampleRate, nominal = this.fps / rate;
    const f = Math.ceil((ctx.currentTime + SCENE_SOUND.leadSeconds) * rate);
    const want = play.from + (offset + f / rate * 1000 - play.start - late) * this.fps / 1000;
    const piece = play.plan ? steerClock(play.plan, want, f, nominal, this.fps) : startClock(want, f, nominal, play.from);
    if (piece) {
      if (!play.plan) {                      // started: steer at the slower pace from here
        clearInterval(this.steerTimer);
        this.steerTimer = setInterval(() => this.steer(), SCENE_SOUND.steerMs);
      }
      play.plan = piece;
      this.post({ type: "clock", piece });
    }
    this.fetchFrom(Math.max(play.from, want));
  }

  // The worklet's position reports: how far each drawn picture's own sample
  // was from the sample playing as it appeared.
  heard(message) {
    if (message.type !== "position" || !this.playing) return;
    this.reports.push(message);
    if (this.reports.length > 8) this.reports.shift();
    const offset = this.outputOffset(), rate = this.ctx?.sampleRate, table = this.table;
    if (offset == null || !rate || !table) return;
    const waiting = [];
    for (const shown of this.pending) {
      const f = (shown.visible - offset) / 1000 * rate;
      const after = this.reports.findIndex((report) => report.frame >= f);
      if (after < 0) { waiting.push(shown); continue; }
      const a = this.reports[Math.max(0, after - 1)], b = this.reports[after], own = table.samples[shown.frame];
      if (a.s == null || b.s == null || own < 0 || a === b) continue;
      const s = a.s + (f - a.frame) * (b.s - a.s) / (b.frame - a.frame);
      this.sync.push((s - own) / rate * 1000);
    }
    this.pending = waiting.slice(-60);
    if (this.sync.length > SCENE_SOUND.syncWindow) this.sync.splice(0, this.sync.length - SCENE_SOUND.syncWindow);
  }

  // The sample `position` plays, or the next run's first when it falls in a gap.
  sampleNear(position) {
    const at = sampleAt(this.runs, position);
    if (at) return at.s;
    const next = this.runs.find((run) => run.p0 >= position);
    return next ? next.ks[0] : null;
  }

  fetchFrom(position) {
    const table = this.table, first = table && this.sampleNear(position);
    if (first == null) return;
    const lo = Math.floor(first / table.blockSamples);
    const hi = Math.min(table.blocks - 1, Math.floor((first + SCENE_SOUND.aheadSeconds * table.rate) / table.blockSamples));
    for (let index = lo; index <= hi; index++) this.fetchBlock(index);
    if (this.decoded.size > SCENE_SOUND.keepBlocks) {
      const far = [...this.decoded.keys()].sort((a, b) => Math.abs(b - lo) - Math.abs(a - lo));
      for (const index of far.slice(0, this.decoded.size - SCENE_SOUND.keepBlocks)) this.decoded.delete(index);
      this.post({ type: "keep", indices: [...this.decoded.keys()] });
    }
  }

  fetchBlock(index) {
    if (this.decoded.has(index) || this.loading.has(index) || this.failed.has(index)) return;
    const table = this.table;
    const job = fetch(`${ROOT}/audio/${index}?session=${this.session}`).then(readBlock)
      .then((bytes) => decodeBlock(bytes, table.rate))
      .then((buffer) => {
        if (this.table !== table) return;
        if (buffer.length !== Math.min(table.blockSamples, table.totalSamples - index * table.blockSamples)) {
          throw new Error("it decoded to another length");
        }
        const channels = [buffer.getChannelData(0), buffer.getChannelData(Math.min(1, buffer.numberOfChannels - 1))];
        this.decoded.set(index, channels);
        this.post({ type: "block", index, channels });
      })
      .catch((error) => {
        if (this.table !== table) return;
        this.failed.add(index);
        this.notice = `Part of this recording's sound could not be read, so it plays silent: ${error.message}`;
        this.changed();
      })
      .finally(() => { if (this.loading.get(index) === job) this.loading.delete(index); });
    this.loading.set(index, job);
  }

  // Paused on `frame`: have its sound ready before Play.
  cue(frame) {
    clearTimeout(this.cueTimer);
    if (!this.table || this.playing) return;
    this.cueTimer = setTimeout(() => this.fetchFrom(frame), SCENE_SOUND.cueMs);
  }

  syncReadout() {
    if (this.sync.length < 15) return null;
    const off = this.sync.map(Math.abs);
    return { p50: percentile(off, 0.5), p95: percentile(off, 0.95) };
  }
}

function soundNote(sound, here) {
  if (sound.broken) return sound.broken;
  if (here === "lost") return "Sound lost here: the capture dropped it, so this part plays silent.";
  if (here === "none") return "No sound was recorded for this picture.";
  return sound.notice;
}

// The Sound row under the transport: Mute (M) and the volume, or why there is no sound.
export function SoundBar({ sound, frame }) {
  useEffect(() => sound.cue(frame), [sound, frame]);
  if (!sound.available) return html`<p class="scene-lab-reason scene-lab-sound-none">${sound.reason || NO_SOUND}</p>`;
  const here = sound.at(frame), note = soundNote(sound, here), sync = sound.syncReadout();
  return html`<div class="scene-lab-bar scene-lab-sound" aria-label="Sound">
    <button type="button" aria-pressed=${sound.muted} onclick=${() => sound.toggleMute()}
        title=${sound.muted ? "Turn the recording's sound back on (M)" : "Mute the recording's sound (M)"}>
      <${Icon} name=${sound.muted ? "speakerMuted" : "speaker"} size=${16} /> Mute</button>
    <input type="range" min="0" max="1" step="0.01" value=${sound.volume} aria-label="Volume" title="Volume"
      oninput=${(event) => sound.setVolume(event.currentTarget.value)} />
    ${note && html`<span class=${`scene-lab-sound-note ${here === "lost" ? "is-lost" : ""}`}>${note}</span>`}
    ${sync && html`<span class="scene-lab-readout scene-lab-sound-sync">sound to picture ${Math.round(sync.p50)} ms
      (p95 ${Math.round(sync.p95)} ms)</span>`}
  </div>`;
}
