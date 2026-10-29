// Claimed accounts are UUID-addressed. Sheet RunnerPage remains a separate source.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { useIdentityFetch } from "../refetch.js";
import { fmtIgtShort } from "../format.js";
import { Icon } from "./icons.js";
import { InlineState } from "./states.js";
import { Breakdown, RankCard, ScopeChips } from "./rankpage.js";
import { ReadonlyScorecard } from "./scorecard.js";
import { RegionFlag } from "./regionflag.js";
import { GhostAction, GhostUnavailable, SeatPicker } from "./community-library.js";
import { readPlayerPage, useUiLog } from "../uilog.js";

const html = htm.bind(h);
const PUBLIC_PLAYERS = "/api/community/players";

function safeSocials(socials) {
  return (Array.isArray(socials) ? socials : []).filter(social => {
    if (!["twitch", "youtube", "website"].includes(social.kind)) return false;
    try {
      const url = new URL(social.url);
      return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password;
    } catch { return false; }
  });
}

function usePlayerData(uid, revision, initialScope) {
  const [scopes, setScopes] = useState(null), [scopeId, setScopeId] = useState(initialScope || null);
  const [snapshot, setSnapshot] = useState(null), [error, setError] = useState(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const request = new AbortController();
    getJSON("/api/marelo/scopes", {signal:request.signal}).then(result => {
      setScopes(result.scopes); setScopeId(current => current ?? result.active);
    }).catch(problem => { if (problem.name !== "AbortError") setError(problem); });
    return () => request.abort();
  }, [retry]);
  const identity = JSON.stringify([uid, scopeId]);
  useIdentityFetch(identity, `${revision}:${retry}`, cleared => {
    if (!uid || !scopeId) return undefined;
    const request = new AbortController();
    if (cleared) setSnapshot(null);
    setError(null);
    getJSON(`${PUBLIC_PLAYERS}/${encodeURIComponent(uid)}?scope=${encodeURIComponent(scopeId)}`,
      {signal:request.signal}).then(data => setSnapshot({identity,data}))
      .catch(problem => { if (problem.name !== "AbortError") setError(problem); });
    return () => request.abort();
  });
  return {scopes, scopeId, setScopeId, data:snapshot?.identity === identity ? snapshot.data : null,
    error, retry:() => setRetry(value => value + 1)};
}

function PlayerIdentity({ player }) {
  const socials = safeSocials(player.socials);
  const explanation = player.claim_explanation || "This player has claimed their Trainer account. This does not verify a performance.";
  return html`<section class="practice-card player-profile-header">
    <h2>${player.name}${player.claimed ? html`<span class="player-claimed" title=${explanation}
      aria-label="Claimed Trainer account"><${Icon} name="check" size=${18} /></span>` : null}</h2>
    ${player.bio ? html`<p class="player-bio">${player.bio}</p>` : null}
    ${socials.length ? html`<nav class="player-socials" aria-label="Player links">
      ${socials.map(social => html`<a key=${social.kind} href=${social.url}
        target="_blank" rel="noopener noreferrer">${({twitch:"Twitch",youtube:"YouTube",website:"Website"})[social.kind]}</a>`)}
    </nav>` : null}
    ${player.sheet_verified && player.sheet_runner ? html`<p class="meta">Linked Sheet runner: ${player.sheet_runner}</p>` : null}
  </section>`;
}

function Comparison({ t, comparison, player, scopes, scopeId, onScope, openLibrary }) {
  const context = comparison?.context;
  const playerRank = comparison?.player_rank;
  const description = context ? ["Saved PBs",
    (context.regions || []).map(region => region.toUpperCase()).join(" + ")].filter(Boolean).join(" · ") : null;
  return html`<section class="player-comparison" data-scope=${scopeId}>
    <${ScopeChips} activeScopeId=${scopeId} onPick=${onScope} chips=${comparison?.chips || []} />
    <${RankCard} t=${t} data=${playerRank} scopes=${scopes || []} scopeId=${scopeId}
      onScope=${onScope} readOnly description=${description} className="player-comparison-context"
      rankReason=${comparison?.rank_reason ? "Some PBs are unavailable. Full rank unavailable." : "No shared PBs yet."} />
    ${comparison?.card ? html`<${ReadonlyScorecard} t=${t} card=${comparison.card}
      playerName=${player.name} openLibrary=${openLibrary} />` : null}
    ${playerRank ? html`<div class="practice-card"><${Breakdown} key=${scopeId} t=${t}
      data=${playerRank} routeOrder=${scopeId.startsWith("route:")} readOnly /></div>` : null}
  </section>`;
}

