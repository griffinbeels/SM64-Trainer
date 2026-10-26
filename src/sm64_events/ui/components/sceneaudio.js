// src/sm64_events/ui/components/sceneaudio.js -- the Scene viewer's sound (M4 E4-audio, lab only).
//
// Plays the sound the runner heard -- the package's source-audio layer --
// with the pictures during Play. Both keep the recording's own time
// (sceneclock.js): a picture stays up as long as the recording showed it and
// the sound plays through it at its own speed. The server maps every frame to
// the sample sounding as its picture appeared (tools/scene_lab/scene_audio.py);
// soundMap turns that into runs of recorded time against sample. Pictures are
// the truth: the sound starts with the play's first picture (SceneLink holds
// that picture until the sound can start, `lead`), and steering keeps the
// sample heard as each picture appears at that picture's own, leaning the
// sound's rate by at most SCENE_SOUND.bend (a big miss jumps). Stepping,
// seeking and Pause are silent at once, and a lost stretch plays as silence
// and says so. sceneaudioworklet.js renders it on the audio thread. Mute (M)
// and the volume persist per viewer. The recording is one mixed stream, so
// there is one stem; per-stem volume (music, effects) would add a gain per
// stem between the worklet and the master gain.
import { h } from "preact";
import { useEffect } from "preact/hooks";
import htm from "htm";
import { Icon } from "./icons.js";
import { sampleAt } from "./sceneaudioworklet.js";
import { recordedTimes } from "./sceneclock.js";

