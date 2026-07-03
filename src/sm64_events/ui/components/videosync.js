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
    setPlaying(true);
  };
  const pause = () => {
    const m = masterFrame();
    for (const { el } of stages.current.values()) if (el) el.pause();
    seekAll(m);                        // re-sync on pause (corrects drift)
    setPlaying(false);
  };
  const step = (dir) => {
    const m = Math.max(0, masterFrame() + dir);
    for (const { el } of stages.current.values()) if (el && !el.paused) el.pause();
    seekAll(m);
    setPlaying(false);
  };
  const toStart = () => {
    for (const { el } of stages.current.values()) if (el) el.pause();
    seekAll(0);
    setPlaying(false);
  };

  return { register, unregister, play, pause, step, toStart, playing };
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
  return html`<video class="replay-player" style="width:100%" preload="auto"
      src=${src} playsinline ref=${setRef}></video>`;
}

// Dual-handle in/out selector over the video duration (game frames).
export function SyncTrack({ videoEl, inFrame, outFrame, onChange }) {
  const [dur, setDur] = useState(0);
  useEffect(() => {
    if (!videoEl) return;
    const on = () => setDur(videoEl.duration || 0);
    videoEl.addEventListener("loadedmetadata", on);
    if (videoEl.duration) setDur(videoEl.duration);
    return () => videoEl.removeEventListener("loadedmetadata", on);
  }, [videoEl]);
  const maxF = Math.max(1, Math.floor(dur * 30));
  const preview = (f) => { if (videoEl) videoEl.currentTime = (f + 0.5) / 30; };
  return html`<div class="synctrack" style="margin:.3rem 0">
    <label class="meta">start
      <input type="range" min="0" max=${maxF} step="1" value=${inFrame || 0}
        oninput=${(e) => { const f = Number(e.target.value); preview(f);
          onChange({ in_frame: f, out_frame: outFrame }); }} />
    </label>
    <label class="meta">end
      <input type="range" min="0" max=${maxF} step="1"
        value=${outFrame == null ? maxF : outFrame}
        oninput=${(e) => { const f = Number(e.target.value); preview(f);
          onChange({ in_frame: inFrame || 0, out_frame: f }); }} />
    </label>
    <span class="meta">${((inFrame || 0) / 30).toFixed(2)}s –
      ${((outFrame == null ? maxF : outFrame) / 30).toFixed(2)}s</span>
  </div>`;
}
