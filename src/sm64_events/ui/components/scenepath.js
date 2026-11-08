// src/sm64_events/ui/components/scenepath.js -- the Scene viewer's camera path: its math (lab only).
//
// Griffin, 2026-11-08: "evaluate https://github.com/MikeXander/SM64Scripts to see what camera
// techniques / methods we could add + support from this tool". Xander's Custom Camera keys a camera
// path for a TAS replay; this is the same idea for the Scene viewer, written from scratch (his
// repository has no license, so only the ideas came across; ui/credits.js credits him).
//
// A camera path is a list of keys. Each key is a camera on a frame of the scene: where it stands and
// looks, its tilt and lens ({focus, position, roll, fov}, as a camera preset keeps one), or null for
// the recorded camera, the one the player saw. Between two keys the camera follows a smooth curve
// that keeps its speed through a key (the lead's correction, 2026-11-08: Xander's default stops at
// every key, which reads as jerky in a video): it slows only into the path's first and last keys,
// into and out of a hold, and at a key whose `ease` is on. Where one of the two keys is the recorded
// camera, the camera blends between the other key and the game's own moving camera (the lab draws
// the blend: {..., recorded: weight}, scene_session.recorded_blend), so a path can ease out of the
// game's camera and back into it. A key's `aim` ("stage" or a stage ghost's ref; null: the point it
// keeps) turns its focus onto that runner as he moves: the camera asks {..., aim: {target: weight}}
// and the lab puts the focus that share of the way to his eyes on his path (scene_session.aim_blend).
// Before the first key and after the last the camera stays on that key.
//
// The path is a pure function of its own clock, so Play, a step, a seek and an export of the same
// moment draw the same camera. Its clock, q, runs with the scene's recorded time (sceneclock.js) in
// ms. A hold key (a second key on the frame of the key before it, `hold` ms after it) stops the
// game on that frame while the camera travels from the key before to it, "bullet time": q goes on
// through the hold while the scene's time stands still. A key's `speed` (1, 1/2, 1/4) plays the
// stretch from it to the next key that much slower, the camera slowing with the game. Play speed
// (PLAY_SPEEDS) slows the whole play on top of that. pathClock turns all of it into when each
// picture shows on screen (`display`, ms after the first picture), which is what SceneLink's Play
// keeps instead of the recorded times, and back from a moment on screen to q, the frame and the
// recorded time heard then (null while slower than 1x or holding: soundPlays decides). The camera
// itself is pathCamera(keys, clock.keyQ, q).
import { GAME_FOV } from "./scenecamera.js";

export const PLAY_SPEEDS = [1, 0.5, 0.25];
export const HOLD = { ms: 2000, minMs: 100, maxMs: 60000 };
export const MAX_KEYS = 64;

// THE place that decides the sound of a slowed play: whether it plays at a moment when the scene runs
// `rate` times as fast as it was recorded (the Play speed times a key's speed; 0 in a hold). Today it
// plays only at 1x: slower is silent, music and sound effects alike (2026-11-08, reversible), and it
// comes back where the play is at 1x again. Play (SceneLink) and Record (scenerecord.js play_ms) both
// ask pathClock's at(), which asks this.
export const soundPlays = (rate) => rate === 1;

const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
const isPose = (camera) => !!camera && Array.isArray(camera.focus) && Array.isArray(camera.position);

// The camera as a key keeps it: its place, aim, roll and field of view; null for the recorded camera.
export const keyCamera = (camera) => (isPose(camera) ? { focus: [...camera.focus], position: [...camera.position],
  roll: camera.roll ?? 0, fov: camera.fov ?? null } : null);

// A key as the path keeps it: every field filled.
export function makeKey({ frame, camera = null, ease = false, hold = 0, speed = 1, aim = null }) {
  return { frame: Math.max(0, Math.round(frame)), camera: keyCamera(camera), ease: ease === true,
    hold: Number.isFinite(hold) ? hold : 0, speed: PLAY_SPEEDS.includes(speed) ? speed : 1,
    aim: typeof aim === "string" && aim ? aim : null };
}

// Whether the camera slows to a stop at key `j` of the played keys whatever its ease: the path's first
// and last, and either end of a hold.
export function alwaysSettles(keys, j) {
  return j === 0 || j === keys.length - 1 || keys[j].hold > 0 || (keys[j + 1]?.hold ?? 0) > 0;
}

// Whether it slows to a stop there: always, or because the key's ease is on.
export const settles = (keys, j) => alwaysSettles(keys, j) || keys[j].ease;

// The keys a scene of `frameCount` pictures plays, in order: by frame, and keys on one frame in the
// order they were made. The first key on a frame holds nothing; each later one on that frame is a hold
// of at least HOLD.minMs. Keys past the scene's last frame are left out.
export function orderedKeys(keys, frameCount) {
  const placed = (keys || []).map((key, index) => ({ key, index }))
    .filter(({ key }) => key.frame >= 0 && key.frame < frameCount)
    .sort((a, b) => a.key.frame - b.key.frame || a.index - b.index);
  return placed.map(({ key }, at) => {
    const held = at > 0 && placed[at - 1].key.frame === key.frame;
    return { ...key, hold: held ? clamp(key.hold || HOLD.ms, HOLD.minMs, HOLD.maxMs) : 0 };
  });
}

