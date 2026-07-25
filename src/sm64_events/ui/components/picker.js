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
// It owns exactly three behaviours, each of which has been implemented
// separately, and wrongly, somewhere in this codebase before:
//   1. render one <optgroup> per group, in the caller's order;
//   2. drop a group the filter emptied, so no heading sits over nothing;
//   3. KEEP THE CURRENT VALUE listed even when the filter rejects it.
// (3) is the important one: a stored or legacy value fed to a filtered
// dropdown otherwise renders blank and reads as unset — fixed once in
// stratpicker.js (purged strategies) and again in the segment builder
// (out-of-topology stored defs) before this component existed.
//
// Ids are STRINGS, so a composite id ("8:2" = course 8, star 2) is as valid as
// a level id. The caller encodes and decodes; this file only passes them on.

/**
 * groups      [{ key, label, options: [{ id, name }] }]
 * value       current id (string) or null
 * onChange    (id | null) => void
 * allow       optional (id) => boolean — the CALLER's domain filter
 * placeholder optional leading option's label; omit for no placeholder
 * id, name    optional — forwarded to the <select> so a caller that needs a
 *             form-field identity (label `for`, form submission) can supply
 *             one; the picker itself has no opinion on either
 */
export function GroupedPicker({ groups, value, onChange, allow, placeholder,
                               disabled = false, id, name }) {
  const shown = visibleGroups(groups, allow, value);
  return html`<select id=${id} name=${name} value=${value ?? ""}
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
