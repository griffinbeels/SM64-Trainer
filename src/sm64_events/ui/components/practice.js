// src/sm64_events/ui/components/practice.js
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { ReplayPlayer } from "./replay.js";
import { StatMenu, DUST_STAT_KEYS } from "./statmenu.js";
import { Timeline } from "./timeline.js";
import { Progress } from "./progress.js";
import { StageBanner } from "./stagebanner.js";
import { Medal, RankBanner, rankColor } from "./ranks.js";
import { StandardsPanel } from "./standards.js";
import { StratPicker } from "./stratpicker.js";
import { FailureCompilation } from "./failcomp.js";
import { Icon } from "./icons.js";
import { PageState } from "./states.js";

const html = htm.bind(h);

const OUTCOME_LABEL = { success: "✔", reset: "✘ reset",
  hard_reset: "✘ hard reset", abandoned: "– abandoned", death: "✘ death" };

const SORT_OPTIONS = [
  ["newest", "newest first"], ["oldest", "oldest first"],
  ["fastest", "fastest first"], ["slowest", "slowest first"]];

// Row time on the current clock: completion time for successes, how-far-in
// for failures. Nulls sort last in both directions.
function rowTime(a, clock) {
  return clock === "igt" ? a.igt_frames : a.rta_frames;
}
function comparator(sort, clock) {
  if (sort === "oldest") return (a, b) => a.id - b.id;
  if (sort === "fastest")
    return (a, b) => (rowTime(a, clock) ?? Infinity) - (rowTime(b, clock) ?? Infinity);
  if (sort === "slowest")
    return (a, b) => (rowTime(b, clock) ?? -Infinity) - (rowTime(a, clock) ?? -Infinity);
  return (a, b) => b.id - a.id; // newest (default)
}

// New-entry blink: attempt ids first seen AFTER the initial view load get
// .row-new (three gold pulses, ~2.4s) so the row that just landed is
// unmissable. The first view after mount and the first after a
// session↔lifetime flip are absorbed silently — those bring in OLD
// attempts, not new entries. Expiry is real state, not just animation-end:
// keyed reorders re-insert the <tr>, which replays any animation class
// still present on it.
function useFreshAttemptIds(t) {
  const [freshIds, setFreshIds] = useState(() => new Set());
  const base = useRef(null);            // { scope, ids } — every id ever seen
  useEffect(() => {
    const v = t.view;
    if (!v) return;
    const ids = [
      ...v.stars.flatMap((s) => s.attempts),
      ...(v.segments || []).flatMap((s) => s.attempts),
      ...v.unassigned,
    ].map((a) => a.id);
    if (!base.current || base.current.scope !== t.scope) {
      base.current = { scope: t.scope, ids: new Set(ids) };
      return;
    }
    const fresh = ids.filter((id) => !base.current.ids.has(id));
    if (fresh.length === 0) return;
    fresh.forEach((id) => base.current.ids.add(id));
    setFreshIds((prev) => new Set([...prev, ...fresh]));
    setTimeout(() => setFreshIds((prev) => {   // per-batch timer — an effect
      const next = new Set(prev);              // cleanup would cancel this
      fresh.forEach((id) => next.delete(id));  // batch's expiry whenever the
      return next;                             // next view lands within 2.6s
    }), 2600);
  }, [t.view]);
  return freshIds;
}

function delta(frames) {
  if (frames === null || frames === undefined) return "";
  const cls = frames > 0 ? "delta-up" : "delta-down";
  const sign = frames > 0 ? "+" : "";
  return html` <span class=${cls}>${sign}${(frames / 30).toFixed(2)}s</span>`;
}

