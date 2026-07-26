// src/sm64_events/ui/components/rankicon.js — the rank-icon STYLE registry
// + dispatcher (task 8, 2026-07-25-mario-cap-rank-icons, on user feedback:
// "add a setting... to use medals INSTEAD of the visual layer we added for
// the hats... it would expand our ability to add a third, fourth, fifth
// option as we experiment. Hats as default.").
//
// A STYLE owns only how a rank is DRAWN, never which rank it is or what
// it's called -- that identity (name, colour, pattern) stays in
// caps.js::CAP. Adding a fifth style is one ICON_STYLES entry plus one
// renderer, touching NO call site: every one of the seventeen places that
// draw a rank icon renders <RankIcon .../> with the exact prop surface Hat
// had (tier/division/size/title/flap/foldWings) and never learns which
// style is active.
//
// That last part is why the active style can't just be a prop threaded down
// from store.js's `t` the way starIcons is: most of those seventeen call
// sites have NO `t` in scope at all -- LadderBar, ScopeChips, RankBanner,
// MareloBar, PracticeCell and celebrate.js's three overlays all render a
// rank icon today with no tracker-state prop anywhere in their signature.
// Re-plumbing every one of those just to hand down a style string is
// exactly the seventeen-call-site edit this task exists to avoid. So the
// active style lives in ONE module-level value here -- written through
// setRankIconStyle (localStorage + every mounted RankIcon), read through a
// tiny subscribe hook -- and store.js's own `rankIcons`/`pickRankIcons`
// (state-shape parity with starIcons, purely for header.js's <select>) is a
// thin proxy over this same pair, so there is exactly one place the value
// actually lives.
import { useEffect, useState } from "preact/hooks";
import { h } from "preact";
import { Hat } from "./hat.js";
import { Medal } from "./medal.js";

export const STORAGE_KEY = "sm64.rankIcons";
export const DEFAULT_ICON_STYLE = "hat";

// Key order is the order header.js's settings <select> offers them. `hat`
// is FIRST and is the default -- its default-ness is load-bearing per the
// user ("Hats as default"), pinned by test_ui_caps.py.
export const ICON_STYLES = {
  hat: { label: "Mario caps", render: Hat },
  medal: { label: "Medals", render: Medal },
};

function readStoredStyle() {
  const stored = typeof localStorage !== "undefined"
    ? localStorage.getItem(STORAGE_KEY) : null;
  return stored && ICON_STYLES[stored] ? stored : DEFAULT_ICON_STYLE;
}

let activeStyle = readStoredStyle();
const listeners = new Set();

export function getRankIconStyle() {
  return activeStyle;
}

// Persists + notifies every mounted RankIcon (and store.js's proxy state,
// via its own listener) so toggling the setting repaints every surface in
// the same tick -- the banner, the practice cells, the Rank tab, the ladder
// scale, the breakdown rows and the chart all read through this one path.
export function setRankIconStyle(style) {
  if (!ICON_STYLES[style] || style === activeStyle) return;
  activeStyle = style;
  localStorage.setItem(STORAGE_KEY, style);
  listeners.forEach((notify) => notify(style));
}

function useRankIconStyle() {
  const [style, setStyle] = useState(activeStyle);
  useEffect(() => {
    listeners.add(setStyle);
    return () => listeners.delete(setStyle);
  }, []);
  return style;
}

// THE dispatcher. Same prop surface Hat has today, so the seventeen-call-site
// sweep was mechanical (import + tag rename only). Props a style doesn't
// understand (a medal has no wings) are simply unread by that style's own
// function signature -- never an error, never a warning.
export function RankIcon(props) {
  const style = useRankIconStyle();
  const entry = ICON_STYLES[style] || ICON_STYLES[DEFAULT_ICON_STYLE];
  return h(entry.render, props);
}
