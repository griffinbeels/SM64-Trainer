import { h } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { fmtIgtShort } from "../format.js";
import { belongsHere, rowId } from "../ghostlibrary.js";
import { courseUnionGroups, parseSegmentId, parseStarId } from "../entities.js";
import { PickerDialog } from "./entitymodal.js";
import { optionIconSrc } from "./entityicons.js";

const html = htm.bind(h);
const PAGE_SIZE = 30;

export function GhostRow({row, ctx, check = true, label = null, importing = false}) {
  const available = importing ? row.importable && !row.duplicate : row.available;
  const extra = importing ? importNote(row) : row.reason || (!available ? "Recording unavailable" : "");
  const author = row.author || "You", strat = row.strat || "Unassigned strategy";
  return html`<label class=${`ghost-library-row${!available ? " is-unavailable" : ""}`} data-ghost-id=${rowId(row)}>
    ${check ? html`<input type="checkbox" checked=${ctx.selected.has(rowId(row))} disabled=${ctx.busy || !available}
      aria-label=${`Select ${author} · ${row.target_name} · ${strat} · ${fmtIgtShort(row.frames)}`}
      onchange=${() => ctx.toggle(row)} />` : html`<span class="ghost-library-row-mark" aria-hidden="true">${row.available ? "✓" : "—"}</span>`}
    <${RowDescription} row=${row} strat=${strat} label=${label} extra=${extra} />
    <span class="ghost-library-creator" title=${author}>${author}</span>
    <${RowTime} row=${row} />
  </label>`;
}

function RowDescription({row, strat, label, extra}) {
  return html`<span class="ghost-library-description"><strong title=${row.target_name}>${row.target_name || "Unknown target"}</strong>
    <span class="meta">${strat}${row.practice ? " · Practice" : ""}${row.outcome && row.outcome !== "success" ? ` · ${row.outcome}` : ""}</span>
    ${label ? html`<span class="ghost-library-best-label">${label}</span>` : null}
    ${extra ? html`<span class="meta ghost-library-reason">${extra}</span>` : null}</span>`;
}

function RowTime({row}) {
  return html`<span class="ghost-library-time">${fmtIgtShort(row.frames)}<small>${row.clock?.toUpperCase()} · ${row.game_version?.toUpperCase() || "Version unknown"}</small></span>`;
}

function importNote(row) {
  if (row.duplicate) return "Already in your library";
  if (!row.importable) return row.reason || "This ghost cannot be imported";
  const compare = {faster: "Faster than your best", not_faster: "Your best is as fast or faster",
    no_best: "No comparable personal best", unknown: "Time comparison unavailable"}[row.comparison] || "Time comparison unavailable";
  return `${compare}${!row.available ? " · Segment definition needs a match before racing" : ""}`;
}

export function Rows({rows, ctx, check = true, importing = false}) {
  return html`<div class="ghost-library-list">${rows.map(row => html`<${GhostRow} key=${rowId(row)} row=${row} ctx=${ctx} check=${check} importing=${importing} />`)}</div>`;
}

export function LibraryPicker({ctx, t}) {
  if (!ctx.picker || !t.view?.catalog) return null;
  const groups = courseUnionGroups(t.view.catalog, (t.segments || []).filter(row => !row.is_hundred_coin_engine),
    t.vocab?.course_by_level || {}, {}, t.vocab?.origins || null).map(group => ({...group,
      icon: optionIconSrc(t, "course", group.key.replace("course-", ""))}));
  const target = ctx.filters.target;
  const value = target?.kind === "segment" ? `segment:${target.entity_key}` : target?.entity_key?.replace("-", ":");
  return html`<${PickerDialog} groups=${groups} value=${value} title="Find a star or segment" depth=${2} placeholder=${null}
    iconFor=${id => optionIconSrc(t, parseSegmentId(id) == null ? "star" : "segment", parseSegmentId(id) ?? id)}
    onClose=${() => ctx.setPicker(false)} onPick=${id => {
      const segment = parseSegmentId(id), star = parseStarId(id);
      ctx.filter({target: segment == null ? {kind: "star", entity_key: `${star.course}-${star.star}`} : {kind: "segment", entity_key: String(segment)}});
      ctx.setPicker(false);
    }} />`;
}

