// src/sm64_events/ui/components/compressionjobs.js — THE list of saved replays
// being made smaller: one row per job, what it is doing in plain words, and a
// bar that moves while it works. ONE implementation, two surfaces: the close
// warning (closewarning.js) and the recording-dot panel (replay.js). The words
// and the shared poller live in ../compression.js.
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { jobFraction, jobName, jobStatus, watchCompression } from "../compression.js";
import { Icon } from "./icons.js";

const html = htm.bind(h);

// The job list while `watching`, else null. Until the first look comes back
// `loaded` is false: a surface must tell "nothing to do" from "not known
// yet". A look that FAILS comes back as an idle list (../compression.js).
export function useCompression(watching) {
  const [state, setState] = useState(null);
  useEffect(() => {
    if (!watching) { setState(null); return undefined; }
    return watchCompression(setState);
  }, [watching]);
  return watching ? state : null;
}

// Matches the bar's width transition in index.html (`.compression-meter`).
const ARRIVAL_MS = 600;

function CompressionJob({ job }) {
  // A row's bar is drawn empty and handed its real width a frame later, so a
  // job first seen at 41% grows to it instead of appearing there. That first
  // growth starts from rest (`is-arriving` eases it in); every later move is
  // one poll handing into the next, at constant speed.
  const [drawn, setDrawn] = useState(false);
  const [arriving, setArriving] = useState(true);
  useEffect(() => {
    const frame = requestAnimationFrame(() => setDrawn(true));
    const settled = setTimeout(() => setArriving(false), ARRIVAL_MS);
    return () => { cancelAnimationFrame(frame); clearTimeout(settled); };
  }, []);
  const percent = Math.round(jobFraction(job) * 100);
  const status = jobStatus(job);
  const finished = job.stage === "done" || job.stage === "in_use";
  return html`<li class=${`compression-job is-${job.stage} ${arriving ? "is-arriving" : ""}`}>
    <div class="compression-job-head">
      <b class="compression-job-name">${jobName(job)}</b>
      ${job.time_text ? html`<span class="compression-job-time">${job.time_text}</span>` : null}
    </div>
    <div class="compression-job-status">
      ${finished ? html`<${Icon} name="check" size=${13} />` : null}
      <span>${status}</span>
    </div>
    <div class="compression-meter" role="progressbar" aria-label=${jobName(job)}
        aria-valuemin="0" aria-valuemax="100" aria-valuenow=${percent}
        aria-valuetext=${status}>
      <div style=${`width:${drawn ? percent : 0}%`}></div>
    </div>
  </li>`;
}

export function CompressionJobs({ jobs }) {
  // A job re-run from an older session can arrive with no attempt id; its
  // place in the list then stands in for the identity it does not have.
  return html`<ul class="compression-jobs">
    ${jobs.map((job, index) => html`<${CompressionJob}
      key=${job.attempt_id ?? `unnamed-${index}`} job=${job} />`)}
  </ul>`;
}
