// Local template library. A later sharing browser can hand the same document
// to the preview/import endpoints; it need not participate in replay clocks.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { Modal } from "./modal.js";
import { templatesChanged, useTemplateRevision } from "../inputpreferences.js";

const html = htm.bind(h);
const MAX_FILE_BYTES = 4 * 1024 * 1024;

async function request(url, body, method = "POST") {
  const response = await fetch(url, body === undefined ? undefined : {
    method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!response.ok) {
    let message = await response.text();
    try { message = JSON.parse(message).detail || message; } catch { /* plain text */ }
    throw new Error(typeof message === "string" ? message : JSON.stringify(message));
  }
  return response;
}

async function download(url, name) {
  const response = await request(url);
  const objectUrl = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Keep the blob alive until the browser has consumed the click.
  setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}

function useTemplateManager({ attemptId, data, onTemplateMarked, targetLabel }) {
  const [mode, setMode] = useState(null);
  const [name, setName] = useState("");
  const nameEdited = useRef(false);
  const [documentText, setDocumentText] = useState("");
  const [preview, setPreview] = useState(null);
  const [rows, setRows] = useState(null);
  const [filter, setFilter] = useState("");
  const [removeId, setRemoveId] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const revision = useTemplateRevision();
  const base = `/api/attempts/${attemptId}/inputs`;
  const target = targetLabel || data.target;

  useEffect(() => {
    if (mode !== "library") return;
    let alive = true;
    setRows(null);
    const query = new URLSearchParams({ kind: data.kind, entity_key: data.entity_key });
    request(`/api/inputs/templates?${query}`).then((r) => r.json())
      .then((value) => { if (alive) setRows(value.templates); })
      .catch((err) => { if (alive) setError(err.message); });
    return () => { alive = false; };
  }, [mode, revision, data.kind, data.entity_key]);

  function editName(value) { nameEdited.current = true; setName(value); }

  function open(next) {
    nameEdited.current = false;
    setError(""); setNotice(""); setPreview(null); setRemoveId(null);
    setName(next === "save" ? `${target}${data.strategy ? ` · ${data.strategy}` : ""}` : "");
    setDocumentText(""); setFilter(""); setMode(next);
  }

  async function run(action) {
    if (busy) return;
    setBusy(true); setError("");
    try { await action(); } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }

  function changed(message) {
    templatesChanged();
    setNotice(message); setMode(null);
    if (onTemplateMarked) onTemplateMarked();
  }

  const save = (event) => {
    event.preventDefault();
    run(async () => {
      await request(`${base}/template`, { name: name.trim() });
      changed(`Saved “${name.trim()}” as your template.`);
    });
  };

  async function readFile(event) {
    const file = event.target.files[0];
    if (!file) return;
    await run(async () => {
      if (file.size > MAX_FILE_BYTES) throw new Error("Choose an input file smaller than 4 MB.");
      const text = new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer());
      setDocumentText(text); setPreview(null);
      if (!nameEdited.current) setName(file.name.replace(/(?:\.inputs)?\.txt$/i, ""));
      await loadPreview(text);
    });
  }

  async function loadPreview(text) {
    const response = await request(`${base}/template/preview`, { document: text });
    const metadata = (await response.json()).document;
    setPreview(metadata);
    if (metadata.name && !nameEdited.current) setName(metadata.name);
  }

  const importPreview = (event) => {
    event.preventDefault();
    run(() => loadPreview(documentText));
  };

  return { attemptId, data, mode, setMode, name, setName: editName, documentText, setDocumentText,
    preview, setPreview, rows, filter, setFilter, removeId, setRemoveId,
    busy, error, notice, setNotice, base, target, open, run, changed, save, readFile, importPreview };
}

