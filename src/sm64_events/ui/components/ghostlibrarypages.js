import { h } from "preact";
import htm from "htm";
import { bestGroups, belongsHere, rowId } from "../ghostlibrary.js";
import { GhostRow, Rows } from "./ghostlibrarybrowse.js";

const html = htm.bind(h);
const heading = (title, text) => html`<div class="ghost-library-page-heading"><h3 class="ghost-library-step-title" tabindex="-1">${title}</h3><p class="meta">${text}</p></div>`;
const count = n => `${n} ${n === 1 ? "ghost" : "ghosts"}`;
const unmatchedNote = n => n ? ` ${n} ${n === 1 ? "needs" : "need"} a matching segment definition before racing.` : "";

export function LibraryHome({ctx}) {
  return html`<section class="ghost-library-context">
    <div class="ghost-library-section-heading"><span>Opened from</span><strong>${ctx.openingName}</strong></div>
    <h3 class="ghost-library-step-title" tabindex="-1">Ghosts you are racing</h3>
    ${ctx.current.length ? html`<${Rows} rows=${ctx.current} ctx=${ctx} check=${false} />`
      : html`<p class="meta">No ghosts are currently playing for this target.</p>`}
    <button disabled=${Boolean(ctx.raceReason)} onclick=${() => ctx.startBrowser("race", false, ctx.opening)}>Choose ghosts to race</button>
    ${ctx.raceReason ? html`<p class="meta">${ctx.raceReason}</p>` : null}
  </section>
  <div class="ghost-library-task-choices">
    <button class="ghost-library-task" aria-label="Import" onclick=${() => ctx.go("import")}><strong>Import</strong><span>Bring in another player's ghost collection.</span><span aria-hidden="true">→</span></button>
    <button class="ghost-library-task" aria-label="Export" onclick=${ctx.startExport}><strong>Export</strong><span>Share your bests or choose a custom set.</span><span aria-hidden="true">→</span></button>
  </div>
  <div class="ghost-library-home-links">
    <button onclick=${() => ctx.startBrowser("browse", true)}>Your personal bests</button>
    <button onclick=${() => {ctx.startBrowser("browse"); ctx.filter({source: "all"}); ctx.setPicker(true);}}>Find a star or segment</button>
    <button onclick=${() => {ctx.startBrowser("browse"); ctx.filter({source: "all"});}}>Browse all ghosts</button>
  </div>`;
}

export function LibraryName({ctx}) {
  const bytes = new TextEncoder().encode(ctx.author.trim()).length;
  return html`${heading("What name should your ghosts show?", "Remembered for future exports. You can change it whenever you share.")}
    <label class="ghost-library-name">Player name<input autofocus value=${ctx.author} placeholder="Your player name"
      oninput=${event => ctx.setAuthor(event.target.value)} onkeydown=${event => {
        if (event.key === "Enter" && bytes > 0 && bytes <= 128 && !ctx.busy) {event.preventDefault(); ctx.saveName();}
      }} /></label>
    ${bytes > 128 ? html`<p class="error">Use a shorter name. Names can contain up to 128 bytes.</p>` : null}`;
}

