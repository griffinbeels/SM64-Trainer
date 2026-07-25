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

// Rendered inside the objective card's rank slot, TWICE with different
// data: once for the Strategy Rank (`sec.rank` — graded on the ACTIVE
// strategy's own ladder, from _section_banner) and once for the Overall
// Rank (`sec.entity_rank` — graded on the entity's best-possible ladder
// across every strategy, from entity_rank). Same component, same layout,
// same gradient wash, same progress bar — a labelled, gradient banner
// sitting next to a small unlabelled chip read as a RENDERING FAULT to the
// user, not two deliberate measures (live report 2026-07-25, round 2 —
// "it feels like it's just a visual error entirely"). ONE component
// rendered twice, never two components that happen to look similar:
// those drift apart visually, and this bug was exactly that kind of drift.
//
// `label` names which measure this is ("Strategy Rank" / "Overall Rank");
// "Overall" rather than "Star" because this same component renders on
// SEGMENT sections too (rule 11 parity) — "Star Rank" would be a lie there.
//
// `.objective-card` is a HARD fixed height (122px at desktop, 258px under
// 760px, both `overflow` values that do NOT reflow the grid) — everything
// here must fit on ONE line; a stacked layout would silently bleed the card
// into the one below it (desktop) or clip (mobile).
//
// `division`/`fill`/`next_tier`/`next_division` all ride the server's
// scoring.division_progress — this component must never compute that curve
// itself (user report 2026-07-24, reaffirmed round 2). The bar fills within
// the CURRENT DIVISION (not the whole tier) and "next" names the next STEP,
// whichever it is — one division up within this tier, or (already at the
// top division) the next harder tier's bottom one — so a good run visibly
// moves the bar instead of barely denting a whole-tier span.
//
// `fastest_strat` only ever appears on the Overall banner's data (entity_rank
// carries it; _section_banner never does) — reading it directly off `banner`
// needs no per-caller special-casing, the empty-on-the-other-banner case is
// just `undefined` and the line omits itself.
export function RankBanner({ label, banner }) {
  if (!banner || !banner.rank) {
    return html`<div class="rank-banner rank-banner-empty">
      <span class="rank-banner-kicker">${label}</span>
      <span class="meta">${sentinelMsg(banner)}</span>
    </div>`;
  }
  const c = rankColor(banner.rank);
  const basis = banner.basis;
  const fastest = banner.fastest_strat;
  const nextLabel = banner.next_tier ? `${banner.next_tier} ${banner.next_division}` : null;
  const fillPct = banner.next_tier ? Math.round((banner.fill || 0) * 100) : 100;
  return html`<div class="rank-banner">
    <div class="rank-banner-row">
      <span class="rank-banner-kicker">${label}</span>
      <${Medal} rank=${banner.rank} size=${26} />
      <b class="rank-banner-name">${banner.rank.toUpperCase()}${banner.division ? ` ${banner.division}` : ""}</b>
      ${basis && html`<span class="meta rank-banner-basis">
        ${MODE_LABEL[banner.mode] || banner.mode} · avg of ${basis.count}${basis.window ? `/${basis.window}` : ""} · ${basis.display}</span>`}
      ${fastest && html`<span class="meta rank-banner-fastest" title=${`fastest strategy here: ${fastest}`}>· ${fastest}</span>`}
      <span class="meta rank-banner-next">${nextLabel
        ? html`next: <b>${nextLabel}</b>` : "top rank"}</span>
    </div>
    <div class="rank-progress-track"
        title=${nextLabel ? `${fillPct}% of the way to ${nextLabel}` : "top rank"}>
      <i style=${`width:${fillPct}%;background:${c}`}></i>
    </div>
  </div>`;
}