function AttemptRow({ a, t, idx, focus, clearFocus, isNew, openCompare, sec }) {
  const [showReplay, setShowReplay] = useState(false);
  const [flash, setFlash] = useState(false);
  const rowRef = useRef(null);
  // Progress-graph pick (see useGraphPick): when this row is
  // the focused one, scroll it into view, flash it, and — when the pick
  // says a saved replay file exists — open the player exactly as if the
  // ▶ button was pressed. Keyed on the nonce so re-clicking the same node
  // works after the user closed the player; runs on mount too, which is
  // what makes a row revealed by the pagination bump handle its own pick.
  useEffect(() => {
    if (!focus || focus.id !== a.id) return;
    if (focus.openReplay) setShowReplay(true);
    requestAnimationFrame(() => {
      if (rowRef.current)
        rowRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
    });
    setFlash(true);
    const timer = setTimeout(() => setFlash(false), 1600);
    if (clearFocus) clearFocus(); // one pick = one handling; later remounts must not re-fire
    return () => clearTimeout(timer);
  }, [focus && focus.nonce]);
  async function clear() {
    await send("POST", `/api/attempts/${a.id}/clear`, { reason: "accidental" });
    t.refresh();
  }
  async function restore() {
    await send("POST", `/api/attempts/${a.id}/restore`);
    t.refresh();
  }
  // Segment attempts are RTA-only (igt is null; the server rejects igt PB
  // saves with "segments are RTA-only") — force rta whatever the view clock.
  const isSeg = a.segment_id != null;
  async function savePb() {
    await send("POST", "/api/pb",
      { attempt_id: a.id, timer_mode: isSeg ? "rta" : t.clock });
    t.refresh();
  }
  async function undoPb() {
    await send("POST", "/api/pb/undo",
      { attempt_id: a.id, timer_mode: isSeg ? "rta" : t.clock });
    t.refresh();
  }
  const time = isSeg ? a.rta : (t.clock === "igt" ? a.igt : a.rta);
  const frames = isSeg ? a.rta_frames : (t.clock === "igt" ? a.igt_frames : a.rta_frames);
  const inTime = isSeg ? a.rta : a.igt; // failures: how-far-in on the section's clock
  // Glow when saving would set a new PB: beats the recorded PB, or no PB
  // exists yet. frames > 0 excludes same-tick race rows (rta=0 junk) whose
  // "PB" would be meaningless.
  const pbBeat = a.outcome === "success" && !a.cleared
    && frames != null && frames > 0
    && (a.pb_delta_frames === null || a.pb_delta_frames < 0);
  // Star rows don't carry course/star on the attempt itself (_attempt_json
  // omits them) — derive the entity from the section for stars, from the
  // attempt for segments.
  const entity = a.segment_id != null ? `segment:${a.segment_id}`
    : (sec ? `star:${sec.course_id}:${sec.star_id}` : null);
  const strat = a.strat_tag || (sec && sec.last_strat) || null;
  const row = html`<tr ref=${(el) => { rowRef.current = el; }}
      class="${a.cleared ? "cleared" : ""} ${flash ? "row-flash" : ""} ${isNew ? "row-new" : ""}">
    <td class="meta attempt-index">#${idx + 1}</td>
    <td class="attempt-medal">${a.rank
      ? html`<${Medal} rank=${a.rank} size=${22} />` : ""}</td>
    <td class="attempt-result ${a.outcome === "success" ? "good" : "badx"}">
      ${OUTCOME_LABEL[a.outcome] || a.outcome}
      ${a.outcome === "death" && a.outcome_detail
        ? html` <span class="meta">(${a.outcome_detail})</span>` : ""}
      ${a.outcome === "success" && time ? html` <b>${time}</b>` : ""}
      ${a.outcome !== "success" && inTime ? html` <span class="meta">${inTime} in</span>` : ""}
      ${t.showDust && a.rollouts_total > 0
        ? html` <span class="meta">· ${a.rollouts_dustless}/${a.rollouts_total} dustless rollouts</span>` : ""}
      ${t.showDust && a.jumps_total > 0
        ? html` <span class="meta">· ${a.jumps_dustless}/${a.jumps_total} dustless jumps</span>` : ""}
      ${a.cleared && a.cleared_reason
        ? html` <span class="meta">(${a.cleared_reason})</span>` : ""}
    </td>
    <td class="attempt-delta">${a.outcome === "success" ? delta(a.pb_delta_frames) : ""}</td>
    <td class="meta attempt-strategy">
      ${sec
        ? html`<${StratPicker} entity=${entity} strategies=${sec.strategies}
            active=${a.strat_tag} blankLabel="— no strategy —"
            highlightUnset=${false}
            submit=${(tag) => send("POST", `/api/attempts/${a.id}/strat`,
                                   { strat_tag: tag })}
            onChanged=${t.refresh} />`
        : html`<span>${a.strat_tag || "— no strategy —"}</span>`}
    </td>
    <td class="attempt-actions">
      <button class="icon-button" onclick=${() => setShowReplay(!showReplay)}
          title="View replay" aria-label="View replay">
        <${Icon} name=${showReplay ? "chevron" : "play"} size=${16} /></button>
      ${a.outcome === "success" && !a.cleared
        ? (a.is_current_pb
          ? html` <button onclick=${undoPb}
              title="delete this save — the previous PB becomes current again">Undo PB</button>`
          : html` <button class=${pbBeat ? "pb-glow" : ""} onclick=${savePb}>
              <${Icon} name="bookmark" size=${14} />
              <span class="save-pb-wide">Save as PB</span>
              <span class="save-pb-narrow">Save PB</span></button>`)
        : ""}
      ${a.cleared
        ? html` <button onclick=${restore}>undo</button>`
        : html` <button class="icon-button" onclick=${clear}
            title="Clear this attempt as a mistake" aria-label="Clear attempt">×</button>`}
    </td>
  </tr>`;
  const onCompare = (openCompare && entity)
    ? () => openCompare({ attemptId: a.id, entity, strat })
    : null;
  const expandedRow = showReplay
    ? html`<tr class="replay-row"><td colspan="6"><${ReplayPlayer} attemptId=${a.id} onCompare=${onCompare} /></td></tr>`
    : null;
  return [row, expandedRow];
}

// Shared table component used by both StarSection and the unassigned block.
// attempts: the full ordered list for stable numbering;
// rows: the filtered/sorted subset to actually render.
function AttemptTable({ attempts, rows, t, focus, clearFocus, freshIds, openCompare, sec }) {
  return html`<table class="attempt-table">
    ${rows.map((a) => {
      const idx = attempts.indexOf(a);
      return html`<${AttemptRow} key=${a.id} a=${a} t=${t} idx=${idx}
        focus=${focus} clearFocus=${clearFocus}
        isNew=${freshIds ? freshIds.has(a.id) : false}
        openCompare=${openCompare} sec=${sec} />`;
    })}
  </table>`;
}

function HideToggle({ hidden, showHidden, setShowHidden }) {
  if (hidden.length === 0) return null;
  return html`<button class="meta"
      style="background:none;border:none;cursor:pointer"
      onclick=${() => setShowHidden(!showHidden)}>
    ${showHidden ? "hide" : "show"} ${hidden.length} hidden
  </button>`;
}

