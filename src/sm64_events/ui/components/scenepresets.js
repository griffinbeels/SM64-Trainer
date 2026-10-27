// src/sm64_events/ui/components/scenepresets.js -- the Scene viewer's camera presets (M4 round 6,
// item 2, lab only; sceneviewer.js draws the Cameras strip under the camera controls).
//
// His words: "I set up a specific camera angle that I really like, and assign it to a camera slot
// position (numbers 1-9 on the keyboard). I should be able to name the camera, and be able to swap
// to the different camera views by pressing each button. I should be able to easily reassign
// cameras to different slots by dragging and dropping."
//
// Nine slots for the course and area the picture on screen is in (a camera's coordinates belong to
// one area, so a level with several areas switches sets as the scene moves between them). An empty
// slot saves the picture's camera there (SceneLink.cameraOnScreen) and asks for its name at once;
// a filled one cuts to its camera (SceneLink.cutTo: a free camera, Play keeps playing) and looks
// pressed while the free camera stands on it; rename and delete sit beside it; dragging one onto
// another slot moves it there, swapping with a filled one. Keys 1-9 cut too (sceneviewer.js KEYS).
// The lab keeps every set in one file in its folder (GET and PUT /api/scene-lab/camera-presets,
// tools/scene_lab/camera_presets.py), so they survive restarts; each change sends the area's whole
// set and shows at once.
import { h } from "preact";
import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { Icon } from "./icons.js";
import { cameraOf } from "./scenecamera.js";

const html = htm.bind(h);
const ROOT = "/api/scene-lab/camera-presets";
export const SLOTS = [1, 2, 3, 4, 5, 6, 7, 8, 9];
export const MAX_NAME = 48;                      // camera_presets.py MAX_NAME
export const defaultName = (slot) => `Camera ${slot}`;

// The course and area the picture at `index` is in: its own frame's, else the nearest earlier
// frame that names both (a fade or a level load between areas may name none), else the nearest
// later one; null when no frame does. Its key names a set of slots ("9:1").
export function placeOf(frames, index) {
  const named = (frame) => frame?.level != null && frame?.area != null;
  for (let at = Math.min(index, frames.length - 1); at >= 0; at--) if (named(frames[at])) return frames[at];
  for (let at = index + 1; at < frames.length; at++) if (named(frames[at])) return frames[at];
  return null;
}
export const placeKey = (frame) => (frame ? `${frame.level}:${frame.area}` : null);
const placeQuery = (key) => { const [level, area] = key.split(":"); return `level=${level}&area=${area}`; };

// A camera as a preset keeps it: where it looks from and at, its screen roll and field of view.
export const presetCamera = (pose) => ({ focus: [...pose.focus], position: [...pose.position], roll: pose.roll ?? null,
  fov: pose.fov ?? null });

// The same camera to the eye: places within a hundredth of a unit (a saved camera comes back as the
// orbit the free camera rebuilds from it), the same roll and field of view.
export function sameCamera(a, b) {
  const near = (p, q) => p.every((value, i) => Math.abs(value - q[i]) < 0.01);
  return !!a && !!b && near(a.focus, b.focus) && near(a.position, b.position) && (a.roll ?? 0) === (b.roll ?? 0)
    && (a.fov ?? null) === (b.fov ?? null);
}

const bySlot = (slots) => [...slots].sort((a, b) => a.slot - b.slot);

// Every set of slots the page has read, by place key: {state: "loading"|"ready"|"failed", slots,
// problem}. A change shows at once and goes to the lab as the area's whole set, one write at a time;
// only the newest change's answer is adopted, and a refused one shows the lab's words and reads the
// set again. `undo` is the last deleted camera until the next change.
export class CameraPresets {
  constructor({ changed }) {
    Object.assign(this, { changed, sets: new Map(), writing: Promise.resolve(), revision: 0, undo: null });
  }

  // A new scene reads every set again.
  forget() { Object.assign(this, { sets: new Map(), undo: null }); }

  of(key) { return key ? this.sets.get(key) || null : null; }

  slot(key, number) { return this.of(key)?.slots.find((entry) => entry.slot === number) || null; }

  load(key) {
    if (!key || this.sets.has(key)) return;
    this.sets.set(key, { state: "loading", slots: [], problem: null });
    getJSON(`${ROOT}?${placeQuery(key)}`).then(
      (answer) => this.sets.set(key, { state: "ready", slots: bySlot(answer.slots || []), problem: answer.problem || null }),
      (problem) => this.sets.set(key, { state: "failed", slots: [], problem: problem.message }),
    ).finally(() => this.changed());
  }

