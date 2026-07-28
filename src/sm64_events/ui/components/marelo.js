// src/sm64_events/ui/components/marelo.js — MARELO header bar.
// Mirrors ranks/scoring.py's division numerals; the tier palette lives in
// caps.js (Task 1, 2026-07-25-mario-cap-rank-icons) — this file never keeps
// its own copy, it imports rankColor same as every other consumer.
import { h } from "preact";
import htm from "htm";
import { capName, divisionDigit } from "./caps.js";
import { RankIcon } from "./rankicon.js";
import { useTween } from "../useTween.js";
import { useRankClimb } from "../rankclimb.js";
import { CardSelect } from "./contextselect.js";
const html = htm.bind(h);

export const fmtScore = (n) => (n == null ? "–" : n.toFixed(1));

// ---- Points -----------------------------------------------------------
// The server stays canonical 0-100 (`marelo`, `gain`, `next_division_at`,
// entity `score` -- none of it changes unit). POINTS is a x100 DISPLAY
// multiplier applied HERE and only here (live report 2026-07-25: "+1.88"
// next to "+0.60" told a user nothing -- LP works because it's a round
// integer on one scale). Every surface that shows a MARELO number imports
// this instead of multiplying its own copy, so two surfaces can never
// round differently and quietly disagree.
//
// x100, not x10: x10 would render 9.6 as 96, which reads as Grandmaster on
// this system's OWN anchor table (Mario >=95) -- two scales that merely
// LOOK alike is worse than one that's plainly ugly. x100 also keeps the
// resolution x10 would round away: a +0.12 gain is a real, visible +12
// points; at x10 it rounds to +1, erasing the exact granularity a
// fractional gain exists to preserve.
export const toPoints = (score) => (score == null ? null : Math.round(score * 100));

// String form with fmtScore's own "–" sentinel for null, so a missing
// value reads identically whether the caller wanted the raw score or points.
export const fmtPoints = (score) => (score == null ? "–" : String(toPoints(score)));

// The route rank card — slot 2 of the header's context grid.
//
// It was the MARELO bar (a <button> jumping to the Rank tab) until
// 2026-07-28. The user's report: "the M 25.6 and C 16% feels like worthless
// AI slop information to me. It should just be clear that this is the OVERALL
// RANKING FOR THE ROUTE THAT I'M PRACTICING. Maybe we can combine the
// 'practice plan' card with this rank display card to create a route rank
// card?" So the practice-plan <select> moved up here — the two controls were
// already the same thing, and practice.js's own comment said so — and the
// card's one gesture is now the route picker, like its three siblings. The
// Rank tab keeps its place in the nav rail.
//
// Mastery and Coverage are not deleted, they are rehomed: the Rank tab draws
// both as real meters, and this card's title still spells them out.
//
// `rank` and `interactive` exist for ONE caller, the celebration overlay
// (components/marelocelebrate.js, Wave 3): it renders this same card, parked
// at the BEFORE rank and with no dropdown, so the thing that flies to the
// centre of the screen is the card itself rather than a lookalike. `tune` is
// forwarded to useRankClimb the same way -- Wave 3 adds the option there;
// today's rankclimb.js simply ignores it, which is what lets the two waves
// compose without a rankclimb.js edit here.
export function RouteRankCard({ marelo, routes = [], activeRouteId = null,
                               onPickRoute = null, identity = null,
                               rank = undefined, interactive = true,
                               tune = null }) {
  // Hooks run unconditionally (rules of hooks) — `null` passes straight
  // through both with no animation.
  const shown = rank !== undefined ? rank
    : (marelo && marelo.tier
       ? { tier: marelo.tier, division: marelo.division,
           fill: marelo.division_progress || 0 }
       : null);
  const climb = useRankClimb(shown, identity, { tune });
  const score = useTween(marelo ? marelo.marelo : null);

  const { label = null, mastery = null, coverage = null,
          n = 0, practiced = 0 } = marelo || {};
  // Named for what the card is RATING, so "Overall" never reads as a route
  // that happens to be called Overall (user, 2026-07-28). ".context-label"
  // is opted into tools/responsive_probe.js's NEVER_TRUNCATE list (it is
  // considered irreducible everywhere else it is used -- Session/Clock/
  // Grading), and "Overall rank"/"Route rank" doesn't fit this card's own
  // text column at every width a one-word label does. Same fix this
  // codebase already made for an identical squeeze (ui-ranks.md: "Round 4
  // dropped the trailing 'Rank' from both kickers") -- the big rank icon and
  // name right below it already say "rank"; the label only needs to say
  // WHOSE.
  const cardLabel = activeRouteId == null ? "Overall" : "Route";
  const options = [["", "Overall"],
                   ...routes.map((route) => [String(route.id), route.name])];

  return html`<div class=${`context-control context-select marelo-bar${
      climb && climb.climbing ? " is-climbing" : ""}`}
      style=${climb ? climb.vars : null}
      title=${label
        ? `${label}: mastery ${fmtScore(mastery)} x coverage ${practiced}/${n}`
        : "Your rating for the practice plan you have selected"}>
    ${climb ? html`<span class="rank-icon-slot marelo-bar-icon">
      <${RankIcon} ...${climb.icon} tier=${climb.tier} division=${climb.division} size=${34} />
    </span>` : html`<span class="rank-icon-slot marelo-bar-icon">–</span>`}
    <span class="marelo-bar-text">
      <span class="context-label">${cardLabel}</span>
      <b>${climb ? `${capName(climb.tier)} ${divisionDigit(climb.division)}` : "Unranked"}</b>
      ${/* Points BEFORE the scope name: the card is as wide as its column, so
           this line ellipsises, and a narrow column must drop the scope name
           rather than the one part of the line that is a value. */
        null}
      <span class="meta">${fmtPoints(score)} pts · ${label || "…"}</span>
    </span>
    <span class="marelo-track"><i style=${`width:${climb ? climb.fill * 100 : 0}%`}></i></span>
    ${interactive && onPickRoute ? html`<${CardSelect} id="route-select"
      name="active_route" label=${cardLabel}
      title="Which route you are practising — this is also what the rank rates"
      options=${options} value=${activeRouteId == null ? "" : String(activeRouteId)}
      onChange=${(event) => onPickRoute(
        event.target.value ? Number(event.target.value) : null)} />` : null}
  </div>`;
}