// Progress-graph pick, shared by StarSection and SegmentSection (and the
// clickable PB tag below). Reveal an attempt's row — bump pagination if it's
// past the fold — then scroll to it, flash it, and auto-open its replay when a
// saved file exists (HEAD existence probe). Graph points and the PB attempt are
// always non-cleared successes, which no list filter removes, so they live in
// `rows` whenever they're in scope; a pick whose attempt is out of scope (e.g.
// a PB from an earlier session, viewed in session scope) is held as pending and
// revealed when a later view brings the row in — the PB tag switches to
// lifetime scope to make that happen.
function useGraphPick(rows, visible, setVisible) {
  const [focus, setFocus] = useState(null);
  const pickNonce = useRef(0);
  // pendingId: an attempt picked while it wasn't (yet) in `rows` — e.g. the PB
  // tag jumping to an out-of-scope PB, which first switches scope to lifetime.
  // Claimed synchronously in pick() (before the async replay probe) so the
  // scope refetch can't re-render past us; the effect below reveals it once the
  // new view brings the row in.
  const pendingId = useRef(null);
  async function reveal(attemptId) {
    // Membership first: bail before the replay probe when the row isn't loaded
    // (an out-of-scope PB before its scope switch lands), so no wasted request.
    const idx = rows.findIndex((a) => a.id === attemptId);
    if (idx === -1) return false;
    if (idx >= visible) setVisible(Math.ceil((idx + 1) / 10) * 10);
    let openReplay = false;
    try {
      openReplay = (await fetch(`/api/replay/saved/${attemptId}`,
                                { method: "HEAD" })).ok;
    } catch { /* probe is best-effort: still scroll + flash */ }
    setFocus({ id: attemptId, nonce: ++pickNonce.current, openReplay });
    return true;
  }
  async function pick(attemptId) {
    pendingId.current = attemptId;                    // claim before any await
    if (await reveal(attemptId)) pendingId.current = null;
  }
  // A pick whose attempt wasn't in `rows` waits here until a new view brings it
  // in (the PB tag switching scope to lifetime), then reveals it.
  useEffect(() => {
    if (pendingId.current == null) return;
    if (!rows.some((a) => a.id === pendingId.current)) return;
    const id = pendingId.current;
    pendingId.current = null;
    reveal(id);
  }, [rows]);
  return { focus, pick, clearFocus: () => setFocus(null) };
}

// PB tag: always a clickable jump to the PB's attempt row — same reveal path as
// its gold progress-graph dot (scroll, flash, open saved replay). When the PB
// is out of the current scope (a lifetime PB from an earlier session, viewed in
// session scope) its row isn't loaded, so clicking first switches to lifetime
// scope; pick() holds the request until the lifetime view brings the row in.
// `mode` is just the clock label shown in parens.
function PbTag({ pb, mode, rows, pick, t }) {
  if (!pb) return html`<span class="pbtag">no PB yet</span>`;
  function jump() {
    if (!pick) return;
    if (!rows.some((a) => a.id === pb.attempt_id) && t.scope !== "lifetime")
      t.pickScope("lifetime");
    pick(pb.attempt_id);
  }
  return html`<span class="pbtag">PB ${pick
    ? html`<a class="pblink" onclick=${jump}
        title="jump to this PB in the list below">${pb.display}</a>`
    : pb.display} (${mode})</span>`;
}

// Validity-bounds chip (spec 2026-07-23): the section's effective min/max
// completion time — successes outside the range are auto-ignored server-side
// (auto-cleared into the hidden bucket; stats/PBs/graphs/runs skip them).
// Dimmed while on the implicit 0.5s default. Edited in SECONDS, stored as
// frames (x30). Stars persist via PUT/DELETE /api/stars/{c}/{s}/time-filter;
// segments rewrite their def's min_time/max_time guard rows through
// PUT /api/segments/{id} — both paths reproject, so history reflags
// immediately. Blank min = the 0.5s default; typed 0 = no minimum; blank
// max = no max.
function TimeFilterChip({ sec, t }) {
  const [open, setOpen] = useState(false);
  const [minS, setMinS] = useState("");
  const [maxS, setMaxS] = useState("");
  const tf = sec.time_filter;
  if (!tf) return null;
  const isSeg = sec.segment_id != null;
  const fmtS = (f) => (f % 30 === 0 ? String(f / 30) : (f / 30).toFixed(2));
  const label = tf.max_frames != null
    ? `⏱ ${fmtS(tf.min_frames)}–${fmtS(tf.max_frames)}s`
    : `⏱ ≥ ${fmtS(tf.min_frames)}s`;

  function openEditor() {
    setMinS(fmtS(tf.min_frames));
    setMaxS(tf.max_frames != null ? fmtS(tf.max_frames) : "");
    setOpen(true);
  }

  async function putSegGuards(minF, maxF) {
    // RMW the def's guard list: time rows replaced, other guards untouched
    const defs = await getJSON("/api/segments");
    const d = defs.find((x) => x.id === sec.segment_id);
    if (!d) return;
    const guards = (d.guards || []).filter(
      (g) => g.type !== "min_time" && g.type !== "max_time");
    if (minF != null) guards.push({ type: "min_time", frames: minF });
    if (maxF != null) guards.push({ type: "max_time", frames: maxF });
    await send("PUT", `/api/segments/${sec.segment_id}`, { guards });
  }

  async function save() {
    const minF = minS === "" ? null : Math.round(Number(minS) * 30);
    const maxF = maxS === "" ? null : Math.round(Number(maxS) * 30);
    if (isSeg) await putSegGuards(minF, maxF);
    // 15 mirrors projection.DEFAULT_MIN_FRAMES (blank min = keep the default)
    else await send("PUT",
      `/api/stars/${sec.course_id}/${sec.star_id}/time-filter`,
      { min_frames: minF == null ? 15 : minF, max_frames: maxF });
    setOpen(false);
    t.refresh();
  }

  async function reset() {
    if (isSeg) await putSegGuards(null, null);
    else await send("DELETE",
      `/api/stars/${sec.course_id}/${sec.star_id}/time-filter`);
    setOpen(false);
    t.refresh();
  }

  if (!open) return html`<button class="meta" style=${tf.is_default ? "opacity:.55" : ""}
      title="valid-time bounds — successes outside this range are ignored"
      onclick=${openEditor}>${label}</button>`;
  return html`<span class="meta">
    min <input type="number" min="0" step="0.1" style="width:4rem"
      value=${minS} oninput=${(e) => setMinS(e.target.value)} />s
    max <input type="number" min="0" step="0.1" style="width:4rem"
      value=${maxS} placeholder="∞" oninput=${(e) => setMaxS(e.target.value)} />s
    <button onclick=${save}>save</button>
    <button onclick=${reset} title="back to the 0.5s default">reset</button>
    <button onclick=${() => setOpen(false)}>cancel</button>
  </span>`;
}

