import {h} from "preact";
import {useState} from "preact/hooks";
import htm from "htm";
import {Modal} from "./modal.js";
import {RankIcon} from "./rankicon.js";
import {RivalRankStepper} from "./rivalrank.js";
import {endpoint, rangeDraft, stepRange} from "../rivals.js";

const html = htm.bind(h);

export function RivalSettings({rivals, onClose, onSave, renderAppearance}) {
  const {pb, limits} = rivals;
  const [draft, setDraft] = useState(()=>rangeDraft(rivals.options, pb, limits));
  const [appearance, setAppearance] = useState({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const change = (name, delta) => setDraft(value=>stepRange(value,pb,limits,name,delta));
  const adaptive = draft.mode === "adaptive";
  const patch = body => setAppearance(previous=>({...previous,...(typeof body === "function" ? body(previous) : body)}));
  const save = async () => {
    setSaving(true); setError(null);
    const result = await onSave(draft,appearance);
    setSaving(false);
    if (result.applied) onClose(); else setError(result.error);
  };
  return html`<${Modal} title="Rival settings" icon="settings" onClose=${saving ? null : onClose}
      footer=${html`<button onclick=${onClose} disabled=${saving}>Cancel</button>
        <button class="primary-button" onclick=${save} disabled=${saving}>${saving ? "Saving…" : "Done"}</button>`}>
    <div class="rival-settings-top">
      <label>Challenge<select value=${draft.mode} onchange=${event=>setDraft(rangeDraft({...draft,mode:event.target.value},pb,limits))}>
        <option value="adaptive">Follow my progress</option><option value="fixed">Choose a rank range</option>
      </select></label>
      <label>Rivals<select value=${draft.count} onchange=${event=>setDraft({...draft,count:Number(event.target.value)})}>
        ${limits.counts.map(count=>html`<option value=${count}>${count} ${count===1 ? "rival" : "rivals"}</option>`)}
      </select></label>
    </div>
    <label class="rival-check"><input type="checkbox" checked=${draft.include_pb}
      onchange=${event=>setDraft({...draft,include_pb:event.target.checked})} />Also race my personal best</label>
    <label class="rival-check"><input type="checkbox" checked=${draft.strategy_only}
      onchange=${event=>setDraft({...draft,strategy_only:event.target.checked})} />${rivals.strategy_name || "Standard"} only</label>
    <div class="rival-range">
      ${["min","max"].map(name=>{
        const key = adaptive ? `${name}_ahead` : `${name}_step`;
        return html`<${RivalRankStepper} key=${name} name=${name==="min" ? "Minimum rank" : "Maximum rank"}
          step=${endpoint(draft,pb,name,limits)} mode=${draft.mode}
          offset=${adaptive ? draft[key] : null}
          canDecrease=${stepRange(draft,pb,limits,name,-1)[key] !== draft[key]}
          canIncrease=${stepRange(draft,pb,limits,name,1)[key] !== draft[key]} change=${delta=>change(name,delta)} />`;
      })}
    </div>
    ${adaptive && pb && html`<div class="rival-pb-context">${pb.step === limits.last_step ? "Your PB" : "Above your PB"}
      <span class="rank-icon-slot" style="--icon-size:32px"><${RankIcon} tier=${pb.tier} division=${pb.division} size=${32} /></span>
    </div>`}
    ${renderAppearance && html`<details class="rival-appearance"><summary>Appearance and sound</summary>
      ${renderAppearance(appearance,patch)}</details>`}
    ${error && html`<p class="error" role="alert">${error}</p>`}
  <//>`;
}
