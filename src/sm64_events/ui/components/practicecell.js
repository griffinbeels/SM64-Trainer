import { h } from "preact";
import htm from "htm";
import { RankIcon } from "./rankicon.js";
import { fallbackToGenericStar, isGenericArt } from "./entityicons.js";

const html = htm.bind(h);

// THE practiceable-thing cell — art / rank medal / name / sub-line — extracted
// from stagebanner.js (2026-07-25) so the entity picker's grid and the practice
// banner render the SAME cell. A user picking a star in the modal sees exactly
// what they will see on the banner afterwards, and there is one place to change
// the anatomy rather than two that drift.
//
// Consumers: components/stagebanner.js (every banner row mode) and
// components/entitymodal.js (the picker grid).

// The art helpers live one layer down: entityicons.js resolves an entity to a
// stem and owns the shared onerror; entities.js owns the pure tables both it
// and the picker's icon chain read. This file only renders.
/**
 * active     this is the current target (glow + bob)
 * armed      a segment whose timer is running now
 * iconSrc    resolved art URL (ui/entities.js optionIcon, or the banner's own
 *            resolveIcon which additionally handles `user:` uploads)
 * rank       optional {rank, division} -> RankIcon; renders "–" when absent
 *            (server shape since the addendum, task 8, 2026-07-26 — rank_by_
 *            star/segment_targets' "rank" field carries a division alongside
 *            the tier now, not a bare tier string)
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
      ${rank ? html`<${RankIcon} tier=${rank.rank} division=${rank.division} size=${16} />` : "–"}</span>
    <span class="starname">${name}</span>
    <span class="starsub">${sub}</span>
    ${onEdit ? html`<span class="editicon" role="button" tabindex="0"
          title="Choose icon…" aria-label="Choose icon"
          onclick=${(clickEvent) => { clickEvent.stopPropagation(); onEdit(); }}
          onkeydown=${editKey}>✎</span>` : null}
  </button>`;
}