function StarSection({ sec, t, ui, pinned, freshIds, openCompare }) {
  const [showHidden, setShowHidden] = useState(false);
  const [visible, setVisible] = useState(10);
  const pb = sec.pb[t.clock];
  const base = showHidden ? sec.attempts
    : sec.attempts.filter((a) => !a.cleared && a.outcome !== "abandoned");
  const hidden = sec.attempts.filter((a) => a.cleared || a.outcome === "abandoned");
  const rows = base
    .filter((a) => !(ui.hideResets
      && (a.outcome === "reset" || a.outcome === "hard_reset")))
    .slice()
    .sort(comparator(ui.sort, t.clock));
  const shown = rows.slice(0, visible);
  const { focus, pick, clearFocus } = useGraphPick(rows, visible, setVisible);

  async function wipeData() {
    const name = `${sec.course_name} · ${sec.star_name}`;
    const msg = t.scope === "lifetime"
      ? `Wipe ALL data for ${name} across every session?\n`
        + "All attempts and PBs for this star are permanently removed "
        + "(markers and strategies are kept).\nThis cannot be undone."
      : `Wipe this session's data for ${name}?\n`
        + "The session's attempts and any PBs saved from them are "
        + "permanently removed (earlier PBs are kept).\nThis cannot be undone.";
    if (!window.confirm(msg)) return;
    await send("POST", "/api/wipe", { kind: "star", course_id: sec.course_id,
                                      star_id: sec.star_id, scope: t.scope });
    t.refresh();
  }

  return html`<div class="practice-detail-grid ${pinned ? "is-primary" : ""}">
    <section class="practice-card objective-card ${pinned ? "active-star" : ""}">
      <div class="objective-heading">
        <span class="objective-symbol"><${Icon} name="target" size=${20} /></span>
        <span class="eyebrow">${pinned ? "Active target" : "Star practice"}</span>
        <div class="objective-name" title=${`${sec.course_name} · ${sec.star_name}`}>
          <span class="objective-context">${sec.course_name}</span>
          <h2>${sec.star_name}</h2>
        </div>
        <div class="objective-strategy">
          <span class="field-label">Strategy</span>
          <${StratPicker} entity=${`star:${sec.course_id}:${sec.star_id}`}
              identity=${{ course_id: sec.course_id, star_id: sec.star_id }}
              strategies=${sec.strategies} active=${sec.last_strat}
              onChanged=${t.refresh} />
        </div>
      </div>
      <div class="objective-metrics" style=${sec.rank && sec.rank.rank
          ? `--rank-glow:${rankColor(sec.rank.rank)}` : ""}>
        <div class="rank-slot"><${RankBanner} banner=${sec.rank} /></div>
        <div class="objective-live-state" aria-label="Practice state">
          <span class="live-state-icon">○</span><span>Ready</span>
        </div>
        <${PbTag} pb=${pb} mode=${t.clock} rows=${rows} pick=${pick} t=${t} />
      </div>
    </section>

    <section class="practice-card analysis-card">
      <div class="card-heading">
        <div><span class="eyebrow">Analysis</span><h3>Attempt history</h3></div>
      </div>
      <div class="analysis-block timeline-block">
        <h4>Attempt timeline <span class="hint" tabindex="0"
          data-tip="Every attempt in the selected scope, positioned by its completion or reset time">ⓘ</span></h4>
        <${Timeline} tl=${sec.timeline} sec=${sec} t=${t} />
      </div>
      <div class="analysis-block trend-block">
        <h4>Performance trend <span class="hint" tabindex="0"
          data-tip="Successful attempts over time — gold dots are saved PBs; click a dot to jump to its row">ⓘ</span></h4>
        <${Progress} prog=${sec.progress} clock=${t.clock} onPick=${pick} />
      </div>
    </section>

    <section class="practice-card attempts-card">
      <div class="card-heading attempts-heading">
        <div><span class="eyebrow">Practice log</span><h3>Recent attempts</h3></div>
        <div class="attempts-tools">
          <span class="meta">${rows.length} shown</span>
          <${SortControl} ui=${ui} />
        </div>
      </div>
      <div class="attempt-scroll">
        <${AttemptTable} attempts=${sec.attempts} rows=${shown} t=${t}
          focus=${focus} clearFocus=${clearFocus} freshIds=${freshIds}
          openCompare=${openCompare} sec=${sec} />
      </div>
      <div class="attempt-footer">
        <div class="attempt-pagination">
          ${rows.length > visible && html`<button class="quiet-button"
              onclick=${() => setVisible(visible + 10)}>Show 10 more</button>`}
          ${visible > 10 && html`<button class="quiet-button"
              onclick=${() => setVisible(Math.max(10, visible - 10))}>Show fewer</button>`}
        </div>
        <div class="attempt-footer-tools">
          <${ResetFilterToggle} ui=${ui} />
          <${HideToggle} hidden=${hidden} showHidden=${showHidden}
              setShowHidden=${setShowHidden} />
        </div>
      </div>
    </section>

    <details class="practice-card detail-drawer" open>
      <summary>Stats, standards, and practice options</summary>
      <div class="detail-tools">
        <a href=${sec.links.ukikipedia} target="_blank">RTA Guide ↗</a>
        ${sec.links.example && html`<a href=${sec.links.example} target="_blank">Example ↗</a>`}
        <${TimeFilterChip} sec=${sec} t=${t} />
        <button class="danger-text" onclick=${wipeData}
          title=${t.scope === "lifetime"
            ? "Wipe this star's data across all sessions"
            : "Wipe this star's data in the current session"}>Clear data</button>
      </div>
      <div class="chips">
        ${sec.stats.filter((s) => t.showDust || !DUST_STAT_KEYS.has(s.key))
          .map((s) => html`
          <span class="chip" title=${s.key}>${s.label} ${s.display ?? "–"}</span>`)}
      </div>
      <${StandardsPanel} entity=${`star:${sec.course_id}:${sec.star_id}`}
          activeStrat=${sec.last_strat} strategies=${sec.strategies}
          onChanged=${t.refresh} defaultOpen=${true} />
      <${FailureCompilation} identity=${{ course_id: sec.course_id, star_id: sec.star_id }}
          defaultOpen=${true} />
    </details>
  </div>`;
}

