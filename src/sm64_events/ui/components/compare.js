// src/sm64_events/ui/components/compare.js — side-by-side comparison tab.
// Top: a combined, recency-sorted feed of every replayable run (stage filter) —
// pick one to load it as MY RUN (left). Right = comparison video(s). One
// centered transport (useSyncController) drives every <video> in lockstep; each
// video has a Premiere-style work-area (in/out) — comparison in/out auto-saves.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { useSyncController, VideoStage, WorkArea } from "./videosync.js";

const html = htm.bind(h);

function entityOf(sec) {
  return sec.kind === "segment"
    ? `segment:${sec.segment_id}`
    : `star:${sec.course_id}:${sec.star_id}`;
}
// Sort key: completion frames (stars = igt, segments = rta); nulls sort last.
function framesOf(a) {
  const f = a.igt_frames != null ? a.igt_frames : a.rta_frames;
  return f == null ? Infinity : f;
}

// ---- top: combined replayable-run feed -------------------------------------
// Flatten every star/segment section into one list of replayable successes,
// each carrying its stage (course, or "Segments"), item, strategy, time and
// recency. Newest first. `available` is the Set of replayable attempt ids.
function buildFeed(view, available) {
  const out = [];
  for (const sec of (view.stars || [])) {
    for (const a of sec.attempts) {
      if (a.cleared || a.outcome !== "success" || !available.has(a.id)) continue;
      out.push({ attemptId: a.id, entity: `star:${sec.course_id}:${sec.star_id}`,
        stageId: `c${sec.course_id}`, stageName: sec.course_name,
        itemName: sec.star_name, strat: a.strat_tag || null,
        time: a.igt || a.rta || "?", ended: a.ended_utc || "" });
    }
  }
  for (const sec of (view.segments || [])) {
    for (const a of sec.attempts) {
      if (a.cleared || a.outcome !== "success" || !available.has(a.id)) continue;
      out.push({ attemptId: a.id, entity: `segment:${sec.segment_id}`,
        stageId: "seg", stageName: "Segments",
        itemName: sec.name, strat: a.strat_tag || null,
        time: a.rta || a.igt || "?", ended: a.ended_utc || "" });
    }
  }
  // recency: ISO ended_utc desc; tie-break newest attempt id
  out.sort((x, y) => (y.ended || "").localeCompare(x.ended || "")
    || (y.attemptId - x.attemptId));
  return out;
}

function StageFeed({ view, available, attemptId, onPick }) {
  const [stage, setStage] = useState("all");
  if (available == null)
    return html`<div class="compare-feed"><div class="meta cf-empty">checking replayable runs…</div></div>`;
  const feed = buildFeed(view, available);
  const stages = [];
  for (const e of feed)
    if (!stages.find((s) => s.id === e.stageId)) stages.push({ id: e.stageId, name: e.stageName });
  const rows = stage === "all" ? feed : feed.filter((e) => e.stageId === stage);
  return html`<div class="compare-feed">
    <div class="cf-head">
      <span class="listhead" style="margin:0">My runs — newest first</span>
      <select value=${stage} onchange=${(e) => setStage(e.target.value)}>
        <option value="all">All stages</option>
        ${stages.map((s) => html`<option value=${s.id}>${s.name}</option>`)}
      </select>
    </div>
    ${rows.length === 0
      ? html`<div class="meta cf-empty">No replayable runs${stage === "all" ? "" : " for this stage"} yet
          — a run must be saved to disk or still in the replay buffer.</div>`
      : html`<div class="cf-list">
        ${rows.map((e) => html`<div class="cf-row ${e.attemptId === attemptId ? "on" : ""}"
            onclick=${() => onPick(e.entity, e.strat, e.attemptId)} title="load as My Run">
          <b>${e.time}</b>
          <span class="cf-stage">${e.stageName}</span>
          <span class="cf-item">${e.itemName}</span>
          ${e.strat ? html`<span class="chip">${e.strat}</span>`
            : html`<span class="meta">— no strat —</span>`}
        </div>`)}
      </div>`}
  </div>`;
}

// ---- left: my run (video + work-area) --------------------------------------
function MyRun({ attemptId, controller, inFrame, outFrame, onSync }) {
  const [st, setSt] = useState({ phase: "idle" });
  const [videoEl, setVideoEl] = useState(null);
  useEffect(() => {
    if (attemptId == null) { setSt({ phase: "idle" }); return; }
    let alive = true;
    setSt({ phase: "loading" });
    send("POST", `/api/attempts/${attemptId}/replay`)
      .then((r) => alive && setSt({ phase: "ready", ...r }))
      .catch((e) => alive && setSt({ phase: "error", message: String(e) }));
    return () => { alive = false; };
  }, [attemptId]);
  if (st.phase === "idle")
    return html`<div class="compare-empty meta">Pick one of your runs above to load it here.</div>`;
  if (st.phase === "loading")
    return html`<div class="compare-empty meta">extracting replay…</div>`;
  if (st.phase === "error")
    return html`<div class="compare-empty"><div class="badx">run footage unavailable</div>
      <div class="meta">${st.message}</div></div>`;
  return html`<div>
    <${VideoStage} id="mine" src=${st.clip_url} inFrame=${inFrame || 0}
      controller=${controller} onEl=${setVideoEl} />
    <${WorkArea} videoEl=${videoEl} inFrame=${inFrame} outFrame=${outFrame}
      onCommit=${(i, o) => onSync(i, o)} />
  </div>`;
}

