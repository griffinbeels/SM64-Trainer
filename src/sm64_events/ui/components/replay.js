// src/sm64_events/ui/components/replay.js — inline clip player + recording dot
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";

const html = htm.bind(h);

// Expanded row under an attempt: extract on mount (server caches), then play.
export function ReplayPlayer({ attemptId }) {
  const [state, setState] = useState({ phase: "loading" });
  const [savedPath, setSavedPath] = useState(null);

  useEffect(() => {
    let alive = true;
    send("POST", `/api/attempts/${attemptId}/replay`)
      .then((r) => alive && setState({ phase: "ready", ...r }))
      .catch((e) => alive && setState({ phase: "error", message: String(e) }));
    return () => { alive = false; };
  }, [attemptId]);

  async function saveReplay() {
    const r = await send("POST", `/api/attempts/${attemptId}/replay/save`);
    setSavedPath(r.path);
  }

  if (state.phase === "loading")
    return html`<span class="meta">extracting replay…</span>`;
  if (state.phase === "error")
    return html`<span class="badx">replay unavailable</span>
      <span class="meta"> ${state.message}</span>`;
  return html`<div class="replay-player">
    ${state.truncated && html`<div class="meta">⚠ starts mid-attempt (buffer didn't cover the full span)</div>`}
    <video controls preload="auto" src=${state.clip_url}></video>
    <div>
      <button onclick=${saveReplay} disabled=${savedPath !== null}>
        ${savedPath ? "Saved" : "Save Replay"}</button>
      ${savedPath && html`<span class="meta"> → ${savedPath}</span>`}
    </div>
  </div>`;
}

// Header indicator: red = recording, grey = no capture, hidden = replay absent.
export function RecordingDot() {
  const [st, setSt] = useState(null);
  useEffect(() => {
    let alive = true;
    const poll = () =>
      getJSON("/api/replay/status")
        .then((s) => alive && setSt(s))
        .catch(() => alive && setSt(null));
    poll();
    const id = setInterval(poll, 5000);
    return () => { alive = false; clearInterval(id); };
  }, []);
  if (st === null) return null;
  const cls = st.recording ? "ok" : "bad";
  const label = st.recording
    ? `rec · ${st.encoder} · audio ${st.audio_mode}` : "no capture";
  return html`<span class="dot ${cls}" title="replay buffer">● ${label}</span>`;
}
