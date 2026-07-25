import { h } from "preact";
import { useEffect, useMemo, useState } from "preact/hooks";
import htm from "htm";
import { Modal } from "./modal.js";
import { Icon } from "./icons.js";
import { PracticeCell } from "./practicecell.js";
import { visibleGroups } from "../entities.js";

const html = htm.bind(h);

// THE entity picker: a trigger button that opens a GRID you navigate, not a
// list you scroll (spec 2026-07-25-picker-grid-navigation). The first version
// shipped as one long list — 25 groups, ~120 rows — and reaching Bob-omb
// Battlefield, the FIRST course in the game, meant scrolling past five secret
// stages. Icons made rows recognisable; they did nothing about the distance
// between them.
//
// Cells are components/practicecell.js — the SAME cell the practice banner
// renders, so a star looks where you pick it exactly as it will where you
// practice it.
//
// A DIALOG, not a popup anchored to the trigger: the workshop panes scroll
// internally under a measured height cap (ui/viewport.js), and an anchored
// popup inside a clipped scrolling pane is where custom dropdowns break.
//
// It knows nothing about what it is picking. Callers pass groups (built by
// ui/entities.js), their own filter as `allow`, and an `iconFor(id)`.
//
// Depth is a PROP, not an inference — a caller that wants one layer never
// risks a stray drill-in:
//   depth 1  one grid, a heading per group          (levels, courses)
//   depth 2  a grid of groups, then that group's cells (star/target, segments)
//
// No search box (user decision 2026-07-25): two clicks, no text entry. Dropping
// it also removed the filter + aria-activedescendant surface that made a
// hand-rolled control risky in the first place.
//
// KEYBOARD: the cells are real <button>s inside the Modal's focus trap, so Tab
// moves, Enter/Space activate, and the shell restores focus to the trigger on
// close — all native. Escape backs OUT of a drilled-in group before it closes.
// There is deliberately no role="grid": that ARIA pattern promises gridcell/row
// structure and roving tabindex, and claiming it without implementing it tells
// a screen reader a lie. Buttons in a container is what this is, so that is
// what it says.

function CellGrid({ options, value, iconFor, onPick }) {
  return html`<div class="entity-grid">
    ${options.map((option) => html`<${PracticeCell} key=${option.id}
      active=${option.id === value}
      iconSrc=${iconFor(option.id)}
      name=${option.name}
      sub=${option.sub ?? ""}
      rank=${option.rank}
      title=${option.name}
      onPick=${() => onPick(option.id)} />`)}
  </div>`;
}

function PickerDialog({ groups, value, allow, title, iconFor, depth,
                       onPick, onClose }) {
  // Which group has been drilled into (depth 2 only). Derived during render,
  // never in an effect — an effect would paint layer 1 and then correct it.
  const [openGroupKey, setOpenGroupKey] = useState(null);
  const shown = useMemo(
    () => visibleGroups(groups, allow, value), [groups, allow, value]);
  const openGroup = shown.find((group) => group.key === openGroupKey) || null;

  // Escape goes BACK out of a drilled-in group before it closes the dialog —
  // what a two-step navigation makes people expect. Capture phase, so it wins
  // over the Modal shell's own Escape-to-close.
  useEffect(() => {
    if (!openGroup) return undefined;
    const onKey = (keyEvent) => {
      if (keyEvent.key !== "Escape") return;
      keyEvent.preventDefault();
      keyEvent.stopPropagation();
      setOpenGroupKey(null);
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [openGroup]);

  if (depth > 1 && !openGroup)
    return html`<${Modal} title=${title} icon="target" size="grid" onClose=${onClose}>
      <div class="entity-grid">
        ${shown.map((group) => html`<${PracticeCell} key=${group.key}
          iconSrc=${group.icon || iconFor(group.options[0].id)}
          name=${group.label}
          sub=${`${group.options.length}`}
          title=${group.label}
          onPick=${() => setOpenGroupKey(group.key)} />`)}
      </div>
    <//>`;

  if (depth > 1)
    return html`<${Modal} title=${openGroup.label} icon="target" size="grid"
        onClose=${onClose}>
      <button type="button" class="entity-back"
          onclick=${() => setOpenGroupKey(null)}>
        <${Icon} name="chevron" size=${15} /> Back
      </button>
      <${CellGrid} options=${openGroup.options} value=${value}
        iconFor=${iconFor} onPick=${onPick} />
    <//>`;

  return html`<${Modal} title=${title} icon="target" size="grid" onClose=${onClose}>
    ${shown.map((group) => html`<div class="entity-section" key=${group.key}>
      ${shown.length > 1
        ? html`<div class="entity-section-head"><b>${group.label}</b></div>`
        : null}
      <${CellGrid} options=${group.options} value=${value}
        iconFor=${iconFor} onPick=${onPick} />
    </div>`)}
  <//>`;
}

/**
 * groups      [{ key, label, icon?, options: [{ id, name, sub?, rank? }] }]
 * value       current id (string) or null
 * onChange    (id) => void
 * allow       optional (id) => boolean — the CALLER's domain filter
 * iconFor     (id) => image URL for a cell
 * title       dialog heading, e.g. "Choose a star"
 * depth       1 = one grid with headings; 2 = groups, then the chosen group
 * placeholder trigger label when nothing is chosen
 */
export function EntityPicker({ groups, value, onChange, allow, iconFor,
                              title = "Choose", placeholder = "— pick —",
                              depth = 1, disabled = false }) {
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
      title=${title} iconFor=${iconFor} depth=${depth}
      onPick=${(id) => { setOpen(false); onChange(id); }}
      onClose=${() => setOpen(false)} />` : null}
  <//>`;
}
