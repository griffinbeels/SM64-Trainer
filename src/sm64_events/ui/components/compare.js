// src/sm64_events/ui/components/compare.js — side-by-side comparison tab.
// Left = my run (reuses the replay extract/serve pipeline by attempt_id).
// Right = comparison video(s) normalized to local mp4 (yt-dlp/file import).
// One centered transport (useSyncController) drives every <video> in lockstep.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { useSyncController, VideoStage, SyncTrack } from "./videosync.js";

const html = htm.bind(h);

// entity_key <-> section helpers
function entityOf(sec) {
  return sec.kind === "segment"
    ? `segment:${sec.segment_id}`
    : `star:${sec.course_id}:${sec.star_id}`;
}
function sectionLabel(sec) {
  return sec.kind === "segment" ? `⏱ ${sec.name}`
    : `${sec.course_name} · ${sec.star_name}`;
}

// ---- left: my run ----------------------------------------------------------
// Sort key: completion frames (stars = igt, segments = rta); nulls sort last.
function framesOf(a) {
  const f = a.igt_frames != null ? a.igt_frames : a.rta_frames;
  return f == null ? Infinity : f;
}

// `available` is a Set of attempt ids whose replay is obtainable now (saved or
// buffer-covered, from GET /api/replay/available), or null while it loads. The
// run list shows ONLY replayable successes — clicking anything else would just
// 409 "no footage" — sorted fastest first.
function MyRunPicker({ view, entity, attemptId, onPick, available }) {
  const sections = [...(view.stars || []), ...(view.segments || [])];
  const cur = sections.find((s) => entityOf(s) === entity) || sections[0];
  const loading = available == null;
  const runs = (cur && !loading)
    ? cur.attempts
        .filter((a) => !a.cleared && a.outcome === "success" && available.has(a.id))
        .slice()
        .sort((a, b) => framesOf(a) - framesOf(b))
    : [];
  const placeholder = loading ? "— checking runs… —"
    : runs.length === 0 ? "— no replayable runs —" : "— pick a run —";
  return html`<div class="compare-pick">
    <select value=${cur ? entityOf(cur) : ""}
        onchange=${(e) => onPick(e.target.value, null)}>
      ${sections.map((s) => html`<option value=${entityOf(s)}>${sectionLabel(s)}</option>`)}
    </select>
    <select value=${attemptId ?? ""}
        onchange=${(e) => onPick(entityOf(cur), e.target.value === "" ? null : Number(e.target.value))}>
      <option value="">${placeholder}</option>
      ${runs.map((a) => html`<option value=${a.id}>${a.igt || a.rta || "?"} · #${a.id}
        ${a.strat_tag ? `· ${a.strat_tag}` : ""}</option>`)}
    </select>
  </div>`;
}

function MyRunStage({ attemptId, controller }) {
  const [st, setSt] = useState({ phase: "idle" });
  useEffect(() => {
    if (attemptId == null) { setSt({ phase: "idle" }); return; }
    let alive = true;
    setSt({ phase: "loading" });
    send("POST", `/api/attempts/${attemptId}/replay`)
      .then((r) => alive && setSt({ phase: "ready", ...r }))
      .catch((e) => alive && setSt({ phase: "error", message: String(e) }));
    return () => { alive = false; };
  }, [attemptId]);
  if (st.phase === "idle") return html`<div class="meta">Pick one of your runs on the left.</div>`;
  if (st.phase === "loading") return html`<div class="meta">extracting replay…</div>`;
  if (st.phase === "error")
    return html`<div class="badx">run footage unavailable</div>
      <div class="meta">${st.message}</div>`;
  return html`<${VideoStage} id="mine" src=${st.clip_url} inFrame=${0}
    controller=${controller} />`;
}

// ---- right: comparisons ----------------------------------------------------
function AddComparison({ entity, strat, suggestion, onAdded }) {
  const [job, setJob] = useState(null);
  const [url, setUrl] = useState("");
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
    e.preventDefault();
    const f = e.dataTransfer.files[0];
    if (f) startUpload(f);
  }

  if (strat == null)
    return html`<div class="meta">Select a strategy in Practice to enable comparisons.</div>`;
  return html`<div class="compare-add" ondragover=${(e) => e.preventDefault()}
      ondrop=${onDrop}>
    ${suggestion && html`<button onclick=${() =>
        startImport(suggestion.source_kind, suggestion.source_ref, suggestion.name)}>
      ▸ Load ${suggestion.name}</button>`}
    <input placeholder="paste a YouTube URL" value=${url}
      oninput=${(e) => setUrl(e.target.value)} />
    <button disabled=${!url} onclick=${() =>
      startImport("youtube", url, url)}>Add URL</button>
    <button onclick=${() => fileRef.current && fileRef.current.click()}>Choose file…</button>
    <input type="file" accept="video/*" style="display:none" ref=${fileRef}
      onchange=${(e) => { const f = e.target.files[0];
        if (f) startUpload(f); }} />
    <span class="meta"> or drag a video here</span>
    ${job && job.state === "running" && html`<div class="meta">
      loading… ${Math.round((job.progress || 0) * 100)}% ${job.message || ""}</div>`}
    ${job && job.state === "error" && html`<div class="badx">import failed: ${job.message}</div>`}
  </div>`;
}

