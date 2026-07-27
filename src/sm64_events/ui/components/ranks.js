// src/sm64_events/ui/components/ranks.js — the rank BANNER and the rank-mode
// list. The tier registry moved to caps.js (spec 2026-07-25-mario-cap-rank-icons);
// these re-exports keep the call sites that import RANK_NAMES/rankColor from
// here working, and are the only reason this file still exports them.
export { RANK_NAMES, rankColor } from "./caps.js";
import { h } from "preact";
import htm from "htm";
import { useRankClimb } from "../rankclimb.js";
import { capName, divisionDigit, rankAt } from "./caps.js";
import { RankIcon } from "./rankicon.js";
const html = htm.bind(h);

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

// What the floor default costs the reader: the sentinel sentence it
// replaces. It rides the kicker's tooltip rather than disappearing. The
// entity banner has no payload to take a reason from, so it says the plain
// truth instead.
function floorHint(banner) {
  return banner ? sentinelMsg(banner) : "No time recorded here yet";
}

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
//
// `hint` is the kicker's tooltip, and today has exactly one caller: the
// merged "Strategy · Star" label practice.js uses when the two measures
// grade identically (bannerLabel/bannerHint). It stays a prop rather than
// wording built here because the reason is a fact about the CALL SITE's
// data, and this component is deliberately ignorant of which two ladders
// produced the banner it was handed.
export function RankBanner({ label, banner, hint = null, identity = null,
                             atFloor: atFloorProp = false,
                             lane = null, order = 0, replayKey = null }) {
  const ranked = !!(banner && banner.rank);
  // Called unconditionally (rules of hooks) even on the sentinel/empty path
  // below — `null` passes straight through with no animation, which is what
  // a not-yet-ranked banner needs.
  //
  // This used to be `useTween` on the raw `fill`, and that was the bug task
  // 0012 exists to kill: `fill` is progress WITHIN the current division, so a
  // rank-up sent it from .95 to .05 and the bar animated BACKWARDS on the one
  // event it is there to celebrate. `useRankClimb` animates the ladder
  // POSITION instead and hands back the tier/division/fill to draw at this
  // frame — the bar is the fractional part, so it can only ever fill.
  //
  // `identity` is what makes a rank RISE distinguishable from the banner
  // simply being handed a different measurement: switching strategy, rank
  // mode or target all legitimately produce a higher rank nobody earned. The
  // caller owns it; see practice.js.
  //
  // A strategy with standards but no time yet grades as the FLOOR — Capless
  // V, an empty bar — rather than as a sentinel sentence (user, 2026-07-27:
  // "The default rank for every strategy is capless 5 with 0 pts contributed.
  // We should show the same UI as normal, just with capless 5"). That is not
  // only nicer to look at: it is what makes the FIRST rank you ever earn
  // climb. A sentinel has no ladder position, so the hook had nothing to
  // climb FROM and snapped — and a first rank is often several tiers up,
  // which is exactly the moment worth celebrating.
  //
  // Only for `unranked`, which means "this strategy has a ladder, you just
  // have no time on it". `no_ladder` (no standards at all) and `no_strat`
  // keep their sentinel: there is no ladder for a floor to sit at the bottom
  // OF, and the user's own call on the second — "you must select a strat to
  // see a rank for the strat".
  // The ENTITY banner has no payload of its own until a first time lands,
  // so the caller tells it (practice.js::ranksAreAtFloor, read off the
  // STRATEGY banner's sentinel reason -- the one place that knows a
  // ladder exists). The strategy banner can also answer for itself.
  const atFloor = !ranked
    && (atFloorProp || (!!banner && banner.reason === "unranked"));
  const graded = ranked ? {
    tier: banner.rank, division: banner.division,
    // At the top of the ladder there is no next step to fill toward, so the
    // bar is simply full — the same sentinel the old tween used.
    fill: banner.next_tier ? (banner.fill || 0) : 1,
  } : atFloor ? { ...rankAt(0), fill: 0 } : null;
  // `lane`/`order` sequence the two banners on one card: the STRATEGY rank
  // climbs, then the star's (user, 2026-07-27 -- "Strategy first. Then
  // star"). A lone banner, and the MARELO bar, pass neither and start
  // immediately.
  const climb = useRankClimb(graded, identity, { lane, order, replayKey });
  if (!graded || !climb) {
    return html`<div class="rank-banner rank-banner-empty">
      <span class="rank-banner-kicker" title=${hint}>${label}</span>
      <span class="meta">${sentinelMsg(banner)}</span>
    </div>`;
  }
  // Everything below has to survive `banner == null`: the entity banner
  // renders at the floor with no payload at all.
  const basis = ranked ? banner.basis : null;
  // Mid-climb the server's `next_tier` names the step after the rank you are
  // LANDING on, which would read as a contradiction beside a rank name that
  // is still climbing ("WALUIGI 4 -> Waluigi 3" while the cap already says
  // 3). While the climb is running the next step is derived from where the
  // bar actually is; the exact time delta is withheld until it settles,
  // because that number is only true of the final rank.
  // At the floor default the server sent no `next_tier` (it sent no rank at
  // all), so the next step is simply the one above the floor — otherwise the
  // row would read "CAPLESS 5 · top rank", which is the opposite of true.
  const settledNext = (ranked && banner.next_tier)
    ? { tier: banner.next_tier, division: banner.next_division }
    : atFloor ? rankAt(1) : null;
  // `rankAt` clamps, so at the very top of the ladder the "next" step is the
  // rank you are already on -- which would print "MARIO 1 -> Mario 1". That
  // is the same "top rank" state the settled banner spells out, so say so.
  // Straight off the climb's own level, NOT rebuilt out of the bar: the bar is
  // pinned at 1 for the whole ladder now (climbplan.js), and
  // `rankPosition(tier, division, 1)` already IS the next level -- so
  // reconstructing a position here would have named the rank after the next
  // one for the entire climb.
  const climbingNext = rankAt(climb.level + 1);
  const atCeiling = climbingNext.tier === climb.tier
    && climbingNext.division === climb.division;
  const next = climb.climbing ? (atCeiling ? null : climbingNext) : settledNext;
  const nextLabel = next ? `${capName(next.tier)} ${divisionDigit(next.division)}` : null;
  const gap = (!climb.climbing && ranked && banner.next_gap_cs != null)
    ? (banner.next_gap_cs / 100).toFixed(2) : null;
  const fillPct = climb.fill * 100;
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
  // The climb's own variables ride the banner root: `--climb-color` (the
  // tier colour, cross-faded while a tier boundary is being crossed) and the
  // per-frame effect values ui/celebrations.js contributes. Every one of
  // them has a CSS fallback, so a surface still renders correctly with no
  // celebration running.
  // The next-step line stays wiped OUT for the whole climb and is wiped back
  // IN by the settle celebration, so the reader never sees the mid-climb text
  // hard-cut to the settled one. Set here rather than in the registry because
  // it is the ABSENCE of a celebration that has to hold it hidden, and a
  // registry entry only ever describes something happening.
  const vars = climb.climbing && climb.vars["--climb-reveal"] === undefined
    ? { ...climb.vars, "--climb-reveal": 0 } : climb.vars;
  return html`<div class=${`rank-banner${climb.climbing ? " is-climbing" : ""}`} style=${vars}>
    <div class="rank-banner-row">
      <!-- At the floor default the sentinel's own wording ("no PB on this
           strategy yet") is the only thing lost by showing Capless 5, so it
           rides the kicker's tooltip rather than disappearing. -->
      <span class="rank-banner-kicker"
          title=${hint || (atFloor ? floorHint(banner) : null)}>${label}</span>
      <!-- Round 4 (addendum, task 8, 2026-07-26 -- the user: "we probably
           should push the rank name... and the rank division a little off
           to the side, since it's overlapping, it feels very cramped right
           now"). The row's own flex gap was never the gap that mattered:
           the icon component deliberately draws its wings OUTSIDE its own
           box (hat.js), so a wing-bearing icon paints past its declared
           width with nothing in the flex layout accounting for it -- at
           this size (24px) that spill is about 9px a side (the canvas
           margin caps.js's own CAP_BOX defines), wide enough to visibly
           collide with the kicker on one side and the rank name on the
           other. This wrapper's own margin reserves that spill so the
           row's real painted content stops overlapping, without widening
           the whole row's gap for every OTHER pair of children too. -->
      <span class="rank-icon-slot rank-banner-icon"><${RankIcon} ...${climb.icon}
          tier=${climb.tier} division=${climb.division} size=${24} /></span>
      <b class="rank-banner-name">${capName(climb.tier).toUpperCase()}${climb.division ? ` ${divisionDigit(climb.division)}` : ""}</b>
      ${basis && html`<span class="meta rank-banner-basis" title=${basisTitle}>${basisText}</span>`}
      <!-- "X.XXs to rank up", not a bare "−0.22s" (user, 2026-07-27) -- the
           number is the thing you chase, and a signed delta made the reader
           work out what it was a delta FROM. It wipes in left-to-right when
           the climb settles, which is also what covers the swap from the
           mid-climb next-step (derived from where the bar is) to the real
           one (the server's, with its time). -->
      <span class="meta rank-banner-next">${nextLabel
        ? html`→ <b>${nextLabel}</b>${gap ? ` · ${gap}s to rank up` : ""}` : "top rank"}</span>
    </div>
    <div class="rank-progress-track" title=${trackTitle}>
      <i style=${`width:${fillPct}%`}></i>
    </div>
  </div>`;
}
