// src/sm64_events/ui/components/ranks.js — mirrors ranks/standards.RANK_COLORS
// and ranks/classify.RANK_NAMES (keep in lockstep).
import { h } from "preact";
import htm from "htm";
const html = htm.bind(h);

export const RANK_NAMES = ["Mario", "Grandmaster", "Master", "Diamond",
  "Platinum", "Gold", "Silver", "Bronze", "Iron"];
export const RANK_COLORS = {
  Mario: "#e23b3b", Grandmaster: "#8b1a1a", Master: "#7b3f9e",
  Diamond: "#3f86d6", Platinum: "#5cb85c", Gold: "#e0b520",
  Silver: "#c2c2c2", Bronze: "#c0894a", Iron: "#8a8a8a" };
const FG = { Mario: "#fff", Grandmaster: "#fff", Master: "#fff", Diamond: "#fff",
  Platinum: "#10300f", Gold: "#3a2c00", Silver: "#2a2a2a", Bronze: "#2e1c08", Iron: "#1c1c1c" };

export const rankColor = (n) => RANK_COLORS[n] || "#3a4250";

export function Medal({ rank, size = 18 }) {
  const bg = rankColor(rank), fg = FG[rank] || "#7e8796";
  return html`<span title=${rank || "no rank"} style=${`display:inline-flex;align-items:center;justify-content:center;width:${size}px;height:${size}px;border-radius:50%;background:${bg};border:2px solid rgba(255,255,255,.5);flex:0 0 auto`}>
    <span style=${`color:${fg};font-size:${Math.round(size * 0.5)}px;line-height:1`}>${rank ? "★" : "–"}</span>
  </span>`;
}

// Mirrors ranks/classify.RANK_MODES keys+labels (keep in lockstep) in
// dropdown order; the header's Rank picker renders from this.
export const RANK_MODE_OPTIONS = [["pb", "PB"], ["avg10", "Avg 10"],
  ["avg50", "Avg 50"], ["best10", "Best 10"], ["best50", "Best 50"],
  ["lifetime", "Lifetime"]];
const MODE_LABEL = Object.fromEntries(RANK_MODE_OPTIONS);

// Sentinel wording (server sends {rank:null, reason, mode}): a strategy is
// ranked ONLY by times achieved with it, so "unranked" means no gradeable
// time on THIS strat yet — the saved PB in pb mode, valid runs in avg modes.
const RANK_SENTINEL = {
  unranked: "— unranked (no PB on this strategy yet)",
  unranked_avg: "— unranked (no valid runs on this strategy yet)",
  no_ladder: "— no rank standards for this strategy",
  no_strat: "— pick a strat to see your rank",
};

function sentinelMsg(banner) {
  if (!banner) return RANK_SENTINEL.no_strat;
  if (banner.reason === "unranked" && banner.mode && banner.mode !== "pb")
    return RANK_SENTINEL.unranked_avg;
  return RANK_SENTINEL[banner.reason] || RANK_SENTINEL.no_strat;
}

// Rendered inside the objective card's rank slot. The rank-colored wash
// across the card is painted by CSS (.objective-metrics::before, keyed off
// the --rank-glow var practice.js sets from rankColor); this component lays
// out the medal, labels, and a full-width next-rank progress track. Nothing
// here may exceed the slot — the old fixed 200px bar in a bordered box bled
// past the card edge (user report 2026-07-24). `.objective-card` is a HARD
// fixed height (122px at desktop, 258px under 760px, both `overflow` values
// that do NOT reflow the grid) — a kicker label or extra fact here must cost
// WIDTH, never a new line, or it silently bleeds into the card below.
//
// The "Strategy" kicker + the sub-division on the tier name (PLATINUM I, not
// bare PLATINUM) exist because this banner sits directly beside
// EntityRankTag's "Star" rank and the two numbers answer different questions
// — grading the ACTIVE strategy's own ladder here vs. the entity's
// best-possible ladder there. A user reading e.g. "PLATINUM" next to "Iron I"
// filed that as a mislabel bug (live report 2026-07-25); it wasn't wrong, it
// was unlabelled. `division` rides the server's scoring.division_for — this
// component must never compute that curve itself.
export function RankBanner({ banner }) {
  if (!banner || !banner.rank) {
    return html`<span class="meta">${sentinelMsg(banner)}</span>`;
  }
  const c = rankColor(banner.rank);
  const gap = banner.gap_cs != null ? (banner.gap_cs / 100).toFixed(2) : null;
  const basis = banner.basis;
  const fillPct = banner.next ? Math.round((banner.fill || 0) * 100) : 100;
  return html`<div class="rank-banner">
    <div class="rank-banner-row">
      <span class="rank-banner-kicker" title="Graded on the strategy picked above">Strategy</span>
      <${Medal} rank=${banner.rank} size=${26} />
      <b class="rank-banner-name">${banner.rank.toUpperCase()}${banner.division ? ` ${banner.division}` : ""}</b>
      ${basis && html`<span class="meta rank-banner-basis">
        ${MODE_LABEL[banner.mode] || banner.mode} · avg of ${basis.count}${basis.window ? `/${basis.window}` : ""} · ${basis.display}</span>`}
      <span class="meta rank-banner-next">${banner.next
        ? html`next: <b>${banner.next}</b> −${gap}s` : "top rank"}</span>
    </div>
    <div class="rank-progress-track"
        title=${banner.next ? `${fillPct}% of the way to ${banner.next}` : "top rank"}>
      <i style=${`width:${fillPct}%;background:${c}`}></i>
    </div>
  </div>`;
}

// The star's OWN rank, beside the strategy's. Two questions, two numbers:
// the strat medal says how well you run THIS strat, this one says how close
// that is to the fastest the star can be. Absent (not "–") when the entity
// has no standards, so a segment without a ladder shows nothing rather than
// implying it was graded and failed.
//
// The "Star" kicker mirrors RankBanner's "Strategy" one (see above) — same
// single-row, no-new-line constraint (`.objective-card`'s fixed height,
// above). When this rank sits well below the strategy rank beside it, the
// reason is almost always "another strategy is faster here" —
// `fastest_strat` (views.py::_fastest_strategy) names it directly, appended
// to the same line, rather than leaving the user to guess; the tooltip
// carries the full sentence for whenever the line itself has to ellipsize.
export function EntityRankTag({ entityRank }) {
  if (!entityRank) return null;
  const fastest = entityRank.fastest_strat;
  const title = fastest
    ? `Star rank — best strategy possible · score ${entityRank.score} · fastest here: ${fastest}`
    : `Star rank — best strategy possible · score ${entityRank.score}`;
  return html`<span class="entity-rank" title=${title}>
    <span class="entity-rank-kicker">Star</span>
    <${Medal} rank=${entityRank.tier} size=${18} />
    <b>${entityRank.tier} ${entityRank.division}</b>
    ${fastest && html`<span class="meta entity-rank-fastest">· ${fastest}</span>`}
  </span>`;
}