// ---- right: comparisons ----------------------------------------------------
function ComparisonStage({ comp, controller, onEdit, onDelete }) {
  const [videoEl, setVideoEl] = useState(null);
  return html`<div class="compare-cmp">
    <div class="shead"><b>${comp.name}</b>
      <button class="meta" onclick=${() => onDelete(comp.id)} title="remove">×</button></div>
    <${VideoStage} id=${`cmp:${comp.id}`} src=${comp.clip_url}
      inFrame=${comp.in_frame || 0} controller=${controller} onEl=${setVideoEl} />
    <${WorkArea} videoEl=${videoEl} inFrame=${comp.in_frame} outFrame=${comp.out_frame}
      onCommit=${(i, o) => onEdit(comp.id, { in_frame: i, out_frame: o })} />
  </div>`;
}

// Video-sized drop zone (drag-drop / browse / rank-standard Load / YouTube URL).
function AddComparison({ entity, strat, suggestion, onAdded }) {
  const [job, setJob] = useState(null);
  const [url, setUrl] = useState("");
  const [over, setOver] = useState(false);
  const fileRef = useRef(null);
  const pollRef = useRef(null);
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  async function startImport(source_kind, source_ref, name) {
    const r = await send("POST", "/api/compare/import",
      { entity_key: entity, strat, name, source_kind, source_ref });
    pollJob(r.job_id);
  }
  async function startUpload(file) {
    const q = new URLSearchParams({ entity_key: entity, strat,
      name: file.name, filename: file.name });
    setJob({ state: "running", progress: 0, message: "uploading" });
    let r;
    try { r = await fetch(`/api/compare/upload?${q}`, { method: "POST", body: file }); }
    catch (e) { setJob({ state: "error", message: String(e) }); return; }
    if (!r.ok) { setJob({ state: "error", message: `upload failed (${r.status})` }); return; }
    const { job_id } = await r.json();
    pollJob(job_id);
  }
  function pollJob(jobId) {
    setJob({ state: "running", progress: 0 });
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const s = await getJSON(`/api/compare/import/${jobId}`);
        setJob(s);
        if (s.state === "done") { clearInterval(pollRef.current); pollRef.current = null; setJob(null); onAdded(); }
        else if (s.state === "error") { clearInterval(pollRef.current); pollRef.current = null; }
      } catch { clearInterval(pollRef.current); pollRef.current = null; }
    }, 800);
  }
  function onDrop(e) {
    e.preventDefault(); setOver(false);
    const f = e.dataTransfer.files[0];
    if (f) startUpload(f);
  }

  if (strat == null)
    return html`<div class="compare-empty meta">Pick a run above (or set a strategy in Practice)
      to add comparisons.</div>`;
  const busy = job && job.state === "running";
  return html`<div class="compare-drop ${over ? "over" : ""}"
      ondragover=${(e) => { e.preventDefault(); setOver(true); }}
      ondragleave=${() => setOver(false)} ondrop=${onDrop}>
    ${busy
      ? html`<div class="cd-inner"><div class="cd-icon">⏳</div>
          <div class="meta">loading… ${Math.round((job.progress || 0) * 100)}% ${job.message || ""}</div></div>`
      : html`<div class="cd-inner">
          <div class="cd-icon">⬆</div>
          <div>Drag &amp; drop a video here</div>
          <div class="meta">or</div>
          <div class="cd-actions">
            <button onclick=${() => fileRef.current && fileRef.current.click()}>Browse files</button>
            ${suggestion && html`<button onclick=${() =>
              startImport(suggestion.source_kind, suggestion.source_ref, suggestion.name)}>
              ▸ Load ${suggestion.name}</button>`}
          </div>
          <div class="cd-url">
            <input placeholder="paste a YouTube URL" value=${url}
              oninput=${(e) => setUrl(e.target.value)} />
            <button disabled=${!url} onclick=${() => startImport("youtube", url, url)}>Add URL</button>
          </div>
          ${job && job.state === "error" && html`<div class="badx">import failed: ${job.message}</div>`}
        </div>`}
    <input type="file" accept="video/*" style="display:none" ref=${fileRef}
      onchange=${(e) => { const f = e.target.files[0]; if (f) startUpload(f); }} />
  </div>`;
}

