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
//
// His words (round 7, item 2): "while paused, the orbit camera should be able to be moving around in
// a circle around mario. This should be a setting (orbit while paused)". With it on, a paused scene's
// orbit turns on the page's own clock instead (interface H's clock_ms: the held picture's time plus
// the time since the pause), asked again every display frame; Play folds that time into the phase,
// so the camera goes on from where it is.
//
// Round 7, item 3 (his words: "more unique camera angles + setups"): Tripod stands where the camera
// on screen stood when you chose it and turns to follow him, Chase rides behind him, Overhead looks
// down at him from a height. Every rig carries its own tilt and lens (the Tilt and lens row,
// scenelens.js), which travel in its request as the game's roll and field of view.
import { h } from "preact";
import htm from "htm";
import { ghostName } from "./sceneghosts.js";
import { SteadyText } from "./steadytext.js";
import { SCENE_CAMERA_DEFAULTS } from "../scenecameratuning.js";

const html = htm.bind(h);

export const RIG_KINDS = [
  ["front", "Front", "A camera in front of him that moves with him, aimed at him"],
  ["side", "Side", "A side view that moves with him, on the side no wall blocks"],
  ["orbit", "Orbit", "A camera circling him as the scene plays"],
  ["chase", "Chase", "A camera behind him that moves with him, aimed at him"],
  ["overhead", "Overhead", "A camera above him looking down, moving with him"],
  ["tripod", "Tripod", "A camera that stays where the picture's camera is now and turns to follow him"],
];
export const SHOT_SIZES = [["close", "Close"], ["medium", "Medium"], ["wide", "Wide"]];
export const SIDE_CHOICES = [["auto", "Whichever is clear"], ["left", "His left"], ["right", "His right"]];
// The page's sliders; the lab takes wider (camera_rig.py HEIGHT_RANGE, RADIUS_RANGE, SPEED_LIMIT).
export const HEIGHT_RANGE = [0, 1500], RADIUS_RANGE = [100, 4000], SPEED_RANGE = [-180, 180];
export const OVERHEAD_RANGE = [200, 6000];
const SHOT_KINDS = new Set(["front", "side", "chase"]);

const KIND_WORDS = Object.fromEntries(RIG_KINDS.map(([kind, words]) => [kind, words]));
const turn = (degrees) => ((degrees % 360) + 360) % 360;

// A rig chosen now, following `target`: the last one's shot, height, side, orbit, tilt and lens
// carried over, so switching between them keeps what you set (a height only between two cameras
// that measure it the same way: Overhead's is how high it looks down from); an orbit's first radius
// and speed are the tuning's, and it goes on circling while paused until that is turned off. A
// tripod's place comes from the camera on screen (SceneLink.useRig).
export function startRig(kind, previous, tuning = SCENE_CAMERA_DEFAULTS, target = "stage") {
  const first = { shot: "medium", height: null, radius: tuning.rigOrbitRadius, speed: tuning.rigOrbitSpeed, phase: 0,
    side: "auto", whilePaused: true, roll: 0, fov: null };
  const kept = Object.fromEntries(Object.entries(first).map(([key, value]) => [key, previous?.[key] ?? value]));
  if ((previous?.kind === "overhead") !== (kind === "overhead")) kept.height = null;
  return { kind, target, ...kept };
}

// The orbit's phase once `heldMs` of a paused orbit have passed: Play then turns on from that angle.
export function orbitFolded(rig, heldMs) {
  return { ...rig, phase: turn(rig.phase + rig.speed * heldMs / 1000) };
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
    rigSideLead: tuning.rigSideLead, rigSideHoldFrames: tuning.rigSideHoldFrames,
    rigSideStayFrames: tuning.rigSideStayFrames, rigWallStandoff: tuning.rigWallStandoff,
    rigPullFrames: tuning.rigPullFrames, rigCraneDistance: tuning.rigCraneDistance,
    rigOrbitRadius: tuning.rigOrbitRadius, rigOrbitSpeed: tuning.rigOrbitSpeed,
    rigLensKeepsSize: tuning.rigLensKeepsSize, rigOverheadHeight: tuning.rigOverheadHeight,
    rigOverheadAngle: tuning.rigOverheadAngle,
  };
}

// The height a rig stands at where none was set: his eye level, Overhead's starting height.
const heightOf = (rig, tuning) => rig.height ?? (rig.kind === "overhead" ? tuning.rigOverheadHeight : tuning.marioHeight);

// The picture request's camera (interface D): {"rig": {...}}, his eye level where no height was set,
// its tilt as the game's roll and its lens as the field of view (the tuning's where none was set),
// and a tripod's place; `clockMs` (interface H) an orbit's own clock while paused, else the
// picture's time turns it.
export function rigRequest(rig, tuning = SCENE_CAMERA_DEFAULTS, clockMs = null) {
  const { kind, target, shot, radius, speed, phase, side } = rig;
  return { rig: { kind, target, shot, height: heightOf(rig, tuning), radius, speed, phase, side,
    roll: rig.roll ?? 0, fov: rig.fov ?? tuning.rigFov,
    ...(kind === "tripod" ? { position: rig.position } : {}),
    tuning: rigTuning(tuning), ...(clockMs == null ? {} : { clock_ms: clockMs }) } };
}

const runnerName = (rig, ghosts) => (rig.target === "stage" ? "Mario" : ghostName(ghosts, rig.target));

// The picture's corner: "Front · Mario", "Orbit · Sully".
export function rigName(rig, ghosts) {
  return `${KIND_WORDS[rig.kind]} · ${runnerName(rig, ghosts)}`;
}

