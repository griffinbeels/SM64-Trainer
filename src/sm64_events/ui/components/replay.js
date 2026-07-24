// src/sm64_events/ui/components/replay.js — inline clip player + recording dot
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { stepGameFrame, jumpToStart } from "../frame.js";
import { Icon } from "./icons.js";
import { InlineState } from "./states.js";

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
export function ReplayPlayer({ attemptId, onCompare }) {
  const [state, setState] = useState({ phase: "loading" });
  const [savedPath, setSavedPath] = useState(null);
  const [playing, setPlaying] = useState(false); // event-driven (onplay/onpause)
  const videoEl = useRef(null);
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
      .then((r) => {
        if (!alive) return;
        setState({ phase: "ready", ...r });
        // saved_path persists across sessions (server globs the save tree):
        // the Save button correctly shows "Saved" for clips saved last week
        setSavedPath(r.saved_path || null);
      })
      .catch((e) => alive && setState({ phase: "error", message: String(e) }));
    return () => { alive = false; };
  }, [attemptId]);

  async function saveReplay() {
    const r = await send("POST", `/api/attempts/${attemptId}/replay/save`);
    setSavedPath(r.path);
  }

  // Frame stepping: pause first (stepping implies pause), then seek to the
  // MIDDLE of the adjacent frame — (n±1 + 0.5)/fps — so floating-point
  // rounding can never straddle a frame boundary. Steps move in GAME
  // frames (30 fps SM64 logic), not encoded frames (60 fps presents):
  // each game frame spans two near-identical encoded frames, so stepping
  // 1/60 visibly changed the image only every SECOND press (live-reported
  // 2026-06-12 — "have to press twice").
  // Known caveat (expected, not a bug): capture isn't phase-locked to the
  // game and presents jitter (~59.90-60.05/s, user-measured) — a game
  // frame occasionally spans 1 or 3 encoded frames, so once in a while a
  // single press lands on a duplicate; the next press recovers.
  function step(dir) {
    stepGameFrame(videoEl.current, dir, state.game_fps || 30);
  }
  function toStart() {
    jumpToStart(videoEl.current, 0);
  }

  function togglePlay() {
    const v = videoEl.current;
    if (!v) return;
    if (v.paused) v.play().catch(() => {});
    else v.pause();
  }

  if (state.phase === "loading")
    return html`<div class="replay-state"><${InlineState}>Extracting replay…<//></div>`;
  if (state.phase === "error")
    return html`<div class="replay-state"><${InlineState} kind="error">
      Replay unavailable · ${state.message}<//></div>`;
  function revealSaved(e) {
    e.preventDefault();
    send("POST", "/api/replay/reveal", { path: savedPath });
  }

  return html`<div class="replay-player">
    <div class="replay-status-row">
      ${state.truncated && html`<span class="replay-notice warning">
        <${Icon} name="clock" size=${14} /> Starts mid-attempt
      </span>`}
      ${state.source === "saved" && html`<span class="replay-notice">
        <${Icon} name="save" size=${14} /> Playing saved replay
      </span>`}
    </div>
    <video controls preload="auto" src=${state.clip_url}
           onplay=${() => setPlaying(true)}
           onpause=${() => setPlaying(false)}
           ref=${(el) => {
             videoEl.current = el; // null on unmount — step()/toggle guard
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
    <div class="replay-transport">
      <button onclick=${toStart} title="Jump to the beginning">
        <${Icon} name="restart" size=${15} /> Start
      </button>
      <button onclick=${() => step(-1)} title="Pause and move back one game frame">
        <${Icon} name="stepBack" size=${15} /> Back 1
      </button>
      <button class="primary-transport" onclick=${togglePlay} title="Play or pause">
        <${Icon} name=${playing ? "pause" : "play"} size=${16} />
        ${playing ? "Pause" : "Play"}
      </button>
      <button onclick=${() => step(1)} title="Pause and move forward one game frame">
        <${Icon} name="stepForward" size=${15} /> Forward 1
      </button>
      <span class="replay-frame-note">1 frame = 1/${state.game_fps || 30} s</span>
    </div>
    <div class="replay-actions">
      <button onclick=${saveReplay} disabled=${savedPath !== null}>
        <${Icon} name=${savedPath ? "check" : "save"} size=${15} />
        ${savedPath ? "Saved" : "Save replay"}</button>
      ${savedPath && html`<a href="#" class="replay-path" title="Show in Explorer"
            onclick=${revealSaved}><${Icon} name="sessions" size=${14} /> Show file</a>`}
      ${onCompare && html`<button onclick=${onCompare}
          title="Open this run in the Compare tab">
        <${Icon} name="compare" size=${15} /> Compare
      </button>`}
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
  return html`<span class="recording-control">
    <button class=${`dot recording-button ${cls}`}
          title="replay buffer (${st.encoder} · audio ${st.audio_mode}) — click for storage limits"
          aria-expanded=${open} onclick=${() => setOpen(!open)}>
      <span class="recording-light"></span>${label}
    </button>
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
  return html`<div class="popover replay-settings-popover">
    <div class="popover-heading">
      <div><span class="eyebrow">Replay</span><b>Buffer storage</b></div>
      <button class="icon-button" title="Close" aria-label="Close replay settings"
          onclick=${close}><${Icon} name="close" size=${16} /></button>
    </div>
    <p class="popover-note">Oldest footage is evicted after either limit is reached.</p>
    <div class="buffer-usage">
      <div><b>${fmtGB(st.disk_bytes)} GB</b>
        <span>of ${fmtGB(st.max_buffer_bytes)} GB · ${fmtSpan(st)} covered</span></div>
      <div class="buffer-meter">
        <div style=${`width:${pct}%;--meter-color:${pct > 85 ? "#e0a3a3" : "#7aa2f7"}`}></div>
      </div>
    </div>
    <div class="replay-setting-grid">
      <div class="replay-setting-row">
        <span><b>Keep footage</b><small>Whole session or a rolling window.</small></span>
        <div class="replay-setting-controls retention-options">
          <div class="retention-mode">
            <label><input type="radio" name="replay-retention" checked=${mode === "session"}
              onchange=${() => setMode("session")} /> Session</label>
            <label><input type="radio" name="replay-retention"
              checked=${mode === "minutes"} onchange=${() => setMode("minutes")} /> Last</label>
          </div>
          <label class="replay-number-field">
            <input id="replay-retention-min" name="replay_retention_min" type="number"
              min="1" max="1440" value=${mins} aria-label="Minutes to retain"
              disabled=${mode !== "minutes"} oninput=${(e) => setMins(e.target.value)} />
            <span>min</span>
          </label>
        </div>
      </div>
      <label class="replay-setting-row">
        <span><b>Disk cap</b><small>Hard maximum for the rolling buffer.</small></span>
        <span class="replay-setting-controls">
          <span class="replay-number-field">
            <input id="replay-cap-gb" name="replay_cap_gb"
              type="number" min="1" max="1024" value=${capGb}
              oninput=${(e) => setCapGb(e.target.value)} />
            <span>GB</span>
          </span>
        </span>
      </label>
      ${preS !== null && html`<div class="replay-setting-row">
        <span><b>Clip padding</b><small>${idleCutoff}s idle gaps are not retained.</small></span>
        <div class="replay-setting-controls padding-inputs">
          <label class="replay-number-field">
            <input id="replay-pre-pad" name="replay_pre_pad" type="number"
              min="0" max="10" step="0.5" value=${preS}
              oninput=${(e) => setPreS(e.target.value)} />
            <span>s before</span>
          </label>
          <label class="replay-number-field">
            <input id="replay-post-pad" name="replay_post_pad" type="number"
              min="0" max="10" step="0.5" value=${postS}
              oninput=${(e) => setPostS(e.target.value)} />
            <span>s after</span>
          </label>
        </div>
      </div>`}
    </div>
    ${info && html`<div class="saved-replay-note">
      <${Icon} name="save" size=${15} />
      <span>Saved replays are permanent · ${fmtGB(info.saved_bytes)} GB</span>
    </div>`}
    <div class="popover-actions">
      ${msg && html`<span class="meta">${msg}</span>`}
      <button onclick=${close}>Close</button>
      <button class="primary-button" onclick=${apply}>
        <${Icon} name="save" size=${15} /> Apply
      </button>
    </div>
  </div>`;
}
