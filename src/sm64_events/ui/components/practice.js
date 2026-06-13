// src/sm64_events/ui/components/practice.js
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { send } from "../api.js";
import { ReplayPlayer } from "./replay.js";
import { StatMenu } from "./statmenu.js";
import { Timeline } from "./timeline.js";
import { Progress } from "./progress.js";

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

function AttemptRow({ a, t, idx, focus, clearFocus, isNew }) {
  const [showReplay, setShowReplay] = useState(false);
  const [flash, setFlash] = useState(false);
  const rowRef = useRef(null);
  // Progress-graph pick (see StarSection.pickFromGraph): when this row is
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
  const row = html`<tr ref=${(el) => { rowRef.current = el; }}
      class="${a.cleared ? "cleared" : ""} ${flash ? "row-flash" : ""} ${isNew ? "row-new" : ""}">
    <td class="meta">#${idx + 1}</td>
    <td class=${a.outcome === "success" ? "good" : "badx"}>
      ${OUTCOME_LABEL[a.outcome] || a.outcome}
      ${a.outcome === "death" && a.outcome_detail
        ? html` <span class="meta">(${a.outcome_detail})</span>` : ""}
      ${a.outcome === "success" && time ? html` <b>${time}</b>` : ""}
      ${a.outcome !== "success" && inTime ? html` <span class="meta">${inTime} in</span>` : ""}
      ${a.rollouts_total > 0
        ? html` <span class="meta">· ${a.rollouts_dustless}/${a.rollouts_total} dustless rollouts</span>` : ""}
      ${a.jumps_total > 0
        ? html` <span class="meta">· ${a.jumps_dustless}/${a.jumps_total} dustless jumps</span>` : ""}
    </td>
    <td>${a.outcome === "success" ? delta(a.pb_delta_frames) : ""}</td>
    <td class="meta">${a.strat_tag || ""}</td>
    <td style="text-align:right">
      <button onclick=${() => setShowReplay(!showReplay)} title="view replay">${showReplay ? "▾" : "▶"}</button>
      ${a.outcome === "success" && !a.cleared
        ? (a.is_current_pb
          ? html` <button onclick=${undoPb}
              title="delete this save — the previous PB becomes current again">Undo PB</button>`
          : html` <button class=${pbBeat ? "pb-glow" : ""} onclick=${savePb}>Save as PB</button>`)
        : ""}
      ${a.cleared
        ? html` <button onclick=${restore}>undo</button>`
        : html` <button onclick=${clear} title="clear (mistake)">×</button>`}
    </td>
  </tr>`;
  const expandedRow = showReplay
    ? html`<tr class="replay-row"><td colspan="5"><${ReplayPlayer} attemptId=${a.id} /></td></tr>`
    : null;
  return [row, expandedRow];
}

