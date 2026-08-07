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
import { Icon } from "./icons.js";
import { PageState } from "./states.js";

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
    return html`<section class="compare-feed practice-card">
      <div class="workshop-empty compact">Checking replayable runs…</div>
    </section>`;
  const feed = buildFeed(view, available);
  const stages = [];
  for (const e of feed)
    if (!stages.find((s) => s.id === e.stageId)) stages.push({ id: e.stageId, name: e.stageName });
  const rows = stage === "all" ? feed : feed.filter((e) => e.stageId === stage);
  return html`<section class="compare-feed practice-card">
    <div class="cf-head">
      <div>
        <span class="eyebrow">Source footage</span>
        <h3>My replayable runs</h3>
      </div>
      <select value=${stage} onchange=${(e) => setStage(e.target.value)}>
        <option value="all">All stages</option>
        ${stages.map((s) => html`<option value=${s.id}>${s.name}</option>`)}
      </select>
    </div>
    ${rows.length === 0
      ? html`<div class="meta cf-empty">No replayable runs${stage === "all" ? "" : " for this stage"} yet
          — a run must be saved to disk or still in the replay buffer.</div>`
      : html`<div class="cf-list">
        ${rows.map((e) => html`<button class=${`cf-row ${e.attemptId === attemptId ? "on" : ""}`}
            onclick=${() => onPick(e.entity, e.strat, e.attemptId)} title="Load as My Run">
          <b>${e.time}</b>
          <span class="cf-stage">${e.stageName}</span>
          <span class="cf-item">${e.itemName}</span>
          ${e.strat ? html`<span class="chip">${e.strat}</span>`
            : html`<span class="meta">— no strat —</span>`}
          <${Icon} name="play" size=${14} className="cf-load-icon" />
        </button>`)}
      </div>`}
  </section>`;
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
    return html`<div class="compare-empty meta">Pick one of your runs from the list below to load it here.</div>`;
  if (st.phase === "loading")
    return html`<div class="compare-empty meta">extracting replay…</div>`;
  if (st.phase === "error")
    return html`<div class="compare-empty"><div class="badx">run footage unavailable</div>
      <div class="meta">${st.message}</div></div>`;
  return html`<div>
    <${VideoStage} id="mine" src=${st.clip_url} inFrame=${inFrame || 0}
      controller=${controller} onEl=${setVideoEl} />
    <${WorkArea} videoEl=${videoEl} inFrame=${inFrame} outFrame=${outFrame}
      controller=${controller} onCommit=${(i, o) => onSync(i, o)} />
  </div>`;
}

// ---- right: comparisons ----------------------------------------------------
// Strategy picker: the entity's strategies + "— none —". Always includes the
// current value so an unknown/legacy strat still shows.
function StrategySelect({ strategies, value, onChange }) {
  const opts = [];
  for (const s of strategies) if (s && !opts.includes(s)) opts.push(s);
  if (value && !opts.includes(value)) opts.unshift(value);
  return html`<select class="cmp-strat meta" value=${value || ""}
      onchange=${(e) => onChange(e.target.value)}>
    <option value="">— no strategy —</option>
    ${opts.map((s) => html`<option value=${s}>${s}</option>`)}
  </select>`;
}

