// src/sm64_events/ui/components/scenerigs.js -- the Scene viewer's cinematic cameras (M4 round 6, item 1).
//
// His words: "a bunch of different camera effects that look REALLY cool". Front rides in front of
// the runner, aimed at him, in a close, medium or wide shot; Side tracks him in profile, on the side
// no wall blocks; Orbit circles him at a speed and radius of your choosing. Each stands at his eye
// level until you raise or lower it, and follows the stage's Mario or any ghost in the scene.
//
// The lab works out every picture's camera from that runner's path (tools/scene_lab/camera_rig.py:
// the path smoothed, the stage's own walls), so the page only says which camera and its settings,
// and the Tune controls' Cinematic numbers travel with every request (rigRequest): one request is
// the whole camera, and the same request draws the same picture when stepping back, seeking or
// exporting. Every picture's header names the camera the lab drew (camera_used): Free camera starts
// there. An orbit's angle is its phase plus its speed times the picture's time on the scene's own
// clock, so choosing Orbit, or a new speed, picks the phase that keeps the camera where it is.
import { h } from "preact";
import htm from "htm";
import { ghostName } from "./sceneghosts.js";
import { SCENE_CAMERA_DEFAULTS } from "../scenecameratuning.js";

const html = htm.bind(h);

export const RIG_KINDS = [
  ["front", "Front", "A camera in front of him that moves with him, aimed at him"],
  ["side", "Side", "A side view that moves with him, on the side no wall blocks"],
  ["orbit", "Orbit", "A camera circling him as the scene plays"],
];
export const SHOT_SIZES = [["close", "Close"], ["medium", "Medium"], ["wide", "Wide"]];
export const SIDE_CHOICES = [["auto", "Whichever is clear"], ["left", "His left"], ["right", "His right"]];
// The page's sliders; the lab takes wider (camera_rig.py HEIGHT_RANGE, RADIUS_RANGE, SPEED_LIMIT).
export const HEIGHT_RANGE = [0, 1500], RADIUS_RANGE = [100, 4000], SPEED_RANGE = [-180, 180];

const KIND_WORDS = Object.fromEntries(RIG_KINDS.map(([kind, words]) => [kind, words]));
const turn = (degrees) => ((degrees % 360) + 360) % 360;

// A rig chosen now, following `target`: the last one's shot, height, side and orbit carried over, so
// switching between Front, Side and Orbit keeps what you set; an orbit's first radius and speed are
// the tuning's.
export function startRig(kind, previous, tuning = SCENE_CAMERA_DEFAULTS, target = "stage") {
  return { kind, target, shot: previous?.shot ?? "medium", height: previous?.height ?? null,
    radius: previous?.radius ?? tuning.rigOrbitRadius, speed: previous?.speed ?? tuning.rigOrbitSpeed,
    phase: previous?.phase ?? 0, side: previous?.side ?? "auto" };
}

// A change of settings; a new speed keeps the orbit's angle at `seconds` (the picture on screen).
export function rigChanged(rig, changes, seconds) {
  const next = { ...rig, ...changes };
  if ("speed" in changes && changes.speed !== rig.speed) next.phase = turn(rig.phase + (rig.speed - next.speed) * seconds);
  return next;
}

// What the picture on screen tells a new orbit: where its camera stands (any camera the lab drew),
// and the runner it saw when it was a cinematic camera following the same one; null where it cannot.
export function seenOnScreen(shown, target) {
  const used = shown?.camera_used ?? null;
  return { from: used?.position ?? null, target: used && shown.rig?.target === target ? used.focus : null };
}

// The phase that puts an orbit's camera, at `seconds`, where `from` stands as seen from `target`
// (the lab's angle: 0 on the +z side, turning toward +x); 0 when either is unknown.
export function orbitPhase(from, target, speed, seconds) {
  if (!from || !target) return 0;
  const bearing = Math.atan2(from[0] - target[0], from[2] - target[2]) * 180 / Math.PI;
  return turn(bearing - speed * seconds);
}

// The Tune controls' rows a rig reads, sent with every request so the lab draws what the panel shows.
export function rigTuning(tuning = SCENE_CAMERA_DEFAULTS) {
  return {
    rigCloseDistance: tuning.rigCloseDistance, rigMediumDistance: tuning.rigMediumDistance,
    rigWideDistance: tuning.rigWideDistance, marioHeight: tuning.marioHeight, rigSmoothFrames: tuning.rigSmoothFrames,
    rigAimFrames: tuning.rigAimFrames, rigStillSpeed: tuning.rigStillSpeed, rigFov: tuning.rigFov,
    rigSideLead: tuning.rigSideLead, rigSideHoldFrames: tuning.rigSideHoldFrames, rigWallStandoff: tuning.rigWallStandoff,
    rigPullFrames: tuning.rigPullFrames, rigCraneDistance: tuning.rigCraneDistance,
    rigOrbitRadius: tuning.rigOrbitRadius, rigOrbitSpeed: tuning.rigOrbitSpeed,
  };
}

