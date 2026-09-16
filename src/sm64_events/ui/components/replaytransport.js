// One complete transport below the image for captured and downloaded recordings.
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { Icon } from "./icons.js";
import { pictureInterval, useReviewMedia } from "../reviewmedia.js";
import { stopShuttle } from "../replayshuttle.js";
import { LoopEditor, PlaybackOptions } from "./replayoptions.js";
import { focusReplay } from "../replayfocus.js";
import { scrubReviewSource } from "../reviewsource.js";
import { cancelReviewSeek } from "../reviewseek.js";

const html = htm.bind(h);
const stamp = seconds => `${Math.floor(seconds / 60)}:${(seconds % 60).toFixed(2).padStart(5, "0")}`;

export function ReplayTransport({ video, clock = null, frameStep = null,
    playing, onStart, onStep, onToggle, stepHandlers = null,
    startTitle = "Jump to the beginning", canStep = true, frameKind = "game", note = null,
    loop: controlledLoop, onLoopChange, reviewReady = true }) {
  const [localLoop, setLocalLoop] = useState(null);
  const [error, setError] = useState(null);
  const loop = controlledLoop === undefined ? localLoop : controlledLoop;
  const media = useReviewMedia(video, { clock, step: frameStep, loop });
  useEffect(() => () => cancelReviewSeek(video), [video]);
  // Replacing a source resets paused without necessarily emitting pause.
  const isPlaying = video ? media.playing : playing;
  const interval = pictureInterval(media.picture, clock, frameStep, media.duration);
  const handlers = (direction) => stepHandlers ? stepHandlers(direction)
    : { onclick: () => onStep(direction) };
  const stepTitle = (direction) => canStep
    ? `Pause and move ${direction} one ${frameKind} frame${stepHandlers ? "; hold to keep going" : ""}`
    : "Frame stepping unavailable for this recording";
  function changeLoop(next) {
    if (onLoopChange) onLoopChange(next); else setLocalLoop(next);
  }
  async function fullscreen() {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else {
        const drawer = video?.closest(".attempt-drawer");
        await (drawer?.querySelector(".attempt-drawer-inputs") ? drawer : video?.closest(".replay-player"))?.requestFullscreen();
      }
    } catch { setError("Fullscreen is unavailable in this window."); }
  }
  return html`<div class="replay-controls">
    <div class="replay-seek-row">
      <input type="range" min="0" max=${media.duration || 1} step="any"
        value=${media.time} disabled=${!media.duration} aria-label="Seek recording"
        onpointerup=${() => focusReplay(video)}
        oninput=${e => { if (video) { stopShuttle(video); scrubReviewSource(video, Number(e.currentTarget.value)); } }} />
      <span class="replay-media-time replay-time-pair">${stamp(media.time)} / ${stamp(media.duration)}</span>
    </div>
    <div class="replay-control-row"><div class="replay-transport">
    <button onclick=${onStart} title=${startTitle}>
      <${Icon} name="restart" size=${15} /> Start
    </button>
    <button ...${handlers(-1)} disabled=${!canStep} title=${stepTitle("back")}>
      <${Icon} name="stepBack" size=${15} /> Back 1
    </button>
    <button class="primary-transport" onclick=${onToggle} title="Play or pause (Space)">
      <${Icon} name=${isPlaying ? "pause" : "play"} size=${16} />
      ${isPlaying ? "Pause" : "Play"}
    </button>
    <button ...${handlers(1)} disabled=${!canStep} title=${stepTitle("forward")}>
      <${Icon} name="stepForward" size=${15} /> Forward 1
    </button>
    </div><${PlaybackOptions} video=${video} media=${media} fullscreen=${fullscreen} /></div>
    <${LoopEditor} video=${video} media=${media} interval=${interval} loop=${loop} changeLoop=${changeLoop}
      reviewReady=${reviewReady} setError=${setError} note=${note} />
    ${error && html`<p class="replay-control-error" role="status">${error}</p>`}
  </div>`;
}
