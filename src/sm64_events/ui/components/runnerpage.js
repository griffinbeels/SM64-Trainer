// src/sm64_events/ui/components/runnerpage.js — the [[Runner page]]: one
// community runner's ratings, read-only, reached through two doors — a
// [[Rank board]] row (leaderboard.js) and a runner's name inside a
// [[Library tab]] entry (librarytarget.js). ONE component either way, never
// two that look alike: it draws through rankpage.js's own ScopeChips/
// CoverageStrip/Breakdown pointed at `/api/leaderboard/runner/{name}` instead
// of `/api/marelo` — never a second implementation of any of the three. No
// history curve (the sheet holds one time per row, no series to plot) and no
// Leaderboard card (that is where you arrived FROM). Read-only throughout:
// CoverageStrip is `readOnly` (no ✎, no detail panel) and Breakdown's
// `variant="runner"` carries no Ignore button. Its one outward door (his
// first read, 2026-08-22): a lit coverage tile or an entity's name in the
// breakdown opens the Library on that entity, landed on THIS runner's
// graded entry -- `openLibrary`'s `{kind:"target", entity, runner, timeCs}`
// intent, the same door the standards table's time links use.
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

export function RunnerPage({ t, runnerName, onClose, openLibrary = () => {} }) {
  const [scopeId, setScopeId] = useState(null);
  const [scopesErr, setScopesErr] = useState(null);
  const [data, setData] = useState(null);
  const [dataErr, setDataErr] = useState(null);
  const runnerApi = `/api/leaderboard/runner/${encodeURIComponent(runnerName)}`;

  // Open on the scope YOU are focused on, not on Overall -- the one thing
  // read off your own scopes; the chip row below lists the runner's.
  useEffect(() => {
    let alive = true;
    getJSON("/api/marelo/scopes")
      .then((response) => alive && setScopeId((current) => current ?? response.active))
      .catch((error) => alive && setScopesErr(error));
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
    getJSON(`${runnerApi}?scope=${encodeURIComponent(scopeId)}`)
      .then((response) => alive && setData(response))
      .catch((error) => alive && setDataErr(error));
    return () => { alive = false; };
  }, [scopeId, runnerName]);

  if (!scopeId) return html`<${PageState} kind=${t.connected ? "loading" : "offline"}
      title=${`Loading ${runnerName}'s ranks`} message=${scopesErr ? scopesErr.message : undefined} />`;

  const routeOrder = scopeId.startsWith("route:");
  const openEntity = (entity) => openLibrary({
    kind: "target", entity: entity.key, runner: runnerName, timeCs: entity.time_cs });

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
    <${ScopeChips} activeScopeId=${scopeId} onPick=${setScopeId} source=${`${runnerApi}/summary`} />
    ${dataErr
      ? html`<div class="practice-card"><${InlineState} kind="error">
          ${dataErr.status === 404
            ? "Could not find this runner on that scope — pick another scope above."
            : dataErr.message}<//></div>`
      : !data
        ? html`<div class="practice-card"><${InlineState}>Loading ${runnerName}'s rating…<//></div>`
        : html`<${Fragment}>
            <div class="practice-card">
              <div class="rank-factor">Coverage <${CoverageStrip} t=${t} data=${data} readOnly
                onOpenEntity=${openEntity}
                caption=${`${data.practiced} of ${data.n} rated `
                  + `${data.n === 1 ? "entry" : "entries"} ${runnerName} has practiced — `
                  + "dim tiles are the ones they have not run yet; a lit tile opens "
                  + "their entry in the Library."} /></div>
            </div>
            <div class="practice-card">
              <${Breakdown} key=${scopeId} t=${t} data=${data} routeOrder=${routeOrder} variant="runner"
                onOpenEntity=${openEntity} />
            </div>
          <//>`}
  </div>`;
}
