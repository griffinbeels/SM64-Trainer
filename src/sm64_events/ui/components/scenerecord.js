// src/sm64_events/ui/components/scenerecord.js -- Record in the Scene viewer (M4 round 6, item 3).
//
// His words: "we should be able to RECORD THE SCENE VIEWER and export it as a video ... with all
// the camera movements / camera swaps / ghosts / hud or no hud / pauses / etc all included in the
// recording. When done recording, a link should appear to the file explorer location for the
// footage."
//
// While Record is on, the page keeps a scene recording (the glossary's): one entry per display
// frame, read off SceneLink at that frame -- the scene frame the play's clock is at (or the frame
// on screen while paused), the camera the page asks for (cameraNow: recorded, free, a ghost's
// view, a rig), the camera the server drew when the picture on screen is that request
// (camera_used), the HUD switch, the ghosts drawn, the sound's switches and mutes, and the
// recorded time the sound plays (play_ms; null while nothing sounds). Stop sends it to the lab
// (POST /api/scene-lab/export), which draws every entry again at full size in a child of its own
// and mixes the sound as the page played it (tools/scene_lab/scene_export.py), so a hitch while
// recording never reaches the video. Each entry costs one small object; arrays that did not
// change are shared with the entry before. The page then shows the export's progress, Cancel,
// and at the end "Show in folder", which opens Explorer at the file through the replay's reveal
// route (POST /api/replay/reveal).
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { Icon } from "./icons.js";
import { getJSON, send } from "../api.js";
import { pictureAt } from "./sceneclock.js";
import { SteadyText } from "./steadytext.js";

const html = htm.bind(h);
const ROOT = "/api/scene-lab";
// fps: the video's frame rate. The free camera, rigs and fly-tos move every display frame, so at 30 a
// camera move would keep half its steps; the game's own pictures (30 a second) each fill two frames.
// version: the recording's format (tools/scene_lab/scene_export.py TIMELINE_VERSION, which refuses another).
export const RECORDING = { version: 1, fps: 60, maxMs: 30 * 60 * 1000, pollMs: 300 };

const samePose = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// The recorded time a play's sound is at on the display frame that began at `frameTime`, and the
// picture it shows then (a play's picture shows on the display frame it is due: SceneLink.holdUntil);
// null before the play's sound starts (its lead, or a resumed play's point: what was heard before
// Pause is never heard again) or after its last picture.
export function playPosition(play, frameTime) {
  if (!play) return null;
  const elapsed = frameTime - play.start + (play.frameMs || 0);
  if (elapsed < (play.resumeMs || 0) || elapsed >= play.length) return null;
  return { frame: pictureAt(play.times, play.from, elapsed), playMs: play.times[play.from] + elapsed };
}

// The camera the server drew for the picture on screen, when that picture answers `camera` at
// `frame`: the header's camera_used (interface E), else what the header says it drew (a ghost's
// view names its camera; a free camera is the pose sent). null otherwise: the export then draws
// `camera` itself, which gives the same picture.
export function cameraUsed(shown, frame, camera) {
  if (!shown || shown.frame !== frame || !samePose(shown.sentCamera ?? null, camera ?? null)) return null;
  if (shown.camera_used !== undefined) return shown.camera_used;
  if (shown.camera !== "free") return null;
  return shown.view ? shown.view.camera ?? null : (camera?.focus ? camera : null);
}

// Keeps the entries of one recording. `animate` calls back once per display frame with its time.
export class SceneRecorder {
  constructor({ animate = (step) => requestAnimationFrame(step), changed = () => {} } = {}) {
    Object.assign(this, { animate, changed, link: null, entries: [], session: null, started: null, last: null,
      on: false, stoppedBy: null, reachedLongest: () => {} });
  }

  get elapsedMs() {
    const last = this.entries[this.entries.length - 1];
    return last ? last.t_ms : 0;
  }

  start(link) {
    if (this.on || !link.scene) return;
    Object.assign(this, { link, entries: [], session: link.scene.session, started: null, last: null, on: true,
      stoppedBy: null });
    const tick = (frameTime) => {
      if (!this.on) return;
      if (this.link.scene?.session !== this.session) return this.stop(frameTime, "closed");
      this.note(frameTime);
      if (this.elapsedMs >= RECORDING.maxMs) return this.reachedLongest(this.stop(frameTime, "long"));
      this.animate(tick);
    };
    this.animate(tick);
    this.changed();
  }

  // One display frame's entry.
  note(frameTime) {
    const link = this.link, sound = link.sound;
    if (this.started == null) this.started = frameTime;
    const at = playPosition(link.playback, frameTime);
    const frame = at ? at.frame : link.frame, camera = link.cameraNow(frame, frameTime);
    const ghosts = link.ghosts.map((ghost) => ghost.ref), mutes = [...sound.ghostMutes];
    const last = this.last;
    const entry = { t_ms: frameTime - this.started, scene_frame: frame, camera,
      camera_used: cameraUsed(link.shown, frame, camera), hud: link.hud,
      ghosts: last && samePose(last.ghosts, ghosts) ? last.ghosts : ghosts,
      sound: { music: sound.music, effects: sound.effects, volume: sound.volume, muted: sound.muted,
        ghost_mutes: last && samePose(last.sound.ghost_mutes, mutes) ? last.sound.ghost_mutes : mutes },
      play_ms: at ? at.playMs : null };
    if (last && samePose(last.camera, camera)) entry.camera = last.camera;
    this.entries.push(entry);
    this.last = entry;
  }

