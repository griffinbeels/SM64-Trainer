// src/sm64_events/ui/components/rankpage.js — the Rank tab: scope picker,
// rank card, history chart, and the per-entity breakdown (which IS the route
// performance view when the scope is a route). Route performance, per-course
// averages and overall progress are the same view under different scopes, so
// there is one picker/card/chart/breakdown, not three near-duplicate pages.
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { rankColor } from "./ranks.js";
import { Crest, fmtPoints, fmtScore, toPoints } from "./marelo.js";
import { Icon } from "./icons.js";
import { PageState, InlineState } from "./states.js";
import { useTween } from "../useTween.js";

const html = htm.bind(h);

// Mirrors ranks/scoring.SCORE_ANCHORS (constant-table mirror, not an
// algorithm — same convention as ranks.js/format.js). Iron carries no
// anchor server-side either; 0 is its implicit floor for the band line.
const ANCHORS = { Mario: 95, Grandmaster: 90, Master: 80, Diamond: 70,
  Platinum: 60, Gold: 45, Silver: 25, Bronze: 10, Iron: 0 };
// The same table, read low-to-high — feeds both the gridlines and the Y-axis
// auto-zoom below, so the two can never disagree on where a tier starts.
const TIERS_ASCENDING = Object.entries(ANCHORS).sort(([, scoreA], [, scoreB]) => scoreA - scoreB);
const TIER_FLOORS = TIERS_ASCENDING.map(([, floor]) => floor);
const SCORE_CEILING = 100; // Mario (the top tier) has no anchor above it.

const CHART_HEIGHT = 220;
// Left margin the tier labels live in. They used to sit at x=4 INSIDE the
// plot, which read fine only when the viewBox's declared aspect (3.3:1)
// happened to match the rendered box — at a wide window CSS gave the <svg>
// a ~7.7:1 box, so the default `xMidYMid meet` shrank-and-centred the whole
// chart, dragging those labels into the middle of the card on top of the
// lines they name. A fixed gutter keeps them clear of the plot at any width.
const CHART_GUTTER = 64;
const CHART_PAD_RIGHT = 12;
const CHART_PAD_TOP = 14;
const CHART_PAD_BOTTOM = 26; // room for the dated x-axis ticks
// Used for the one frame before the ResizeObserver below reports a real
// measurement.
const FALLBACK_CHART_WIDTH = 720;

// Every tier boundary, Iron's floor through the 100 cap above Mario — the
// full set of lines autoZoomDomain can widen toward.
const TIER_BOUNDARIES = [...TIER_FLOORS, SCORE_CEILING];

// A first pass at auto-zoom snapped the domain out to the tier boundary
// BELOW low and the one ABOVE high — correct by that rule, but the rule
// itself gave three tiers of headroom to six points of real climb (the
// live report this chart exists for: MARELO 9.6, an Iron-to-Bronze move,
// still read as flat). Padding by a fraction of the data's own range keeps
// the zoom proportional to how much the data actually moved instead.
const Y_PAD_FRACTION = 0.15;
// Floor in score points, not a fraction of the range: a fraction alone
// hits zero exactly when it matters most — every point at the same score,
// where 0.15 * 0 is 0 and an unpadded domain divides by zero below.
const Y_PAD_FLOOR = 2;

function nearestBoundaryAtOrBelow(value) {
  return [...TIER_BOUNDARIES].reverse().find((boundary) => boundary <= value);
}
function nearestBoundaryAtOrAbove(value) {
  return TIER_BOUNDARIES.find((boundary) => boundary >= value);
}