  write(key, slots, undo = null) {
    const revision = ++this.revision, set = this.of(key);
    if (!set || set.state !== "ready") return;
    Object.assign(set, { slots: bySlot(slots), problem: null });
    this.undo = undo;
    this.changed();
    this.writing = this.writing.then(() => send("PUT", `${ROOT}?${placeQuery(key)}`, { slots: set.slots })).then(
      (answer) => { if (revision === this.revision && this.of(key) === set) set.slots = bySlot(answer.slots); },
      (problem) => {
        if (revision !== this.revision) return null;
        set.problem = problem.message;
        return getJSON(`${ROOT}?${placeQuery(key)}`).then((answer) => {
          if (revision === this.revision && this.of(key) === set) set.slots = bySlot(answer.slots || []);
        }, () => {});
      },
    ).finally(() => this.changed());
  }

  others(key, ...numbers) { return (this.of(key)?.slots || []).filter((entry) => !numbers.includes(entry.slot)); }

  save(key, number, camera, name = defaultName(number)) {
    this.write(key, [...this.others(key, number), { slot: number, name, camera }]);
  }

  rename(key, number, name) {
    const entry = this.slot(key, number), cleaned = name.split(/\s+/).filter(Boolean).join(" ").slice(0, MAX_NAME);
    if (entry && cleaned && cleaned !== entry.name) this.write(key, [...this.others(key, number), { ...entry, name: cleaned }]);
  }

  remove(key, number) {
    const entry = this.slot(key, number);
    if (entry) this.write(key, this.others(key, number), { key, entry });
  }

  // The last deleted camera back in its slot, unless something took that slot since.
  restore() {
    const { key, entry } = this.undo || {};
    if (entry && !this.slot(key, entry.slot)) this.write(key, [...this.others(key), entry]);
  }

  // Dragged from slot `from` onto slot `to`: it moves there, and a camera already there takes its place.
  move(key, from, to) {
    const moving = this.slot(key, from), there = this.slot(key, to);
    if (!moving || from === to) return;
    this.write(key, [...this.others(key, from, to), { ...moving, slot: to }, ...(there ? [{ ...there, slot: from }] : [])]);
  }
}

// What the strip says under its heading: why it cannot be used, the lab's problem, or how to use it.
function stripNote(link, key, set) {
  if (!link.freeAvailable) return { text: link.scene.free_camera?.reason || "The free camera is unavailable.", bad: false };
  if (!key) return { text: "This picture does not say which course and area it shows, so no cameras are kept for it.", bad: false };
  if (set?.problem) return { text: set.problem, bad: true };
  if (!set || set.state === "loading") return { text: "Reading the saved cameras...", bad: false };
  return { text: "Keys 1-9 cut to a saved camera. Cameras are kept for this course and area.", bad: false };
}

// A slot's name while it is being typed: Enter or leaving the field keeps it, Esc keeps the old one.
// Enter and Esc hand the keyboard back to the viewer, so the number keys cut again at once.
function NameField({ entry, done }) {
  const field = useRef(null), finished = useRef(false);
  useLayoutEffect(() => { field.current?.focus({ preventScroll: true }); field.current?.select(); }, []);   // before a key can land
  const finish = (keep, giveBack = false) => {
    if (finished.current) return;
    finished.current = true;
    done(keep ? field.current.value : null);
    if (giveBack) field.current.closest(".scene-lab")?.focus({ preventScroll: true });
  };
  return html`<input ref=${field} type="text" class="scene-lab-slot-name-field" maxlength=${MAX_NAME}
    value=${entry.name} aria-label=${`Name for camera ${entry.slot}`}
    onkeydown=${(event) => {
      if (event.key === "Enter") { event.preventDefault(); finish(true, true); }
      if (event.key === "Escape") { event.preventDefault(); finish(false, true); }
    }}
    onblur=${() => finish(true)} />`;
}

