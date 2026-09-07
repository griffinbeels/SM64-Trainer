// Shared controls; each player supplies the timing its source actually supports.
import { h } from "preact";
import htm from "htm";
import { Icon } from "./icons.js";

const html = htm.bind(h);

export function ReplayTransport({ playing, onStart, onStep, onToggle,
                                  stepHandlers = null, startTitle = "Jump to the beginning",
                                  canStep = true, frameKind = "game", note = null }) {
  const handlers = (direction) => stepHandlers ? stepHandlers(direction)
    : { onclick: () => onStep(direction) };
  const stepTitle = (direction) => canStep
    ? `Pause and move ${direction} one ${frameKind} frame${stepHandlers ? "; hold to keep going" : ""}`
    : "Frame stepping unavailable for this recording";
  return html`<div class="replay-transport">
    <button onclick=${onStart} title=${startTitle}>
      <${Icon} name="restart" size=${15} /> Start
    </button>
    <button ...${handlers(-1)} disabled=${!canStep} title=${stepTitle("back")}>
      <${Icon} name="stepBack" size=${15} /> Back 1
    </button>
    <button class="primary-transport" onclick=${onToggle} title="Play or pause">
      <${Icon} name=${playing ? "pause" : "play"} size=${16} />
      ${playing ? "Pause" : "Play"}
    </button>
    <button ...${handlers(1)} disabled=${!canStep} title=${stepTitle("forward")}>
      <${Icon} name="stepForward" size=${15} /> Forward 1
    </button>
    ${note && html`<span class="replay-frame-note">${note}</span>`}
  </div>`;
}
