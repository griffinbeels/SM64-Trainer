// src/sm64_events/ui/components/scenebuild.js -- the Ghost Lab's progress while a run becomes the scene
// (lab only).
//
// Watching another run (sceneghosts.js) re-simulates it into a scene of its own the first time
// (server/scene_resim.py: tens of seconds; a cached one in about two). POST /open {ghost} answers once the
// scene is made, and the old scene keeps playing until then; meanwhile /status's "resim" says how far the
// build is: {what, stage, done, total} (total 0 while a step cannot count). The page polls it while a build
// runs and shows it in two places, the row being made and the play bar, through this ONE component, so a
// later shape of that progress (handoff-boundary may add a playable-frames count) changes this file only.
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { getJSON } from "../api.js";
import { SteadyText } from "./steadytext.js";

const html = htm.bind(h);
export const BUILD_POLL = { ms: 400 };

// What each step of a build is called on the page.
const STEP_WORDS = {
  starting: "Starting", "choosing a start": "Finding its start", "re-simulating": "Re-making the run",
  "writing the scene": "Writing the scene", "making the stage": "Making the stage",
};

// The share of the build done, 0 to 1, or null while the step cannot count.
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
        const status = await getJSON("/api/scene-lab/status");
        if (live) setProgress(status.resim || null);
      } catch { /* the next ask tries again */ }
      if (live) timer = setTimeout(ask, BUILD_POLL.ms);
    };
    ask();
    return () => { live = false; clearTimeout(timer); };
  }, [on]);
  return progress;
}

// "Making Sully's scene", or "Making your scene" for a run of yours.
export const buildWords = (name) => (name === "You" || !name ? "Making your scene" : `Making ${name}'s scene`);

// One build's progress: a bar that fills, the words and the share. `compact`: a ghost row's (a thin
// bar along its foot and the share). Its box never depends on the share (SteadyText).
export function BuildProgress({ build, compact = false }) {
  const share = buildShare(build.progress), percent = share == null ? null : Math.round(share * 100);
  const words = buildWords(build.name), step = STEP_WORDS[build.progress?.stage] || "Starting";
  return html`<div class=${`ghost-lab-build${compact ? " is-compact" : ""}`} role="progressbar" aria-label=${words}
      aria-valuemin="0" aria-valuemax="100" aria-valuenow=${percent ?? undefined} aria-valuetext=${step}
      title=${`${words}: ${step}${percent == null ? "" : `, ${percent}%`}`}>
    <span class="ghost-lab-build-fill" style=${`transform: scaleX(${share ?? 0})`}></span>
    ${!compact && html`<span class="ghost-lab-build-words">${words}</span>`}
    <span class="ghost-lab-build-share"><${SteadyText} text=${percent == null ? step : `${percent}%`}
      sizes=${["100%", ...Object.values(STEP_WORDS)]} /></span>
  </div>`;
}
