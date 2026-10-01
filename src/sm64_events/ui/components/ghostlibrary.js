import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, pollJob, send } from "../api.js";
import { fmtIgtShort } from "../format.js";
import { libraryRows, raceChoice, rowId, selectionOf } from "../ghostlibrary.js";
import { Modal } from "./modal.js";
import { ProgressLine } from "./states.js";
import { readGhostLibrary, useUiLog } from "../uilog.js";

const html = htm.bind(h);
const URL = "/api/ghost/library";
const PAGE_SIZE = 30;

function libraryView(catalog, selected, filters, page, session) {
  const target = session?.target;
  const rows = catalog?.ghosts || [];
  const chosen = rows.filter(row => selected.has(rowId(row)));
  const visible = libraryRows(rows, filters, target);
  const lastPage = Math.max(0, Math.ceil(visible.length / PAGE_SIZE) - 1);
  const shownPage = Math.min(page, lastPage);
  const shown = visible.slice(shownPage * PAGE_SIZE, (shownPage + 1) * PAGE_SIZE);
  const choice = raceChoice(chosen, target, session?.selection?.ghosts?.pb ?? true);
  const raceCount = choice.attempts.length + choice.imports.length;
  const cap = catalog?.max_race;
  const raceFits = cap != null && raceCount + Number(choice.pb) <= cap;
  const exportFits = chosen.length > 0 && chosen.length <= (catalog?.max_export || 0);

  return {target, rows, chosen, visible, lastPage, shownPage, shown, choice, raceCount, cap, raceFits, exportFits};
}

