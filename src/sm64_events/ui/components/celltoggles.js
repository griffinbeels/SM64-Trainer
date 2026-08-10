import { h } from "preact";
import htm from "htm";

const html = htm.bind(h);

// THE small buttons drawn INSIDE a practice cell's own art, bottom-centred.
//
// Extracted from stagebanner.js's RedsCell on 2026-08-08 (round 22) because a
// SECOND surface wanted the identical thing and Griffin asked for the identical
// thing by name: "we should actually re-use the same exact design we use as the
// Pipe/Star selector for the Bowser stages... it's REALLY important that we
// reuse the same system as the bowser level icons, because we already put in a
// lot of work to getting that to work."
//
// TWO consumers, and the difference between them is one prop:
//
//   RedsCell          two buttons, MUTUALLY EXCLUSIVE (star vs pipe), with the
//                     clock glyph between them. Exactly one is `selected`.
//   a star's pieces   one button per [[subsection]], MULTI-SELECT: each is
//                     independently on or off. His own framing of the delta --
//                     "it's basically different by the fact that it's more like
//                     a multi-selection rather than a toggle version" -- and no
//                     clock, because nothing here is a choice of clock.
//
// This module has NO opinion about which of those it is drawing. Exclusivity
// lives in the caller's click handler; all that arrives here is which buttons
// are lit. That is what keeps one implementation honest rather than a shared
// component with two modes inside it.
//
// LAYOUT is a function of COUNT, and it is his: "It should just be a straight
// line on the bottom, or a 2 by 2 grid. (if there are only 2 options, then it's
// 2 in a row; if there are 4, then it's a 2x2 grid)." So one row up to two
// buttons, a two-column grid from three (three draws 2 + 1). The ceiling he
// named is "probably 3-4 subsections per star at max"; nothing here enforces
// one, and a fifth simply adds a third grid row.
//
// A cell that hosts these CANNOT be a `<button>` -- a button may not contain a
// button -- which is why `PracticeCell` switches to `<div role="button">` the
// moment it is given any. That constraint is the whole reason RedsCell was
// hand-written in the first place; see practicecell.js.

/**
 * @param toggles   [{key, iconSrc, title, selected, onToggle, ariaLabel}]
 *                  `selected` lights the button; everything else dims.
 * @param separator optional node drawn BETWEEN two buttons (the Reds clock).
 *                  Ignored from three buttons up, where there is no "between".
 */
export function CellToggles({ toggles, separator = null }) {
  const shown = (toggles || []).filter(Boolean);
  if (!shown.length) return null;
  const grid = shown.length > 2;
  const stop = (clickEvent, run) => {
    clickEvent.stopPropagation();
    clickEvent.preventDefault();
    run();
  };
  const buttons = shown.map((toggle) => html`<button type="button"
      key=${toggle.key}
      class="cell-toggle-btn ${toggle.selected ? "is-selected" : ""}"
      aria-pressed=${!!toggle.selected}
      aria-label=${toggle.ariaLabel || toggle.title}
      title=${toggle.title}
      onclick=${(clickEvent) => stop(clickEvent, toggle.onToggle)}>
    <img src=${toggle.iconSrc} alt="" draggable="false"
         onerror=${toggle.onIconError} />
  </button>`);
  if (!grid && separator && buttons.length === 2) {
    return html`<span class="cell-toggles">
      ${buttons[0]}${separator}${buttons[1]}</span>`;
  }
  return html`<span class="cell-toggles ${grid ? "is-grid" : ""}">
    ${buttons}</span>`;
}
