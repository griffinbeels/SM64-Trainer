// src/sm64_events/ui/components/videosync.js — drive N videos in lockstep.
// Offset-only sync: each stage has an in-point (game frames); the transport
// keeps a shared master game-frame and, on every discrete action, re-seeks
// each video to (inFrame + masterFrame) aimed at the frame middle. Continuous
// play just runs every <video> at true rate (they start aligned); pause
// re-syncs to correct any drift.
import { h } from "preact";
import { useCallback, useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { gameFrameOf } from "../frame.js";

const html = htm.bind(h);
const VOLUME_KEY = "replay_volume";
function storedVolume() {
  let v = NaN;
  try { v = parseFloat(localStorage.getItem(VOLUME_KEY)); } catch {}
  return v >= 0 && v <= 1 ? v : 0.3;
}

export function useSyncController() {
  const stages = useRef(new Map());   // id -> { el, getInFrame }
  const [playing, setPlaying] = useState(false);
  const playingRef = useRef(false);   // live state for click-toggle (see toggle)

  const register = useCallback((id, el, getInFrame) => {
    if (el) stages.current.set(id, { el, getInFrame });
    else stages.current.delete(id);
  }, []);
  const unregister = useCallback((id) => stages.current.delete(id), []);

  // master frame = the first stage's game frame minus its own in-point
  const masterFrame = () => {
    const m = stages.current.get("mine") || stages.current.values().next().value;
    if (!m || !m.el) return 0;
    return Math.max(0, gameFrameOf(m.el) - (m.getInFrame() || 0));
  };

  const seekAll = (master) => {
    for (const { el, getInFrame } of stages.current.values()) {
      if (!el) continue;
      const t = ((getInFrame() || 0) + master + 0.5) / 30;
      const max = Number.isFinite(el.duration) ? el.duration : Infinity;
      el.currentTime = Math.min(Math.max(t, 0), max);
    }
  };

  const play = () => {
    for (const { el } of stages.current.values())
      if (el) el.play().catch(() => {});
    playingRef.current = true; setPlaying(true);
  };
  const pause = () => {
    const m = masterFrame();
    for (const { el } of stages.current.values()) if (el) el.pause();
    seekAll(m);                        // re-sync on pause (corrects drift)
    playingRef.current = false; setPlaying(false);
  };
  const step = (dir) => {
    const m = Math.max(0, masterFrame() + dir);
    for (const { el } of stages.current.values()) if (el && !el.paused) el.pause();
    seekAll(m);
    playingRef.current = false; setPlaying(false);
  };
  const toStart = () => {
    for (const { el } of stages.current.values()) if (el) el.pause();
    seekAll(0);
    playingRef.current = false; setPlaying(false);
  };
  // click-a-video toggle: play/pause ALL stages. playingRef holds the live
  // state (a click can land between renders) so it's correct either way.
  const toggle = () => { if (playingRef.current) pause(); else play(); };

  return { register, unregister, play, pause, step, toStart, toggle, playing };
}

export function VideoStage({ src, inFrame, controller, id, onEl }) {
  const ref = useRef(null);
  const inRef = useRef(inFrame || 0);
  useEffect(() => { inRef.current = inFrame || 0; }, [inFrame]);
  useEffect(() => () => controller.unregister(id), [id]);
  const setRef = useCallback((el) => {
    ref.current = el;
    controller.register(id, el, () => inRef.current);
    if (el && !el.dataset.vol) { el.dataset.vol = "1"; el.volume = storedVolume(); }
    if (onEl) onEl(el);
  }, [id, controller.register, onEl]);
  // clicking the video toggles play/pause for EVERY synced stage
  return html`<video class="replay-player" style="width:100%;cursor:pointer" preload="auto"
      src=${src} playsinline ref=${setRef}
      onclick=${() => controller.toggle()}></video>`;
}

// Premiere-style single-bar "work area": the whole track IS the clip
// (far-left = 0:00, far-right = duration); a highlighted region between two
// draggable handles is the in/out. Dragging a handle scrub-seeks the video and,
// on release, calls onCommit(inFrame, outFrame|null) so the caller can persist
// it (null out = "to the end"). in/out are GAME frames (30 fps).
export function WorkArea({ videoEl, inFrame, outFrame, onCommit }) {
  const [dur, setDur] = useState(0);
  const [inF, setInF] = useState(inFrame || 0);
  const [outF, setOutF] = useState(outFrame == null ? null : outFrame);
  const trackRef = useRef(null);
  useEffect(() => { setInF(inFrame || 0); setOutF(outFrame == null ? null : outFrame); },
    [inFrame, outFrame]);
  useEffect(() => {
    if (!videoEl) return;
    const on = () => setDur(videoEl.duration || 0);
    videoEl.addEventListener("loadedmetadata", on);
    if (videoEl.duration) setDur(videoEl.duration);
    return () => videoEl.removeEventListener("loadedmetadata", on);
  }, [videoEl]);

  const maxF = Math.max(1, Math.round(dur * 30));
  const outEff = outF == null ? maxF : outF;
  const seek = (f) => { if (videoEl) videoEl.currentTime = (f + 0.5) / 30; };
  const frameAt = (clientX) => {
    const r = trackRef.current.getBoundingClientRect();
    const pct = r.width ? (clientX - r.left) / r.width : 0;
    return Math.round(Math.min(1, Math.max(0, pct)) * maxF);
  };
  function drag(which, e) {
    e.preventDefault();
    const live = { in: inF, out: outEff };
    const move = (ev) => {
      const f = frameAt(ev.clientX);
      if (which === "in") { live.in = Math.min(f, live.out - 1); setInF(live.in); seek(live.in); }
      else { live.out = Math.max(f, live.in + 1); setOutF(live.out); seek(live.out); }
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      if (onCommit) onCommit(live.in, live.out >= maxF ? null : live.out);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  const inPct = (inF / maxF) * 100;
  const outPct = (outEff / maxF) * 100;
  return html`<div class="workarea">
    <div class="wa-track" ref=${trackRef}>
      <div class="wa-region" style=${`left:${inPct}%;width:${Math.max(0, outPct - inPct)}%`}></div>
      <div class="wa-handle" style=${`left:${inPct}%`}
           onpointerdown=${(e) => drag("in", e)} title="clip start"></div>
      <div class="wa-handle" style=${`left:${outPct}%`}
           onpointerdown=${(e) => drag("out", e)} title="clip end"></div>
    </div>
    <div class="meta wa-times">${(inF / 30).toFixed(2)}s – ${(outEff / 30).toFixed(2)}s
      <span class="wa-total">/ ${(maxF / 30).toFixed(2)}s</span></div>
  </div>`;
}
