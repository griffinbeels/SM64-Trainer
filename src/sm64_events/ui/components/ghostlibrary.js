import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { useGhostLibrary } from "../ghostlibraryflow.js";
import { Modal } from "./modal.js";
import { ProgressLine } from "./states.js";
import { readGhostLibrary, useUiLog } from "../uilog.js";
import { LibraryBrowser, LibraryPicker } from "./ghostlibrarybrowse.js";
import { LibraryHome, LibraryName, LibraryExport, LibraryTargetBests, LibraryReview,
         LibraryImport, LibraryImportReview, LibraryReceipt } from "./ghostlibrarypages.js";

const html = htm.bind(h);
const PAGES = {home: LibraryHome, name: LibraryName, export: LibraryExport,
  "target-bests": LibraryTargetBests, browser: LibraryBrowser, review: LibraryReview,
  import: LibraryImport, "import-review": LibraryImportReview, receipt: LibraryReceipt};

const countNoun = (n, noun) => `${n} ${noun}${n === 1 ? "" : "s"}`;

function FooterAction({ctx}) {
  const n = ctx.chosen.length, plural = `${n} ${n === 1 ? "ghost" : "ghosts"}`;
  const readyToExport = ctx.exportFits && ctx.author.trim();
  if (ctx.page === "name") return html`<${NameFooter} ctx=${ctx} />`;
  if (ctx.page === "browser" && ctx.view === "race")
    return html`<button class="primary primary-button" disabled=${ctx.busy || !ctx.raceFits} onclick=${ctx.race}>Use selected ghosts</button>`;
  if (ctx.page === "browser" || ctx.page === "target-bests")
    return html`<button class="primary primary-button" disabled=${ctx.busy || !ctx.exportFits} onclick=${ctx.review}>Review ${plural}</button>`;
  if (ctx.page === "review")
    return html`<button class="primary primary-button" disabled=${ctx.busy || !readyToExport} onclick=${ctx.exportSelected}>Export collection</button>`;
  if (ctx.page === "import-review")
    return html`<button class="primary primary-button" disabled=${ctx.busy || !ctx.importChosen.length} onclick=${ctx.commitImport}>Import ${countNoun(ctx.importChosen.length, "ghost")}</button>`;
  if (ctx.page === "receipt") return html`<button class="primary primary-button" disabled=${ctx.busy} onclick=${() => ctx.go("home")}>Done</button>`;
  return null;
}

function NameFooter({ctx}) {
  const bytes = new TextEncoder().encode(ctx.author.trim()).length;
  return html`<button class="primary primary-button" disabled=${ctx.busy || !bytes || bytes > 128} onclick=${ctx.saveName}>Continue</button>`;
}

function LibraryFooter({ctx}) {
  const back = {name: ctx.nameBack, export: "home", "target-bests": "export", browser: ctx.view === "custom" ? "export" : "home",
    review: ctx.reviewBack, import: "home", "import-review": "import"}[ctx.page];
  const importing = ctx.page === "import" || ctx.page === "import-review";
  const count = importing ? ctx.importChosen.length : ctx.chosen.length;
  const cap = ctx.catalog?.max_export || 0;
  const raceLimit = ctx.catalog.max_race - Number(ctx.choice.pb);
  const raceOverflow = ctx.page === "browser" && ctx.view === "race" && ctx.raceCount + Number(ctx.choice.pb) > ctx.catalog.max_race;
  return html`${back ? html`<button disabled=${ctx.busy} onclick=${() => {if (ctx.page === "name") ctx.setAuthor(ctx.rememberedAuthor); ctx.go(back);}}>Back</button>` : null}
    <span class="ghost-library-count">${["browser", "target-bests", "review", "import-review"].includes(ctx.page) ? `${count} selected` : ""}</span>
    ${raceOverflow ? html`<span class="meta">Choose at most ${countNoun(raceLimit, "recording")} with this PB choice.</span>` : null}
    ${count > cap && !importing && !raceOverflow ? html`<span class="meta">A collection holds up to ${cap} ghosts.</span>` : null}
    ${importing ? html`<button disabled=${ctx.busy} onclick=${ctx.cancelImport}>Cancel import</button>` : null}
    <${FooterAction} ctx=${ctx} />`;
}

function GhostLibrary(props) {
  const ctx = useGhostLibrary(props);
  const root = useRef(null);
  useUiLog(root, [readGhostLibrary]);
  useEffect(() => {
    const heading = root.current?.querySelector(".ghost-library-step-title");
    if (heading) heading.focus({preventScroll: true});
  }, [ctx.page]);
  if (ctx.picker) return html`<${LibraryPicker} ctx=${ctx} t=${props.t} />`;
  const Page = PAGES[ctx.page];
  return html`<${Modal} title="Ghost Library" size="large" icon="practice"
    description="Race your favourites. Share your progress." onClose=${ctx.busy ? undefined : ctx.close}
    footer=${ctx.catalog ? html`<${LibraryFooter} ctx=${ctx} />` : null}>
    <div ref=${root} class="ghost-library" data-testid="ghost-library" data-page=${ctx.page} data-step=${ctx.page}>
      <input ref=${ctx.upload} type="file" accept=".sm64ghosts,.zip" hidden onchange=${ctx.previewFile} />
      ${ctx.catalog ? html`<fieldset class="ghost-library-controls" disabled=${ctx.busy}><${Page} ctx=${ctx} /></fieldset>`
        : html`<p class="meta">Loading recorded ghosts…</p>`}
      ${ctx.progress?.state === "running" ? html`<${ProgressLine} progress=${ctx.progress.progress} message=${ctx.progress.message} running=${ctx.busy} />` : null}
      ${ctx.error ? html`<p class="error" role="alert">${ctx.error}</p>` : null}
    </div>
  </${Modal}>`;
}

export function GhostLibraryButton({t, entityKey, session, write}) {
  const [open, setOpen] = useState(false);
  return html`<button type="button" class="ghost-library-button" onclick=${() => setOpen(true)}>Ghost Library · Import / Export</button>
    ${open ? html`<${GhostLibrary} t=${t} entityKey=${entityKey} session=${session} write=${write} onClose=${() => setOpen(false)} />` : null}`;
}
