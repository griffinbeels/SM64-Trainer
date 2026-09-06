// src/sm64_events/ui/components/importsheet.js — take your Ultimate Sheet
// column with you.
//
// A runner already on the sheet has their times written down in public. This
// is the door that turns a whole column into personal bests in one gesture,
// for the 448 people who have one.
//
// TWO SOURCES, on purpose. The NAMES come from the bundled snapshot
// (`GET /api/library/runners`), so the list fills the instant the drawer opens
// with no network wait; the TIMES come from a fresh download the server makes
// at import time, so what lands is your most recent entries rather than
// whatever we last shipped. That split is the whole reason the picker feels
// instant and the import is still current — and why this door has no preview
// step: the preview would be the same 7 MB download as the import.
//
// Nothing here says a word about what was imported afterwards. Provenance is
// recorded and never drawn: "the user DID beat it. We shouldn't assume they're
// lying." Everything after the picker is `importflow.js`.
import { h } from "preact";
import { useEffect, useMemo, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, pollJob, send } from "../api.js";
import { ImportOutcome, useImportFlow } from "./importflow.js";
import { SearchSelect } from "./searchselect.js";
import { ProgressLine } from "./states.js";

const html = htm.bind(h);

export function ImportSheet({ onDone }) {
  const [runners, setRunners] = useState(null);
  const [runner, setRunner] = useState("");
  // The import is a JOB (round 29): the download + land takes 10-15 s, and
  // a door that sat on its button for all of it read as hung -- "I want to
  // see my progress as it's happening, otherwise it feels laggy and
  // unresponsive." The server reports its real steps (download, build, fit,
  // match, land) and the flow carries them to the line below the button.
  const flow = useImportFlow({
    source: `sheet:${runner}`, onDone,
    post: async (onStep) => {
      const { job_id } = await send("POST", "/api/import/sheet/job", { runner });
      return pollJob(`/api/import/sheet/job/${job_id}`, onStep);
    },
  });

  useEffect(() => {
    let alive = true;
    getJSON("/api/library/runners")
      .then((body) => alive && setRunners(body.runners || []))
      .catch(() => alive && setRunners([]));
    return () => { alive = false; };
  }, []);

  // One flat group: 448 names with no meaningful grouping, and the panel puts
  // a filter box above anything past its floor, which is what makes a list
  // that long usable at all ("any long drop down like this should have a
  // search feature", round 9).
  const groups = useMemo(
    () => [{ label: "", options: (runners || []).map(
      (name) => ({ value: name, label: name })) }],
    [runners]);

  const working = flow.phase === "working";
  return html`<div class="importdoor importsheet">
    <div class="settings-section-head">
      <div>
        <h3>Import from the Ultimate Sheet</h3>
        <p>Already on the sheet? Take your times with you.</p>
      </div>
    </div>
    ${runners == null
      ? html`<p class="settings-note">Reading the sheet's runner list…</p>`
      : html`<div class="importdoor-row importsheet-row">
        <${SearchSelect} groups=${groups} value=${runner}
            valueLabel=${runner || "Find your name"}
            title="Runners on the Ultimate Sheet"
            onChange=${(name) => { setRunner(name); flow.reset(); }} />
        <button type="button" class="primary-button"
            disabled=${!runner || working} onclick=${flow.run}>
          ${working ? "Importing…" : "Import my times"}
        </button>
      </div>`}
    ${working && flow.progress && html`<${ProgressLine}
        className="importsheet-progress"
        progress=${flow.progress.progress} message=${flow.progress.message}
        running=${true} />`}
    ${working && !flow.progress && html`<p class="settings-note">Downloading the
      current sheet so your most recent times are the ones that land.</p>`}
    <${ImportOutcome} flow=${flow} rowNoun="row" />
    <p class="settings-note">Importing again later costs nothing: a time only
      lands when it beats the best you already hold for that star and strategy.</p>
  </div>`;
}
