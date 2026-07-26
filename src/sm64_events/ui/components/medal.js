// src/sm64_events/ui/components/medal.js — the disc rank-icon STYLE.
//
// A style owns only how a rank is DRAWN, never which rank it is or what it's
// called -- name and colour stay in caps.js::CAP (task 8 brief,
// 2026-07-25-mario-cap-rank-icons). That is why this renders a
// Waluigi-purple disc labelled Waluigi for the tier keyed `Gold`, not a
// return to the pre-cap Gold/Silver names or their old flat palette.
//
// Ports the deleted Medal (ranks.js@6ec7f5b, before the Mario-cap rewrite)
// as a STYLE under the rankicon.js registry rather than its own component:
// same disc shape and star glyph, but two things that component hardcoded
// are now derived from the shared registry instead --
//   * a division shows the same Arabic digit the hat style shows
//     (caps.js::divisionDigit), so the two styles can never disagree about
//     what division you are;
//   * the foreground ink is computed from the tier colour's own luminance
//     (inkFor, below) instead of a per-tier FG lookup table -- that table
//     needed a hand-edit on every palette change, and two of caps.js's nine
//     colours moved during the fix wave the day after it would have shipped.
import { h } from "preact";
import htm from "htm";
import { rankColor, capName, divisionDigit, DETAIL_MIN_SIZE } from "./caps.js";

const html = htm.bind(h);

// WCAG relative-luminance crossover where black and white text give EQUAL
// contrast against a background: solving (Lbg+0.05)/(0+0.05) ==
// (1+0.05)/(Lbg+0.05) for Lbg gives sqrt(1.05*0.05) - 0.05 =~ 0.179. Above
// it black reads better; below it, white does.
const CONTRAST_CROSSOVER = 0.179;
const INK_DARK = "#1b1206";   // hat.js's GLYPH_INK -- same ink, same reason
const INK_LIGHT = "#eef3f7";  // hat.js's wing tint white

function srgbChannel(byteValue) {
  const normalized = byteValue / 255;
  return normalized <= 0.03928
    ? normalized / 12.92
    : ((normalized + 0.055) / 1.055) ** 2.4;
}

function relativeLuminance(hexColor) {
  const red = parseInt(hexColor.slice(1, 3), 16);
  const green = parseInt(hexColor.slice(3, 5), 16);
  const blue = parseInt(hexColor.slice(5, 7), 16);
  return 0.2126 * srgbChannel(red) + 0.7152 * srgbChannel(green) + 0.0722 * srgbChannel(blue);
}

function inkFor(backgroundHex) {
  return relativeLuminance(backgroundHex) > CONTRAST_CROSSOVER ? INK_DARK : INK_LIGHT;
}

// `flap`/`foldWings` are hat-style-only (a medal has no wings to animate) --
// destructured here only so they never leak onto the DOM as unknown
// attributes; a style is allowed to ignore a prop it doesn't understand,
// never error on it (spec's contract).
export function Medal({ tier, division = null, size = 18, title = null,
                        flap = false, foldWings = 0 }) {
  const background = rankColor(tier);
  const ink = inkFor(background);
  // Same condition hat.js gates its own patch/glyph on (division present AND
  // at/above DETAIL_MIN_SIZE) -- below that, or with no division at all, the
  // disc shows the star the deleted Medal always drew.
  const showDigit = division != null && size >= DETAIL_MIN_SIZE;
  const glyph = showDigit ? divisionDigit(division) : "★";

  // Same default-title contract as Hat's own `defaultTitle` (hat.js) so
  // swapping styles can never change a tooltip: cap name + division digit
  // when ranked, "Unranked" when tier is null/unrecognised -- tier==null
  // getting exactly this sentinel (not a second spelling) is the brief's
  // own requirement.
  const defaultTitle = tier
    ? `${capName(tier)}${division ? ` ${divisionDigit(division)}` : ""}`
    : "Unranked";
  const resolvedTitle = title != null ? title : defaultTitle;

  const discStyle = `display:inline-flex;align-items:center;justify-content:center;`
    + `width:${size}px;height:${size}px;border-radius:50%;background:${background};`
    + `border:2px solid rgba(255,255,255,.5);flex:0 0 auto`;
  const glyphStyle = `color:${ink};font-size:${Math.round(size * 0.5)}px;line-height:1`;

  return html`<span title=${resolvedTitle} style=${discStyle}>
    <span style=${glyphStyle}>${glyph}</span>
  </span>`;
}
