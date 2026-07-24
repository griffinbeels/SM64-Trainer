// src/sm64_events/ui/components/failcomp.js
// Shared "Compilation" drawer for a star OR segment practice card (star<->segment
// parity — tests/test_ui_section_parity.py). A collapsible panel (same shell as
// the Rank standards drawer: .stdpanel / .disc.standards-toggle / .stdbody) that
// holds the window settings + a Generate action. Posts to /api/compilation, polls
// the job, then shows the output path with a Reveal-in-Explorer button (reuses
// /api/replay/reveal — the output lives under save_root). Identity dispatch
// mirrors the server body: {segment_id} vs {star}.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { Icon } from "./icons.js";

const html = htm.bind(h);

function stored(key, fallback) {
  const v = parseFloat(localStorage.getItem(key));
  return Number.isFinite(v) ? v : fallback;
}

export function FailureCompilation({ identity, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);
  const [xBefore, setXBefore] = useState(() => stored("sm64.failcomp.xBefore", 5));
  const [yAfter, setYAfter] = useState(() => stored("sm64.failcomp.yAfter", 3));
  const [job, setJob] = useState(null);   // {state, progress, message, result}
  const pollRef = useRef(null);
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  // A blank/invalid number input parses to NaN — ignore it and keep the last
  // good value rather than storing NaN (which would silently break the next
  // POST /api/compilation body).
  function setX(v) { if (!Number.isFinite(v)) return; setXBefore(v); localStorage.setItem("sm64.failcomp.xBefore", v); }
  function setY(v) { if (!Number.isFinite(v)) return; setYAfter(v); localStorage.setItem("sm64.failcomp.yAfter", v); }

  async function generate() {
    if (job && job.state === "running") return; // re-entrancy guard: one job at a time
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
  const kind = identity.segment_id != null ? "segment" : "star";

  // Reuse the Rank-standards drawer shell (.stdpanel / .disc.standards-toggle /
  // .stdbody / .standards-chevron) so this reads as a sibling drawer; only the
  // inner .compilation-* content is bespoke. The header subtitle shows the live
  // window so the setting is legible while collapsed.
  return html`<div class="stdpanel">
    <button class="disc standards-toggle" onclick=${() => setOpen(!open)} aria-expanded=${open}>
      <${Icon} name="play" size=${16} />
      <span>Compilation</span>
      <span class="meta"> · ${xBefore}s before · ${yAfter}s after</span>
      <${Icon} name="chevron" size=${16} className="standards-chevron" />
    </button>
    ${open ? html`<div class="stdbody compilation-body">
      <p class="compilation-lead">Stitch every failed attempt for this ${kind},
        in the order they'd happen during a run, ending with your fastest clean
        run — one MP4 to trim in an editor.</p>

      <div class="compilation-settings">
        <span class="field-label">Window around each failure</span>
        <div class="compilation-fields">
          <label class="compilation-field">Before
            <input type="number" min="0" step="0.5" value=${xBefore}
              onchange=${(e) => setX(parseFloat(e.target.value))} />
            <span class="unit">s</span></label>
          <label class="compilation-field">After
            <input type="number" min="0" step="0.5" value=${yAfter}
              onchange=${(e) => setY(parseFloat(e.target.value))} />
            <span class="unit">s</span></label>
        </div>
        <small class="meta">Seconds of footage kept before and after each death or reset.</small>
      </div>

      <div class="compilation-actions">
        <button class="primary-button" disabled=${running} onclick=${generate}>
          <${Icon} name="play" size=${15} /> ${running ? "Generating…" : "Generate compilation"}
        </button>
        ${running ? html`<span class="meta">${job.message || "working…"}</span>` : null}
      </div>

      ${job && job.state === "error"
        ? html`<div class="badx compilation-msg">${job.message}</div>` : null}

      ${res ? html`<div class="compilation-result">
        <div class="compilation-summary">
          <${Icon} name="check" size=${15} />
          <span>${res.clip_count} clip${res.clip_count === 1 ? "" : "s"}${res.finale_time
            ? ` · fastest run ${res.finale_time}` : ""}${res.skipped
            ? ` · ${res.skipped} skipped (aged out)` : ""}${res.no_finale
            ? " · no successful run in buffer" : ""}</span>
        </div>
        <code class="compilation-path">${res.path}</code>
        <button class="compilation-reveal" onclick=${() => reveal(res.path)}>
          <${Icon} name="expand" size=${15} /> Reveal in Explorer
        </button>
      </div>` : null}
    </div>` : null}
  </div>`;
}
