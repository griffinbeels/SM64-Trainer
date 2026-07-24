// src/sm64_events/ui/components/iconpicker.js — grid picker for the bundled
// split-icon set (ui/assets/star_icons). Assigns a per-entity icon OVERRIDE
// to a star or segment: POST /api/icon, kind-dispatched exactly like
// /api/strat, null = back to the default art. The stem list comes from
// GET /api/icons (the server globs the asset dir, so a newly dropped icon
// appears with zero code changes). Opened from the banner cells' hover ✎
// (ui/components/stagebanner.js) and the segment editor's Icon row
// (ui/components/segments.js) — ONE component so stars and segments can
// never drift (spec 2026-07-24-segment-icon-cells).
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { Modal } from "./modal.js";

const html = htm.bind(h);

// identity: {course_id, star_id} | {kind:"segment", segment_id} (an ek field
// may ride along for the caller's own lookups — ignored here).
// current: the entity's stored override stem, or null (highlights the tile).
// onDone(): close — after a successful pick OR a plain dismiss.
export function IconPicker({ identity, current, onDone }) {
  const [icons, setIcons] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    getJSON("/api/icons")
      .then((listing) => setIcons(listing.icons))
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

  return html`<${Modal} title="Choose an icon" icon="practice"
      description="Shown on the course quick-select. Default follows the star-icons display setting."
      onClose=${onDone}>
    ${err && html`<p class="badx">${err}</p>`}
    ${!icons && !err && html`<p class="meta">Loading icons…</p>`}
    ${icons && html`<div class="icongrid">
      <button type="button" class="icontile ${current ? "" : "on"}"
          title="Use the default art" onclick=${() => pick(null)}>
        <span class="icontile-default" aria-hidden="true">↺</span>
        <span>Default</span>
      </button>
      ${icons.map((stem) => html`<button type="button" key=${stem}
          class="icontile ${current === stem ? "on" : ""}" title=${stem}
          onclick=${() => pick(stem)}>
        <img src=${`/ui/assets/star_icons/${stem}.png`} alt=""
             loading="lazy" draggable="false" />
        <span>${stem}</span>
      </button>`)}
    </div>`}
  <//>`;
}