// The picture request's camera (interface D): {"rig": {...}}, his eye level where no height was set.
export function rigRequest(rig, tuning = SCENE_CAMERA_DEFAULTS) {
  const { kind, target, shot, radius, speed, phase, side } = rig;
  return { rig: { kind, target, shot, height: rig.height ?? tuning.marioHeight, radius, speed, phase, side,
    tuning: rigTuning(tuning) } };
}

const runnerName = (rig, ghosts) => (rig.target === "stage" ? "Mario" : ghostName(ghosts, rig.target));

// The picture's corner: "Front · Mario", "Orbit · Sully".
export function rigName(rig, ghosts) {
  return `${KIND_WORDS[rig.kind]} · ${runnerName(rig, ghosts)}`;
}

// What the hint says while a cinematic camera is on.
export function rigHint(rig, ghosts) {
  const who = runnerName(rig, ghosts);
  const how = rig.kind === "orbit" ? `circles ${who}` : rig.kind === "side" ? `tracks ${who} from the side`
    : `rides in front of ${who}`;
  return `The camera ${how} as the scene plays, steps and seeks. Moving the camera starts Free camera from here; R returns to the recorded camera.`;
}

const heightWords = (rig) => (rig.height == null ? "Eye level" : `${Math.round(rig.height)}`);

// The Cinematic row: the three cameras and whom they follow; with one on, its settings under it.
export function CinematicBar({ link, tuning }) {
  const available = link.freeAvailable, reason = link.scene.free_camera.reason, rig = link.rig;
  const runners = [["stage", "Mario"], ...link.ghosts.map((ghost) => [ghost.ref, ghostName(link.ghosts, ghost.ref)])];
  const range = (label, value, [min, max], step, set, words) => html`<label class="scene-lab-rig-range">${label}
    <input type="range" min=${min} max=${max} step=${step} value=${value} aria-label=${label}
      oninput=${(event) => set(Number(event.currentTarget.value), event.timeStamp)} />
    <output>${words}</output></label>`;
  return html`<div class="scene-lab-bar scene-lab-rigs" aria-label="Cinematic cameras">
    <span class="scene-lab-rigs-name">Cinematic</span>
    ${RIG_KINDS.map(([kind, words, what]) => html`<button type="button" key=${kind} disabled=${!available}
        aria-pressed=${rig?.kind === kind} title=${available ? what : reason}
        onclick=${(event) => link.useRig(kind, event.timeStamp)}>${words}</button>`)}
    <label class="scene-lab-rig-follow">Follow
      <select value=${link.follow} disabled=${!available} title="Whom the cinematic cameras follow"
          onchange=${(event) => link.followWith(event.currentTarget.value, event.timeStamp)}>
        ${runners.map(([ref, name]) => html`<option key=${ref} value=${ref}>${name}</option>`)}
      </select></label>
  </div>
  ${rig && html`<div class="scene-lab-bar scene-lab-rig-options" aria-label="Cinematic camera settings">
    ${rig.kind !== "orbit" && html`<div class="scene-lab-rig-shots" role="group" aria-label="Shot size">
      ${SHOT_SIZES.map(([shot, words]) => html`<button type="button" key=${shot} aria-pressed=${rig.shot === shot}
          onclick=${(event) => link.setRig({ shot }, event.timeStamp)}>${words}</button>`)}</div>`}
    ${rig.kind === "side" && html`<label>Side
      <select value=${rig.side} onchange=${(event) => link.setRig({ side: event.currentTarget.value }, event.timeStamp)}>
        ${SIDE_CHOICES.map(([side, words]) => html`<option key=${side} value=${side}>${words}</option>`)}
      </select></label>`}
    ${rig.kind === "orbit" && range("Speed", rig.speed, SPEED_RANGE, 1, (speed, time) => link.setRig({ speed }, time),
      `${Math.round(rig.speed)}°/s`)}
    ${rig.kind === "orbit" && range("Radius", rig.radius, RADIUS_RANGE, 10, (radius, time) => link.setRig({ radius }, time),
      `${Math.round(rig.radius)}`)}
    ${range("Height", rig.height ?? tuning.marioHeight, HEIGHT_RANGE, 5, (height, time) => link.setRig({ height }, time),
      heightWords(rig))}
    <button type="button" disabled=${rig.height == null} onclick=${(event) => link.setRig({ height: null }, event.timeStamp)}
      title="Back to his eye level">Eye level</button>
  </div>`}`;
}