// Auto-zoom the Y axis to the data's own [low, high] range, padded by
// Y_PAD_FRACTION of that range (floored at Y_PAD_FLOOR), clamped to Iron's
// floor and the 100 cap. If the padded window ends up entirely inside one
// tier band — no boundary crossing left to show — widen it just enough to
// reach the nearer boundary, so the chart never loses its frame of
// reference even when the whole climb happened inside a single tier.
function autoZoomDomain(scores) {
  const low = Math.min(...scores), high = Math.max(...scores);
  const pad = Math.max((high - low) * Y_PAD_FRACTION, Y_PAD_FLOOR);
  let domainLow = Math.max(0, low - pad);
  let domainHigh = Math.min(100, high + pad);

  const hasBoundaryInView = TIER_BOUNDARIES.some(
    (boundary) => boundary >= domainLow && boundary <= domainHigh);
  if (!hasBoundaryInView) {
    const boundaryBelow = nearestBoundaryAtOrBelow(domainLow);
    const boundaryAbove = nearestBoundaryAtOrAbove(domainHigh);
    if (domainLow - boundaryBelow <= boundaryAbove - domainHigh) domainLow = boundaryBelow;
    else domainHigh = boundaryAbove;
  }
  return [domainLow, domainHigh];
}

const DAY_MS = 86400000;

// Tick label granularity follows the series' own span: a fixed format either
// repeats the same date across a one-day range or drops the year on a
// one-year range.
function fmtTick(date, spanMs) {
  if (spanMs <= 0)
    return date.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  if (spanMs < DAY_MS) return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (spanMs < 120 * DAY_MS) return date.toLocaleDateString([], { month: "short", day: "numeric" });
  if (spanMs < 730 * DAY_MS) return date.toLocaleDateString([], { month: "short", year: "2-digit" });
  return date.toLocaleDateString([], { year: "numeric" });
}

function humanSpan(spanMs) {
  const days = spanMs / DAY_MS;
  if (days < 1) return "today";
  if (days < 14) { const count = Math.max(1, Math.round(days)); return `${count} day${count === 1 ? "" : "s"}`; }
  if (days < 60) { const count = Math.max(1, Math.round(days / 7)); return `${count} week${count === 1 ? "" : "s"}`; }
  if (days < 730) { const count = Math.max(1, Math.round(days / 30)); return `${count} month${count === 1 ? "" : "s"}`; }
  const count = Math.max(1, Math.round(days / 365)); return `${count} year${count === 1 ? "" : "s"}`;
}

// "18 Jun – 25 Jul · 5 weeks" — the span the chart covers, stated once
// rather than left for the viewer to infer from the tick labels.
function fmtSpanCaption(minTime, maxTime) {
  const dateOpts = { month: "short", day: "numeric" };
  if (maxTime <= minTime)
    return new Date(minTime).toLocaleDateString([], { ...dateOpts, year: "numeric" });
  const startLabel = new Date(minTime).toLocaleDateString([], dateOpts);
  const endLabel = new Date(maxTime).toLocaleDateString([], dateOpts);
  return `${startLabel} – ${endLabel} · ${humanSpan(maxTime - minTime)}`;
}

// Evenly spaced by TIME, not by point index (irregular data would misplace
// index-based ticks), and sized to the plot's own width so a narrow render
// doesn't crowd its own labels. A zero-length span (one point in time, or
// every point sharing a timestamp) still gets one centered tick instead of
// a divide-by-zero.
function timeTicks(minTime, maxTime, plotWidth) {
  if (!(maxTime > minTime)) return [{ time: minTime, fraction: 0.5 }];
  const tickCount = Math.max(2, Math.min(6, Math.round(plotWidth / 110)));
  return Array.from({ length: tickCount }, (_, tickIndex) => {
    const fraction = tickIndex / (tickCount - 1);
    return { time: minTime + fraction * (maxTime - minTime), fraction };
  });
}

