// src/sm64_events/ui/components/sceneaudioworklet.js -- renders the Scene viewer's sound (M4 E4-audio).
//
// An AudioWorklet module: it runs on the audio thread, imports nothing, and
// allocates nothing per sample. sceneaudio.js sends it the recording's map
// from recorded time (ms, sceneclock.js) to sample (runs of knots: a run is
// sound with no cut in it), the decoded blocks around the play position, and
// a clock: from context frame f0 on, recorded time p0 + (f - f0) * slope.
// Each output frame plays the sample its recorded time maps to, so a new
// slope steers the sound with no seam. A start, a stop, a jump and the edge of
// a run fade over FADE frames, so nothing clicks. The page imports sampleAt
// from here too, so the map has one reader. Blocks come per track: "mix" (the
// recording) and, when the scene has them, "music" and "effects", which lie on
// the recording's own samples, so one map and one clock serve all three; a
// track change fades out, swaps and fades in, and no track is silence. Each
// ghost in the scene adds a track of its own sound ("ghost:<ref>", ghost_sound.py),
// on the same samples, mixed under the stage at its own gain (0 muted), which
// moves over FADE frames.
const FADE = 64;                 // 1.3 ms at 48 kHz
const REPORT_QUANTA = 16;        // a position report every ~43 ms of sound
const AT = { run: 0, s: 0, edge: 0 };

function below(values, value, lo, hi) {   // the last index in [lo, hi) whose value is <= value
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (values[mid] <= value) lo = mid; else hi = mid;
  }
  return lo;
}

// The sample recorded time `p` plays, or null between runs: AT, reused, with
// the run's index (pass it back as `hint`) and the distance in ms to the
// nearer end of the run.
export function sampleAt(runs, p, hint = 0) {
  let index = hint < runs.length && runs[hint].p0 <= p ? hint : 0;
  if (!(index < runs.length && runs[index].p0 <= p && p < runs[index].p1)) {
    if (!runs.length || p < runs[0].p0) return null;
    let lo = 0, hi = runs.length;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (runs[mid].p0 <= p) lo = mid; else hi = mid;
    }
    index = lo;
    if (p >= runs[index].p1) return null;
  }
  const run = runs[index], { kp, ks } = run, j = below(kp, p, 0, kp.length - 1);
  AT.run = index;
  AT.s = ks[j] + (p - kp[j]) * (ks[j + 1] - ks[j]) / (kp[j + 1] - kp[j]);
  AT.edge = Math.min(p - run.p0, run.p1 - p);
  return AT;
}

const Base = globalThis.AudioWorkletProcessor ?? class {};

// The page's messages, each on the processor (this).
const TAKE = {
  map(message) {
    if (message.extend) {                           // a growing scene's longer map: same clock, same blocks
      Object.assign(this, { runs: message.runs, hint: 0 });
      return;
    }
    Object.assign(this, { runs: message.runs, blockSamples: message.blockSamples, clock: null, next: [], env: 0,
      fading: false, hint: 0, cached: -1 });
    for (const blocks of this.tracks.values()) blocks.clear();
  },
  block(message) {
    const track = message.track ?? "mix";
    this.trackBlocks(track).set(message.index, message.channels);
    if (track === this.track) this.cached = -1;
    for (const ghost of this.ghosts) if (ghost.track === track) ghost.cached = -1;
  },
  keep(message) {
    const blocks = this.trackBlocks(message.track ?? "mix");
    for (const index of [...blocks.keys()]) if (!message.indices.includes(index)) blocks.delete(index);
    this.cached = -1;
    for (const ghost of this.ghosts) ghost.cached = -1;
  },
  ghosts(message) {                                 // the ghosts' tracks and gains; a gone ghost's blocks go
    const was = new Map(this.ghosts.map((ghost) => [ghost.track, ghost]));
    this.ghosts = message.tracks.map(({ track, gain }) => Object.assign(was.get(track)
      ?? { track, level: 0, cached: -1, data: null }, { gain, blocks: this.trackBlocks(track) }));
    for (const track of was.keys()) if (!message.tracks.some((ghost) => ghost.track === track)) this.tracks.delete(track);
  },
  track(message) {                                  // fades out, swaps, fades in (at once when silent)
    this.nextTrack = message.track ?? null;
    this.switching = this.nextTrack !== this.track;
    if (this.switching && this.env === 0) this.useTrack(this.nextTrack);
  },
  clock(message) {                                  // replaces what was waiting from its frame on
    this.next = [...this.next.filter((piece) => piece.f0 < message.piece.f0), message.piece];
  },
  stop() {
    this.next = [{ f0: 0, stop: true }];
  },
};

