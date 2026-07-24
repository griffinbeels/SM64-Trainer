// src/sm64_events/ui/components/runview.js — full-game run timer (Run tab).
// Renders GET /api/run (via the store's t.run, refetched on run_* WS events);
// the big clock + current-step time TICK client-side off the authoritative
// started_utc + start_offset_ms. Pause/Resume/Reset buttons call
// POST /api/run/pause|resume|reset; clock freezes at paused_at when paused,
// and subtracts accumulated paused_ms so paused time is excluded.
// Always-on: idle shows preview steps + 0:00+offset; active ticks; finished
// freezes on the last split until the next run begins. No Start button —
// selecting a route arms it (pickRoute calls POST /api/run/start).
// Focus mode (neutral, no ±/gold) and click-to-hide any timer are pure UI
// state in localStorage.
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { Icon } from "./icons.js";
import { PageState } from "./states.js";

const html = htm.bind(h);

function fmtMs(ms) {
  if (ms == null) return "—";
  const sign = ms < 0 ? "-" : "";
  ms = Math.abs(Math.round(ms));
  const m = Math.floor(ms / 60000);
  const s = Math.floor((ms % 60000) / 1000);
  const cs = Math.floor((ms % 1000) / 10);
  return `${sign}${m}:${String(s).padStart(2, "0")}.${String(cs).padStart(2, "00")}`;
}

// Persisted set of hidden timer keys (click-to-hide).
function loadHidden() {
  try { return new Set(JSON.parse(localStorage.getItem("sm64.runHidden") || "[]")); }
  catch { return new Set(); }
}

function fmtDate(utc) {
  try { return new Date(utc).toLocaleString(); } catch { return utc; }
}

// --- replaces the existing RunGraph: x = oldest->newest, y = time with 0 at
// the BOTTOM and the worst time at the TOP (slower = higher; not inverted). ---
function RunGraph({ runs }) {
  const fin = runs.filter((r) => r.status === "finished" && r.total_ms != null);
  if (fin.length < 2)
    return html`<div class="rungraph-empty">finish at least 2 runs to see a graph</div>`;
  const W = 600, H = 150, pad = 22;
  const val = (r) => r.total_ms + r.start_offset_ms;
  const max = Math.max(...fin.map(val)) || 1;        // 0-based axis
  const x = (i) => pad + (i * (W - 2 * pad)) / (fin.length - 1);
  const y = (v) => (H - pad) - (v / max) * (H - 2 * pad);   // 0 -> bottom, max -> top
  const path = fin.map((r, i) =>
    `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(val(r)).toFixed(1)}`).join(" ");
  return html`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="rungraph">
    <line x1=${pad} y1=${H - pad} x2=${W - pad} y2=${H - pad} stroke="#2c3140" />
    <path d=${path} fill="none" stroke="#4a6fa5" stroke-width="1.5" />
    ${fin.map((r, i) => html`<circle cx=${x(i)} cy=${y(val(r))} r="3.5"
        fill=${r.is_pb ? "#ffd75f" : "#6fa8ff"}>
      <title>${fmtMs(val(r))}${r.is_pb ? " · PB" : ""} — ${fmtDate(r.started_utc)}</title>
    </circle>`)}
  </svg>`;
}

function RunHistory({ t, hist, openRun, setOpenRun }) {
  const [finishedOnly, setFinishedOnly] = useState(true);
  if (!hist) return html`<div class="runhistory workshop-empty compact">No run history yet.</div>`;
  const finished = hist.runs.filter((r) => r.status === "finished" && r.total_ms != null);
  const pbRun = finished.length
    ? finished.reduce((a, b) => (a.total_ms <= b.total_ms ? a : b)) : null;
  const list = [...hist.runs].reverse();
  const shown = finishedOnly ? list.filter((r) => r.status === "finished") : list;
  return html`<div class="runhistory">
    <div class="run-history-toolbar">
      <label class="reset-toggle">
        <input type="checkbox" checked=${finishedOnly}
            onchange=${(e) => setFinishedOnly(e.target.checked)} />
        <${Icon} name="check" size=${15} /> Finished only
      </label>
      ${pbRun ? html`<span class="pbtag">PB ${pbRun.display_total}</span>` : null}
    </div>
    <div class="run-history-graph">
      <${RunGraph} runs=${hist.runs} />
    </div>
    <div class="run-history-list">
      ${shown.length === 0 ? html`<div class="workshop-empty compact">No matching runs yet.</div>`
        : shown.map((r) => html`<div class=${`run-history-entry ${openRun === r.id ? "open" : ""}`}>
          <button class="run-history-row"
              aria-expanded=${openRun === r.id}
              onclick=${() => setOpenRun(openRun === r.id ? null : r.id)}>
            <span class="run-history-date">${fmtDate(r.started_utc)}</span>
            <span class="run-history-result">${r.status === "finished"
                ? html`<b>${r.display_total}</b>${r.is_pb
                  ? html` <span class="rungold">★ PB</span>` : null}`
                : html`<span class="meta">Aborted · reached step ${r.reached_step}</span>`}</span>
            <${Icon} name="chevron" size=${16} className="history-chevron" />
          </button>
          ${openRun === r.id ? html`<div class="run-history-splits">
            ${r.splits.map((s) => html`<div class="run-history-split">
              <span class="routenum">${s.step_index + 1}</span>
              <span>${s.display}</span>
              <span class="run-history-duration">${s.duration_display}
                ${s.fails ? html`<small>${s.fails} fail${s.fails > 1 ? "s" : ""}</small>` : null}
              </span>
            </div>`)}
          </div>` : null}
        </div>`)}
    </div>
  </div>`;
}

