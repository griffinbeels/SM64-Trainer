// A PB time and preserving its recording have separate outcomes.
import { h } from "preact";
import { useRef, useState } from "preact/hooks";
import htm from "htm";
import { send } from "../api.js";
import { Icon } from "./icons.js";
import { caveatOf, cardBadge } from "./marks.js";

const html = htm.bind(h);

export function usePbReplay(attemptId, timerMode, refresh) {
  const running = useRef(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(null);
  function finish() {
    running.current = false;
    setBusy(false);
  }
  async function run(retry) {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    setNotice(null);
    try {
      const result = retry
        ? await send("POST", `/api/attempts/${attemptId}/replay/save`)
        : await send("POST", "/api/pb", { attempt_id: attemptId, timer_mode: timerMode });
      if (result.replay_save?.status === "failed") {
        setNotice({ message: `PB saved. Replay could not be saved: ${result.replay_save.message}`, retry: true });
      } else if (retry) {
        setNotice({ message: "Replay saved.", retry: false });
      }
      await refresh();
    } catch (error) {
      setNotice({ message: `${retry ? "Replay" : "PB"} save failed: ${error.message}`, retry });
    } finally {
      finish();
    }
  }
  return { busy, notice, save: () => run(false), retry: () => run(true) };
}

// Why a row can neither save nor undo a PB, in the words its disabled Save
// carries. The server's gate reason (tracking/pbaction.py) picks the
// strategy sentences; the rest read off the row itself.
export function unavailableReason(attempt) {
  if (attempt.outcome !== "success") return "Only a finished run can be a PB";
  if (attempt.cleared) return "Restore this run to save it as a PB";
  const gate = attempt.pb_blocked && attempt.pb_blocked.reason;
  const strat = attempt.strat_tag;
  if (gate === "no_active_strat") {
    return strat ? `Pick ${strat} above to save this as its PB` : "Pick a strategy to save a PB";
  }
  if (gate === "foreign_strat") {
    return strat ? `Switch to ${strat} to save this as its PB` : "Give this run a strategy to save it as a PB";
  }
  return "This run can't be saved as a PB";
}

export function PbAction({ attempt, blocked, state, undo, beat }) {
  // The server resolves Save/Undo/blocked. Preserve its strategy and time gates.
  if (attempt.pb_action === "undo") return html`<button onclick=${undo} disabled=${state.busy}
    title="delete this save — the previous PB on this strategy becomes current again">
    <${Icon} name="undo" size=${14} /><span class="pb-word">Undo PB</span></button>`;
  // Nothing to save or undo: the button holds its place, disabled, and says
  // why (round 11 -- no row may leave a hole where its Save would be).
  if (attempt.pb_action !== "save" && !blocked) return html`<button
    class="pb-unavailable" disabled title=${unavailableReason(attempt)}>
    <${Icon} name="bookmark" size=${14} />
    <span class="save-pb-wide">Save as PB</span><span class="save-pb-narrow">Save PB</span></button>`;
  const { cls, title, mark } = saveReason(attempt, blocked, beat);
  return html`<button class=${cls}
    disabled=${Boolean(blocked) || state.busy} onclick=${state.save}
    title=${title} aria-label=${mark ? title : undefined}>
    <${Icon} name="bookmark" size=${14} />
    ${/* A button wearing its reason says less, so it fits the same slot as
         every other row's (round 7: no button moves between rows): "Save
         PB" plus the mark where there is room, the icon plus the mark in
         the narrow log. The full sentence rides the title either way. */""}
    <span class="save-pb-wide">${mark ? "Save PB" : "Save as PB"}</span>${mark
      ? "" : html`<span class="save-pb-narrow">Save PB</span>`}
    ${mark ? cardBadge(mark) : ""}</button>`;
}

// What the Save button says about itself. A refused save wears its reason
// disabled; a run that was not VALID (task 0144: a save state, a subarea
// reset, no reset) never saves itself, and his ruling leaves it "a manual
// choice for the user to decide" -- so Save stays live and wears that reason
// the same way. The server already sends both keys.
function saveReason(attempt, blocked, beat) {
  if (blocked) {
    return { cls: "pb-blocked", mark: blocked,
             title: `Cannot be saved as a PB — ${blocked.sentence}` };
  }
  const invalid = attempt.valid === false ? caveatOf(attempt.invalid_reason) : null;
  if (invalid) {
    return { cls: "pb-invalid", mark: invalid,
             title: `Not a valid run — ${invalid.sentence}. You can still save it as a PB.` };
  }
  return { cls: beat ? "pb-glow" : "", mark: null,
           title: "Save this time as a PB and keep its replay" };
}

export function PbReplayNotice({ state }) {
  if (!state.notice) return null;
  return html`<tr class="pb-replay-notice"><td colspan="6">
    <span role="status">${state.notice.message}</span>
    ${state.notice.retry ? html` <button disabled=${state.busy} onclick=${state.retry}>
      ${state.busy ? "Saving replay…" : "Retry save replay"}</button>` : ""}
  </td></tr>`;
}
