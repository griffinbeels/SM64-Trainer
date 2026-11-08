// src/sm64_events/ui/components/sceneplaybar.js -- the Ghost Lab's play bar (round 9, items 3 and 5; lab only).
//
// His words: "the play bar is getting longer and shorter. This is really frustrating to use. The UI should not
// be bouncing around like that when we go between playing / paused." One bar of fixed parts under the picture,
// in the order a video player puts them: Restart, the step back to the last move, one frame back, Play, one
// frame on, the step to the next move; the slider and what is on screen (the frame, and the move of whoever
// Follow names); the Play speed (1x, ½x, ¼x), Loop, and the sound (its button opens the volume, Music and Sound
// effects on top of the page, so they take no row). Every part has its size whatever it says (SteadyText),
// and where the bar wraps (narrow panes: the slider takes its own row) depends on the pane's width alone.
// While a watched run is being made into a scene, its progress sits on the picture's foot just above the bar
// (scenebuild.js), so the bar does not move then either.
import { h } from "preact";
import { useEffect } from "preact/hooks";
import htm from "htm";
import { Icon } from "./icons.js";
import { usePop } from "./scenepop.js";
import { ACTION_SIZES, ActionButton, actionLine, useActions } from "./sceneactions.js";
import { NO_SOUND, SoundBar } from "./sceneaudio.js";
import { BuildProgress } from "./scenebuild.js";
import { PlaySpeeds } from "./scenepathbar.js";
import { SteadyText } from "./steadytext.js";

const html = htm.bind(h);
const PLAY_WORDS = ["Play", "Pause"];
const FRAME_SIZE = ["Frame 00000 of 00000+"];

// Where a frame is, in words, for the readout's hover: "level 9, area 1, game frame 5418".
function frameWords(frame) {
  if (!frame) return "";
  return [frame.level != null && `level ${frame.level}`, frame.area != null && `area ${frame.area}`,
    frame.game_frame != null && `game frame ${frame.game_frame}`].filter(Boolean).join(", ");
}

// An icon button with its name for a reader and its hover words.
function Tool({ name, label, title, onclick, pressed = null, disabled = false, size = 16 }) {
  return html`<button type="button" class="icon-button" aria-label=${label} title=${title || label}
      aria-pressed=${pressed ?? undefined} disabled=${disabled} onclick=${onclick}>
    <${Icon} name=${name} size=${size} /></button>`;
}

// The sound: one button (M) that opens the volume, Music and Sound effects over the page; Esc or a click
// elsewhere closes it. Without sound the button says why and opens nothing.
function SoundButton({ link }) {
  const { open, setOpen, place } = usePop(), sound = link.sound;
  useEffect(() => sound.cue(link.frame), [sound, link.frame]);     // the sound around the frame, ready for Play
  const quiet = !sound.available, muted = sound.muted;
  return html`<span class="ghost-lab-sound" ref=${place}>
    <button type="button" class="icon-button" aria-expanded=${open} aria-label="Sound"
        title=${quiet ? sound.reason || NO_SOUND : `Volume, music and sound effects${muted ? " (muted: M)" : " (M mutes)"}`}
        onclick=${() => setOpen(!open)}>
      <${Icon} name=${quiet || muted ? "speakerMuted" : "speaker"} size=${16} /></button>
    ${open && html`<div class="ghost-lab-pop ghost-lab-sound-pop">
      <${SoundBar} sound=${sound} frame=${link.frame} />
    </div>`}
  </span>`;
}

// The slider and the readout: "Frame 312 of 604" and the move on screen; the frame's level, area and
// game frame on hover.
function Scrub({ link }) {
  const frames = link.scene.frames, line = actionLine(link);
  const words = `Frame ${link.frame + 1} of ${frames.length}${link.scene.growing ? "+" : ""}`;
  return html`<div class="scene-lab-scrub">
    <input type="range" min="0" max=${Math.max(0, frames.length - 1)} value=${link.frame} aria-label="Frame"
      oninput=${(event) => { link.pause(); link.seek(Number(event.currentTarget.value), "input", event.timeStamp); }} />
    <span class="scene-lab-frame" title=${[words, frameWords(frames[link.frame])].filter(Boolean).join(" · ")}>
      <${SteadyText} text=${words} sizes=${FRAME_SIZE} />
      <small title=${line.title || line.text}><${SteadyText} text=${line.text} sizes=${ACTION_SIZES} /></small></span>
  </div>`;
}

// `building`: the run being made into a scene ({ref, name, progress}), or null.
export function PlayBar({ link, building = null }) {
  useActions(link);
  return html`<div class="scene-lab-bar ghost-lab-play" aria-label="Playback">
    ${building && html`<${BuildProgress} build=${building} />`}
    <div class="ghost-lab-play-steps">
      <${Tool} name="restart" label="Restart" title=${link.playing ? "Play again from the first frame" : "Go to the first frame"}
        onclick=${(event) => link.restart(event.timeStamp)} />
      <${ActionButton} link=${link} direction=${-1} />
      <${Tool} name="stepBack" label="Back 1" title="Back one frame (Left arrow or ,)"
        onclick=${(event) => link.step(-1, event.timeStamp)} />
      <button type="button" class="primary ghost-lab-play-main" onclick=${() => link.toggle()} title="Play or pause (Space)">
        <${Icon} name=${link.playing ? "pause" : "play"} size=${18} />
        <span class="sr-only"><${SteadyText} text=${link.playing ? "Pause" : "Play"} sizes=${PLAY_WORDS} /></span></button>
      <${Tool} name="stepForward" label="Forward 1" title="Forward one frame (Right arrow or .)"
        onclick=${(event) => link.step(1, event.timeStamp)} />
      <${ActionButton} link=${link} direction=${1} />
    </div>
    <${Scrub} link=${link} />
    <div class="ghost-lab-play-end">
      <${PlaySpeeds} link=${link} />
      <${Tool} name="loop" label="Loop" pressed=${link.loop} title="When on, Play goes on from the first frame after the last"
        onclick=${() => link.setLoop(!link.loop)} />
      <${SoundButton} link=${link} />
    </div>
  </div>`;
}
