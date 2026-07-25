import { h } from "preact";
import htm from "htm";
import { visibleGroups } from "../entities.js";

const html = htm.bind(h);

// THE picker behind every "choose a course / star / level / segment" control:
// the segment builder's clause params, the practice-target modal, and the
// route step editor. It knows NOTHING about levels, courses, stars, segments,
// world topology or routes — callers supply the groups (ui/entities.js builds
// them) and their own filter.
//
// It renders what entities.js's visibleGroups computes: one <optgroup> per
// group, in the caller's order, with emptied groups dropped and the current
// value kept listed. That last one is the important behaviour — see
// entities.js above visibleGroups for why (it's been fixed wrong twice
// before, elsewhere, and the reasoning lives with the function, not here).
//
// Ids are STRINGS, so a composite id ("8:2" = course 8, star 2) is as valid as
// a level id. The caller encodes and decodes; this file only passes them on.

/**
 * groups      [{ key, label, options: [{ id, name }] }]
 * value       current id (string) or null
 * onChange    (id | null) => void
 * allow       optional (id) => boolean — the CALLER's domain filter
 * placeholder optional leading option's label; omit for no placeholder
 * name        form-field identity for the <select> (form submission);
 *             defaults to a stable fallback so every call site gets one
 *             without editing four call sites — a Chrome form-field advisory
 *             fired at all three before this default existed, because
 *             nobody ever passed it. No `id` prop: nothing needs a label
 *             `for` yet, and a caller that does can add it back then.
 */
export function GroupedPicker({ groups, value, onChange, allow, placeholder,
                               disabled = false, name = "entity-picker" }) {
  const shown = visibleGroups(groups, allow, value);
  return html`<select name=${name} value=${value ?? ""}
      disabled=${disabled}
      onchange=${(event) => onChange(event.target.value === ""
        ? null : event.target.value)}>
    ${placeholder == null ? null
      : html`<option value="">${placeholder}</option>`}
    ${shown.map((group) => html`<optgroup key=${group.key} label=${group.label}>
      ${group.options.map((option) => html`<option key=${option.id}
        value=${option.id}>${option.name}</option>`)}
    </optgroup>`)}
  </select>`;
}
