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
// ONE CONSUMER since round 31 (2026-08-10): RedsCell's star/pipe pair, two
// buttons, MUTUALLY EXCLUSIVE, with the clock glyph between them and exactly
// one `selected`. A star's own [[subsection]] pieces used to be the second
// consumer -- one button per piece, MULTI-SELECT, enabling/disabling it -- and
// that badge is retired outright: "There's actually no point. We should just
// ALWAYS display the subsegments inside of the practice log... we don't need a
// button to enable / disable them now." A piece still nests inside its
// parent's practice-log card (`ui/subsections.js`); it simply has no switch on
// the selector any more.
//
// This module still has NO opinion about which cell is drawing it. Exclusivity
// lives in the caller's click handler; all that arrives here is which buttons
// are lit.
//
// LAYOUT is a function of COUNT, and it is his: "It should just be a straight
// line on the bottom, or a 2 by 2 grid. (if there are only 2 options, then it's
// 2 in a row; if there are 4, then it's a 2x2 grid)." With one consumer left
// (always exactly 2) only the two-in-a-row branch is reachable; the grid
// branch is dead code kept alive by nothing but this module surviving Task 3.
//
// A cell that hosts these CANNOT be a `<button>` -- a button may not contain a
// button -- which is why RedsCell is hand-written as a `<div role="button">`
// rather than a `PracticeCell` call; see practicecell.js. `PracticeCell` itself
// no longer has a `toggles` prop or an element swap (round 31) -- it was built
// for the badge and has no other caller.

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