// Segment sibling of StarSection — deliberately NOT a generalization:
// segments are RTA-only (igt is null everywhere) and have no links.
// Everything else must stay at feature parity with the star card; the shared
// pieces are components (StratPicker, PbTag, TimeFilterChip, …) so a feature
// can't land on one card and miss the other, and
// tests/test_ui_section_parity.py fails when it does.
// Broken sections (definition deleted, history remains) render but drop the
// timeline/marker editor and the strat picker — both key off the deleted
// definition (POST /api/strat 404s for a segment that no longer exists).
function SegmentSection({ sec, t, ui, pinned, freshIds, openCompare }) {
  const [showHidden, setShowHidden] = useState(false);
  const [visible, setVisible] = useState(10);
  // armedSegs is the single live source: WS notices are instant, every view
  // fetch reconciles it so it cannot stay stale — see store.js refresh().
  const armed = t.armedSegs.has(sec.segment_id);
  const tgt = (t.view && t.view.target) || {};
  const isTarget = tgt.kind === "segment" && tgt.segment_id === sec.segment_id;
  const base = showHidden ? sec.attempts
    : sec.attempts.filter((a) => !a.cleared && a.outcome !== "abandoned");
  const hidden = sec.attempts.filter((a) => a.cleared || a.outcome === "abandoned");
  const rows = base
    .filter((a) => !(ui.hideResets
      && (a.outcome === "reset" || a.outcome === "hard_reset")))
    .slice()
    .sort(comparator(ui.sort, "rta"));
  const shown = rows.slice(0, visible);
  const { focus, pick, clearFocus } = useGraphPick(rows, visible, setVisible);

  async function wipeData() {
    const msg = t.scope === "lifetime"
      ? `Wipe ALL data for ${sec.name} across every session?\n`
        + "All attempts and PBs for this segment are permanently removed "
        + "(the definition and markers are kept).\nThis cannot be undone."
      : `Wipe this session's data for ${sec.name}?\n`
        + "The session's attempts and any PBs saved from them are "
        + "permanently removed (earlier PBs are kept).\nThis cannot be undone.";
    if (!window.confirm(msg)) return;
    await send("POST", "/api/wipe", { kind: "segment",
                                      segment_id: sec.segment_id,
                                      scope: t.scope });
    t.refresh();
  }

  const pinTag = armed ? "Running" : isTarget ? "Ready" : "Recent";
  return html`<div class="practice-detail-grid ${pinned ? "is-primary" : ""}">
    <section class="practice-card objective-card ${pinned ? "active-star" : ""}">
      <div class="objective-heading">
        <span class="objective-symbol"><${Icon} name="segments" size=${20} /></span>
        <span class="eyebrow">${pinned ? "Active segment" : "Segment practice"}</span>
        <div class="objective-name" title=${sec.name}>
          <span class="objective-context">${sec.broken ? "History only" : "Segment"}</span>
          <h2>${sec.name}</h2>
        </div>
        <div class="objective-strategy">
          <span class="field-label">Strategy</span>
          ${!sec.broken
            ? html`<${StratPicker} entity=${`segment:${sec.segment_id}`}
                identity=${{ kind: "segment", segment_id: sec.segment_id }}
                strategies=${sec.strategies} active=${sec.last_strat}
                onChanged=${t.refresh} />`
            : html`<span class="meta">Definition deleted</span>`}
        </div>
      </div>
      <div class="objective-metrics" style=${sec.rank && sec.rank.rank
          ? `--rank-glow:${rankColor(sec.rank.rank)}` : ""}>
        <div class="rank-slot"><${RankBanner} banner=${sec.rank} /></div>
        <div class="objective-live-state ${armed ? "running" : ""}"
            aria-label=${`Segment state: ${pinTag}`}>
          <${Icon} name="clock" size=${17} /><span>${pinTag}</span>
        </div>
        <${PbTag} pb=${sec.pb.rta} mode="rta" rows=${rows} pick=${pick} t=${t} />
      </div>
    </section>

    <section class="practice-card analysis-card">
      <div class="card-heading">
        <div><span class="eyebrow">Analysis</span><h3>Attempt history</h3></div>
      </div>
      <div class="analysis-block timeline-block">
        <h4>Attempt timeline <span class="hint" tabindex="0"
          data-tip="Every attempt in the selected scope, positioned by its completion or reset time">ⓘ</span></h4>
        ${!sec.broken
          ? html`<${Timeline} tl=${sec.timeline} sec=${sec} t=${t} />`
          : html`<div class="stable-empty compact">Timeline unavailable for a deleted definition.</div>`}
      </div>
      <div class="analysis-block trend-block">
        <h4>Performance trend <span class="hint" tabindex="0"
          data-tip="Successful attempts over time — gold dots are saved PBs; click a dot to jump to its row">ⓘ</span></h4>
        <${Progress} prog=${sec.progress} clock="rta" onPick=${pick} />
      </div>
    </section>

    <section class="practice-card attempts-card">
      <div class="card-heading attempts-heading">
        <div><span class="eyebrow">Practice log</span><h3>Recent attempts</h3></div>
        <div class="attempts-tools">
          <span class="meta">${rows.length} shown</span>
          <${SortControl} ui=${ui} />
        </div>
      </div>
      <div class="attempt-scroll">
        <${AttemptTable} attempts=${sec.attempts} rows=${shown} t=${t}
          focus=${focus} clearFocus=${clearFocus} freshIds=${freshIds}
          openCompare=${openCompare} sec=${sec} />
      </div>
      <div class="attempt-footer">
        <div class="attempt-pagination">
          ${rows.length > visible && html`<button class="quiet-button"
              onclick=${() => setVisible(visible + 10)}>Show 10 more</button>`}
          ${visible > 10 && html`<button class="quiet-button"
              onclick=${() => setVisible(Math.max(10, visible - 10))}>Show fewer</button>`}
        </div>
        <div class="attempt-footer-tools">
          <${ResetFilterToggle} ui=${ui} />
          <${HideToggle} hidden=${hidden} showHidden=${showHidden}
              setShowHidden=${setShowHidden} />
        </div>
      </div>
    </section>

    <details class="practice-card detail-drawer" open>
      <summary>Stats, standards, and practice options</summary>
      <div class="detail-tools">
        ${!sec.broken && html`<${TimeFilterChip} sec=${sec} t=${t} />`}
        <button class="danger-text" onclick=${wipeData}
          title=${t.scope === "lifetime"
            ? "Wipe this segment's data across all sessions"
            : "Wipe this segment's data in the current session"}>Clear data</button>
      </div>
      <div class="chips">
        ${sec.stats.filter((s) => t.showDust || !DUST_STAT_KEYS.has(s.key))
          .map((s) => html`
          <span class="chip" title=${s.key}>${s.label} ${s.display ?? "–"}</span>`)}
      </div>
      <${StandardsPanel} entity=${`segment:${sec.segment_id}`}
          activeStrat=${sec.last_strat} strategies=${sec.strategies}
          onChanged=${t.refresh} defaultOpen=${true} />
      <${FailureCompilation} identity=${{ segment_id: sec.segment_id }}
          defaultOpen=${true} />
    </details>
  </div>`;
}

