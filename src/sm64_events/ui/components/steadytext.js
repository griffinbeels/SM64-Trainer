// src/sm64_events/ui/components/steadytext.js -- words that never change the size of what holds them.
//
// Griffin, 2026-11-08 (Scene viewer round 9, item 3): "the play bar is getting longer and shorter.
// This is really frustrating to use. The UI should not be bouncing around like that when we go
// between playing / paused." A control whose words follow its state (Play/Pause, Record/Stop, Set
// focus/Finding...) or a readout whose digits grow was as wide as its words, so the row around it
// wrapped somewhere else on every change and everything after it moved.
//
// SteadyText draws `text` in a box exactly as big as the biggest of `sizes`. Each size is an invisible
// copy in the same grid cell; its words are CSS content (data-text), so they are never part of the
// page's text, an accessible name or a test's textContent. In a line (the default) the shown text
// never sizes the box: a value longer than every size is cut with an ellipsis instead of pushing
// anything. So a caller lists every label a control can show, or the widest value a readout can hold
// ("0" stands for any digit: readouts set font-variant-numeric: tabular-nums, so digits are equal).
// `block` is a paragraph that wraps (a hint): as wide as its parent and as tall as its tallest size
// at that width (the shown text counts too, so an unlisted longer one grows rather than hides).
// The rules are the one `.steady-text` block in index.html.
import { h } from "preact";
import htm from "htm";

const html = htm.bind(h);

export function SteadyText({ text, sizes, block = false, className = "" }) {
  const names = `steady-text${block ? " is-block" : ""}${className ? ` ${className}` : ""}`;
  return html`<span class=${names}>
    ${sizes.map((size) => html`<span class="steady-text-size" aria-hidden="true" data-text=${size}></span>`)}
    <span class="steady-text-shown">${text}</span>
  </span>`;
}