// ---- transport -------------------------------------------------------------
function Transport({ controller }) {
  return html`<div class="compare-transport">
    <button onclick=${() => controller.toStart()} title="jump to beginning">⏮ start</button>
    <button onclick=${() => controller.step(-1)} title="back one frame">⏴ frame</button>
    <button onclick=${() => controller.playing ? controller.pause() : controller.play()}
      style="min-width:5.5rem">${controller.playing ? "❚❚ pause" : "▶ play"}</button>
    <button onclick=${() => controller.step(1)} title="forward one frame">frame ⏵</button>
    <div class="meta">1 frame = 1/30 s (game frame)</div>
  </div>`;
}

export function Compare({ t, intent, clearIntent, active }) {
  const controller = useSyncController();
  const [view, setView] = useState(null);          // lifetime session view
  const [availSet, setAvailSet] = useState(null);  // replayable attempt ids (null = loading)
  const [entity, setEntity] = useState(null);
  const [strat, setStrat] = useState(null);
  const [attemptId, setAttemptId] = useState(null);
  const [cmp, setCmp] = useState({ saved: [], suggestion: null });
  const [myIn, setMyIn] = useState(0);             // My Run work-area (in-memory)
  const [myOut, setMyOut] = useState(null);
  const deepLinked = useRef(false);
  const initialized = useRef(false);

  // (re)load the view + replayable-run set whenever the tab becomes active — the
  // buffer shifts, so the feed/availability refresh on each open. The default
  // entity is chosen only on the FIRST activation (later activations keep the
  // user's selection — that's what makes the comparison persist across tabs).
  useEffect(() => {
    if (!active) return;
    const first = !initialized.current;
    initialized.current = true;
    getJSON("/api/session?scope=lifetime").then((v) => {
      setView(v);
      if (first && !intent) {
        const tgt = v.target || {};
        const def = tgt.kind === "segment" ? `segment:${tgt.segment_id}`
          : tgt.course_id != null ? `star:${tgt.course_id}:${tgt.star_id}` : null;
        if (def) setEntity(def);
      }
    }).catch(() => {});
    getJSON("/api/replay/available")
      .then((r) => setAvailSet(new Set(r.available)))
      .catch(() => setAvailSet(new Set()));
  }, [active]);

  // deep-link intent (Compare button from Practice) — sets entity+strat+run
  useEffect(() => {
    if (!intent) return;
    setEntity(intent.entity); setStrat(intent.strat); setAttemptId(intent.attemptId);
    deepLinked.current = true;
    clearIntent();
  }, [intent]);

  // resolve the strat from the section when entity changes by plain browsing;
  // skip once right after a deep-link / feed pick (which set the strat itself)
  useEffect(() => {
    if (!view || !entity) return;
    if (deepLinked.current) { deepLinked.current = false; return; }
    const secs = [...(view.stars || []), ...(view.segments || [])];
    const sec = secs.find((s) => entityOf(s) === entity);
    if (sec) setStrat(sec.last_strat || null);
  }, [entity, view]);

  // reset My Run's work-area when a different run is picked
  useEffect(() => { setMyIn(0); setMyOut(null); }, [attemptId]);

  const reloadCmp = () => {
    if (!entity) return;
    getJSON(`/api/compare/view?entity=${encodeURIComponent(entity)}`
      + (strat ? `&strat=${encodeURIComponent(strat)}` : ""))
      .then(setCmp).catch(() => {});
  };
  useEffect(reloadCmp, [entity, strat]);

  async function editCmp(id, pts) {
    await send("PUT", `/api/compare/videos/${id}`, pts);   // auto-save in/out
    reloadCmp();
  }
  async function delCmp(id) {
    await send("DELETE", `/api/compare/videos/${id}`);
    reloadCmp();
  }
  // picking a run from the feed sets its entity + strategy (so the matching
  // comparison auto-loads) + the run itself
  function pickRun(ent, s, aid) {
    setEntity(ent);
    if (s !== undefined) { setStrat(s || null); deepLinked.current = true; }
    setAttemptId(aid == null ? null : aid);
  }

  if (!view) return html`<p class="meta">loading…</p>`;
  const shown = cmp.saved;
  const suggestion = cmp.auto && cmp.auto.mode === "suggestion" ? cmp.suggestion : null;

  return html`<div class="compare">
    <${StageFeed} view=${view} available=${availSet} attemptId=${attemptId} onPick=${pickRun} />
    <div class="compare-grid">
      <div class="compare-col">
        <div class="meta listhead">My run</div>
        <${MyRun} attemptId=${attemptId} controller=${controller}
          inFrame=${myIn} outFrame=${myOut}
          onSync=${(i, o) => { setMyIn(i); setMyOut(o); }} />
      </div>
      <div class="compare-center">
        <${Transport} controller=${controller} />
      </div>
      <div class="compare-col">
        <div class="meta listhead">Comparison ${strat ? `· ${strat}` : ""}</div>
        ${shown.map((c) => html`<${ComparisonStage} key=${c.id} comp=${c}
          controller=${controller} onEdit=${editCmp} onDelete=${delCmp} />`)}
        <${AddComparison} entity=${entity} strat=${strat} suggestion=${suggestion}
          onAdded=${reloadCmp} />
      </div>
    </div>
  </div>`;
}
