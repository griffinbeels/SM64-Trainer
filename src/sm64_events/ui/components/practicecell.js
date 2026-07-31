import { h } from "preact";
import htm from "htm";
import { RankIcon } from "./rankicon.js";
import { RANK_FLOOR, capName } from "./caps.js";
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
 * active     this is the current target (glow + bob) — the ONLY highlight
 *            state a cell has (round 2, 2026-07-30: "we should reuse the
 *            same exact system... unify this design" — a segment cell used
 *            to ALSO carry an `armed`/"running" state with its own green
 *            glow, which fought `active-star`'s gold one whenever a segment
 *            was both armed and targeted, and read as redundant besides —
 *            the pinned SegmentSection card already shows "Running" as its
 *            own live-state line, and a quick-select cell repeating that is
 *            not a second fact, just a second place the first one is
 *            drawn. Deleted outright, not merely defaulted off, so there is
 *            nothing left for a segment cell to diverge back into: stars
 *            and segments now make the IDENTICAL PracticeCell call)
 * iconSrc    resolved art URL — always from entityicons.js's entityIconSrc /
 *            entityIcon chain, never derived at a call site (2026-07-26)
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
 * sub        sub-line node — the active strategy's name, or nothing; the
 *            SAME content for every cell regardless of kind (round 2: a
 *            segment cell used to swap this for a "⏱ running" chip while
 *            armed, which is the other half of the armed/active unification
 *            above)
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
export function PracticeCell({ active, iconSrc, fallbackSlot = 0,
                              rank, hasStandards = false, strat, name, sub,
                              title, dimIdle = false,
                              rankBadge = false, onPick, onEdit }) {
  // Unranked but RANKABLE draws the ladder floor rather than a bare "-",
  // so it reads as "bottom of the ladder" instead of "not a thing that
  // ranks" (user, 2026-07-30). Unranked with no standards at all still
  // draws nothing: there is no ladder for a floor to be the bottom of.
  // Only the in-flow `.starrank` row uses this -- `rankBadge` (the picker
  // grid) deliberately renders NOTHING when unranked, because an in-flow
  // placeholder per grid row is most of what made that grid scroll, and a
  // floor icon on every unpracticed cell would reinstate exactly that.
  const rankOrFloor = rank
    || (hasStandards ? { rank: RANK_FLOOR.tier, division: RANK_FLOOR.division }
                     : null);
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
      class="starcell ${active ? "active-star" : ""}"
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
      ${rankOrFloor
        ? html`<${RankIcon} tier=${rankOrFloor.rank} division=${rankOrFloor.division}
            size=${16} />`
        : "–"}</span>`}
    <span class="starname">${name}</span>
    <span class="starsub">${sub}</span>
    ${onEdit ? html`<span class="editicon" role="button" tabindex="0"
          title="Choose icon…" aria-label="Choose icon"
          onclick=${(clickEvent) => { clickEvent.stopPropagation(); onEdit(); }}
          onkeydown=${editKey}>✎</span>` : null}
  </button>`;
}
