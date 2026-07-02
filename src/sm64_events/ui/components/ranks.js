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

// Scalable rank disc — same colors as Medal but drawn as SVG, so it fills (and
// scales with) its container instead of a fixed px box. Used to overlay a rank
// badge centered on a star (stagebanner.js StarRow), where the star itself
// scales with the pane. The caller's wrapper owns positioning + pointer-events.
export function RankDisc({ rank }) {
  const bg = rankColor(rank), fg = FG[rank] || "#7e8796";
  return html`<svg viewBox="0 0 100 100" width="100%" height="100%"
      style="display:block" aria-hidden="true">
    <circle cx="50" cy="50" r="45" fill=${bg}
            stroke="rgba(255,255,255,.65)" stroke-width="6" />
    <text x="50" y="52" text-anchor="middle" dominant-baseline="central"
          font-size="56" font-weight="700" fill=${fg}>${rank ? "★" : "–"}</text>
  </svg>`;
}

// Sentinel wording (server sends {rank:null, reason}): a strategy is ranked
// ONLY by times achieved with it, so "unranked" means no PB on THIS strat yet
// (a PB on another strat doesn't count) — distinct from "pick a strat".
const RANK_SENTINEL = {
  unranked: "— unranked (no PB on this strategy yet)",
  no_ladder: "— no rank standards for this strategy",
  no_strat: "— pick a strat to see your rank",
};

export function RankBanner({ banner }) {
  if (!banner || !banner.rank) {
    const msg = (banner && RANK_SENTINEL[banner.reason]) || RANK_SENTINEL.no_strat;
    return html`<span class="meta">${msg}</span>`;
  }
  const c = rankColor(banner.rank);
  const gap = banner.gap_cs != null ? (banner.gap_cs / 100).toFixed(2) : null;
  return html`<div style=${`display:flex;align-items:center;gap:12px;border:1px solid ${c}55;border-radius:8px;padding:8px 12px;background:linear-gradient(90deg, ${c}33, transparent)`}>
    <${Medal} rank=${banner.rank} size=${30} />
    <div>
      <div style="font-weight:800;letter-spacing:.4px">${banner.rank.toUpperCase()}</div>
      ${banner.next
        ? html`<div class="meta">next: <b>${banner.next}</b> −${gap}s
            <div style="height:6px;width:200px;background:#0d1117;border-radius:3px;margin-top:4px;overflow:hidden">
              <i style=${`display:block;height:100%;width:${Math.round((banner.fill || 0) * 100)}%;background:${c}`}></i>
            </div></div>`
        : html`<div class="meta">top rank</div>`}
    </div>
  </div>`;
}