// The Cameras strip: nine slots for the place the picture on screen is in.
export function CameraSlots({ link }) {
  const presets = link.presets, key = link.presetKey, set = presets.of(key);
  const [renaming, setRenaming] = useState(null);           // {key, slot}
  const [drag, setDrag] = useState({ from: null, over: null });
  useEffect(() => { if (link.freeAvailable) presets.load(key); }, [key, link.freeAvailable, link.scene]);   // a new scene reads again
  const usable = link.freeAvailable && set?.state === "ready";
  const here = link.free && link.orbit ? cameraOf(link.orbit) : null;
  const note = stripNote(link, key, set);
  async function saveHere(number) {
    const camera = await link.cameraOnScreen();
    if (!camera || presets.of(key)?.state !== "ready") return;
    presets.save(key, number, camera);
    setRenaming({ key, slot: number });
  }
  const dragTo = (number) => ({
    ondragover: (event) => {
      if (drag.from == null || drag.from === number) return;
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
      if (drag.over !== number) setDrag({ ...drag, over: number });
    },
    ondragleave: () => { if (drag.over === number) setDrag({ ...drag, over: null }); },
    ondrop: (event) => {
      event.preventDefault();
      if (drag.from != null) presets.move(key, drag.from, number);
      setDrag({ from: null, over: null });
    },
  });
  const filled = (entry) => {
    const pressed = !!here && sameCamera(here, entry.camera);
    if (renaming?.key === key && renaming.slot === entry.slot) {
      return html`<div class="scene-lab-slot-edit"><kbd>${entry.slot}</kbd><${NameField} entry=${entry} done=${(name) => {
        setRenaming(null);
        if (name != null) presets.rename(key, entry.slot, name);
      }} /></div>`;
    }
    return html`<button type="button" class="scene-lab-slot-cut" aria-pressed=${pressed} disabled=${!usable}
        aria-label=${`${entry.name}: camera ${entry.slot}`}
        title=${`Cut to ${entry.name} (${entry.slot}). Drag it onto another slot to move it there.`}
        onclick=${(event) => link.recallSlot(entry.slot, event.timeStamp)}>
      <kbd>${entry.slot}</kbd><span class="scene-lab-slot-name">${entry.name}</span></button>
    <span class="scene-lab-slot-tools">
      <button type="button" disabled=${!usable} aria-label=${`Rename ${entry.name}`} title="Rename"
        onclick=${() => setRenaming({ key, slot: entry.slot })}><${Icon} name="edit" size=${12} /></button>
      <button type="button" disabled=${!usable} aria-label=${`Delete ${entry.name}`} title="Delete"
        onclick=${() => presets.remove(key, entry.slot)}><${Icon} name="trash" size=${12} /></button>
    </span>`;
  };
  const empty = (number) => html`<button type="button" class="scene-lab-slot-save" disabled=${!usable}
      aria-label=${`Save current view in slot ${number}`} title="Save current view here"
      onclick=${() => saveHere(number)}>
    <kbd>${number}</kbd><span class="scene-lab-slot-name"><${Icon} name="plus" size=${12} /> Save</span></button>`;
  const undone = presets.undo?.key === key ? presets.undo.entry : null;
  return html`<div class="scene-lab-cameras" role="group" aria-label="Cameras">
    <div class="scene-lab-cameras-head">
      <h3>Cameras</h3>
      <p class=${`scene-lab-hint${note.bad ? " is-bad" : ""}`} role=${note.bad ? "status" : null}>${note.text}</p>
      ${undone && html`<span class="scene-lab-cameras-undo">Deleted "${undone.name}" from slot ${undone.slot}.${" "}
        <button type="button" onclick=${() => presets.restore()}>Undo</button></span>`}
    </div>
    <ol class="scene-lab-slots">
      ${SLOTS.map((number) => {
        const entry = presets.slot(key, number), movable = usable && !!entry && renaming?.slot !== number;
        const classes = ["scene-lab-slot", entry ? "is-filled" : "is-empty", drag.over === number && "is-target",
          drag.from === number && "is-dragged"];
        return html`<li key=${number} class=${classes.filter(Boolean).join(" ")} data-slot=${number} draggable=${movable}
            ondragstart=${(event) => {
              if (!movable) return event.preventDefault();
              if (event.dataTransfer) {
                event.dataTransfer.effectAllowed = "move";
                event.dataTransfer.setData("text/plain", String(number));
              }
              setDrag({ from: number, over: null });
            }}
            ondragend=${() => setDrag({ from: null, over: null })} ...${dragTo(number)}>
          ${entry ? filled(entry) : empty(number)}
        </li>`;
      })}
    </ol>
  </div>`;
}
