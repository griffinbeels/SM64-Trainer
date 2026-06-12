// src/sm64_events/ui/components/replay.js — inline clip player + recording dot
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";

const html = htm.bind(h);

// One shared volume for every replay player — current and future. The last
// user adjustment wins everywhere: changing volume on any player fans out
// to all mounted players and persists (localStorage) for players not yet
// opened, including after a reload. Default 30% — game audio is loud
// against an otherwise-silent page; a stored user choice overrides it.
const VOLUME_KEY = "replay_volume";

function storedVolume() {
  let v = NaN;
  try { v = parseFloat(localStorage.getItem(VOLUME_KEY)); } catch {}
  return v >= 0 && v <= 1 ? v : 0.3;   // NaN fails both comparisons
}

let applyingVolume = false; // re-entrancy guard: our fan-out, not the user

function attachSharedVolume(el) {
  el.volume = storedVolume(); // before addEventListener: must not self-fire
  el.addEventListener("volumechange", () => {
    if (applyingVolume) return;
    try { localStorage.setItem(VOLUME_KEY, String(el.volume)); } catch {}
    applyingVolume = true;
    document.querySelectorAll(".replay-player video").forEach((v) => {
      if (v !== el) v.volume = el.volume;
    });
    applyingVolume = false;
  });
}

