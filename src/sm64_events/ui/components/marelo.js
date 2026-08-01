// src/sm64_events/ui/components/marelo.js — MARELO header bar.
// Mirrors ranks/scoring.py's division numerals; the tier palette lives in
// caps.js (Task 1, 2026-07-25-mario-cap-rank-icons) — this file never keeps
// its own copy, it imports rankColor same as every other consumer.
import { h } from "preact";
import { useEffect } from "preact/hooks";
import htm from "htm";
import { barFill, capName, divisionDigit, rankColor } from "./caps.js";
import { RankIcon } from "./rankicon.js";
import { useTween } from "../useTween.js";
import { useRankClimb } from "../rankclimb.js";
import { useRouteSwap } from "../routeswap.js";
import { mareloTuning } from "../marelotuning.js";
import { barEase } from "../climbcurve.js";
import { CardSelect } from "./contextselect.js";
const html = htm.bind(h);

// The rank name under the icon, and the SWAP's from/to text -- one place so
// the resting label and the two halves of a crossfade can never disagree
// about what "Unranked" means or how a division prints.
const rankLabelText = (entry) => (entry && entry.tier
  ? `${capName(entry.tier)} ${divisionDigit(entry.division)}` : "Unranked");

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
                               tune = null, onClimbTier = null }) {
  // Hooks run unconditionally (rules of hooks) — `null` passes straight
  // through both with no animation.
  const shown = rank !== undefined ? rank
    : (marelo && marelo.tier
       ? { tier: marelo.tier, division: marelo.division,
           fill: marelo.division_progress || 0 }
       : null);
  const climb = useRankClimb(shown, identity, { tune });
  // Relay the climb's own PER-FRAME tier to a caller that must walk it in
  // lockstep (components/marelocelebrate.js, report 1, 2026-07-28): the
  // celebration backdrop and the app-wide ambient tint used to read
  // `celebration.from`/`.to` directly, a TWO-STATE snapshot, so the
  // background jumped straight to the destination tier's colour the instant
  // the climb began instead of walking Capless -> Toad -> ... -> Waluigi
  // alongside the card actually doing that on screen. `climb.tier` is
  // exactly the value painted right now -- this hands it out rather than
  // making a second caller re-derive it. Keyed on the tier value alone, not
  // on `climb` itself (a fresh object every animation frame via
  // rankclimb.js's `renderState`) -- this must fire once per tier crossing,
  // never once per frame of fill movement.
  useEffect(() => {
    if (onClimbTier) onClimbTier(climb ? climb.tier : null);
  }, [climb && climb.tier]);
  const score = useTween(marelo ? marelo.marelo : null);

  const { label = null, mastery = null, coverage = null,
          n = 0, practiced = 0 } = marelo || {};

  // ---- The route SWAP -----------------------------------------------------
  // "when we swap to a different route... we should reuse the squash / pop
  // animation" (user, 2026-07-28) -- an EXCHANGE, never a climb; see
  // ui/routeswap.js's header for why it is a separate clock layered on top
  // of useRankClimb's existing snap-on-identity-change rather than a
  // relaxation of it. Scoped to the ONE caller with a real route to swap
  // between: the celebration overlay always passes `rank` explicitly (never
  // `undefined`), so `routeSwapKey` stays `null` there and this never fires
  // mid-flight. `marelo.scope_id` (not `activeRouteId`) is the swap key for
  // the same reason it already drives `cardLabel` above -- it is the
  // server's own answer to "what did I just rate", so a route pick and the
  // swap it triggers can never disagree about which scope actually landed.
  const routeSwapKey = rank !== undefined ? null
    : (marelo ? (marelo.scope_id || "overall") : null);
  const swap = useRouteSwap(routeSwapKey,
    { tier: shown && shown.tier, division: shown && shown.division,
      fill: shown ? shown.fill : 0, label },
    mareloTuning());
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
  // Derived from the SAME payload as the value beneath it, never from the
  // client's own route slot: the label and the rating must answer to one
  // source or they can disagree, and they did -- "Overall" sitting directly
  // above "16 Star - LBLJ (Standard)" on any client whose localStorage had
  // never been written (2026-07-28). `scope_id` is the server's own answer to
  // "what did I just rate", so there is nothing left to keep in step.
  const cardLabel = (marelo && marelo.scope_id && marelo.scope_id !== "overall")
    ? "Route" : "Overall";
  const options = [["", "Overall"],
                   ...routes.map((route) => [String(route.id), route.name])];

  // Everything below drives off `swap.progress` alone -- the icon squash,
  // both crossfades and the bar lerp share ONE clock, which is the "this
  // should all happen with the same timing" requirement as a number rather
  // than four durations someone matched by hand. `barEase` (climbcurve.js)
  // is THE easing this codebase uses for any progress bar -- monotone,
  // lands exactly on target, never overshoots -- reused here for the two
  // text crossfades too so nothing in the swap fights the bar's own motion
  // language. The icon alone uses CELEBRATIONS' own squash curve (routeswap.js),
  // which is allowed to overshoot: a cap springing is a physical object, a
  // bar or a fade is a value, and only the first may ever pass its target.
  const swapping = !!swap;
  const swapEase = swapping ? barEase(swap.progress) : 0;
  const iconTier = swapping ? (swap.crossed ? swap.to.tier : swap.from.tier)
    : (climb ? climb.tier : null);
  const iconDivision = swapping ? (swap.crossed ? swap.to.division : swap.from.division)
    : (climb ? climb.division : null);
  const iconProps = swapping ? swap.icon : (climb ? climb.icon : null);
  const haveIcon = swapping || !!climb;
  // The swap carries RAW fills (routeswap.js snapshots the payload, not the
  // climb), so it converts here, and it lerps the two DRAWN widths rather
  // than the two fills: the bar is anchored at its midpoint for every rank
  // except the ladder floor (caps.js::barFill), so two routes at different
  // rungs do not share one scale — anchoring after the lerp would jump the
  // bar on the exact frame the exchange swaps which rank it is measuring.
  // The resting path needs no conversion: `climb.bar` is already drawn.
  const drawnFill = (entry) => (entry
    ? barFill(entry.tier, entry.division, entry.fill) : 0);
  const barFillFraction = swapping
    ? drawnFill(swap.from) + (drawnFill(swap.to) - drawnFill(swap.from)) * swapEase
    : (climb ? climb.bar : 0);
  // The track's OWN colour crosses over on the same clock -- otherwise the
  // bar would lerp smoothly while its fill colour snapped straight to the
  // new route's tier, which is exactly the "a surface that goes away
  // mid-transition is a second bug" trap this codebase has already paid for
  // once (ui/celebrations.js's tierColor, same color-mix shape, reused here
  // rather than re-derived). `climb.vars` is safe to spread underneath --
  // useRankClimb has already SNAPPED by the time a swap is showing (the
  // identity gate fired), so it carries only its own resting `--climb-color`
  // and no leftover effect variables.
  const cardStyle = swapping
    ? { ...(climb ? climb.vars : {}),
        "--climb-color": `color-mix(in srgb, ${rankColor(swap.to.tier)} `
          + `${(swapEase * 100).toFixed(1)}%, ${rankColor(swap.from.tier)})` }
    : (climb ? climb.vars : null);

  // Round 2 layout (2026-07-28, user's own sketch): a LEFT column pairing
  // the rank icon with the rank NAME directly beneath it, and a RIGHT
  // column stacking the scope name, a full-width progress bar, and the
  // points underneath it -- the bar is the dominant element now, not a
  // sliver squeezed beside three lines of text. `cardLabel` ("Overall" /
  // "Route") is no longer painted as its own kicker line -- the scope's own
  // name (`label`) already answers "for what", the big rank icon and name
  // answer "what rank" -- but it is still derived the identical scope-aware
  // way and still rides the card as the picker's accessible name (below),
  // which is what keeps it a real, live value rather than a dead assignment.
  return html`<div class=${`context-control context-select marelo-bar${
      climb && climb.climbing ? " is-climbing" : ""}${swapping ? " is-swapping" : ""}`}
      style=${cardStyle}
      title=${label
        ? `${label}: mastery ${fmtScore(mastery)} x coverage ${practiced}/${n}`
        : "Your rating for the practice plan you have selected"}>
    <span class="marelo-bar-icon-col">
      ${haveIcon ? html`<span class="rank-icon-slot marelo-bar-icon">
        <${RankIcon} ...${iconProps} tier=${iconTier} division=${iconDivision} size=${34} />
      </span>` : html`<span class="rank-icon-slot marelo-bar-icon">–</span>`}
      ${swapping ? html`<span class="marelo-swap-fade">
          <b style=${{ opacity: swap.fade.out }}>${rankLabelText(swap.from)}</b>
          <b style=${{ opacity: swap.fade.in }}>${rankLabelText(swap.to)}</b>
        </span>`
        : html`<b>${climb ? rankLabelText(climb) : "Unranked"}</b>`}
    </span>
    <span class="marelo-bar-body">
      ${swapping ? html`<span class="marelo-swap-fade">
          <span class="context-value" style=${{ opacity: swap.fade.out }}>${swap.from.label || "…"}</span>
          <span class="context-value" style=${{ opacity: swap.fade.in }}>${swap.to.label || "…"}</span>
        </span>`
        : html`<span class="context-value">${label || "…"}</span>`}
      <span class="marelo-track"><i style=${`width:${barFillFraction * 100}%`}></i></span>
      <span class="meta">${fmtPoints(score)} pts</span>
    </span>
    ${interactive && onPickRoute ? html`<${CardSelect} id="route-select"
      name="active_route" label=${cardLabel}
      title="Which route you are practising — this is also what the rank rates"
      options=${options} value=${activeRouteId == null ? "" : String(activeRouteId)}
      onChange=${(event) => onPickRoute(
        event.target.value ? Number(event.target.value) : null)} />` : null}
  </div>`;
}
