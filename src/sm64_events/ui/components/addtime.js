// src/sm64_events/ui/components/addtime.js — record a time you already earned.
//
// A player arriving with years of practice should not have to throw it away
// before the trainer will tell them anything. This is the door that works for
// everyone: type the time, it becomes a personal best.
//
// It reuses `TimeFields` rather than growing a field of its own — that control
// is THE way a time is typed here, three boxes reading {m}'{s}"{cc}, and a
// second notation would be the same ambiguity it was built to remove.
//
// It also does NOT carry a strategy picker. The card it sits in already has
// one, and the strategy showing there is the one the time is filed under —
// which is exactly what was asked for ("whatever strategy is selected is the
// default"). A second picker beside the first would be two controls writing
// one fact.
//
// THE SNAP is the part that would otherwise read as a bug. The timer is a
// frame counter, so only 30 of every 100 centisecond values can ever appear on
// it — a typed time has a 70% chance of naming one that cannot. The server
// rounds up on save either way; showing the rounded value BEFORE saving is
// what stops the card coming back with a number nobody typed. `attainableCs`
// is pinned to the server's own rule by tests/test_cross_language_parity.py.
import { h } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { send } from "../api.js";
import { attainableCs, fmtIgtShort, frameAtOrAfter } from "../format.js";
import { Disclose } from "./collapsible.js";
import { Icon } from "./icons.js";
import { TimeFields } from "./timefields.js";

const html = htm.bind(h);

// What the server reports back, in his words rather than the payload's.
function outcome(summary) {
  if (summary.imported) return { tone: "ok", text: "Saved" };
  if (summary.already_faster) {
    return { tone: "held", text: "You have already gone faster" };
  }
  return { tone: "bad", text: "That time could not be read" };
}

export function AddTime({ entityKey, strategy, onDone, gameVersion = null }) {
  const [open, setOpen] = useState(false);
  const [seconds, setSeconds] = useState(null);
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);

  // Null until three boxes hold something; the snap only has an answer once
  // there is a number to snap.
  const typedCs = seconds == null ? null : Math.round(seconds * 100);
  const landingCs = typedCs == null ? null : attainableCs(typedCs);
  const snapped = typedCs != null && landingCs !== typedCs;

  async function save() {
    if (landingCs == null || !strategy || busy) return;
    setBusy(true);
    try {
      const summary = await send("POST", "/api/import/manual", {
        entity_key: entityKey, strat_tag: strategy,
        time_cs: landingCs, game_version: gameVersion,
      });
      setStatus(outcome(summary));
      if (summary.imported && onDone) onDone(summary);
    } catch (err) {
      setStatus({ tone: "bad", text: err.message });
    } finally {
      setBusy(false);
    }
  }

  return html`<div class="addtime">
    <button type="button" class="quiet-button addtime-open"
        aria-expanded=${open ? "true" : "false"}
        onclick=${() => { setOpen(!open); setStatus(null); }}>
      <${Icon} name="plus" size=${13} />${" "}Add a time you already have
    </button>
    <${Disclose} open=${open} className="addtime-disclose">
      <div class="addtime-body">
        <${TimeFields} seconds=${seconds} onCommit=${setSeconds}
            compact=${true} label="time already earned" />
        ${/* The strategy is the card's, not a second pick of its own. Saying
             which one it is beats making him look away to check. */""}
        ${/* htm collapses the whitespace where text meets an interpolation,
             so every such join is written as ${" "} -- three sentences have
             shipped fused this way (`.claude/rules/ui-core.md`). */""}
        ${strategy
          ? html`<span class="addtime-strat">on${" "}${strategy}</span>`
          : html`<span class="addtime-strat addtime-needs-strat">pick a
              strategy above first</span>`}
        <button type="button" class="primary-button addtime-save"
            disabled=${landingCs == null || !strategy || busy}
            onclick=${save}>Save</button>
        ${snapped && html`<span class="addtime-snap">saves as${" "}
          ${fmtIgtShort(frameAtOrAfter(landingCs))}${" "}— the timer counts
          frames, so it cannot show every hundredth</span>`}
        ${status && html`<span class=${`addtime-status is-${status.tone}`}>${
          status.text}</span>`}
      </div>
    <//>
  </div>`;
}
