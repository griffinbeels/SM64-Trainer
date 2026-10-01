// Recorded timeline rows share role markers, landmark naming and visit folding.
import { h } from "preact";
import { useRef, useState } from "preact/hooks";
import htm from "htm";
import { fmtIgtShort } from "../format.js";
import { Icon } from "./icons.js";
const html = htm.bind(h);

// What a picked row is FOR, given how many are picked and where this one
// sits. Checked by RENDER rather than by a node test of its own -- the class
// it produces is the only thing telling a reader which end of a newest-first
// list is the start, so `tests/test_fixture_reaches_the_real_page.py` counts
// `.record-mark.role-start`/`.role-finish`/`.role-stop` at two picks and at
// three, which covers every branch including the boundary (at two, index 1 is
// the finish and never a stop).
function roleOf(index, count, rowId, boundaries) {
  if (boundaries) {
    if (rowId === boundaries.primary_start_id) return "start";
    if (rowId === boundaries.finish_id) return "finish";
    return "stop";
  }
  if (index === 0) return "start";
  if (index === count - 1) return "finish";
  return "stop";
}

const ROLE_WORDS = { start: "Start", stop: "Stop", finish: "Finish" };

// Review the same explicit roles the timeline marks, including samples from
// different attempts that do not demonstrate an intermediate walk.
export function RecordedConditions({ synth }) {
  return html`<div class="record-conditions">
    <div class="record-start-review">
      <p class="meta">Starts when any one happens:</p>
      ${(synth.start_sentences || [synth.start_sentence]).map((sentence, index) =>
        html`<p key=${index} class="meta">${index > 0 ? "OR " : ""}<b>${sentence}</b></p>`)}
    </div>
    ${(synth.picked || []).map((step, i) => html`<p key=${i} class="meta">
      Then: <b>${step.sentence}</b></p>`)}
    <p class="meta">Ends when: <b>${synth.end_sentence}</b></p>
    ${synth.path_notice && html`<p class="meta record-path-notice">${synth.path_notice}</p>`}
  </div>`;
}

// One clickable row per timeline moment. The row IS the picker -- no
// secondary confirm step (spec: "point at what you just did") -- and it is a
// TOGGLE, because with N selections there is no step to advance to that could
// stand in for un-picking one.
// The rename control. Only a NAMEABLE landmark gets one — a name typed here
// has to land on THIS thing and nothing else. Since 2026-08-07 that is almost
// everything: a pole or a tree carries no spawn point (the game creates them
// scriptless) but never moves, so it is keyed by where it stands, which is his
// own ask — "we should be able to rename ANYTHING", and the specific poles the
// Ultimate Sheet's subsections are built on. What is still refused is an
// object with NEITHER coordinate, whose key is shared with every other of its
// kind in that area. The rule and its measurement: core/landmark.py.
//
// A pencil on its own row-wrapper rather than inside the row button, because a
// button inside a button is invalid and the browser drops it silently.
//
// THE DRAFT IS THE INPUT'S OWN STATE while he types (round 9 item 2: "my text
// suddenly gets deleted, and I find myself racing against some mysterious
// timer"). The timer was the next game event: the live tail appends a row,
// the recorder re-renders, and an input whose `value` is drawn from the
// stored name gets diffed straight back to it — empty, for the unnamed row he
// is mid-typing on. `draft` survives the repaint because it is component
// state; the COMMITS still read `e.target.value` (the DOM's own text), so a
// driven test that sets the value and hits Enter in one tick — before any
// re-render lands the draft — commits what was typed, same as a human whose
// keystrokes each get their own tick.
function RenameControl({ row, renaming, onStart, onCommit, onCancel }) {
  if (!row.landmark || !row.landmark_nameable) return null;
  if (renaming) {
    return html`<${RenameInput} key=${row.id} row=${row}
        onCommit=${onCommit} onCancel=${onCancel} />`;
  }
  return html`<button type="button" class="record-rename"
      title=${row.landmark_name
        ? `Rename ${row.landmark_name} — every row it appears in follows`
        : "Name this one — every row it appears in follows"}
      onclick=${() => onStart(row)}>✎</button>`;
}