  // Ends the recording: {session, timeline, duration_ms}, or null when nothing was kept. `why`:
  // "stop" (the person), "closed" (the scene went away: nothing to export), "long" (the longest).
  stop(frameTime = performance.now(), why = "stop") {
    if (!this.on) return null;
    this.on = false;
    this.stoppedBy = why;
    this.changed();
    if (why === "closed" || !this.entries.length) return null;
    const duration = Math.max(this.elapsedMs, frameTime - this.started);
    return { session: this.session, timeline: this.entries, duration_ms: duration };
  }
}

const clock = (ms) => {
  const seconds = Math.floor(ms / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
};
const fileName = (path) => String(path || "").split(/[\\/]/).pop();

// The export a page started, kept across visits: leaving the viewer and coming back shows it again.
let exporting = null;

const waitMs = (ms) => new Promise((go) => { setTimeout(go, ms); });

// Sends a finished recording and follows its export until it ends. `update` gets each status.
export async function exportRecording(recorded, update, { poll = waitMs } = {}) {
  const { job } = await send("POST", `${ROOT}/export`, { version: RECORDING.version, ...recorded, fps: RECORDING.fps });
  let status = { state: "running", progress: 0, message: "Starting...", job };
  exporting = status;
  update(status);
  while (status.state === "running") {
    await poll(RECORDING.pollMs);
    status = { ...(await getJSON(`${ROOT}/export/${job}`)), job };
    if (exporting?.job === job) exporting = status;
    update(status);
  }
  return status;
}

// The Record row under the sound: Record / Stop, how long, then the export's progress, Cancel and
// "Show in folder". `recorder` is a test's SceneRecorder.
export function RecordBar({ link, recorder: given = null }) {
  const [, setRevision] = useState(0);
  const [status, setStatus] = useState(() => exporting);
  const [problem, setProblem] = useState(null);
  const recorder = useRef(given);
  if (!recorder.current) recorder.current = new SceneRecorder();
  const own = recorder.current;
  own.changed = () => setRevision((revision) => revision + 1);
  useEffect(() => {
    if (!own.on) return undefined;
    const timer = setInterval(() => setRevision((revision) => revision + 1), 250);
    return () => clearInterval(timer);
  }, [own.on]);
  useEffect(() => () => { own.stop(performance.now(), "closed"); }, []);
  const running = status?.state === "running";
  async function finish(recorded) {
    if (!recorded) return;
    setProblem(null);
    try {
      await exportRecording(recorded, setStatus);
    } catch (error) {
      setStatus(null);
      setProblem(error.message);
    }
  }
  own.reachedLongest = finish;       // the longest recording stops itself and is made as Stop would
  async function cancel() {
    try { await send("POST", `${ROOT}/export/${status.job}/cancel`); } catch (error) { setProblem(error.message); }
  }
  async function reveal(event) {
    event.preventDefault();
    try { await send("POST", "/api/replay/reveal", { path: status.path }); } catch (error) { setProblem(error.message); }
  }
  const note = problem || STOPPED[own.stoppedBy] || null;
  // One button that says Record or Stop at one width, then a place of its own for what it is doing
  // (round 9, item 3: pressing it never moves anything): every line there is one line, cut short with
  // the whole sentence on hover.
  return html`<div class="scene-lab-bar scene-lab-record" aria-label="Record">
    ${own.on
      ? html`<button type="button" class="is-recording" onclick=${() => finish(own.stop(performance.now(), "stop"))}
            title="Stop recording and make the video">
          <${Icon} name="stop" size=${15} /><${SteadyText} text="Stop" sizes=${RECORD_WORDS} /></button>`
      : html`<button type="button" disabled=${running} onclick=${() => { setProblem(null); own.start(link); }}
            title=${running ? "A video is still being made" : "Record what the viewer shows, with its sound, and save it as a video"}>
          <${Icon} name="record" size=${15} className="scene-lab-record-dot" /><${SteadyText} text="Record"
            sizes=${RECORD_WORDS} /></button>`}
    <span class="scene-lab-record-status">
      ${own.on && html`<span class="scene-lab-record-time" role="status"><${SteadyText}
        text=${`Recording ${clock(own.elapsedMs)}`} sizes=${RECORDING_SIZE} /></span>`}
      ${!own.on && status && html`<${ExportLine} status=${status} cancel=${cancel} reveal=${reveal} />`}
      ${note && html`<span class="scene-lab-record-note is-bad" role="status" title=${note}>${note}</span>`}
    </span>
  </div>`;
}

const RECORD_WORDS = ["Record", "Stop"];
const RECORDING_SIZE = ["Recording 00:00"];        // the longest recording is 30:00

// Why a recording stopped by itself.
const STOPPED = { closed: "Recording stopped: the scene closed.",
  long: "Recording stopped at 30 minutes, the longest one video holds." };

// The export after Stop: its progress and Cancel, then the lab's sentence and "Show in folder", or why
// it was not made.
function ExportLine({ status, cancel, reveal }) {
  if (status.state === "running") {
    return html`<progress max="1" value=${status.progress || 0} aria-label="Making the video"></progress>
      <span class="scene-lab-record-note" role="status" title=${status.message}>${status.message}</span>
      <button type="button" onclick=${cancel} title="Stop making this video">Cancel</button>`;
  }
  if (status.state === "done") {
    const said = status.message || `Saved ${fileName(status.path)}.`;
    return html`<span class="scene-lab-record-note" role="status" title=${said}>${said}</span>${" "}<a href="#"
        class="scene-lab-record-link" onclick=${reveal} title=${status.path}>Show in folder</a>`;
  }
  return html`<span class=${`scene-lab-record-note ${status.state === "error" ? "is-bad" : ""}`} role="status"
    title=${status.message}>${status.message}</span>`;
}
