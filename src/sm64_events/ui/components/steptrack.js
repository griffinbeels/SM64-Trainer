// src/sm64_events/ui/components/steptrack.js — a segment's route, as chips.
//
// ONE consumer now, and that is a deliberate narrowing. This module used to
// carry two: `StepTrack`, the read-only row an armed practice card drew, and
// `StepPicker`, the editable one in the recorder's review. Griffin deleted the
// read-only half on 2026-08-06 — "the PURPOSE of that indicator was to make it
// clear that the segment logic was working for me during development, but now
// that it is indeed working, I don't think we really need this anymore" — so
// what survives is the AUTHORING row: every place the journal says you walked
// through, each toggling between a REQUIRED stop and one you merely passed.
//
// The `StepChip`/`StepMark` vocabulary stays as it is rather than being folded
// into the picker, because it is the readable unit and a second consumer would
// otherwise be tempted to draw its own.
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

// DELETED HERE, 2026-08-06: `StepTrack({detail, onEdit})` — the read-only row
// an armed practice card drew ("Step 3 of 4 · ✓ BitFS › ✓ Lobby › ▸ Upstairs").
// It reported the arm cursor while playing, which is a DEVELOPMENT question,
// and Griffin's ruling is that the answer is no longer worth screen space:
// "they will know how to do the strat, no need to display the steps".
//
// Nothing about the server changed with it. `views.py::_armed_detail_for` ->
// `segments.py::card_step_labels` still ships `steps`/`progress`/`total` on
// every section, the projector still advances the cursor, and
// `tools/what_happened.py` still reads it back — so the state is queryable
// when debugging, just not painted.
//
// Its `onEdit` doorway into the definition went with it, and so did the
// `openSegment`/`segmentIntent` chain that served it (app.js, segments.js).
// The Segments tab's library is the way into a definition again.

// The editable half, and now the only one: the places the journal says you
// walked through, each a toggle between a REQUIRED stop and one you merely
// passed.
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
