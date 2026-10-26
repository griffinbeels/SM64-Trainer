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
import { SCENE_CAMERA_DEFAULTS } from "../scenecameratuning.js";

export const FULL_TURN = 65536;                 // the game's angle units in a turn
export const SPEED_RANGE = [0.05, 20];          // the flight speed control's multiplier
const PER_DEGREE = FULL_TURN / 360;

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
export function orbitFromPose(focus, position, { roll = 0, fov = null } = {}) {
  const d = sub(position, focus), dist = Math.hypot(...d);
  if (!(dist >= 1)) return null;
  return { focus: [...focus], yaw: Math.atan2(d[0], d[2]), pitch: Math.asin(clamp(d[1] / dist, -1, 1)), dist,
    roll, fov };
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

// A tilted view levels as it moves: `degrees` of roll come off, never past level.
export function leveled(orbit, degrees) {
  const roll = orbit.roll || 0, by = degrees * PER_DEGREE;
  return { ...orbit, roll: Math.sign(roll) * Math.max(0, Math.abs(roll) - by) };
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
// scale with the distance to the focus point.
export function slid(orbit, dx, dy, tuning = SCENE_CAMERA_DEFAULTS) {
  const { right, up } = axes(cameraOf(orbit)), step = orbit.dist * tuning.panPerPixel;
  return { ...orbit, focus: add(orbit.focus, add(scale(right, -dx * step), scale(up, dy * step))) };
}

// The wheel: closer or back, by a fraction of the distance.
export function dollied(orbit, wheel, tuning = SCENE_CAMERA_DEFAULTS) {
  return { ...orbit, dist: clamp(orbit.dist * Math.exp(wheel * tuning.dollyPerWheel),
    Math.min(tuning.closestDistance, orbit.dist), Math.max(tuning.farthestDistance, orbit.dist)) };
}

// Mouse movement while flying: the view turns where the camera stands.
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

// Look at Mario: the focus point on Mario, the camera's direction kept, as
// close as the focus distance when it was farther, and level.
export function lookingAt(orbit, mario, tuning = SCENE_CAMERA_DEFAULTS) {
  const limit = tuning.steepestDegrees * Math.PI / 180;
  return { ...orbit, focus: add(mario, [0, tuning.marioHeight, 0]), dist: Math.min(orbit.dist, tuning.focusDistance),
    pitch: clamp(orbit.pitch, -limit, limit), roll: 0 };
}

// Set focus: turn to the picked point and orbit it, the camera where it was, level.
export function focusedOn(orbit, point) {
  return orbitFromPose(point, cameraOf(orbit).position, { roll: 0, fov: orbit.fov }) || orbit;
}
