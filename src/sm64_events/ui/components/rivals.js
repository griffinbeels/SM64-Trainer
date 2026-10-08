import {h} from "preact";
import {useEffect, useRef, useState} from "preact/hooks";
import htm from "htm";
import {Icon} from "./icons.js";
import {RankIcon} from "./rankicon.js";
import {RivalSettings} from "./rivalsettings.js";
import {RivalPicker} from "./rivalpicker.js";
import {fmtIgtShort} from "../format.js";

const html = htm.bind(h);

function RivalRow({row, replace, reset}) {
  const name = row.player_name || "Chosen rival";
  return html`<li class="rival-row" data-slot=${row.slot}>
    ${!row.pb && html`<button class="rival-hit" type="button" aria-label=${`Replace ${name}`}
      title=${`Replace ${name}`} onclick=${()=>replace(row.slot)}></button>`}
    <div class="rival-content">
      <span class="rival-color" aria-hidden="true" style=${`--rival-hue:${row.hue_degrees}deg`}></span>
      <div class="rival-identity"><div class="rival-name-line"><strong>${row.pb ? "Your personal best" : name}</strong>
        ${row.pinned && html`<button class="icon-button rival-reset" type="button" title="Restore automatic rival"
          aria-label=${`Restore automatic rival in ${name}’s slot`} onclick=${()=>reset(row.slot)}>
          <${Icon} name="restart" /></button>`}
      </div><small>${row.pb ? "" : row.pinned ? "Chosen by you · " : "Automatic · "}${row.strategy_name || row.strategy || "Standard"}</small>
        ${row.reason_text && html`<small class="rival-unavailable">${row.reason_text}</small>`}
      </div>
      <span class="rank-icon-slot" style="--icon-size:40px">
        ${row.tier && html`<${RankIcon} tier=${row.tier} division=${row.division} size=${40} />`}
      </span>
      <span class="rival-time">${row.frames == null ? "–" : fmtIgtShort(row.frames)}</span>
      ${!row.pb && html`<span class="rival-swap" aria-hidden="true">⇄</span>`}
    </div>
  </li>`;
}

export function Rivals({session, write, renderAppearance}) {
  const rivals = session?.rivals;
  const card = useRef(null);
  const dialogHost = useRef(null);
  const [dialog,setDialog] = useState(null);
  const [anchor,setAnchor] = useState({});
  const [error,setError] = useState(null);
  useEffect(()=>{
    if (!dialog || !card.current) return undefined;
    const measure=()=>{
      const rect=card.current.getBoundingClientRect();
      const half=Math.min(320,(innerWidth-32)/2);
      const halfHeight=(dialogHost.current?.querySelector(".modal")?.getBoundingClientRect().height || 0)/2;
      setAnchor({"--rival-dialog-x":`${Math.max(half+16,Math.min(innerWidth-half-16,rect.x+rect.width/2))}px`,
        "--rival-dialog-y":`${Math.max(halfHeight+16,Math.min(innerHeight-halfHeight-16,rect.y+rect.height/2))}px`});
    };
    measure();
    const observer=new ResizeObserver(measure); observer.observe(card.current);
    const modal=dialogHost.current?.querySelector(".modal");
    if(modal) observer.observe(modal);
    window.addEventListener("resize",measure); window.addEventListener("scroll",measure,true);
    return ()=>{observer.disconnect();window.removeEventListener("resize",measure);window.removeEventListener("scroll",measure,true);};
  }, [dialog]);
  if(!rivals) return null;
  const act=async body=>{
    setError(null);
    const result=await write({rivals:body,target:session.target});
    if(!result.applied) setError(result.error);
    return result;
  };
  const reset=async slot=>{
    const result=await act({action:"reset",slot});
    if(result.applied) card.current?.querySelector(`[data-slot="${slot}"] .rival-hit`)?.focus();
  };
  const visible=[...rivals.rows];
  if(rivals.options.include_pb && rivals.pb) visible.push({...rivals.pb,pb:true,hue_degrees:rivals.pb.hue_degrees ?? 250});
  return html`<section class="rivals" ref=${card} aria-label="Your rivals">
    <header class="rivals-header"><h3>Your rivals</h3><div>
      <button class="primary-button" onclick=${()=>setDialog({kind:"picker",slot:null})}><${Icon} name="plus" />Add Rival</button>
      <button class="icon-button" aria-label="Rival settings" title="Rival settings"
        onclick=${()=>setDialog({kind:"settings"})}><${Icon} name="settings" /></button>
    </div></header>
    ${rivals.status && html`<div class="rival-status" role="status">${rivals.status}</div>`}
    ${visible.length ? html`<ul class="rival-roster">${visible.map(row=>html`
      <${RivalRow} key=${row.pb ? "pb" : row.slot} row=${row} reset=${reset}
        replace=${slot=>setDialog({kind:"picker",slot})} />`)}</ul>`
      : html`<p class="rival-empty">No ghosts in this range yet.</p>`}
    ${error && html`<p class="error" role="alert">${error}</p>`}
    ${dialog && html`<div class="rival-dialog-host" ref=${dialogHost} style=${anchor}>
      ${dialog.kind==="settings" ? html`<${RivalSettings} rivals=${rivals} onClose=${()=>setDialog(null)}
        renderAppearance=${renderAppearance} onSave=${(options,appearance)=>write({rivals:{options},target:session.target,...appearance})} />`
        : html`<${RivalPicker} target=${session.target} rows=${visible} replacement=${dialog.slot}
          onClose=${()=>setDialog(null)} onAdd=${(id,slot)=>act({action:"add",id,...(slot == null ? {} : {slot})})} />`}
    </div>`}
  </section>`;
}