// --- Route Practice focus (Phase C) ---------------------------------------
// Non-destructive focus layer: when a route is active the Practice tab shows
// ONLY that route's members, in route order. The current-step pointer reads
// the live target; clicking a candidate sets the target (retry anything
// freely). Driven by the route view (GET /api/routes/{id}) for order + names +
// %s, cross-referenced to the session view for the current step's full section.
const fpct = (r) => `${Math.round((r ?? 0) * 100)}%`;

function candIsTarget(c, tgt) {
  return c.kind === "segment"
    ? (tgt.kind === "segment" && tgt.segment_id === c.segment_id)
    : (tgt.kind !== "segment" && tgt.course_id === c.course && tgt.star_id === c.star);
}

async function setTargetCandidate(c, t) {
  if (c.kind === "segment")
    await send("POST", "/api/target", { kind: "segment", segment_id: c.segment_id });
  else
    await send("POST", "/api/target", { course_id: c.course, star_id: c.star });
  t.refresh();
}

function RouteFocus({ rv, t, ui, freshIds, openCompare }) {
  const v = t.view;
  const tgt = v.target || {};
  // current = first step whose any candidate is the live target; else step 0
  // (the suggested start). next = the following step (badge only — advancing is
  // a suggestion; the target auto-follows completions, the user may click any
  // step to retry).
  let currentIdx = rv.steps.findIndex((s) =>
    s.candidates.some((c) => candIsTarget(c, tgt)));
  if (currentIdx === -1) currentIdx = 0;

  return html`<div>
    <div class="meta listhead">route — ${rv.name}</div>
    ${rv.steps.length === 0
      ? html`<p class="meta">This route has no steps yet — add some in the Routes tab.</p>`
      : null}
    ${rv.steps.map((s, i) => {
      const isCurrent = i === currentIdx;
      const badge = isCurrent
        ? html`<span class="chip routecur">▶ CURRENT</span>`
        : i === currentIdx + 1 ? html`<span class="chip">NEXT</span>` : null;
      return html`<div class="routefstep ${isCurrent ? "active-star" : ""}">
        <div class="shead">
          <span class="routenum">${i + 1}.</span>
          ${badge}
          ${s.candidates.length > 1
            ? html`<span class="chip">${s.need} of ${s.candidates.length}</span>` : null}
          ${s.label ? html`<b>${s.label}</b>` : null}
          ${s.candidates.map((c) => html`<button
              class=${candIsTarget(c, tgt) ? "pb-glow" : ""}
              onclick=${() => setTargetCandidate(c, t)}
              title="practice this">${c.display}</button>`)}
          <span style="flex:1"></span>
          ${s.rank ? html`<${Medal} rank=${s.rank} size=${16} />` : null}
          <span class="routerate">step ${fpct(s.step_rate)}</span>
          <span class="routecum">cum ${fpct(s.cumulative)}</span>
        </div>
      </div>`;
    })}
  </div>`;
}

// Practice-log controls live IN the log card they act on (2026-07-24 UX
// pass): sort in the card heading, reset visibility in the footer. They used
// to sit in an "analysis toolbar" under the charts, which read as controls
// for the graphs.
function SortControl({ ui }) {
  return html`<label class="sort-control" title="Order the practice log">
    <${Icon} name="sort" size=${16} /><span class="sr-only">Sort attempts</span>
    <select value=${ui.sort} onchange=${(e) => ui.setSort(e.target.value)}>
      ${SORT_OPTIONS.map(([k, label]) => html`<option value=${k}>${label}</option>`)}
    </select></label>`;
}

function ResetFilterToggle({ ui }) {
  return html`<label class="reset-toggle" title="Hide reset attempts from the log">
    <${Icon} name="eyeOff" size=${16} />
    <input type="checkbox" checked=${ui.hideResets}
           onchange=${(e) => ui.setHideResets(e.target.checked)} />
    <span>Hide resets</span></label>`;
}