// The ✓/✕ pair is the CANCEL AFFORDANCE he asked for (round 12 item 5:
// "we should be able to cancel the name change easily (there's no clear way
// to do this)") — Escape already cancelled, and a key nobody can see is not
// an affordance. `onmousedown` preventDefault keeps the input focused, so
// clicking either button never fires the input's own blur-commit first.
function RenameInput({ row, onCommit, onCancel }) {
  const [draft, setDraft] = useState(row.landmark_name || "");
  const inputRef = useRef(null);
  const commit = () => onCommit(row, inputRef.current ? inputRef.current.value
                                                      : draft);
  return html`<span class="record-rename-edit"
      onclick=${(e) => e.stopPropagation()}>
    <input class="record-rename-input" type="text" autofocus ref=${inputRef}
      value=${draft}
      placeholder="name this one"
      oninput=${(e) => setDraft(e.target.value)}
      onblur=${(e) => onCommit(row, e.target.value)}
      onkeydown=${(e) => {
        if (e.key === "Enter") { e.preventDefault(); onCommit(row, e.target.value); }
        if (e.key === "Escape") { e.preventDefault(); onCancel(); }
      }} />
    <button type="button" class="record-rename-ok" title="Save this name"
      onmousedown=${(e) => e.preventDefault()}
      onclick=${commit}>✓</button>
    <button type="button" class="record-rename-cancel"
      title="Keep the old name (Esc)"
      onmousedown=${(e) => e.preventDefault()}
      onclick=${onCancel}>✕</button>
  </span>`;
}

function TimelineRows({ rows, order, starts, boundaries, onToggle, renamingId,
                        onRenameStart, onRenameCommit, onRenameCancel }) {
  return html`<div class="record-rows">
    ${rows.map((row) => {
      const index = order.indexOf(row.id);
      const alternative = starts.includes(row.id);
      const picked = index >= 0 || alternative;
      const role = alternative ? "start" : picked
        ? roleOf(index, order.length, row.id, boundaries) : null;
      return html`<div key=${row.id} class="record-row-wrap">
        <button type="button"
          class=${`record-row${picked ? " picked" : ""}`}
          aria-pressed=${picked ? "true" : "false"}
          onclick=${() => onToggle(row)}>
        <span class=${`record-mark${picked ? ` role-${role}` : ""}`}>
          ${alternative ? "OR" : picked ? index + 1 : ""}
        </span>
        <span class="record-label">${row.label}</span>
        ${picked && (order.length > 1 || starts.length > 0)
          && html`<span class="record-role">${ROLE_WORDS[role]}</span>`}
        ${/* "what was the timer in game", his own words -- and it is the
             thing you choose BY, so it belongs on the row rather than in the
             review. Through fmtIgtShort, the display form every other time on
             screen goes through; a type with no Usamune number of its own (a
             level change) shows nothing rather than a computed stand-in. */""}
        ${row.igt_frames != null
          && html`<span class="record-igt">${fmtIgtShort(row.igt_frames)}</span>`}
        </button>
        <${RenameControl} row=${row} renaming=${renamingId === row.id}
            onStart=${onRenameStart} onCommit=${onRenameCommit}
            onCancel=${onRenameCancel} />
      </div>`;
    })}
  </div>`;
}

// One card per visit: the place's own symbol and name, how many moments landed
// there, and a fold. OPEN BY DEFAULT, so the state held is the COLLAPSED set —
// an empty set is "everything open", which is what a first render must be.
//
// The header is the fold. A separate chevron button beside a heading gives one
// affordance two hit targets, and this app's own rule is that a card is ONE hit
// target (the context cards, 2026-07-25, where the mismatch read as a bug).
export function VisitCard({ card, collapsed, onToggleFold, icon, order, starts, boundaries, onToggleRow,
                     renamingId, onRenameStart, onRenameCommit, onRenameCancel }) {
  return html`<section class=${`record-card${collapsed ? " folded" : ""}`}>
    <button type="button" class="record-card-head" aria-expanded=${!collapsed}
        onclick=${onToggleFold}
        title=${collapsed ? `Show what happened in ${card.label}`
                          : `Hide what happened in ${card.label}`}>
      <span class="record-card-chevron"><${Icon} name="chevron" size=${14} /></span>
      ${icon && html`<img class="record-card-icon" src=${icon} alt="" />`}
      <span class="record-card-name">${card.label || "Somewhere unrecorded"}</span>
      <span class="count-badge">${card.rows.length}</span>
    </button>
    ${/* The body is ALWAYS rendered and folded by CSS, never removed from the
         tree. A conditional render has nothing left to animate out, and every
         state change on this machine animates (his standing rule, 2026-07-26).
         `grid-template-rows: 1fr -> 0fr` is the one height transition that
         needs no measured pixel value; `visibility` rides along so a folded
         card's buttons cannot be reached by Tab, which zero height alone does
         not prevent. */""}
    <div class="record-card-body">
      <div class="record-card-clip">
        <${TimelineRows} rows=${card.rows} order=${order} starts=${starts} boundaries=${boundaries}
            onToggle=${onToggleRow} renamingId=${renamingId}
            onRenameStart=${onRenameStart} onRenameCommit=${onRenameCommit}
            onRenameCancel=${onRenameCancel} />
      </div>
    </div>
  </section>`;
}
