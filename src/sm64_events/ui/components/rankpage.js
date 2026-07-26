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
import { capName, divisionDigit } from "./caps.js";
import { fmtPoints, fmtScore, toPoints } from "./marelo.js";
import { Hat } from "./hat.js";
import { Icon } from "./icons.js";
import { PageState, InlineState } from "./states.js";
import { useTween } from "../useTween.js";
import { courseStemForEntityKey, fallbackSlotForEntityKey,
         fallbackToGenericStar, isGenericArt, resolveIcon } from "./entityicons.js";

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

// Mirrors `ranks/scoring.py`'s DIVISION_NUMERALS length — each tier is cut
// into that many equal score-width slices. Same constant-table convention as
// ANCHORS above, and pinned against the Python side by
// tests/test_ui_rank_chart.py, because this is the one place the UI draws
// division geometry rather than reading it off the server.
const DIVISIONS_PER_TIER = 5;
// ...and its numerals, bottom of the tier first, mirroring the same file's
// DIVISION_NUMERALS. Used only to LABEL the ladder's division lines on
// hover; the division a SCORE lands in still comes from the server.
const DIVISION_NUMERALS = ["V", "IV", "III", "II", "I"];

// [{tier, from, to}] over the whole 0-100 score axis: Mario's band runs to
// the ceiling, every other tier's to the next anchor up.
const TIER_BANDS = TIERS_ASCENDING.map(([tier, floor], index) => ({
  tier, from: floor,
  to: index + 1 < TIERS_ASCENDING.length ? TIERS_ASCENDING[index + 1][1] : SCORE_CEILING,
}));
// Score IS percent on a 0-100 axis, so a band's edges are its stops verbatim.
const BAND_GRADIENT = "linear-gradient(90deg, "
  + TIER_BANDS.map((band) => `${rankColor(band.tier)} ${band.from}% ${band.to}%`).join(", ")
  + ")";

// Which tier BAND a 0-100 score sits in. Tier only, never division: an
// AGGREGATE has no ladder, so the server grades it against the full anchor
// table too (`scoring.division_for` with `defined=None`), which is exactly
// what ANCHORS mirrors — this agrees with it by construction. The division
// numeral, which genuinely needs a ladder, is still never computed here.
function bandForScore(score) {
  return [...TIER_BANDS].reverse().find((band) => score >= band.from)
    || TIER_BANDS[0];
}

// Every division line on the 0-100 axis: where it starts, and what reaching
// it makes you. The tier's own floor is its BOTTOM division (V), so a tier
// boundary is just the division line that happens to open a new tier.
const LADDER_STEPS = TIER_BANDS.flatMap((band) => {
  const width = (band.to - band.from) / DIVISIONS_PER_TIER;
  return DIVISION_NUMERALS.map((numeral, index) => ({
    at: band.from + width * index, tier: band.tier, division: numeral,
    opensTier: index === 0,
  }));
});

