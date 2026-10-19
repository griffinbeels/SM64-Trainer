import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON } from "../api.js";
import { Icon } from "./icons.js";

const html = htm.bind(h);

export function PlayerSearch({ onOpenPlayer }) {
  const [prefix, setPrefix] = useState("");
  const [result, setResult] = useState(null), [error, setError] = useState(null);
  const [loading, setLoading] = useState(false), [retry, setRetry] = useState(0);
  const request = useRef(null), active = useRef(true), lastCursor = useRef(null);
  useEffect(() => () => { active.current = false; request.current?.abort(); }, []);
  useEffect(() => {
    const changed = () => setRetry(value => value + 1);
    window.addEventListener("community-account-changed", changed);
    return () => window.removeEventListener("community-account-changed", changed);
  }, []);
  async function load(cursor = null) {
    request.current?.abort();
    const current = new AbortController(); request.current = current;
    lastCursor.current = cursor; setLoading(true); setError(null);
    const params = new URLSearchParams({prefix: prefix.trim()});
    if (cursor) params.set("cursor", cursor);
    try {
      const value = await getJSON(`/api/community/players?${params}`, {signal: current.signal});
      if (!active.current || current.signal.aborted) return;
      setResult(previous => ({...value, players: cursor ? [...previous.players, ...value.players] : value.players}));
    } catch (problem) {
      if (active.current && !current.signal.aborted) {
        if (problem.status === 409) lastCursor.current = null;
        setError(problem.message);
      }
    } finally { if (active.current && request.current === current) setLoading(false); }
  }
  useEffect(() => {
    request.current?.abort();
    setResult(null); setError(null);
    if (!prefix.trim()) return undefined;
    const timer = setTimeout(() => load(), 300);
    return () => { clearTimeout(timer); request.current?.abort(); };
  }, [prefix, retry]);
  return html`<section class="practice-card player-search">
    <h3>Trainer players</h3><p class="meta">Explore claimed accounts and the progress they choose to share.</p>
    <label>Player name<input type="search" value=${prefix} maxlength="80"
      oninput=${event => setPrefix(event.currentTarget.value)} placeholder="Start typing a player name" /></label>
    ${error ? html`<p class="error" role="alert">${error}</p><button type="button" disabled=${loading}
      onclick=${() => load(lastCursor.current)}>Retry</button>` : null}
    ${loading ? html`<p class="meta" role="status">Finding players…</p>` : null}
    ${result && !result.players.length ? html`<p class="meta">No claimed players match this name.</p>` : null}
    ${result ? html`<div class="player-search-results">${result.players.map(player => html`<button type="button"
      key=${player.uid} onclick=${() => onOpenPlayer?.(player.uid)}>${player.name}<${Icon} name="check" size=${14} /></button>`)}</div>` : null}
    ${result?.next_cursor && !error ? html`<button type="button" disabled=${loading}
      onclick=${() => load(result.next_cursor)}>Load more players</button>` : null}
  </section>`;
}
