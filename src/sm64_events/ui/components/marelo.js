// src/sm64_events/ui/components/marelo.js — MARELO crest + header bar.
// Mirrors ranks/scoring.py's division numerals; the tier palette is
// ranks.js RANK_COLORS (one registry, mirrored once).
import { h } from "preact";
import htm from "htm";
import { rankColor } from "./ranks.js";
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

// A crest, not a medal: the section medals are per-strat and per-entity, and
// an aggregate that looked identical to them would read as "just another
// star's rank" in the header.
export function Crest({ tier, division, size = 34 }) {
  const c = rankColor(tier);
  return html`<span class="marelo-crest" title=${tier ? `${tier} ${division}` : "unranked"}
      style=${`--crest:${c};width:${size}px;height:${size}px`}>
    <b style=${`font-size:${Math.round(size * 0.34)}px`}>${division || "–"}</b>
  </span>`;
}

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
  return html`<button type="button" class="marelo-bar" onclick=${onOpen}
      title=${`${label}: mastery ${fmtScore(mastery)} x coverage ${practiced}/${n}`}>
    <${Crest} tier=${tier} division=${division} />
    <span class="marelo-bar-text">
      <b>${tier ? `${tier} ${division}` : "Unranked"}</b>
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