// The Mastery bar (round 7): a mean 0-100 score drawn on the ladder it is
// scored against, instead of a gold bar filling toward an unstated maximum.
// A line per division, a heavier one per tier, every stretch in its own
// tier's colour. The value is a SCORE, never a rating — the tier NAME under
// the YOU medal is the band it falls in (bandForScore, full-table like the
// server's own aggregate path), and the division numeral, which would need
// a ladder, is still never computed here.
function LadderBar({ value }) {
  const filled = Math.max(0, Math.min(SCORE_CEILING, value || 0));
  const here = bandForScore(filled);
  return html`<div>
    <!-- Which band is which, named by the same medal every other rank
         surface uses, centred over its own stretch of the track (live
         request 2026-07-25 round 8 — "so that it's very clear what each one
         is"). The CURRENT rank's medal is bigger, raised, tagged YOU, and
         sits over the value's ACTUAL position rather than its band's centre
         (round 9: "our active rank should be above the ACTUAL position in
         the bar — like that big white line"), so it travels along the track
         as divisions are climbed instead of jumping a whole tier at a time.
         Its band's small medal is dropped while it stands in for it — two
         medals of the same rank on one row would read as a duplicate. -->
    <div class="rank-ladder-scale">
      ${TIER_BANDS.filter((band) => band.tier !== here.tier).map((band) => html`<span
          class="rank-ladder-mark" style=${`left:${(band.from + band.to) / 2}%`}
          title=${`${capName(band.tier)} — ${toPoints(band.from)} to ${toPoints(band.to)} pts`}>
        <${Hat} tier=${band.tier} size=${13} />
      </span>`)}
      <span class="rank-ladder-mark is-you" style=${`left:${filled}%`}
          title=${`You are here — ${toPoints(filled)} pts`}>
        <b class="rank-ladder-you">YOU</b>
        <${Hat} tier=${here.tier} size=${22} />
      </span>
    </div>
    <div class="rank-ladder">
    <span class="rank-ladder-bands" style=${`background-image:${BAND_GRADIENT}`}></span>
    ${filled > 0 && html`<span class="rank-ladder-fill" style=${
      `width:${filled}%;background-image:${BAND_GRADIENT};`
      + `background-size:${(100 / filled) * 100}% 100%`}></span>`}
    <!-- Every line is hoverable and says what reaching it makes you (round
         9). The element is a 9px transparent box drawing its own 1-2px line,
         because a 1px pip is not a hit target. -->
    ${LADDER_STEPS.filter((step) => step.at > 0).map((step) => html`<span
      class="rank-ladder-step ${step.opensTier ? "is-tier" : ""}"
      style=${`left:${step.at}%`}
      title=${`${capName(step.tier)} ${divisionDigit(step.division)} — MARELO ${toPoints(step.at)} pts`}></span>`)}
    ${filled > 0 && html`<span class="rank-ladder-head" style=${`left:${filled}%`}></span>`}
    </div>
  </div>`;
}

// Wheel zoom (live request 2026-07-25 round 6). `zoom` is the FRACTION of the
// full time span in view, always anchored at the newest point — "zooms toward
// present day when scrolling in, and back out to all time when scrolling
// out". 1 = the entire journey, which is the default the user asked to keep.
const MIN_ZOOM = 0.02;
const ZOOM_SENSITIVITY = 0.0015;
// deltaY arrives in pixels (0), lines (1) or pages (2) depending on the
// browser and device; Firefox reports ±3 LINES where Chrome reports ±100
// pixels, so without this a Firefox wheel tick would move the zoom by 0.4%.
const DELTA_SCALE = { 0: 1, 1: 16, 2: 400 };

function wheelDelta(event) {
  return event.deltaY * (DELTA_SCALE[event.deltaMode] || 1);
}

// A point is a rank-up when its tier or division differs from the previous
// point's AND the rating went UP. MARELO can fall (a worse average, an
// entity excluded, a route edited), and a fall back through a boundary is
// not an event to celebrate — the same rule the server's celebration
// watermark applies (`scopes.celebration_delta` fires only on a RISE). The
// first point is never a rank-up either, for the same reason it is never a
// celebration: there was no earlier rank to leave.
function rankUpKind(point, previous) {
  if (!previous || !point.tier || !(point.marelo > previous.marelo)) return null;
  if (point.tier !== previous.tier) return "tier";
  if (point.division !== previous.division) return "division";
  return null;
}