// Expanded row under an attempt: extract on mount (server caches), then play.
export function ReplayPlayer({ attemptId }) {
  const [state, setState] = useState({ phase: "loading" });
  const [savedPath, setSavedPath] = useState(null);
  // One programmatic play() per View-Replay click (= per component mount),
  // NEVER on re-render: gameplay emits events (mario_acted, anchors...),
  // each WS push re-renders this tree, and an inline ref re-fires every
  // render — the old `autoplay` + play()-in-ref resumed paused videos the
  // moment the user started playing in game. Playback may start ONLY here
  // (once) or from the player's own controls.
  const autoPlayed = useRef(false);

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
  function revealSaved(e) {
    e.preventDefault();
    send("POST", "/api/replay/reveal", { path: savedPath });
  }

  return html`<div class="replay-player">
    ${state.truncated && html`<div class="meta">⚠ starts mid-attempt (buffer didn't cover the full span)</div>`}
    <video controls preload="auto" src=${state.clip_url}
           ref=${(el) => {
             if (!el) return;
             if (!el.dataset.sharedVolume) { // ref re-fires on every render
               el.dataset.sharedVolume = "1";
               attachSharedVolume(el);
             }
             if (!autoPlayed.current) { // see autoPlayed above: once per mount
               autoPlayed.current = true;
               el.play().catch(() => {});
             }
           }}></video>
    <div>
      <button onclick=${saveReplay} disabled=${savedPath !== null}>
        ${savedPath ? "Saved" : "Save Replay"}</button>
      ${savedPath && html` <a href="#" class="meta replay-path" title="show in Explorer"
            onclick=${revealSaved}>→ ${savedPath}</a>`}
    </div>
  </div>`;
}

function fmtGB(bytes) {
  const gb = bytes / 1024 ** 3;
  return gb >= 10 ? gb.toFixed(0) : gb.toFixed(1);
}

function fmtSpan(st) {
  if (!st.buffer_start_utc || !st.buffer_end_utc) return "empty";
  const s = (new Date(st.buffer_end_utc) - new Date(st.buffer_start_utc)) / 1000;
  if (s >= 5400) return `${(s / 3600).toFixed(1)} h`;
  if (s >= 90) return `${Math.round(s / 60)} min`;
  return `${Math.round(s)} s`;
}

// Header indicator: red = recording, grey = no capture, hidden = replay
// absent. Always shows buffer disk use vs cap; click opens the limits panel.
export function RecordingDot() {
  const [st, setSt] = useState(null);
  const [open, setOpen] = useState(false);
  const [tick, setTick] = useState(0); // bump to re-poll immediately
  useEffect(() => {
    let alive = true;
    const poll = () =>
      getJSON("/api/replay/status")
        .then((s) => alive && setSt(s))
        .catch(() => alive && setSt(null));
    poll();
    const id = setInterval(poll, 5000);
    return () => { alive = false; clearInterval(id); };
  }, [tick]);
  if (st === null) return null;
  const cls = st.recording ? "ok" : "bad";
  const label = st.recording
    ? `rec${st.idle ? " (idle)" : ""} · ${fmtSpan(st)} · ${fmtGB(st.disk_bytes)}/${fmtGB(st.max_buffer_bytes)} GB`
    : "no capture";
  return html`<span style="position:relative">
    <span class="dot ${cls}" style="cursor:pointer"
          title="replay buffer (${st.encoder} · audio ${st.audio_mode}) — click for storage limits"
          onclick=${() => setOpen(!open)}>● ${label}</span>
    ${open && html`<${BufferSettings} st=${st}
        refresh=${() => setTick((t) => t + 1)}
        close=${() => setOpen(false)} />`}
  </span>`;
}

// Storage-limits panel: the ONLY two knobs that bound buffer disk use
// (retention + hard cap). PUT applies live (oldest footage evicts now) and
// persists to data/replay_settings.json.
function BufferSettings({ st, refresh, close }) {
  const [info, setInfo] = useState(null);
  const [mode, setMode] = useState(st.retention_s == null ? "session" : "minutes");
  const [mins, setMins] = useState(
    st.retention_s != null ? Math.round(st.retention_s / 60) : 10);
  const [capGb, setCapGb] = useState(Math.round(st.max_buffer_bytes / 1024 ** 3));
  const [preS, setPreS] = useState(null);   // loaded with the settings GET
  const [postS, setPostS] = useState(null);
  const [msg, setMsg] = useState(null);
  useEffect(() => {
    getJSON("/api/replay/settings").then((s) => {
      setInfo(s);
      setPreS(String(s.pre_pad_s));
      setPostS(String(s.post_pad_s));
    }).catch(() => {});
  }, []);

  async function apply() {
    const cap = Number(capGb), m = Number(mins);
    if (!Number.isFinite(cap) || (mode === "minutes" && !Number.isFinite(m))) {
      setMsg("enter a number"); return;
    }
    const body = {
      retention_s: mode === "session" ? null : m * 60,
      max_buffer_bytes: Math.round(cap * 1024 ** 3),
    };
    if (preS !== null) body.pre_pad_s = Number(preS);   // omitted = unchanged
    if (postS !== null) body.post_pad_s = Number(postS);
    try {
      await send("PUT", "/api/replay/settings", body);
      setMsg("saved ✓ (applies immediately)");
      refresh();
    } catch (e) {
      setMsg(String(e));
    }
  }
  const idleCutoff = Math.max(3, (Number(preS) || 0) + (Number(postS) || 0));

  const pct = Math.min(100, (st.disk_bytes / st.max_buffer_bytes) * 100);
  return html`<div class="popover" style="min-width:360px">
    <div><b>Replay buffer storage</b>
      <span class="meta"> — oldest footage is evicted past either limit</span></div>
    <div style="margin:.4rem 0">
      <div class="meta">${fmtGB(st.disk_bytes)} GB of ${fmtGB(st.max_buffer_bytes)} GB cap
        · covering ${fmtSpan(st)}</div>
      <div style="height:6px;background:#2a2f3a;border-radius:3px;margin-top:2px">
        <div style="height:6px;border-radius:3px;width:${pct}%;background:${pct > 85 ? "#e0a3a3" : "#7aa2f7"}"></div>
      </div>
    </div>
    <div>Keep:
      <label><input type="radio" name="replay-retention" checked=${mode === "session"}
        onchange=${() => setMode("session")} /> whole session</label>
      <label style="margin-left:.5rem"><input type="radio" name="replay-retention"
        checked=${mode === "minutes"} onchange=${() => setMode("minutes")} /> last</label>
      <input id="replay-retention-min" name="replay_retention_min" type="number"
        min="1" max="1440" style="width:4.5rem" value=${mins}
        disabled=${mode !== "minutes"} oninput=${(e) => setMins(e.target.value)} /> min
    </div>
    <div style="margin-top:.3rem">Disk cap:
      <input id="replay-cap-gb" name="replay_cap_gb" type="number" min="1" max="1024"
        style="width:4.5rem" value=${capGb}
        oninput=${(e) => setCapGb(e.target.value)} /> GB
    </div>
    ${preS !== null && html`<div style="margin-top:.3rem">Clip padding:
      <input id="replay-pre-pad" name="replay_pre_pad" type="number"
        min="0" max="10" step="0.5" style="width:4rem" value=${preS}
        oninput=${(e) => setPreS(e.target.value)} /> s before ·
      <input id="replay-post-pad" name="replay_post_pad" type="number"
        min="0" max="10" step="0.5" style="width:4rem" value=${postS}
        oninput=${(e) => setPostS(e.target.value)} /> s after
      <div class="meta">clips clamp to available footage; buffer pauses after
        ${idleCutoff} s without player input and resumes on the next input</div>
    </div>`}
    ${info && html`<div class="meta" style="margin-top:.3rem">
      saved replays (kept forever, not part of the buffer):
      ${fmtGB(info.saved_bytes)} GB in ${info.save_root}\\</div>`}
    <div style="margin-top:.4rem">
      <button onclick=${apply}>Apply</button>
      <button onclick=${close}>Close</button>
      ${msg && html` <span class="meta">${msg}</span>`}
    </div>
  </div>`;
}
