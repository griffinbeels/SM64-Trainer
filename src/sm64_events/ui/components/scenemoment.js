// src/sm64_events/ui/components/scenemoment.js -- the Scene viewer's Save states row (Ghost Handoff, lab only;
// sceneviewer.js draws it under the Cameras strip).
//
// His words: "Once we build out the infrastructure so that I can select an arbitrary game state and create a save
// state from it, I'll be able to test independently and gather results." One press of a number makes a save state
// of the frame on screen and puts it in that Usamune slot (server/scene_moments.py): the numbers are the ones
// Usamune's menu shows. The lab's reason shows as it said it, and a press after fixing that uses the same build.
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { send } from "../api.js";
import { SLOTS } from "./scenepresets.js";

const html = htm.bind(h);
const ROOT = "/api/scene-lab/moment";
export const HINT = "Press a number to put this frame in that Usamune slot.";
export const OLDER = "This recording is older, so a few small values were worked out and may not be exact.";

// Why no press can run on this frame right now, or null.
export function whyNot(link) {
  if (link.scene?.growing) return "This scene is still being recorded. Try again when it ends.";
  const row = link.scene?.frames?.[link.frame];
  if (!row || row.package_frame == null) return "This scene does not say which frame this is.";
  if (row.package_frame === 0) return "This is the first frame. Pick a later frame.";
  return null;
}

// One press: the build, then the write. Each step's refusal is the answer.
export async function saveMoment(scene, row, shown, slot) {
  const source = scene.attempt != null ? { attempt: scene.attempt } : { package: scene.name };
  const built = await send("POST", ROOT, { ...source, frame: row.package_frame });
  if (built.refused) return { bad: true, text: built.reason };
  const written = await send("POST", `${ROOT}/${built.id}/write`, { slot });
  if (written.refused) return { bad: true, text: written.reason };
  return { bad: false, text: `Frame ${shown} is in slot ${slot}.${built.exact ? "" : ` ${OLDER}`}` };
}

export function SaveStates({ link }) {
  const [busy, setBusy] = useState(null);                // {slot, shown}
  const [said, setSaid] = useState(null);
  const session = link.scene?.session;
  useEffect(() => { setSaid(null); }, [session]);
  const reason = whyNot(link);
  async function press(slot) {
    const at = link.frame, shown = at + 1, scene = link.scene, row = scene.frames[at];
    setBusy({ slot, shown });
    setSaid(null);
    try {
      setSaid({ ...await saveMoment(scene, row, shown, slot), at });
    } catch (error) {
      setSaid({ bad: true, text: error.message, at });
    } finally {
      setBusy(null);
    }
  }
  // An answer belongs to the frame it was for: stepping away takes it down, and Play keeps it up.
  const current = said && (link.playing || said.at === link.frame) ? said : null;
  const note = busy ? { bad: false, text: `Putting frame ${busy.shown} in slot ${busy.slot}...` }
    : current ?? { bad: false, text: reason ?? HINT };
  return html`<div class="scene-lab-moment" role="group" aria-label="Save states">
    <div class="scene-lab-cameras-head">
      <h3>Save states</h3>
      <p class=${`scene-lab-hint${note.bad ? " is-bad" : ""}`} role="status">${note.text}</p>
    </div>
    <ol class="scene-lab-moment-slots">
      ${SLOTS.map((slot) => html`<li key=${slot}>
        <button type="button" disabled=${!!busy || !!reason} aria-busy=${busy?.slot === slot}
          aria-label=${`Put this frame in slot ${slot}`} onclick=${() => press(slot)}><kbd>${slot}</kbd></button></li>`)}
    </ol>
  </div>`;
}