// A raw URL is never shown — the title is (backfilled server-side); if a title
// still can't be resolved, fall back to a friendly label, never the URL.
function displayTitle(comp) {
  const n = comp.name || "";
  if (comp.source_kind === "youtube" && /^https?:\/\//i.test(n)) return "YouTube video";
  return n || "video";
}

// No header ABOVE the video, so the comparison video top-aligns with My Run.
// Title (italic subheader) sits under the video; strategy moved to the section
// header; the remove (×) button rides the work-area button row (after set end).
function ComparisonStage({ comp, controller, onEdit, onDelete }) {
  const [videoEl, setVideoEl] = useState(null);
  const [name, setName] = useState(comp.name || "");
  useEffect(() => { setName(comp.name || ""); }, [comp.name]);   // follow title backfill
  const label = displayTitle(comp);
  const stratSuffix = comp.strat ? ` · ${comp.strat}` : "";
  // YouTube title is hyperlinked to its source URL
  const caption = comp.source_kind === "youtube" && comp.source_ref
    ? html`<a href=${comp.source_ref} target="_blank" rel="noopener"
        title=${comp.source_ref}>${label}</a>${stratSuffix}`
    : html`${label}${stratSuffix}`;
  return html`<div class="compare-cmp">
    <${VideoStage} id=${`cmp:${comp.id}`} src=${comp.clip_url}
      inFrame=${comp.in_frame || 0} controller=${controller} onEl=${setVideoEl}
      caption=${caption} />
    <${WorkArea} videoEl=${videoEl} inFrame=${comp.in_frame} outFrame=${comp.out_frame}
      controller=${controller} onCommit=${(i, o) => onEdit(comp.id, { in_frame: i, out_frame: o })}
      extra=${html`<button class="icon-button danger-icon" onclick=${() => onDelete(comp.id)}
        title="Close this comparison" aria-label="Close this comparison">
        <${Icon} name="close" size=${15} />
      </button>`} />
    <div class="cmp-save">
      <input value=${name} oninput=${(e) => setName(e.target.value)}
        placeholder="name this comparison" />
      <button onclick=${() => onEdit(comp.id, { name })} title="Save this name">
        <${Icon} name="save" size=${15} /> Save
      </button>
    </div>
  </div>`;
}

// Video-sized drop zone (drag-drop / browse / rank-standard Load / YouTube URL).
function AddComparison({ entity, strat, strategies, suggestion, onAdded, hasVideos,
                        existing, onExisting }) {
  const [job, setJob] = useState(null);
  const [url, setUrl] = useState("");
  const [over, setOver] = useState(false);
  const [addStrat, setAddStrat] = useState(strat || "");   // "" = no strategy
  useEffect(() => { setAddStrat(strat || ""); }, [strat]); // follow the picked run's strat
  const fileRef = useRef(null);
  const pollRef = useRef(null);
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  async function startImport(source_kind, source_ref, name, useStrat) {
    const r = await send("POST", "/api/compare/import",
      { entity_key: entity, strat: useStrat != null ? useStrat : addStrat,
        name, source_kind, source_ref });
    pollJob(r.job_id);
  }
  async function startUpload(file) {
    const q = new URLSearchParams({ entity_key: entity, strat: addStrat,
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
        if (s.state === "done") { clearInterval(pollRef.current); pollRef.current = null; setJob(null); onAdded(s.comparison && s.comparison.id); }
        else if (s.state === "error") { clearInterval(pollRef.current); pollRef.current = null; }
      } catch { clearInterval(pollRef.current); pollRef.current = null; }
    }, 800);
  }
  function onDrop(e) {
    e.preventDefault(); setOver(false);
    const f = e.dataTransfer.files[0];
    if (f) startUpload(f);
  }

  if (!entity)
    return html`<div class="compare-empty meta">Pick a run from the list below to add comparison videos.</div>`;
  const busy = job && job.state === "running";
  return html`<div class="compare-drop ${over ? "over" : ""}"
      ondragover=${(e) => { e.preventDefault(); setOver(true); }}
      ondragleave=${() => setOver(false)} ondrop=${onDrop}>
    ${busy
      ? html`<div class="cd-inner"><div class="cd-icon">⏳</div>
          <div class="meta">loading… ${Math.round((job.progress || 0) * 100)}% ${job.message || ""}</div></div>`
      : html`<div class="cd-inner">
          <div class="cd-strat meta">Strategy:
            <${StrategySelect} strategies=${strategies} value=${addStrat} onChange=${setAddStrat} /></div>
          <div class="cd-icon"><${Icon} name="upload" size=${28} /></div>
          <div>Drag & drop ${hasVideos ? "another" : "a"} video here</div>
          <div class="meta">or</div>
          <div class="cd-actions">
            <button onclick=${() => fileRef.current && fileRef.current.click()}>
              <${Icon} name="upload" size=${15} /> Browse files
            </button>
            ${suggestion && html`<button onclick=${() =>
              startImport(suggestion.source_kind, suggestion.source_ref, suggestion.name, suggestion.strat)}>
              ▸ Load ${suggestion.name}</button>`}
          </div>
          <div class="cd-url">
            <input placeholder="paste a YouTube URL" value=${url}
              oninput=${(e) => setUrl(e.target.value)} />
            <button disabled=${!url} onclick=${() => startImport("youtube", url, url)}>Load video</button>
          </div>
          ${/* always present so you can keep loading more; empty = no options */""}
          <div class="cd-existing meta">or load a saved comparison:
            <select disabled=${!(existing && existing.length)}
                onchange=${(e) => { const v = e.target.value; e.target.value = "";
                  const it = (existing || []).find((x) => String(x.id) === v);
                  if (it) onExisting(it); }}>
              <option value="">${existing && existing.length ? "— pick one —" : "— none saved —"}</option>
              ${(existing || []).map((l) => html`<option value=${l.id}>${l.name || "video"}${l.strat && l.strat !== strat ? ` (${l.strat})` : ""}</option>`)}
            </select></div>
          ${job && job.state === "error" && html`<div class="badx">import failed: ${job.message}</div>`}
        </div>`}
    <input type="file" accept="video/*" style="display:none" ref=${fileRef}
      onchange=${(e) => { const f = e.target.files[0]; if (f) startUpload(f); }} />
  </div>`;
}

