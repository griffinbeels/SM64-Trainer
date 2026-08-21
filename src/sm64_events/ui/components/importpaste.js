// src/sm64_events/ui/components/importpaste.js — paste the times you have.
//
// The door for everyone whose golds are not on the Ultimate Sheet: a personal
// spreadsheet, a note file, a Discord message they wrote years ago.
//
// THE FORMAT IS NOT ONE WE INVENTED, which is the whole reason this is usable
// at all. One time per line, the target before it, the strategy after; tabs,
// runs of spaces, commas and pipes all separate, so a spreadsheet paste, a
// hand-aligned block and a CSV all read the same with nothing to configure.
// The names it answers to are the ones already in play — the game's star
// names, the abbreviations runners type, the sheet's own labels, and the
// segments built here.
//
// IT PREVIEWS BEFORE IT WRITES. A few hundred lines is exactly where a silent
// misread is expensive, and the preview runs the SAME planner the button then
// performs, so it answers the question it is asked. What could not be read
// comes back line by line, in the words it was written in — a resolver that
// drops what it did not understand reports a clean import of half the data.
import { h } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { send } from "../api.js";
import { Icon } from "./icons.js";

const html = htm.bind(h);

// Shown in the empty box. Real lines in three of the shapes people already
// have, so the format is learned by reading rather than by documentation.
const PLACEHOLDER = [
  "BoB 1        0:23.57",
  "WF 6         8.86      LJ",
  "Blast Away the Wall, 10.03, Texture",
].join("\n");

const REASONS = {
  no_time: "no time on this line",
  no_target: "a time with nothing to file it under",
  unknown_target: "this name matched no star or segment",
};

function sentence(result) {
  const parts = [];
  if (result.imported) {
    parts.push(`${result.imported} time${result.imported === 1 ? "" : "s"}`);
  }
  if (result.already_faster) {
    parts.push(`${result.already_faster} you had already beaten`);
  }
  if (!parts.length) return "Nothing here beats what you already have.";
  return parts.join(", ");
}

export function ImportPaste({ onDone }) {
  const [text, setText] = useState("");
  const [preview, setPreview] = useState(null);
  const [phase, setPhase] = useState("idle");   // idle | checking | ready
                                                // | working | done | error
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");

  async function post(dryRun) {
    return send("POST", "/api/import/paste", { text, dry_run: dryRun });
  }

  async function check() {
    if (!text.trim() || phase === "checking") return;
    setPhase("checking");
    setError("");
    try {
      setPreview(await post(true));
      setPhase("ready");
    } catch (err) {
      setError(err.message);
      setPhase("error");
    }
  }

  async function run() {
    setPhase("working");
    try {
      const body = await post(false);
      setResult(body);
      setPhase("done");
      if (body.imported && onDone) onDone(body);
    } catch (err) {
      setError(err.message);
      setPhase("error");
    }
  }

  const shown = phase === "done" ? result : preview;
  const rejected = (shown && shown.rejected) || [];

  return html`<div class="importdoor importpaste">
    <div class="settings-section-head">
      <div>
        <h3>Paste times you have written down</h3>
        <p>One per line: the star, the time, and the strategy if you know it.</p>
      </div>
    </div>
    <textarea class="importpaste-box" rows="6" spellcheck="false"
        placeholder=${PLACEHOLDER} value=${text}
        oninput=${(event) => {
          setText(event.target.value);
          setPreview(null);
          setPhase("idle");
        }} />
    <div class="importpaste-row">
      ${phase === "ready" || phase === "done"
        ? html`<button type="button" class="primary-button"
              disabled=${phase === "working" || !(shown && shown.imported)}
              onclick=${run}>
            ${phase === "done" ? "Imported" : `Import ${shown.imported}`}
          </button>`
        : html`<button type="button" class="primary-button"
              disabled=${!text.trim() || phase === "checking"} onclick=${check}>
            ${phase === "checking" ? "Reading…" : "Check what this would add"}
          </button>`}
      ${shown && html`<span class="settings-note importpaste-summary">${
        sentence(shown)}</span>`}
    </div>
    ${shown && shown.without_strategy > 0 && html`<p class="settings-note">${
      shown.without_strategy}${" "}of these name no strategy. They still land
      as your best time — they just show no rank until you pick one on the
      card.</p>`}
    ${rejected.length > 0 && html`<div class="importpaste-rejects">
      <p class="settings-note is-bad">${rejected.length}${" "}
        line${rejected.length === 1 ? "" : "s"} could not be read:</p>
      <ul>
        ${rejected.slice(0, 12).map((row) => html`<li key=${row.line}>
          <span class="importpaste-lineno">${row.line}</span>
          <code>${row.text}</code>
          <span class="meta">${REASONS[row.reason] || row.reason}</span>
        </li>`)}
      </ul>
      ${rejected.length > 12 && html`<p class="settings-note">…and${" "}
        ${rejected.length - 12} more.</p>`}
    </div>`}
    ${phase === "error" && html`<p class="settings-note is-bad">${error}</p>`}
    ${phase === "done" && result && result.imported > 0
      && html`<button type="button" class="quiet-button importpaste-undo"
          onclick=${async () => {
            const body = await send("DELETE", "/api/import/paste");
            setResult({ ...result, imported: 0, undone: body.removed });
            if (onDone) onDone(body);
          }}>
        <${Icon} name="trash" size=${13} />${" "}Undo this import
      </button>`}
  </div>`;
}
