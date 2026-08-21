// src/sm64_events/ui/components/importsection.js — one section, four doors.
//
// The four import panels were four `.settings-section`s stacked, which pushed
// Display and Sessions most of a drawer away — and a control somebody has to
// scroll to hunt for gets redesigned rather than scrolled to. So: one heading,
// a row naming the four ways in, and only the chosen one drawn.
//
// NOTHING IS CHOSEN BY DEFAULT, on purpose. Importing is a first-day gesture
// and this section is passed over on every other day, so its resting state is
// one heading and one row of chips — the rest of the drawer stays within
// reach. Picking is also what says which door you are in, which a stack of
// four open panels never did.
import { h } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { ImportSheet } from "./importsheet.js";
import { ImportPaste } from "./importpaste.js";
import { ImportLiveSplit } from "./importlivesplit.js";
import { ImportLink } from "./importlink.js";

const html = htm.bind(h);

// Order is how likely each is to be the answer, not how they were built: most
// people asking this question are on the sheet, and the ones who are not
// mostly have their times written down somewhere.
const DOORS = [
  ["sheet", "Ultimate Sheet", ImportSheet],
  ["paste", "Paste a list", ImportPaste],
  ["livesplit", "LiveSplit file", ImportLiveSplit],
  ["link", "My own sheet", ImportLink],
];

export function ImportSection({ onDone }) {
  const [open, setOpen] = useState(null);
  const chosen = DOORS.find(([key]) => key === open);

  return html`<section class="settings-section importsection">
    <div class="settings-section-head">
      <div>
        <h3>Bring in times you already have</h3>
        <p>Practised before you found this? None of it has to be thrown away.</p>
      </div>
    </div>
    <div class="importsection-doors" role="group"
        aria-label="Where your times are">
      ${DOORS.map(([key, label]) => html`<button key=${key} type="button"
          class=${`chip${open === key ? " is-selected" : ""}`}
          aria-pressed=${open === key ? "true" : "false"}
          onclick=${() => setOpen(open === key ? null : key)}>${label}</button>`)}
    </div>
    ${chosen && html`<div class="importsection-door">
      <${chosen[2]} onDone=${onDone} />
    </div>`}
  </section>`;
}
