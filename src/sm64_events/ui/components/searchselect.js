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
 * [{value, label, style?}]}] — a null group label draws no heading (a flat
 * list is one anonymous group). An option's optional `style` is an inline
 * style string drawn on its row (round 30: a font picker shows each name in
 * its own face — "we should see its name *in its font*"). The caller owns
 * open/close state so it can keep the panel up through a failed action (the
 * link door shows the server's 409 where the click landed).
 */
export function SearchMenu({ title, groups, onPick, onClose, busy = false,
                             error = null, emptyNote = "Nothing to pick.",
                             align = "left", selected = null }) {
  const [filter, setFilter] = useState("");
  const inputRef = useRef(null);
  const menuRef = useRef(null);
  // Click → type → click: the filter is the whole point of the panel, so it
  // takes focus on open rather than making him aim a second time.
  useEffect(() => { if (inputRef.current) inputRef.current.focus(); }, []);
  // ROUND 11 (2026-08-08): "if I click outside of it, I'm clearly expressing
  // intent to close the dropdown. This should apply to all dropdowns of this
  // type." One listener here covers every door. The ANCHOR is the menu's
  // PARENT, not the menu: every call site renders this panel as a sibling of
  // its trigger inside one wrapper, so a click on the trigger stays "inside"
  // and closes through the trigger's own toggle rather than racing a
  // close-then-reopen. `pointerdown`, not `click`, so the panel is gone
  // before the outside element's own click lands.
  useEffect(() => {
    const closeOnOutsidePointer = (pointerEvent) => {
      const menuElement = menuRef.current;
      if (!menuElement) return;
      const anchor = menuElement.parentElement || menuElement;
      if (anchor.contains(pointerEvent.target)) return;
      onClose();
    };
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    return () => document.removeEventListener("pointerdown", closeOnOutsidePointer);
  }, [onClose]);
  const needle = filter.toLowerCase();
  const total = groups.reduce((count, group) => count + group.options.length, 0);
  const shown = groups
    .map((group) => ({ ...group,
      options: group.options.filter((option) =>
        !needle || option.label.toLowerCase().includes(needle)) }))
    .filter((group) => group.options.length);
  return html`<div class="search-menu ${align === "right" ? "search-menu-right" : ""}"
      ref=${menuRef}
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
            ${group.options.map((option) => {
              // `selected` marks options in a MULTI picker (round 14): the
              // menu stays open and each row is a toggle, so it has to say
              // which are on. Null for every single-pick caller, which then
              // renders exactly as before.
              const isPicked = !!(selected && selected.has(String(option.value)));
              return html`<button type="button"
                key=${String(option.value)}
                class="search-menu-option ${isPicked ? "is-picked" : ""}"
                data-value=${String(option.value)} disabled=${busy}
                aria-pressed=${selected ? String(isPicked) : null}
                style=${option.style || null}
                onclick=${() => onPick(option.value)}>
                ${selected ? html`<span class="search-menu-check"
                    aria-hidden="true">${isPicked ? "✓" : ""}</span>` : ""}
                ${option.label}</button>`;
            })}
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
 *
 * `align="right"` anchors the menu's RIGHT edge under the trigger instead of
 * its left, opening leftward -- for a trigger a caller has pushed toward the
 * right of a wide container (`.search-menu`'s default `left: 0` anchor,
 * unchanged, is right for every trigger that sits nearer its container's
 * left/centre, which is every OTHER call site today). Without it, the
 * scorecard's goal picker -- pinned to the far right of `.scorecard-head`
 * by that row's own `margin-right: auto` -- opened a 240px+ panel that ran
 * straight off the right edge of the page, widening the document's own
 * scrollable area (his report: "when I press the dropdown, it goes
 * offscreen... it shouldn't mess with the width of the page at all").
 */
export function SearchSelect({ value, valueLabel, title, groups, onChange,
                               buttonClass = "quiet-button search-select-trigger",
                               onOpen = null, align = "left", multi = false,
                               valueStyle = null }) {
  const [open, setOpen] = useState(false);
  // `onOpen` is a lazy-load hook, not a mount fetch: the scorecard's Runners
  // group (448 names) would otherwise download every time the Rank tab
  // mounts, whether or not anyone ever opens the picker. Fired on every
  // transition INTO open (never on close, never while already open) --
  // idempotent for a caller that caches what it fetched, same shape as
  // `useUiLog`'s own dedupe.
  function toggle() {
    setOpen((wasOpen) => {
      const nowOpen = !wasOpen;
      if (nowOpen && onOpen) onOpen();
      return nowOpen;
    });
  }
  return html`<div class="search-select">
    <button type="button" class=${buttonClass} aria-expanded=${open}
        onclick=${toggle}>
      <span class="search-select-value" style=${valueStyle}>${valueLabel}</span>
      <${Icon} name="chevron" size=${13} />
    </button>
    ${open ? html`<${SearchMenu} title=${title} groups=${groups} align=${align}
        selected=${multi ? new Set((value || []).map(String)) : null}
        onPick=${(picked) => {
          // MULTI (round 14): every row is a toggle and the panel STAYS
          // open -- picking ten runners must not cost ten trips through the
          // trigger. The caller gets the whole next selection, never a
          // delta, so it never has to reconstruct what it already owns.
          if (multi) {
            const current = (value || []).map(String);
            const key = String(picked);
            onChange(current.includes(key)
              ? current.filter((one) => one !== key)
              : [...current, key]);
            return;
          }
          setOpen(false);
          if (picked !== value) onChange(picked);
        }}
        onClose=${() => setOpen(false)} />` : ""}
  </div>`;
}
