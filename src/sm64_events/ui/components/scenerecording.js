// The Scene viewer's sign and switch for the live scene recording (clean-behavior, 2026-11-06).
//
// The scene lab asks the game's graphics plugin to record every frame of recent play, so each
// attempt gets a scene. Recording that runs where nobody can see it, or that nobody can stop, is
// what Microsoft's rules for unwanted software describe ("fail to clearly indicate when the
// software is active"). So this row says in plain words whether it records now, and one button
// stops it (the plugin stops within a second: the server deletes the lease it renews,
// tools/scene_lab/live_spool.py) or starts it again. The server keeps the choice for the next
// start (server/scene_lab_api.py RECORDING_SWITCH). It reads GET /api/scene-lab's "live" every
// RECORDING_POLL_MS: {recording, on, switch, reason}; a server that cannot switch shows nothing.
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { send } from "../api.js";
import { pollJSON } from "../pollstate.js";

const html = htm.bind(h);
const ROOT = "/api/scene-lab";
export const RECORDING_POLL_MS = 2000;

export const RECORDING_WORDS = {
  running: "Recording your play so each attempt gets a scene.",
  waiting: "Scene recording is on. It starts when you play the game.",
  off: "Scene recording is off. Attempts you play now get no scene.",
};

// What the row shows for one "live" answer, or null for none (pure: tests read it directly).
export function recordingView(live) {
  if (!live || !live.switch) return null;
  if (!live.on) return { state: "off", words: RECORDING_WORDS.off, action: "Start recording" };
  if (live.recording) return { state: "running", words: RECORDING_WORDS.running, action: "Stop recording" };
  return { state: "waiting", words: RECORDING_WORDS.waiting, detail: live.reason || null, action: "Stop recording" };
}

export function RecordingSwitch() {
  const [live, setLive] = useState(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState(null);
  useEffect(() => pollJSON(ROOT, (probe) => setLive(probe?.live || null),
    { intervalMs: RECORDING_POLL_MS, onError: () => {} }), []);
  const view = recordingView(live);
  if (!view) return null;
  async function flip() {
    setBusy(true);
    setProblem(null);
    try {
      setLive((await send("POST", `${ROOT}/recording`, { on: view.state === "off" })).live);
    } catch (error) { setProblem(error.message); }
    setBusy(false);
  }
  return html`<div class="scene-recording is-${view.state}" role="status" aria-live="polite">
    <span class="scene-recording-light" aria-hidden="true"></span>
    <span class="scene-recording-words" title=${view.detail || view.words}>${view.words}</span>
    <button type="button" disabled=${busy} onclick=${flip}>${busy ? "Switching..." : view.action}</button>
    ${problem && html`<p class="scene-lab-reason">${problem}</p>`}
  </div>`;
}
