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
// instant and the import is still current.
//
// The summary is ONE sentence, never a mark per row — and nothing here says a
// word about what was imported afterwards. Provenance is recorded and never
// drawn: "the user DID beat it. We shouldn't assume they're lying."
import { h } from "preact";
import { useEffect, useMemo, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { Icon } from "./icons.js";
import { SearchSelect } from "./searchselect.js";

const html = htm.bind(h);

// What the server reports back, as one sentence. `already_faster` is not a
// failure and must not read like one — it is the improvement rule working.
function sentence(result) {
  const parts = [];
  if (result.imported) {
    parts.push(`${result.imported} time${result.imported === 1 ? "" : "s"} added`);
  }
  if (result.already_faster) {
    parts.push(`${result.already_faster} you had already beaten`);
  }
  const rejected = result.rejected || {};
  const dropped = (rejected.subsections || 0) + (rejected.no_entity || 0)
    + (rejected.segments || 0);
  if (dropped) parts.push(`${dropped} the trainer cannot practice`);
  if (!parts.length) return "That column had no times this trainer can use.";
  // A sentence opening on a bare number reads as a fragment, and the case it
  // opens on is the ORDINARY one -- a second import, where everything is
  // already beaten. Say what happened first.
  const lead = result.imported ? "" : "Nothing new — ";
  return `${lead}${parts.join(", ")}.`;
}

export function ImportSheet({ onDone }) {
  const [runners, setRunners] = useState(null);
  const [runner, setRunner] = useState("");
  const [phase, setPhase] = useState("idle");   // idle | working | done | error
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [removed, setRemoved] = useState(0);

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

  async function run() {
    if (!runner || phase === "working") return;
    setPhase("working");
    setError("");
    try {
      const body = await send("POST", "/api/import/sheet", { runner });
      setResult(body);
      setPhase("done");
      if (onDone) onDone(body);
    } catch (err) {
      setError(err.message);
      setPhase("error");
    }
  }

  async function undo() {
    try {
      const body = await send("DELETE",
                              `/api/import/sheet:${encodeURIComponent(runner)}`);
      setRemoved(body.removed);
      setPhase("undone");
      if (onDone) onDone(body);
    } catch (err) {
      setError(err.message);
      setPhase("error");
    }
  }

  return html`<section class="settings-section importsheet">
    <div class="settings-section-head">
      <div>
        <h3>Import from the Ultimate Sheet</h3>
        <p>Already on the sheet? Take your times with you.</p>
      </div>
    </div>
    ${runners == null
      ? html`<p class="settings-note">Reading the sheet's runner list…</p>`
      : html`<div class="importsheet-row">
        <${SearchSelect} groups=${groups} value=${runner}
            valueLabel=${runner || "Find your name"}
            title="Runners on the Ultimate Sheet"
            onChange=${(name) => { setRunner(name); setPhase("idle"); }} />
        <button type="button" class="primary-button"
            disabled=${!runner || phase === "working"} onclick=${run}>
          ${phase === "working" ? "Reading the sheet…" : "Import my times"}
        </button>
      </div>`}
    ${phase === "working" && html`<p class="settings-note">Downloading the
      current sheet so your most recent times are the ones that land.</p>`}
    ${phase === "done" && result && html`<p class="settings-note is-ok">${
      sentence(result)}</p>`}
    ${/* The undo for the gesture just made. A delete route with nothing
         calling it is a capability that does not exist, and this is the one
         moment it is wanted: he pressed a button, several hundred bests
         landed, and he wants them gone. Erased outright rather than marked
         ("just completely erase them, it's cool"), and latest-row-wins puts
         back whatever each one superseded. Offered only while the import he
         is undoing is still on screen. */""}
    ${phase === "done" && result && result.imported > 0
      && html`<button type="button" class="quiet-button importsheet-undo"
          onclick=${undo}>
        <${Icon} name="trash" size=${13} />${" "}Undo this import
      </button>`}
    ${phase === "undone" && html`<p class="settings-note">${removed}${" "}
      imported ${removed === 1 ? "time" : "times"} erased. Anything each one
      replaced is your best again.</p>`}
    ${phase === "error" && html`<p class="settings-note is-bad">${error}</p>`}
    <p class="settings-note">Importing again later costs nothing: a time only
      lands when it beats the best you already hold for that star and strategy.</p>
  </section>`;
}
