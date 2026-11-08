// src/sm64_events/ui/components/scenepathbar.js -- the Scene viewer's camera path controls (lab only).
//
// One block under the Cameras strip (sceneviewer.js draws it): Path, which puts the camera path on
// screen, + Key, and the path's keys. + Key keeps the frame on screen and its camera: the free
// camera's, a cinematic camera's or a ghost's view as the lab drew it, the path's own, or, from the
// Recorded camera, the recorded camera itself, which the path then eases out of and back into
// (scenepath.js owns what a key means). Each key can be jumped to (the scene pauses on its frame
// and the path shows its camera), replaced with the camera on screen, deleted, and made to pass
// through without slowing (Ease off).
//
// A path's coordinates belong to one area of one level, so each course and area keeps one path, as
// it keeps its nine camera presets (scenepresets.js placeOf): the block shows the path of the area the
// picture on screen is in. A key's frame counts from the first frame of the scene that plays it. The
// lab keeps every path in one file in its folder (GET and PUT /api/scene-lab/camera-paths,
// tools/scene_lab/camera_paths.py); each change sends the area's whole path and shows at once.
//
// SceneLink (sceneviewer.js) asks this module for the path's camera (pathCameraNow), what a Play keeps
// as its clock (playTiming), what a Play does every display frame (pathPlayTick) and where a paused
// path stands (pathStopped), so the path never lives in the viewer's own code.
import { h } from "preact";
import { useEffect } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { Icon } from "./icons.js";
import { recordedTimes } from "./sceneclock.js";
import { HOLD, MAX_KEYS, makeKey, orderedKeys, pathCamera, pathClock } from "./scenepath.js";

const html = htm.bind(h);
const ROOT = "/api/scene-lab/camera-paths";

// Its own sheet beside scenelab.css, linked once when the viewer loads this module.
if (typeof document !== "undefined" && !document.querySelector("link[data-scene-path]")) {
  const sheet = Object.assign(document.createElement("link"), { rel: "stylesheet", href: "/ui/scenepath.css" });
  sheet.dataset.scenePath = "";
  document.head.append(sheet);
}
const placeQuery = (key) => { const [level, area] = key.split(":"); return `level=${level}&area=${area}`; };
// Keys as the lab keeps them: by frame, keys on one frame in the order they were made.
const byFrame = (keys) => keys.map((key, index) => ({ key, index }))
  .sort((a, b) => a.key.frame - b.key.frame || a.index - b.index).map(({ key }) => key);

// Every path the page has read, by place key ("9:1"): {state: "loading"|"ready"|"failed", keys,
// problem}. A change shows at once and goes to the lab as the area's whole path, one write at a time;
// only the newest change's answer is adopted, and a refused one shows the lab's words and reads the
// path again.
export class CameraPaths {
  constructor({ changed }) {
    Object.assign(this, { changed, paths: new Map(), writing: Promise.resolve(), revision: 0 });
  }

  forget() { this.paths = new Map(); }

  of(key) { return key ? this.paths.get(key) || null : null; }

  load(key) {
    if (!key || this.paths.has(key)) return;
    this.paths.set(key, { state: "loading", keys: [], problem: null });
    getJSON(`${ROOT}?${placeQuery(key)}`).then(
      (answer) => this.paths.set(key, { state: "ready", keys: byFrame(answer.keys || []), problem: answer.problem || null }),
      (problem) => this.paths.set(key, { state: "failed", keys: [], problem: problem.message }),
    ).finally(() => this.changed());
  }

  write(key, keys) {
    const revision = ++this.revision, path = this.of(key);
    if (!path || path.state !== "ready") return;
    Object.assign(path, { keys: byFrame(keys), problem: null });
    this.changed();
    this.writing = this.writing.then(() => send("PUT", `${ROOT}?${placeQuery(key)}`, { keys: path.keys })).then(
      (answer) => { if (revision === this.revision && this.of(key) === path) path.keys = byFrame(answer.keys); },
      (problem) => {
        if (revision !== this.revision) return null;
        path.problem = problem.message;
        return getJSON(`${ROOT}?${placeQuery(key)}`).then((answer) => {
          if (revision === this.revision && this.of(key) === path) path.keys = byFrame(answer.keys || []);
        }, () => {});
      },
    ).finally(() => this.changed());
  }
}

