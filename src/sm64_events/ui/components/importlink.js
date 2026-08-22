// src/sm64_events/ui/components/importlink.js — import from your own sheet.
//
// The door for a player who keeps their times in a spreadsheet of their own:
// paste its address and the workbook says which shape it is. A copy of the
// Ultimate Sheet is read by the real reader and one runner's column taken; any
// other grid becomes lines and goes through the same parser a pasted block
// does, so `star | time | strat` needs no format of its own.
//
// It asks for a NAME only when the sheet turns out to be an Ultimate copy —
// the preview comes back saying `needs_runner`, because that shape has a
// column per runner and no way to guess which is yours. Every other sheet is
// entirely yours already, so asking would be a question with one answer.
//
// The likeliest failure by far is sharing, so the server's error says so where
// the person can act on it rather than reporting a bare 404. Everything after
// the address field is `importflow.js`.
import { h } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { send } from "../api.js";
import { ImportButton, ImportOutcome, useImportFlow } from "./importflow.js";

const html = htm.bind(h);

export function ImportLink({ onDone }) {
  const [url, setUrl] = useState("");
  const [runner, setRunner] = useState("");
  const flow = useImportFlow({
    source: "link", onDone,
    post: (dryRun) => send("POST", "/api/import/link",
                           { url, runner, dry_run: dryRun }),
  });
  const needsRunner = flow.phase === "ready" && flow.shown.needs_runner;

  return html`<div class="importdoor importlink">
    <div class="settings-section-head">
      <div>
        <h3>Import from your own spreadsheet</h3>
        <p>Paste its address. Anyone with the link has to be able to open it.</p>
      </div>
    </div>
    <div class="importdoor-row">
      <input type="url" class="importlink-url" spellcheck="false"
          placeholder="https://docs.google.com/spreadsheets/…" value=${url}
          oninput=${(event) => { setUrl(event.target.value); flow.reset(); }} />
      ${!needsRunner && html`<${ImportButton} flow=${flow}
          canCheck=${Boolean(url.trim())} checkLabel="Read this sheet" />`}
    </div>
    ${needsRunner && html`<div class="importdoor-row">
      <input type="text" class="importlink-url" placeholder="Your name on it"
          value=${runner}
          oninput=${(event) => setRunner(event.target.value)} />
      <button type="button" class="primary-button" disabled=${!runner.trim()}
          onclick=${flow.check}>Read my column</button>
    </div>
    <p class="settings-note">That is a copy of the Ultimate Sheet — it has a
      column per runner, so it needs to know which one is yours.</p>`}
    ${!needsRunner && html`<${ImportOutcome} flow=${flow} rowNoun="row" />`}
  </div>`;
}
