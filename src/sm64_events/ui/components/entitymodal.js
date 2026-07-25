import { h } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import htm from "htm";
import { Modal } from "./modal.js";
import { Icon } from "./icons.js";
import { visibleGroups } from "../entities.js";

const html = htm.bind(h);

// THE entity picker: a trigger button that opens a searchable, grouped,
// keyboard-driven list in a dialog. It replaced a native <select> because
// <option> cannot contain an image and these rows carry art (spec
// 2026-07-25-entity-picker-icons).
//
// It knows nothing about what it is picking. Callers pass groups (built by
// ui/entities.js), their own filter as `allow`, and an `iconFor(id)` that
// resolves a row's art — so the domain stays outside this file, exactly as it
// did for the select this replaces.
//
// A DIALOG, not a popup anchored to the trigger: the workshop panes scroll
// internally under a measured height cap (ui/viewport.js), and an anchored
// popup inside a clipped scrolling pane is where custom dropdowns break.
//
// Keyboard is what native gave for free and this has to earn back: type to
// filter, Up/Down across group boundaries, Enter to pick, Escape to close.
// The Modal shell already traps focus and restores it to the trigger on close.

const matches = (text, needle) =>
  text.toLowerCase().includes(needle.trim().toLowerCase());

function PickerDialog({ groups, value, allow, title, iconFor, onPick, onClose }) {
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const listRef = useRef(null);

  // Filtered groups, and the flat row order the arrow keys walk. Derived
  // during render, never in an effect — an effect would paint the unfiltered
  // list first and then correct it.
  const [shownGroups, flatRows] = useMemo(() => {
    const filtered = visibleGroups(groups, allow, value)
      .map((group) => ({ ...group,
        options: group.options.filter((option) => matches(option.name, query)) }))
      .filter((group) => group.options.length > 0);
    return [filtered, filtered.flatMap((group) => group.options)];
  }, [groups, allow, value, query]);

  useEffect(() => { setActiveIndex(0); }, [query]);

  const move = (delta) => setActiveIndex((current) => {
    if (flatRows.length === 0) return 0;
    const next = (current + delta + flatRows.length) % flatRows.length;
    const node = listRef.current
      && listRef.current.querySelector(`[data-row="${next}"]`);
    if (node && node.scrollIntoView) node.scrollIntoView({ block: "nearest" });
    return next;
  });

  const onKeyDown = (keyEvent) => {
    if (keyEvent.key === "ArrowDown") { keyEvent.preventDefault(); move(1); }
    else if (keyEvent.key === "ArrowUp") { keyEvent.preventDefault(); move(-1); }
    else if (keyEvent.key === "Enter") {
      keyEvent.preventDefault();
      const row = flatRows[activeIndex];
      if (row) onPick(row.id);
    } else if (keyEvent.key === "Escape") { onClose(); }
  };

  let rowIndex = -1;
  return html`<${Modal} title=${title} icon="target" onClose=${onClose}>
    <input class="entity-search" type="search" autofocus value=${query}
      placeholder="Type to filter…" aria-label="Filter"
      oninput=${(inputEvent) => setQuery(inputEvent.target.value)}
      onkeydown=${onKeyDown} />
    <div class="entity-list" role="listbox" ref=${listRef}
        aria-activedescendant=${`entity-row-${activeIndex}`}>
      ${shownGroups.length === 0
        ? html`<p class="meta">Nothing matches "${query}".</p>`
        : shownGroups.map((group) => html`<div class="entity-group"
            key=${group.key}>
          <div class="entity-group-head">
            ${group.icon
              ? html`<img class="entity-row-icon" src=${group.icon} alt="" />`
              : null}
            <b>${group.label}</b>
          </div>
          ${group.options.map((option) => {
            rowIndex += 1;
            const index = rowIndex;
            return html`<button type="button" key=${option.id}
                id=${`entity-row-${index}`} data-row=${index}
                role="option" aria-selected=${option.id === value}
                class=${`entity-row ${index === activeIndex ? "active" : ""} `
                       + `${option.id === value ? "chosen" : ""}`}
                onmousemove=${() => setActiveIndex(index)}
                onclick=${() => onPick(option.id)}>
              <img class="entity-row-icon" src=${iconFor(option.id)} alt=""
                loading="lazy" />
              <span>${option.name}</span>
            </button>`;
          })}
        </div>`)}
    </div>
  <//>`;
}

/**
 * groups      [{ key, label, icon?, options: [{ id, name }] }]
 * value       current id (string) or null
 * onChange    (id | null) => void
 * allow       optional (id) => boolean — the CALLER's domain filter
 * iconFor     (id) => image URL for a row
 * title       dialog heading, e.g. "Choose a star"
 * placeholder trigger label when nothing is chosen
 */
export function EntityPicker({ groups, value, onChange, allow, iconFor,
                              title = "Choose", placeholder = "— pick —",
                              disabled = false }) {
  const [open, setOpen] = useState(false);
  const current = visibleGroups(groups, allow, value)
    .flatMap((group) => group.options)
    .find((option) => option.id === value) || null;
  return html`<${h.Fragment}>
    <button type="button" class="entity-trigger" disabled=${disabled}
        aria-haspopup="dialog" onclick=${() => setOpen(true)}>
      ${current
        ? html`<img class="entity-row-icon" src=${iconFor(current.id)} alt="" />`
        : null}
      <span class="entity-trigger-label">${current ? current.name : placeholder}</span>
      <${Icon} name="chevron" size=${15} />
    </button>
    ${open ? html`<${PickerDialog} groups=${groups} value=${value} allow=${allow}
      title=${title} iconFor=${iconFor}
      onPick=${(id) => { setOpen(false); onChange(id); }}
      onClose=${() => setOpen(false)} />` : null}
  <//>`;
}
