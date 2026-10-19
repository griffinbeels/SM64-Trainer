// Claimed accounts are UUID-addressed. Sheet RunnerPage remains a separate source.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { useIdentityFetch } from "../refetch.js";
import { fmtIgtShort, fmtSeconds } from "../format.js";
import { Icon } from "./icons.js";
import { RankIcon } from "./rankicon.js";
import { fmtPoints } from "./marelo.js";
import { InlineState } from "./states.js";
import { SearchSelect } from "./searchselect.js";
import { ReadonlyScorecard } from "./scorecard.js";
import { RegionFlag } from "./regionflag.js";
import { SeatPicker } from "./community-library.js";
import { readPlayerPage, useUiLog } from "../uilog.js";

const html = htm.bind(h);
const PUBLIC_PLAYERS = "/api/community/players";
const timeLabel = value => value && Number.isFinite(Date.parse(value))
  ? new Date(value).toLocaleString() : null;

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

function PublicationState({ progress, partialReason }) {
  const updated = timeLabel(progress?.synced_utc);
  const coverage = progress?.coverage;
  return html`<p class="meta player-publication-state">${progress?.published
      ? `Published saved PBs${updated ? ` · Last synced ${updated}` : ""}`
      : "This player has not shared saved PBs. Their available ghosts are shown below."}</p>
    ${coverage && coverage.complete === false ? html`<p class="meta player-partial-coverage">
      Partial publication${Number.isInteger(coverage.shared_rows) ? ` · ${coverage.shared_rows} shared PB rows` : ""}
      ${Number.isInteger(coverage.withheld_rows) ? ` · ${coverage.withheld_rows} withheld` : ""}
      ${Number.isInteger(coverage.unsupported_rows) ? ` · ${coverage.unsupported_rows} unsupported` : ""}.
    </p>` : null}
    ${partialReason ? html`<p class="meta player-partial-reason">${partialReason}</p>` : null}`;
}

function PlayerIdentity({ player, progress, partialReason }) {
  const socials = safeSocials(player.socials);
  const explanation = player.claim_explanation || "This player has claimed their Trainer account. This does not verify a performance.";
  return html`<section class="practice-card player-profile-header">
    <h2>${player.name}${player.claimed ? html`<span class="player-claimed" title=${explanation}
      aria-label="Claimed Trainer account"><${Icon} name="check" size=${18} /></span>` : null}</h2>
    ${player.claimed ? html`<p class="meta player-claim-explanation">${explanation}</p>` : null}
    ${player.bio ? html`<p class="player-bio">${player.bio}</p>` : null}
    ${socials.length ? html`<nav class="player-socials" aria-label="Player links">
      ${socials.map(social => html`<a key=${social.kind} href=${social.url}
        target="_blank" rel="noopener noreferrer">${({twitch:"Twitch",youtube:"YouTube",website:"Website"})[social.kind]}</a>`)}
    </nav>` : null}
    <${PublicationState} progress=${progress} partialReason=${partialReason} />
    ${player.sheet_verified && player.sheet_runner ? html`<p class="meta">Linked Sheet runner: ${player.sheet_runner}</p>` : null}
  </section>`;
}

function RankSummary({ rank, label, reason }) {
  return html`<div class="player-rank-summary">
    ${rank?.tier ? html`<span class="rank-icon-slot" style="--icon-size:42px">
      <${RankIcon} tier=${rank.tier} division=${rank.division} size=${42} /></span>` : null}
    <div><strong>${label}</strong><p class="meta">${rank?.marelo != null
      ? `${fmtPoints(rank.marelo)} pts · ${rank.practiced}/${rank.n} graded entries`
      : reason || "No graded PBs in this scope."}</p></div>
  </div>`;
}

