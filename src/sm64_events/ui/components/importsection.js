// src/sm64_events/ui/components/importsection.js — one section, the doors
// behind a chip row.
//
// The import panels were separate `.settings-section`s stacked, which pushed
// Display and Sessions most of a drawer away — and a control somebody has to
// scroll to hunt for gets redesigned rather than scrolled to. So: one heading,
// a row naming the ways in, and only the chosen one drawn.
//
// ONE door here since round 3 (2026-08-23); the other way in is the box on a
// star's own card. "Paste a list" and "LiveSplit file" went in round 2 ("too
// difficult to get quite right... we'll spend too much time getting
// distracted here"; commit d70721b9 last carries them) and "My own sheet" in
// round 3 ("this is also too much for us to handle, we need to just get the
// Ultimate Sheet parsing as good as possible, and assume that people are
// using that"). All three are backlog task 0103. The chip row stays for the
// same reason it arrived: the resting state is a heading and a chip, and a
// door that comes back is one row in DOORS.
//
// NOTHING IS CHOSEN BY DEFAULT, on purpose. Importing is a first-day gesture
// and this section is passed over on every other day, so its resting state is
// one heading and one row of chips — the rest of the drawer stays within
// reach.
import { h } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { ImportSheet } from "./importsheet.js";

const html = htm.bind(h);

const DOORS = [
  ["sheet", "Ultimate Sheet", ImportSheet],
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
