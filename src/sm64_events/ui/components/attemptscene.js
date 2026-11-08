// src/sm64_events/ui/components/attemptscene.js -- the Practice log's Ghost
// button (M4, lab only): one click opens that attempt's scene in the Scene
// viewer and plays it.
//
// The lab cuts each finished attempt into a scene (server/scene_lab_api.py).
// GET /api/scene-lab/attempt-scenes?ids= answers, per attempt id, whether its
// scene is "ready", still "cutting", or "unavailable" with the reason, and
// leaves out ids the lab knows nothing about: those rows draw no button. A
// table asks once for the rows it shows whenever that set of ids changes, and
// again every SCENE_POLL.ms only while one of them is still cutting, so a row
// whose cut lands turns its button on by itself. It also asks again when the
// caller's `revision` changes: the newest `ghost_files_changed` event, which a
// Settings -> Ghost files purge broadcasts (ghoststorage.js), so a row whose
// scene was deleted loses its button without a reload. With the lab off
// (`t.sceneLab` is null, app.js) nothing is asked and no row draws it.
// Nothing here imports the viewer: app.js loads it only once the lab answers.
import { createContext, h } from "preact";
import { useContext, useEffect, useState } from "preact/hooks";
import htm from "htm";
import { getJSON } from "../api.js";
import { Icon } from "./icons.js";

const html = htm.bind(h);
export const SCENE_POLL = { ms: 1000 };
export const OPEN_IN_VIEWER = "Open in Ghost Lab";
export const SCENE_FINISHING = "Scene finishing…";

// What a row's button shows for its scene: null (no button) when the lab knows nothing of it.
export function sceneButton(scene) {
  if (!scene) return null;
  if (scene.state === "ready") return { disabled: false, title: OPEN_IN_VIEWER };
  if (scene.state === "cutting") return { disabled: true, title: SCENE_FINISHING };
  return { disabled: true, title: scene.reason || "This attempt has no scene." };
}

const cutting = (scenes) => Object.values(scenes).some((scene) => scene?.state === "cutting");

// The lab's answers for the attempt ids a table shows, keyed by id ({} with the lab off or
// before the first answer). A failed ask keeps the last answers and, while one was cutting,
// asks again on the same beat. A new `revision` (any value; compared by identity) asks again
// for the same ids; keep it stable across renders, or every render asks.
export function useAttemptScenes(ids, enabled, revision = null) {
  const key = enabled ? [...new Set(ids)].sort((a, b) => a - b).join(",") : "";
  const [scenes, setScenes] = useState({});
  useEffect(() => {
    if (!key) return undefined;
    let live = true, timer = null, last = {};
    const ask = async () => {
      try {
        last = (await getJSON(`/api/scene-lab/attempt-scenes?ids=${key}`)).scenes || {};
        if (live) setScenes(last);
      } catch { /* the lab may not answer yet: no buttons, never an error on the page */ }
      if (live && cutting(last)) timer = setTimeout(ask, SCENE_POLL.ms);
    };
    ask();
    return () => { live = false; clearTimeout(timer); };
  }, [key, revision]);
  return key ? scenes : {};
}

// A table's answers, read by each row's button (AttemptTable provides them), so no
// row component gains a prop.
export const AttemptScenes = createContext({});

// The ghost mark the shell's own "Scene viewer" entry wears, the play button's size and frame.
export function SceneButton({ attempt, open }) {
  const shown = sceneButton(useContext(AttemptScenes)[attempt]);
  if (!shown) return null;
  return html`<${GhostButton} disabled=${shown.disabled} title=${shown.title} open=${open} />`;
}

// The one Ghost button: the Practice log's rows (above) and the Library's community ghosts
// (community-library.js), which open that ghost in the Scene viewer. `label` names it for a reader.
export function GhostButton({ open, disabled = false, title = OPEN_IN_VIEWER, label = OPEN_IN_VIEWER }) {
  return html`<button type="button" class="icon-button attempt-scene" disabled=${disabled}
      title=${title} aria-label=${label} onclick=${disabled ? null : open}>
    <${Icon} name="ghost" size=${16} /></button>`;
}
