// src/sm64_events/ui/components/importflow.js — what every import door shares.
//
// A door is an INPUT — a name picker — and everything after the input is the
// same for all of them: import, one sentence saying what happened, the rows
// that could not be used, and an undo. Each door used to carry its own copy of
// that, which meant a wording change was several edits and a new door was
// another copy. It lives here once.
//
// `useImportFlow` is the state machine; `ImportOutcome` draws it. A door
// renders its input, its own button on `flow.run`, and the outcome. The
// CHECK→IMPORT preview step left with the link door (round 3, 2026-08-23) —
// the sheet door never previewed, because the preview would be the same 7 MB
// download as the import.
//
// The server answers every door in ONE shape (`server/import_api.py::finish`):
// `{imported, already_faster, without_strategy, rejected: [{text,
// reason}], held: [{text, reason, row_key}]}`. That is what lets the
// outcome be one component. `held` (round 28) is the rows the door KEPT
// ASIDE rather than dropped -- a sheet time the trainer has nowhere to put
// yet; it shows on its Library row, prints in the sheet column as it is,
// and lands the moment its row is linked to a segment.
import { h } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { send } from "../api.js";
import { Icon } from "./icons.js";

const html = htm.bind(h);

// Every reason any door can report, in words a person can act on. The keys
// are `library/import_runner.py`'s; a new one there owes a sentence here.
export const REASONS = {
  no_entity: "rows the trainer has no target for",
  subsections: "rows timing part of a star rather than the star",
  segments: "rows mapped to a movement, which needs one of yours",
  real_time: "rows timed on a real-time clock, which a star here cannot hold",
};

// Every rejected row is drawn, under the sentence for its reason. There was a
// cap of 12 with an "…and N more" until round 3 (2026-08-23): a sheet column
// drops 30-odd rows, and those are exactly the ones he reviews ("show all the
// things that failed as a list"). The list scrolls past its own height.
export function groupRejects(rejected) {
  const groups = [];
  for (const row of rejected) {
    let group = groups.find((candidate) => candidate.reason === row.reason);
    if (!group) {
      group = { reason: row.reason, rows: [] };
      groups.push(group);
    }
    group.rows.push(row);
  }
  return groups;
}

function plural(count, noun) {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

// `post(onStep) -> Promise<summary>` is the door's own request; a door whose
// request is a polled job calls `onStep({progress, message})` as it goes and
// the flow carries it as `progress` for a `ProgressLine` (round 29: "I want
// to see my progress as it's happening, otherwise it feels laggy and
// unresponsive"); a one-request door simply never calls it. `source` is what
// `DELETE /api/import/<source>` erases. Phases:
//   idle | working | done | undone | error
export function useImportFlow({ source, post, onDone }) {
  const [phase, setPhase] = useState("idle");
  const [shown, setShown] = useState(null);     // the latest summary
  const [error, setError] = useState("");
  // `null` until the door's request reports a step; `{progress, message}`
  // after, kept through `done` so the line can sit at 100% while the outcome
  // draws under it.
  const [progress, setProgress] = useState(null);

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
    phase, shown, error, progress,
    reset() { setShown(null); setPhase("idle"); setError(""); setProgress(null); },
    run: () => attempt("working", async () => {
      setProgress(null);
      const body = await post((step) => setProgress(
        { progress: step.progress, message: step.message }));
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

// What the server reported, as one sentence. `already_faster` is not a
// failure and must not read like one — it is the improvement rule working.
// Opens on what HAPPENED rather than on a bare number, because the ordinary
// case of a second import is that everything was already beaten.
export function outcomeSentence(shown, noun = "time") {
  const parts = [];
  if (shown.imported) {
    parts.push(`${plural(shown.imported, noun)} added`);
  }
  if (shown.already_faster) {
    parts.push(`${shown.already_faster} you had already beaten`);
  }
  const held = (shown.held || []).length;
  if (held) {
    parts.push(`${held} kept aside`);
  }
  if (!parts.length) return "Nothing here beats what you already have.";
  return `${shown.imported ? "" : "Nothing new — "}${parts.join(", ")}.`;
}

// The sentence, the strategy-less note, the rows that could not be read, the
// error, the undo and the undone note — everything below a door's input.
export function ImportOutcome({ flow, noun = "time", rowNoun = "row" }) {
  const { phase, shown, error } = flow;
  if (phase === "undone") {
    // The batch is gone, so everything that described it goes with it.
    return html`<div class="importdoor-outcome"><p class="settings-note">${
      plural(shown.removed || 0, `imported ${noun}`)} erased, with anything
      the import kept aside. Anything each one replaced is your best
      again.</p></div>`;
  }
  const done = phase === "done";
  const rejected = (shown && shown.rejected) || [];
  const held = (shown && shown.held) || [];
  return html`<div class="importdoor-outcome">
    ${shown && html`<p
        class=${`settings-note importdoor-summary${done ? " is-ok" : ""}`}>${
      outcomeSentence(shown, noun)}</p>`}
    ${shown && shown.without_strategy > 0 && html`<p class="settings-note">${
      shown.without_strategy}${" "}of these name no strategy. They still land
      as your best time — they just show no rank until you pick one on the
      card.</p>`}
    ${rejected.length > 0 && html`<div class="importdoor-rejects">
      <p class="settings-note is-bad">${plural(rejected.length, rowNoun)}${" "}
        could not be used:</p>
      ${groupRejects(rejected).map((group) => html`<div
          class="importdoor-reject-group" key=${group.reason}>
        <p class="meta importdoor-reject-reason">${
          REASONS[group.reason] || group.reason} (${group.rows.length})</p>
        <ul>
          ${group.rows.map((row, index) => html`<li key=${index}>
            <code>${row.text}</code>
          </li>`)}
        </ul>
      </div>`)}
    </div>`}
    ${/* Round 28: rows the door KEPT ASIDE. Not a failure, so not red:
         the time is safe, prints in the sheet column as written, and
         lands by itself the moment the row is linked to one of his
         segments. Grouped under the same reason sentences as a refusal,
         because the reason is what tells him which link to make. */""}
    ${held.length > 0 && html`<div class="importdoor-rejects importdoor-held">
      <p class="settings-note">${plural(held.length, rowNoun)}${" "}
        kept aside — the trainer has nowhere to put them yet. Each shows
        on its Library row, prints in your sheet column as it is, and
        lands the moment you link its row to a segment of yours:</p>
      ${groupRejects(held).map((group) => html`<div
          class="importdoor-reject-group" key=${group.reason}>
        <p class="meta importdoor-reject-reason">${
          REASONS[group.reason] || group.reason} (${group.rows.length})</p>
        <ul>
          ${group.rows.map((row, index) => html`<li key=${index}>
            <code>${row.text}</code>
          </li>`)}
        </ul>
      </div>`)}
    </div>`}
    ${phase === "error" && html`<p class="settings-note is-bad">${error}</p>`}
    ${/* The undo for the gesture just made. A delete route with nothing
         calling it is a capability that does not exist, and this is the one
         moment it is wanted: he pressed a button, several hundred bests
         landed, and he wants them gone. Erased outright rather than marked
         ("just completely erase them, it's cool"), and latest-row-wins puts
         back whatever each one superseded. Offered only while the import he
         is undoing is still on screen. */""}
    ${done && (shown.imported > 0 || held.length > 0) && html`<button type="button"
        class="quiet-button importdoor-undo" onclick=${flow.undo}>
      <${Icon} name="trash" size=${13} />${" "}Undo this import
    </button>`}
  </div>`;
}
