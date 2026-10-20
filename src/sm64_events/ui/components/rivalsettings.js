// src/sm64_events/ui/components/rivalsettings.js — the Rivals section of Ghost settings.
//
// Who you race: how the rivals are chosen, how many, whether your own PB
// ghost races too, and the rank range. It lives in the one Ghost settings
// sheet beside appearance and the Ghost Library (round 5: the Lineup row
// carries no settings button of its own). The sheet owns Save and Cancel;
// this section only edits its draft.
import { h } from "preact";
import htm from "htm";
import { RankIcon } from "./rankicon.js";
import { RivalRankStepper } from "./rivalrank.js";
import { endpoint, rangeDraft, stepRange } from "../rivals.js";

const html = htm.bind(h);

export function rivalDraft(rivals) {
  return rangeDraft(rivals.options, rivals.pb, rivals.limits);
}

export function RivalOptions({ rivals, draft, setDraft }) {
  const { pb, limits } = rivals;
  const change = (name, delta) => setDraft(value => stepRange(value, pb, limits, name, delta));
  const adaptive = draft.mode === "adaptive";
  return html`<section class="rival-options" aria-label="Ghosts">
    <h4 class="ghost-settings-heading">Ghosts</h4>
    <div class="rival-settings-top">
      <label>Challenge<select value=${draft.mode} onchange=${event => setDraft(rangeDraft({ ...draft, mode: event.target.value }, pb, limits))}>
        <option value="adaptive">Follow my progress</option><option value="fixed">Choose a rank range</option>
      </select></label>
      <label>Ghosts<select value=${draft.count} onchange=${event => setDraft({ ...draft, count: Number(event.target.value) })}>
        ${limits.counts.map(count => html`<option value=${count}>${count} ${count === 1 ? "ghost" : "ghosts"}</option>`)}
      </select></label>
    </div>
    <label class="rival-check"><input type="checkbox" checked=${draft.include_pb}
      onchange=${event => setDraft({ ...draft, include_pb: event.target.checked })} />Also race my personal best</label>
    <div class="rival-range">
      ${["min", "max"].map(name => {
        const key = adaptive ? `${name}_ahead` : `${name}_step`;
        return html`<${RivalRankStepper} key=${name} name=${name === "min" ? "Minimum rank" : "Maximum rank"}
          step=${endpoint(draft, pb, name, limits)} mode=${draft.mode}
          offset=${adaptive ? draft[key] : null}
          canDecrease=${stepRange(draft, pb, limits, name, -1)[key] !== draft[key]}
          canIncrease=${stepRange(draft, pb, limits, name, 1)[key] !== draft[key]} change=${delta => change(name, delta)} />`;
      })}
    </div>
    ${adaptive && !pb && html`<p class="rival-empty">With no time yet, you race people from Capless 5 to Toad 1. Your first personal best moves this range.</p>`}
    ${adaptive && pb && html`<div class="rival-pb-context">${pb.step === limits.last_step ? "Your PB" : "Above your PB"}
      <span class="rank-icon-slot" style="--icon-size:32px"><${RankIcon} tier=${pb.tier} division=${pb.division} size=${32} /></span>
    </div>`}
  </section>`;
}
