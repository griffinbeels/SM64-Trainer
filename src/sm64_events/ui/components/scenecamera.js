// src/sm64_events/ui/components/scenecamera.js -- the Scene viewer's free camera
// (M4 E3, lab only): every place it can be and every way it moves, in the
// game's world coordinates. The one owner of the camera's math on the page:
// the server takes the camera these functions make and only checks it
// (tools/scene_lab/scene_session.py world_camera), and a Set focus point comes
// back in the camera's own axes for worldPoint here to place.
//
// A free camera is an orbit: the focus point, yaw around the world's up axis,
// pitch above the horizon, distance, and the screen roll (the game's s16
// angle) and field of view (degrees, or null for the frame's own) it draws
// with. cameraOf turns it into what the server draws. The horizon stays level
// and the focus point stays put unless a movement moves it; nothing here
// collides with the stage, accelerates or coasts. Every movement takes the
// tuning (scenecameratuning.js) as its last argument, the shipped values when
// left out.
//
// Tilt and Lens (round 7) are the page's words for the roll and the field of
// view, converted here and nowhere else. A tilt you set stays as you move
// (`levels: false`); only one that came with a recorded picture (a shake)
// levels. Turning, looking and flight keep to the world's horizon while the
// picture is tilted, as a camera on a level tripod head with a tilted plate:
// W A S D stay level with the ground and turning never makes the horizon
// drift, where axes tilted with the picture would climb or dive as you strafe.
// Only a slide, which grabs the scene, follows the pointer across the tilted
// picture.
import { SCENE_CAMERA_DEFAULTS } from "../scenecameratuning.js";

export const FULL_TURN = 65536;                 // the game's angle units in a turn
export const SPEED_RANGE = [0.05, 20];          // the flight speed control's multiplier
const PER_DEGREE = FULL_TURN / 360;

// Tilt is degrees, positive turning the picture clockwise. The game's screen
// roll turns it the other way (drawn on the renderer 2026-10-28: roll +5461,
// 30 degrees, turned HMC's picture counterclockwise), so a tilt is the roll
// with its sign changed. Lens is the focal length on 35 mm film that sees as
// much, matched by the picture's diagonal (a 4:3 picture's is 5/3 of its
// height), the way a camera's spec states it: the game's usual 45-degree view
// is a 31 mm lens.
export const TILT_RANGE = [-180, 180];
export const LENS_RANGE = [14, 200];
export const GAME_FOV = 45;                     // degrees: the game's own lens on most of a course
const FILM_HALF_DIAGONAL = Math.hypot(36, 24) / 2, DIAGONAL_PER_HEIGHT = 5 / 3;
export const tiltOf = (roll) => (-(roll || 0) / PER_DEGREE) || 0;
export const rollOf = (tilt) => clamp(Math.round(-tilt * PER_DEGREE), -32768, 32767);
export const lensOf = (fov) => FILM_HALF_DIAGONAL / (Math.tan((fov ?? GAME_FOV) * Math.PI / 360) * DIAGONAL_PER_HEIGHT);
export const fovOf = (mm) => Math.atan(FILM_HALF_DIAGONAL / (mm * DIAGONAL_PER_HEIGHT)) * 360 / Math.PI;

const sub = (a, b) => a.map((value, i) => value - b[i]);
const add = (a, b) => a.map((value, i) => value + b[i]);
const scale = (a, s) => a.map((value) => value * s);
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const unit = (a) => { const n = Math.hypot(...a) || 1; return scale(a, 1 / n); };
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
const offset = (yaw, pitch, dist) =>
  [dist * Math.cos(pitch) * Math.sin(yaw), dist * Math.sin(pitch), dist * Math.cos(pitch) * Math.cos(yaw)];

// The orbit that draws the camera at `position` looking at `focus`, exactly:
// its distance is kept even when it is closer than the wheel allows, so the
// picture does not move on entry. null when the two coincide (no direction).
// `levels`: its tilt came with a recorded picture and levels as it moves; false
// for a tilt someone chose (Tilt, a saved camera, a cinematic camera's).
export function orbitFromPose(focus, position, { roll = 0, fov = null, levels = true } = {}) {
  const d = sub(position, focus), dist = Math.hypot(...d);
  if (!(dist >= 1)) return null;
  return { focus: [...focus], yaw: Math.atan2(d[0], d[2]), pitch: Math.asin(clamp(d[1] / dist, -1, 1)), dist,
    roll, fov, levels };
}

// What the server draws: {focus, position, roll (whole game units), fov}.
export function cameraOf(orbit) {
  const { focus, yaw, pitch, dist } = orbit;
  return { focus: [...focus], position: add(focus, offset(yaw, pitch, dist)),
    roll: clamp(Math.round(orbit.roll || 0), -32768, 32767), fov: orbit.fov ?? null };
}

