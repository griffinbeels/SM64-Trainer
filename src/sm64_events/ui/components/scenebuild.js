// src/sm64_events/ui/components/scenebuild.js -- the Ghost Lab's progress while a run becomes the scene
// (lab only).
//
// Watching another run (sceneghosts.js) re-simulates it into a scene of its own (server/scene_resim.py). A
// build goes in two parts, and this ONE module says how far it is in both, for the row being made, the
// picture's foot and the play bar's slider:
// - Before its first frames exist (a stage made cold: seconds), POST /open {ghost} has not answered and the
//   old scene keeps playing. /status's "resim" says how far that is, {what, stage, done, total} (total 0
//   while a step cannot count); the page asks every BUILD_POLL.ms.
// - Once the first frames are built (about a second with a stage made ahead), /open answers with the scene
//   growing: "growing" true, "frames" the part built so far, "resim" {start, building: true, records,
//   first_record, built_to, parting_frame, save_states}. Each {"kind": "grown"} message on the scene's socket
//   adds frames and the newest table; the last one says "growing" false with the final table (no "building":
//   it is done). The share built is frames / (records - first_record); the slider spans the whole run and
//   the part not built yet is drawn as an unfilled stretch that cannot be reached.
// Stop (POST /open/cancel) ends a build: the scene keeps the frames it has ("cancelled" in its last table).
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { Icon } from "./icons.js";
import { SteadyText } from "./steadytext.js";

const html = htm.bind(h);
const ROOT = "/api/scene-lab";
export const BUILD_POLL = { ms: 400 };

// What each step of a build is called on the page.
const STEP_WORDS = {
  starting: "Starting", "choosing a start": "Finding its start", "re-simulating": "Re-making the run",
  "writing the scene": "Writing the scene", "making the stage": "Making the stage",
};

// The share of a cold build done, 0 to 1, or null while the step cannot count.
export function buildShare(progress) {
  if (!progress || !(progress.total > 0)) return null;
  return Math.max(0, Math.min(1, progress.done / progress.total));
}

// /status's "resim" while `on`, asked every BUILD_POLL.ms; null otherwise.
export function useBuildProgress(on) {
  const [progress, setProgress] = useState(null);
  useEffect(() => {
    if (!on) {
      setProgress(null);
      return undefined;
    }
    let live = true, timer = null;
    const ask = async () => {
      try {
        const status = await getJSON(`${ROOT}/status`);
        if (live) setProgress(status.resim || null);
      } catch { /* the next ask tries again */ }
      if (live) timer = setTimeout(ask, BUILD_POLL.ms);
    };
    ask();
    return () => { live = false; clearTimeout(timer); };
  }, [on]);
  return progress;
}

// The scene on screen while its run is still being built: {built, total, share} in frames (the slider's
// whole length is `total`), or null for a whole scene.
export function sceneGrowth(scene) {
  const resim = scene?.resim;
  if (!scene?.growing || !resim?.building) return null;
  const built = scene.frames.length;
  const total = Math.max(built, (resim.records ?? 0) - (resim.first_record ?? 1));
  return { built, total, share: total > 0 ? built / total : 0 };
}

// The build to show, or null: `pending` ({ref, name}) is a watch whose /open has not answered (its share from
// /status, `progress`); else the scene on screen still growing from its run.
export function buildOf(scene, pending, progress) {
  if (pending) {
    return { ...pending, share: buildShare(progress), step: STEP_WORDS[progress?.stage] || STEP_WORDS.starting };
  }
  const growth = sceneGrowth(scene);
  if (!growth || !scene.ghost) return null;
  return { ref: scene.ghost, name: scene.watching?.player, share: growth.share, step: STEP_WORDS["re-simulating"] };
}

// Ends the running build; the scene keeps what it has. {cancelled} as the lab answered.
export const stopBuild = () => send("POST", `${ROOT}/open/cancel`);

// "Making Sully's scene", or "Making your scene" for a run of yours.
export const buildWords = (name) => (name === "You" || !name ? "Making your scene" : `Making ${name}'s scene`);

// One build's progress: a bar that fills, the words, the share and Stop. `compact`: a ghost row's (a thin
// bar along its foot and the share). Its box never depends on the share (SteadyText).
export function BuildProgress({ build, compact = false, stop = null }) {
  const percent = build.share == null ? null : Math.round(build.share * 100), words = buildWords(build.name);
  return html`<div class=${`ghost-lab-build${compact ? " is-compact" : ""}`} role="progressbar" aria-label=${words}
      aria-valuemin="0" aria-valuemax="100" aria-valuenow=${percent ?? undefined} aria-valuetext=${build.step}
      title=${`${words}: ${build.step}${percent == null ? "" : `, ${percent}%`}`}>
    <span class="ghost-lab-build-fill" style=${`transform: scaleX(${build.share ?? 0})`}></span>
    ${!compact && html`<span class="ghost-lab-build-words">${words}</span>`}
    <span class="ghost-lab-build-share"><${SteadyText} text=${percent == null ? build.step : `${percent}%`}
      sizes=${["100%", ...Object.values(STEP_WORDS)]} /></span>
    ${!compact && stop && html`<button type="button" class="icon-button ghost-lab-build-stop" aria-label="Stop making the scene"
      title="Stop making the scene (it keeps the frames it has)" onclick=${stop}><${Icon} name="close" size=${14} /></button>`}
  </div>`;
}

// The slider's stretch not built yet: from the last frame built (as far as a drag reaches; `data-reach`, 0 to
// 1 of the slider's travel) to the end, unfilled. The thumb's half width (THUMB_HALF px) sits at each end of
// that travel. Nothing while whole.
const THUMB_HALF = 8;
export function Unbuilt({ scene }) {
  const growth = sceneGrowth(scene);
  if (!growth) return null;
  const reach = growth.total > 1 ? Math.max(0, growth.built - 1) / (growth.total - 1) : 0;
  return html`<span class="ghost-lab-unbuilt" aria-hidden="true" data-reach=${reach.toFixed(4)}
    style=${`left: calc(${reach.toFixed(4)} * (100% - ${2 * THUMB_HALF}px) + ${THUMB_HALF}px)`}></span>`;
}
