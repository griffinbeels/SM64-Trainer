// The recorder's parent selection: replace a slot, add a parent, or remove it.
import { h } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { courseUnionGroups, entityKeyForOption, parseSegmentId } from "../entities.js";
import { Icon } from "./icons.js";
import { PickerDialog } from "./entitymodal.js";
import { optionIconSrc } from "./entityicons.js";

const html = htm.bind(h);

function parentGroupsFor(t) {
  const groups = t?.view ? courseUnionGroups(t.view.catalog,
    (t.segments || []).filter((s) => !s.is_hundred_coin_engine),
    (t.vocab || {}).course_by_level || {}, {}, (t.vocab || {}).origins || null) : [];
  // Castle area tiles answer as themselves, while still opening their segments.
  return groups.map((group) => {
    const region = group.key.startsWith("castle-") && group.key !== "castle-segments"
      ? group.key.slice(7) : null;
    return region ? { ...group, pick: `area:${region}`, sub: "the whole area" } : group;
  });
}

function parentNameFor(groups, id) {
  for (const group of groups) {
    if (group.pick === id) return group.label;
    const option = group.options.find((item) => item.id === id);
    if (option) return option.name;
  }
  return entityKeyForOption(id);
}

function replaceParent(held, at, id) {
  const next = held.filter((_, index) => index !== at);
  if (id == null || next.includes(id)) return next;
  if (at === "add" || at == null || at >= held.length) return [...held, id];
  next.splice(at, 0, id);
  return next;
}

export function RecordParents({ t, options, onChange }) {
  const [pickingAt, setPickingAt] = useState(null);
  const groups = parentGroupsFor(t);
  const commit = (id) => {
    onChange((held) => replaceParent(held, pickingAt, id));
    setPickingAt(null);
  };
  return html`<div class="record-parent-picker">
    <div class="record-parent">
      <span class="field-label">What is this a piece of?</span>
      ${options.length === 0
        ? html`<button type="button" class="entity-trigger" onclick=${() => setPickingAt("add")}>
            Nothing — it stands on its own
          </button>`
        : options.map((id, index) => html`<button type="button" key=${id} class="entity-trigger"
            onclick=${() => setPickingAt(index)}>${parentNameFor(groups, id)}</button>`)}
      ${options.length > 0 && html`<button type="button" class="quiet-button record-parent-add"
          title="It is a piece of another star or segment too" onclick=${() => setPickingAt("add")}>
        <${Icon} name="plus" size=${14} /> Add
      </button>`}
    </div>
    ${pickingAt != null && html`<${PickerDialog} groups=${groups}
      value=${typeof pickingAt === "number" ? options[pickingAt] : null}
      title="What is this a piece of?" depth=${2}
      placeholder=${typeof pickingAt === "number"
        ? "Nothing — remove this one" : "Nothing — it stands on its own"}
      iconFor=${(id) => optionIconSrc(t, parseSegmentId(id) == null ? "star" : "segment",
        parseSegmentId(id) == null ? id : parseSegmentId(id))}
      onPick=${commit} onClose=${() => setPickingAt(null)} />`}
  </div>`;
}
