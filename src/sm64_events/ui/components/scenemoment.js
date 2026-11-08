// src/sm64_events/ui/components/scenemoment.js -- one frame of the Ghost Lab's scene into a Usamune slot
// (Ghost Handoff, lab only). The save-state strip (scenestrip.js) is its page.
//
// His words: "Once we build out the infrastructure so that I can select an arbitrary game state and create a save
// state from it, I'll be able to test independently and gather results." One press makes a save state of the frame
// on screen and puts it in that Usamune slot (server/scene_moments.py): the numbers are the ones Usamune's menu
// shows. The lab's reason shows as it said it, and a press after fixing that uses the same build.
import { send } from "../api.js";

const ROOT = "/api/scene-lab/moment";
export const OLDER = "This recording is older, so a few small values were worked out and may not be exact.";

// Why no press can run on this frame right now, or null.
export function whyNot(link) {
  if (link.scene?.growing) return "This scene is still being recorded. Try again when it ends.";
  const row = link.scene?.frames?.[link.frame];
  if (!row || row.package_frame == null) return "This scene does not say which frame this is.";
  if (row.package_frame === 0) return "This is the first frame. Pick a later frame.";
  return null;
}

// Where the lab finds the scene on screen: the watched ghost's re-made scene, his attempt's, or a lab package.
export function momentSource(scene) {
  if (scene.ghost) return { ghost: scene.ghost };
  return scene.attempt != null ? { attempt: scene.attempt } : { package: scene.name };
}

// One press: the build, then the write (with `picture`, a small JPEG of the frame on screen, which the strip
// shows on that slot). Each step's refusal is the answer. A written answer names the build (US or JP) the save
// state came from, for its flag; one written into the other build's game says so in words.
export async function saveMoment(scene, row, shown, slot, picture = null) {
  const built = await send("POST", ROOT, { ...momentSource(scene), frame: row.package_frame });
  if (built.refused) return { bad: true, text: built.reason };
  const written = await send("POST", `${ROOT}/${built.id}/write`, { slot, ...(picture ? { picture } : {}) });
  if (written.refused) return { bad: true, text: written.reason };
  const crossed = written.translated ? ` ${written.translated.words}` : "";
  const said = { bad: false, text: `Frame ${shown} is in slot ${slot}.${crossed}${built.exact ? "" : ` ${OLDER}`}` };
  return written.build ? { ...said, build: written.build } : said;
}