export function Run({ t }) {
  const run = t.run;                       // {active, pb, gold, start_offset_ms}
  const [routes, setRoutes] = useState([]);
  const [routeId, setRouteId] = useState(() => {
    const s = localStorage.getItem("sm64.activeRoute"); return s ? Number(s) : null; });
  const [routeView, setRouteView] = useState(null);   // preview steps + start_condition
  const [hist, setHist] = useState(null);
  const [focus, setFocus] = useState(() => localStorage.getItem("sm64.runFocus") === "1");
  const [hidden, setHidden] = useState(loadHidden);
  const [openRun, setOpenRun] = useState(null);        // run id expanded in history
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [err, setErr] = useState(null);

  useEffect(() => { getJSON("/api/routes").then(setRoutes).catch(() => {}); }, []);
  const active = run && run.active;
  const effRouteId = active ? active.route_id : routeId;
  useEffect(() => {
    if (effRouteId == null) { setRouteView(null); setHist(null); return; }
    getJSON(`/api/routes/${effRouteId}`).then(setRouteView).catch(() => setRouteView(null));
    getJSON(`/api/run/history?route_id=${effRouteId}`).then(setHist).catch(() => setHist(null));
  }, [effRouteId, run]);
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNowMs(Date.now()), 60);
    return () => clearInterval(id);
  }, [active && active.id]);

  const toggleFocus = () => {
    const v = !focus; localStorage.setItem("sm64.runFocus", v ? "1" : "0"); setFocus(v); };
  const toggleHide = (key) => setHidden((prev) => {
    const next = new Set(prev); next.has(key) ? next.delete(key) : next.add(key);
    localStorage.setItem("sm64.runHidden", JSON.stringify([...next])); return next; });
  const Timer = ({ k, children, cls }) => html`<button
      class=${`runhide run-time-button ${cls || ""}`}
      aria-label=${hidden.has(k) ? "Show hidden timer" : "Hide timer"}
      title=${hidden.has(k) ? "Show this timer" : "Hide this timer"}
      onclick=${() => toggleHide(k)}>${
      hidden.has(k) ? "– – – –" : children}</button>`;

  // Selecting a route ARMS it (no Start button). "none" disarms.
  async function pickRoute(id) {
    setErr(null);
    if (id == null) {
      localStorage.removeItem("sm64.activeRoute"); setRouteId(null);
      try { await send("POST", "/api/run/end"); } catch (e) {}
      t.refreshRun(); return;
    }
    localStorage.setItem("sm64.activeRoute", String(id)); setRouteId(id);
    try { await send("POST", "/api/run/start", { route_id: id }); } catch (e) { setErr(String(e)); }
    t.refreshRun();
  }

  async function pauseRun() { try { await send("POST", active.paused ? "/api/run/resume" : "/api/run/pause"); t.refreshRun(); } catch (e) { setErr(String(e)); } }
  async function resetRun() { try { await send("POST", "/api/run/reset"); t.refreshRun(); } catch (e) { setErr(String(e)); } }

  if (!run) return html`<${PageState} kind=${t.connected ? "loading" : "offline"}
      title="Preparing the run timer" />`;

  // Frozen post-run display: ONLY when the MOST RECENT run finished. After a
  // Reset / F1-abort the newest run is "aborted", so we fall through to the idle
  // 0:00 + preview (armed and ready) instead of showing a stale finished time.
  const mostRecent = hist && hist.runs.length ? hist.runs[hist.runs.length - 1] : null;
  const lastFinished = (mostRecent && mostRecent.status === "finished"
    && mostRecent.total_ms != null) ? mostRecent : null;

  // clock + step rows by state: active (live) > finished (frozen) > idle (preview)
  let clockMs, rows;
  if (active) {
    clockMs = active.start_offset_ms - (active.paused_ms || 0)
      + ((active.paused && active.paused_at ? Date.parse(active.paused_at) : nowMs)
         - Date.parse(active.started_utc));
    rows = active.steps.map((s, i) => ({
      key: i, display: s.display, group: s.candidates && s.candidates.length > 1,
      need: s.need, doneN: s.done.length, current: i === active.current_step,
      cumMs: s.elapsed_ms != null ? s.elapsed_ms + active.start_offset_ms
        : (i === active.current_step ? clockMs : null) }));
  } else if (lastFinished) {
    clockMs = lastFinished.total_ms + lastFinished.start_offset_ms;
    rows = lastFinished.splits.map((s) => ({
      key: s.step_index, display: s.display, current: false,
      cumMs: s.elapsed_ms + lastFinished.start_offset_ms }));
  } else {
    clockMs = run.start_offset_ms;                       // idle: 0:00 + offset
    rows = (routeView ? routeView.steps : []).map((s, i) => ({
      key: i, display: s.candidates.map((c) => c.display).join(" / ") || s.label || "?",
      group: s.candidates.length > 1, need: s.need, current: false, cumMs: null }));
  }
  const startLabel = routeView && routeView.start_condition
    ? (routeView.start_condition.type === "reset_game" ? "starts on game reset (F1)"
       : `starts on: ${routeView.start_condition.type}`) : "";

  const runState = active ? (active.paused ? "Paused" : "Running")
    : (lastFinished ? "Finished" : (effRouteId != null ? "Armed" : "Not armed"));
  return html`<div class=${`workshop-page run-page ${focus ? "runfocus" : ""}`}>
    <header class="practice-card workshop-hero">
      <div class="workshop-title">
        <span class="workshop-title-icon"><${Icon} name="run" size=${22} /></span>
        <div>
          <span class="eyebrow">Play</span>
          <h2>Run</h2>
          <p>Arm a route, follow each split, and keep the clock readable while you play.</p>
        </div>
      </div>
      <span class=${`run-state ${active && !active.paused ? "is-live" : ""}`}>
        <span class="status-light"></span>${runState}
      </span>
    </header>

    <section class="practice-card run-console-card">
      <div class="runbar">
        <label class="run-route-picker">
          <span class="field-label">Route</span>
          <select value=${effRouteId ?? ""} disabled=${!!active}
              onchange=${(e) => pickRoute(e.target.value ? Number(e.target.value) : null)}>
            <option value="">— pick a route to arm —</option>
            ${routes.map((r) => html`<option value=${r.id}>${r.name}</option>`)}
          </select>
        </label>
        <div class="run-actions">
          <button class=${focus ? "is-selected" : ""} onclick=${toggleFocus}
              title="Remove performance colors">
            <${Icon} name="eyeOff" size=${16} /> Focus ${focus ? "on" : ""}
          </button>
          ${active ? html`<button onclick=${pauseRun}>
            <${Icon} name=${active.paused ? "play" : "pause"} size=${16} />
            ${active.paused ? "Resume" : "Pause"}
          </button>` : null}
          ${active ? html`<button class="danger-text" onclick=${resetRun}>
            <${Icon} name="restart" size=${16} /> Reset
          </button>` : null}
        </div>
        <div class="run-pb-slot">
          <span class="field-label">Personal best</span>
          <b>${run.pb ? run.pb.display : "—"}</b>
        </div>
      </div>
      ${err ? html`<div class="run-error badx">${err}</div>` : null}
      <div class="run-live-slot">
        ${effRouteId == null
          ? html`<div class="workshop-empty run-empty">
              <span class="workshop-empty-icon"><${Icon} name="routes" size=${32} /></span>
              <h3>Pick a route to arm the timer</h3>
              <p>Selecting a route prepares it immediately. The clock begins on that route's start condition—usually an F1 reset.</p>
            </div>`
          : html`<div class="run-live-content">
            <div class="run-clock-row">
              <div>
                <span class="eyebrow">${runState}</span>
                <div class="runclock"><${Timer} k="total">${fmtMs(clockMs)}<//></div>
              </div>
              <div class="run-clock-note">
                ${active && active.paused
                  ? html`<b>Timer paused</b><span>Resume when you are ready.</span>`
                  : active
                    ? html`<b>Route in progress</b><span>Complete the highlighted step.</span>`
                    : html`<b>${lastFinished ? "Latest run finished" : "Ready to begin"}</b>
                        <span>${lastFinished ? "The final time stays frozen until the next run." : startLabel}</span>`}
                <small>Click any time to hide or reveal it.</small>
              </div>
            </div>
            <div class="run-split-scroll">
              <table class="runsplits"><tbody>
                ${rows.map((r) => html`<tr class=${r.current
                    ? "runstep-cur" : (r.cumMs != null ? "rundone" : "runupcoming")}>
                  <td class="run-split-index"><span class="routenum">${r.key + 1}</span></td>
                  <td class="run-split-name">
                    ${r.group ? html`<span class="chip">${r.need} of</span> ` : ""}${r.display}
                    ${r.group && r.doneN != null
                      ? html` <span class="meta">(${r.doneN}/${r.need})</span>` : ""}
                  </td>
                  <td class="run-split-time"><${Timer} k=${`step:${r.key}`}>${fmtMs(r.cumMs)}<//></td>
                </tr>`)}
              </tbody></table>
            </div>
          </div>`}
      </div>
    </section>

    <section class="practice-card run-history-card">
      <div class="workshop-card-heading">
        <div><span class="eyebrow">Progress</span><h3>Run history</h3></div>
        <span class="count-badge">${hist ? hist.runs.length : 0}</span>
      </div>
      <${RunHistory} t=${t} hist=${hist} openRun=${openRun} setOpenRun=${setOpenRun} />
    </section>
  </div>`;
}
