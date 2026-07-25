// src/sm64_events/ui/components/rankpage.js — the Rank tab: scope picker,
// rank card, history chart, and the per-entity breakdown (which IS the route
// performance view when the scope is a route). Route performance, per-course
// averages and overall progress are the same view under different scopes, so
// there is one picker/card/chart/breakdown, not three near-duplicate pages.
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { rankColor, RANK_NAMES } from "./ranks.js";
import { Crest, fmtScore } from "./marelo.js";
import { Icon } from "./icons.js";
import { PageState, InlineState } from "./states.js";

const html = htm.bind(h);

// Mirrors ranks/scoring.SCORE_ANCHORS (constant-table mirror, not an
// algorithm — same convention as ranks.js/format.js). Iron carries no
// anchor server-side either; 0 is its implicit floor for the band line.
const ANCHORS = { Mario: 95, Grandmaster: 90, Master: 80, Diamond: 70,
  Platinum: 60, Gold: 45, Silver: 25, Bronze: 10, Iron: 0 };

const CHART_WIDTH = 720, CHART_HEIGHT = 220;

function HistoryChart({ points }) {
  if (!points || points.length < 2)
    return html`<p class="meta">Not enough history yet — finish a few more runs.</p>`;
  const xForIndex = (index) => (index / (points.length - 1)) * CHART_WIDTH;
  const yForScore = (score) =>
    CHART_HEIGHT - (Math.max(0, Math.min(100, score)) / 100) * CHART_HEIGHT;
  const line = points.map((point, index) =>
    `${index ? "L" : "M"}${xForIndex(index).toFixed(1)},${yForScore(point.marelo).toFixed(1)}`
  ).join(" ");
  return html`<svg class="rank-chart" viewBox=${`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
      role="img" aria-label="MARELO over time">
    ${RANK_NAMES.map((tier) => html`<g>
      <line x1="0" x2=${CHART_WIDTH} y1=${yForScore(ANCHORS[tier])} y2=${yForScore(ANCHORS[tier])}
        stroke=${rankColor(tier)} stroke-opacity=".28" stroke-dasharray="3 4" />
      <text x="4" y=${yForScore(ANCHORS[tier]) - 3} fill=${rankColor(tier)}
        font-size="9">${tier}</text></g>`)}
    <path d=${line} fill="none" stroke="var(--gold)" stroke-width="2" />
  </svg>`;
}

function Breakdown({ data, routeOrder, onToggle }) {
  const [byGain, setByGain] = useState(!routeOrder);
  const rows = byGain
    ? [...data.entities].sort((entityA, entityB) => entityB.gain - entityA.gain)
    : data.entities;
  return html`<div class="rank-breakdown">
    <div class="rank-breakdown-head">
      <b>${routeOrder ? "Route order" : "Everything in scope"}</b>
      <button type="button" class="chip" onclick=${() => setByGain(!byGain)}>
        ${byGain ? "Sort: biggest gain" : "Sort: route order"}</button>
    </div>
    <table class="rank-table"><tbody>
      ${rows.map((entity) => html`<tr class=${[
          entity.score == null ? "unpracticed" : "",
          entity.excluded ? "is-excluded" : ""].filter(Boolean).join(" ")}>
        <td class="rank-cell-name">${entity.label}</td>
        <td>${entity.tier
          ? html`<${Crest} tier=${entity.tier} division=${entity.division} size=${22} />`
          : "–"}</td>
        <td class="meta">${fmtScore(entity.score)}</td>
        <td class="meta rank-cell-gain">+${entity.gain.toFixed(2)}</td>
        <td><button type="button" class="chip"
          onclick=${() => onToggle(entity.key, !entity.excluded)}
          title=${entity.excluded
            ? "Include this in every rating again"
            : "Exclude this from every rating"}>
          ${entity.excluded ? "Include" : "Ignore"}</button></td>
      </tr>`)}
    </tbody></table>
  </div>`;
}

export function RankPage({ t }) {
  const [scopes, setScopes] = useState(null);
  const [scopesErr, setScopesErr] = useState(null);
  const [scopeId, setScopeId] = useState(null);
  const [data, setData] = useState(null);
  const [dataErr, setDataErr] = useState(null);
  const [points, setPoints] = useState([]);

  useEffect(() => {
    let alive = true;
    getJSON("/api/marelo/scopes").then((response) => {
      if (!alive) return;
      setScopes(response.scopes);
      // The focus route IS the scope (spec 3.4): follow it until the user
      // deliberately browses elsewhere via the picker.
      setScopeId((current) => current ?? response.active);
    }).catch((error) => alive && setScopesErr(error));
    return () => { alive = false; };
  }, []);

  // t.mareloRev is a dependency, not just scopeId: it's bumped by store.js
  // on every attempt_completed / marelo_changed / rank_mode_changed /
  // route_selected (and more — see store.js REFRESH_ON). Without it this
  // tab only ever fetched on mount and on scope change, so it went stale
  // while open during play (spec 2026-07-24 Step 2b) — the rating, chart
  // and breakdown kept showing pre-run numbers with nothing to indicate
  // they were old.
  useEffect(() => {
    if (!scopeId) return undefined;
    let alive = true;
    // Clear the old scope's state up front: a 404 on the NEW scope must never
    // leave the OLD scope's card/chart/breakdown on screen under the new
    // scope's label — that is exactly the "silently becomes a different
    // rating" failure the deliberate 404 exists to prevent.
    setDataErr(null);
    setData(null);
    setPoints([]);
    const query = `?scope=${encodeURIComponent(scopeId)}`;
    getJSON(`/api/marelo${query}`).then((response) => alive && setData(response))
      .catch((error) => alive && setDataErr(error));
    getJSON(`/api/marelo/history${query}`).then((response) => alive && setPoints(response.points))
      .catch(() => alive && setPoints([]));
    return () => { alive = false; };
  }, [scopeId, t.mareloRev]);

  async function toggleExcluded(entityKey, excluded) {
    try {
      await send("POST", "/api/marelo/exclude", { entity: entityKey, excluded });
      setData(await getJSON(`/api/marelo?scope=${encodeURIComponent(scopeId)}`));
    } catch (error) { setDataErr(error); }
  }

  if (!scopes) return html`<${PageState} kind=${t.connected ? "loading" : "offline"}
      title="Loading ranks" message=${scopesErr ? scopesErr.message : undefined} />`;
  if (!scopeId) return html`<${PageState} kind=${t.connected ? "loading" : "offline"}
      title="Loading ranks" />`;

  const routeOrder = scopeId.startsWith("route:");
  const coveragePct = data ? Math.round((data.coverage || 0) * 100) : 0;

  return html`<div class="rank-page">
    <div class="practice-card rank-card">
      <label class="route-focus-control">
        <${Icon} name="rank" size=${18} />
        <span class="field-label">Scope</span>
        <select value=${scopeId} onchange=${(event) => setScopeId(event.target.value)}>
          ${scopes.map((scope) => html`<option value=${scope.id}>${scope.label}</option>`)}
        </select>
      </label>
      ${dataErr
        ? html`<${InlineState} kind="error">${dataErr.status === 404
            ? "This scope is gone — pick another from the list above."
            : dataErr.message}<//>`
        : !data
          ? html`<${InlineState}>Loading this scope…<//>`
          : html`<div class="rank-card-main">
              <${Crest} tier=${data.tier} division=${data.division} size=${64} />
              <div>
                <h2>${data.tier ? `${data.tier} ${data.division}` : "Unranked"}</h2>
                <p class="meta">MARELO ${fmtScore(data.marelo)} · next division at ${fmtScore(data.next_division_at)}</p>
              </div>
            </div>
            <div class="rank-factors">
              <label>Mastery <i style=${`width:${data.mastery || 0}%`}></i>
                <span class="meta">${fmtScore(data.mastery)} over ${data.practiced} practiced</span></label>
              <label>Coverage <i style=${`width:${coveragePct}%`}></i>
                <span class="meta">${data.practiced}/${data.n}</span></label>
            </div>
            ${data.n < 5 && html`<p class="meta">Small scope — ${data.n} rated ${
              data.n === 1 ? "entry" : "entries"}.</p>`}`}
    </div>
    ${data && !dataErr && html`<div class="practice-card">
        <h3>Progress</h3>
        <${HistoryChart} points=${points} />
        <p class="meta">Recomputed from your attempts against current standards —
          editing this route or ignoring an entry rewrites the curve.</p>
      </div>
      <div class="practice-card">
        <${Breakdown} key=${scopeId} data=${data} routeOrder=${routeOrder}
          onToggle=${toggleExcluded} />
      </div>`}
  </div>`;
}
