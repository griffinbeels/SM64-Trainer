import {h} from "preact";
import {useEffect, useRef, useState} from "preact/hooks";
import htm from "htm";
import {getJSON} from "../api.js";
import {fmtIgtShort} from "../format.js";
import {groupPlayers} from "../rivals.js";
import {Modal} from "./modal.js";
import {RankIcon} from "./rankicon.js";

const html = htm.bind(h);

function PlayerResult({player, chosen, onAdd, busy}) {
  const [selected, setSelected] = useState(player.recordings[0].id);
  const row = player.recordings.find(item=>item.id===selected) || player.recordings[0];
  const duplicate = chosen.has(row.id);
  return html`<li class="rival-player-result">
    <div><strong>${player.name}</strong>
      ${player.recordings.length > 1 ? html`<select aria-label=${`Recording by ${player.name}`}
          value=${row.id} onchange=${event=>setSelected(event.target.value)}>
        ${player.recordings.map((recording,index)=>html`<option value=${recording.id}>
          ${fmtIgtShort(recording.frames)} · ${recording.strategy_name || recording.strategy || "Standard"}${index===0 ? " · Fastest" : ""}
        </option>`)}
      </select>` : html`<small>${row.strategy_name || row.strategy || "Standard"}</small>`}
    </div>
    <span class="rank-icon-slot" style="--icon-size:36px"><${RankIcon} tier=${row.tier} division=${row.division} size=${36} /></span>
    <span class="rival-time">${fmtIgtShort(row.frames)}</span>
    <button class="primary-button" disabled=${busy || duplicate} onclick=${()=>onAdd(row.id)}>
      ${duplicate ? "Added" : "Add"}</button>
  </li>`;
}

export function RivalPicker({target, scope = "strategy", rows, replacement, onClose, onAdd}) {
  const [query,setQuery] = useState("");
  const [answer,setAnswer] = useState(null);
  const [error,setError] = useState(null);
  const [busy,setBusy] = useState(false);
  const cache = useRef(new Map());
  const request = useRef(0);
  useEffect(()=>{
    const revision = ++request.current;
    setError(null);
    const key = JSON.stringify([target,scope,query.trim()]);
    const cached = cache.current.get(key);
    if (cached) {setAnswer(cached); return undefined;}
    setAnswer(null);
    const timer = setTimeout(async()=>{
      try {
        const params = new URLSearchParams({player:query.trim(),target:JSON.stringify(target),scope});
        const result = await getJSON(`/api/ghost/rivals/search?${params}`);
        if (revision===request.current) {
          cache.current.set(key,result);
          if (cache.current.size>20) cache.current.delete(cache.current.keys().next().value);
          setAnswer(result);
        }
      } catch(failure) {if(revision===request.current) setError(failure.message);}
    }, query ? 250 : 0);
    return ()=>{clearTimeout(timer);request.current++;};
  }, [query,JSON.stringify(target),scope]);
  const add = async id=>{
    setBusy(true); setError(null);
    const result = await onAdd(id,replacement);
    setBusy(false);
    if(result.applied) onClose(); else setError(result.error);
  };
  const chosen = new Set(rows.map(row=>row.id));
  const players = groupPlayers(answer?.records || []);
  return html`<${Modal} title=${replacement == null ? "Add Rival" : "Replace rival"} icon="target"
      onClose=${busy ? null : onClose}>
    <input class="rival-search" type="search" autofocus maxlength="80" placeholder="Search player"
      aria-label="Search player" value=${query} oninput=${event=>setQuery(event.target.value)} />
    ${error ? html`<p class="error" role="alert">${error}</p>` : !answer ? html`<p role="status">Searching…</p>`
      : players.length ? html`<ul class="rival-player-results">${players.map(player=>html`
          <${PlayerResult} key=${player.id} player=${player} chosen=${chosen} onAdd=${add} busy=${busy} />`)}</ul>`
      : html`<p>No ghosts found for this ${target?.kind === "segment" ? "segment" : "star"}.</p>`}
    ${answer?.next_cursor && html`<button disabled=${busy} onclick=${async()=>{
      const revision = request.current;
      setBusy(true);
      try {
        const params = new URLSearchParams({player:query.trim(),target:JSON.stringify(target),scope,cursor:JSON.stringify(answer.next_cursor)});
        const result = await getJSON(`/api/ghost/rivals/search?${params}`);
        if (revision===request.current) setAnswer({...result,records:[...answer.records,...result.records]});
      } catch(failure){if(revision===request.current) setError(failure.message);} finally {setBusy(false);}
    }}>More players</button>`}
  <//>`;
}