function ComparisonStage({ comp, controller, onEdit, onDelete }) {
  const [videoEl, setVideoEl] = useState(null);
  return html`<div>
    <div class="shead"><b>${comp.name}</b>
      <button class="meta" onclick=${() => onDelete(comp.id)} title="remove">×</button></div>
    <${VideoStage} id=${`cmp:${comp.id}`} src=${comp.clip_url}
      inFrame=${comp.in_frame || 0} controller=${controller} onEl=${setVideoEl} />
    <${SyncTrack} videoEl=${videoEl} inFrame=${comp.in_frame} outFrame=${comp.out_frame}
      onChange=${(pts) => onEdit(comp.id, pts)} />
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

export function Compare({ t, intent, clearIntent }) {
  const controller = useSyncController();
  const [view, setView] = useState(null);          // lifetime session view
  const [entity, setEntity] = useState(null);
  const [strat, setStrat] = useState(null);
  const [attemptId, setAttemptId] = useState(null);
  const [cmp, setCmp] = useState({ saved: [], suggestion: null });
  const [availSet, setAvailSet] = useState(null); // replayable attempt ids (null = loading)
  const deepLinked = useRef(false);

  // load the lifetime session view once (left-side picker source), and the set
  // of currently-replayable runs — recomputed on every open because the ring
  // shifts, so runs that aged out of the buffer (and were never saved) drop off.
  useEffect(() => {
    getJSON("/api/session?scope=lifetime").then((v) => {
      setView(v);
      // default: the live target's section, else the first section
      const tgt = v.target || {};
      const def = tgt.kind === "segment" ? `segment:${tgt.segment_id}`
        : tgt.course_id != null ? `star:${tgt.course_id}:${tgt.star_id}` : null;
      if (!intent) setEntity(def);
    }).catch(() => {});
    getJSON("/api/replay/available")
      .then((r) => setAvailSet(new Set(r.available)))
      .catch(() => setAvailSet(new Set()));
  }, []);

  // apply a deep-link intent (Compare button from Practice)
  useEffect(() => {
    if (!intent) return;
    setEntity(intent.entity);
    setStrat(intent.strat);
    setAttemptId(intent.attemptId);
    deepLinked.current = true;
    clearIntent();
  }, [intent]);

  // resolve the active strat for the chosen entity from the session view
  useEffect(() => {
    if (!view || !entity) return;
    if (deepLinked.current) { deepLinked.current = false; return; }
    const secs = [...(view.stars || []), ...(view.segments || [])];
    const sec = secs.find((s) => entityOf(s) === entity);
    if (sec) setStrat(sec.last_strat || null);
  }, [entity, view]);

  // fetch comparisons + auto-pick whenever (entity, strat) changes
  const reloadCmp = () => {
    if (!entity) return;
    getJSON(`/api/compare/view?entity=${encodeURIComponent(entity)}`
      + (strat ? `&strat=${encodeURIComponent(strat)}` : ""))
      .then(setCmp).catch(() => {});
  };
  useEffect(reloadCmp, [entity, strat]);

  async function editCmp(id, pts) {
    await send("PUT", `/api/compare/videos/${id}`, pts);
    reloadCmp();
  }
  async function delCmp(id) {
    await send("DELETE", `/api/compare/videos/${id}`);
    reloadCmp();
  }
  function pickRun(ent, aid) {
    setEntity(ent);
    setAttemptId(aid);
  }

  if (!view) return html`<p class="meta">loading…</p>`;
  // auto-selected saved comparison shows by default; others are addable
  const shown = cmp.saved;
  const suggestion = cmp.auto && cmp.auto.mode === "suggestion" ? cmp.suggestion : null;

  return html`<div class="compare">
    <div class="compare-grid">
      <div class="compare-col">
        <div class="meta listhead">My run</div>
        <${MyRunPicker} view=${view} entity=${entity} attemptId=${attemptId}
          onPick=${pickRun} available=${availSet} />
        <${MyRunStage} attemptId=${attemptId} controller=${controller} />
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