// ---------------------------------------------------------------- the link's side

// The path of the place on screen, as the lab keeps it ([] while it is read or there is none).
export const storedKeys = (link) => link.paths.of(link.presetKey)?.keys || [];

// The keys this scene plays (scenepath.js orderedKeys), for the place on screen.
export function playedKeys(link) {
  const stored = storedKeys(link), count = link.scene?.frames.length || 0;
  const kept = link.pathMemo;
  if (kept?.stored === stored && kept.count === count) return kept.keys;
  const keys = orderedKeys(stored, count);
  link.pathMemo = { stored, count, keys, clocks: new Map() };
  return keys;
}

// The path's clock for `keys` over the scene's recorded times, kept while neither changes.
export function clockOf(link, keys, speed = 1) {
  const times = recordedTimes(link.scene), memo = link.pathMemo;
  const key = `${speed}|${times.length}|${times[times.length - 1] ?? 0}`;
  if (memo?.keys === keys) {
    if (!memo.clocks.has(key)) memo.clocks.set(key, pathClock(times, keys, { speed, fps: link.scene?.fps || 30 }));
    return memo.clocks.get(key);
  }
  return pathClock(times, keys, { speed, fps: link.scene?.fps || 30 });
}

// The camera a picture asked for at `now` is drawn from while the path is on screen (SceneLink.cameraNow):
// during a Play, the play's own path at the moment on screen then (the display frame that begins at
// `now`, as a recording counts it); paused, where the path stopped on this frame, else where the frame
// begins on the path's clock. null for the recorded camera.
export function pathCameraNow(link, frame, now = performance.now()) {
  const play = link.playback;
  if (play?.path) {
    const at = play.path.clock.at(play.times[play.from] + now - play.start + (play.frameMs || 0));
    return pathCamera(play.path.keys, play.path.clock.keyQ, at.q);
  }
  const keys = playedKeys(link);
  if (!keys.length) return null;
  const clock = clockOf(link, keys), held = link.pathHeld;
  const q = held?.keys === keys && held.frame === frame ? held.q : clock.qStart(frame);
  return pathCamera(keys, clock.keyQ, q);
}

// What a Play from now keeps as its clock: with the path on screen, the path's (`path`: its keys and
// clock, which the play keeps to its end), else the recorded times. `display` is when each picture
// shows, ms after the first; `lengthFrom(from)` how long a play from `from` lasts.
export function playTiming(link, scene) {
  if (!link.pathOn || !playedKeys(link).length) {
    const times = recordedTimes(scene), frameMs = 1000 / (scene.fps || 30);
    return { times, path: null, lengthFrom: (from) => times[times.length - 1] - times[from] + frameMs };
  }
  const keys = playedKeys(link), clock = clockOf(link, keys);
  return { times: clock.display, path: { keys, clock }, lengthFrom: (from) => clock.length - clock.display[from] };
}

// Every display frame of a Play with the path on screen: the picture on screen is asked for again
// with the path's camera when that camera moved since the last one asked, and the sound follows the
// path's clock (pathSound).
export function pathPlayTick(link, play, frameTime) {
  if (!play.path) return;
  pathSound(link, play, frameTime);
  const camera = JSON.stringify(link.cameraNow(play.asked, frameTime + link.readyMs()));
  if (camera !== play.path.sent) {
    play.path.sent = camera;
    link.want("move", performance.now());
  }
}

// A Play's sound as it starts (SceneLink.play, and again when a growing scene goes on): from the play's
// first picture at its start, as ever, unless the path's clock is silent there (a hold, a slower
// stretch: scenepath.js soundPlays).
export function playSound(link, play) {
  const clock = play.path?.clock;
  if (!clock || clock.plain) return link.sound.play(play.from, play.start);
  play.path.sounding = clock.at(play.times[play.from] + (play.resumeMs || 0)).heardMs != null;
  if (play.path.sounding) link.sound.play(play.from, play.start); else link.sound.stop();
}

