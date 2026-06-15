// src/sm64_events/ui/components/runview.js — full-game run timer (Run tab).
// Renders GET /api/run (via the store's t.run, refetched on run_* WS events);
// the big clock + current-step time TICK client-side off the authoritative
// started_utc + start_offset_ms. Forgiving RTA: no pause subtraction (v1).
// Focus mode (neutral, no ±/gold) and click-to-hide any timer are pure UI
// state in localStorage.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";

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
const fmtDelta = (ms) =>
  ms == null ? "" : `${ms > 0 ? "+" : ms < 0 ? "−" : ""}${(Math.abs(ms) / 1000).toFixed(2)}`;

// Persisted set of hidden timer keys (click-to-hide).
function loadHidden() {
  try { return new Set(JSON.parse(localStorage.getItem("sm64.runHidden") || "[]")); }
  catch { return new Set(); }
}

export function Run({ t }) {
  const run = t.run;
  const [routes, setRoutes] = useState([]);
  const [routeId, setRouteId] = useState(() => {
    const s = localStorage.getItem("sm64.activeRoute"); return s ? Number(s) : null;
  });
  const [focus, setFocus] = useState(() => localStorage.getItem("sm64.runFocus") === "1");
  const [hidden, setHidden] = useState(loadHidden);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [err, setErr] = useState(null);

  useEffect(() => { getJSON("/api/routes").then(setRoutes).catch(() => {}); }, []);
  // tick the live clock only while a run is active
  const active = run && run.active;
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNowMs(Date.now()), 60);
    return () => clearInterval(id);
  }, [active && active.id]);

  const toggleFocus = () => {
    const v = !focus; localStorage.setItem("sm64.runFocus", v ? "1" : "0"); setFocus(v);
  };
  const toggleHide = (key) => setHidden((prev) => {
    const next = new Set(prev);
    next.has(key) ? next.delete(key) : next.add(key);
    localStorage.setItem("sm64.runHidden", JSON.stringify([...next]));
    return next;
  });
  const Timer = ({ k, children, cls }) => html`<span
      class="runhide ${cls || ""}" title="click to hide/show"
      onclick=${() => toggleHide(k)}>${hidden.has(k) ? "- - - -" : children}</span>`;

  async function startRun() {
    if (routeId == null) { setErr("pick a route first"); return; }
    try { setErr(null); await send("POST", "/api/run/start", { route_id: routeId });
      t.refreshRun(); }
    catch (e) { setErr(String(e)); }
  }
  async function endRun() {
    try { await send("POST", "/api/run/end"); t.refreshRun(); }
    catch (e) { setErr(String(e)); }
  }

  if (!run) return html`<p class="meta">loading…</p>`;

  // live total elapsed (ms) from the authoritative start + offset
  const liveMs = active
    ? (nowMs - Date.parse(active.started_utc) + active.start_offset_ms)
    : null;

  return html`<div class=${focus ? "runfocus" : ""}>
    <div class="runbar">
      <select value=${(active ? active.route_id : routeId) ?? ""} disabled=${!!active}
          onchange=${(e) => { const v = e.target.value ? Number(e.target.value) : null;
            setRouteId(v); if (v != null) localStorage.setItem("sm64.activeRoute", String(v)); }}>
        <option value="">— pick a route —</option>
        ${routes.map((r) => html`<option value=${r.id}>${r.name}</option>`)}
      </select>
      ${active
        ? html`<button onclick=${endRun}>End run</button>`
        : html`<button onclick=${startRun}>Start run</button>`}
      <button onclick=${toggleFocus}>${focus ? "Focus ✓" : "Focus"}</button>
      <span style="flex:1"></span>
      ${run.pb ? html`<span class="meta">PB ${run.pb.display}</span>` : null}
      ${run.gold && run.gold.display ? html`<span class="meta">SoB ${run.gold.display}</span>` : null}
    </div>

    ${err ? html`<div class="badx">${err}</div>` : null}

    ${!active
      ? html`<p class="meta">${routeId == null
          ? "Pick a route and press Start run."
          : "Armed — press F1 to begin the run (the clock starts on reset)."}</p>`
      : html`<div>
        <div class="runclock"><${Timer} k="total">${fmtMs(liveMs)}<//></div>
        <table class="runsplits"><tbody>
          ${active.steps.map((s, i) => {
            const isCur = i === active.current_step;
            const cls = isCur ? "runstep-cur" : (s.elapsed_ms != null ? "rundone" : "runupcoming");
            // cumulative shown: completed -> its split; current -> live; upcoming -> —
            const cumMs = s.elapsed_ms != null ? s.elapsed_ms + active.start_offset_ms
              : (isCur ? liveMs : null);
            // ± vs PB cumulative (only meaningful once this step has data and PB exists)
            const delta = (s.elapsed_ms != null && s.pb_elapsed_ms != null)
              ? (s.elapsed_ms + active.start_offset_ms) - (s.pb_elapsed_ms + active.start_offset_ms)
              : null;
            // gold: this step's segment duration beat the route's best for it
            const prevCum = i > 0 && active.steps[i - 1].elapsed_ms != null
              ? active.steps[i - 1].elapsed_ms : 0;
            const segDur = s.elapsed_ms != null ? s.elapsed_ms - prevCum : null;
            const isGold = !focus && segDur != null && s.gold_ms != null && segDur < s.gold_ms;
            const grp = s.candidates && s.candidates.length > 1;
            return html`<tr class=${cls}>
              <td class="meta">${i + 1}</td>
              <td>${grp ? html`<span class="chip">${s.need} of ${s.candidates.length}</span> ` : ""}
                  ${s.display}${grp ? html` <span class="meta">(${s.done.length}/${s.need})</span>` : ""}</td>
              <td style="text-align:right" class=${isGold ? "rungold" : ""}>
                <${Timer} k=${`step:${i}`}>${fmtMs(cumMs)}<//>${isGold ? " ★" : ""}</td>
              <td style="text-align:right">${focus || delta == null ? "" : html`
                <${Timer} k=${`delta:${i}`} cls=${delta > 0 ? "runbehind" : "runahead"}>
                  ${fmtDelta(delta)}<//>`}</td>
            </tr>`;
          })}
        </tbody></table>
      </div>`}
  </div>`;
}
