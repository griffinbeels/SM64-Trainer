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
// It previews before it writes, through the same planner the button then
// performs; everything after the textarea is `importflow.js`.
import { h } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { send } from "../api.js";
import { ImportButton, ImportOutcome, useImportFlow } from "./importflow.js";

const html = htm.bind(h);

// Shown in the empty box. Real lines in three of the shapes people already
// have, so the format is learned by reading rather than by documentation.
const PLACEHOLDER = [
  "BoB 1        0:23.57",
  "WF 6         8.86      LJ",
  "Blast Away the Wall, 10.03, Texture",
].join("\n");

export function ImportPaste({ onDone }) {
  const [text, setText] = useState("");
  const flow = useImportFlow({
    source: "paste", onDone,
    post: (dryRun) => send("POST", "/api/import/paste",
                           { text, dry_run: dryRun }),
  });

  return html`<div class="importdoor importpaste">
    <div class="settings-section-head">
      <div>
        <h3>Paste times you have written down</h3>
        <p>One per line: the star, the time, and the strategy if you know it.</p>
      </div>
    </div>
    <textarea class="importpaste-box" rows="6" spellcheck="false"
        placeholder=${PLACEHOLDER} value=${text}
        oninput=${(event) => { setText(event.target.value); flow.reset(); }} />
    <div class="importdoor-row">
      <${ImportButton} flow=${flow} canCheck=${Boolean(text.trim())}
          checkLabel="Check what this would add" />
    </div>
    <${ImportOutcome} flow=${flow} />
  </div>`;
}
