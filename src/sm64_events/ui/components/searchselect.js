// ui/components/searchselect.js — the filterable popup, as ONE component.
//
// ROUND 9 (2026-08-08), his ruling verbatim: "any long drop down like this
// should have a search feature -- I like the segment link search popup.
// This is a great way to do it, and we should basically reuse this anywhere
// we have a dropdown selector." This module IS that popup, extracted from
// the Library's link door: a titled overlay panel with a filter input and
// grouped options, plus a dropdown-shaped trigger (`SearchSelect`) for call
// sites that used to be a native <select>.
//
// Domain-free on purpose: callers hand in groups of {value, label} and get
// the picked value back. Only THIS file may render the `.search-menu` class
// family — pinned in tests/test_single_source.py, because "reuse, never
// reimplement" is not a mechanism until a second implementation is a red
// build. SHORT dropdowns (a handful of options) stay native selects: a
// popup with a filter on four options is worse than the thing it replaces,
// and the filter input only renders past FILTER_FLOOR options for the same
// reason.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { Icon } from "./icons.js";

const html = htm.bind(h);

const FILTER_FLOOR = 8;

/**
 * The overlaid panel itself. `groups`: [{label: string|null, options:
 * [{value, label}]}] — a null group label draws no heading (a flat list is
 * one anonymous group). The caller owns open/close state so it can keep the
 * panel up through a failed action (the link door shows the server's 409
 * where the click landed).
 */
export function SearchMenu({ title, groups, onPick, onClose, busy = false,
                             error = null, emptyNote = "Nothing to pick." }) {
  const [filter, setFilter] = useState("");
  const inputRef = useRef(null);
  // Click → type → click: the filter is the whole point of the panel, so it
  // takes focus on open rather than making him aim a second time.
  useEffect(() => { if (inputRef.current) inputRef.current.focus(); }, []);
  const needle = filter.toLowerCase();
  const total = groups.reduce((count, group) => count + group.options.length, 0);
  const shown = groups
    .map((group) => ({ ...group,
      options: group.options.filter((option) =>
        !needle || option.label.toLowerCase().includes(needle)) }))
    .filter((group) => group.options.length);
  return html`<div class="search-menu"
      onkeydown=${(keyEvent) => { if (keyEvent.key === "Escape") onClose(); }}>
    <div class="search-menu-head">
      <span>${title}</span>
      <button type="button" class="quiet-button" aria-label="Close"
          onclick=${onClose}><${Icon} name="close" size=${12} /></button>
    </div>
    ${total > FILTER_FLOOR
      ? html`<input ref=${inputRef} class="search-menu-filter" type="search"
          placeholder="Type to filter…" value=${filter}
          oninput=${(inputEvent) => setFilter(inputEvent.target.value)} />` : ""}
    <div class="search-menu-options">
      ${total === 0 ? html`<p class="meta">${emptyNote}</p>`
        : shown.length === 0 ? html`<p class="meta">No match.</p>`
        : shown.map((group) => html`<div class="search-menu-group"
              key=${group.label || "_flat"}>
            ${group.label
              ? html`<div class="search-menu-group-head">${group.label}</div>` : ""}
            ${group.options.map((option) => html`<button type="button"
                key=${String(option.value)} class="search-menu-option"
                disabled=${busy}
                onclick=${() => onPick(option.value)}>${option.label}</button>`)}
          </div>`)}
    </div>
    ${error ? html`<p class="search-menu-error">${error}</p>` : ""}
  </div>`;
}

/**
 * A dropdown-shaped caller: trigger button wearing the current value's
 * label, the menu overlaid beneath while open (nothing on the page moves —
 * the 2026-07-26 overlay ruling). Picking closes and reports the value;
 * picking the current value just closes.
 */
export function SearchSelect({ value, valueLabel, title, groups, onChange,
                               buttonClass = "quiet-button search-select-trigger" }) {
  const [open, setOpen] = useState(false);
  return html`<div class="search-select">
    <button type="button" class=${buttonClass} aria-expanded=${open}
        onclick=${() => setOpen((prev) => !prev)}>
      <span class="search-select-value">${valueLabel}</span>
      <${Icon} name="chevron" size=${13} />
    </button>
    ${open ? html`<${SearchMenu} title=${title} groups=${groups}
        onPick=${(picked) => {
          setOpen(false);
          if (picked !== value) onChange(picked);
        }}
        onClose=${() => setOpen(false)} />` : ""}
  </div>`;
}
