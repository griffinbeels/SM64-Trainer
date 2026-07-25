// src/sm64_events/ui/components/ranks.js — mirrors ranks/standards.RANK_COLORS
// and ranks/classify.RANK_NAMES (keep in lockstep).
import { h } from "preact";
import htm from "htm";
import { useTween } from "../useTween.js";
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
// data: once for the strategy rank (`sec.rank` — graded on the ACTIVE
// strategy's own ladder, from _section_banner) and once for the entity's
// own rank (`sec.entity_rank` — graded on the entity's best-possible ladder
// across every strategy, from entity_rank). Same component, same layout,
// same gradient wash, same progress bar — a labelled, gradient banner
// sitting next to a small unlabelled chip read as a RENDERING FAULT to the
// user, not two deliberate measures (live report 2026-07-25, round 2 —
// "it feels like it's just a visual error entirely"). ONE component
// rendered twice, never two components that happen to look similar:
// those drift apart visually, and this bug was exactly that kind of drift.
//
// `label` names which measure this is — "Strategy" on both banners' left
// half, and the ENTITY's own noun on the right: "Star" from StarSection,
// "Segment" from SegmentSection. It comes from the call site rather than
// being hardcoded here precisely because this component renders on both
// kinds (rule 11 parity) — a fixed "Star" would be a lie on a segment card.
// The word "Rank" was dropped from both in round 4 (2026-07-25): 13
// characters of label is unaffordable on a ~390px row, the two sit adjacent
// so the short forms can't be confused, and the medal + tier beside them
// already say "rank" louder than the kicker did.
//
// `.objective-card` is a HARD fixed height (122px at desktop, 258px under
// 760px, both `overflow` values that do NOT reflow the grid) — everything
// here must fit on ONE line; a stacked layout would silently bleed the card
// into the one below it (desktop) or clip (mobile).
//
// `division`/`fill`/`next_tier`/`next_division`/`next_gap_cs` all ride the
// server's scoring.division_progress / time_for_score (views.py's
// `_graded_progress`, the ONE place both banners' data is built) — this
// component must never compute that curve itself (user report 2026-07-24,
// reaffirmed rounds 2 and 3). The bar fills within the CURRENT DIVISION (not
// the whole tier) and "next" names the next STEP, whichever it is — one
// division up within this tier, or (already at the top division) the next
// harder tier's bottom one — with the exact time still needed to reach it,
// so a good run visibly moves the bar instead of barely denting a
// whole-tier span, and the number to chase is right there next to it.
//
// The entity banner's `fastest_strat` does NOT render here (round 4,
// 2026-07-25): on the live-report card it and the `next:` target both got
// clipped mid-word competing for the same line — a tooltip on truncated
// visible text still reads as a layout fault. It moved to the card's
// strategy header instead (practice.js, next to the strategy picker, where
// the two strategy NAMES sit next to each other and the comparison is the
// point) — a wider, quieter region than this banner has room for. This
// component's job shrank back to what always fit: rank / division / bar /
// next, none of which can be abbreviated.
export function RankBanner({ label, banner }) {
  const ranked = !!(banner && banner.rank);
  // Called unconditionally (rules of hooks) even on the sentinel/empty
  // path below — `null` passes straight through useTween with no
  // animation, which is exactly what a not-yet-ranked banner needs. This is
  // the ONE division-fill tween for both banners this component renders
  // (Strategy AND the entity's own Star/Segment banner, spec task F2) — a
  // fresh attempt lands here as a fill % change and climbs to it instead of
  // snapping, the same primitive every other numeric surface uses.
  const rawFillPct = ranked ? (banner.next_tier ? Math.round((banner.fill || 0) * 100) : 100) : null;
  const fillPct = useTween(rawFillPct);
  if (!ranked) {
    return html`<div class="rank-banner rank-banner-empty">
      <span class="rank-banner-kicker">${label}</span>
      <span class="meta">${sentinelMsg(banner)}</span>
    </div>`;
  }
  const c = rankColor(banner.rank);
  const basis = banner.basis;
  const nextLabel = banner.next_tier ? `${banner.next_tier} ${banner.next_division}` : null;
  const gap = banner.next_gap_cs != null ? (banner.next_gap_cs / 100).toFixed(2) : null;
  const displayFillPct = Math.round(fillPct);
  // The mode name (e.g. "Avg 10") is dropped from the VISIBLE basis text —
  // round 4, 2026-07-25: it's global app state already shown in the
  // header's Rank Mode picker, not something this row needs to repeat, and
  // dropping it (plus "avg of") was the difference between fitting and
  // overflowing on the avg-mode fixture. This is wording around the rank
  // data, not the rank data itself — the tier/division/count/time stay
  // exactly as graded.
  const basisText = basis && `${basis.count}${basis.window ? `/${basis.window}` : ""}·${basis.display}`;
  const basisTitle = basis && `${MODE_LABEL[banner.mode] || banner.mode} — `
    + `avg of ${basis.count}${basis.window ? `/${basis.window}` : ""} valid runs`
    + ` — ${basis.display}`;
  // The basis line is `display:none` below a 1250px pane (index.html's
  // @container rules), and a hidden element cannot be hovered — so its own
  // `title` is NOT a fallback at exactly the widths where it disappears.
  // The progress track is the one element that is always rendered and spans
  // the banner's full width, so the basis rides ITS tooltip too. That keeps
  // "what time is this rank graded on" recoverable at every width, which is
  // the premise the whole hide-the-basis decision rests on.
  const trackTitle = [nextLabel ? `${displayFillPct}% of the way to ${nextLabel}` : "top rank",
    basisTitle].filter(Boolean).join(" · ");
  return html`<div class="rank-banner">
    <div class="rank-banner-row">
      <span class="rank-banner-kicker">${label}</span>
      <${Medal} rank=${banner.rank} size=${24} />
      <b class="rank-banner-name">${banner.rank.toUpperCase()}${banner.division ? ` ${banner.division}` : ""}</b>
      ${basis && html`<span class="meta rank-banner-basis" title=${basisTitle}>${basisText}</span>`}
      <span class="meta rank-banner-next">${nextLabel
        ? html`→ <b>${nextLabel}</b>${gap ? ` −${gap}s` : ""}` : "top rank"}</span>
    </div>
    <div class="rank-progress-track" title=${trackTitle}>
      <i style=${`width:${fillPct}%;background:${c}`}></i>
    </div>
  </div>`;
}