function BrowserFilters({ctx}) {
  const {filters, filter} = ctx;
  const targetName = ctx.rows.find(row => belongsHere(row, filters.target))?.target_name || "Chosen target";
  return html`<div class="ghost-library-filters">
    <input type="search" aria-label="Search ghosts" placeholder="Search player, target or strategy" value=${filters.query}
      oninput=${event => filter({query: event.target.value})} />
    <select aria-label="Ghost ownership" value=${filters.source} onchange=${event => filter({source: event.target.value})}>
      <option value="local">Your ghosts</option><option value="import">Imported ghosts</option><option value="all">All ghosts</option>
    </select>
    <select aria-label="Sort ghosts" value=${filters.sort} onchange=${event => filter({sort: event.target.value})}>
      <option value="fastest">Fastest first</option><option value="recent">Most recent</option>
    </select>
    ${ctx.view !== "race" ? html`<button onclick=${() => ctx.setPicker(true)}>Choose a star or segment</button>` : null}
    ${ctx.view !== "race" ? html`<button onclick=${() => filter({target: ctx.opening})}>Opened target</button>` : null}
    ${filters.target ? html`<span class="ghost-library-filter-target">${targetName}${ctx.view !== "race" ? html`<button onclick=${() => filter({target: null})}>All targets</button>` : null}</span>` : null}
    <label><input type="checkbox" checked=${filters.bests} onchange=${event => filter({bests: event.target.checked})} />Personal bests only</label>
    ${!filters.bests ? html`<label><input type="checkbox" checked=${filters.practice} onchange=${event => filter({practice: event.target.checked})} />Show practice / unfinished</label>` : null}
  </div>`;
}

function BestList({rows, ctx}) {
  const groups = new Map();
  for (const row of rows) {
    const key = `${row.kind}:${row.entity_key}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  }
  return html`<div class="ghost-library-list">${[...groups.entries()].map(([key, members]) => html`
    <section class="ghost-library-best-group" key=${key}><h3>${members[0].target_name}</h3>
      <div class="ghost-library-strategies">${members.map(row => html`<${GhostRow} row=${row} ctx=${ctx} key=${rowId(row)} />`)}</div>
    </section>`)}</div>`;
}

export function LibraryBrowser({ctx}) {
  const [offset, setOffset] = useState(0);
  const pages = Math.max(1, Math.ceil(ctx.visible.length / PAGE_SIZE));
  const currentPage = Math.min(offset, pages - 1);
  const shown = ctx.visible.slice(currentPage * PAGE_SIZE, (currentPage + 1) * PAGE_SIZE);
  const available = ctx.visible.filter(row => row.available);
  const title = ctx.view === "race" ? "Choose ghosts to race" : ctx.view === "custom" ? "Choose your ghosts" : "Your ghost collection";
  return html`<h3 class="ghost-library-step-title" tabindex="-1">${title}</h3>
    <p class="meta">${ctx.view === "race" ? `For ${ctx.openingName}. Up to ${ctx.catalog.max_race} ghosts, including the current PB slot.` : "Find a target, browse your bests, or choose recordings to share."}</p>
    <${BrowserFilters} ctx=${ctx} />
    <div class="ghost-library-selection">
      <button disabled=${ctx.busy || !available.length} onclick=${() => ctx.setSelected(before => new Set([...before, ...available.map(rowId)]))}>Select filtered (${available.length})</button>
      <button disabled=${ctx.busy || !ctx.selected.size} onclick=${() => ctx.setSelected(new Set())}>Clear selection</button>
      <span class="meta">${ctx.visible.length} results · ${available.length} recordings available</span>
    </div>
    ${shown.length ? ctx.filters.bests ? html`<${BestList} rows=${shown} ctx=${ctx} />` : html`<${Rows} rows=${shown} ctx=${ctx} />`
      : html`<p class="ghost-library-empty">No ghosts match this search. Try another target or show all ghosts.</p>`}
    ${pages > 1 ? html`<div class="ghost-library-pagination">
      <button disabled=${currentPage === 0} onclick=${() => setOffset(currentPage - 1)}>Previous</button>
      <span>Page ${currentPage + 1} of ${pages}</span><button disabled=${currentPage + 1 === pages} onclick=${() => setOffset(currentPage + 1)}>Next</button>
    </div>` : null}`;
}