function EmptyPractice({ v, t, ui, unassignedRows, freshIds, openCompare,
                         hidden, showHidden, setShowHidden }) {
  return html`<div class="practice-detail-grid is-primary">
    <section class="practice-card objective-card objective-empty">
      <div class="objective-heading">
        <span class="objective-symbol"><${Icon} name="target" size=${20} /></span>
        <span class="eyebrow">Active objective</span>
        <div class="objective-name">
          <span class="objective-context">Waiting for a target</span>
          <h2>No active objective</h2>
        </div>
      </div>
      <div class="objective-metrics">
        <div class="rank-slot stable-empty compact">Rank —</div>
        <div class="objective-live-state"><span class="live-state-icon">○</span><span>Idle</span></div>
        <span class="pbtag">PB —</span>
      </div>
    </section>
    <section class="practice-card analysis-card">
      <div class="card-heading">
        <div><span class="eyebrow">Analysis</span><h3>Attempt history</h3></div>
      </div>
      <div class="stable-chart-placeholder">
        <${Icon} name="feed" size=${24} />
        <b>Choose a star or segment to see its trend</b>
        <span>Your session keeps recording while this view is idle.</span>
      </div>
    </section>
    <section class="practice-card attempts-card">
      <div class="card-heading attempts-heading">
        <div><span class="eyebrow">Practice log</span><h3>Unassigned attempts</h3></div>
        <div class="attempts-tools">
          <span class="meta">${unassignedRows.length} shown</span>
          <${SortControl} ui=${ui} />
        </div>
      </div>
      <div class="attempt-scroll">
        ${unassignedRows.length
          ? html`<${AttemptTable} attempts=${v.unassigned} rows=${unassignedRows}
              t=${t} freshIds=${freshIds} openCompare=${openCompare} />`
          : html`<div class="stable-empty">No attempts are waiting for a target.</div>`}
      </div>
      <div class="attempt-footer">
        <div class="attempt-pagination"></div>
        <div class="attempt-footer-tools">
          <${ResetFilterToggle} ui=${ui} />
          <${HideToggle} hidden=${hidden} showHidden=${showHidden}
              setShowHidden=${setShowHidden} />
        </div>
      </div>
    </section>
  </div>`;
}