function Comparison({ t, comparison, player, scopes, scopeId, onScope, openLibrary }) {
  const context = comparison?.context;
  const groups = [{label:"Ranked scopes", options:(scopes || []).map(scope => ({value:scope.id,label:scope.label}))}];
  const selected = (scopes || []).find(scope => scope.id === scopeId);
  return html`<section class="player-comparison" data-scope=${scopeId}>
    <div class="practice-card player-comparison-context">
      <span class="meta">Your comparison scope</span>
      <${SearchSelect} title="Your comparison scope" value=${scopeId}
        valueLabel=${selected?.label || scopeId} groups=${groups} onChange=${onScope} />
      ${context ? html`<p class="meta">${context.label || selected?.label || scopeId}${" · "}PB basis${" · "}${(context.regions || []).map(region => region.toUpperCase()).join(" + ")}
        ${" · "}<span title=${context.calibration_revision || ""}>Using your Trainer’s rank standards</span></p>` : null}
      ${comparison ? html`<div class="player-rank-pair">
        <${RankSummary} rank=${comparison.you_rank} label="Your PB rank" />
        <${RankSummary} rank=${comparison.player_rank} label=${`${player.name} · published PB rank`} reason=${comparison.rank_reason} />
      </div>` : html`<p class="meta">A full comparison becomes available when saved PBs are shared.</p>`}
    </div>
    ${comparison?.card ? html`<${ReadonlyScorecard} t=${t} card=${comparison.card}
      playerName=${player.name} openLibrary=${openLibrary} />` : null}
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
        id:row.id,scope:"strategy",version:row.game_version,...(slot == null ? {} : {slot})});
      if (!alive.current) return;
      if (result.state === "needs_slot") setChoice({row,seats:result.seats});
      else if (["added","removed"].includes(result.state)) {
        setChoice(null); setNotice(result.state === "added" ? "Saved to this strategy’s Lineup." : "Removed from this strategy’s Lineup.");
        const key = savedTargetKey(row);
        setPage(previous => ({...previous,rows:previous.rows.map(item => savedTargetKey(item) === key
          ? {...item,saved_slot:pinSlot(result.pins,item.id)} : item)}));
      } else throw new Error("The saved rival could not be confirmed. Refresh these recordings.");
    } catch (problem) { if (alive.current) setActionError(problem.message); }
    finally { settled(); }
  }
  return {page,busy,choice,actionError,notice,load,change,close:() => { setChoice(null); setActionError(null); }};
}

function savedTargetKey(row) {
  return JSON.stringify([row.local_target?.kind,row.local_target?.entity_key,row.local_target?.strat,"strategy"]);
}

function pinSlot(pins, id) {
  const slot = Object.keys(pins || {}).find(key => pins[key] === id);
  return slot === undefined ? null : Number(slot);
}

function PlayerGhostRow({ row, tiles, busy, change }) {
  const key = row.local_target?.kind === "star" ? `star:${row.local_target.entity_key.replace("-",":")}`
    : row.local_target?.kind === "segment" ? `segment:${row.local_target.entity_key}` : null;
  const saved = tiles.find(tile => tile.key === key)?.goal_cs;
  const added = Number.isInteger(row.saved_slot) && row.saved_slot >= 0;
  return html`<article class="community-library-recording player-ghost-row" data-recording-id=${row.id}>
    <div class="community-library-player"><strong>${row.target?.name || "Shared practice target"}</strong>
      <span class="meta">${row.strategy}${" · "}<${RegionFlag} version=${row.game_version} size=${18} />${" · "}${row.clock.toUpperCase()}</span></div>
    <div class="community-library-performance"><strong>${fmtIgtShort(row.frames)} recorded</strong>
      ${saved != null ? html`<span class="meta">${fmtSeconds(saved/100)} saved PB in this comparison</span>` : null}</div>
    <button type="button" disabled=${busy || (!added && !row.can_add)}
      onclick=${() => change(row,added ? "remove" : "add")}>${added ? "Remove" : "Add rival"}</button>
    ${!added && !row.can_add ? html`<p class="meta community-library-unavailable">${row.unavailable_reason}</p>` : null}
  </article>`;
}

function GhostListFeedback({ page, retry }) {
  if (page.loading) return html`<p class="meta" role="status">Loading shared recordings…</p>`;
  if (page.error) return html`<p class="error" role="alert">${page.error}</p>
    <button type="button" onclick=${retry}>Retry</button>`;
  return !page.rows.length ? html`<p class="meta">No shared ghosts are available.</p>` : null;
}

function PlayerGhosts({ uid, comparison }) {
  const {page,busy,choice,actionError,notice,load,change,close} = usePlayerGhosts(uid);
  const tiles = (comparison?.card?.rows || []).flatMap(row => row.tiles);
  return html`<section class="practice-card player-ghosts">
    <div class="player-section-head"><h3>Available ghosts</h3><button type="button" disabled=${busy || page.loading}
      onclick=${() => load()}>Refresh</button></div>
    <p class="meta">These are playable recording times. A saved PB can be faster or use another strategy.</p>
    <${GhostListFeedback} page=${page} retry=${() => load()} />
    <div class="community-library-recordings">${page.rows.map(row => html`<${PlayerGhostRow}
      key=${row.id} row=${row} tiles=${tiles} busy=${busy} change=${change} />`)}</div>
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
      <button type="button" onclick=${retry}>Refresh shared page</button></div>
    ${error ? html`<section class="practice-card"><p class="error" role="alert">${error.message}</p>
      <button type="button" onclick=${retry}>Retry</button></section>` : null}
    ${!data && !error ? html`<section class="practice-card"><${InlineState}>Loading this player’s shared progress…<//></section>` : null}
    ${data ? html`<${PlayerIdentity} player=${data.player} progress=${data.progress} partialReason=${data.partial_reason} />
      <${Comparison} t=${t} comparison=${data.comparison} player=${data.player} scopes=${scopes}
        scopeId=${scopeId} onScope=${setScopeId} openLibrary=${openLibrary} />
      <${PlayerGhosts} key=${uid} uid=${uid} comparison=${data.comparison} />` : null}
  </div>`;
}

export function PlayerPage(properties) {
  return html`<${PlayerPageView} key=${properties.uid} ...${properties} />`;
}
