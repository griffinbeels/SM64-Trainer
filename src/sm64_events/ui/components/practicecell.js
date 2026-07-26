import { h } from "preact";
import htm from "htm";
import { RankIcon } from "./rankicon.js";
import { capName } from "./caps.js";
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
 * strat      the strategy `rank` was earned WITH, for the corner badge's
 *            title (rankBadge only; see below) -- picker cells grade the
 *            BEST-scoring strategy (build_entity_ranks), while the SAME cell
 *            on the practice banner shows the ACTIVE one, often different.
 *            Naming it is what stops the medal changing after you pick from
 *            reading as a rendering fault (spec §3 risk 1; final review I2,
 *            2026-07-25/26)
 * sub        sub-line node (strat name, running chip, or nothing)
 * dimIdle    dim non-active cells — the BANNER's look; the picker grid passes
 *            false, since a grid of dim cells reads as disabled
 * rankBadge  the picker grid's look (task 4, 2026-07-25): draws `rank` as an
 *            out-of-flow corner badge over the art instead of the banner's
 *            in-flow `.starrank` row, and renders NOTHING when `rank` is
 *            falsy (never the banner's "–" placeholder) — an in-flow row
 *            cost a line per grid ROW even when unranked, most of the 94px
 *            that made the picker scroll on a 900px-tall window (live audit
 *            2026-07-25); grading the cells does not pay that back, since a
 *            course with two of seven stars practiced still renders five
 *            placeholders. Default false = the banner's byte-for-byte
 *            unchanged look.
 * onEdit     optional; omit and the ✎ affordance is not rendered at all (the
 *            picker has no per-cell icon override — that lives on the banner)
 */
export function PracticeCell({ active, armed, iconSrc, fallbackSlot = 0,
                              rank, strat, name, sub, title, dimIdle = false,
                              rankBadge = false, onPick, onEdit }) {
  const editKey = (keyEvent) => {
    if (keyEvent.key !== "Enter" && keyEvent.key !== " ") return;
    keyEvent.preventDefault(); keyEvent.stopPropagation(); onEdit();
  };
  // The badge's OWN title, naming the strategy the medal was earned WITH
  // (rankBadge only -- the banner's in-flow row shows the ACTIVE strategy,
  // never ambiguous there, so it keeps RankIcon's own default title).
  // capName() is mandatory here: a raw tier key is wrong on screen, never
  // the style guide's plain preference (tests/test_ui_cap_names.py). `rank`
  // is now `{rank, division}` (the addendum, task 8, 2026-07-26), so this
  // reads the tier off `rank.rank` rather than treating the whole object as
  // the tier key.
  const badgeTitle = rank
    ? (strat ? `${capName(rank.rank)} · best on ${strat}` : capName(rank.rank))
    : null;
  return html`<button
      class="starcell ${active ? "active-star" : ""} ${armed ? "armed" : ""}"
      title=${title || name} onclick=${onPick}>
    <span class="starholder">
      <img class="starimg ${isGenericArt(iconSrc) ? "" : "courseicon"} ${dimIdle && !active ? "dim" : ""}"
           src=${iconSrc}
           onerror=${(errorEvent) => fallbackToGenericStar(errorEvent, fallbackSlot)}
           alt="" draggable="false" />
    </span>
    ${rankBadge
      ? (rank ? html`<span class="starrank-badge"><${RankIcon} tier=${rank.rank} division=${rank.division} title=${badgeTitle} size=${16} /></span>` : null)
      : html`<span class="starrank">
      ${rank ? html`<${RankIcon} tier=${rank.rank} division=${rank.division} size=${16} />` : "–"}</span>`}
    <span class="starname">${name}</span>
    <span class="starsub">${sub}</span>
    ${onEdit ? html`<span class="editicon" role="button" tabindex="0"
          title="Choose icon…" aria-label="Choose icon"
          onclick=${(clickEvent) => { clickEvent.stopPropagation(); onEdit(); }}
          onkeydown=${editKey}>✎</span>` : null}
  </button>`;
}