export function LibraryExport({ctx}) {
  const current = ctx.bests.filter(row => belongsHere(row, ctx.opening));
  const available = ctx.bests.filter(row => row.available).length;
  return html`${heading("What would you like to share?", "Your ghosts travel together in one collection file.")}
    <div class="ghost-library-name-summary"><span>Sharing as <strong>${ctx.author}</strong></span><button onclick=${ctx.changeName}>Change</button></div>
    <div class="ghost-library-options">
      <button aria-label=${ctx.opening?.kind === "segment" ? "This Segment's Personal Bests" : "This Star's Personal Bests"} disabled=${!current.length} onclick=${ctx.currentBests}><strong>${ctx.opening?.kind === "segment" ? "This Segment's Personal Bests" : "This Star's Personal Bests"}</strong><span>${current.length ? `${ctx.openingName} · Overall and strategy bests` : "No personal best yet for this target"}</span><span aria-hidden="true">→</span></button>
      <button aria-label="All personal bests" disabled=${!available} onclick=${ctx.allBests}><strong>All personal bests</strong><span>${count(available)} available · one best per target and strategy</span><span aria-hidden="true">→</span></button>
      <button aria-label="Choose a custom set" onclick=${() => ctx.startBrowser("custom", false, ctx.opening)}><strong>Choose a custom set</strong><span>Find targets, strategies or players. Save a shortcut afterwards.</span><span aria-hidden="true">→</span></button>
    </div>
    ${ctx.catalog.shortcuts?.length ? html`<section class="ghost-library-shortcuts"><h3>Saved searches</h3><p class="meta">These find the latest matching ghosts each time.</p>
      ${ctx.catalog.shortcuts.map(shortcut => html`<div class="ghost-library-shortcut" key=${shortcut.id}>
        <button onclick=${() => ctx.useShortcut(shortcut)}>${shortcut.name}<span aria-hidden="true">→</span></button>
        <button aria-label=${`Remove shortcut ${shortcut.name}`} onclick=${() => ctx.removeShortcut(shortcut)}>Remove</button>
      </div>`)}</section>` : null}`;
}

export function LibraryTargetBests({ctx}) {
  const groups = bestGroups(ctx.bests, ctx.opening);
  return html`${heading(ctx.opening?.kind === "segment" ? "This Segment's Personal Bests" : "This Star's Personal Bests",
    "The overall best starts selected. Add other strategy bests, or share any strategy on its own.")}
    <p class="ghost-library-target-name">${ctx.openingName}</p>
    ${groups.length > 1 ? html`<p class="ghost-library-notice">Timers and game versions have separate overall bests. These times are compared within each group.</p>` : null}
    <div class="ghost-library-list">${groups.map(group => html`<section key=${group.key} class="ghost-library-best-group">
      <h3>${group.rows[0].clock?.toUpperCase()} · ${group.rows[0].game_version?.toUpperCase() || "Version unknown"}</h3>
      <div class="ghost-library-strategies">${group.rows.map(row => html`<${GhostRow} row=${row} ctx=${ctx} key=${rowId(row)}
        label=${group.winners.includes(row) ? group.winners.length > 1 ? "Joint overall best" : "Overall best" : "Best for this strategy"} />`)}</div>
    </section>`)}</div>`;
}

export function LibraryReview({ctx}) {
  const missing = ctx.reviewBack === "export" && ctx.selectionLabel === "All personal bests" ? ctx.bests.filter(row => !row.available).length : 0;
  return html`${heading(ctx.chosen.length ? "Ready to share?" : "No recordings match this search",
    ctx.chosen.length ? `${count(ctx.chosen.length)} will be saved in one collection file.` : "Choose another export option or change your search.")}
    <div class="ghost-library-review-meta"><div><span>Player name</span><strong>${ctx.author}</strong><button onclick=${ctx.changeName}>Change</button></div>
      <div><span>Selection</span><strong>${ctx.selectionLabel}</strong></div></div>
    ${missing ? html`<p class="ghost-library-notice">${missing} personal best recordings are unavailable. Their results stay in history; only available recordings will be exported.</p>` : null}
    <${Rows} rows=${ctx.chosen} ctx=${ctx} check=${false} />
    ${ctx.view === "custom" ? html`<p class="meta">After exporting, you can save this search as a shortcut.</p>` : null}`;
}

export function LibraryImport({ctx}) {
  return html`${heading("Choose a ghost collection", "Preview its ghosts before anything is added to your library.")}
    <div class="ghost-library-file"><strong>One file, many ghosts</strong><p class="meta">Choose a .sm64ghosts collection another player shared with you.</p>
      <button class="primary primary-button" onclick=${() => ctx.upload.current.click()}>Choose collection file</button></div>
    ${ctx.preview ? html`<div class="ghost-library-resume"><strong>${ctx.fileName}</strong><button onclick=${() => ctx.go("import-review")}>Continue review</button></div>` : null}`;
}