function usePlayerGhosts(uid) {
  const [page, setPage] = useState({rows:[],cursor:null,loading:true,error:null});
  const [busy, setBusy] = useState(false), [choice, setChoice] = useState(null);
  const [actionError, setActionError] = useState(null), [notice, setNotice] = useState(null);
  const request = useRef(null), alive = useRef(true), writing = useRef(false);
  async function load(cursor = null) {
    request.current?.abort();
    const current = new AbortController(); request.current = current;
    setPage(previous => ({...previous,loading:true,error:null}));
    try {
      const data = await getJSON(`${PUBLIC_PLAYERS}/${encodeURIComponent(uid)}/recordings${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`,
        {signal:current.signal});
      if (!alive.current || request.current !== current) return;
      setPage(previous => ({rows:cursor ? [...previous.rows,...data.recordings] : data.recordings,
        cursor:data.next_cursor,loading:false,error:null}));
    } catch (problem) {
      if (alive.current && problem.name !== "AbortError") setPage(previous => ({...previous,loading:false,error:problem.message}));
    }
  }
  useEffect(() => { load(); return () => { alive.current = false; request.current?.abort(); }; }, []);
  function settled() { writing.current = false; if (alive.current) setBusy(false); }
  async function change(row, action, slot = null) {
    if (writing.current) return;
    writing.current = true; setBusy(true); setActionError(null); setNotice(null);
    try {
      const result = await send("POST","/api/ghost/rivals/saved",{target:row.local_target,action,
        id:row.id,version:row.game_version,...(slot == null ? {} : {slot})});
      if (!alive.current) return;
      if (result.state === "needs_slot") setChoice({row,seats:result.seats});
      else if (["added","removed"].includes(result.state)) {
        setChoice(null); setNotice(result.state === "added" ? "Saved to this star’s Lineup." : "Removed from this star’s Lineup.");
        const key = savedTargetKey(row);
        setPage(previous => ({...previous,rows:previous.rows.map(item => savedTargetKey(item) === key
          ? {...item,saved_slot:pinSlot(result.pins,item.id)} : item)}));
      } else throw new Error("The saved ghost could not be confirmed. Refresh these recordings.");
    } catch (problem) { if (alive.current) setActionError(problem.message); }
    finally { settled(); }
  }
  return {page,busy,choice,actionError,notice,load,change,close:() => { setChoice(null); setActionError(null); }};
}

function savedTargetKey(row) {
  return JSON.stringify([row.local_target?.kind,row.local_target?.entity_key]);
}

function pinSlot(pins, id) {
  const slot = Object.keys(pins || {}).find(key => pins[key] === id);
  return slot === undefined ? null : Number(slot);
}

function PlayerGhostRow({ row, busy, change }) {
  const added = Number.isInteger(row.saved_slot) && row.saved_slot >= 0;
  return html`<article class="community-library-recording player-ghost-row" data-recording-id=${row.id}>
    <div class="community-library-player"><strong>${row.target?.name || "Shared practice target"}</strong>
      <span class="meta">${row.strategy}${" · "}<${RegionFlag} version=${row.game_version} size=${18} />${" · "}${row.clock.toUpperCase()}</span></div>
    <div class="community-library-performance"><strong>${fmtIgtShort(row.frames)}</strong></div>
    <${GhostAction} row=${row} added=${added} context="player" disabled=${busy || (!added && !row.can_add)}
      onClick=${() => change(row,added ? "remove" : "add")}>${added ? "Remove" : "Add ghost"}</${GhostAction}>
    <${GhostUnavailable} row=${row} added=${added} />
  </article>`;
}

function GhostListFeedback({ page, retry }) {
  if (page.loading) return html`<p class="meta" role="status">Loading shared recordings…</p>`;
  if (page.error) return html`<p class="error" role="alert">${page.error}</p>
    <button type="button" onclick=${retry}>Retry</button>`;
  return !page.rows.length ? html`<p class="meta">No shared ghosts are available.</p>` : null;
}

function PlayerGhosts({ uid }) {
  const {page,busy,choice,actionError,notice,load,change,close} = usePlayerGhosts(uid);
  return html`<section class="practice-card player-ghosts">
    <div class="player-section-head"><h3>Available ghosts</h3><button type="button" class="icon-button" disabled=${busy || page.loading}
      title="Refresh ghosts" aria-label="Refresh ghosts" onclick=${() => load()}><${Icon} name="restart" /></button></div>
    <${GhostListFeedback} page=${page} retry=${() => load()} />
    <div class="community-library-recordings">${page.rows.map(row => html`<${PlayerGhostRow}
      key=${row.id} row=${row} busy=${busy} change=${change} />`)}</div>
    ${page.cursor ? html`<button type="button" disabled=${page.loading || busy} onclick=${() => load(page.cursor)}>Load more</button>` : null}
    ${notice ? html`<p class="meta" role="status">${notice}</p>` : null}
    ${actionError && !choice ? html`<p class="error" role="alert">${actionError}</p>` : null}
    ${choice ? html`<${SeatPicker} choice=${choice} busy=${busy} error=${actionError}
      onClose=${close} onChoose=${slot => change(choice.row,"add",slot)} />` : null}
  </section>`;
}

function PlayerPageView({ t, uid, onClose, openLibrary, initialScope, onScopeChange }) {
  const root = useRef(null);
  useUiLog(root, [readPlayerPage]);
  const {scopes,scopeId,setScopeId,data,error,retry} = usePlayerData(uid,t.standingRev ?? t.mareloRev,initialScope);
  useEffect(() => { if (scopeId) onScopeChange?.(scopeId); }, [scopeId]);
  return html`<div ref=${root} class="rank-page player-page" data-player-id=${uid}>
    <div class="player-page-tools"><button type="button" class="entity-back" onclick=${onClose}><${Icon} name="chevron" size=${15} /> Back</button>
      <button type="button" class="icon-button" title="Refresh shared page" aria-label="Refresh shared page"
        onclick=${retry}><${Icon} name="restart" /></button></div>
    ${error ? html`<section class="practice-card"><p class="error" role="alert">${error.message}</p>
      <button type="button" onclick=${retry}>Retry</button></section>` : null}
    ${!data && !error ? html`<section class="practice-card"><${InlineState}>Loading this player’s shared progress…<//></section>` : null}
    ${data ? html`<${PlayerIdentity} player=${data.player} />
      <${Comparison} t=${t} comparison=${data.comparison} player=${data.player} scopes=${scopes}
        scopeId=${scopeId} onScope=${setScopeId} openLibrary=${openLibrary} />
      <${PlayerGhosts} key=${uid} uid=${uid} />` : null}
  </div>`;
}

export function PlayerPage(properties) {
  return html`<${PlayerPageView} key=${properties.uid} ...${properties} />`;
}
