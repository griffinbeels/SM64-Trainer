import { h } from "preact";
import htm from "htm";
import { Medal } from "./ranks.js";

const html = htm.bind(h);

// THE practiceable-thing cell — art / rank medal / name / sub-line — extracted
// from stagebanner.js (2026-07-25) so the entity picker's grid and the practice
// banner render the SAME cell. A user picking a star in the modal sees exactly
// what they will see on the banner afterwards, and there is one place to change
// the anatomy rather than two that drift.
//
// Consumers: components/stagebanner.js (every banner row mode) and
// components/entitymodal.js (the picker grid).

const STAR_IMG_COUNT = 6;    // star_1.png .. star_6.png in ui/assets/
export const genericStarSrc = (slot = 0) =>
  `/ui/assets/star_${Math.min(slot + 1, STAR_IMG_COUNT)}.png`;

// generic gold-star art vs "real" art (bundled split icon OR uploaded user
// icon) — the latter gets the opaque-square `courseicon` treatment
export const isGenericArt = (src) => /\/assets\/star_\d+\.png$/.test(src);

// A load failure (missing/corrupt icon) degrades to the generic star art;
// dropping `courseicon` also removes the opaque-square styling.
export function fallbackToGenericStar(event, slot = 0) {
  const img = event.target;
  if (!isGenericArt(img.src)) {
    img.classList.remove("courseicon");
    img.src = genericStarSrc(slot);
  }
}

/**
 * active     this is the current target (glow + bob)
 * armed      a segment whose timer is running now
 * iconSrc    resolved art URL (ui/entities.js optionIcon, or the banner's own
 *            resolveIcon which additionally handles `user:` uploads)
 * rank       optional rank key -> Medal; renders "–" when absent
 * sub        sub-line node (strat name, running chip, or nothing)
 * dimIdle    dim non-active cells — the BANNER's look; the picker grid passes
 *            false, since a grid of dim cells reads as disabled
 * onEdit     optional; omit and the ✎ affordance is not rendered at all (the
 *            picker has no per-cell icon override — that lives on the banner)
 */
export function PracticeCell({ active, armed, iconSrc, fallbackSlot = 0,
                              rank, name, sub, title, dimIdle = false,
                              onPick, onEdit }) {
  const editKey = (keyEvent) => {
    if (keyEvent.key !== "Enter" && keyEvent.key !== " ") return;
    keyEvent.preventDefault(); keyEvent.stopPropagation(); onEdit();
  };
  return html`<button
      class="starcell ${active ? "active-star" : ""} ${armed ? "armed" : ""}"
      title=${title || name} onclick=${onPick}>
    <span class="starholder">
      <img class="starimg ${isGenericArt(iconSrc) ? "" : "courseicon"} ${dimIdle && !active ? "dim" : ""}"
           src=${iconSrc}
           onerror=${(errorEvent) => fallbackToGenericStar(errorEvent, fallbackSlot)}
           alt="" draggable="false" />
    </span>
    <span class="starrank">
      ${rank ? html`<${Medal} rank=${rank} size=${16} />` : "–"}</span>
    <span class="starname">${name}</span>
    <span class="starsub">${sub}</span>
    ${onEdit ? html`<span class="editicon" role="button" tabindex="0"
          title="Choose icon…" aria-label="Choose icon"
          onclick=${(clickEvent) => { clickEvent.stopPropagation(); onEdit(); }}
          onkeydown=${editKey}>✎</span>` : null}
  </button>`;
}