export function InputTemplates(props) {
  const model = useTemplateManager(props);
  const { attemptId, data, mode, setMode, busy, error, notice, base, target, open, run } = model;
  return html`<div class="attempt-drawer-tools input-template-tools">
    <button onclick=${() => open("save")} disabled=${busy}>Save as template</button>
    <button onclick=${() => open("library")} disabled=${busy}>Templates</button>
    <button onclick=${() => open("import")} disabled=${busy}>Import inputs</button>
    <button disabled=${busy} onclick=${() => run(() => download(`${base}/document`,
        `attempt-${attemptId}.inputs.txt`))}>Export inputs</button>
    ${notice && html`<span role="status">${notice}</span>`}
    ${!mode && error && html`<span role="alert" class="is-error">${error}</span>`}
    ${mode && html`<${Modal}
        title=${mode === "save" ? "Save this attempt as a template" : mode === "import" ? "Import input template" : "Your input templates"}
        onClose=${busy ? undefined : () => setMode(null)}>
      <div class="input-template-manager" aria-busy=${busy}>
        <p class="input-template-target"><strong>${target}</strong>${data.strategy ? ` · ${data.strategy}` : ""}</p>
        ${mode === "save" && html`<${SaveTemplate} model=${model} />`}
        ${mode === "import" && html`<${ImportTemplate} model=${model} />`}
        ${mode === "library" && html`<${TemplateLibrary} model=${model} />`}
        ${notice && html`<p role="status">${notice}</p>`}
        ${error && html`<p role="alert" class="is-error">${error}</p>`}
      </div>
    <//>`}
  </div>`;
}

function SaveTemplate({ model }) {
  const { save, name, setName, data, busy } = model;
  return html`<form onsubmit=${save}>
    <label>Template name<input required maxlength="160" value=${name}
        oninput=${(e) => setName(e.target.value)} /></label>
    <p class="meta">Credit: ${data.local_author}. This will be the template for future attempts at this target and strategy.</p>
    <button type="submit" disabled=${busy || !name.trim()}>Save template</button>
  </form>`;
}

function ImportTemplate({ model }) {
  const { importPreview, busy, readFile, documentText, setDocumentText, setPreview,
    name, setName, preview, target, data, run, base, changed } = model;
  return html`<form onsubmit=${importPreview}>
    <label>Input file<input type="file" accept=".txt,.inputs,text/plain"
        disabled=${busy} onchange=${readFile} /></label>
    <label>Or paste an input document<textarea rows="6" value=${documentText}
        disabled=${busy} oninput=${(e) => { setDocumentText(e.target.value); setPreview(null); }} /></label>
    <label>Template name<input maxlength="160" value=${name}
        disabled=${busy} oninput=${(e) => setName(e.target.value)} /></label>
    ${!preview && html`<button type="submit" disabled=${busy || !documentText.trim()}>Preview import</button>`}
    ${preview && html`<div class="input-import-preview">
      <strong>${preview.target}</strong>
      <p>${preview.author || "Uncredited"} · ${preview.frames} frames · ${preview.version}
        ${preview.strategy ? ` · ${preview.strategy}` : ""}</p>
      <p>Use this for <strong>${target}</strong>${data.strategy ? ` · ${data.strategy}` : ""}.
        Both timelines start at frame 0.</p>
      ${data.kind === "segment" && html`<p>Check that this is the same segment: segment numbers can differ between players.</p>`}
      <button type="button" disabled=${busy || !name.trim()} onclick=${() => run(async () => {
        await request(`${base}/template/import`, { document: documentText, name: name.trim() });
        changed(`Imported “${name.trim()}” and selected it as your template.`);
      })}>Import and use template</button>
    </div>`}
  </form>`;
}

function TemplateLibrary({ model }) {
  const { filter, setFilter, rows, error, data, busy, run, base, setNotice,
    removeId, setRemoveId, open } = model;
  return html`
    <p class="meta">Choose an example for this attempt’s strategy. The original template keeps its credit and inputs.</p>
    <label>Filter by name, player or strategy<input type="search" value=${filter}
        oninput=${(e) => setFilter(e.target.value)} /></label>
    ${rows === null ? html`<p role="status">${error ? "Templates could not be loaded." : "Loading templates…"}</p>`
      : rows.length === 0 ? html`<p>No templates yet. Save a good attempt or import one from another player.</p>`
      : html`<ul class="input-template-list">${rows.filter((row) =>
          [row.name, row.author, row.strat_tag].filter(Boolean).join(" ").toLowerCase().includes(filter.toLowerCase()))
        .map((row) => html`<li key=${row.id}>
          <div><strong>${row.name}</strong>
            <p class="meta">${row.author || "Uncredited"} · ${row.strat_tag || "Any strategy"}
              ${data.template?.id === row.id ? " · Comparing now" : ""}</p></div>
          <div class="input-template-actions">
            <button disabled=${busy || data.template?.id === row.id} onclick=${() => run(async () => {
              await request(`${base}/template/select`, { template_id: row.id });
              templatesChanged(); setNotice(`Selected “${row.name}”.`);
            })}>${data.template?.id === row.id ? "Comparing now" : "Use for this strategy"}</button>
            <button disabled=${busy} onclick=${() => run(async () => {
            const response = await request(`/api/inputs/templates/${row.id}/document`);
            await navigator.clipboard.writeText(await response.text());
            setNotice(`Copied “${row.name}”. Paste it into a message to share.`);
          })}>Copy inputs</button>
          <button disabled=${busy} onclick=${() => run(() => download(
                `/api/inputs/templates/${row.id}/document`, `template-${row.id}.inputs.txt`))}>Export</button>
            <button disabled=${busy} onclick=${() => setRemoveId(row.id)}>Remove</button>
          </div>
          ${removeId === row.id && html`<div class="input-template-remove">
            <span>Remove “${row.name}” from your templates?</span>
            <button disabled=${busy} onclick=${() => run(async () => {
              await request(`/api/inputs/templates/${row.id}`, {}, "DELETE");
              setRemoveId(null); templatesChanged();
            })}>Remove template</button>
            <button disabled=${busy} onclick=${() => setRemoveId(null)}>Keep</button>
          </div>`}
        </li>`)}</ul>`}
    <button disabled=${busy} onclick=${() => open("import")}>Import inputs</button>
  `;
}
