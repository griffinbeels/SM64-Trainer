// src/sm64_events/ui/components/practice.js
import { h } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { send } from "../api.js";
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

function delta(frames) {
  if (frames === null || frames === undefined) return "";
  const cls = frames > 0 ? "delta-up" : "delta-down";
  const sign = frames > 0 ? "+" : "";
  return html` <span class=${cls}>${sign}${(frames / 30).toFixed(2)}s</span>`;
}

function AttemptRow({ a, t, idx }) {
  async function clear() {
    await send("POST", `/api/attempts/${a.id}/clear`, { reason: "accidental" });
    t.refresh();
  }
  async function restore() {
    await send("POST", `/api/attempts/${a.id}/restore`);
    t.refresh();
  }
  async function savePb() {
    await send("POST", "/api/pb", { attempt_id: a.id, timer_mode: t.clock });
    t.refresh();
  }
  const time = t.clock === "igt" ? a.igt : a.rta;
  const frames = t.clock === "igt" ? a.igt_frames : a.rta_frames;
  // Glow when saving would set a new PB: beats the recorded PB, or no PB
  // exists yet. frames > 0 excludes same-tick race rows (rta=0 junk) whose
  // "PB" would be meaningless.
  const pbBeat = a.outcome === "success" && !a.cleared
    && frames != null && frames > 0
    && (a.pb_delta_frames === null || a.pb_delta_frames < 0);
  return html`<tr class=${a.cleared ? "cleared" : ""}>
    <td class="meta">#${idx + 1}</td>
    <td class=${a.outcome === "success" ? "good" : "badx"}>
      ${OUTCOME_LABEL[a.outcome] || a.outcome}
      ${a.outcome === "death" && a.outcome_detail
        ? html` <span class="meta">(${a.outcome_detail})</span>` : ""}
      ${a.outcome === "success" && time ? html` <b>${time}</b>` : ""}
      ${a.outcome !== "success" && a.igt ? html` <span class="meta">${a.igt} in</span>` : ""}
      ${a.rollouts_total > 0
        ? html` <span class="meta">· ${a.rollouts_dustless}/${a.rollouts_total} dustless rollouts</span>` : ""}
      ${a.jumps_total > 0
        ? html` <span class="meta">· ${a.jumps_dustless}/${a.jumps_total} dustless jumps</span>` : ""}
    </td>
    <td>${a.outcome === "success" ? delta(a.pb_delta_frames) : ""}</td>
    <td class="meta">${a.strat_tag || ""}</td>
    <td style="text-align:right">
      ${a.outcome === "success" && !a.cleared
        ? html`<button class=${pbBeat ? "pb-glow" : ""} onclick=${savePb}>Save as PB</button> ` : ""}
      ${a.cleared
        ? html`<button onclick=${restore}>undo</button>`
        : html`<button onclick=${clear} title="clear (mistake)">×</button>`}
    </td>
  </tr>`;
}

// Shared table component used by both StarSection and the unassigned block.
// attempts: the full ordered list for stable numbering;
// rows: the filtered/sorted subset to actually render.
function AttemptTable({ attempts, rows, t }) {
  return html`<table>
    ${rows.map((a) => {
      const idx = attempts.indexOf(a);
      return html`<${AttemptRow} key=${a.id} a=${a} t=${t} idx=${idx} />`;
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

function StarSection({ sec, t, ui, pinned }) {
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
  return html`<div class="starsec ${pinned ? "active-star" : ""}">
    ${pinned && html`<div class="active-tag">★ ACTIVE STAR</div>`}
    <div class="shead">
      <b>${sec.course_name} · ${sec.star_name}</b>
      <a href=${sec.links.ukikipedia} target="_blank">RTA Guide</a>
      ${sec.links.example && html`<a href=${sec.links.example} target="_blank">Example</a>`}
      <span class="pbtag">${pb ? `PB ${pb.display} (${t.clock})` : "no PB yet"}</span>
    </div>
    <${Timeline} tl=${sec.timeline} sec=${sec} t=${t} />
    <${Progress} prog=${sec.progress} clock=${t.clock} />
    <${AttemptTable} attempts=${sec.attempts} rows=${shown} t=${t} />
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
  const v = t.view;
  if (!v) return html`<p class="meta">loading… (server unreachable? check /health)</p>`;

  const tgt = v.target || {};
  const isActive = (sec) =>
    sec.course_id === tgt.course_id && sec.star_id === tgt.star_id;
  const active = tgt.course_id != null ? v.stars.find(isActive) : undefined;
  const rest = v.stars.filter((sec) => sec !== active);

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
    ${active && html`<${StarSection} key=${`${active.course_id}:${active.star_id}`} sec=${active} t=${t} ui=${ui} pinned=${true} />`}
    ${v.stars.length === 0 && v.unassigned.length === 0
      ? html`<p class="meta">No attempts this session yet — grab a star.</p>` : ""}
    ${rest.length > 0 && html`<div class="meta listhead">stars — recent activity first</div>`}
    ${rest.map((sec) => html`<${StarSection} key=${`${sec.course_id}:${sec.star_id}`} sec=${sec} t=${t} ui=${ui} pinned=${false} />`)}
    ${v.unassigned.length > 0 && html`<div class="starsec">
      <div class="shead"><b>No target</b>
        <span class="meta">failures before any star was grabbed or set</span></div>
      <${AttemptTable} attempts=${v.unassigned} rows=${unassignedRows} t=${t} />
      <${HideToggle} hidden=${unassignedHidden}
                     showHidden=${showUnassignedHidden}
                     setShowHidden=${setShowUnassignedHidden} />
    </div>`}`;
}
