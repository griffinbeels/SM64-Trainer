// src/sm64_events/ui/components/stratpicker.js — THE active-strategy dropdown
// (+ "new strat…" modal) for a practice card.
//
// ONE component shared by the star and segment cards. It exists because the
// picker shipped on stars only: segments had no way to pick a strategy at all
// (user-reported 2026-07-23) even though the whole rank/standards/compare
// stack was already strategy-aware for them. Parity is now structural — a
// change here lands on both kinds — and tests/test_ui_section_parity.py fails
// if a future card renders one without the other.
//
// `identity` is the POST /api/strat body identity: {course_id, star_id} for a
// star, {kind:"segment", segment_id} for a segment. `entity` is the rank
// entity_key the creation modal writes standards under.
//
// `submit(tag)` overrides where the pick is written (default: POST /api/strat
// with `identity`). The attempt rows in practice.js pass a submit that hits
// POST /api/attempts/{id}/strat, so reclassifying a recorded run reuses this
// exact dropdown — including the "+ new strat…" modal and the dropped-write
// recovery — instead of a second, drifting copy.
import { h } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { send } from "../api.js";
import { StratModal } from "./stratmodal.js";

const html = htm.bind(h);

// `allowBlank=false` drops the "no strategy" sentinel entirely. Segments whose
// DEFINITION carries a default_strat pass it: there is basically one way to do
// a castle movement, so "no strategy" is not a state the user may choose (spec
// 2026-07-24-segment-default-strat). The server enforces the same rule — a
// falsy strat_set on a defaulted segment falls back to the default rather than
// clearing (projection.py caveat 17) — so this only hides an option that could
// not stick anyway.
// `groups` (optional) is the server's own resolution of which exit-star
// variant each strategy belongs to — [] or absent for every entity but a
// 100-coin star. When present the list is drawn as <optgroup>s showing the
// LEAF ("Standard") under a heading ("100c + Race"), because a 100-coin
// strategy's stored name is variant-qualified and the qualifier is exactly
// what the heading already says. Nothing here derives the grouping: the split
// happens once, in ranks/standards.py, and arrives ready to render.
export function StratPicker({ entity, identity, strategies, active, onChanged,
                              submit, blankLabel = "— no strat —",
                              highlightUnset = true, allowBlank = true,
                              groups = null }) {
  // Bumped to force the <select> to remount and re-read `active`. A native
  // <select> change updates the DOM immediately, but if the write is dropped
  // (or cancelled) `active` stays null, so its `value` prop never changes and
  // Preact won't reset the element — the dropdown would keep showing a phantom
  // pick while the border stays red, then revert on the next unrelated
  // remount. Bumping the key snaps it back to the server's truth.
  const [nonce, setNonce] = useState(0);
  const [showModal, setShowModal] = useState(false);
  // `strategies` is the server's CURRENT list — a purged/tombstoned name is
  // filtered out of it — but `active` on a historical attempt row can still
  // carry a now-purged name. A <select> whose `value` matches no <option>
  // renders with selectedIndex -1, i.e. BLANK, which is indistinguishable
  // from the "— no strat —" sentinel and is exactly the "unlabeled looks
  // like a rendering gap" bug this component exists to fix. So the current
  // value always stays listed, same rule as segments.js's dropdowns.
  const options = active && !(strategies || []).includes(active)
    ? [...(strategies || []), active]
    : (strategies || []);

  async function setStrat(value) {
    if (value === "__new") { setShowModal(true); return; }
    const tag = value || null;
    try {
      // `submit` lets a caller redirect the write without forking the
      // component: the practice cards set the ACTIVE strategy, an attempt row
      // reclassifies one recorded attempt. One dropdown, so the modal, the
      // dropped-write recovery, and the styling can't drift apart.
      if (submit) await submit(tag);
      else await send("POST", "/api/strat", { ...identity, strat_tag: tag });
    } catch (e) {
      // A dropped write (tracker reconnecting, or a second copy running) must
      // NOT silently leave a phantom selection that later reverts. Tell the
      // user and force the dropdown back to the real, still-unset value.
      window.alert("Couldn't save the strategy — the tracker may be reconnecting "
        + "or a second copy of it is running. Please try again.");
      setNonce((n) => n + 1);
    }
    onChanged();   // resync the dropdown to the server's truth either way
  }

  // A grouped list must still be able to show a value the groups do not cover
  // — a historical attempt's purged strat, or a hand-edited name matching no
  // variant — for the same reason the flat list keeps `active` listed: a
  // <select> whose value matches no <option> renders BLANK, which reads as
  // "no strategy" rather than as the name it actually holds.
  const grouped = (groups || []).filter((g) => (g.strategies || []).length);
  const inGroups = new Set(grouped.flatMap(
    (g) => g.strategies.map((s) => s.name)));
  const strays = options.filter((s) => !inGroups.has(s));

  // A CHOICE THAT DOES NOT EXIST IS NOT SHOUTED ABOUT (round 23, 2026-08-08).
  // `needs-strat` is the red glow meaning "you have not picked one yet" -- an
  // instruction. With no strategies to pick it instructs nobody: Griffin, on a
  // freshly-recorded subsection, "If a segment doesn't have a ranked ladder
  // (aka there are no strategies to select), it should not be blinking red
  // (there's nothing to select...)". The control stays live and stays
  // enabled -- "+ new strat..." is a real action and disabling the only way
  // out would be the other half of his dead-control rule -- it just goes
  // quiet. `options` rather than `strategies` is deliberate: a historical
  // attempt carrying a purged name has exactly one option and it IS the
  // active one, which this expression already covers through `!active`.
  const nothingToPick = options.length === 0;
  return html`<select key=${`strat-${nonce}`}
      class="meta ${!active && highlightUnset && !nothingToPick ? "needs-strat" : ""}"
      value=${active || ""}
      onchange=${(changeEvent) => setStrat(changeEvent.target.value)}>
    ${allowBlank ? html`<option value="">${blankLabel}</option>` : null}
    ${grouped.length
      ? html`${grouped.map((g) => html`<optgroup label=${g.label}>
            ${g.strategies.map((s) => html`
              <option value=${s.name}>${s.leaf}</option>`)}
          </optgroup>`)}
        ${strays.map((s) => html`<option value=${s}>${s}</option>`)}`
      : options.map((s) => html`<option value=${s}>${s}</option>`)}
    <option value="__new">+ new strat…</option>
  </select>
  ${showModal ? html`<${StratModal} entity=${entity} existing=${options}
      onSaved=${(stratName) => { setShowModal(false); setStrat(stratName); }}
      onClose=${() => { setShowModal(false); setNonce((n) => n + 1); }} />` : null}`;
}