// Each display frame of a Play on a path whose clock has silent stretches: the sound stops where one
// begins, and where one ends starts again on the recorded time heard then.
function pathSound(link, play, frameTime) {
  const clock = play.path.clock;
  if (clock.plain || frameTime < play.start) return;
  const at = clock.at(play.times[play.from] + frameTime - play.start), sounding = at.heardMs != null;
  if (sounding === !!play.path.sounding) return;
  play.path.sounding = sounding;
  if (!sounding) return link.sound.stop();
  const times = recordedTimes(link.scene);
  link.sound.play(at.frame, frameTime - (at.heardMs - times[at.frame]));
}

// A Play stops: a paused path stays where it was on its clock (a step, a seek or another frame moves it
// to where that frame begins).
export function pathStopped(link, play, now = performance.now()) {
  if (!play?.path) return;
  const clock = play.path.clock, frame = link.frame, count = clock.display.length;
  const end = frame + 1 < count ? clock.qStart(frame + 1) : clock.qEnd;
  const q = Math.min(Math.max(clock.at(play.times[play.from] + now - play.start).q, clock.qStart(frame)), end);
  link.pathHeld = { keys: play.path.keys, frame, q };      // on the frame on screen, never the one after it
}

// The path on screen, or not. Choosing it leaves the other cameras, as choosing any camera does.
export function usePath(link, on, time) {
  if (on && (!link.freeAvailable || !playedKeys(link).length)) return;
  link.interrupt();
  link.stopLook("the camera path");
  link.releaseKeys();
  const replay = link.playing;
  if (replay) link.pause();
  Object.assign(link, { free: false, view: null, rig: null, pathOn: on, notice: null, picking: false });
  if (replay) link.play();
  link.want("input", time);
  link.changed();
}

// A change of keys while the path plays: the play goes on from where it is with the new path.
function changedKeys(link) {
  if (link.pathOn && link.playing) { link.pause(); link.play(); } else if (link.pathOn) link.want("input");
}

// ---------------------------------------------------------------- the block

// Whether the camera on screen is the recorded one: + Key then keeps the recorded camera itself.
const recordedOnScreen = (link) => !link.free && !link.view && !link.rig && !link.pathOn;

async function cameraForKey(link) {
  if (recordedOnScreen(link)) return { camera: null };
  const camera = await link.cameraOnScreen();
  return camera ? { camera } : null;
}

// What the block says under its name: why it cannot be used, the lab's problem, or what to do.
function pathNote(link, key, path) {
  if (!link.freeAvailable) return { text: link.scene.free_camera?.reason || "The free camera is unavailable.", bad: false };
  if (!key) return { text: "This picture does not say which course and area it shows, so no path is kept for it.", bad: false };
  if (path?.problem) return { text: path.problem, bad: true };
  if (!path || path.state === "loading") return { text: "Reading the camera path...", bad: false };
  if (!path.keys.length) return { text: "+ Key keeps this frame and the camera on screen. Add two or more.", bad: false };
  return { text: "Path plays the keys. Two keys on one frame stop the game there while the camera moves.", bad: false };
}

