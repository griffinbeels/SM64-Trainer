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

export function RankBanner({ banner }) {
  if (!banner || !banner.rank) {
    return html`<span class="meta">— pick a strat to see your rank</span>`;
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
