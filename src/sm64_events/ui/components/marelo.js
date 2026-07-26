// src/sm64_events/ui/components/marelo.js — MARELO header bar.
// Mirrors ranks/scoring.py's division numerals; the tier palette lives in
// caps.js (Task 1, 2026-07-25-mario-cap-rank-icons) — this file never keeps
// its own copy, it imports rankColor same as every other consumer.
import { h } from "preact";
import htm from "htm";
import { rankColor } from "./ranks.js";
import { capName, divisionDigit } from "./caps.js";
import { Hat } from "./hat.js";
import { useTween } from "../useTween.js";
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

export function MareloBar({ marelo, onOpen }) {
  // Tweened FROM the previous fetch's value (spec task F2) -- this bar is
  // mounted once in the header and never unmounts, so it's the one place a
  // rank improvement is visible from every tab, not just the Rank tab.
  // Called unconditionally (rules of hooks) ahead of the `!marelo` early
  // return; `null` passes straight through with no animation.
  const fill = useTween(marelo ? Math.round((marelo.division_progress || 0) * 100) : null);
  const score = useTween(marelo ? marelo.marelo : null);
  if (!marelo) return null;
  const { tier, division, label, mastery, coverage, n, practiced } = marelo;
  // Unranked is an EXPLICIT empty state, not a Hat drawn with no tier (final
  // review I5, 2026-07-25: `tier == null` used to still call Hat, which drew
  // a plain grey cap with nothing in it -- the deleted Crest drew a "–" for
  // the same state). PracticeCell's starrank cell already spells "no rank"
  // as a bare "–"; this reuses that spelling rather than inventing a third.
  return html`<button type="button" class="marelo-bar" onclick=${onOpen}
      title=${`${label}: mastery ${fmtScore(mastery)} x coverage ${practiced}/${n}`}>
    ${tier ? html`<${Hat} tier=${tier} division=${division} size=${34} />` : "–"}
    <span class="marelo-bar-text">
      <b>${tier ? `${capName(tier)} ${divisionDigit(division)}` : "Unranked"}</b>
      <span class="meta">${label} · ${fmtPoints(score)} pts</span>
    </span>
    <span class="marelo-track"><i style=${`width:${fill}%;background:${rankColor(tier)}`}></i></span>
    <!-- Mastery stays 0-100, never points: it's a mean SCORE (mastery x
         coverage = marelo), not a rating on the tier ladder, and running it
         through toPoints would imply a fourth scale that doesn't exist. -->
    <span class="meta marelo-split">M ${fmtScore(mastery)} · C ${
      n ? Math.round((coverage || 0) * 100) : 0}%</span>
  </button>`;
}