export function PathBar({ link }) {
  const paths = link.paths, key = link.presetKey, path = paths.of(key);
  useEffect(() => { if (link.freeAvailable) paths.load(key); }, [key, link.freeAvailable, link.scene]);
  const usable = link.freeAvailable && path?.state === "ready";
  const stored = path?.keys || [], keys = playedKeys(link), count = link.scene.frames.length;
  const clock = keys.length ? clockOf(link, keys) : null;
  const note = pathNote(link, key, path);
  const save = (next) => { paths.write(key, next); changedKeys(link); };
  async function add() {
    if (stored.length >= MAX_KEYS) return link.sayBriefly(`A path holds at most ${MAX_KEYS} keys.`);
    const frame = link.frame, got = await cameraForKey(link);
    if (!got || paths.of(key) !== path) return;
    const held = stored.some((one) => one.frame === frame);     // a second key on a frame is a hold
    save([...stored, makeKey({ frame, camera: got.camera, hold: held ? HOLD.ms : 0 })]);
  }
  const holdMs = (value) => Math.round(Math.min(HOLD.maxMs, Math.max(HOLD.minMs, (Number(value) || 0) * 1000)));
  async function replace(index) {
    const got = await cameraForKey(link);
    if (got && paths.of(key) === path) save(stored.map((one, at) => (at === index ? { ...one, camera: got.camera } : one)));
  }
  const change = (index, fields) => save(stored.map((one, at) => (at === index ? { ...one, ...fields } : one)));
  // Kept keys are in frame order and the scene plays those before its end, so a key's place among the
  // played keys is its place in the list.
  function jump(index, time) {
    const one = stored[index];
    link.pause();
    link.seek(one.frame, "input", time);
    if (clock) link.pathHeld = { keys, frame: one.frame, q: clock.keyQ[index] };
    if (!link.pathOn) usePath(link, true, time);
  }
  return html`<div class="scene-lab-path" role="group" aria-label="Camera path">
    <div class="scene-lab-bar">
      <span class="scene-lab-rigs-name">Path</span>
      <button type="button" aria-pressed=${!!link.pathOn} disabled=${!usable || !keys.length}
          title=${keys.length ? "Show the camera path: the camera moves through its keys as the scene plays"
            : "Add keys first"}
          onclick=${(event) => usePath(link, !link.pathOn, event.timeStamp)}>Path</button>
      <button type="button" disabled=${!usable} onclick=${add}
          title="Add a key: this frame and the camera on screen (from the Recorded camera, the recorded camera itself)">
        <${Icon} name="plus" size=${13} /> Key</button>
      <p class=${`scene-lab-hint${note.bad ? " is-bad" : ""}`} role=${note.bad ? "status" : null}>${note.text}</p>
    </div>
    ${stored.length > 0 && html`<ol class="scene-lab-path-keys">
      ${stored.map((one, index) => {
        const outside = one.frame >= count, here = !outside && one.frame === link.frame;
        const held = index > 0 && stored[index - 1].frame === one.frame;
        return html`<li key=${index} class=${[here && "is-here", outside && "is-outside"].filter(Boolean).join(" ")}>
          <button type="button" class="scene-lab-path-jump" disabled=${!usable || outside}
              title=${outside ? "This key is after the end of this scene" : "Go to this key"}
              onclick=${(event) => jump(index, event.timeStamp)}>Frame ${one.frame + 1}</button>
          <span class="scene-lab-path-what">${one.camera ? "Camera" : "Recorded camera"}</span>
          ${held && html`<label class="scene-lab-path-hold"
              title="The game stops on this frame while the camera moves here from the key before">Hold
            <input type="number" min=${HOLD.minMs / 1000} max=${HOLD.maxMs / 1000} step="0.1" disabled=${!usable}
              value=${(one.hold || HOLD.ms) / 1000} aria-label=${`Hold on frame ${one.frame + 1}, in seconds`}
              onchange=${(event) => change(index, { hold: holdMs(event.currentTarget.value) })} /> s</label>`}
          <button type="button" aria-pressed=${one.ease} disabled=${!usable}
              title=${one.ease ? "The camera slows into and out of this key. Turn off to pass through it"
                : "The camera passes through this key. Turn on to slow into and out of it"}
              onclick=${() => change(index, { ease: !one.ease })}>Ease</button>
          <button type="button" disabled=${!usable} onclick=${() => replace(index)}
              title="Use the camera on screen for this key">Replace</button>
          <button type="button" class="scene-lab-path-delete" disabled=${!usable} aria-label=${`Delete the key on frame ${one.frame + 1}`}
              title="Delete" onclick=${() => save(stored.filter((_, at) => at !== index))}>
            <${Icon} name="trash" size=${13} /></button>
        </li>`;
      })}
    </ol>`}
  </div>`;
}
