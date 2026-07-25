// src/sm64_events/ui/components/marelo.js — MARELO crest + header bar.
// Mirrors ranks/scoring.py's division numerals; the tier palette is
// ranks.js RANK_COLORS (one registry, mirrored once).
import { h } from "preact";
import htm from "htm";
import { rankColor } from "./ranks.js";
const html = htm.bind(h);

export const fmtScore = (n) => (n == null ? "–" : n.toFixed(1));

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
  if (!marelo) return null;
  const { tier, division, label, mastery, coverage, n, practiced } = marelo;
  const score = marelo.marelo;
  // Endowed progress (spec section 2.4): the track shows how far into the
  // CURRENT division you are, so there is a near goal even at Iron V. The
  // server computes it (it owns the band edges) -- do not re-derive it here.
  const fill = Math.round((marelo.division_progress || 0) * 100);
  return html`<button type="button" class="marelo-bar" onclick=${onOpen}
      title=${`${label}: mastery ${fmtScore(mastery)} x coverage ${practiced}/${n}`}>
    <${Crest} tier=${tier} division=${division} />
    <span class="marelo-bar-text">
      <b>${tier ? `${tier} ${division}` : "Unranked"}</b>
      <span class="meta">${label} · ${fmtScore(score)}</span>
    </span>
    <span class="marelo-track"><i style=${`width:${fill}%;background:${rankColor(tier)}`}></i></span>
    <span class="meta marelo-split">M ${fmtScore(mastery)} · C ${
      n ? Math.round((coverage || 0) * 100) : 0}%</span>
  </button>`;
}
