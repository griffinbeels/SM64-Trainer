// src/sm64_events/ui/components/scenelens.js -- the Scene viewer's Tilt and lens row (M4 round 7, item 3).
//
// His words: "I want to be able to support dutch angles / more unique camera angles + setups to be
// able to maximize the potential of the system. I want to be able to allow users to basically do
// whatever angle they want / tilt the camera / adjust focal length / etc".
//
// One row for whichever camera is on screen: a cinematic camera's own tilt and lens (they travel in
// its request), else the free camera's; moving either from the recorded camera or a ghost's view
// starts Free camera from the picture on screen, as W A S D do. Tilt is degrees, positive turning
// the picture clockwise; Lens is the 35 mm-equivalent focal length, on a slider that moves as far
// for each doubling (scenecamera.js owns both conversions). The keys do the same while held: Z and C
// tilt, X levels, - and = change the lens, and so does Ctrl with the wheel.
import { h } from "preact";
import htm from "htm";
import { GAME_FOV, LENS_RANGE, TILT_RANGE, lensOf, tiltOf } from "./scenecamera.js";

const html = htm.bind(h);
const STEPS = 1000;                                          // the Lens slider's positions
const [SHORTEST, LONGEST] = LENS_RANGE;
export const lensPosition = (mm) => Math.round(STEPS * Math.log(mm / SHORTEST) / Math.log(LONGEST / SHORTEST));
export const lensAt = (position) => SHORTEST * (LONGEST / SHORTEST) ** (position / STEPS);
export const GAME_LENS = lensOf(GAME_FOV);

// The tilt and lens the row shows: the cinematic camera's, the free camera's, else those of the
// camera the lab drew the picture on screen from, else level at the game's lens.
export function lensNow(link, tuning) {
  if (link.rig) return { roll: link.rig.roll ?? 0, fov: link.rig.fov ?? tuning.rigFov };
  if (link.free && link.orbit) return { roll: link.orbit.roll || 0, fov: link.orbit.fov ?? GAME_FOV };
  const used = link.shown?.camera_used;
  return { roll: used?.roll ?? 0, fov: used?.fov ?? GAME_FOV };
}

export function LensBar({ link, tuning }) {
  const { roll, fov } = lensNow(link, tuning), tilt = Math.round(tiltOf(roll)), mm = lensOf(fov);
  const game = Math.round(mm) === Math.round(GAME_LENS);
  return html`<div class="scene-lab-bar scene-lab-lens" aria-label="Tilt and lens">
    <label class="scene-lab-rig-range" title="Turns the picture: right is clockwise (hold Z or C)">Tilt
      <input type="range" min=${TILT_RANGE[0]} max=${TILT_RANGE[1]} step="1" value=${tilt} aria-label="Tilt"
        oninput=${(event) => link.setTilt(Number(event.currentTarget.value), event.timeStamp)} />
      <output>${tilt}°</output></label>
    <button type="button" disabled=${!tilt} onclick=${(event) => link.setTilt(0, event.timeStamp)}
      title="Back to level (X)">Level</button>
    <label class="scene-lab-rig-range" title="Wider to the left, closer in to the right (hold - or =, or Ctrl with the wheel)">Lens
      <input type="range" min="0" max=${STEPS} step="1" value=${lensPosition(mm)} aria-label="Lens"
        oninput=${(event) => link.setLens(lensAt(Number(event.currentTarget.value)), event.timeStamp)} />
      <output>${Math.round(mm)} mm${game ? " · game's" : ""}</output></label>
    <button type="button" disabled=${game} onclick=${(event) => link.setLens(GAME_LENS, event.timeStamp)}
      title=${`Back to the game's own lens (${Math.round(GAME_LENS)} mm, ${GAME_FOV}° tall)`}>Game's lens</button>
  </div>`;
}
