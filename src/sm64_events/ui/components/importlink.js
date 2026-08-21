// src/sm64_events/ui/components/importlink.js — import from your own sheet.
//
// The door for a player who keeps their times in a spreadsheet of their own:
// paste its address and the workbook says which shape it is. A copy of the
// Ultimate Sheet is read by the real reader and one runner's column taken; any
// other grid becomes lines and goes through the same parser a pasted block
// does, so `star | time | strat` needs no format of its own.
//
// It asks for a NAME only when the sheet turns out to be an Ultimate copy —
// that shape has a column per runner and no way to guess which is yours. Every
// other sheet is entirely yours already, so asking would be a question with
// one answer.
//
// The likeliest failure by far is sharing, so the error says so where the
// person can act on it rather than reporting a bare 404.
import { h } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { send } from "../api.js";
import { Icon } from "./icons.js";

const html = htm.bind(h);

const REASONS = {
  no_time: "no time on this row",
  no_target: "a time with nothing to file it under",
  unknown_target: "this name matched no star or segment",
  no_entity: "rows the trainer has no target for",
  subsections: "rows timing part of a star rather than the star",
  segments: "rows mapped to a movement, which needs one of yours",
};

export function ImportLink({ onDone }) {
  const [url, setUrl] = useState("");
  const [runner, setRunner] = useState("");
  const [preview, setPreview] = useState(null);
  const [phase, setPhase] = useState("idle");   // idle | checking | ready
                                                // | needs_runner | working
                                                // | done | error
  const [error, setError] = useState("");

  async function post(dryRun) {
    return send("POST", "/api/import/link",
                { url, runner, dry_run: dryRun });
  }

  async function check() {
    if (!url.trim() || phase === "checking") return;
    setPhase("checking");
    setError("");
    try {
      setPreview(await post(true));
      setPhase("ready");
    } catch (err) {
      // The one recoverable refusal: an Ultimate copy that needs a name.
      if (/runner/i.test(err.message)) {
        setPhase("needs_runner");
        setError("");
        return;
      }
      setError(err.message);
      setPhase("error");
    }
  }

  async function run() {
    setPhase("working");
    try {
      const body = await post(false);
      setPreview(body);
      setPhase("done");
      if (body.imported && onDone) onDone(body);
    } catch (err) {
      setError(err.message);
      setPhase("error");
    }
  }

  const rejected = (preview && preview.rejected) || [];
  const ready = phase === "ready" || phase === "done";

  return html`<div class="importdoor importlink">
    <div class="settings-section-head">
      <div>
        <h3>Import from your own spreadsheet</h3>
        <p>Paste its address. Anyone with the link has to be able to open it.</p>
      </div>
    </div>
    <div class="importpaste-row">
      <input type="url" class="importlink-url" spellcheck="false"
          placeholder="https://docs.google.com/spreadsheets/…" value=${url}
          oninput=${(event) => {
            setUrl(event.target.value);
            setPreview(null);
            setPhase("idle");
          }} />
      ${ready
        ? html`<button type="button" class="primary-button"
              disabled=${phase === "done" || !preview.imported} onclick=${run}>
            ${phase === "done" ? "Imported" : `Import ${preview.imported}`}
          </button>`
        : html`<button type="button" class="primary-button"
              disabled=${!url.trim() || phase === "checking"} onclick=${check}>
            ${phase === "checking" ? "Reading…" : "Read this sheet"}
          </button>`}
    </div>
    ${phase === "needs_runner" && html`<div class="importpaste-row">
      <input type="text" class="importlink-url" placeholder="Your name on it"
          value=${runner}
          oninput=${(event) => setRunner(event.target.value)} />
      <button type="button" class="primary-button" disabled=${!runner.trim()}
          onclick=${check}>Read my column</button>
    </div>`}
    ${phase === "needs_runner" && html`<p class="settings-note">That is a copy
      of the Ultimate Sheet — it has a column per runner, so it needs to know
      which one is yours.</p>`}
    ${preview && html`<p class=${`settings-note${phase === "done" ? " is-ok" : ""}`}>${
      preview.imported}${" "}time${preview.imported === 1 ? "" : "s"}${" "}
      ${phase === "done" ? "added" : "to add"}${
      preview.already_faster
        ? `, ${preview.already_faster} you had already beaten` : ""}.</p>`}
    ${rejected.length > 0 && html`<div class="importpaste-rejects">
      <p class="settings-note is-bad">${rejected.length}${" "}
        ${rejected.length === 1 ? "row" : "rows"} did not land:</p>
      <ul>
        ${rejected.slice(0, 12).map((row, index) => html`<li key=${index}>
          <code>${row.text}</code>
          <span class="meta">${REASONS[row.reason] || row.reason}</span>
        </li>`)}
      </ul>
      ${rejected.length > 12 && html`<p class="settings-note">…and${" "}
        ${rejected.length - 12} more.</p>`}
    </div>`}
    ${phase === "error" && html`<p class="settings-note is-bad">${error}</p>`}
    ${phase === "done" && preview.imported > 0
      && html`<button type="button" class="quiet-button importpaste-undo"
          onclick=${async () => {
            const body = await send("DELETE", "/api/import/link");
            setPreview({ ...preview, imported: 0 });
            setPhase("idle");
            if (onDone) onDone(body);
          }}>
        <${Icon} name="trash" size=${13} />${" "}Undo this import
      </button>`}
  </div>`;
}
