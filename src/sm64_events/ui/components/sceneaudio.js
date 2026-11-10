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
// and the volume persist per viewer.
//
// Music and Sound effects (round 4, item 11: "toggle music / game sound
// effects separately"): an attempt scene's sound can carry its music and its
// sound effects apart (the source-stems layer, tools/scene_lab/stems_layer.py),
// split from the game's own audio program, on the recording's own samples.
// Both on plays the recording itself, exactly what he heard; one on plays that
// track; both off is silence. A switch fades out and in on the same clock, so
// every sound stays where it was. The switches persist with Mute; a scene
// without the split shows them disabled and says why.
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
export const NOT_SPLIT_HERE = "Music and sound effects are not split here, so this part plays silent.";
// Steering numbers; the lean was checked on a
// synthetic click track (snd page check), the rest are untuned.
export const SCENE_SOUND = {
  preference: "sm64.sceneSound", volume: 0.5,   // the localStorage entry: {volume, muted, music, effects}
  joinMs: 2,                 // samples this close to the recorded time between two pictures are one run
  bend: 0.01, convergeSeconds: 1, deadMs: 3, jumpMs: 80,
  leadSeconds: 0.04,         // a steer takes effect this far ahead of the audio clock
  tickMs: 1000 / 60,         // a display frame: a play's picture shows within one after its recorded time
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
    return { volume: volume >= 0 && volume <= 1 ? volume : SCENE_SOUND.volume, muted: saved?.muted === true,
      music: saved?.music !== false, effects: saved?.effects !== false };
  } catch { return { volume: SCENE_SOUND.volume, muted: false, music: true, effects: true }; }
}

function storeSound({ volume, muted, music, effects }) {
  try { localStorage.setItem(SCENE_SOUND.preference, JSON.stringify({ volume, muted, music, effects })); }
  catch { /* storage unavailable: the choice lasts this page */ }
}

// The table's split: the frames where a single track has nothing (a set), or null
// when the scene has no split.
function stemsOf(audio) {
  return audio?.stems?.available ? { lost: new Set(audio.stems.lost || []) } : null;
}

// Why the scene has no split (the server's sentence), or null when it has one.
function splitReasonOf(audio) {
  return audio.stems?.available ? null : audio.stems?.reason || null;
}

// A ghost's own sound track (ghost_sound.py): its ref, on the recording's samples.
const GHOST_TRACK = "ghost:";
const ghostTrack = (ref) => `${GHOST_TRACK}${ref}`;

