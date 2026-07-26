// src/sm64_events/ui/components/hat.js — the Mario-cap rank icon renderer.
//
// Ports the design-probe reference sheet
// (.superpowers/sdd/2026-07-25-mario-cap-rank-icons/reference-sheet.html),
// rendered and eyeballed across 45 states before this shipped -- its layer
// composition (see the CSS in index.html, the `.hat` rule block) is known-good
// and is not to be "cleaned up" independent of a render. This file only
// wires that composition to the real registry (caps.js) and the shipped
// per-side wing sprites (task 2) instead of the reference's hardcoded
// stand-ins, so a palette or geometry change in caps.js can never drift
// from what actually renders.
import { h } from "preact";
import htm from "htm";
import {
  CAP, divisionDigit, wingTiers, rankColor, CANVAS, CAP_BOX, PATCH_BOX,
} from "./caps.js";

const html = htm.bind(h);

// One component, called with or without a division, replaces both Medal
// (ranks.js) and Crest (marelo.js) -- Task 4, 2026-07-25-mario-cap-rank-icons.
// This supersedes .claude/rules/ui.md's older note that Crest was "a CREST
// not a medal on purpose": that comment's reasoning was about SHAPE (an
// aggregate reads as "just another star's rank" if it looks identical to a
// per-entity medal), but Crest had already spread to four per-entity sites
// on the Rank tab by the time this task started, so shape was never actually
// what separated the two call sites. The real distinction was always DATA --
// does this rank carry a division to show -- which is exactly the `division`
// prop here. One component rendered twice with different props is the rule.

const HAT_DIR = "/ui/assets/hat";
const art = (stem) => `url(${HAT_DIR}/${stem}.png)`;

// Below this cap height the numeral is a smudge (design contact sheet: dead
// at 22/26, readable from 30) -- so patch/glyph/wings only draw at 30px+,
// and only when there IS a division to show. `division == null` is a DATA
// rule, not a size rule: Medal's silhouette-only call sites pass a 22px
// medal with no division, and gating on size alone would draw an empty sign
// field there.
const DETAIL_MIN_SIZE = 30;

// Ink colour for the sign-field glyph. Not part of caps.js: it is a
// rendering constant of the glyph itself (Mario red for the "M", a dark
// bronze everywhere else), not a tier's own identity colour.
const GLYPH_INK = "#1b1206";
const GLYPH_INK_MARIO = "#d81f1f";

function wingLayers(wings) {
  const layers = [];
  // Increasing tier order, each appended after the last -- later tiers
  // paint OVER earlier ones, matching the reference sheet's verified stack.
  // Each tier is split l/r (task 2) so a flap (task 6) can turn the two
  // wings in opposite directions; both sides of a tier render as their own
  // fill+shade pair.
  for (let tier = 1; tier <= wings; tier++) {
    for (const side of ["l", "r"]) {
      const stem = `wing${tier}_${side}`;
      layers.push(html`<i class="fill" style=${`--c:#eef3f7;--art:${art(stem)}`}></i>`);
      layers.push(html`<i class="shade" style=${`--art:${art(stem)}`}></i>`);
    }
  }
  return layers;
}

export function Hat({ tier, division = null, size = 18, title = null }) {
  const spec = CAP[tier] || {};
  const color = rankColor(tier);
  const detail = division != null && size >= DETAIL_MIN_SIZE;
  const wings = detail ? wingTiers(tier, division) : 0;

  // `size` is the CAP height; the element is wider than that because the
  // sprite canvas also holds the wingspan either side of it.
  const canvasHeightPx = size / CAP_BOX.height;
  const canvasWidthPx = canvasHeightPx * (CANVAS.width / CANVAS.height);

  const filters = [];
  if (spec.treatment === "translucent" || spec.treatment === "glow")
    filters.push(`drop-shadow(0 0 ${size * 0.05}px ${color})`);
  const style = `width:${canvasWidthPx}px;height:${canvasHeightPx}px;`
    + (spec.treatment === "translucent" ? "opacity:.8;" : "")
    + (filters.length ? `filter:${filters.join(" ")};` : "");

  // Layer order, bottom to top, is load-bearing: wings -> cap (or outline)
  // -> spots -> patch -> glyph. The patch sits above the spots on purpose:
  // the spot art includes a top spot the sign field is meant to cover.
  const layers = wingLayers(wings);

  if (spec.treatment === "outline") {
    // Capless must stay visible at 13px -- on the design sheet a bare
    // outline read as a broken render on the app's navy, and this is the
    // floor tier, the most common icon in the app. A dim fill of the full
    // cap silhouette sits under the ring so there is always a shape to see
    // at small sizes, not just a thin line.
    //
    // Fix round 1 (Griffin, 2026-07-25): the ring is what makes this read
    // as "an outline you don't have yet" rather than "a solid cap you do
    // have" -- painting the ring in the SAME raw tier hex as the dim fill
    // made the two indistinguishable and the icon read as a dark blob. The
    // ring must be the brightest part of the icon; the fill must stay
    // clearly subordinate to it. `color-mix` toward white keeps the tier
    // hue recognisable without touching the hex itself (this codebase
    // already uses `color-mix(in srgb, var(--tier) …, transparent)` for the
    // rank-up glow in index.html, same technique).
    //
    // The fill's OWN opacity is size-dependent: at `detail` sizes (30px+)
    // the ring itself is wide enough to read clearly, so the fill stays
    // very faint and the icon reads as a hollow outline. Below that the
    // ring's line is too thin to survive downscaling -- the fill is then
    // the ONLY thing carrying "findable at all", so it needs more presence
    // there than it's allowed at a size where the ring can carry it.
    const ringColor = `color-mix(in srgb, ${color} 55%, white)`;
    const fillOpacity = detail ? 0.14 : 0.3;
    layers.push(html`<i class="fill" style=${`--c:${color};opacity:${fillOpacity};--art:${art("cap")}`}></i>`);
    layers.push(html`<i class="fill" style=${`--c:${ringColor};--art:${art("cap_outline")}`}></i>`);
  } else {
    layers.push(html`<i class="fill" style=${`--c:${color};--art:${art("cap")}`}></i>`);
    layers.push(html`<i class="shade" style=${`--art:${art("cap")}`}></i>`);
    if (spec.treatment === "metal")
      layers.push(html`<i class="highlight" style=${`--art:${art("cap")}`}></i>`);
    if (spec.pattern) {
      layers.push(html`<i class="fill" style=${`--c:${spec.patternColor};--art:${art(spec.pattern)}`}></i>`);
      layers.push(html`<i class="spot-shade" style=${`--art:${art("cap")};--mask:${art(spec.pattern)}`}></i>`);
    }
  }

  if (detail) {
    layers.push(html`<i class="patch" style=${`--art:${art("patch")}`}></i>`);
    const glyphVars = `--patch-left:${PATCH_BOX.left * 100}%;--patch-top:${PATCH_BOX.top * 100}%;`
      + `--patch-width:${PATCH_BOX.width * 100}%;--patch-height:${PATCH_BOX.height * 100}%;`;
    const glyphColor = spec.glyph ? GLYPH_INK_MARIO : GLYPH_INK;
    layers.push(html`<i class="glyph" style=${`${glyphVars}font-size:${size * 0.26}px;color:${glyphColor}`}>${spec.glyph || divisionDigit(division)}</i>`);
  }

  return html`<span class="hat" title=${title} style=${style}>${layers}</span>`;
}