const html = htm.bind(h);
const ROOT = "/api/scene-lab";
const WORKLET = new URL("./sceneaudioworklet.js", import.meta.url).href;
export const NO_SOUND = "This recording has no sound.";
// Steering numbers; the picture delays and the lean were checked on a
// synthetic click track (snd page check), the rest are untuned.
export const SCENE_SOUND = {
  preference: "sm64.sceneSound", volume: 0.5,   // the localStorage entry: {volume, muted}
  joinMs: 2,                 // samples this close to the recorded time between two pictures are one run
  bend: 0.01, convergeSeconds: 1, deadMs: 3, jumpMs: 80,
  leadSeconds: 0.04,         // a steer takes effect this far ahead of the audio clock
  pictureDelayMs: 35,        // a picture appears this long after it is asked for, until the scene measures it
  tickMs: 1000 / 60,         // a play asks for each picture on the display frame after it is due
  coldLeadMs: 250,           // a starting audio device can be heard this soon after Play, before it reports its clock
  marginMs: 5, settleMs: 500, settleJumpMs: 10,
  startMs: 20, steerMs: 100, latencyWindow: 9, aheadSeconds: 6, keepBlocks: 8, cueMs: 150, syncWindow: 600,
  idleMs: 300000,            // the audio device is let go this long after the last play
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

// The frame table as runs the worklet plays, on the recorded-time axis: knot
// k is (when the recording showed picture k, its sample). Both come from the
// same present stamp, so within a run the sound advances at its own speed
// through any hold. A run ends where the next picture's sample is not the
// recorded time later (a gap, a reopened capture) or has none; its last
// picture plays until the next picture, at most one game frame.
export function soundMap(samples, times, rate, fps) {
  const runs = [], join = SCENE_SOUND.joinMs * rate / 1000;
  const follows = (k) => samples[k + 1] >= 0
    && Math.abs(samples[k + 1] - samples[k] - (times[k + 1] - times[k]) * rate / 1000) <= join;
  for (let a = 0; a < samples.length;) {
    if (samples[a] < 0) { a++; continue; }
    let b = a;
    while (b + 1 < samples.length && follows(b)) b++;
    const tail = Math.min(1000 / fps, b + 1 < times.length ? times[b + 1] - times[b] : Infinity);
    const kp = Float64Array.from({ length: b - a + 2 }, (_, i) => (a + i <= b ? times[a + i] : times[b] + tail));
    const ks = Float64Array.from({ length: b - a + 2 }, (_, i) => (a + i <= b ? samples[a + i]
      : samples[b] + tail * rate / 1000));
    runs.push({ p0: kp[0], p1: kp[kp.length - 1], kp, ks });
    a = b + 1;
  }
  return runs;
}

// The play's first clock piece: it starts where the recorded time reaches
// `from` (the first picture's), or now (at `f`) when that has passed. `want`
// is the recorded time the pictures say is heard at frame `f`; `nominal` is
// ms of recorded time per sample frame.
export function startClock(want, f, nominal, from) {
  const at = f + (from - want) / nominal;
  if (at <= f) return { f0: f, p0: want, slope: nominal, jump: true };
  const f0 = Math.ceil(at);
  return { f0, p0: from + (f0 - at) * nominal, slope: nominal, jump: true };
}

// The next clock piece, or null to keep `plan`: a miss past `jumpMs` jumps
// (the worklet fades across it); a smaller one leans the rate toward the
// pictures, at most `bend`, until it is inside deadMs. Times in ms. While a
// play's sound settles, its first pictures' delay corrects the start with a
// jump past settleJumpMs rather than a second of leaning.
export function steerClock(plan, want, f, nominal, jumpMs = SCENE_SOUND.jumpMs) {
  if (f < plan.f0) return null;                       // the sound has not started yet
  const now = plan.p0 + (f - plan.f0) * plan.slope, missMs = want - now;
  const { bend, convergeSeconds, deadMs } = SCENE_SOUND;
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
// arm, lead and play (Play: the recorded clock starts with frame `from` due at
// performance time `start`), shown (a picture was drawn) and stop (Pause, a
// step, a seek, a closed scene); the page reads the rest.
export class SceneSound {
  constructor({ changed } = {}) {
    const saved = storedSound();
    Object.assign(this, { changed: changed || (() => {}), volume: saved.volume, muted: saved.muted, table: null,
      runs: [], times: new Float64Array(0), reason: null, session: null, fps: 30, ctx: null, master: null, node: null,
      playing: null, lastLate: null, delays: [], offsets: [], reports: [], pending: [], sync: [], decoded: new Map(),
      loading: new Map(), failed: new Set(), notice: null, broken: null, steerTimer: null, cueTimer: null,
      idleTimer: null });
  }

  get available() { return !!this.table; }

  load(scene) {
    this.stop();
    const audio = scene?.audio;
    Object.assign(this, { session: scene?.session ?? null, fps: scene?.fps || 30, times: recordedTimes(scene),
      decoded: new Map(), loading: new Map(), failed: new Set(), notice: null, sync: [], pending: [], reports: [] });
    if (audio?.available) {
      this.table = { rate: audio.rate, blockSamples: audio.block_samples, totalSamples: audio.total_samples,
        blocks: audio.blocks, samples: Int32Array.from(audio.samples), lost: new Set(audio.lost) };
      this.runs = soundMap(this.table.samples, this.times, audio.rate, this.fps);
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

  // How late a play's picture shows against its recorded time: this play's
  // own lateness once it has some; before that the last play's; else what a
  // picture took from being asked for to showing (the play's first, asked for
  // at Play, then the scene's paused ones, the open's included) plus half a
  // display frame, which a play's picture also waits for its request; else
  // pictureDelayMs.
  pictureDelay() {
    const play = this.playing, paused = median(this.delays);
    if (play?.late.length) return median(play.late);
    if (play?.first != null) return play.first + SCENE_SOUND.tickMs / 2;
    return this.lastLate ?? (paused == null ? SCENE_SOUND.pictureDelayMs : paused + SCENE_SOUND.tickMs / 2);
  }

  // How long SceneLink holds the first picture of a play so the sound can
  // start with it: the sound is heard no sooner than the audio clock's lead
  // plus the device's own output delay (unknown while the device starts:
  // coldLeadMs), and the play's first new picture shows pictureDelay after it
  // is due. 0 without sound.
  lead() {
    if (!this.table || !this.ctx) return 0;
    const offset = this.ctx.state === "running" ? this.outputOffset() : null;
    if (offset == null) return SCENE_SOUND.coldLeadMs;
    const heard = offset + (this.ctx.currentTime + SCENE_SOUND.leadSeconds) * 1000 + SCENE_SOUND.marginMs;
    return Math.max(0, heard - performance.now() - this.pictureDelay());
  }

  play(from, start) {
    if (!this.table) return;
    this.stop();
    this.arm();
    if (!this.ctx) return;
    this.playing = { from, start, asked: performance.now(), late: [], first: null, plan: null, began: null };
    this.fetchFrom(this.times[from]);
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

  // A picture asked for at `sent` was drawn; it shows from the next compositor
  // frame. While paused that measures the picture delay; during a play, how
  // late the picture is against its recorded time (a picture asked for before
  // it was due, like the play's first, says nothing about that).
  shown(frame, sent) {
    const play = this.playing, due = play && play.start + this.times[frame] - this.times[play.from];
    const early = play && frame === play.from && sent < due && sent >= play.asked;   // asked for at Play
    if (play && !early && (frame < play.from || !(sent >= due))) return;
    const next = globalThis.requestAnimationFrame ?? ((callback) => setTimeout(callback, 0));
    next((frameTime) => {            // the display frame's start, unless the stamp is on another clock (jsdom)
      const now = performance.now(), visible = Math.abs(now - frameTime) < 50 ? frameTime : now;
      if (!play || early) {
        if (visible - sent >= 1000) return;
        if (!play && !this.playing) this.delays = [...this.delays.slice(-(SCENE_SOUND.latencyWindow - 1)), visible - sent];
        else if (this.playing === play) play.first = visible - sent;
        return;
      }
      if (this.playing !== play) return;
      play.late.push(visible - due);
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

  // Bring the sound to the pictures: the recorded time heard at a frame is
  // where the recorded clock is then, less how late its pictures appear
  // (pictureDelay: from the first drawn picture of the play on, its own).
  steer() {
    const play = this.playing, ctx = this.ctx;
    if (!play || !this.node) return;
    this.measureOutput();
    const offset = this.outputOffset();
    if (offset == null) return;
    const rate = ctx.sampleRate, nominal = 1000 / rate, origin = this.times[play.from];
    const f = Math.ceil((ctx.currentTime + SCENE_SOUND.leadSeconds) * rate);
    const want = origin + offset + f / rate * 1000 - play.start - this.pictureDelay();
    const settling = play.began != null && f - play.began < SCENE_SOUND.settleMs * rate / 1000;
    const piece = play.plan ? steerClock(play.plan, want, f, nominal, settling ? SCENE_SOUND.settleJumpMs : undefined)
      : startClock(want, f, nominal, origin);
    if (piece) {
      if (!play.plan) {                      // started: steer at the slower pace from here
        play.began = piece.f0;
        clearInterval(this.steerTimer);
        this.steerTimer = setInterval(() => this.steer(), SCENE_SOUND.steerMs);
      }
      play.plan = piece;
      this.post({ type: "clock", piece });
    }
    this.fetchFrom(Math.max(origin, want));
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

  // The sample recorded time `time` plays, or the next run's first when it falls in a gap.
  sampleNear(time) {
    const at = sampleAt(this.runs, time);
    if (at) return at.s;
    const next = this.runs.find((run) => run.p0 >= time);
    return next ? next.ks[0] : null;
  }

  fetchFrom(time) {
    const table = this.table, first = table && this.sampleNear(time);
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
    this.cueTimer = setTimeout(() => this.fetchFrom(this.times[frame] ?? 0), SCENE_SOUND.cueMs);
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