export class SceneSoundProcessor extends Base {
  constructor() {
    super();
    Object.assign(this, { runs: [], blockSamples: 1, tracks: new Map([["mix", new Map()]]), track: "mix",
      blocks: null, switching: false, nextTrack: "mix", clock: null, next: [], env: 0, fading: false, ghosts: [],
      hint: 0, quanta: 0, starved: 0, cached: -1, cachedData: null, firstP: 0, firstS: null, l: 0, r: 0 });
    this.blocks = this.tracks.get("mix");
    if (this.port) this.port.onmessage = (event) => this.take(event.data);
  }

  trackBlocks(track) {
    if (!this.tracks.has(track)) this.tracks.set(track, new Map());
    return this.tracks.get(track);
  }

  // The blocks played from now on: a track's, or none (silence).
  useTrack(track) {
    Object.assign(this, { track, blocks: track ? this.trackBlocks(track) : null, switching: false, cached: -1 });
  }

  take(message) {
    TAKE[message.type]?.call(this, message);
  }

  value(index, channel) {
    if (!this.blocks) return 0;                       // no track: silence, and nothing is missing
    const size = this.blockSamples, block = Math.floor(index / size);
    if (block !== this.cached) {
      this.cachedData = this.blocks.get(block) ?? null;
      this.cached = block;
    }
    if (!this.cachedData) {
      this.starved++;
      return 0;
    }
    return this.cachedData[channel][index - block * size] ?? 0;
  }

  // A ghost's sample: its own block cache; a block not here yet is silence (that ghost only).
  ghostValue(ghost, index, channel) {
    const size = this.blockSamples, block = Math.floor(index / size);
    if (block !== ghost.cached) {
      ghost.data = ghost.blocks.get(block) ?? null;
      ghost.cached = block;
    }
    return ghost.data ? ghost.data[channel][index - block * size] ?? 0 : 0;
  }

  // Takes up the next clock piece once it is due: at once when it carries the
  // sound on, after fading the old one out when it jumps or stops.
  advance(f) {
    const due = this.next[0];
    if (!due || due.f0 > f) return;
    if ((due.jump || due.stop) && this.clock && this.env > 0) {
      this.fading = true;
      return;
    }
    this.next.shift();
    this.clock = due.stop ? null : due;
    this.fading = false;
    if (!this.clock || due.jump) this.env = 0;
  }

  // The sound at frame `f` into this.l and this.r, faded at its run's ends.
  sound(f, first) {
    this.l = this.r = 0;
    if (!this.clock) return;
    const p = this.clock.p0 + (f - this.clock.f0) * this.clock.slope, at = sampleAt(this.runs, p, this.hint);
    if (first) {
      this.firstP = p;
      this.firstS = at ? at.s : null;
    }
    if (!at) return;
    this.hint = at.run;
    const edge = Math.min(1, at.edge / this.clock.slope / FADE), whole = Math.floor(at.s), part = at.s - whole;
    this.l = edge * (this.value(whole, 0) * (1 - part) + this.value(whole + 1, 0) * part);
    this.r = edge * (this.value(whole, 1) * (1 - part) + this.value(whole + 1, 1) * part);
    for (const ghost of this.ghosts) {
      ghost.level += Math.max(-1 / FADE, Math.min(1 / FADE, ghost.gain - ghost.level));
      if (!ghost.level) continue;
      const scale = edge * ghost.level;
      this.l += scale * (this.ghostValue(ghost, whole, 0) * (1 - part) + this.ghostValue(ghost, whole + 1, 0) * part);
      this.r += scale * (this.ghostValue(ghost, whole, 1) * (1 - part) + this.ghostValue(ghost, whole + 1, 1) * part);
    }
  }

  // One quantum: `out` is [left, right], `frame0` the context frame of its first sample.
  render(out, frame0) {
    const left = out[0], right = out[1] ?? out[0];
    for (let i = 0; i < left.length; i++) {
      this.advance(frame0 + i);
      if (this.switching && this.env === 0) this.useTrack(this.nextTrack);
      this.sound(frame0 + i, i === 0);
      const out = this.fading || this.switching;
      this.env = out ? Math.max(0, this.env - 1 / FADE) : Math.min(1, this.env + 1 / FADE);
      left[i] = this.l * this.env;
      right[i] = this.r * this.env;
    }
    if (this.clock && ++this.quanta % REPORT_QUANTA === 0 && this.port) {
      this.port.postMessage({ type: "position", frame: frame0, p: this.firstP, s: this.firstS, starved: this.starved });
    }
  }

  process(inputs, outputs) {
    this.render(outputs[0], globalThis.currentFrame);
    return true;
  }
}

if (typeof globalThis.registerProcessor === "function") globalThis.registerProcessor("scene-sound", SceneSoundProcessor);
