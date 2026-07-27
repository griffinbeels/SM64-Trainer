// src/sm64_events/ui/components/iconpicker.js — grid picker for selector
// icons. Assigns a per-entity icon OVERRIDE to a star or segment: POST
// /api/icon, kind-dispatched exactly like /api/strat, null = back to the
// default art. The grid lists the bundled split-icon set (GET /api/icons —
// the server globs the asset dir, so a newly dropped icon appears with zero
// code changes) plus previously uploaded user icons, and a "+" tile uploads
// any image file (raw-body POST /api/icons/upload; stored server-side in
// the data dir, referenced as `user:<file>`). Opened from the banner cells'
// hover ✎ (ui/components/stagebanner.js) and the segment editor's Icon row
// (ui/components/segments.js) — ONE component so stars and segments can
// never drift (spec 2026-07-24-segment-icon-cells + addendum).
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { iconSrcFromStem, parseEntityKey, parseStarId } from "../entities.js";
import { Modal } from "./modal.js";

const html = htm.bind(h);

// THE stem -> img-src rule moved DOWN to ../entities.js on 2026-07-26, so the
// import-free resolver that has to apply it to an override stem can reach it
// too. Re-exported (imported above as well — a bare `export … from` makes no
// local binding, and the grid below calls it) because this module's own
// callers have always imported it from here.
export { iconSrcFromStem };

/** The /api/icon identity for an entity key — the kind-dispatched body the
 *  endpoint takes, plus the `ek` the caller uses to look its current override
 *  up. Derived from the key rather than hand-built per call site, so a new
 *  surface offering ✎ cannot invent a fourth shape. */
export function iconIdentityForKey(entityKey) {
  const { kind, id } = parseEntityKey(entityKey);
  if (kind === "segment")
    return { kind: "segment", segment_id: Number(id), ek: entityKey };
  const { course, star } = parseStarId(id);
  return { course_id: course, star_id: star, ek: entityKey };
}

/** Icon-picking state for a surface that offers ✎ on more than one cell:
 *  ONE picker per surface, hoisted OUT of the cells so a click inside the
 *  modal can never bubble into a cell's own onclick. Returns [setPicking,
 *  modal] — call setPicking(identityForKey(key)) to open. Lived in
 *  stagebanner.js until the Rank tab's coverage tiles needed the same thing
 *  (2026-07-26); it is here rather than there because a hook that opens THIS
 *  modal belongs beside it. */
export function useIconPicking(t) {
  const [picking, setPicking] = useState(null);
  const modal = picking && html`<${IconPicker} identity=${picking}
      current=${(((t.view || {}).icon_overrides) || {})[picking.ek] || null}
      onDone=${() => { setPicking(null); t.refresh(); }} />`;
  return [setPicking, modal];
}

const tileLabel = (stem) =>
  stem.startsWith("user:") ? stem.slice(5).replace(/\.\w+$/, "") : stem;

// identity: {course_id, star_id} | {kind:"segment", segment_id} (an ek field
// may ride along for the caller's own lookups — ignored here).
// current: the entity's stored override stem, or null (highlights the tile).
// onDone(): close — after a successful pick OR a plain dismiss.
export function IconPicker({ identity, current, onDone }) {
  const [icons, setIcons] = useState(null);
  const [userIcons, setUserIcons] = useState([]);
  const [err, setErr] = useState(null);
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef(null);
  useEffect(() => {
    getJSON("/api/icons")
      .then((listing) => {
        setIcons(listing.icons);
        setUserIcons(listing.user_icons || []);
      })
      .catch((loadErr) => setErr(String(loadErr)));
  }, []);

  async function pick(stem) {
    const body = identity.kind === "segment"
      ? { kind: "segment", segment_id: identity.segment_id, icon: stem }
      : { course_id: identity.course_id, star_id: identity.star_id,
          icon: stem };
    try {
      await send("POST", "/api/icon", body);
      onDone();
    } catch (pickErr) { setErr(String(pickErr)); }
  }

  // "+" tile: upload the chosen file (raw body, no multipart) and assign it
  // in one step. Failures land in the modal's error line, not the console.
  async function upload(fileEvent) {
    const file = fileEvent.target.files && fileEvent.target.files[0];
    fileEvent.target.value = "";       // same file re-pickable after an error
    if (!file) return;
    setUploading(true); setErr(null);
    try {
      const resp = await fetch(
        `/api/icons/upload?name=${encodeURIComponent(file.name)}`,
        { method: "POST", body: file });
      if (!resp.ok) {
        let detail = null;
        try { detail = (await resp.json()).detail; } catch { /* non-JSON */ }
        throw new Error(detail || `upload failed (${resp.status})`);
      }
      await pick((await resp.json()).icon);
    } catch (uploadErr) { setErr(String(uploadErr)); }
    setUploading(false);
  }

  return html`<${Modal} title="Choose an icon" icon="practice"
      description="Shown on the course quick-select. Square images ~100×100 look best (the built-in set's size)."
      onClose=${onDone}>
    ${err && html`<p class="badx">${err}</p>`}
    ${!icons && !err && html`<p class="meta">Loading icons…</p>`}
    ${icons && html`<div class="icongrid">
      <button type="button" class="icontile"
          title="Upload an image file (square, ~100×100 recommended)"
          disabled=${uploading}
          onclick=${() => fileInput.current && fileInput.current.click()}>
        <span class="icontile-default" aria-hidden="true">+</span>
        <span>${uploading ? "Uploading…" : "Upload…"}</span>
      </button>
      <input ref=${fileInput} type="file" hidden
          accept=".png,.jpg,.jpeg,.webp,.gif" onchange=${upload} />
      <button type="button" class="icontile ${current ? "" : "on"}"
          title="Use the default art" onclick=${() => pick(null)}>
        <span class="icontile-default" aria-hidden="true">↺</span>
        <span>Default</span>
      </button>
      ${[...userIcons, ...icons].map((stem) => html`<button type="button"
          key=${stem} class="icontile ${current === stem ? "on" : ""}"
          title=${tileLabel(stem)} onclick=${() => pick(stem)}>
        <img src=${iconSrcFromStem(stem)} alt=""
             loading="lazy" draggable="false" />
        <span>${tileLabel(stem)}</span>
      </button>`)}
    </div>`}
  <//>`;
}