// Callback ref held in state, not useRef: the chart doesn't exist on the
// first render whenever `points` is still too short (the bail-out below), so
// a ref effect keyed on `[]` would read null once and never re-run when the
// real <svg> mounts later. Same fix as viewport.js's usePaneCap, applied to
// width instead of height.
function useMeasuredWidth(fallback) {
  const [element, setElement] = useState(null);
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    if (!element || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver((entries) => {
      const measuredWidth = entries[0] && entries[0].contentRect.width;
      if (measuredWidth) setWidth(Math.round(measuredWidth));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [element]);
  return [setElement, width];
}

// Measures its own container and draws the viewBox at the SAME width, so the
// chart always renders 1:1 — no `preserveAspectRatio="none"`, which would
// stop the letterboxing but stretch the tier/tick text horizontally instead.
export function HistoryChart({ points }) {
  const [setChartEl, measuredWidth] = useMeasuredWidth(FALLBACK_CHART_WIDTH);
  if (!points || points.length < 2)
    return html`<p class="meta">Not enough history yet — finish a few more runs.</p>`;

  const plotLeft = CHART_GUTTER;
  const plotRight = measuredWidth - CHART_PAD_RIGHT;
  const plotWidth = Math.max(1, plotRight - plotLeft);
  const plotTop = CHART_PAD_TOP;
  const plotBottom = CHART_HEIGHT - CHART_PAD_BOTTOM;
  const plotHeight = plotBottom - plotTop;

  const times = points.map((point) => Date.parse(point.utc));
  const minTime = Math.min(...times), maxTime = Math.max(...times);
  const timeSpan = maxTime - minTime;
  const xForTime = (time) => plotLeft
    + (timeSpan > 0 ? (time - minTime) / timeSpan : 0.5) * plotWidth;

  const [domainLow, domainHigh] = autoZoomDomain(points.map((point) => point.marelo));
  const domainSpan = domainHigh - domainLow;
  const yForScore = (score) => plotBottom
    - ((Math.max(domainLow, Math.min(domainHigh, score)) - domainLow) / domainSpan) * plotHeight;

  const line = points.map((point, index) =>
    `${index ? "L" : "M"}${xForTime(Date.parse(point.utc)).toFixed(1)},${yForScore(point.marelo).toFixed(1)}`
  ).join(" ");

  const visibleTiers = TIERS_ASCENDING.filter(([, floor]) => floor >= domainLow && floor <= domainHigh);
  const ticks = timeTicks(minTime, maxTime, plotWidth);

  return html`<div class="rank-chart-block">
    <svg class="rank-chart" ref=${setChartEl}
        viewBox=${`0 0 ${measuredWidth} ${CHART_HEIGHT}`}
        role="img" aria-label="MARELO over time">
      ${visibleTiers.map(([tier, floor]) => html`<g>
        <line x1=${plotLeft} x2=${plotRight} y1=${yForScore(floor)} y2=${yForScore(floor)}
          stroke=${rankColor(tier)} stroke-opacity=".28" stroke-dasharray="3 4" />
        <text x=${plotLeft - 8} y=${yForScore(floor) - 3} text-anchor="end"
          fill=${rankColor(tier)} font-size="9">${tier}</text></g>`)}
      ${ticks.map((tick, tickIndex) => html`<text
          x=${xForTime(tick.time)} y=${CHART_HEIGHT - 8}
          text-anchor=${ticks.length === 1 ? "middle"
            : tickIndex === 0 ? "start" : tickIndex === ticks.length - 1 ? "end" : "middle"}
          fill="var(--muted)" font-size="9">${fmtTick(new Date(tick.time), timeSpan)}</text>`)}
      <path d=${line} fill="none" stroke="var(--gold)" stroke-width="2" />
    </svg>
    <p class="meta rank-chart-span">${fmtSpanCaption(minTime, maxTime)}</p>
  </div>`;
}

// The breakdown's "next rank" column (task C.3, trimmed round 2): the
// TARGET only -- "→ Platinum IV", "→ Gold", or "Maxed" -- never the
// current tier/division too, which the Crest two columns over already
// shows; one format for every row instead of a practiced-vs-unpracticed
// split. Sourced from `entity.next_tier`/`entity.next_division`, which the
// server computes via `scoring.division_progress` against the entity's OWN
// ladder (server/ranks_api.py::_score_scope). Never re-derived here: a
// second copy of the division math in JS is exactly the kind of drift that
// would make this column disagree with the Crest.
function nextRankLabel(entity) {
  if (!entity.next_tier) return "Maxed";
  return entity.next_division
    ? `→ ${entity.next_tier} ${entity.next_division}`
    : `→ ${entity.next_tier}`;
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
    <table class="rank-table">
      <thead><tr>
        <th>Entity</th><th>Rank</th>
        <th class="rank-cell-points">Score (pts)</th>
        <th>Next rank</th>
        <th class="rank-cell-gain">Gain (pts)</th>
        <th></th>
      </tr></thead>
      <tbody>
      ${rows.map((entity) => html`<tr class=${[
          entity.score == null ? "unpracticed" : "",
          entity.excluded ? "is-excluded" : ""].filter(Boolean).join(" ")}>
        <td class="rank-cell-name">${entity.label}</td>
        <td>${entity.tier
          ? html`<${Crest} tier=${entity.tier} division=${entity.division} size=${22} />`
          : "–"}</td>
        <td class="meta rank-cell-points">${fmtPoints(entity.score)}</td>
        <td class="meta rank-cell-next">${nextRankLabel(entity)}</td>
        <td class="meta rank-cell-gain">+${toPoints(entity.gain)}</td>
        <td><button type="button" class="chip"
          onclick=${() => onToggle(entity.key, !entity.excluded)}
          title=${entity.excluded
            ? "Include this in every rating again"
            : "Exclude this from every rating"}>
          ${entity.excluded ? "Include" : "Ignore"}</button></td>
      </tr>`)}
      </tbody>
    </table>
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

  // The card's three numeric surfaces (spec task F2): the rating itself,
  // and the Mastery/Coverage bars each tweened together with the number
  // printed beside them, so a bar filling next to a number that already
  // jumped to its new value can't read as two components disagreeing.
  // Called unconditionally (rules of hooks) even while `data` is still
  // loading or a scope switch has cleared it -- `null` passes straight
  // through with no animation.
  const tweenedMarelo = useTween(data ? data.marelo : null);
  const tweenedMastery = useTween(data ? data.mastery : null);
  const tweenedCoveragePct = useTween(data ? Math.round((data.coverage || 0) * 100) : null);

  if (!scopes) return html`<${PageState} kind=${t.connected ? "loading" : "offline"}
      title="Loading ranks" message=${scopesErr ? scopesErr.message : undefined} />`;
  if (!scopeId) return html`<${PageState} kind=${t.connected ? "loading" : "offline"}
      title="Loading ranks" />`;

  const routeOrder = scopeId.startsWith("route:");

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
                <p class="meta">MARELO ${fmtPoints(tweenedMarelo)} pts · next division at ${fmtPoints(data.next_division_at)}</p>
              </div>
            </div>
            <!-- Task C.5 (live report 2026-07-25): a user saw PLATINUM on a
                 strategy banner next to Iron I here and filed it as a bug --
                 it wasn't. MARELO auto-grades whatever the active rank
                 mode's basis is on EVERY view; the practice card's PB banner
                 only moves when you press its manual Save button. Naming
                 that difference here is what turns "these disagree" into
                 "these answer different questions" -- the no-friction MARELO
                 behavior was a deliberate choice, not a bug to quietly fix. -->
            <p class="meta rank-card-note">MARELO grades every attempt automatically —
              it doesn't wait for a saved PB the way the practice card's rank banner does.</p>
            <div class="rank-factors">
              <!-- Mastery stays 0-100 (task C.4): it's a MEAN SCORE, not a
                   rating on the tier ladder (marelo = mastery x coverage),
                   so running it through toPoints would imply a fourth scale
                   that doesn't exist. fmtScore on purpose, not fmtPoints. -->
              <label>Mastery <i style=${`width:${tweenedMastery || 0}%`}></i>
                <span class="meta">${fmtScore(tweenedMastery)} over ${data.practiced} practiced</span></label>
              <label>Coverage <i style=${`width:${tweenedCoveragePct || 0}%`}></i>
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
