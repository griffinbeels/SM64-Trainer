import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { Icon } from "./icons.js";
import { REPLAY_SPEEDS, setReplaySpeed, stopShuttle } from "../replayshuttle.js";
import { watchReviewCommands } from "../reviewcommands.js";
import { focusReplay } from "../replayfocus.js";
import { seekReviewSource } from "../reviewsource.js";

const html = htm.bind(h);
const stamp = seconds => `${Math.floor(seconds / 60)}:${(seconds % 60).toFixed(2).padStart(5, "0")}`;
const markLabel = value => value == null ? "—" : stamp(value);

export function PlaybackOptions({ video, media, fullscreen }) {
  return html`<div class="replay-playback-options">
      <label class="replay-speed" title="Playback speed"><${Icon} name="speed" size=${20} />
        <select value=${media.rate} aria-label="Playback speed"
        onchange=${e => { setReplaySpeed(video, Number(e.currentTarget.value)); focusReplay(video); }}>
        ${REPLAY_SPEEDS.map(rate => html`<option value=${rate}>${rate}×</option>`)}
      </select></label>
      <div class="replay-sound">
      <button class="replay-mute" title=${media.muted ? "Unmute" : "Mute"} aria-label=${media.muted ? "Unmute" : "Mute"}
        aria-pressed=${media.muted} onclick=${event => {
          if (video) video.muted = !video.muted;
          if (event.detail > 0) focusReplay(video);
        }}>
        <${Icon} name=${media.muted ? "speakerMuted" : "speaker"} size=${22} />
        ${media.muted && html`<${Icon} name="close" size=${17} className="replay-mute-cross" />`}</button>
      <div class="replay-volume-popover">
      <input class="replay-volume" type="range" min="0" max="1" step=".01"
        onpointerup=${() => focusReplay(video)}
        value=${media.volume} aria-label="Volume" oninput=${e => {
          if (video) { video.volume = Number(e.currentTarget.value); video.muted = false; }
        }} /></div></div>
      <button onclick=${fullscreen} title="Fullscreen review with timeline">Fullscreen</button>
    </div>`;
}

export function LoopEditor({ video, media, interval, loop, changeLoop, reviewReady, setError, note }) {
  const [marks, setMarks] = useState({ start: null, end: null });
  const start = loop?.start ?? marks.start, end = loop?.end ?? marks.end;
  const commands = useRef(null);
  commands.current = { range: loop, in: () => mark("start"), out: () => mark("end"), clear: clearLoop,
    start: () => { if (video && loop) { stopShuttle(video); seekReviewSource(video, loop.start); } } };
  useEffect(() => video ? watchReviewCommands(video, () => commands.current) : undefined, [video]);
  function mark(name) {
    if (!interval || !reviewReady) return;
    const next = { start, end, [name]: interval[name] };
    if (next.start !== null && next.end !== null && next.end <= next.start) {
      next[name === "start" ? "end" : "start"] = null;
      changeLoop(null);
    }
    setError(null); setMarks(next);
    if (next.start !== null && next.end !== null)
      changeLoop({ ...next, enabled: true });
  }
  function clearLoop() {
    if (!reviewReady) return;
    setMarks({ start: null, end: null }); changeLoop(null); setError(null);
  }
  return html`<div class="replay-loop-row">
      ${media.shuttle && html`<output class="replay-shuttle-state" aria-live="polite">${media.shuttle}</output>`}
      <button disabled=${!interval || !reviewReady} onclick=${() => mark("start")}
        title="Set In at the start of the displayed picture (I)">Set In</button>
      <span class="replay-media-time">${markLabel(start)}</span>
      <button disabled=${!interval || !reviewReady} onclick=${() => mark("end")}
        title="Set Out at the end of the displayed picture (O)">Set Out</button>
      <span class="replay-media-time">${markLabel(end)}</span>
      <button class=${loop?.enabled ? "is-active" : ""} aria-pressed=${!!loop?.enabled}
        disabled=${!loop || !reviewReady} onclick=${() => changeLoop({ ...loop, enabled: !loop.enabled })}>Loop</button>
      <button disabled=${!reviewReady || (start === null && end === null)}
        title="Remove In and Out (X)" onclick=${clearLoop}>Clear loop</button>
      ${note && html`<span class="replay-frame-note">${note}</span>`}
    </div>`;
}