// The game's lookat axes for a camera (mtxf_lookat with no roll; the screen
// roll turns the picture after them): right, up and forward.
export function axes(camera) {
  const forward = unit(sub(camera.focus, camera.position));
  const right = unit(cross(forward, [0, 1, 0]));
  return { right, up: cross(right, forward), forward };
}

// A point in the camera's own axes (x right, y up, z toward the viewer, as a
// Set focus pick answers) in world coordinates.
export function worldPoint(orbit, [x, y, z]) {
  const camera = cameraOf(orbit), { right, up, forward } = axes(camera);
  return add(camera.position, add(add(scale(right, x), scale(up, y)), scale(forward, -z)));
}

// A recorded tilt levels as the camera moves: `degrees` of roll come off, never
// past level. A tilt someone chose stays.
export function leveled(orbit, degrees) {
  if (orbit.levels === false) return orbit;
  const roll = orbit.roll || 0, by = degrees * PER_DEGREE;
  return { ...orbit, roll: Math.sign(roll) * Math.max(0, Math.abs(roll) - by) };
}

// The roll a camera keeps when it turns to face something new (Set focus, a
// double-click's flight): a chosen tilt, else level.
const keptRoll = (orbit) => (orbit.levels === false ? orbit.roll || 0 : 0);

// Tilt: the picture turned `degrees` clockwise; it stays as the camera moves.
export function tilted(orbit, degrees) {
  return { ...orbit, roll: rollOf(clamp(degrees, ...TILT_RANGE)), levels: false };
}

// Lens: `mm` of focal length (LENS_RANGE); the camera stays where it is.
export function lensed(orbit, mm) {
  return { ...orbit, fov: fovOf(clamp(mm, ...LENS_RANGE)) };
}

// Z / C and - / = held for `seconds`, on a camera's {roll, fov}: `move.tilt`
// -1 turns the picture counterclockwise and +1 clockwise at tiltPerSecond,
// `move.zoom` +1 lengthens the lens and -1 widens it by lensPerSecond each
// second; `pace` multiplies both (Shift, Alt). The other one is returned as given.
export function tiltAndLens({ roll, fov }, move, seconds, pace = 1, tuning = SCENE_CAMERA_DEFAULTS) {
  const turn = (move.tilt || 0) * pace * seconds, zoom = (move.zoom || 0) * pace * seconds;
  return { roll: turn ? rollOf(clamp(tiltOf(roll) + turn * tuning.tiltPerSecond, ...TILT_RANGE)) : roll,
    fov: zoom ? fovOf(clamp(lensOf(fov) * tuning.lensPerSecond ** zoom, ...LENS_RANGE)) : fov };
}

// Ctrl+wheel (a touchpad pinch): the lens after `wheel` (deltaY in pixels; down widens).
export function wheelLens(fov, wheel, tuning = SCENE_CAMERA_DEFAULTS) {
  return fovOf(clamp(lensOf(fov) * Math.exp(-wheel * tuning.lensPerWheel), ...LENS_RANGE));
}

// Pitch moves within the steepest allowed, except that a camera already
// steeper (a recorded one) keeps its angle and only comes back toward the range.
function pitchWithin(next, current, tuning) {
  const limit = tuning.steepestDegrees * Math.PI / 180;
  return clamp(next, Math.min(-limit, current), Math.max(limit, current));
}

// Left drag: turn around the focus point.
export function turned(orbit, dx, dy, tuning = SCENE_CAMERA_DEFAULTS) {
  const k = tuning.orbitPerPixel;
  return leveled({ ...orbit, yaw: orbit.yaw - dx * k, pitch: pitchWithin(orbit.pitch + dy * k, orbit.pitch, tuning) },
    (Math.abs(dx) + Math.abs(dy)) * tuning.levelPerPixel);
}

// Shift-drag: grab and slide; the scene follows the pointer, in steps that
// scale with the distance to the focus point, across a tilted picture too (the
// pointer's move turned back by the tilt into the camera's own axes).
export function slid(orbit, dx, dy, tuning = SCENE_CAMERA_DEFAULTS) {
  const { right, up } = axes(cameraOf(orbit)), step = orbit.dist * tuning.panPerPixel;
  const turn = tiltOf(orbit.roll) * Math.PI / 180, cos = Math.cos(turn), sin = Math.sin(turn);
  const across = dx * cos + dy * sin, down = dy * cos - dx * sin;
  return { ...orbit, focus: add(orbit.focus, add(scale(right, -across * step), scale(up, down * step))) };
}

// The wheel: closer or back, by a fraction of the distance.
export function dollied(orbit, wheel, tuning = SCENE_CAMERA_DEFAULTS) {
  return { ...orbit, dist: clamp(orbit.dist * Math.exp(wheel * tuning.dollyPerWheel),
    Math.min(tuning.closestDistance, orbit.dist), Math.max(tuning.farthestDistance, orbit.dist)) };
}

