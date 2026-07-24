// src/sm64_events/ui/components/failcomp.js
// Shared "Generate failure compilation" control for a star OR segment practice
// card (star<->segment parity — tests/test_ui_section_parity.py). Posts to
// /api/compilation, polls the job, then shows the output path with a
// Reveal-in-Explorer button (reuses /api/replay/reveal — the output lives under
// save_root). Identity dispatch mirrors the server body: {segment_id} vs {star}.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";

const html = htm.bind(h);

function stored(key, fallback) {
  const v = parseFloat(localStorage.getItem(key));
  return Number.isFinite(v) ? v : fallback;
}

export function FailureCompilation({ identity }) {
  const [xBefore, setXBefore] = useState(() => stored("sm64.failcomp.xBefore", 5));
  const [yAfter, setYAfter] = useState(() => stored("sm64.failcomp.yAfter", 3));
  const [job, setJob] = useState(null);   // {state, progress, message, result}
  const pollRef = useRef(null);
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  function setX(v) { setXBefore(v); localStorage.setItem("sm64.failcomp.xBefore", v); }
  function setY(v) { setYAfter(v); localStorage.setItem("sm64.failcomp.yAfter", v); }

  async function generate() {
    setJob({ state: "running", progress: 0, message: "starting" });
    const target = identity.segment_id != null
      ? { segment_id: identity.segment_id }
      : { star: { course_id: identity.course_id, star_id: identity.star_id } };
    let r;
    try {
      r = await send("POST", "/api/compilation",
        { x_before: xBefore, y_after: yAfter, ...target });
    } catch (e) { setJob({ state: "error", message: String(e.message || e) }); return; }
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const s = await getJSON(`/api/compilation/${r.job_id}`);
        setJob(s);
        if (s.state === "done" || s.state === "error") {
          clearInterval(pollRef.current); pollRef.current = null;
        }
      } catch (e) {
        clearInterval(pollRef.current); pollRef.current = null;
        setJob({ state: "error", message: String(e.message || e) });
      }
    }, 800);
  }

  async function reveal(path) {
    try { await send("POST", "/api/replay/reveal", { path }); } catch { /* best effort */ }
  }

  const running = job && job.state === "running";
  const res = job && job.state === "done" && job.result;
  return html`<div class="failcomp">
    <div class="failcomp-row">
      <label>Before <input type="number" min="0" step="0.5" value=${xBefore}
        onchange=${(e) => setX(parseFloat(e.target.value))} /> s</label>
      <label>After <input type="number" min="0" step="0.5" value=${yAfter}
        onchange=${(e) => setY(parseFloat(e.target.value))} /> s</label>
      <button class="quiet-button" disabled=${running} onclick=${generate}>
        ${running ? "Generating…" : "Generate failure compilation"}</button>
    </div>
    ${running && html`<div class="meta">${job.message || "working…"}</div>`}
    ${job && job.state === "error"
      && html`<div class="danger-text">${job.message}</div>`}
    ${res && html`<div class="failcomp-result">
      <div class="meta">${res.clip_count} clips${res.finale_time
        ? ` · fastest run ${res.finale_time}` : ""}${res.skipped
        ? ` · ${res.skipped} skipped (aged out)` : ""}${res.no_finale
        ? " · no successful run in buffer" : ""}</div>
      <code class="failcomp-path">${res.path}</code>
      <button class="quiet-button" onclick=${() => reveal(res.path)}>
        Reveal in Explorer</button>
    </div>`}
  </div>`;
}
