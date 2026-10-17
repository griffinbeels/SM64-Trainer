// src/sm64_events/ui/components/rivalpicker.js — the swap popover on a Lineup seat.
//
// ⇄ on a seat opens this under that seat: a small task, so nothing else on
// screen moves (round 3). It searches the players who shared a run on this
// star and strategy (GET /api/ghost/rivals/search), one row per player, and
// a pick puts that run in this seat, pinned. A pinned seat also offers its way
// back: "Let the trainer choose". Escape or a click away changes nothing.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON } from "../api.js";
import { fmtIgtShort } from "../format.js";
import { groupPlayers } from "../rivals.js";
import { RankIcon } from "./rankicon.js";

const html = htm.bind(h);

function PlayerResult({ player, chosen, onPick, busy }) {
  const [selected, setSelected] = useState(player.recordings[0].id);
  const row = player.recordings.find(item => item.id === selected) || player.recordings[0];
  const duplicate = chosen.has(row.id);
  return html`<li class="rival-swap-result">
    <span class="rank-icon-slot" style="--icon-size:18px"><${RankIcon} tier=${row.tier} division=${row.division} size=${18} /></span>
    <span class="rival-swap-who"><strong>${player.name}</strong>
      ${player.recordings.length > 1 && html`<select aria-label=${`Recording by ${player.name}`}
          value=${row.id} onchange=${event => setSelected(event.target.value)}>
        ${player.recordings.map((recording, index) => html`<option value=${recording.id}>
          ${fmtIgtShort(recording.frames)}${index === 0 ? " · Fastest" : ""}
        </option>`)}
      </select>`}
    </span>
    <span class="rival-swap-time">${fmtIgtShort(row.frames)}</span>
    <button type="button" class="rival-swap-pick" disabled=${busy || duplicate} onclick=${() => onPick(row.id)}>
      ${duplicate ? "Racing" : "Race"}</button>
  </li>`;
}

export function RivalSwap({ target, scope = "strategy", rows, slot, name, pinned, style, onClose, onAdd, onUnpin }) {
  const [query, setQuery] = useState("");
  const [answer, setAnswer] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const cache = useRef(new Map());
  const request = useRef(0);
  const box = useRef(null);
  useEffect(() => {
    const revision = ++request.current;
    setError(null);
    const key = JSON.stringify([target, scope, query.trim()]);
    const cached = cache.current.get(key);
    if (cached) { setAnswer(cached); return undefined; }
    setAnswer(null);
    const timer = setTimeout(async () => {
      try {
        const params = new URLSearchParams({ player: query.trim(), target: JSON.stringify(target), scope });
        const result = await getJSON(`/api/ghost/rivals/search?${params}`);
        if (revision === request.current) {
          cache.current.set(key, result);
          if (cache.current.size > 20) cache.current.delete(cache.current.keys().next().value);
          setAnswer(result);
        }
      } catch (failure) { if (revision === request.current) setError(failure.message); }
    }, query ? 250 : 0);
    return () => { clearTimeout(timer); request.current++; };
  }, [query, JSON.stringify(target), scope]);
  useEffect(() => {
    const away = event => { if (box.current && !box.current.contains(event.target) && !busy) onClose(); };
    const escape = event => { if (event.key === "Escape" && !busy) onClose(); };
    const timer = setTimeout(() => { document.addEventListener("pointerdown", away); document.addEventListener("keydown", escape); });
    return () => { clearTimeout(timer); document.removeEventListener("pointerdown", away); document.removeEventListener("keydown", escape); };
  }, [busy, onClose]);
  const pick = async id => {
    setBusy(true); setError(null);
    const result = await onAdd(id);
    setBusy(false);
    if (result.applied) onClose(); else setError(result.error);
  };
  const chosen = new Set(rows.map(row => row.id));
  const players = groupPlayers(answer?.records || []);
  return html`<div class="rival-swap" ref=${box} role="dialog" aria-label=${`Swap ${name}`} style=${style}>
    <div class="rival-swap-label">Instead of ${name}</div>
    ${pinned && html`<button type="button" class="rival-swap-unpin" disabled=${busy} onclick=${onUnpin}>
      Let the trainer choose</button>`}
    <input class="rival-swap-search" type="search" autofocus maxlength="80" placeholder="Find a player"
      aria-label="Find a player" value=${query} oninput=${event => setQuery(event.target.value)} />
    ${error ? html`<p class="error" role="alert">${error}</p>` : !answer ? html`<p class="rival-swap-status" role="status">Searching…</p>`
      : players.length ? html`<ul class="rival-swap-results">${players.map(player => html`
          <${PlayerResult} key=${player.id} player=${player} chosen=${chosen} onPick=${pick} busy=${busy} />`)}</ul>`
      : html`<p class="rival-swap-status">No one found for this ${target?.kind === "segment" ? "segment" : "star"}.</p>`}
    ${answer?.next_cursor && html`<button type="button" class="rival-swap-more" disabled=${busy} onclick=${async () => {
      const revision = request.current;
      setBusy(true);
      try {
        const params = new URLSearchParams({ player: query.trim(), target: JSON.stringify(target), scope, cursor: JSON.stringify(answer.next_cursor) });
        const result = await getJSON(`/api/ghost/rivals/search?${params}`);
        if (revision === request.current) setAnswer({ ...result, records: [...answer.records, ...result.records] });
      } catch (failure) { if (revision === request.current) setError(failure.message); } finally { setBusy(false); }
    }}>More players</button>`}
  </div>`;
}