// What plays for the switches: the recording when both are on (or the scene
// has no split), one track when only it is on, nothing when both are off.
export function trackFor(split, music, effects) {
  if (!split || (music && effects)) return "mix";
  return music ? "music" : effects ? "effects" : null;
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
// performance time `start`), shown (a play's picture was drawn) and stop (Pause, a
// step, a seek, a closed scene); the page reads the rest.
export class SceneSound {
  constructor({ changed, observe } = {}) {
    const saved = storedSound();
    Object.assign(this, { changed: changed || (() => {}), observe: observe || (() => {}), volume: saved.volume,
      muted: saved.muted, table: null,
      music: saved.music, effects: saved.effects, track: "mix", switchTo: undefined, splitReason: null,
      runs: [], times: new Float64Array(0), reason: null, session: null, fps: 30, ctx: null, master: null, node: null,
      playing: null, lastLate: null, offsets: [], reports: [], pending: [], sync: [], held: new Map(),
      loading: new Map(), failed: new Set(), notice: null, broken: null, steerTimer: null, cueTimer: null,
      idleTimer: null, cueTime: 0, ghosts: [], ghostMutes: new Set(), ghostProblems: new Map() });
  }

  get available() { return !!this.table; }

  // The decoded blocks of the track that plays (a track's blocks stay while another plays).
  get decoded() { return this.blocksOf(this.track ?? "mix"); }

  blocksOf(track) {
    if (!this.held.has(track)) this.held.set(track, new Map());
    return this.held.get(track);
  }

  get split() { return !!this.table?.stems; }

  load(scene) {
    this.stop();
    const audio = scene?.audio;
    Object.assign(this, { session: scene?.session ?? null, fps: scene?.fps || 30, times: recordedTimes(scene),
      held: new Map(), loading: new Map(), failed: new Set(), notice: null, sync: [], pending: [], reports: [],
      switchTo: undefined, ghosts: [], ghostProblems: new Map() });
    this.postGhosts();
    if (audio?.available) {
      this.table = { rate: audio.rate, blockSamples: audio.block_samples, totalSamples: audio.total_samples,
        blocks: audio.blocks, samples: Int32Array.from(audio.samples), lost: new Set(audio.lost),
        stems: stemsOf(audio) };
      this.splitReason = splitReasonOf(audio);
      this.noteRefusal(scene);
      this.runs = soundMap(this.table.samples, this.times, audio.rate, this.fps);
      this.reason = null;
      if (this.ctx && this.ctx.sampleRate !== audio.rate) this.close();
      this.post({ type: "map", runs: this.runs, blockSamples: audio.block_samples });
      this.track = trackFor(this.split, this.music, this.effects);
      this.post({ type: "track", track: this.track });
    } else {
      Object.assign(this, { table: null, runs: [], reason: scene ? audio?.reason || NO_SOUND : null, track: "mix",
        splitReason: null });
    }
    this.changed();
  }

  // A refused split: the row says the plain sentence, the log keeps why (the stems layer's own words).
  noteRefusal(scene) {
    const stems = scene.audio.stems;
    if (!stems?.detail) return;
    this.observe({ surface: "scene_viewer", kind: "sound_not_split", scene: scene.name ?? null,
      reason: stems.reason, detail: stems.detail, stats: stems.stats || null });
  }

  // A growing scene gained frames, and maybe a refit of its sound (the server builds it from the
  // recorder's ring as the scene grows): the table and the map extend in place. Its stream only
  // grows, so the blocks already decoded stay, except a last block that was short then.
  grew(scene) {
    this.times = recordedTimes(scene);
    const audio = scene?.audio, table = this.table;
    if (!audio?.available) return;
    if (!table) {                            // never switched on in the middle of a play: at the next one
      if (!this.playing) this.load(scene);
      return;
    }
    const short = table.totalSamples % table.blockSamples ? Math.floor(table.totalSamples / table.blockSamples) : -1;
    Object.assign(table, { totalSamples: audio.total_samples, blocks: audio.blocks,
      samples: Int32Array.from(audio.samples), lost: new Set(audio.lost), stems: stemsOf(audio) });
    this.splitReason = splitReasonOf(audio);
    for (const ghost of this.ghosts) {             // the server makes a grown scene's ghost tracks again
      this.held.delete(ghostTrack(ghost));
      for (const key of [...this.failed]) if (key.startsWith(`${ghostTrack(ghost)}:`)) this.failed.delete(key);
      this.post({ type: "keep", track: ghostTrack(ghost), indices: [] });
    }
    if (this.blocksOf("mix").delete(short)) this.post({ type: "keep", indices: [...this.blocksOf("mix").keys()] });
    this.failed.delete(`mix:${short}`);
    this.runs = soundMap(table.samples, this.times, audio.rate, this.fps);
    // An extension: the worklet keeps its clock and blocks (a fresh map would stop a play's sound).
    this.post({ type: "map", runs: this.runs, blockSamples: audio.block_samples, extend: true });
    this.retrack();
    this.changed();
  }

  // A switch: Music or Sound effects on or off, remembered with Mute.
  toggle(which) {
    this[which] = !this[which];
    storeSound(this);
    this.retrack();
    this.changed();
  }

  // Plays the track the switches ask for: at once when it is silence or its
  // block at the play position is here, else once that block arrives (the old
  // track plays meanwhile, so a switch never leaves a hole).
  retrack() {
    const track = this.table ? trackFor(this.split, this.music, this.effects) : "mix";
    if (track === this.track) {
      this.switchTo = undefined;
      return;
    }
    this.switchTo = track;
    if (track === null) {
      this.useTrack(null);
      return;
    }
    const first = this.sampleNear(this.nowTime());
    const index = first == null ? null : Math.floor(first / this.table.blockSamples);
    if (index == null || this.blocksOf(track).has(index) || this.failed.has(`${track}:${index}`)) {
      this.useTrack(track);
    } else {
      this.fetchFrom(this.nowTime(), track);
    }
  }

  useTrack(track) {
    this.track = track;
    this.switchTo = undefined;
    this.post({ type: "track", track });
    if (track) this.fetchFrom(this.nowTime(), track);
  }

  // The recorded time sounding now: the play's clock, or the paused picture's.
  nowTime() {
    const plan = this.playing?.plan, ctx = this.ctx;
    if (plan && ctx) return plan.p0 + (ctx.currentTime * ctx.sampleRate - plan.f0) * plan.slope;
    return this.cueTime;
  }

  // What frame `frame`'s sound is: "sound", "lost" (silence, marked), "unsplit"
  // (one track plays and this part has no split: silence, marked) or "none".
  at(frame) {
    const table = this.table;
    if (!table || !(table.samples[frame] >= 0)) return "none";
    if (table.lost.has(frame) && this.track === "mix") return "lost";
    return this.track && this.track !== "mix" && table.stems?.lost.has(frame) ? "unsplit" : "sound";
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
      for (const [track, blocks] of this.held) {
        for (const [index, channels] of blocks) this.post({ type: "block", track, index, channels });
      }
      this.post({ type: "track", track: this.track });
      this.postGhosts();
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
  // own lateness once it has some; before that the last play's; else half a
  // display frame. SceneLink asks for a play's pictures ahead and draws each
  // on the display frame it is due, so one that is ready in time shows within
  // a frame after its time.
  pictureDelay() {
    const play = this.playing;
    if (play?.late.length) return median(play.late);
    return this.lastLate ?? SCENE_SOUND.tickMs / 2;
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
    this.playing = { from, start, late: [], plan: null, began: null };
    this.fetchAround(this.times[from]);
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

  // A play's picture was drawn for its recorded time (SceneLink calls this for
  // those only); it shows from the next compositor frame, and how late that is
  // against its recorded time steers the sound.
  shown(frame) {
    const play = this.playing;
    if (!play || frame < play.from) return;
    const due = play.start + this.times[frame] - this.times[play.from];
    const next = globalThis.requestAnimationFrame ?? ((callback) => setTimeout(callback, 0));
    next((frameTime) => {            // the display frame's start, unless the stamp is on another clock (jsdom)
      const now = performance.now(), visible = Math.abs(now - frameTime) < 50 ? frameTime : now;
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
    this.fetchAround(Math.max(origin, want));
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

  fetchFrom(time, track = this.switchTo ?? this.track) {
    const table = this.table, first = table && this.sampleNear(time);
    if (first == null || !track) return;
    const lo = Math.floor(first / table.blockSamples), decoded = this.blocksOf(track);
    const hi = Math.min(table.blocks - 1, Math.floor((first + SCENE_SOUND.aheadSeconds * table.rate) / table.blockSamples));
    for (let index = lo; index <= hi; index++) this.fetchBlock(index, track);
    if (decoded.size > SCENE_SOUND.keepBlocks) {
      const far = [...decoded.keys()].sort((a, b) => Math.abs(b - lo) - Math.abs(a - lo));
      for (const index of far.slice(0, decoded.size - SCENE_SOUND.keepBlocks)) decoded.delete(index);
      this.post({ type: "keep", track, indices: [...decoded.keys()] });
    }
  }

  // One block of a track: the recording's ("mix") or its music or effects, which share its blocks.
  fetchBlock(index, track = "mix") {
    const key = `${track}:${index}`, decoded = this.blocksOf(track);
    if (decoded.has(index) || this.loading.has(key) || this.failed.has(key)) return;
    // A ghost's first block makes its whole track on the server: ask one, then the rest.
    const ghost = track.startsWith(GHOST_TRACK);
    if (ghost && !decoded.size && [...this.loading.keys()].some((asked) => asked.startsWith(`${track}:`))) return;
    const table = this.table, query = track === "mix" ? "" : `&track=${encodeURIComponent(track)}`;
    const job = fetch(`${ROOT}/audio/${index}?session=${this.session}${query}`).then(readBlock)
      .then((bytes) => decodeBlock(bytes, table.rate))
      .then((buffer) => {
        if (this.table !== table || (ghost && !this.ghosts.includes(track.slice(GHOST_TRACK.length)))) return;
        if (buffer.length !== Math.min(table.blockSamples, table.totalSamples - index * table.blockSamples)) {
          throw new Error("it decoded to another length");
        }
        const channels = [buffer.getChannelData(0), buffer.getChannelData(Math.min(1, buffer.numberOfChannels - 1))];
        decoded.set(index, channels);
        this.post({ type: "block", track, index, channels });
        if (ghost && decoded.size === 1) this.fetchFrom(this.nowTime(), track);   // its track is made: the rest
        if (this.switchTo === track) this.useTrack(track);
      })
      .catch((error) => {
        if (this.table !== table) return;
        this.failed.add(key);
        if (ghost) {
          this.ghostProblems.set(track.slice(GHOST_TRACK.length), error.message);
          this.changed();
          return;
        }
        this.notice = `Part of this recording's sound could not be read, so it plays silent: ${error.message}`;
        if (this.switchTo === track) this.useTrack(track);
        this.changed();
      })
      .finally(() => { if (this.loading.get(key) === job) this.loading.delete(key); });
    this.loading.set(key, job);
  }

  // The recording's track around `time`, and every ghost's.
  fetchAround(time) {
    this.fetchFrom(time);
    for (const ghost of this.ghosts) this.fetchFrom(time, ghostTrack(ghost));
  }

  // The ghosts the scene draws (SceneLink's answer, refs in its order): each plays its own sound
  // effects (ghost_sound.py) under the volume and Mute, unless muted here.
  setGhosts(refs) {
    const gone = this.ghosts.filter((ref) => !refs.includes(ref));
    this.ghosts = [...refs];
    for (const ref of gone) {
      this.held.delete(ghostTrack(ref));
      this.ghostProblems.delete(ref);
    }
    this.postGhosts();
    if (this.table) this.fetchAround(this.nowTime());
    this.changed();
  }

  ghostMuted(ref) { return this.ghostMutes.has(ref); }

  toggleGhost(ref) {
    if (!this.ghostMutes.delete(ref)) this.ghostMutes.add(ref);
    this.postGhosts();
    this.changed();
  }

  postGhosts() {
    this.post({ type: "ghosts", tracks: this.ghosts.map((ref) => ({ track: ghostTrack(ref), gain: this.ghostMuted(ref) ? 0 : 1 })) });
  }

  // Paused on `frame`: have its sound ready before Play.
  cue(frame) {
    clearTimeout(this.cueTimer);
    this.cueTime = this.times[frame] ?? 0;
    if (!this.table || this.playing) return;
    this.cueTimer = setTimeout(() => this.fetchAround(this.cueTime), SCENE_SOUND.cueMs);
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
  if (here === "unsplit") return NOT_SPLIT_HERE;
  if (here === "none") return "No sound was recorded for this picture.";
  return sound.notice || sound.splitReason;
}

// One of the two switches: the track plays while it is on.
function TrackSwitch({ sound, which, label }) {
  const on = sound[which], split = sound.split, name = label.toLowerCase();
  const title = !split ? sound.splitReason || "This scene's music and sound effects are not split."
    : on ? `Turn the ${name} off` : `Turn the ${name} on`;
  return html`<button type="button" aria-pressed=${on} disabled=${!split} title=${title}
      onclick=${() => sound.toggle(which)}>${label}</button>`;
}

// What the sound's timing against the pictures reads, for the viewer's readout lines ("" until it has
// enough samples).
export function syncWords(sound) {
  const sync = sound.available ? sound.syncReadout() : null;
  return sync ? `sound to picture ${Math.round(sync.p50)} ms (p95 ${Math.round(sync.p95)} ms)` : "";
}

// The Sound row under the transport: Mute (M), the volume, Music and Sound
// effects, or why there is no sound. What this picture's sound is (it changes
// as the scene plays: "Sound lost here") is one line in a place of its own,
// cut short with the whole sentence on hover, so playing never moves the row
// or the rows under it (round 9, item 3). Its timing against the pictures is
// a readout line of the viewer's (syncWords).
export function SoundBar({ sound, frame }) {
  useEffect(() => sound.cue(frame), [sound, frame]);
  if (!sound.available) return html`<p class="scene-lab-reason scene-lab-sound-none">${sound.reason || NO_SOUND}</p>`;
  const here = sound.at(frame), note = soundNote(sound, here);
  return html`<div class="scene-lab-bar scene-lab-sound" aria-label="Sound">
    <button type="button" aria-pressed=${sound.muted} onclick=${() => sound.toggleMute()}
        title=${sound.muted ? "Turn the recording's sound back on (M)" : "Mute the recording's sound (M)"}>
      <${Icon} name=${sound.muted ? "speakerMuted" : "speaker"} size=${16} /> Mute</button>
    <input type="range" min="0" max="1" step="0.01" value=${sound.volume} aria-label="Volume" title="Volume"
      oninput=${(event) => sound.setVolume(event.currentTarget.value)} />
    <${TrackSwitch} sound=${sound} which="music" label="Music" />
    <${TrackSwitch} sound=${sound} which="effects" label="Sound effects" />
    <span class=${`scene-lab-sound-note${here === "lost" || here === "unsplit" ? " is-lost" : ""}`}
      title=${note || undefined}>${note || ""}</span>
  </div>`;
}
