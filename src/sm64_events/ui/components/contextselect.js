// src/sm64_events/ui/components/contextselect.js — how a context card becomes
// ONE hit target.
//
// Lived inside header.js until 2026-07-28, when the MARELO bar became the
// route rank card and needed the same mechanism without header.js importing
// marelo.js and marelo.js importing header.js back.
import { h, Fragment } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { Icon } from "./icons.js";
import { SearchMenu } from "./searchselect.js";

const html = htm.bind(h);

// One context card = one hit target. The practice-target card already
// highlighted and opened as a whole because it IS a <button>; the three
// <select> cards only reacted on the select itself, which read as an
// inconsistency (user, 2026-07-25). Here the native <select> is stretched
// over the entire card and hidden with opacity (see .context-select in
// index.html — never transparent COLOURS, Chromium themes the popup off the
// computed background and a transparent one gets a white list), so a click
// anywhere opens the real dropdown — which means the closed-state value and
// the chevron are drawn by us.
//
// The title rides the SELECT, not the card: it covers the card anyway, so the
// tooltip still answers a hover anywhere — and this way it also reaches a
// screen reader as the combobox's description.
//
// Returns a FRAGMENT so the <select> stays a DIRECT child of the card element
// (`.context-select > select` is a child combinator; nesting it in a wrapper
// silently drops the whole rule and with it the hit target).
//
// `Fragment` is imported by NAME, not read off `h.Fragment` -- the vendored
// preact.module.js exports it separately and never attaches it to `h`, so
// `h.Fragment` is `undefined` and htm's `<${h.Fragment}>` renders a literal
// `<undefined>` element (confirmed by render: it swallowed the stretched
// select, which is why the hit-target sweep found DIV/SPAN/B instead of
// SELECT at every probe point). Caught here because this component is
// actually exercised by tests/test_header_ui.py's hit-test sweep; the same
// `<${h.Fragment}>` spelling exists in entitymodal.js/strategystep.js and is
// outside this task's file ownership, so it is reported rather than fixed.
export function CardSelect({ id, name, label, title, options, value, onChange }) {
  if (!options.length) return null;
  return html`<${Fragment}>
    <${Icon} name="chevron" size=${16} />
    <select id=${id} name=${name} aria-label=${label} title=${title}
        value=${value} onchange=${onChange}>
      ${options.map(([optionValue, optionLabel]) =>
        html`<option value=${optionValue}>${optionLabel}</option>`)}
    </select>
  <//>`;
}

// ROUND 10 (2026-08-08): the route rank card's picker is round 9's
// filterable popup now ("Let's also update the MARELO menu to use that new
// type of dropdown") -- the one LONG dropdown among the four cards; the
// other three stay native selects per round 9's own short-dropdown carve-out.
// SAME one-hit-target mechanism as CardSelect: an invisible trigger button
// stretched over the whole card (`.context-select > .context-card-trigger`,
// beside the select rule in index.html), the card wearing the focus ring via
// :has(). The menu overlays BENEATH the card, shifting nothing.
export function CardSearchSelect({ label, title, menuTitle, groups, value, onPick }) {
  const [open, setOpen] = useState(false);
  return html`<${Fragment}>
    <${Icon} name="chevron" size=${16} />
    <button type="button" class="context-card-trigger" aria-label=${label}
        title=${title} aria-expanded=${open}
        onclick=${() => setOpen((prev) => !prev)}></button>
    ${open ? html`<${SearchMenu} title=${menuTitle || label} groups=${groups}
        onPick=${(picked) => {
          setOpen(false);
          if (picked !== value) onPick(picked);
        }}
        onClose=${() => setOpen(false)} />` : ""}
  <//>`;
}

// Both the value and the <option>s come from the SAME `options` list, so they
// cannot disagree.
export function ContextSelect({ icon, label, options, value, onChange, id, name,
                               title, empty }) {
  const picked = options.find(([optionValue]) => optionValue === value);
  return html`<div
      class=${`context-control${options.length ? " context-select" : ""}`}>
    <${Icon} name=${icon} size=${19} />
    <span class="context-control-copy">
      <span class="context-label">${label}</span>
      <span class="context-value">${picked ? picked[1] : empty}</span>
    </span>
    <${CardSelect} id=${id} name=${name} label=${label} title=${title}
      options=${options} value=${value} onChange=${onChange} />
  </div>`;
}