// The last index whose value is at or before `value` in an ascending array (0 when none is).
function lastAtOrBefore(values, value) {
  let lo = 0, hi = values.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (values[mid] <= value) lo = mid; else hi = mid - 1;
  }
  return lo;
}

// The path's clock for one scene. `times`: when the recording showed each picture (sceneclock.js
// recordedTimes); `keys`: orderedKeys; `speed`: the Play speed; `fps`: how long the last picture
// lasts at 1x. Everything is a function of the three, so two clocks of the same arguments agree.
//
//   keyQ[j]        key j's moment on the path's clock
//   qStart(k)      when picture k begins on the path's clock (before any hold on its frame)
//   display[k]     when picture k shows on screen, ms after the first (what Play keeps)
//   length         how long the whole scene plays, first picture to the end of the last
//   at(w)          {q, frame, sceneMs, heardMs} at `w` ms on screen after the first picture;
//                  heardMs is the recorded time the sound plays then, null where it is silent
//   plain          true when display is the recorded times themselves (nothing slower, no hold)
export function pathClock(times, keys = [], { speed = 1, fps = 30 } = {}) {
  const n = times.length, frameMs = 1000 / fps;
  const holdsBefore = new Float64Array(n + 1);          // hold ms on frames before frame k
  const keyQ = [], holds = [];
  let held = 0;
  keys.forEach((key, j) => {
    if (key.hold > 0) {
      holds.push({ q0: keyQ[j - 1], q1: keyQ[j - 1] + key.hold, frame: key.frame });
      held += key.hold;
      keyQ.push(keyQ[j - 1] + key.hold);
    } else keyQ.push(times[key.frame] + held);
  });
  for (let k = 0, j = 0, sum = 0; k <= n; k++) {
    while (j < keys.length && keys[j].frame < k) sum += keys[j++].hold;
    holdsBefore[k] = sum;
  }
  const qStart = (k) => times[k] + holdsBefore[k];
  const qEnd = (n ? times[n - 1] + frameMs : 0) + held;
  // The rate the path's clock runs at on screen: Play speed times the speed of the key it is past.
  const rates = [{ q: 0, rate: speed }];
  keys.forEach((key, j) => rates.push({ q: keyQ[j], rate: speed * key.speed }));
  const knots = [{ q: 0, w: 0, rate: speed }];
  for (const { q, rate } of rates.slice(1)) {
    const last = knots[knots.length - 1];
    knots.push({ q, w: last.w + (q - last.q) / last.rate, rate });
  }
  const qs = knots.map((knot) => knot.q), ws = knots.map((knot) => knot.w);
  const wOf = (q) => { const knot = knots[lastAtOrBefore(qs, q)]; return knot.w + (q - knot.q) / knot.rate; };
  const qOf = (w) => { const knot = knots[lastAtOrBefore(ws, w)]; return knot.q + (w - knot.w) * knot.rate; };
  const sOf = (q) => {                                  // the scene's own time: it stands still in a hold
    let removed = 0;
    for (const { q0, q1, frame } of holds) {
      if (q < q0) break;
      if (q < q1) return times[frame];
      removed += q1 - q0;
    }
    return q - removed;
  };
  const rateAt = (q) => knots[lastAtOrBefore(qs, q)].rate;
  const holding = (q) => holds.some(({ q0, q1 }) => q >= q0 && q < q1);
  const display = Float64Array.from({ length: n }, (_, k) => wOf(qStart(k)));
  const plain = speed === 1 && !holds.length && keys.every((key) => key.speed === 1);
  return {
    keyQ, holds, display, plain, qStart, qEnd, wOf, qOf, sOf,
    length: wOf(qEnd),
    // The picture is the one Play shows then (the last whose display time has come), and the scene's
    // time stays inside it, so rounding never puts a moment on the picture before.
    at(w) {
      const shown = Math.max(0, w), q = clamp(qOf(shown), 0, qEnd);
      const frame = n ? lastAtOrBefore(display, shown) : 0;
      const sceneMs = n ? clamp(sOf(q), times[frame], frame + 1 < n ? times[frame + 1] : Infinity) : 0;
      return { q, frame, sceneMs, heardMs: soundPlays(holding(q) ? 0 : rateAt(q)) ? sceneMs : null };
    },
  };
}

// ---------------------------------------------------------------- the camera between keys

// A camera as the numbers the curve moves: place, aim, roll (unwrapped against `near`) and the lens
// as log(tan(half the field of view)), which zooms evenly.
function numbersOf(camera, near = null) {
  let roll = camera.roll ?? 0;
  if (near != null) roll += Math.round((near - roll) / 65536) * 65536;
  return [...camera.focus, ...camera.position, roll, Math.log(Math.tan((camera.fov ?? GAME_FOV) * Math.PI / 360))];
}

