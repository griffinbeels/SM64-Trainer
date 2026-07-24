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

export function StratPicker({ entity, identity, strategies, active, onChanged,
                              submit, blankLabel = "— no strat —",
                              highlightUnset = true }) {
  // Bumped to force the <select> to remount and re-read `active`. A native
  // <select> change updates the DOM immediately, but if the write is dropped
  // (or cancelled) `active` stays null, so its `value` prop never changes and
  // Preact won't reset the element — the dropdown would keep showing a phantom
  // pick while the border stays red, then revert on the next unrelated
  // remount. Bumping the key snaps it back to the server's truth.
  const [nonce, setNonce] = useState(0);
  const [showModal, setShowModal] = useState(false);
  const options = strategies || [];

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

  return html`<select key=${`strat-${nonce}`}
      class="meta ${!active && highlightUnset ? "needs-strat" : ""}"
      value=${active || ""}
      onchange=${(changeEvent) => setStrat(changeEvent.target.value)}>
    <option value="">${blankLabel}</option>
    ${options.map((s) => html`<option value=${s}>${s}</option>`)}
    <option value="__new">+ new strat…</option>
  </select>
  ${showModal ? html`<${StratModal} entity=${entity} existing=${options}
      onSaved=${(stratName) => { setShowModal(false); setStrat(stratName); }}
      onClose=${() => { setShowModal(false); setNonce((n) => n + 1); }} />` : null}`;
}