// Mouse movement with the right button held: the view turns where the camera stands.
export function looked(orbit, dx, dy, tuning = SCENE_CAMERA_DEFAULTS) {
  const k = tuning.lookPerPixel, position = cameraOf(orbit).position;
  const yaw = orbit.yaw - dx * k, pitch = pitchWithin(orbit.pitch + dy * k, orbit.pitch, tuning);
  return leveled({ ...orbit, yaw, pitch, focus: sub(position, offset(yaw, pitch, orbit.dist)) },
    (Math.abs(dx) + Math.abs(dy)) * tuning.levelPerPixel);
}

// Flight speed, units per second: the shipped speed times the speed control's
// multiplier, Shift (fast) and Alt (slow).
export function flightSpeed(multiplier, { fast = false, slow = false } = {}, tuning = SCENE_CAMERA_DEFAULTS) {
  return tuning.flightSpeed * multiplier * (fast ? tuning.fastMultiplier : 1) * (slow ? tuning.slowMultiplier : 1);
}

// The speed control after `notches` wheel notches or button steps (up is +).
export function speedStepped(multiplier, notches, tuning = SCENE_CAMERA_DEFAULTS) {
  return clamp(multiplier * tuning.speedPerNotch ** notches, ...SPEED_RANGE);
}

// W A S D and Q E for `seconds`: forward is where the camera looks, Q and E
// are straight down and up. A constant speed with no acceleration and no
// coasting; diagonals are no faster than straight lines.
export function flown(orbit, move, seconds, speed, tuning = SCENE_CAMERA_DEFAULTS) {
  const { right, forward } = axes(cameraOf(orbit));
  const step = add(add(scale(forward, move.forward || 0), scale(right, move.right || 0)), [0, move.up || 0, 0]);
  const length = Math.hypot(...step);
  if (!length || !(seconds > 0)) return orbit;
  return leveled({ ...orbit, focus: add(orbit.focus, scale(step, speed * seconds / length)) },
    seconds * tuning.levelPerSecond);
}

// Look at Mario: the camera stays where it stands and only turns to face him
// (his report, 2026-10-27: "Same position, just looking at mario"); its roll
// and field of view are kept, and it orbits him from then on. Unchanged when
// the camera stands on that point (no direction to face).
export function lookingAt(orbit, mario, tuning = SCENE_CAMERA_DEFAULTS) {
  return orbitFromPose(add(mario, [0, tuning.marioHeight, 0]), cameraOf(orbit).position,
    { roll: orbit.roll || 0, fov: orbit.fov ?? null, levels: orbit.levels !== false }) || orbit;
}

// Set focus: turn to the picked point and orbit it, the camera where it was,
// level unless you set its tilt.
export function focusedOn(orbit, point) {
  return orbitFromPose(point, cameraOf(orbit).position, { roll: keptRoll(orbit), fov: orbit.fov,
    levels: orbit.levels !== false }) || orbit;
}

// Double-click fly-to's destination: just in front of the clicked surface,
// backed off along the click's ray toward where the camera was (never past
// it), looking at Mario where Look at Mario looks, level unless you set its
// tilt. null when that spot is Mario's own focus point.
export function flyToTarget(orbit, hit, mario, tuning = SCENE_CAMERA_DEFAULTS) {
  const from = cameraOf(orbit).position, back = sub(from, hit), length = Math.hypot(...back);
  const position = add(hit, scale(unit(back), Math.min(tuning.flyToStandoff, length)));
  return orbitFromPose(add(mario, [0, tuning.marioHeight, 0]), position, { roll: keptRoll(orbit), fov: orbit.fov,
    levels: orbit.levels !== false });
}

// Starts and ends at rest (no jolt leaving, no bump arriving): a sine ease,
// under way sooner than a cubic one, so the flight visibly answers the
// double-click within its first frames.
export const easeInOut = (t) => (1 - Math.cos(Math.PI * t)) / 2;

// The camera `t` (0..1, already eased) of the way from one orbit to another:
// its place and its focus point travel in straight lines, the tilt levels.
export function glided(from, to, t) {
  const a = cameraOf(from), b = cameraOf(to), mix = (p, q) => p.map((value, i) => value + (q[i] - value) * t);
  return orbitFromPose(mix(a.focus, b.focus), mix(a.position, b.position),
    { roll: (from.roll || 0) + ((to.roll || 0) - (from.roll || 0)) * t, fov: to.fov, levels: to.levels !== false })
    || (t < 1 ? from : to);
}

// Where a double-click's flight is `elapsed` ms in: {orbit, done}.
export function glideAt(from, to, elapsed, tuning = SCENE_CAMERA_DEFAULTS) {
  const t = tuning.flyToMs > 0 ? clamp(elapsed / tuning.flyToMs, 0, 1) : 1;
  return { orbit: t < 1 ? glided(from, to, easeInOut(t)) : to, done: t >= 1 };
}