function cameraFrom(numbers, fov) {
  const focus = numbers.slice(0, 3), position = numbers.slice(3, 6);
  if (Math.hypot(...position.map((value, i) => value - focus[i])) < 1) position[2] += 1.5;   // never on its own aim
  const roll = ((Math.round(numbers[6]) + 32768) % 65536 + 65536) % 65536 - 32768;
  return { focus, position, roll, fov: fov == null ? null : Math.atan(Math.exp(numbers[7])) * 360 / Math.PI };
}

// Each camera key's numbers, rolls unwrapped along the path so a turn takes the short way round.
function keyNumbers(keys) {
  let roll = null;
  return keys.map((key) => {
    if (!key.camera) return null;
    const numbers = numbersOf(key.camera, roll);
    roll = numbers[6];
    return numbers;
  });
}

// How fast each number leaves key j (per ms of the path's clock): 0 where the camera settles there
// (settles: the path's ends, a hold's ends, Ease on) or beside the recorded camera; else the slope
// through the keys either side of it, so the camera passes through without slowing.
function slopes(keys, numbers, keyQ, j) {
  const before = j - 1, after = j + 1;
  const smooth = !settles(keys, j) && numbers[j] && numbers[before] && numbers[after]
    && keyQ[after] > keyQ[j] && keyQ[j] > keyQ[before];
  return smooth ? numbers[after].map((value, i) => (value - numbers[before][i]) / (keyQ[after] - keyQ[before]))
    : numbers[j]?.map(() => 0);
}

const smoothstep = (u) => u * u * (3 - 2 * u);

// The share of the focus each runner holds `u` of the way from key j to key j + 1 ({target: weight}, the
// rest the keys' own points): each key's runner fades out as the next one's fades in.
function aimsAt(keys, j, u) {
  const weights = {}, add = (target, weight) => {
    if (target && weight > 0) weights[target] = (weights[target] || 0) + weight;
  };
  add(keys[j].aim, 1 - smoothstep(u));
  if (keys[j + 1]) add(keys[j + 1].aim, smoothstep(u));
  return weights;
}

const aimed = (camera, weights) => (camera && Object.keys(weights).length ? { ...camera, aim: weights } : camera);

// The camera at `q` on the path's clock: a camera ({focus, position, roll, fov}), a camera blended with
// the recorded one ({..., recorded: weight 0..1}, the lab blends), with its focus turned toward a runner
// ({..., aim: {target: weight}}, the lab blends), or null for the recorded camera. `keyQ` is the
// clock's. Cubic Hermite between two camera keys, so a key is met exactly at its moment and the
// camera moves smoothly between.
export function pathCamera(keys, keyQ, q) {
  if (!keys.length) return null;
  const numbers = keyNumbers(keys);
  const fov = keys.some((key) => key.camera && key.camera.fov != null) ? GAME_FOV : null;
  const own = (j) => (keys[j].camera ? cameraFrom(numbers[j], keys[j].camera.fov ?? fov) : null);
  const last = keys.length - 1;
  if (q <= keyQ[0]) return aimed(own(0), aimsAt(keys, 0, 0));
  if (q >= keyQ[last]) return aimed(own(last), aimsAt(keys, last, 0));
  let j = 0;
  while (j < last - 1 && keyQ[j + 1] <= q) j += 1;
  const span = keyQ[j + 1] - keyQ[j], u = span > 0 ? clamp((q - keyQ[j]) / span, 0, 1) : 1;
  if (!keys[j].camera || !keys[j + 1].camera) {
    return aimed(recordedBlend(own(j), own(j + 1), u), aimsAt(keys, keys[j].camera ? j : j + 1, 0));
  }
  const p0 = numbers[j], p1 = numbers[j + 1], m0 = slopes(keys, numbers, keyQ, j), m1 = slopes(keys, numbers, keyQ, j + 1);
  const u2 = u * u, u3 = u2 * u;
  const h00 = 2 * u3 - 3 * u2 + 1, h10 = u3 - 2 * u2 + u, h01 = -2 * u3 + 3 * u2, h11 = u3 - u2;
  const moved = p0.map((value, i) => h00 * value + h10 * span * m0[i] + h01 * p1[i] + h11 * span * m1[i]);
  return aimed(cameraFrom(moved, keys[j].camera.fov ?? keys[j + 1].camera.fov ?? fov), aimsAt(keys, j, u));
}

// `u` of the way from camera `from` to camera `to` where one of them (null) is the recorded camera: the
// other camera, blended toward the recorded one by a weight that eases in and out (the lab blends).
function recordedBlend(from, to, u) {
  if (!from && !to) return null;
  const toward = smoothstep(from ? u : 1 - u), camera = from || to;
  if (toward <= 0) return camera;
  return toward >= 1 ? null : { ...camera, recorded: toward };
}
