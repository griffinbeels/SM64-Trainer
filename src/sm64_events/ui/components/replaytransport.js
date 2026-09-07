// One complete transport below the image for captured and downloaded recordings.
import { h } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { Icon } from "./icons.js";
import { pictureInterval, useReviewMedia } from "../reviewmedia.js";

const html = htm.bind(h);
const stamp = seconds => `${Math.floor(seconds / 60)}:${(seconds % 60).toFixed(2).padStart(5, "0")}`;

export function ReplayTransport({ video = null, clock = null, frameStep = null,
    playing, onStart, onStep, onToggle, stepHandlers = null,
    startTitle = "Jump to the beginning", canStep = true, frameKind = "game", note = null,
    loop: controlledLoop, onLoopChange, reviewReady = true }) {
  const [localLoop, setLocalLoop] = useState(null);
  const [marks, setMarks] = useState({ start: null, end: null });
  const [error, setError] = useState(null);
  const loop = controlledLoop === undefined ? localLoop : controlledLoop;
  const media = useReviewMedia(video, { clock, step: frameStep, loop });
  const interval = pictureInterval(media.picture, clock, frameStep, media.duration);
  const handlers = (direction) => stepHandlers ? stepHandlers(direction)
    : { onclick: () => onStep(direction) };
  const stepTitle = (direction) => canStep
    ? `Pause and move ${direction} one ${frameKind} frame${stepHandlers ? "; hold to keep going" : ""}`
    : "Frame stepping unavailable for this recording";
  function changeLoop(next) {
    if (onLoopChange) onLoopChange(next); else setLocalLoop(next);
  }
  function mark(name) {
    if (!interval) return;
    const next = { start: loop?.start ?? marks.start, end: loop?.end ?? marks.end,
      [name]: interval[name] };
    if (next.start !== null && next.end !== null && next.end <= next.start) {
      setError("B must be after A. Choose another picture."); return;
    }
    setError(null); setMarks(next);
    if (next.start !== null && next.end !== null)
      changeLoop({ ...next, enabled: loop?.enabled ?? false });
  }
  async function fullscreen() {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await video?.closest(".replay-player")?.requestFullscreen();
    } catch { setError("Fullscreen is unavailable in this window."); }
  }
  return html`<div class="replay-controls">
    <div class="replay-seek-row">
      <span class="replay-media-time">${stamp(media.time)}</span>
      <input type="range" min="0" max=${media.duration || 1} step="any"
        value=${media.time} disabled=${!media.duration} aria-label="Seek recording"
        oninput=${e => { if (video) video.currentTime = Number(e.currentTarget.value); }} />
      <span class="replay-media-time">${stamp(media.duration)}</span>
    </div>
    <div class="replay-control-row"><div class="replay-transport">
    <button onclick=${onStart} title=${startTitle}>
      <${Icon} name="restart" size=${15} /> Start
    </button>
    <button ...${handlers(-1)} disabled=${!canStep} title=${stepTitle("back")}>
      <${Icon} name="stepBack" size=${15} /> Back 1
    </button>
    <button class="primary-transport" onclick=${onToggle} title="Play or pause (Space)">
      <${Icon} name=${playing ? "pause" : "play"} size=${16} />
      ${playing ? "Pause" : "Play"}
    </button>
    <button ...${handlers(1)} disabled=${!canStep} title=${stepTitle("forward")}>
      <${Icon} name="stepForward" size=${15} /> Forward 1
    </button>
    </div><div class="replay-playback-options">
      <label class="replay-speed">Speed <select value=${media.rate} aria-label="Playback speed"
        onchange=${e => { if (video) video.playbackRate = Number(e.currentTarget.value); }}>
        ${[.1, .25, .5, .75, 1, 1.5, 2].map(rate => html`<option value=${rate}>${rate}×</option>`)}
      </select></label>
      <button title=${media.muted ? "Unmute" : "Mute"} aria-label=${media.muted ? "Unmute" : "Mute"}
        aria-pressed=${media.muted} onclick=${() => { if (video) video.muted = !video.muted; }}>
        ${media.muted ? "Muted" : "Sound"}</button>
      <input class="replay-volume" type="range" min="0" max="1" step=".01"
        value=${media.volume} aria-label="Volume" oninput=${e => {
          if (video) { video.volume = Number(e.currentTarget.value); video.muted = false; }
        }} />
      <button onclick=${fullscreen} title="Fullscreen player and controls">Fullscreen</button>
    </div></div>
    <div class="replay-loop-row">
      <button disabled=${!interval || !reviewReady} onclick=${() => mark("start")}
        title="Set A at the start of the displayed picture">Set A</button>
      <span class="replay-media-time">${(loop?.start ?? marks.start) == null ? "—" : stamp(loop?.start ?? marks.start)}</span>
      <button disabled=${!interval || !reviewReady} onclick=${() => mark("end")}
        title="Set B at the end of the displayed picture">Set B</button>
      <span class="replay-media-time">${(loop?.end ?? marks.end) == null ? "—" : stamp(loop?.end ?? marks.end)}</span>
      <button class=${loop?.enabled ? "is-active" : ""} aria-pressed=${!!loop?.enabled}
        disabled=${!loop || !reviewReady} onclick=${() => changeLoop({ ...loop, enabled: !loop.enabled })}>Loop</button>
      <button disabled=${!reviewReady || (!loop && marks.start === null && marks.end === null)}
        onclick=${() => { setMarks({ start: null, end: null }); changeLoop(null); setError(null); }}>Clear loop</button>
      ${note && html`<span class="replay-frame-note">${note}</span>`}
    </div>
    ${error && html`<p class="replay-control-error" role="status">${error}</p>`}
  </div>`;
}
