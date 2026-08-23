// src/sm64_events/ui/components/importflow.js — what every import door shares.
//
// A door is an INPUT — a name picker, a URL field —
// and everything after the input is the same for all of them: preview, import,
// one sentence saying what happened, the rows that could not be read, and an
// undo. Each door used to carry its own copy of that, which meant a wording
// change was several edits and a new door was another copy. It lives here once.
//
// `useImportFlow` is the state machine; `ImportButton` and `ImportOutcome`
// draw it. A door renders its input, the button and the outcome, and is done.
//
// The server answers every door in ONE shape (`server/import_api.py::finish`):
// `{imported, already_faster, without_strategy, rejected: [{line, text,
// reason}], dry_run, ...}`. That is what lets the outcome be one component.
import { h } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { send } from "../api.js";
import { Icon } from "./icons.js";

const html = htm.bind(h);

// Every reason any door can report, in words a person can act on.
export const REASONS = {
  no_time: "no time on this line",
  no_target: "a time with nothing to file it under",
  unknown_target: "this name matched no star or segment",
  no_entity: "rows the trainer has no target for",
  subsections: "rows timing part of a star rather than the star",
  segments: "rows mapped to a movement, which needs one of yours",
};

// How many rejected rows are drawn before "…and N more".
const REJECTS_SHOWN = 12;

function plural(count, noun) {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

// `post(dryRun) -> Promise<summary>` is the door's own request; `source` is
// what `DELETE /api/import/<source>` erases. Phases:
//   idle | checking | ready | working | done | undone | error
export function useImportFlow({ source, post, onDone }) {
  const [phase, setPhase] = useState("idle");
  const [shown, setShown] = useState(null);     // the latest summary
  const [error, setError] = useState("");

  async function attempt(busyPhase, work) {
    setPhase(busyPhase);
    setError("");
    try {
      await work();
    } catch (err) {
      setError(err.message);
      setPhase("error");
    }
  }

  return {
    phase, shown, error,
    reset() { setShown(null); setPhase("idle"); setError(""); },
    check: () => attempt("checking", async () => {
      setShown(await post(true));
      setPhase("ready");
    }),
    run: () => attempt("working", async () => {
      const body = await post(false);
      setShown(body);
      setPhase("done");
      if (body.imported && onDone) onDone(body);
    }),
    undo: () => attempt("working", async () => {
      const body = await send(
        "DELETE", `/api/import/${encodeURIComponent(source)}`);
      setShown({ ...shown, imported: 0, removed: body.removed });
      setPhase("undone");
      if (onDone) onDone(body);
    }),
  };
}

// The one action button: CHECK until there is a preview, then IMPORT N.
// `canCheck` is whether the door's input holds anything to read yet.
export function ImportButton({ flow, canCheck, checkLabel, noun = "time" }) {
  const { phase, shown } = flow;
  if (phase === "ready" || phase === "done") {
    return html`<button type="button" class="primary-button"
        disabled=${phase === "done" || !shown.imported} onclick=${flow.run}>
      ${phase === "done" ? "Imported" : `Import ${shown.imported}`}
    </button>`;
  }
  return html`<button type="button" class="primary-button"
      disabled=${!canCheck || phase === "checking" || phase === "working"}
      onclick=${flow.check}>
    ${phase === "checking" ? "Reading…" : checkLabel}
  </button>`;
}

// What the server reported, as one sentence. `already_faster` is not a
// failure and must not read like one — it is the improvement rule working.
// Opens on what HAPPENED rather than on a bare number, because the ordinary
// case of a second import is that everything was already beaten.
export function outcomeSentence(shown, done, noun = "time") {
  const parts = [];
  if (shown.imported) {
    parts.push(`${plural(shown.imported, noun)} ${done ? "added" : "to add"}`);
  }
  if (shown.already_faster) {
    parts.push(`${shown.already_faster} you had already beaten`);
  }
  if (!parts.length) return "Nothing here beats what you already have.";
  return `${shown.imported ? "" : "Nothing new — "}${parts.join(", ")}.`;
}

// The sentence, the strategy-less note, the rows that could not be read, the
// error, the undo and the undone note — everything below a door's input.
export function ImportOutcome({ flow, noun = "time", rowNoun = "line" }) {
  const { phase, shown, error } = flow;
  if (phase === "undone") {
    // The batch is gone, so everything that described it goes with it.
    return html`<div class="importdoor-outcome"><p class="settings-note">${
      plural(shown.removed || 0, `imported ${noun}`)} erased. Anything each
      one replaced is your best again.</p></div>`;
  }
  const done = phase === "done";
  const rejected = (shown && shown.rejected) || [];
  return html`<div class="importdoor-outcome">
    ${shown && html`<p
        class=${`settings-note importdoor-summary${done ? " is-ok" : ""}`}>${
      outcomeSentence(shown, done, noun)}</p>`}
    ${shown && shown.without_strategy > 0 && html`<p class="settings-note">${
      shown.without_strategy}${" "}of these name no strategy. They still land
      as your best time — they just show no rank until you pick one on the
      card.</p>`}
    ${rejected.length > 0 && html`<div class="importdoor-rejects">
      <p class="settings-note is-bad">${plural(rejected.length, rowNoun)}${" "}
        could not be used:</p>
      <ul>
        ${rejected.slice(0, REJECTS_SHOWN).map((row, index) => html`<li
            key=${`${row.line}-${index}`}>
          ${row.line > 0 && html`<span class="importdoor-lineno">${
            row.line}</span>`}
          <code>${row.text}</code>
          <span class="meta">${REASONS[row.reason] || row.reason}</span>
        </li>`)}
      </ul>
      ${rejected.length > REJECTS_SHOWN && html`<p class="settings-note">…and${
        " "}${rejected.length - REJECTS_SHOWN} more.</p>`}
    </div>`}
    ${phase === "error" && html`<p class="settings-note is-bad">${error}</p>`}
    ${/* The undo for the gesture just made. A delete route with nothing
         calling it is a capability that does not exist, and this is the one
         moment it is wanted: he pressed a button, several hundred bests
         landed, and he wants them gone. Erased outright rather than marked
         ("just completely erase them, it's cool"), and latest-row-wins puts
         back whatever each one superseded. Offered only while the import he
         is undoing is still on screen. */""}
    ${done && shown.imported > 0 && html`<button type="button"
        class="quiet-button importdoor-undo" onclick=${flow.undo}>
      <${Icon} name="trash" size=${13} />${" "}Undo this import
    </button>`}
  </div>`;
}
