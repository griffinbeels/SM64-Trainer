// src/sm64_events/ui/components/importlivesplit.js — your LiveSplit golds.
//
// A gold is the fastest a split has ever been, which is proof of the best you
// have ever done one piece of the run — exactly the thing a practicer already
// has and this tool did not.
//
// TWO RULES SHOW UP IN THE COPY, because both would otherwise read as bugs.
// A gold is a REAL-TIME stretch of the run, so it lands on a segment you built
// here and never on a star, whose bests are Usamune IGT — a real-time split
// filed against an IGT ladder reads as a wildly good time. And splits are
// matched by NAME, so what does not match is named back rather than counted
// away: a file of thirty splits that lands four owes you twenty-six answers.
//
// Picking the file IS the check: the preview runs the moment a file is chosen.
// Everything after the picker is `importflow.js`.
import { h } from "preact";
import { useRef, useState } from "preact/hooks";
import htm from "htm";
import { ImportButton, ImportOutcome, useImportFlow } from "./importflow.js";

const html = htm.bind(h);

async function postSplits(bytes, dryRun) {
  // Raw bytes, not multipart: the server takes the request body itself
  // rather than pulling `python-multipart` into the frozen exe for one field
  // (the same call `server/compare_api.py`'s upload already makes).
  const reply = await fetch(
    `/api/import/livesplit${dryRun ? "?dry_run=true" : ""}`,
    { method: "POST", body: bytes,
      headers: { "Content-Type": "application/octet-stream" } });
  const payload = await reply.json().catch(() => null);
  if (!reply.ok) {
    throw new Error((payload && payload.detail) || `${reply.status}`);
  }
  return payload;
}

export function ImportLiveSplit({ onDone }) {
  // A ref, not state: the check fires in the same tick as the pick, before a
  // state update could have landed.
  const bytes = useRef(null);
  const [name, setName] = useState("");
  const flow = useImportFlow({
    source: "livesplit", onDone,
    post: (dryRun) => postSplits(bytes.current, dryRun),
  });

  async function pick(event) {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    setName(file.name);
    bytes.current = await file.arrayBuffer();
    flow.check();
  }

  return html`<div class="importdoor importlivesplit">
    <div class="settings-section-head">
      <div>
        <h3>Import your LiveSplit golds</h3>
        <p>Your best-ever time for each split, filed against the segments you
          have built here.</p>
      </div>
    </div>
    <div class="importdoor-row">
      <input type="file" accept=".lss,application/xml,text/xml"
          class="importlivesplit-file" onchange=${pick} />
      ${flow.phase !== "idle" && html`<${ImportButton} flow=${flow}
          canCheck=${bytes.current != null} checkLabel="Read this file" />`}
    </div>
    ${name && html`<p class="settings-note">${name}</p>`}
    <${ImportOutcome} flow=${flow} noun="gold" rowNoun="split" />
  </div>`;
}