export function Practice({ t, openCompare }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [showUnassignedHidden, setShowUnassignedHidden] = useState(false);
  const stored = localStorage.getItem("sm64.sort");
  const [sort, setSortState] = useState(
    SORT_OPTIONS.some(([k]) => k === stored) ? stored : "newest");
  const [hideResets, setHideResetsState] = useState(
    localStorage.getItem("sm64.hideResets") === "1");
  const ui = {
    sort, hideResets,
    setSort: (v) => { localStorage.setItem("sm64.sort", v); setSortState(v); },
    setHideResets: (v) => {
      localStorage.setItem("sm64.hideResets", v ? "1" : "0");
      setHideResetsState(v);
    },
  };
  const freshIds = useFreshAttemptIds(t);
  const [routes, setRoutes] = useState([]);
  const [activeRouteId, setActiveRouteId] = useState(() => {
    const s = localStorage.getItem("sm64.activeRoute");
    return s ? Number(s) : null;
  });
  const [routeView, setRouteView] = useState(null);
  useEffect(() => { getJSON("/api/routes").then(setRoutes).catch(() => {}); }, []);
  // Refetch the resolved route view on selection change AND on every session
  // view update, so per-step/cumulative % stay live as attempts land. A 404
  // (route deleted) clears it → the tab falls back to normal practice.
  useEffect(() => {
    if (activeRouteId == null) { setRouteView(null); return; }
    getJSON(`/api/routes/${activeRouteId}`).then(setRouteView).catch((e) => {
      setRouteView(null);
      // A 404 means the remembered route is GONE, so forget it. Clearing only
      // the local view left the stale id in localStorage, and this effect
      // re-runs on every session-view update — so a deleted route re-fetched
      // and 404'd on every event, forever, across reloads (live log
      // 2026-07-24). Any other failure (server restart, network blip) keeps
      // the selection: losing it would be worse than one empty render.
      if (e && e.status === 404) pickRoute(null);
    });
  }, [activeRouteId, t.view]);
  const pickRoute = (id) => {
    if (id == null) localStorage.removeItem("sm64.activeRoute");
    else localStorage.setItem("sm64.activeRoute", String(id));
    setActiveRouteId(id);
    // Tell the SERVER too (spec 2026-07-23 §5: localStorage is an optimistic
    // mirror, the journaled route_selected is the source of truth). Without
    // this the active route was never journaled, so every seeded castle-
    // movement segment — all 55 carry the in_active_route guard — could only
    // ever arm as a standalone target, i.e. the route corpus was inert. It
    // also feeds active_route.star_keys, which is what lets the selector show
    // only the route's stars.
    send("POST", "/api/route/select", { route_id: id })
      .then(() => t.refresh())      // pull the new active_route.star_keys
      .catch(() => {});   // selection still works locally if the write fails
  };
  const v = t.view;
  if (!v) return html`<${PageState} kind=${t.connected ? "loading" : "offline"}
      title=${t.connected ? "Preparing your practice view" : "Waiting for the trainer"}
      message=${t.connected
        ? "Loading your target, attempts, and current stage…"
        : "The app will reconnect automatically when the local server is available."} />`;

  const tgt = v.target || {};
  const segs = v.segments || [];
  // Active star and active segment are mutually exclusive — a single practice
  // focus. The server keeps ONE target and retires the star target the moment
  // a segment arms OR Mario enters a different course (projection.py), so a
  // LIVE star target authoritatively means "doing stars": highlight that star
  // and suppress every segment pin. With no star target we're in segment-land
  // — pin armed > sticky-recent > target-segment as before. (Tied to the
  // server rule: don't reintroduce a frontend "armed beats star" override, or
  // setting a star while a segment is still armed would wrongly hide the star.)
  const starActive = tgt.kind !== "segment" && tgt.course_id != null;
  const isActiveStar = (sec) => sec.course_id === tgt.course_id
    && sec.star_id === tgt.star_id;
  const isActiveSeg = (sec) => tgt.kind === "segment"
    && sec.segment_id === tgt.segment_id;
  const activeStar = starActive ? v.stars.find(isActiveStar) : undefined;
  const activeSeg = segs.find(isActiveSeg);
  // Pinned segments — presentation only, the target does not move:
  // every currently-ARMED segment is "active now" and pins to the top,
  // most recently armed first (armedOrder appends on arm → reverse).
  // With nothing armed, the sticky last-armed pin keeps the page on the
  // segment being practiced (an accidental exit disarms — correct timing
  // semantics — but the section stays put until a different segment arms);
  // before anything has ever armed, the target segment pins.
  const armedPins = [...t.armedOrder].reverse()
    .map((id) => segs.find((s) => s.segment_id === id))
    .filter(Boolean);
  const stickyPin = t.lastPinnedSeg != null
    ? segs.find((s) => s.segment_id === t.lastPinnedSeg)
    : undefined;
  const pinnedSegs = starActive ? []
    : armedPins.length ? armedPins
    : stickyPin ? [stickyPin] : activeSeg ? [activeSeg] : [];
  // Only one detail surface owns the fixed Objective / Analysis / Attempts
  // tracks. Additional armed segments remain reachable in the stable index
  // below instead of inserting more full cards above the crop.
  const primarySeg = pinnedSegs[0];
  const restStars = v.stars.filter((sec) => sec !== activeStar);
  const restSegs = segs.filter((sec) => sec !== primarySeg);
  // Deliberately stable at a fixed view: live attempts must not reshuffle this
  // index underneath an OBS crop or a player's pointer. The active objective
  // above carries recency; this list follows the server's catalog order.
  const restSections = [...restStars, ...restSegs];

  const unassignedVisible = v.unassigned.filter(
    (a) => !a.cleared && a.outcome !== "abandoned");
  const unassignedHidden = v.unassigned.filter(
    (a) => a.cleared || a.outcome === "abandoned");
  // The unassigned log honors the same sort/reset controls as the section
  // logs — they render in its card too.
  const unassignedRows = (showUnassignedHidden ? v.unassigned : unassignedVisible)
    .filter((a) => !(hideResets
      && (a.outcome === "reset" || a.outcome === "hard_reset")))
    .slice()
    .sort(comparator(sort, t.clock));

  return html`<div class="practice-page">
    <section class="practice-toolbar practice-card">
      <label class="route-focus-control">
        <${Icon} name="routes" size=${18} />
        <span class="field-label">Practice plan</span>
        <select value=${activeRouteId ?? ""}
            onchange=${(e) => pickRoute(e.target.value ? Number(e.target.value) : null)}>
          <option value="">All practice</option>
          ${routes.map((r) => html`<option value=${r.id}>${r.name}</option>`)}
        </select>
      </label>
      <span class="toolbar-note">${routeView
        ? "Route focus is on · history still records"
        : "Choose from the current course or your practice index"}</span>
      <button type="button" onclick=${() => setMenuOpen(!menuOpen)}>
        <${Icon} name="feed" size=${17} />Stats
      </button>
      ${/* Anchored INSIDE the toolbar: as a sibling grid item it added a
           practice-page grid row, shifting every card 12px on open. */""}
      ${menuOpen && html`<div class="stats-popover">
        <${StatMenu} t=${t} close=${() => setMenuOpen(false)} />
      </div>`}
    </section>

    <${StageBanner} t=${t} />

    ${activeStar
      ? html`<${StarSection} key=${`${activeStar.course_id}:${activeStar.star_id}`}
          sec=${activeStar} t=${t} ui=${ui} pinned=${true}
          freshIds=${freshIds} openCompare=${openCompare} />`
      : primarySeg
        ? html`<${SegmentSection} key=${`seg:${primarySeg.segment_id}`}
            sec=${primarySeg} t=${t} ui=${ui} pinned=${true}
            freshIds=${freshIds} openCompare=${openCompare} />`
        : html`<${EmptyPractice} v=${v} t=${t} ui=${ui}
            unassignedRows=${unassignedRows} freshIds=${freshIds}
            openCompare=${openCompare} hidden=${unassignedHidden}
            showHidden=${showUnassignedHidden}
            setShowHidden=${setShowUnassignedHidden} />`}

    ${routeView
      ? html`<section class="practice-card route-focus-card">
          <${RouteFocus} rv=${routeView} t=${t} ui=${ui}
            freshIds=${freshIds} openCompare=${openCompare} />
        </section>`
      : restSections.length > 0 && html`<section class="practice-index">
          <div class="index-heading">
            <div><span class="eyebrow">Practice index</span><h3>Stars and segments</h3></div>
            <span class="meta">Stable catalog order · open any item for its history</span>
          </div>
          <div class="practice-index-list">
            ${restSections.map((sec) => html`<details class="practice-index-item">
              <summary>
                <span class="index-icon"><${Icon}
                  name=${sec.kind === "segment" ? "segments" : "practice"} size=${18} /></span>
                <span class="index-name">${sec.kind === "segment"
                  ? sec.name : `${sec.course_name} · ${sec.star_name}`}</span>
                <span class="meta">${sec.attempts.length} attempts</span>
                <span class="index-chevron"><${Icon} name="chevron" size=${16} /></span>
              </summary>
              ${sec.kind === "segment"
                ? html`<${SegmentSection} key=${`seg:${sec.segment_id}`}
                    sec=${sec} t=${t} ui=${ui} pinned=${false}
                    freshIds=${freshIds} openCompare=${openCompare} />`
                : html`<${StarSection} key=${`${sec.course_id}:${sec.star_id}`}
                    sec=${sec} t=${t} ui=${ui} pinned=${false}
                    freshIds=${freshIds} openCompare=${openCompare} />`}
            </details>`)}
          </div>
        </section>`}
  </div>`;
}
