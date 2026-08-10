import { h, Fragment } from "preact";
import { useEffect, useState } from "preact/hooks";
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

function CellGrid({ options, value, iconFor, onPick, clearLabel }) {
  return html`<div class="entity-grid">
    ${clearLabel ? html`<button type="button" class="entity-clear"
        title=${clearLabel} onclick=${() => onPick(null)}>
      <span class="entity-clear-mark">×</span>
      <span class="entity-clear-label">${clearLabel}</span>
    </button>` : null}
    ${options.map((option) => html`<${PracticeCell} key=${option.id}
      active=${option.id === value}
      iconSrc=${iconFor(option.id)}
      name=${option.name}
      sub=${option.sub ?? ""}
      rank=${option.rank}
      strat=${option.strat}
      rankBadge=${true}
      title=${option.name}
      onPick=${() => onPick(option.id)} />`)}
  </div>`;
}

// Exported so a caller with its own trigger (header.js's context card IS the
// trigger) can render the dialog directly instead of going through
// EntityPicker, which would draw a second <button class="entity-trigger">
// nobody wants (task 7, 2026-07-25-target-picker-strategy-step).
export function PickerDialog({ groups, value, allow, title, iconFor, depth,
                              placeholder, nextStep, onPick, onClose }) {
  // Which group has been drilled into (depth 2 only). Derived during render,
  // never in an effect — an effect would paint layer 1 and then correct it.
  const [openGroupKey, setOpenGroupKey] = useState(null);
  // Which cell has been picked toward the (optional) third step. Same reason
  // as openGroupKey above: derived during render, not an effect.
  const [pendingId, setPendingId] = useState(null);
  // No useMemo: every call site builds `groups` and `allow` inline, so their
  // identities change each render and the memo would never hit (review M9).
  const shown = visibleGroups(groups, allow, value);
  const openGroup = shown.find((group) => group.key === openGroupKey) || null;
  // A group carrying `pick` is an answer in its OWN right as well as a door
  // into its members, so it appears twice: as the layer-1 tile you drill
  // through, and as the first cell inside — same art, the group's own label,
  // and whatever `sub` the caller wrote to say what picking it means.
  const selfOption = (group) => (group == null || group.pick == null ? null
    : { id: group.pick, name: group.label, sub: group.sub || "" });
  const groupIcon = (group) => group.icon || iconFor(group.options[0].id);
  const pendingOption = pendingId == null ? null
    : shown.flatMap((group) => [selfOption(group), ...group.options])
        .filter(Boolean)
        .find((option) => option.id === pendingId) || null;

  // A caller with no nextStep keeps the original contract byte-for-byte:
  // every pick reaches the outer onPick (close + onChange) immediately. The
  // clear cell (id === null) always does too — there is no step to choose
  // for "nothing", so it never enters pending state.
  const handlePick = (id) => {
    if (id === null || !nextStep) { onPick(id); return; }
    setPendingId(id);
  };

  // Drilling in (or advancing to the step) unmounts the button that was
  // clicked, so focus falls back to <body> — and the Modal shell only
  // intercepts Tab at the FIRST and LAST focusable, so from <body> a Tab
  // walks into the page behind the dialog (review M10). Move focus to Back,
  // which is both a real target and the thing a keyboard user most likely
  // wants next.
  const focusOnDrillIn = (node) => { if (node) node.focus(); };

  // Escape STACKS: at the step it clears the pending pick first (back to the
  // grid it came from); only once nothing is pending does it back a drilled-
  // in group out to layer 1. One handler for both, so they cannot race each
  // other. Capture phase, so it wins over the Modal shell's own Escape-to-
  // close — which is what fires at layer 1, where neither state is set.
  useEffect(() => {
    if (!pendingOption && !openGroup) return undefined;
    const onKey = (keyEvent) => {
      if (keyEvent.key !== "Escape") return;
      keyEvent.preventDefault();
      keyEvent.stopPropagation();
      if (pendingOption) { setPendingId(null); return; }
      setOpenGroupKey(null);
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [pendingOption, openGroup]);

  // The step takes over the SAME dialog rather than opening a second one —
  // it is a third layer of the one picker, not a separate flow. onChange is
  // never called from here: the step owns the write (spec
  // 2026-07-25-target-picker-strategy-step).
  if (pendingOption)
    return html`<${Modal} title=${pendingOption.name} icon="target" size="grid"
        onClose=${onClose}>
      <${nextStep} value=${pendingOption.id} option=${pendingOption}
        onBack=${() => setPendingId(null)} onClose=${onClose} />
    <//>`;

  if (depth > 1 && !openGroup)
    return html`<${Modal} title=${title} icon="target" size="grid" onClose=${onClose}>
      <div class="entity-grid">
        ${placeholder ? html`<button type="button" class="entity-clear"
            title=${placeholder} onclick=${() => handlePick(null)}>
          <span class="entity-clear-mark">×</span>
          <span class="entity-clear-label">${placeholder}</span>
        </button>` : null}
        ${/* EVERY tile drills, including one carrying `pick`. That marking
             made the tile terminal until 2026-08-09 (round 14: "for the
             castle areas, those are the high level areas, so it shouldn't
             have a further drill down"), which put the movements recorded
             inside an area out of reach — "I should be able to click into
             Upstairs, and be able to select any segment within Upstairs OR
             select a general 'Upstairs' association". Both answers live one
             layer down now; the tile still lights up when the group itself
             is the current value. */""}
        ${shown.map((group) => html`<${PracticeCell} key=${group.key}
          iconSrc=${groupIcon(group)}
          name=${group.label}
          sub=${`${group.options.length} to practice`}
          title=${group.label}
          active=${group.pick != null && group.pick === value}
          onPick=${() => setOpenGroupKey(group.key)} />`)}
      </div>
    <//>`;

  if (depth > 1) {
    // The group's own cell LEADS its members — a piece of the Upstairs as a
    // whole is the commonest answer here, and it wears the tile's own art so
    // the cell you just clicked is still recognisable one layer in.
    const self = selfOption(openGroup);
    const inGroup = self ? [self, ...openGroup.options] : openGroup.options;
    const iconInGroup = (id) => (self && id === self.id ? groupIcon(openGroup)
                                                       : iconFor(id));
    return html`<${Modal} title=${openGroup.label} icon="target" size="grid"
        onClose=${onClose}>
      <button type="button" class="entity-back" ref=${focusOnDrillIn}
          onclick=${() => setOpenGroupKey(null)}>
        <${Icon} name="chevron" size=${15} /> Back
      </button>
      <${CellGrid} options=${inGroup} value=${value}
        iconFor=${iconInGroup} onPick=${handlePick} />
    <//>`;
  }

  return html`<${Modal} title=${title} icon="target" size="grid" onClose=${onClose}>
    ${shown.map((group, index) => html`<div class="entity-section" key=${group.key}>
      ${shown.length > 1
        ? html`<div class="entity-section-head"><b>${group.label}</b></div>`
        : null}
      <${CellGrid} options=${group.options} value=${value}
        iconFor=${iconFor} onPick=${handlePick}
        clearLabel=${index === 0 ? placeholder : null} />
    </div>`)}
  <//>`;
}

/**
 * groups      [{ key, label, icon?, pick?, sub?, options: [{ id, name, sub?,
 *             rank? }] }] — a group with `pick` is pickable AS ITSELF: its
 *             layer-2 grid leads with a cell emitting that id, labelled with
 *             the group's label and `sub`. The caller marks it, the same
 *             call-site-owns-the-policy rule `allow` follows.
 * value       current id (string) or null
 * onChange    (id) => void
 * allow       optional (id) => boolean — the CALLER's domain filter
 * iconFor     (id) => image URL for a cell
 * title       dialog heading, e.g. "Choose a star"
 * depth       1 = one grid with headings; 2 = groups, then the chosen group
 * placeholder trigger label when nothing is chosen AND the label of the clear
 *             cell that emits null. Omit it and the picker cannot be cleared —
 *             the native <select> this replaced had a placeholder <option>
 *             emitting null, and dropping it silently made eight optional
 *             clause params unclearable (whole-branch review C1, 2026-07-25).
 * nextStep    optional component for a third layer. Picking a cell (never the
 *             clear cell) renders <${nextStep} value option onBack onClose />
 *             inside this SAME dialog instead of calling onChange — the step
 *             owns the write, this picker never learns what it is (spec
 *             2026-07-25-target-picker-strategy-step). Omit it and every
 *             call site is byte-for-byte what it was before this prop existed.
 */
export function EntityPicker({ groups, value, onChange, allow, iconFor,
                              title = "Choose", placeholder = "— pick —",
                              depth = 1, disabled = false, nextStep = null }) {
  const [open, setOpen] = useState(false);
  const current = visibleGroups(groups, allow, value)
    .flatMap((group) => group.options)
    .find((option) => option.id === value) || null;
  return html`<${Fragment}>
    <button type="button" class="entity-trigger" disabled=${disabled}
        aria-haspopup="dialog" onclick=${() => setOpen(true)}>
      ${current
        ? html`<img class="entity-row-icon" src=${iconFor(current.id)} alt="" />`
        : null}
      <span class="entity-trigger-label">${current ? current.name : placeholder}</span>
      <${Icon} name="chevron" size=${15} />
    </button>
    ${open ? html`<${PickerDialog} groups=${groups} value=${value} allow=${allow}
      title=${title} iconFor=${iconFor} depth=${depth} placeholder=${placeholder}
      nextStep=${nextStep}
      onPick=${(id) => { setOpen(false); onChange(id); }}
      onClose=${() => setOpen(false)} />` : null}
  <//>`;
}