// Hover text for a rank-up dot. Built outside the template because an SVG
// <title> is markup, not an attribute: string-concatenating around an
// html`` literal would splice a VNode into a string and render
// "[object Object]".
function markTitle(mark) {
  const when = new Date(mark.point.utc)
    .toLocaleDateString([], { month: "short", day: "numeric" });
  return `${mark.kind === "tier" ? "Tier up" : "Division up"}`
    + ` — ${capName(mark.point.tier)} ${divisionDigit(mark.point.division)} · ${when}`;
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
//
// Two axes, two different rules (live request 2026-07-25 round 6):
//
// X is the user's: the wheel zooms the time window, anchored at the newest
// point, and the default is the whole journey. Points are never filtered out
// of the path — the line is drawn in full and CLIPPED to the plot — so a
// window holding one point or none still shows the curve passing through it
// instead of collapsing to nothing.
//
// Y follows the zoom. Fully zoomed out it is the WHOLE ladder, Iron's floor
// to the 100 cap, so every tier still to climb is on screen ("we should
// actually show all of the higher tiers as well"). Zooming in eases it
// toward `autoZoomDomain` over the visible points, which is what makes an
// individual jump legible — the request's other half, and the reason the
// auto-zoom this replaces existed at all. The blend is linear in `zoom` so
// the axis dollies rather than snapping between two framings.
export function HistoryChart({ points }) {
  const [setChartEl, measuredWidth] = useMeasuredWidth(FALLBACK_CHART_WIDTH);
  const [zoom, setZoom] = useState(1);
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
  const fullSpan = maxTime - minTime;
  const viewStart = maxTime - fullSpan * zoom;
  const viewSpan = maxTime - viewStart;
  const xForTime = (time) => plotLeft
    + (viewSpan > 0 ? (time - viewStart) / viewSpan : 0.5) * plotWidth;

  const inView = points.filter((point, index) => times[index] >= viewStart);
  const [autoLow, autoHigh] = autoZoomDomain(
    (inView.length ? inView : points).map((point) => point.marelo));
  const domainLow = autoLow * (1 - zoom);
  const domainHigh = SCORE_CEILING + (autoHigh - SCORE_CEILING) * (1 - zoom);
  const domainSpan = Math.max(1e-6, domainHigh - domainLow);
  const yForScore = (score) => plotBottom
    - ((Math.max(domainLow, Math.min(domainHigh, score)) - domainLow) / domainSpan) * plotHeight;

  const line = points.map((point, index) =>
    `${index ? "L" : "M"}${xForTime(times[index]).toFixed(1)},${yForScore(point.marelo).toFixed(1)}`
  ).join(" ");

  // EVERY tier in the domain gets a line AND a label. A collision guard that
  // dropped the label when two lines sat within 11px was wrong twice over
  // (live report 2026-07-25, round 7 — "the graph is missing a tier for
  // grandmaster"): the crowding it guessed at isn't real — Grandmaster 90
  // and Mario 95 are ~9px apart on a 180px plot, and a 9px font's cap height
  // is ~6.5px, so the labels clear each other — and a ladder that silently
  // omits a rank is precisely what this chart is for.
  const tierLines = TIERS_ASCENDING
    .filter(([, floor]) => floor >= domainLow && floor <= domainHigh)
    .map(([tier, floor]) => ({ tier, y: yForScore(floor) }));

  const marks = points.map((point, index) => ({
    point, kind: rankUpKind(point, points[index - 1]),
    x: xForTime(times[index]), y: yForScore(point.marelo),
  })).filter((mark) => mark.kind);

  const ticks = timeTicks(viewStart, maxTime, plotWidth);

  function onWheel(event) {
    const delta = wheelDelta(event);
    // At either limit the wheel belongs to the page again — a chart that
    // swallowed the scroll after it stopped zooming would trap the tab.
    if ((zoom >= 1 && delta > 0) || (zoom <= MIN_ZOOM && delta < 0)) return;
    event.preventDefault();
    // Functional update, not `zoom * …`: a fast scroll delivers several
    // wheel events inside ONE frame, and every handler in that burst would
    // read the same rendered `zoom` — measured in the harness as twelve
    // events moving the window exactly as far as one.
    setZoom((current) => Math.min(1, Math.max(MIN_ZOOM,
      current * Math.exp(delta * ZOOM_SENSITIVITY))));
  }

  return html`<div class="rank-chart-block">
    <svg class="rank-chart" ref=${setChartEl} onwheel=${onWheel}
        viewBox=${`0 0 ${measuredWidth} ${CHART_HEIGHT}`}
        role="img" aria-label="MARELO over time">
      <defs><clipPath id="rank-chart-clip">
        <rect x=${plotLeft} y=${plotTop - 4} width=${plotWidth} height=${plotHeight + 8} />
      </clipPath></defs>
      ${tierLines.map(({ tier, y }) => html`<g>
        <line x1=${plotLeft} x2=${plotRight} y1=${y} y2=${y}
          stroke=${rankColor(tier)} stroke-opacity=".28" stroke-dasharray="3 4" />
        <text x=${plotLeft - 8} y=${y - 3} text-anchor="end"
          fill=${rankColor(tier)} font-size="9">${capName(tier)}</text></g>`)}
      ${ticks.map((tick, tickIndex) => html`<text
          x=${xForTime(tick.time)} y=${CHART_HEIGHT - 8}
          text-anchor=${ticks.length === 1 ? "middle"
            : tickIndex === 0 ? "start" : tickIndex === ticks.length - 1 ? "end" : "middle"}
          fill="var(--muted)" font-size="9">${fmtTick(new Date(tick.time), viewSpan)}</text>`)}
      <g clip-path="url(#rank-chart-clip)">
        <path d=${line} fill="none" stroke="var(--gold)" stroke-width="2" />
        ${marks.map((mark) => html`<circle cx=${mark.x} cy=${mark.y}
            r=${mark.kind === "tier" ? 6 : 3.5} fill=${rankColor(mark.point.tier)}
            stroke="rgba(6,16,28,.85)" stroke-width=${mark.kind === "tier" ? 2 : 1.5}>
          <title>${markTitle(mark)}</title>
        </circle>`)}
      </g>
    </svg>
    <p class="meta rank-chart-span">
      ${fmtSpanCaption(zoom < 1 ? viewStart : minTime, maxTime)}
      ${zoom < 1
        ? html` · <button type="button" class="linkish"
            onclick=${() => setZoom(1)}>show all time</button>`
        : html` · <span class="rank-chart-hint">scroll to zoom</span>`}
    </p>
  </div>`;
}

// The breakdown's "next rank" column (task C.3, trimmed round 2): the
// TARGET only -- "→ Platinum IV", "→ Gold", or "Maxed" -- never the
// current tier/division too, which the Hat icon two columns over already
// shows; one format for every row instead of a practiced-vs-unpracticed
// split. Sourced from `entity.next_tier`/`entity.next_division`, which the
// server computes via `scoring.division_progress` against the entity's OWN
// ladder (server/ranks_api.py::_score_scope). Never re-derived here: a
// second copy of the division math in JS is exactly the kind of drift that
// would make this column disagree with the Hat icon.
function nextRankLabel(entity) {
  // An entity you have never practiced has no rank to step up FROM, and the
  // server's `next_tier` for it is the Gold QUEST TARGET the Gain column is
  // measured against — printing that here read as a claim about its next
  // rank ("it also doesn't make sense for next rank to be gold, when there
  // aren't even rank standards" — live report 2026-07-25 round 7). Say what
  // is true instead; the Gold target moves to the Gain cell's tooltip, next
  // to the number it actually explains.
  if (entity.score == null) return "not practiced yet";
  if (!entity.next_tier) return "Maxed";
  return entity.next_division
    ? `→ ${capName(entity.next_tier)} ${divisionDigit(entity.next_division)}`
    : `→ ${capName(entity.next_tier)}`;
}

function gainTitle(entity) {
  if (entity.score == null)
    return `What this scope's rating would gain if you practised this to `
      + `${capName(entity.next_tier)} — that target is why an unpractised entry shows `
      + `the biggest number in this column.`;
  return `What this scope's rating would gain if you reached the next tier here.`;
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
          ? html`<${Hat} tier=${entity.tier} division=${entity.division} size=${30} />`
          : "–"}</td>
        <td class="meta rank-cell-points">${fmtPoints(entity.score)}</td>
        <td class="meta rank-cell-next">${nextRankLabel(entity)}</td>
        <td class="meta rank-cell-gain" title=${gainTitle(entity)}>+${toPoints(entity.gain)}</td>
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

// The scope chip row (task D.1): every season's tier at a glance, op.gg
// style, so comparing a 16 Star rank against a 120 Star rank no longer costs
// a dropdown round-trip each way. Fetches `/api/marelo/summary` -- which
// calls `_score_scope`, never `_build_marelo` (server/ranks_api.py), so
// reading this row can never seed, sync or lower a celebration watermark
// for a scope the user has not actually opened. `refreshKey` mirrors the
// tab's own staleness fix (t.mareloRev): the chips must not go stale while
// the tab is left open during play, same reason the breakdown re-fetches
// on every REFRESH_ON event.
function ScopeChips({ activeScopeId, onPick, refreshKey }) {
  const [chips, setChips] = useState(null);
  useEffect(() => {
    let alive = true;
    getJSON("/api/marelo/summary").then((response) => alive && setChips(response.chips))
      .catch(() => alive && setChips([]));
    return () => { alive = false; };
  }, [refreshKey]);
  if (!chips || !chips.length) return null;
  return html`<div class="scope-chip-row">
    ${chips.map((chip) => html`<button type="button" key=${chip.scope_id}
        class="scope-chip ${chip.scope_id === activeScopeId ? "is-selected" : ""}"
        onclick=${() => onPick(chip.scope_id)}>
      <${Hat} tier=${chip.tier} division=${chip.division} size=${30} />
      <span class="scope-chip-text">
        <b>${chip.label}</b>
        <span class="meta">${fmtPoints(chip.marelo)} pts</span>
      </span>
    </button>`)}
  </div>`;
}

// One glanceable icon tile for the Top-N strip below -- tier-tinted border,
// the SAME icon resolution the practice selector's cells use (entityicons.js,
// extracted from stagebanner.js for exactly this reuse) so a star's Top-N
// tile can never show different art than its own selector cell does. A
// segment key resolves no course stem at this layer (the breakdown payload
// carries no start_levels) and falls back to the generic star, same as
// stagebanner's own segment cells do for an unmapped level.
// ONE tile for both icon rows on this tab — Best in scope and Coverage.
// They differ only in size and in whether the entity is scored: an
// unpracticed one keeps its art but loses the tier ring and dims, which is
// what makes a coverage row readable as "these, not those" at a glance
// (round 8). Never two components that happen to look alike — the split
// rank banners were exactly that mistake one card over.
function EntityTile({ t, entity, size = 42, open, onToggle }) {
  const fallbackSlot = fallbackSlotForEntityKey(entity.key);
  const iconSrc = resolveIcon(t, entity.key,
    courseStemForEntityKey(entity.key), fallbackSlot);
  const practiced = entity.score != null;
  return html`<button type="button"
      class="entity-tile ${open ? "is-open" : ""} ${practiced ? "" : "is-unpracticed"}"
      style=${`--tile-size:${size}px;`
        + (practiced ? `--tier-tint:${rankColor(entity.tier)}` : "")}
      onclick=${onToggle} aria-expanded=${open ? "true" : "false"}
      title=${practiced
        ? `${entity.label} — ${capName(entity.tier)} ${divisionDigit(entity.division)} · ${fmtPoints(entity.score)} pts`
        : `${entity.label} — not practiced yet`}>
    <img class="entity-tile-icon ${isGenericArt(iconSrc) ? "" : "courseicon"}" src=${iconSrc}
         onerror=${(event) => fallbackToGenericStar(event, fallbackSlot)}
         alt="" draggable="false" />
  </button>`;
}

// The practice section for a rankable entity key, out of the view the rest
// of the app already holds (`t.view.stars` / `t.view.segments`, built by
// tracking/views.py). No new endpoint and no second copy of the data: the
// Rank tab shows exactly what the practice card would, including the view's
// own session/lifetime scope — which is why a Top-N entity practiced in an
// older session can legitimately have no section here.
function sectionForKey(view, entityKey) {
  if (!view) return null;
  const parts = entityKey.split(":");
  if (parts[0] === "segment")
    return (view.segments || []).find(
      (section) => String(section.segment_id) === parts[1]) || null;
  return (view.stars || []).find(
    (section) => String(section.course_id) === parts[1]
      && String(section.star_id) === parts[2]) || null;
}

async function practiceEntity(entityKey, t) {
  const parts = entityKey.split(":");
  await send("POST", "/api/target", parts[0] === "segment"
    ? { kind: "segment", segment_id: Number(parts[1]) }
    : { course_id: Number(parts[1]), star_id: Number(parts[2]) });
  t.refresh();
}

const TOP_N_ATTEMPTS = 10;

// The expanded tile (live request 2026-07-25 round 7): "I could click on it
// and get an overview of my history for that star, and would be easily able
// to see my PB". PB, rank and recent attempts, read straight off the section
// the store already has — deliberately a SUMMARY, not a second practice
// card: the timeline, trend graph, replays and per-attempt tools stay one
// click away behind "Practice this", which is the surface built for them.
function EntityDetail({ t, entity, onClose }) {
  const section = sectionForKey(t.view, entity.key);
  // Segments are RTA-only whatever the view clock is — the same rule
  // views.py applies when it builds their sections.
  const clock = section && section.kind === "segment" ? "rta" : t.clock;
  const pb = section && section.pb && section.pb[clock];
  const rows = section
    ? [...section.attempts]
        .filter((attempt) => !attempt.cleared && attempt.outcome === "success")
        .sort((left, right) => right.id - left.id)
        .slice(0, TOP_N_ATTEMPTS)
    : [];
  return html`<div class="entity-detail">
    <div class="entity-detail-head">
      <${Hat} tier=${entity.tier} division=${entity.division} size=${30} />
      <h4>${entity.label}</h4>
      <span class="meta">${fmtPoints(entity.score)} pts${
        pb ? ` · PB ${pb.display} (${clock})` : " · no saved PB"}</span>
      <span class="entity-detail-spacer"></span>
      <button type="button" class="chip"
        onclick=${() => practiceEntity(entity.key, t)}>Practice this</button>
      <button type="button" class="chip" onclick=${onClose}>Close</button>
    </div>
    ${!section
      ? html`<p class="meta">No attempts in the current view scope — switch the
          header's scope to Lifetime, or open it with "Practice this".</p>`
      : !rows.length
        ? html`<p class="meta">No successful attempts in this scope yet.</p>`
        : html`<div class="entity-attempts"><table><tbody>
          ${rows.map((attempt) => html`<tr key=${attempt.id}
              class=${attempt.is_current_pb ? "is-pb" : ""}>
            <td>${attempt.rank ? html`<${Hat} tier=${attempt.rank} size=${14} />` : ""}</td>
            <td><b>${attempt[clock] || "—"}</b></td>
            <td class="entity-attempt-strat">${attempt.strat_tag || "—"}</td>
            <td>${attempt.is_current_pb ? "PB" : ""}</td>
          </tr>`)}
        </tbody></table></div>`}
  </div>`;
}

// The "Best in scope" card that used to live here (task D.2, an op.gg-style
// top-12 row) was REMOVED in round 9, 2026-07-25: once Coverage became every
// entity as a tile — tier-ringed when practiced, dim when not — a second row
// of the same tiles showing a subset of the same entities was, in the user's
// words, "redundant". "What am I best at" is still answerable, by the
// breakdown's Sort: biggest gain, on the same page.

// Same size as the removed Best-in-scope tiles: this strip is now the only
// place the art appears on this tab, and 30px was sized for a card that had
// a bigger sibling above it.
const COVERAGE_TILE_PX = 42;

// Coverage as the THING it counts, not a bar (live request 2026-07-25 round
// 8): every rated entry in the scope, in the scope's own order — route order
// for a route — lit with its tier ring when practiced and dimmed when not.
// "The goal is to really really clearly showcase 'this is your coverage,
// because you can see which stars you've practiced and which stars you
// haven't' rather than it being a bar."
//
// Excluded rows are filtered OUT, which is what keeps the strip honest: the
// server's `n` is the non-excluded count, so tile count == n and lit count
// == practiced, and the caption below can never disagree with the picture
// above it. Tiles open the SAME detail panel Best-in-scope uses — on an
// unpracticed entry that panel is just "Practice this", which is the
// obvious next move from a dim tile.
function CoverageStrip({ t, data, caption }) {
  const [openKey, setOpenKey] = useState(null);
  const rated = data.entities.filter((entity) => !entity.excluded);
  const open = rated.find((entity) => entity.key === openKey) || null;
  // The caption is a PROP, not the sibling it used to be: an expanded
  // detail panel rendered inside the label would otherwise sit between the
  // tiles and the sentence explaining them.
  return html`<div class="rank-coverage">
    <div class="entity-strip rank-coverage-strip">
      ${rated.map((entity) => html`<${EntityTile} key=${entity.key} t=${t}
        entity=${entity} size=${COVERAGE_TILE_PX} open=${entity.key === openKey}
        onToggle=${() => setOpenKey(entity.key === openKey ? null : entity.key)} />`)}
    </div>
    <span class="meta">${caption}</span>
    ${open && html`<${EntityDetail} t=${t} entity=${open}
      onClose=${() => setOpenKey(null)} />`}
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

  // The card's tweened surfaces (spec task F2): the rating itself and the
  // Mastery ladder, each animated together with the number printed beside
  // it, so a bar filling next to a number that already jumped to its new
  // value can't read as two components disagreeing. Coverage lost its tween
  // with its bar in round 8 — it is now a row of entity tiles, where the
  // change is a tile lighting up rather than a length growing.
  // Called unconditionally (rules of hooks) even while `data` is still
  // loading or a scope switch has cleared it -- `null` passes straight
  // through with no animation.
  const tweenedMarelo = useTween(data ? data.marelo : null);
  const tweenedMastery = useTween(data ? data.mastery : null);

  if (!scopes) return html`<${PageState} kind=${t.connected ? "loading" : "offline"}
      title="Loading ranks" message=${scopesErr ? scopesErr.message : undefined} />`;
  if (!scopeId) return html`<${PageState} kind=${t.connected ? "loading" : "offline"}
      title="Loading ranks" />`;

  const routeOrder = scopeId.startsWith("route:");

  return html`<div class="rank-page">
    <${ScopeChips} activeScopeId=${scopeId} onPick=${setScopeId} refreshKey=${t.mareloRev} />
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
              <!-- Unranked is an EXPLICIT empty state (final review I5,
                   2026-07-25), matching PracticeCell's starrank "–" rather
                   than calling Hat with no tier and drawing a plain grey
                   cap. -->
              ${data.tier ? html`<${Hat} tier=${data.tier} division=${data.division} size=${64} />` : "–"}
              <div>
                <h2>${data.tier ? `${capName(data.tier)} ${divisionDigit(data.division)}` : "Unranked"}</h2>
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
            <!-- rank-factor is a DIV, not the label it started as: a label
                 forwards hover and click to its first labelable descendant,
                 and once these rows grew buttons (the coverage tiles) that
                 meant hovering anywhere in the row — the caption, the empty
                 space beside it — lit up the first tile, and clicking it
                 would have opened that entity (live report 2026-07-25 round
                 9). Nothing here labels a single control any more, so
                 nothing should behave as though it does.
                 (No backticks in a comment inside an htm template: the first
                 one ENDS the template literal and the rest of the comment is
                 parsed as JS. This exact comment did that, twice.) -->
            <div class="rank-factors">
              <!-- Mastery stays 0-100 (task C.4): it's a MEAN SCORE, not a
                   rating on the tier ladder (marelo = mastery x coverage),
                   so running it through toPoints would imply a fourth scale
                   that doesn't exist. fmtScore on purpose, not fmtPoints. -->
              <div class="rank-factor">Mastery <${LadderBar} value=${tweenedMastery} />
                <span class="meta">${`${fmtScore(tweenedMastery)} / 100 — the average `
                  + `rank score of the ${data.practiced} `
                  + `${data.practiced === 1 ? "entry" : "entries"} you have practiced, `
                  + "on the same ladder the chart below draws"}</span></div>
              <div class="rank-factor">Coverage <${CoverageStrip} t=${t} data=${data}
                caption=${`${data.practiced} of ${data.n} rated `
                  + `${data.n === 1 ? "entry" : "entries"} practiced — dim tiles are `
                  + "the ones you have not run yet. MARELO is mastery × coverage, so "
                  + "an unpractised entry costs rating even when the ones you do "
                  + "practise are strong"} /></div>
            </div>
            ${data.n < 5 && html`<p class="meta">Small scope — ${data.n} rated ${
              data.n === 1 ? "entry" : "entries"}.</p>`}`}
    </div>
    ${data && !dataErr && html`
      <div class="practice-card">
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
