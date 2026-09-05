// The public link is metadata on one attempt; previews never prepare a video.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { videoSource } from "./librarymodel.js";

const html = htm.bind(h);

export function publicRecordingUrl(value) {
  const text = (value || "").trim();
  try {
    const url = new URL(text);
    return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password
      ? text : null;
  } catch { return null; }
}

function LinkPreview({ url }) {
  const [metadata, setMetadata] = useState(null);
  useEffect(() => {
    let alive = true;
    setMetadata(null);
    if (!url) return undefined;
    const timer = setTimeout(() => {
      getJSON(`/api/media/preview?url=${encodeURIComponent(url)}`)
        .then((result) => alive && setMetadata(result))
        .catch(() => {}); // Recognition is optional; persistence is independent.
    }, 350);
    return () => { alive = false; clearTimeout(timer); };
  }, [url]);
  if (!url) return null;
  const source = videoSource(url);
  const thumb = publicRecordingUrl(metadata?.thumbnail || source?.thumb);
  return html`<div class="recording-link-preview">
    ${thumb && html`<img src=${thumb} alt="" loading="lazy"
      onerror=${(event) => { event.target.style.visibility = "hidden"; }} />`}
    <div><b>${metadata?.title || source?.site || "Recording"}</b>
      <a href=${url} target="_blank" rel="noopener noreferrer">${url}</a></div>
  </div>`;
}

export function RecordingLink({ attemptId, onLoaded, onChanged }) {
  const [saved, setSaved] = useState(null);
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [undo, setUndo] = useState(null);
  const [reload, setReload] = useState(0);
  const inputRef = useRef(null);
  const focusEdit = useRef(false);
  const endpoint = `/api/attempts/${attemptId}/recording`;
  const fieldId = `recording-link-${attemptId}`;

  useEffect(() => {
    let alive = true;
    getJSON(endpoint).then((result) => {
      if (!alive) return;
      setSaved(result); setDraft(result.url || ""); setError("");
      onLoaded?.(result.url);
    }).catch((failure) => alive && setError(String(failure)));
    return () => { alive = false; };
  }, [attemptId, reload]);

  useEffect(() => {
    if (editing && focusEdit.current) {
      inputRef.current?.focus(); inputRef.current?.select();
      focusEdit.current = false;
    }
  }, [editing]);

  function change() {
    setDraft(saved.url || ""); setError("");
    focusEdit.current = true; setEditing(true);
  }
  function cancel() {
    setDraft(saved.url || ""); setEditing(false); setError("");
  }
  async function persist(url, restoring = false) {
    if (busy || !saved) return;
    setBusy(true); setError("");
    try {
      const result = await send("PUT", endpoint,
        { url, expected_revision: saved.revision });
      setUndo(restoring ? null : { url: saved.url });
      setNotice(restoring ? "Link restored" : !url ? "Link removed"
        : saved.url ? "Link updated" : "Link added");
      setSaved(result); setDraft(result.url || ""); setEditing(false);
      onChanged?.(result.url);
    } catch (failure) {
      // Preserve the draft and undo. Refresh only the revision on conflict so
      // the next explicit Retry is a deliberate replacement of the latest link.
      if (failure.status === 409) {
        try {
          const latest = await getJSON(endpoint);
          setSaved(latest); setEditing(true); onChanged?.(latest.url);
          setError("This link changed elsewhere. Your draft is kept. Check it, then Retry.");
        } catch { setError("Could not reload the changed link. Try again."); }
      } else setError(String(failure));
    } finally { setBusy(false); }
  }
  function save(event) {
    event?.preventDefault();
    const url = publicRecordingUrl(draft);
    if (!url) { setError("Paste a complete http:// or https:// video link."); return; }
    persist(url);
  }

  const showField = saved && (editing || !saved.url);
  return html`<section class="recording-link" aria-label="Public recording link">
    <div class="recording-link-heading">
      <label for=${showField ? fieldId : null}>Public recording link</label>
      ${saved?.url && !editing && html`<button onclick=${change}>Change link</button>`}
    </div>
    <p class="meta">Included when this attempt is exported as your PB.</p>
    ${!saved ? html`<p role="status">${error || "Loading link…"}
      ${error && html`<button onclick=${() => setReload(reload + 1)}>Retry</button>`}</p>`
      : showField ? html`<${LinkForm} fieldId=${fieldId} inputRef=${inputRef}
          draft=${draft} saved=${saved} editing=${editing} busy=${busy} error=${error}
          save=${save} cancel=${cancel} persist=${persist}
          onInput=${(value) => { setDraft(value); setError(""); }} />` : html`<${LinkPreview} key=${saved.url} url=${saved.url} />`}
    ${error && saved && html`<p class="recording-link-error" id=${`${fieldId}-error`}
      role="alert">${error}</p>`}
    <div class="recording-link-status" role="status" aria-live="polite">
      ${notice}${undo && html`<button disabled=${busy}
        onclick=${() => persist(undo.url, true)}>Undo</button>`}
    </div>
  </section>`;
}

function LinkForm({ fieldId, inputRef, draft, saved, editing, busy, error,
                    save, cancel, persist, onInput }) {
  return html`<form onsubmit=${save}>
        <div class="recording-link-field">
          <input id=${fieldId} ref=${inputRef} type="text" inputmode="url"
            autocomplete="url" placeholder="Paste video link" value=${draft}
            disabled=${busy} aria-invalid=${!!error}
            aria-describedby=${error ? `${fieldId}-error` : null}
            oninput=${(event) => { onInput(event.target.value); }}
            onkeydown=${(event) => {
              event.stopPropagation();
              if (event.key === "Enter" && !busy) save(event);
              if (event.key === "Escape" && !busy) { event.preventDefault(); cancel(); }
            }} />
          <button type="submit" disabled=${busy}>${busy ? "Saving…" : error ? "Retry"
            : saved.url ? "Save link" : "Add link"}</button>
          ${editing && html`<button type="button" disabled=${busy} onclick=${cancel}>Cancel</button>`}
        </div>
        <${LinkPreview} key=${publicRecordingUrl(draft)} url=${publicRecordingUrl(draft)} />
        ${saved.url && html`<button type="button" class="recording-link-remove"
          disabled=${busy} onclick=${() => persist(null)}>Remove link</button>`}
      </form>`;
}