// What the hint says while a cinematic camera is on.
export function rigHint(rig, ghosts) {
  const who = runnerName(rig, ghosts);
  const how = { orbit: `circles ${who}`, side: `tracks ${who} from the side`, chase: `rides behind ${who}`,
    overhead: `looks down at ${who} from above`, tripod: `stays where it stands and turns to follow ${who}` }[rig.kind]
    ?? `rides in front of ${who}`;
  return `The camera ${how} as the scene plays, steps and seeks. Z and C tilt it, X levels it, - and = change its lens. Moving the camera starts Free camera from here; R returns to the recorded camera.`;
}

const heightWords = (rig) => (rig.height != null ? `${Math.round(rig.height)}`
  : rig.kind === "overhead" ? "Default" : "Eye level");
// Each setting's readout at its widest (round 9, item 3: a value changing never moves the row).
const SPEED_SIZES = ["-000°/s"], RADIUS_SIZES = ["0000"], HEIGHT_SIZES = ["0000", "Default", "Eye level"];

// One camera's settings: its shot size, side, speed, radius, height and orbit-while-paused.
function rigSettings(rig, link, tuning) {
  const range = (label, value, [min, max], step, set, words, sizes) => html`<label class="scene-lab-rig-range">${label}
    <input type="range" min=${min} max=${max} step=${step} value=${value} aria-label=${label}
      oninput=${(event) => set(Number(event.currentTarget.value), event.timeStamp)} />
    <output><${SteadyText} text=${words} sizes=${sizes} /></output></label>`;
  return html`
    ${SHOT_KINDS.has(rig.kind) && html`<div class="scene-lab-rig-shots" role="group" aria-label="Shot size">
      ${SHOT_SIZES.map(([shot, words]) => html`<button type="button" key=${shot} aria-pressed=${rig.shot === shot}
          onclick=${(event) => link.setRig({ shot }, event.timeStamp)}>${words}</button>`)}</div>`}
    ${rig.kind === "side" && html`<label>Side
      <select value=${rig.side} onchange=${(event) => link.setRig({ side: event.currentTarget.value }, event.timeStamp)}>
        ${SIDE_CHOICES.map(([side, words]) => html`<option key=${side} value=${side}>${words}</option>`)}
      </select></label>`}
    ${rig.kind === "orbit" && range("Speed", rig.speed, SPEED_RANGE, 1, (speed, time) => link.setRig({ speed }, time),
      `${Math.round(rig.speed)}°/s`, SPEED_SIZES)}
    ${rig.kind === "orbit" && range("Radius", rig.radius, RADIUS_RANGE, 10, (radius, time) => link.setRig({ radius }, time),
      `${Math.round(rig.radius)}`, RADIUS_SIZES)}
    ${rig.kind !== "tripod" && range("Height", heightOf(rig, tuning), rig.kind === "overhead" ? OVERHEAD_RANGE : HEIGHT_RANGE,
      5, (height, time) => link.setRig({ height }, time), heightWords(rig), HEIGHT_SIZES)}
    ${rig.kind !== "tripod" && html`<button type="button" disabled=${rig.height == null}
      onclick=${(event) => link.setRig({ height: null }, event.timeStamp)}
      title=${rig.kind === "overhead" ? "Back to the height it starts at" : "Back to his eye level"}>${rig.kind === "overhead"
        ? "Default height" : "Eye level"}</button>`}
    ${rig.kind === "orbit" && html`<button type="button" aria-pressed=${rig.whilePaused}
      onclick=${(event) => link.setRig({ whilePaused: !rig.whilePaused }, event.timeStamp)}
      title="Keep circling him while the scene is paused">Orbit while paused</button>`}
    ${rig.kind === "tripod" && html`<span class="scene-lab-rig-note">To move it, fly the free camera somewhere, then choose Tripod again.</span>`}`;
}

// The Cinematic row: the cameras and whom they follow (their tilt and lens are the row below,
// scenelens.js). The camera on screen's settings are CinematicSettings, on the picture.
export function CinematicBar({ link, tuning }) {
  const available = link.freeAvailable, reason = link.scene.free_camera.reason, rig = link.rig;
  const runners = [["stage", "Mario"], ...link.ghosts.map((ghost) => [ghost.ref, ghostName(link.ghosts, ghost.ref)])];
  return html`<div class="scene-lab-bar scene-lab-rigs" aria-label="Cinematic cameras">
    ${RIG_KINDS.map(([kind, words, what]) => html`<button type="button" key=${kind} disabled=${!available}
        aria-pressed=${rig?.kind === kind} title=${available ? what : reason}
        onclick=${(event) => link.useRig(kind, event.timeStamp)}>${words}</button>`)}
    <label class="scene-lab-rig-follow">Follow
      <select value=${link.follow} disabled=${!available} title="Whom the cinematic cameras follow"
          onchange=${(event) => link.followWith(event.currentTarget.value, event.timeStamp)}>
        ${runners.map(([ref, name]) => html`<option key=${ref} value=${ref}>${name}</option>`)}
      </select></label>
  </div>`;
}

// The cinematic camera on screen's settings (shot size, side, speed, radius, height), on the picture
// with its notices (sceneviewer.js's StageOverlays) while one is on, none otherwise. On the picture so
// they take no row: a row of settings that came and went moved everything under it, and one kept for
// them stood empty with no camera on (round 9, item 3, and the lead's review of it).
export function CinematicSettings({ link, tuning }) {
  const rig = link.rig;
  return rig ? html`<div class="scene-lab-bar scene-lab-rig-options" aria-label="Cinematic camera settings">
    ${rigSettings(rig, link, tuning)}</div>` : null;
}
