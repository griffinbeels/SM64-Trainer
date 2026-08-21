// src/sm64_events/ui/components/runnerpage.js — the [[runner page]]: one
// community runner's ratings, read-only, reached through two doors — a
// [[Rank board]] row (leaderboard.js) and a runner's name inside a
// [[Library tab]] entry (librarytarget.js). ONE component either way, never
// two that look alike (task-5-brief.md's own contract): it draws through
// rankpage.js's own ScopeChips/CoverageStrip/Breakdown pointed at the
// runner's `/api/leaderboard/runner/{name}` data instead of `/api/marelo` —
// never a second implementation of any of the three. No history curve (the
// sheet holds one time per row, no series to plot) and no Leaderboard card
// (that is where you arrived FROM). Read-only throughout: CoverageStrip gets
// no `onEdit` (no ✎) and Breakdown's `variant="runner"` carries no Ignore
// button.
import { h, Fragment } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { getJSON } from "../api.js";
import { RankIcon } from "./rankicon.js";
import { fmtPoints } from "./marelo.js";
import { Icon } from "./icons.js";
import { PageState, InlineState } from "./states.js";
import { Breakdown, CoverageStrip, ScopeChips } from "./rankpage.js";

const html = htm.bind(h);

export function RunnerPage({ t, runnerName, onClose }) {
  const [scopes, setScopes] = useState(null);
  const [scopesErr, setScopesErr] = useState(null);
  const [scopeId, setScopeId] = useState(null);
  const [data, setData] = useState(null);
  const [dataErr, setDataErr] = useState(null);

  // The scope LIST comes off the same door RankPage uses (there is only one
  // set of scopes in this app, yours or a runner's) — only the per-scope
  // RATING below reads from the runner's own endpoint.
  useEffect(() => {
    let alive = true;
    getJSON("/api/marelo/scopes").then((response) => {
      if (!alive) return;
      setScopes(response.scopes);
      setScopeId((current) => current ?? response.active);
    }).catch((error) => alive && setScopesErr(error));
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (!scopeId) return undefined;
    let alive = true;
    // Clear the old scope's rating up front, same reason RankPage's own
    // fetch effect does: a 404 on the new scope must never leave the OLD
    // scope's breakdown on screen under the new scope's label.
    setDataErr(null);
    setData(null);
    getJSON(`/api/leaderboard/runner/${encodeURIComponent(runnerName)}`
        + `?scope=${encodeURIComponent(scopeId)}`)
      .then((response) => alive && setData(response))
      .catch((error) => alive && setDataErr(error));
    return () => { alive = false; };
  }, [scopeId, runnerName]);

  if (!scopes) return html`<${PageState} kind=${t.connected ? "loading" : "offline"}
      title=${`Loading ${runnerName}'s ranks`} message=${scopesErr ? scopesErr.message : undefined} />`;
  if (!scopeId) return html`<${PageState} kind=${t.connected ? "loading" : "offline"}
      title=${`Loading ${runnerName}'s ranks`} />`;

  const routeOrder = scopeId.startsWith("route:");

  return html`<div class="rank-page runner-page">
    <button type="button" class="entity-back" onclick=${onClose}>
      <${Icon} name="chevron" size=${15} /> Back to the board
    </button>
    <div class="practice-card rank-card">
      <div class="rank-card-main">
        <span class="rank-icon-slot rank-card-icon">
          ${data && data.tier
            ? html`<${RankIcon} tier=${data.tier} division=${data.division} size=${64} />`
            : "–"}
        </span>
        <div>
          <h2>${runnerName}</h2>
          <p class="meta">${data
            ? `${data.label} · MARELO ${fmtPoints(data.marelo)} pts`
            : "Loading…"}</p>
        </div>
      </div>
    </div>
    <${ScopeChips} activeScopeId=${scopeId} onPick=${setScopeId}
        source=${`/api/leaderboard/runner/${encodeURIComponent(runnerName)}/summary`} />
    ${dataErr
      ? html`<div class="practice-card"><${InlineState} kind="error">
          ${dataErr.status === 404
            ? "Could not find this runner on that scope — pick another scope above."
            : dataErr.message}<//></div>`
      : !data
        ? html`<div class="practice-card"><${InlineState}>Loading ${runnerName}'s rating…<//></div>`
        : html`<${Fragment}>
            <div class="practice-card">
              <div class="rank-factor">Coverage <${CoverageStrip} t=${t} data=${data}
                caption=${`${data.practiced} of ${data.n} rated `
                  + `${data.n === 1 ? "entry" : "entries"} ${runnerName} has practiced — `
                  + "dim tiles are the ones they have not run yet."} /></div>
            </div>
            <div class="practice-card">
              <${Breakdown} key=${scopeId} data=${data} routeOrder=${routeOrder} variant="runner" />
            </div>
          <//>`}
  </div>`;
}