function useLibrary(session, write, onClose) {
  const [catalog, setCatalog] = useState(null);
  const [author, setAuthor] = useState("");
  const [selected, setSelected] = useState(new Set());
  const [filters, setFilters] = useState({query: "", source: "local", targetOnly: false,
    practice: false, sort: "fastest"});
  const [page, setPage] = useState(0);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [download, setDownload] = useState(null);
  const [skipped, setSkipped] = useState([]);
  const upload = useRef(null);
  const {target, rows, chosen, visible, lastPage, shownPage, shown, choice, raceCount, cap, raceFits, exportFits} = libraryView(catalog, selected, filters, page, session);

  const load = async () => {
    const result = await getJSON(URL);
    setCatalog(result);
    return result;
  };
  useEffect(() => {
    let alive = true;
    getJSON(URL).then(result => {if (alive) {setCatalog(result); setAuthor(result.author || "");}})
      .catch(problem => {if (alive) setError(problem.message);});
    return () => {alive = false;};
  }, []);

  const filter = patch => {setFilters(before => ({...before, ...patch})); setPage(0);};
  const toggle = row => setSelected(before => {
    const next = new Set(before), id = rowId(row);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  const runJob = async start => {
    setBusy(true); setError(""); setMessage(""); setDownload(null); setSkipped([]);
    setProgress({progress: 0, message: "Preparing collection…", state: "running"});
    try {
      const job = await start();
      return await pollJob(`${URL}/jobs/${job.job_id}`, setProgress);
    } finally {setBusy(false);}
  };

  const exportSelected = async () => {
    try {
      const result = await runJob(() => send("POST", `${URL}/export`, {
        selected: selectionOf(chosen), author}));
      setDownload(result.download_url);
      setSkipped(result.skipped || []);
      setMessage(`Exported ${result.exported} ghosts${result.skipped?.length ? `; ${result.skipped.length} unavailable recordings skipped` : ""}.`);
      // Same-origin download, initiated by the explicit Export gesture.
      const link = document.createElement("a");
      link.href = result.download_url; link.download = "sm64-ghost-collection.sm64ghosts";
      document.body.appendChild(link); link.click(); link.remove();
    } catch (problem) {setError(problem.message);}
  };

  const importCollection = async event => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    try {
      const result = await runJob(() => getJSON(`${URL}/import`, {method: "POST", body: file,
        headers: {"Content-Type": "application/octet-stream"}}));
      await load();
      filter({source: "import", query: "", targetOnly: false, practice: true});
      setSelected(new Set(result.import_ids || []));
      setMessage(`Imported ${result.imported} ghosts${result.duplicates ? `; ${result.duplicates} already in your library` : ""}.${result.unmatched ? ` ${result.unmatched} need a matching segment definition.` : ""}`);
    } catch (problem) {setError(problem.message);}
  };

  const race = async () => {
    setBusy(true); setError("");
    try {
      await send("POST", `${URL}/prepare-race`, {ghosts: choice, target});
      const result = await write({ghosts: choice, target, view: "mario"});
      if (result?.applied === false) {setError(result.error); return;}
      onClose();
    } catch (problem) {setError(problem.message);}
    finally {setBusy(false);}
  };


  return {target, rows, chosen, visible, lastPage, shownPage, shown, choice, raceCount, cap, raceFits, exportFits, busy, progress, message, error, download, skipped, upload, author, filters, page, selected, setSelected, setAuthor, setPage, toggle, filter, exportSelected, importCollection, race, onClose, catalog};
}

function LibraryFooter({ctx}) {
  const {chosen, raceCount, cap, raceFits, exportFits, busy, download, author, exportSelected, race} = ctx;
  return html`<span class="ghost-library-count">${chosen.length} selected</span>
    ${download ? html`<a class="button" href=${download} download="sm64-ghost-collection.sm64ghosts">Download collection</a>` : null}
    <button type="button" onclick=${race} disabled=${busy || !raceCount || !raceFits}
      title=${!raceFits && cap ? `Race up to ${cap} ghosts, including your PB.` : "Race selected ghosts for the current target"}>Race ${raceCount || "selected"} here</button>
    <button type="button" class="primary" onclick=${exportSelected}
      disabled=${busy || !exportFits || !author.trim()}>Export selected</button>`;

}

function LibraryMessages({ctx}) {
  const {progress,busy,error,message,skipped,catalog,visible,rows} = ctx;
  return html`${progress?.state === "running" ? html`<${ProgressLine} progress=${progress.progress} message=${progress.message} running=${busy} />` : null}
      ${error ? html`<p class="error" role="alert">${error}</p>` : null}
      ${message ? html`<p class="meta" role="status">${message}</p>` : null}
      ${skipped.length ? html`<details><summary>${skipped.length} unavailable recordings</summary><ul>${skipped.map(row => html`<li>${row.source === "local" ? `Run #${row.id}` : "Imported ghost"}: ${row.reason}</li>`)}</ul></details>` : null}
      ${!catalog && !error ? html`<p class="meta">Loading recorded ghosts…</p>` : null}
      ${catalog && !visible.length ? html`<p class="meta">${rows.length ? "No ghosts match these filters." : "Play a run to record a ghost, or import a collection. Saved ghosts stay here after the recording buffer expires."}</p>` : null}`;
}

function LibraryView({ctx}) {
  const root = useRef(null);
  useUiLog(root, [readGhostLibrary]);
  const {target, chosen, visible, lastPage, shownPage, shown, choice, raceCount, cap, raceFits, busy, upload, author, filters, selected, setSelected, setAuthor, setPage, toggle, filter, importCollection, onClose} = ctx;
  const footer=html`<${LibraryFooter} ctx=${ctx} />`;
  return html`<${Modal} title="Ghost Library" size="large" icon="practice"
      description="Pick ghosts to race or share. One collection file imports every ghost together."
      onClose=${busy ? undefined : onClose} footer=${footer}>
    <div ref=${root} class="ghost-library" data-testid="ghost-library">
      <div class="ghost-library-toolbar">
        <button type="button" class="primary" disabled=${busy} onclick=${() => upload.current.click()}>Import collection</button>
        <input ref=${upload} type="file" accept=".sm64ghosts,.zip" hidden onchange=${importCollection} />
        <label class="ghost-library-author">Export name<input value=${author} maxlength="128" placeholder="Your player name"
          disabled=${busy} oninput=${event => setAuthor(event.target.value)} /></label>
      </div>
      <div class="ghost-library-filters">
        <input type="search" aria-label="Search ghosts" placeholder="Search player, target or strategy" value=${filters.query}
          oninput=${event => filter({query: event.target.value})} />
        <select aria-label="Ghost ownership" value=${filters.source} onchange=${event => filter({source: event.target.value})}>
          <option value="local">Your ghosts</option><option value="import">Imported ghosts</option><option value="all">All ghosts</option>
        </select>
        <select aria-label="Ghost targets" value=${filters.targetOnly ? "current" : "all"} onchange=${event => filter({targetOnly: event.target.value === "current"})}>
          <option value="all">All targets</option><option value="current">Current target</option>
        </select>
        <select aria-label="Sort ghosts" value=${filters.sort} onchange=${event => filter({sort: event.target.value})}>
          <option value="fastest">Fastest first</option><option value="recent">Most recent</option>
        </select>
        <label><input type="checkbox" checked=${filters.practice} onchange=${event => filter({practice: event.target.checked})} />Show practice / unfinished</label>
      </div>
      <div class="ghost-library-selection">
        <button type="button" disabled=${busy || !visible.length} onclick=${() => setSelected(before => new Set([...before, ...visible.map(rowId)]))}>Select filtered (${visible.length})</button>
        <button type="button" disabled=${busy || !chosen.length} onclick=${() => setSelected(new Set())}>Clear selection</button>
        <span class="meta">${visible.length} ghosts · ${chosen.length} selected${target ? ` · ${raceCount} for this target` : ""}</span>
      </div>
      <${LibraryMessages} ctx=${ctx} />
      <div class="ghost-library-list">${shown.map(row => html`<label class="ghost-library-row" key=${rowId(row)}>
        <input type="checkbox" checked=${selected.has(rowId(row))} disabled=${busy}
          aria-label=${`Select ${row.author} · ${row.target_name} · ${row.strat || "Unassigned strategy"} · ${fmtIgtShort(row.frames)}`}
          onchange=${() => toggle(row)} />
        <span class="ghost-library-description">
          <strong title=${row.target_name}>${row.target_name || "Unknown target"}</strong>
          <span class="meta" title=${row.strat || "Unassigned strategy"}>${row.strat || "Unassigned strategy"}${row.practice ? " · Practice" : ""}${row.outcome !== "success" ? ` · ${row.outcome}` : ""}</span>
          ${row.reason ? html`<span class="meta">${row.reason}</span>` : null}
        </span>
        <span class="ghost-library-creator" title=${row.author}>${row.author}</span>
        <span class="ghost-library-time">${fmtIgtShort(row.frames)}<small>${row.clock?.toUpperCase()}</small></span>
      </label>`)}</div>
      ${visible.length > PAGE_SIZE ? html`<div class="ghost-library-pagination">
        <button type="button" disabled=${shownPage === 0} onclick=${() => setPage(shownPage - 1)}>Previous</button>
        <span>Page ${shownPage + 1} of ${lastPage + 1}</span>
        <button type="button" disabled=${shownPage === lastPage} onclick=${() => setPage(shownPage + 1)}>Next</button>
      </div>` : null}
      ${raceCount && !raceFits && cap ? html`<p class="meta">Select at most ${cap - Number(choice.pb)} ghosts for this target to race with your current PB choice. Collections can hold many more.</p>` : null}
    </div>
  </${Modal}>`;
}

function GhostLibrary({ session, write, onClose }) {
  const ctx=useLibrary(session,write,onClose);
  return html`<${LibraryView} ctx=${ctx} />`;
}

export function GhostLibraryButton({ t, session, write }) {
  const [open, setOpen] = useState(false);
  return html`<button type="button" class="ghost-library-button" onclick=${() => setOpen(true)}>Ghost Library · Import / Export</button>
    ${open ? html`<${GhostLibrary} key="library" t=${t} session=${session} write=${write} onClose=${() => setOpen(false)} />` : null}`;
}
