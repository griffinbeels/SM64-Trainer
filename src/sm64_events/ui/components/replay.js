// src/sm64_events/ui/components/replay.js — inline clip player + recording dot
import { h } from "preact";
import { useCallback, useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { pollJSON } from "../pollstate.js";
import { clipClock, stepGameFrame, jumpToStart, attemptStartTime } from "../frame.js";
import { watchVideoPicture } from "../videopicture.js";
import { holdRepeat } from "../holdrepeat.js";
import { watchReplayKeys } from "../replaykeys.js";
import { stopShuttle } from "../replayshuttle.js";
import { playReview } from "../reviewcommands.js";
import { attachReviewSource, pauseReviewSource } from "../reviewsource.js";
import { Icon } from "./icons.js";
import { InlineState } from "./states.js";
import { RecordingLink } from "./recordinglink.js";
import { ExternalVideo, attachSharedVolume } from "./externalvideo.js";
import { ReplayTransport } from "./replaytransport.js";

const html = htm.bind(h);

// Expanded row under an attempt: extract on mount (server caches), then play.
// `onVideoEl` reports the <video> element upward so a sibling can follow
// the SAME clock -- the input timeline does. Mirrors VideoStage's own
// `onEl` rather than inventing a second way to hand an element out.
// `onView` reports the clip's metadata the same way, because that clock
// only lines up with the input track once the sibling knows where in the
// clip the attempt's anchor sits (`anchor_offset_s`).
export function ReplayPlayer({ attemptId, imported = false, onCompare, onVideoEl, onView,
    reviewState, onReviewState, beforeSave }) {
  const [url, setUrl] = useState(undefined);
  const [nativeUnavailable, setNativeUnavailable] = useState(imported);
  const [initialLink, setInitialLink] = useState(true);
  return html`<div class="attempt-recording">
    ${!nativeUnavailable
      ? html`<${NativeReplayPlayer} attemptId=${attemptId} onCompare=${onCompare}
          onVideoEl=${onVideoEl} onView=${onView}
          reviewState=${reviewState} onReviewState=${onReviewState} beforeSave=${beforeSave}
          onUnavailable=${() => setNativeUnavailable(true)} />`
      : url ? html`<${ExternalVideo} key=${url} url=${url} autoplay=${initialLink}
          replayActions onCompare=${onCompare}
          reviewState=${reviewState} onReviewState=${onReviewState} />`
      : url === undefined ? html`<p class="replay-state meta">Loading recording…</p>`
      : html`<p class="replay-state meta">${imported ? "Add a public recording to watch this attempt."
          : "No captured replay available. Add a public recording below."}</p>`}
    <${RecordingLink} key=${attemptId} attemptId=${attemptId}
      onLoaded=${setUrl} onChanged=${(next) => { setInitialLink(false); setUrl(next); }} />
  </div>`;
}

function useReplayStepping(videoEl, state) {
  // The shared clock walks captured pictures in order, skipping known
  // heartbeat copies and preserving the clip's actual timestamp intervals.
  // Only legacy clips without a picture clock use 30 Hz time stepping.
  function step(dir) {
    stopShuttle(videoEl.current);
    stepGameFrame(videoEl.current, dir, state.game_fps || 30,
                  state.frame_map || null, clipClock(state));
  }
  // A press remembers whether the clip was playing; the release hands that
  // back, so a hold mid-playback scrubs and then plays on, while a step on
  // a paused clip stays on the frame it reached.
  function stepHold(dir) {
    return holdRepeat(() => step(dir), {
      onPress: () => {
        const video = videoEl.current;
        if (!video || video.paused) return null;
        return () => { playReview(video); };
      },
    });
  }
  function toStart() {
    stopShuttle(videoEl.current);
    jumpToStart(videoEl.current, attemptStartTime(state));
  }
  // Arrow keys use the same hold schedule as the buttons. The coordinator
  // gives them to one player when several attempt drawers are expanded.
  useEffect(() => {
    const video = videoEl.current;
    if (!video) return undefined;
    return watchReplayKeys(video.closest(".attempt-drawer") || video.closest(".replay-player"), {
      step, toStart, video,
      toggle: () => video.paused ? playReview(video) : pauseReviewSource(video),
      onPress: () => video.paused ? null : () => { playReview(video); },
    });
  }, [state]);

  return { step, stepHold, toStart };
}

function useNativeReplayState(attemptId, onView, onUnavailable) {
  const [state, setState] = useState({ phase: "loading" });
  const [savedPath, setSavedPath] = useState(null);
  useEffect(() => {
    let alive = true;
    send("POST", `/api/attempts/${attemptId}/replay`)
      .then((r) => {
        if (!alive) return;
        setState({ phase: "ready", ...r });
        if (onView) onView(r);
        if (!r.clip_url) onUnavailable();
        // saved_path persists across sessions (server globs the save tree):
        // the Save button correctly shows "Saved" for clips saved last week
        setSavedPath(r.saved_path || null);
      })
      .catch((e) => {
        if (!alive) return;
        setState({ phase: "error", message: String(e) });
        // The drawer waits for this answer before it shows the timeline
        // (his 2026-09-01 ruling): a clip that cannot be cut is an answer.
        if (onView) onView(null);
        onUnavailable();
      });
    return () => { alive = false; };
  }, [attemptId]);

  return { state, savedPath, setSavedPath };
}

function NativeReplayPlayer({ attemptId, onCompare, onUnavailable, onVideoEl, onView,
    reviewState, onReviewState, beforeSave }) {
  const { state, savedPath, setSavedPath } = useNativeReplayState(attemptId, onView, onUnavailable);
  const [playing, setPlaying] = useState(false); // event-driven (onplay/onpause)
  const [mediaVideo, setMediaVideo] = useState(null);
  const [saveError, setSaveError] = useState(null);
  const [sourceError, setSourceError] = useState(null);
  const [saving, setSaving] = useState(false);
  const videoEl = useRef(null);
  // One programmatic play() per View-Replay click (= per component mount),
  // NEVER on re-render: gameplay emits events (mario_acted, anchors...),
  // each WS push re-renders this tree, and an inline ref re-fires every
  // render — the old `autoplay` + play()-in-ref resumed paused videos the
  // moment the user started playing in game. Playback may start ONLY here
  // (once) or from the player's own controls.
  const autoPlayed = useRef(false);
  const stopObserving = useRef(null);
  const stopSource = useRef(null);
  const attachVideoEl = useCallback((el) => {
    if (videoEl.current === el) return;
    if (stopObserving.current) stopObserving.current();
    stopSource.current?.();
    videoEl.current = el;
    setMediaVideo(el);
    stopObserving.current = el ? watchVideoPicture(el, () => {}) : null;
    if (onVideoEl) onVideoEl(el);
    if (!el) return;
    attachSharedVolume(el);
    stopSource.current = attachReviewSource(el, state.review_media, state.clip_url, setSourceError, state.frame_times);
  }, [onVideoEl, state]);

  // Seek after metadata arrives, before the one initial play(). Both the
  // initial position and Start use the same real-picture destination.
  useEffect(() => {
    const video = videoEl.current;
    if (!video || state.phase !== "ready") return undefined;
    const begin = () => {
      if (autoPlayed.current) return;
      autoPlayed.current = true;
      jumpToStart(video, attemptStartTime(state));
      video.play().catch(() => {});
    };
    if (video.readyState >= 1) begin();
    else video.addEventListener("loadedmetadata", begin, { once: true });
    return () => video.removeEventListener("loadedmetadata", begin);
  }, [state]);

  async function saveReplay() {
    setSaving(true); setSaveError(null);
    try {
      await beforeSave?.();
      const r = await send("POST", `/api/attempts/${attemptId}/replay/save`);
      setSavedPath(r.path);
    } catch (error) { setSaveError(String(error)); }
    finally { setSaving(false); }
  }

  const { step, stepHold, toStart } = useReplayStepping(videoEl, state);

  function togglePlay() {
    const v = videoEl.current;
    if (!v) return;
    if (v.paused) playReview(v);
    else pauseReviewSource(v);
  }

  if (state.phase === "loading")
    return html`<div class="replay-state"><${InlineState}>Extracting replay…<//></div>`;
  if (state.phase === "error")
    return html`<div class="replay-state"><${InlineState} kind="error">
      Replay unavailable · ${state.message}<//></div>`;
  // A timeline can exist without footage. An empty video element would
  // falsely become its clock and prevent standalone input inspection.
  if (!state.clip_url) return null;
  function revealSaved(e) {
    e.preventDefault();
    send("POST", "/api/replay/reveal", { path: savedPath });
  }

  return html`<div class="replay-player">
    <div class="replay-status-row">
      ${state.starts_mid_attempt && html`<span class="replay-notice warning">
        <${Icon} name="clock" size=${14} /> Starts mid-attempt
      </span>`}
      ${state.ends_early && html`<span class="replay-notice warning">
        <${Icon} name="clock" size=${14} /> Ends before the finish
      </span>`}
      ${state.source === "saved" && html`<span class="replay-notice">
        <${Icon} name="save" size=${14} /> Playing saved replay
      </span>`}
    </div>
    <video preload="auto" src=${state.clip_url} aria-label="Attempt recording" tabindex="0"
           onclick=${togglePlay}
           onplay=${() => setPlaying(true)}
           onpause=${() => setPlaying(false)}
           ref=${attachVideoEl}></video>
    <${ReplayTransport} playing=${playing} onStart=${toStart} onStep=${step}
      video=${mediaVideo} clock=${clipClock(state)}
      loop=${reviewState === undefined ? undefined : reviewState?.loop ?? null}
      reviewReady=${reviewState !== null}
      onLoopChange=${onReviewState ? loop => onReviewState({ loop }) : undefined}
      startTitle="Jump to the attempt start (↓)"
      stepHandlers=${stepHold}
      onToggle=${togglePlay} note="← → Step · J K L Playback · I O Loop · X Clear · Shift+I Loop start" />
    <${ReplayActions} savedPath=${savedPath} saving=${saving} saveReplay=${saveReplay}
      revealSaved=${revealSaved} onCompare=${onCompare} />
    ${saveError && html`<p class="replay-control-error" role="status">${saveError}</p>`}
    ${sourceError && html`<p class="replay-control-error" role="status">${sourceError}</p>`}
  </div>`;
}

function ReplayActions({ savedPath, saving, saveReplay, revealSaved, onCompare }) {
  return html`<div class="replay-actions">
      <button onclick=${saveReplay} disabled=${savedPath !== null || saving}>
        <${Icon} name=${savedPath ? "check" : "save"} size=${15} />
        ${savedPath ? "Saved" : saving ? "Saving…" : "Save replay"}</button>
      ${savedPath && html`<a href="#" class="replay-path" title="Show in Explorer"
            onclick=${revealSaved}><${Icon} name="sessions" size=${14} /> Show file</a>`}
      ${onCompare && html`<button onclick=${onCompare}
          title="Open this run in the Compare tab">
        <${Icon} name="compare" size=${15} /> Compare
      </button>`}
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
  useEffect(() => pollJSON("/api/replay/status", setSt,
    {onError: () => setSt(null)}), [tick]);
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

function RetentionControls({mode, setMode, attempts, setAttempts, mins, setMins, ready}) {
  if (!ready) return html`<span class="meta">Loading settings…</span>`;
  return html`<div class="replay-setting-controls retention-options">
          <div class="retention-mode">
            <label><input type="radio" name="replay-retention" checked=${mode === "attempts"}
              onchange=${() => setMode("attempts")} /> Attempts</label>
            <label><input type="radio" name="replay-retention" checked=${mode === "session"}
              onchange=${() => setMode("session")} /> Session</label>
            <label><input type="radio" name="replay-retention"
              checked=${mode === "minutes"} onchange=${() => setMode("minutes")} /> Minutes</label>
          </div>
          <label class="replay-number-field">
            <input id="replay-retention-count" name="replay_retention_count" type="number"
              min="1" max=${mode === "attempts" ? "1000" : "1440"}
              value=${mode === "attempts" ? attempts : mins}
              aria-label=${mode === "attempts" ? "Attempts to retain" : "Minutes to retain"}
              disabled=${mode === "session" || !ready}
              oninput=${(e) => mode === "attempts" ? setAttempts(e.target.value) : setMins(e.target.value)} />
            <span>${mode === "attempts" ? "attempts" : "min"}</span>
          </label>
        </div>`;
}

function PaddingControls({preS, setPreS, postS, setPostS}) {
  const idleCutoff = Math.max(3, (Number(preS) || 0) + (Number(postS) || 0));
  return html`${preS !== null && html`<div class="replay-setting-row">
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
      </div>`}`;
}

// Unsaved replay history: attempt/time windows plus the shared disk cap.
function BufferSettings({ st, refresh, close }) {
  const [info, setInfo] = useState(null);
  const [mode, setMode] = useState("attempts");
  const [attempts, setAttempts] = useState(10);
  const [mins, setMins] = useState(
    st.retention_s != null ? Math.round(st.retention_s / 60) : 10);
  const [capGb, setCapGb] = useState(Math.round(st.max_buffer_bytes / 1024 ** 3));
  const [preS, setPreS] = useState(null);   // loaded with the settings GET
  const [postS, setPostS] = useState(null);
  const [msg, setMsg] = useState(null);
  useEffect(() => {
    const controller = new AbortController();
    getJSON("/api/replay/settings", {signal: controller.signal}).then((s) => {
      if (controller.signal.aborted) return;
      setInfo(s);
      setMode(s.retention_attempts != null ? "attempts" : s.retention_s == null ? "session" : "minutes");
      setAttempts(s.retention_attempts ?? 10);
      setMins(s.retention_s != null ? Math.round(s.retention_s / 60) : 10);
      setCapGb(s.max_buffer_bytes / 1024 ** 3);
      setPreS(String(s.pre_pad_s));
      setPostS(String(s.post_pad_s));
    }).catch(() => {
      if (!controller.signal.aborted) setMsg("Could not load settings. Close and reopen to retry.");
    });
    return () => controller.abort();
  }, []);

  async function apply() {
    const cap = Number(capGb), m = Number(mins), count = Number(attempts);
    if (!Number.isFinite(cap) || (mode === "minutes" && !Number.isFinite(m))
        || (mode === "attempts" && (!Number.isInteger(count) || count < 1 || count > 1000))) {
      setMsg("enter a number"); return;
    }
    const body = {
      retention_s: mode === "minutes" ? m * 60 : null,
      retention_attempts: mode === "attempts" ? count : null,
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


  const pct = Math.min(100, (st.disk_bytes / st.max_buffer_bytes) * 100);
  return html`<div class="popover replay-settings-popover">
    <div class="popover-heading">
      <div><span class="eyebrow">Replay</span><b>Buffer storage</b></div>
      <button class="icon-button" title="Close" aria-label="Close replay settings"
          onclick=${close}><${Icon} name="close" size=${16} /></button>
    </div>
    <p class="popover-note">Keep recent attempts for review. Saved replays and marked PBs stay saved.</p>
    <div class="buffer-usage">
      <div><b>${fmtGB(st.disk_bytes)} GB</b>
        <span>of ${fmtGB(st.max_buffer_bytes)} GB · ${fmtSpan(st)} covered</span></div>
      <div class="buffer-meter">
        <div style=${`width:${pct}%;--meter-color:${pct > 85 ? "#e0a3a3" : "#7aa2f7"}`}></div>
      </div>
    </div>
    <div class="replay-setting-grid">
      <div class="replay-setting-row replay-retention-row">
        <span><b>Keep footage</b><small>Oldest unsaved footage expires first. Expired footage cannot be restored.</small></span>
        <${RetentionControls} mode=${mode} setMode=${setMode}
          attempts=${attempts} setAttempts=${setAttempts} mins=${mins} setMins=${setMins}
          ready=${info !== null} />
      </div>
      <label class="replay-setting-row">
        <span><b>Disk cap</b><small>Storage limit for unsaved footage.</small></span>
        <span class="replay-setting-controls">
          <span class="replay-number-field">
            <input id="replay-cap-gb" name="replay_cap_gb"
              type="number" min="1" max="1024" value=${capGb}
              oninput=${(e) => setCapGb(e.target.value)} />
            <span>GB</span>
          </span>
        </span>
      </label>
      <${PaddingControls} preS=${preS} setPreS=${setPreS} postS=${postS} setPostS=${setPostS} />
    </div>
    ${info && html`<div class="saved-replay-note">
      <${Icon} name="save" size=${15} />
      <span>Saved replays are permanent · ${fmtGB(info.saved_bytes)} GB</span>
    </div>`}
    <div class="popover-actions">
      ${msg && html`<span class="meta">${msg}</span>`}
      <button onclick=${close}>Close</button>
      <button class="primary-button" onclick=${apply} disabled=${info === null}>
        <${Icon} name="save" size=${15} /> Apply
      </button>
    </div>
  </div>`;
}