// ---- transport -------------------------------------------------------------
function Transport({ controller }) {
  return html`<div class="compare-transport">
    <button onclick=${() => controller.toStart()} title="Jump to beginning">
      <${Icon} name="restart" size=${16} /> Start
    </button>
    <button onclick=${() => controller.step(-1)} title="Back one frame">
      <${Icon} name="stepBack" size=${16} /> Back 1
    </button>
    <button onclick=${() => controller.playing ? controller.pause() : controller.play()}
      class="primary-transport">
      <${Icon} name=${controller.playing ? "pause" : "play"} size=${17} />
      ${controller.playing ? "Pause" : "Play"}
    </button>
    <button onclick=${() => controller.step(1)} title="Forward one frame">
      <${Icon} name="stepForward" size=${16} /> Forward 1
    </button>
  </div>`;
}

export function Compare({ t, intent, clearIntent, active }) {
  const controller = useSyncController();
  const [view, setView] = useState(null);          // lifetime session view
  const [availSet, setAvailSet] = useState(null);  // replayable attempt ids (null = loading)
  const [entity, setEntity] = useState(null);
  const [strat, setStrat] = useState(null);
  const [attemptId, setAttemptId] = useState(null);
  const [cmp, setCmp] = useState({ saved: [], suggestion: null, library: [] });
  const [openSet, setOpenSet] = useState(() => new Set());   // comparison ids shown now
  const [myIn, setMyIn] = useState(0);             // My Run work-area (in-memory)
  const [myOut, setMyOut] = useState(null);
  const initialized = useRef(false);
  // A {entity, strat, ids} the Library's "Study in Compare" intent asked to
  // have OPEN, held here until entity/strat state genuinely catch up with
  // it -- see the dependency-less effect below for why this can't just call
  // openComp from the intent effect directly (task-6 fix round 1, CRITICAL:
  // Study imported clips and left the pane showing none of them).
  const pendingOpenRef = useRef(null);
  const autoLoaded = useRef(new Set());            // combos whose default was auto-opened this mount
  // combos where the user CLOSED the rank-standard default (opt-out, persisted)
  const rankOff = useRef(null);
  if (rankOff.current === null) {
    try { rankOff.current = new Set(JSON.parse(localStorage.getItem("sm64.compareRankOff")) || []); }
    catch { rankOff.current = new Set(); }
  }

  // (re)load the feed + replayable-run set. We do NOT pick a default run/entity:
  // on a fresh load the right side stays empty until YOU pick a run. Refreshed
  // when the tab becomes active AND whenever the store sees a new run
  // (t.view changes) — so the feed updates in REAL TIME as you play. This never
  // touches entity/strat/openSet, so the loaded comparison is left alone.
  const refreshFeed = () => {
    getJSON("/api/session?scope=lifetime").then(setView).catch(() => {});
    getJSON("/api/replay/available")
      .then((r) => setAvailSet(new Set(r.available)))
      .catch(() => setAvailSet(new Set()));
  };
  useEffect(() => {
    if (!active) return;
    initialized.current = true;
    refreshFeed();
  }, [active]);
  useEffect(() => {
    if (active && initialized.current) refreshFeed();
  }, [t.view]);

  // deep-link intent (Compare button from Practice, or the Library's "Study
  // in Compare" fold-in) — sets entity+strat+run. `openIds` (Study only) is
  // NOT opened here: entity/strat state have not caught up yet on this same
  // render (setEntity/setStrat only take effect on the NEXT one), so opening
  // now would persist under the OLD combo's storage key -- stash it and let
  // the dependency-less effect below (after openComp exists) consume it once
  // entity/strat genuinely equal what this intent asked for.
  useEffect(() => {
    if (!intent) return;
    setEntity(intent.entity); setStrat(intent.strat); setAttemptId(intent.attemptId);
    if (intent.openIds && intent.openIds.length)
      pendingOpenRef.current = { entity: intent.entity, strat: intent.strat || null, ids: intent.openIds };
    clearIntent();
  }, [intent]);

  // My Run's start/end persist PER ATTEMPT (localStorage) — configure a run once
  // and it's restored (the playhead opens at the saved start) on every reload.
  useEffect(() => {
    if (attemptId == null) { setMyIn(0); setMyOut(null); return; }
    let sync = { in: 0, out: null };
    try {
      const raw = localStorage.getItem(`sm64.compareRunSync.${attemptId}`);
      if (raw) { const p = JSON.parse(raw); sync = { in: p.in || 0, out: p.out == null ? null : p.out }; }
    } catch {}
    setMyIn(sync.in); setMyOut(sync.out);
  }, [attemptId]);
  function saveMyRunSync(i, o) {
    setMyIn(i); setMyOut(o);
    if (attemptId != null) {
      try {
        localStorage.setItem(`sm64.compareRunSync.${attemptId}`, JSON.stringify({ in: i, out: o }));
      } catch {}
    }
  }

  const reloadCmp = () => {
    if (!entity) return;
    getJSON(`/api/compare/view?entity=${encodeURIComponent(entity)}`
      + (strat ? `&strat=${encodeURIComponent(strat)}` : ""))
      .then(setCmp).catch(() => {});
  };
  useEffect(reloadCmp, [entity, strat]);

  // The OPEN SET (which saved comparisons are shown) is remembered per combo in
  // localStorage: opening a run reloads the last set you had open; closing (×)
  // keeps the comparison saved so you can re-open it from "load existing".
  const comboKey = entity ? `${entity}|${strat || ""}` : null;
  const openKey = comboKey ? `sm64.compareOpen.${comboKey}` : null;
  useEffect(() => {
    if (!openKey) { setOpenSet(new Set()); return; }
    let ids = [];
    try { const raw = localStorage.getItem(openKey); if (raw) ids = JSON.parse(raw); } catch {}
    setOpenSet(new Set(ids));
  }, [openKey]);
  const persistOpen = (next) => {
    if (openKey) { try { localStorage.setItem(openKey, JSON.stringify([...next])); } catch {} }
  };
  const dismissRank = (k) => {                       // remember the opt-out across sessions
    rankOff.current.add(k);
    try { localStorage.setItem("sm64.compareRankOff", JSON.stringify([...rankOff.current])); } catch {}
  };
  const openComp = (id) => { if (id == null) return;
    setOpenSet((prev) => { const n = new Set(prev); n.add(id); persistOpen(n); return n; }); };
  const closeComp = (id) => {
    setOpenSet((prev) => { const n = new Set(prev); n.delete(id); persistOpen(n); return n; });
    // closing the rank-standard video = opting OUT of the default for this combo
    const c = cmp.saved.find((x) => x.id === id);
    if (c && cmp.rank_source && c.source_ref === cmp.rank_source && comboKey) dismissRank(comboKey);
  };

  // Flushes `pendingOpenRef` (Study in Compare's own imports) the moment
  // entity/strat state genuinely equal what it asked for. Runs on EVERY
  // render rather than a dependency array naming `openKey`/`entity`/`strat`
  // on purpose: Study routing to the SAME combo already on screen sets
  // entity/strat to their OWN current value, which Preact bails on with no
  // re-render at all, so an effect keyed on "did openKey change" would never
  // fire again and the freshly-imported ids would sit unopened forever. A
  // ref check + early return costs nothing on the renders that don't match.
  // Declared AFTER the open-set-from-localStorage effect above so a genuine
  // combo SWITCH always sees that restore run first on the settling render
  // and never gets its newly-opened ids clobbered by it.
  useEffect(() => {
    const pending = pendingOpenRef.current;
    if (!pending) return;
    if (pending.entity !== entity || pending.strat !== (strat || null)) return;
    pendingOpenRef.current = null;
    pending.ids.forEach(openComp);
  });

  // shared import + poll (rank-standard auto-load below); OPENS the new comparison
  function importAndReload(source_kind, source_ref, name, useStrat) {
    send("POST", "/api/compare/import",
      { entity_key: entity, strat: useStrat != null ? useStrat : (strat || ""),
        name, source_kind, source_ref })
      .then((r) => {
        const id = setInterval(async () => {
          try {
            const s = await getJSON(`/api/compare/import/${r.job_id}`);
            if (s.state === "done") { clearInterval(id);
              if (s.comparison) openComp(s.comparison.id); reloadCmp(); }
            else if (s.state === "error") clearInterval(id);
          } catch { clearInterval(id); }
        }, 800);
      }).catch(() => {});
  }
  // Rank-standard is OPT-OUT: whenever a combo has nothing open and its strategy
  // has a rank-standard example, open it by DEFAULT (open the saved copy, or
  // import it if not saved yet). Closing that video opts out (persisted in
  // rankOff); a combo with other videos open is left alone. autoLoaded guards
  // the async import gap so it fires once per combo per mount.
  useEffect(() => {
    if (!comboKey || !cmp.rank_source) return;
    if (cmp.entity !== entity || (cmp.strat || "") !== (strat || "")) return;  // stale cmp
    if (openSet.size > 0) return;                    // something already open
    if (rankOff.current.has(comboKey)) return;       // user opted out of the default
    if (autoLoaded.current.has(comboKey)) return;    // don't retrigger mid-import
    autoLoaded.current.add(comboKey);
    const saved = cmp.saved.find((c) => c.source_ref === cmp.rank_source);
    if (saved) openComp(saved.id);
    else importAndReload("youtube", cmp.rank_source, `${strat || ""} — rank standard`, strat);
  }, [cmp, entity, strat, comboKey, openSet]);

  async function editCmp(id, pts) {
    await send("PUT", `/api/compare/videos/${id}`, pts);   // auto-save in/out
    reloadCmp();
  }
  async function adoptExisting(sourceId) {                  // pull an other-strat video in
    const row = await send("POST", "/api/compare/adopt", { source_id: sourceId, strat: strat || "" });
    if (row && row.id != null) openComp(row.id);
    reloadCmp();
  }
  // picking a run from the feed sets its entity + strategy (so the matching
  // comparison auto-loads) + the run itself
  // pick a run: set its entity + strategy + the run. Same (entity, strategy) as
  // already loaded → setEntity/setStrat are no-ops (unchanged values), so the
  // comparison's reload/openSet effects don't fire — the right side is untouched.
  function pickRun(ent, s, aid) {
    setEntity(ent);
    if (s !== undefined) setStrat(s || null);
    setAttemptId(aid == null ? null : aid);
  }

  if (!view) return html`<${PageState} kind=${t.connected ? "loading" : "offline"}
      title="Preparing your comparison workspace" />`;
  const suggestion = cmp.suggestion || null;
  const shown = cmp.saved.filter((c) => openSet.has(c.id));         // only the OPEN set
  // "load existing": your saved comparisons that aren't currently open — this
  // strat's closed ones (re-open), plus other strats' (adopt into this strat).
  const existing = [
    ...cmp.saved.filter((c) => !openSet.has(c.id))
      .map((c) => ({ id: c.id, name: c.name, strat: c.strat, adopt: false })),
    ...cmp.library.map((c) => ({ id: c.id, name: c.name, strat: c.strat, adopt: true })),
  ];
  const onExisting = (item) => item.adopt ? adoptExisting(item.id) : openComp(item.id);
  // the entity's strategies (for the per-video + add-zone dropdowns)
  const curSec = [...(view.stars || []), ...(view.segments || [])]
    .find((s) => entityOf(s) === entity);
  const entityStrategies = (curSec && curSec.strategies) || [];

  return html`<div class="workshop-page compare-page">
    <header class="practice-card workshop-hero">
      <div class="workshop-title">
        <span class="workshop-title-icon"><${Icon} name="compare" size=${22} /></span>
        <div>
          <span class="eyebrow">Review</span>
          <h2>Compare</h2>
          <p>Line up your attempt with a reference and inspect both on the same game frame.</p>
        </div>
      </div>
      <span class="compare-sync-note"><${Icon} name="clock" size=${16} /> 30 game frames / second</span>
    </header>
    ${/* Order (user request 2026-07-24): videos first so they're never cut
         off, bare playback controls right under them, run feed last. */""}
    <div class="compare-grid">
      <section class="practice-card compare-col compare-stage-card">
        <div class="compare-stage-heading">
          <div><span class="eyebrow">Left video</span><h3>My run</h3></div>
          ${attemptId != null ? html`<span class="count-badge">#${attemptId}</span>` : null}
        </div>
        <${MyRun} attemptId=${attemptId} controller=${controller}
          inFrame=${myIn} outFrame=${myOut} onSync=${saveMyRunSync} />
      </section>
      <section class="practice-card compare-col compare-stage-card">
        <div class="compare-stage-heading cmp-head">
          <div><span class="eyebrow">Right video</span><h3>Comparison</h3></div>
          <${StrategySelect} strategies=${entityStrategies} value=${strat || ""}
            onChange=${(s) => setStrat(s || null)} />
        </div>
        ${shown.map((c) => html`<${ComparisonStage} key=${c.id} comp=${c}
          controller=${controller} onEdit=${editCmp} onDelete=${closeComp} />`)}
        <${AddComparison} entity=${entity} strat=${strat} strategies=${entityStrategies}
          suggestion=${suggestion} hasVideos=${shown.length > 0}
          onAdded=${(id) => { if (id != null) openComp(id); reloadCmp(); }}
          existing=${existing} onExisting=${onExisting} />
      </section>
    </div>
    <section class="practice-card compare-transport-card" aria-label="Playback controls">
      <${Transport} controller=${controller} />
    </section>
    <${StageFeed} view=${view} available=${availSet} attemptId=${attemptId} onPick=${pickRun} />
  </div>`;
}