export function LibraryImportReview({ctx}) {
  const duplicate = ctx.importRows.filter(row => row.duplicate).length;
  return html`${heading("Choose which ghosts to add", `${ctx.fileName} · ${count(ctx.importRows.length)} in this collection`)}
    <div class="ghost-library-import-modes" role="group" aria-label="Which ghosts to import">
      ${[["all", "All new ghosts"], ["faster", "Faster than my best"], ["custom", "Choose manually"]].map(([mode, label]) => html`
        <button class=${ctx.importMode === mode ? "is-selected" : ""} aria-pressed=${ctx.importMode === mode} onclick=${() => ctx.selectImport(mode)}>${label}</button>`)}</div>
    ${ctx.importMode === "faster" ? html`<div class="ghost-library-import-explanation"><p class="meta">Faster means the same target, strategy, timer and known game version. Unknown versions cannot be compared.</p>
      <label><input type="checkbox" checked=${ctx.includeNoBest} onchange=${event => ctx.selectImport("faster", event.target.checked)} />Also add ghosts where I have no comparable best</label></div>` : null}
    ${duplicate ? html`<p class="meta">${duplicate} already in your library. Those copies stay unchanged.</p>` : null}
    <${Rows} rows=${ctx.importRows} ctx=${ctx} importing=${true} />
    <p class="meta">Your personal bests stay unchanged. Unmatched segments can be added now and raced after their definitions are matched.</p>`;
}

export function LibraryReceipt({ctx}) {
  const receipt = ctx.receipt, imported = receipt.kind === "import";
  return html`<span class="ghost-library-receipt-mark" aria-hidden="true">✓</span>
    ${heading(imported ? "Ghosts added to your library" : "Your collection is ready",
      imported ? `${count(receipt.imported)} added.` : `${count(receipt.exported)} · Shared as ${ctx.author}`)}
    ${imported ? html`<p class="ghost-library-notice">${receipt.duplicates || 0} already in your library.${unmatchedNote(receipt.unmatched)}</p>
      <div class="ghost-library-home-links"><button onclick=${() => {ctx.startBrowser("browse"); ctx.filter({source: "import", practice: true});}}>View imported ghosts</button>
        <button disabled=${Boolean(ctx.raceReason)} onclick=${() => ctx.startBrowser("race", false, ctx.opening)}>Choose ghosts to race</button></div>
      ${ctx.raceReason ? html`<p class="meta">${ctx.raceReason}</p>` : null}` : html`
      <a class="button" href=${receipt.download_url} download="sm64-ghost-collection.sm64ghosts">Download collection</a>
      ${receipt.skipped?.length ? html`<details><summary>${receipt.skipped.length} recordings could not be exported</summary><ul>${receipt.skipped.map(row => html`<li>${row.reason}</li>`)}</ul></details>` : null}`}
    ${!imported && ctx.view === "custom" ? html`<${SaveShortcut} ctx=${ctx} />` : null}`;
}

function SaveShortcut({ctx}) {
  const target = ctx.rows.find(row => belongsHere(row, ctx.filters.target))?.target_name;
  return html`<section class="ghost-library-save-shortcut"><h3>Make next time quicker</h3>
      <p class="meta">Save this search. It finds the latest matching recordings each time; individual checkbox choices apply only to this export.</p>
      <p class="meta">${ctx.filters.target ? `Fixed target: ${target || ctx.filters.target.entity_key}.` : "All targets."}${ctx.filters.bests ? " Personal bests only." : ""}${ctx.filters.query ? ` Search: ${ctx.filters.query}.` : ""}</p>
      <label>Shortcut name<input value=${ctx.shortcutName} placeholder="Name this search" oninput=${event => ctx.setShortcutName(event.target.value)} /></label>
      <button disabled=${ctx.busy || !ctx.shortcutName.trim() || ctx.saved} onclick=${ctx.saveShortcut}>Save shortcut</button>
      ${ctx.saved ? html`<p role="status">Shortcut saved.</p>` : null}</section>`;
}
