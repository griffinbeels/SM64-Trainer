// src/sm64_events/ui/components/strategyselect.js — pick a strategy LOCALLY.
//
// Distinct from `stratpicker.js`, which is a different job: that one WRITES
// the entity's active strategy to the server and can mint a new one. This is a
// plain dropdown whose answer stays in the caller's own state — what the
// comparison view uses to choose which strategy's videos to show. It was lifted
// out of `compare.js` so the add-a-time control could share it; that control
// then files under the card's own strategy instead and has no picker, so this
// has ONE consumer today and is where a second local pick would come from.
//
// THE rule it carries: a stored or historical value fed to a FILTERED list
// renders BLANK when the filter drops it, which is indistinguishable from
// nothing being selected. The current value therefore stays listed
// unconditionally. `stratpicker.js` and `segments.js` each guard the same
// thing on their own dropdowns for the same reason.
import { h } from "preact";
import htm from "htm";

const html = htm.bind(h);

export function StrategySelect({ strategies, value, onChange, className = "",
                                blankLabel = "— no strategy —" }) {
  const options = [];
  for (const name of strategies || []) {
    if (name && !options.includes(name)) options.push(name);
  }
  if (value && !options.includes(value)) options.unshift(value);
  return html`<select class=${`${className} meta`.trim()} value=${value || ""}
      onchange=${(event) => onChange(event.target.value)}>
    <option value="">${blankLabel}</option>
    ${options.map((name) => html`<option value=${name}>${name}</option>`)}
  </select>`;
}