// Shared table component used by both StarSection and the unassigned block.
// attempts: the full ordered list for stable numbering;
// rows: the filtered/sorted subset to actually render.
function AttemptTable({ attempts, rows, t, focus, clearFocus, freshIds }) {
  return html`<table>
    ${rows.map((a) => {
      const idx = attempts.indexOf(a);
      return html`<${AttemptRow} key=${a.id} a=${a} t=${t} idx=${idx}
        focus=${focus} clearFocus=${clearFocus}
        isNew=${freshIds ? freshIds.has(a.id) : false} />`;
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

function StarSection({ sec, t, ui, pinned, freshIds }) {
  const [showHidden, setShowHidden] = useState(false);
  const [visible, setVisible] = useState(10);
  const [focus, setFocus] = useState(null);
  const pickNonce = useRef(0);
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

  // Progress-graph node click: reveal that attempt's row (bump pagination
  // if it's past the fold), scroll to it, and auto-open its replay when a
  // saved file exists (HEAD existence probe — graph points are always in
  // `rows`: they're non-cleared successes, which no list filter removes).
  async function pickFromGraph(attemptId) {
    let openReplay = false;
    try {
      openReplay = (await fetch(`/api/replay/saved/${attemptId}`,
                                { method: "HEAD" })).ok;
    } catch { /* probe is best-effort: still scroll + flash */ }
    const idx = rows.findIndex((a) => a.id === attemptId);
    if (idx === -1) return;
    if (idx >= visible) setVisible(Math.ceil((idx + 1) / 10) * 10);
    setFocus({ id: attemptId, nonce: ++pickNonce.current, openReplay });
  }

  async function setStrat(v) {
    if (v === "__new") {
      v = (window.prompt("New strategy name:") || "").trim();
      if (!v) { t.refresh(); return; }   // refresh resets the select to current
    }
    await send("POST", "/api/strat", {
      course_id: sec.course_id, star_id: sec.star_id,
      strat_tag: v || null,
    });
    t.refresh();
  }

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

  return html`<div class="starsec ${pinned ? "active-star" : ""}">
    ${pinned && html`<div class="active-tag">★ ACTIVE STAR</div>`}
    <div class="shead">
      <b>${sec.course_name} · ${sec.star_name}</b>
      <a href=${sec.links.ukikipedia} target="_blank">RTA Guide</a>
      ${sec.links.example && html`<a href=${sec.links.example} target="_blank">Example</a>`}
      <select class="meta" value=${sec.last_strat || ""}
              onchange=${(e) => setStrat(e.target.value)}>
        <option value="">— no strat —</option>
        ${sec.strategies.map((s) => html`<option value=${s}>${s}</option>`)}
        <option value="__new">+ new strat…</option>
      </select>
      <span class="pbtag">${pb ? `PB ${pb.display} (${t.clock})` : "no PB yet"}</span>
      <button class="meta" onclick=${wipeData}
        title=${t.scope === "lifetime"
          ? "wipe this star's data (all sessions)"
          : "wipe this star's data (current session)"}>clear data</button>
    </div>
    <${Timeline} tl=${sec.timeline} sec=${sec} t=${t} />
    <${Progress} prog=${sec.progress} clock=${t.clock} onPick=${pickFromGraph} />
    <${AttemptTable} attempts=${sec.attempts} rows=${shown} t=${t}
      focus=${focus} clearFocus=${() => setFocus(null)} freshIds=${freshIds} />
    ${(rows.length > visible || visible > 10) && html`<div>
      ${rows.length > visible && html`<button class="meta"
          style="background:none;border:none;cursor:pointer"
          onclick=${() => setVisible(visible + 10)}>
        Show 10 more
      </button>`}
      ${visible > 10 && html`<button class="meta"
          style="background:none;border:none;cursor:pointer"
          onclick=${() => setVisible(Math.max(10, visible - 10))}>
        Hide last 10
      </button>`}
    </div>`}
    <${HideToggle} hidden=${hidden} showHidden=${showHidden} setShowHidden=${setShowHidden} />
    <div class="chips">
      ${sec.stats.map((s) => html`
        <span class="chip" title=${s.key}>${s.label} ${s.display ?? "–"}</span>`)}
    </div>
  </div>`;
}

// Segment sibling of StarSection — deliberately NOT a generalization:
// segments are RTA-only (igt is null everywhere), have no links, and no
// strat selector in v1 (POST /api/strat is star-shaped: course_id+star_id
// required, no kind — sec.strategies stays display-only until it grows one).
// Broken sections (definition deleted, history remains) render but drop the
// timeline/marker editor — markers key off the deleted definition.
function SegmentSection({ sec, t, ui, pinned, freshIds }) {
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

  // Pinned tag, three-state: the target always wins the ★ tag; otherwise the
  // honest armed flag decides between live (ARMED) and sticky (RECENT) pins.
  const pinTag = isTarget ? "★ ACTIVE SEGMENT"
    : armed ? "⏱ ARMED SEGMENT" : "⏱ RECENT SEGMENT";
  return html`<div class="starsec ${pinned ? "active-star" : ""}">
    ${pinned && html`<div class="active-tag">${pinTag}</div>`}
    <div class="shead">
      <b>⏱ ${sec.name}</b>
      ${armed && html`<span class="chip good">⏱ armed</span>`}
      ${sec.broken && html`<span class="meta">definition deleted — history only</span>`}
      <span class="pbtag">${sec.pb.rta ? `PB ${sec.pb.rta.display} (rta)` : "no PB yet"}</span>
      <button class="meta" onclick=${wipeData}
        title=${t.scope === "lifetime"
          ? "wipe this segment's data (all sessions)"
          : "wipe this segment's data (current session)"}>clear data</button>
    </div>
    ${!sec.broken && html`<${Timeline} tl=${sec.timeline} sec=${sec} t=${t} />`}
    <${Progress} prog=${sec.progress} clock="rta" />
    <${AttemptTable} attempts=${sec.attempts} rows=${shown} t=${t} freshIds=${freshIds} />
    ${(rows.length > visible || visible > 10) && html`<div>
      ${rows.length > visible && html`<button class="meta"
          style="background:none;border:none;cursor:pointer"
          onclick=${() => setVisible(visible + 10)}>
        Show 10 more
      </button>`}
      ${visible > 10 && html`<button class="meta"
          style="background:none;border:none;cursor:pointer"
          onclick=${() => setVisible(Math.max(10, visible - 10))}>
        Hide last 10
      </button>`}
    </div>`}
    <${HideToggle} hidden=${hidden} showHidden=${showHidden} setShowHidden=${setShowHidden} />
    <div class="chips">
      ${sec.stats.map((s) => html`
        <span class="chip" title=${s.key}>${s.label} ${s.display ?? "–"}</span>`)}
    </div>
  </div>`;
}

function ControlBar({ ui }) {
  return html`<div class="bar">
    <label class="meta">sort${" "}
      <select value=${ui.sort} onchange=${(e) => ui.setSort(e.target.value)}>
        ${SORT_OPTIONS.map(([k, label]) => html`<option value=${k}>${label}</option>`)}
      </select></label>
    <label class="meta" style="cursor:pointer">
      <input type="checkbox" checked=${ui.hideResets}
             onchange=${(e) => ui.setHideResets(e.target.checked)} />
      ${" "}hide resets <span class="meta">(stats unaffected)</span></label>
  </div>`;
}

export function Practice({ t }) {
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
  const v = t.view;
  if (!v) return html`<p class="meta">loading… (server unreachable? check /health)</p>`;

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
  const restStars = v.stars.filter((sec) => sec !== activeStar);
  const restSegs = segs.filter((sec) => !pinnedSegs.includes(sec));

  const unassignedVisible = v.unassigned.filter(
    (a) => !a.cleared && a.outcome !== "abandoned");
  const unassignedHidden = v.unassigned.filter(
    (a) => a.cleared || a.outcome === "abandoned");
  const unassignedRows = showUnassignedHidden ? v.unassigned : unassignedVisible;

  return html`
    <div style="display:flex;justify-content:flex-end">
      <button onclick=${() => setMenuOpen(!menuOpen)}>⚙ stats</button>
    </div>
    ${menuOpen && html`<${StatMenu} t=${t} close=${() => setMenuOpen(false)} />`}
    <${ControlBar} ui=${ui} />
    ${pinnedSegs.map((sec) => html`<${SegmentSection} key=${`seg:${sec.segment_id}`} sec=${sec} t=${t} ui=${ui} pinned=${true} freshIds=${freshIds} />`)}
    ${activeStar && html`<${StarSection} key=${`${activeStar.course_id}:${activeStar.star_id}`} sec=${activeStar} t=${t} ui=${ui} pinned=${true} freshIds=${freshIds} />`}
    ${v.stars.length === 0 && segs.length === 0 && v.unassigned.length === 0
      ? html`<p class="meta">No attempts this session yet — grab a star.</p>` : ""}
    ${restSegs.length > 0 && html`<div class="meta listhead">segments — recent activity first</div>`}
    ${restSegs.map((sec) => html`<${SegmentSection} key=${`seg:${sec.segment_id}`} sec=${sec} t=${t} ui=${ui} pinned=${false} freshIds=${freshIds} />`)}
    ${restStars.length > 0 && html`<div class="meta listhead">stars — recent activity first</div>`}
    ${restStars.map((sec) => html`<${StarSection} key=${`${sec.course_id}:${sec.star_id}`} sec=${sec} t=${t} ui=${ui} pinned=${false} freshIds=${freshIds} />`)}
    ${v.unassigned.length > 0 && html`<div class="starsec">
      <div class="shead"><b>No target</b>
        <span class="meta">failures before any star was grabbed or set</span></div>
      <${AttemptTable} attempts=${v.unassigned} rows=${unassignedRows} t=${t} freshIds=${freshIds} />
      <${HideToggle} hidden=${unassignedHidden}
                     showHidden=${showUnassignedHidden}
                     setShowHidden=${setShowUnassignedHidden} />
    </div>`}`;
}
