// src/sm64_events/ui/components/steptrack.js — a segment's route, as chips.
//
// ONE chip, two consumers, and that is the whole point of the module: the row
// you READ while running a movement and the row you EDIT while defining one
// are the same row. He learned to read `✓ BitFS › ✓ Lobby › ▸ Upstairs › · BitS`
// on the practice card; the editor asks him to click the chips in a row that
// looks exactly like it, so authoring needs no second vocabulary.
//
// `StepTrack` is the read-only card version, driven by the arm's own cursor.
// `StepPicker` is the editable one: every place the journal says you walked
// through, each toggling between a REQUIRED stop and one you merely passed.
// Markup and CSS classes are shared, so the two can never drift into looking
// like different concepts.
import { h } from "preact";
import htm from "htm";

const html = htm.bind(h);

// Both markers always exist and swap by OPACITY, the incoming one delayed past
// the outgoing one's fade — the sequential text exchange `.claude/rules/
// ui-core.md` requires, expressed in CSS rather than through climbcurve.js's
// exchangeFade (which drives a JS-animated pair). Two glyphs at half opacity in
// one slot read as a rendering fault, and no duration fixes that.
//
// Four marks for four states, and the not-yet-reached dot earns its place: the
// slot has to hold its width whatever state the chip is in, or a chip jumps
// sideways the moment it becomes current, and a reserved-but-empty slot reads
// at 4x as a glyph that failed to load rather than as a step you have not got
// to. Three states, three marks, nothing unexplained.
function StepMark() {
  return html`<span class="step-mark" aria-hidden="true"
    ><span class="mark-done">✓</span
    ><span class="mark-now">▸</span
    ><span class="mark-ahead">·</span></span>`;
}

// `state` is one of done / now / ahead (the card) or required / skipped (the
// editor); the CSS maps each to a mark and a weight. A chip with `onToggle` is
// a real <button> so it is keyboard-reachable and announces its pressed state —
// the picker is a set of toggles, not a list with click handlers bolted on.
export function StepChip({ label, state, title, onToggle, pressed }) {
  const body = html`<${StepMark} /><span class="step-name">${label}</span>`;
  return onToggle
    ? html`<li class=${`step-chip ${state}`}>
        <button type="button" class="step-toggle" title=${title}
            aria-pressed=${pressed ? "true" : "false"} onclick=${onToggle}>
          ${body}
        </button>
      </li>`
    : html`<li class=${`step-chip ${state}`} title=${title}>${body}</li>`;
}

// The step track an ARMED multi-step thing draws below its metrics row — the
// WHOLE route as short place chips, with the one you are on marked. ONE
// component for the star card and the segment card (rule 11): this was two
// byte-identical copies of the markup until 2026-08-03, which is the shape
// that drifts, and it is a single component precisely because a 100-coin star
// is the one star that has steps at all.
//
// Places rather than sentences, and always ONE LINE, is Griffin's own pick
// from a two-option contact of the shapes (2026-08-03) — the card is
// fixed-height, so a track that can grow to four rows would clip whatever sits
// under it (`.claude/rules/ui-core.md`'s wrapping-inside-a-fixed-height-card
// trap). The full imperative for the CURRENT step ("Enter Castle Inside Lobby
// coming from Bowser in the Fire Sea") is not thrown away, it rides the row's
// own `title` — the qualifier a chip has no room for is one hover away rather
// than deleted.
//
// `steps` is server truth (`views.py::_armed_detail_for` -> `segments.py::
// card_step_labels`) and `progress` indexes straight into it, so nothing here
// re-derives what a step IS. Chips paint by comparing an index against that
// one number; there is no second notion of done-ness to disagree with it.
//
// `onEdit`, when given, makes the row a doorway into the definition it is
// describing. Noticing a wrong step happens WHILE PLAYING, and until this
// existed the only way in was the Segments tab plus a hunt through the library
// — the reason the whole class of "I can see it's wrong and can't reach it"
// report exists (`.claude/rules/acceptance.md`: put the reason where the click
// lands).
export function StepTrack({ detail, onEdit }) {
  if (!detail) return null;
  const steps = detail.steps || [];
  const waiting = detail.waiting_for ? `Waiting for ${detail.waiting_for}` : null;
  const hint = onEdit
    ? `${waiting || "This segment's route"} — click to edit these steps`
    : waiting;
  const track = html`<ol class="step-track">
    ${steps.map((label, index) => html`<${StepChip} key=${index} label=${label}
        state=${index < detail.progress ? "done"
          : index === detail.progress ? "now" : "ahead"} />`)}
  </ol>`;
  return html`<div class="seg-waiting step-row" title=${hint}>
    <span class="seg-waiting-step">Step${" "}
      ${detail.progress + 1}${" "}of${" "}${detail.total + 1}</span>
    ${onEdit
      ? html`<button type="button" class="step-track-door" onclick=${onEdit}
            title=${hint}>${track}</button>`
      : track}
  </div>`;
}

// The editable half: the places the journal says you walked through, each a
// toggle between a REQUIRED stop and one you merely passed.
//
// A toggle, never add/remove, and that is the design: the walk is ground truth,
// so the question is never "which places exist" — it is "which of them does
// this movement REQUIRE". Expressed this way an invalid step is unreachable
// (correct by construction): you cannot name a room you never stood in, cannot
// order them wrongly, and cannot pick between two clause types that describe
// the same room where only one of them can ever arm.
//
// `required` is a Set of node keys. Empty is legal and means "no declared
// path" — the definition saves as it always did.
export function StepPicker({ steps, required, onToggle }) {
  if (!steps || steps.length === 0) return null;
  return html`<div class="step-picker">
    <div class="step-row">
      <span class="seg-waiting-step">Then</span>
      <ol class="step-track">
        ${steps.map((step) => html`<${StepChip} key=${step.node}
            label=${step.label}
            state=${required.has(step.node) ? "required" : "skipped"}
            pressed=${required.has(step.node)}
            title=${required.has(step.node)
              ? `Required: ${step.sentence}. Click if you were only passing through.`
              : `Only passing through. Click to require: ${step.sentence}.`}
            onToggle=${() => onToggle(step.node)} />`)}
      </ol>
    </div>
    <p class="meta step-picker-hint">
      ${required.size === 0
        ? "Nothing required in between — any route from start to finish counts."
        : `Going anywhere else voids the run. Click a stop you were only passing through.`}
    </p>
  </div>`;
}
