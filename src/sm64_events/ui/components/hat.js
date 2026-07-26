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
  CAP, capName, divisionDigit, wingTiers, rankColor, CANVAS, CAP_BOX, PATCH_BOX,
  DETAIL_MIN_SIZE,
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

// DETAIL_MIN_SIZE (caps.js) -- so patch/glyph/wings only draw at 30px+, and
// only when there IS a division to show. `division == null` is a DATA rule,
// not a size rule: Medal's silhouette-only call sites pass a 22px medal with
// no division, and gating on size alone would draw an empty sign field
// there. Shared with every other style (rankicon.js::ICON_STYLES) so a
// division switches detail at the same size everywhere.

// Ink colour for the sign-field glyph. Not part of caps.js: it is a
// rendering constant of the glyph itself (Mario red for the "M", a dark
// bronze everywhere else), not a tier's own identity colour.
const GLYPH_INK = "#1b1206";
const GLYPH_INK_MARIO = "#d81f1f";

// A tinted layer is always a `.fill` (mask) + `.shade` (multiply) PAIR
// reading the SAME art file -- index.html's `.hat .fill`/`.hat .shade` rules
// both resolve `var(--art)`, and that CSS-side agreement only holds if the
// two elements were handed the same URL to begin with. Routing every pair
// through this ONE helper makes that structural rather than a convention to
// remember: `art(stem)` is resolved ONCE and reused for both layers, so
// there is nowhere to hand them different files without editing this
// function itself (final review I4, 2026-07-25 -- previously each pair set
// `--art` twice, independently, on sibling elements, and changing one
// `art("cap")` call and not its twin broke the tint with the whole suite
// green). `extraClass` carries the wing side marker (wing-l/wing-r)
// alongside fill/shade, not instead of them.
function tintedPair(stem, color, extraClass = "") {
  const artUrl = art(stem);
  const withSide = (base) => (extraClass ? `${base} ${extraClass}` : base);
  return [
    html`<i class=${withSide("fill")} style=${`--c:${color};--art:${artUrl}`}></i>`,
    html`<i class=${withSide("shade")} style=${`--art:${artUrl}`}></i>`,
  ];
}

function wingLayers(wings) {
  const layers = [];
  // Increasing tier order, each appended after the last -- later tiers
  // paint OVER earlier ones, matching the reference sheet's verified stack.
  // Each tier is split l/r (task 2) so a flap (task 6) or a fold (task 10)
  // can turn the two wings independently; both sides of a tier render as
  // their own fill+shade pair via tintedPair. The side class (wing-l/wing-r)
  // is what index.html's flap/fold keyframes select on -- it carries no
  // styling of its own.
  for (let tier = 1; tier <= wings; tier++) {
    for (const side of ["l", "r"]) {
      const stem = `wing${tier}_${side}`;
      const sideClass = side === "l" ? "wing-l" : "wing-r";
      layers.push(...tintedPair(stem, "#eef3f7", sideClass));
    }
  }
  return layers;
}

// `flap`: true only at celebrate.js's three rank-up call sites (task 6). It
// adds the `hat-flap` modifier class index.html's keyframes select on --
// every OTHER Hat in the app (Rank tab tiles, MareloBar, RankBanner medals,
// the practice cards) renders the exact same wings motionless. Constant
// idle flapping across a screen of medals would be motion noise, and this
// app runs on stream.
//
// `foldWings`: >0 only for the one render TierRankUp shows at the fill->flip
// boundary (task 10, addendum, 2026-07-25 -- "the wings will disappear...
// animate them down and behind the cap, like a dog tucking its ears").
// Division alone would drop straight from `celebration.from.division`'s
// wing count to division V's zero the instant the tier climb begins --
// this decouples the WING COUNT from the shown division for exactly that
// one tick, so the outgoing wings can still be drawn (and folded away)
// while the glyph already reads the climbing tier's "no real division"
// digit. `flap` and `foldWings` are mutually exclusive on the wing layers
// (fold wins if both are passed, since celebrate.js does not bother
// conditionally dropping `flap` for that one tick) -- see index.html's
// `.hat-fold` rules.
export function Hat({ tier, division = null, size = 18, title = null, flap = false, foldWings = 0 }) {
  const spec = CAP[tier] || {};
  const color = rankColor(tier);
  const detail = division != null && size >= DETAIL_MIN_SIZE;
  const folding = foldWings > 0;
  const wings = folding ? foldWings : (detail ? wingTiers(tier, division) : 0);

  // `size` is the CAP's own footprint, both axes -- the element Medal/Crest
  // used to occupy was exactly `size`px tall (and, being square, `size`px
  // wide too) in every fixed-height card this replaces, so the OUTER box
  // below must stay exactly the CAP's height and width, not the sprite
  // CANVAS's -- the canvas is both taller AND wider than the cap (it also
  // holds the wingspan above/beside it). (Fix round 1, Griffin 2026-07-25,
  // height axis only: the first cut sized the outer box to the full canvas
  // height, growing every caller's row ~6px. Fix round 2, final review I1,
  // 2026-07-25: round 1's own argument -- "the box matches what Medal/Crest
  // occupied" -- was axis-neutral and had simply never been applied to
  // width, which left the box 45% wider than the cap it draws at every one
  // of the nine call sites that never pass a division, and left the
  // wingless division-V tier-up landing state paying for wingspan it could
  // never use either.) The full canvas renders in an INNER wrapper, shifted
  // up AND left by the cap's own offset within it (capTopPx/capLeftPx) so
  // the cap aligns with the outer box's top-left corner -- everything
  // outside that (the wings) spills off the outer box on purpose, on
  // whichever side(s) they grow. Only an ancestor with its own
  // overflow:hidden can clip that spill; `.hat` itself never does
  // (index.html).
  const canvasHeightPx = size / CAP_BOX.height;
  const canvasWidthPx = canvasHeightPx * (CANVAS.width / CANVAS.height);
  const capTopPx = CAP_BOX.top * canvasHeightPx;
  const capLeftPx = CAP_BOX.left * canvasWidthPx;
  const capWidthPx = CAP_BOX.width * canvasWidthPx;

  const outerStyle = `width:${capWidthPx}px;height:${size}px;`;

  const filters = [];
  if (spec.treatment === "translucent" || spec.treatment === "glow")
    filters.push(`drop-shadow(0 0 ${size * 0.05}px ${color})`);
  const canvasStyle = `width:${canvasWidthPx}px;height:${canvasHeightPx}px;`
    + `top:${-capTopPx}px;left:${-capLeftPx}px;`
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
    layers.push(...tintedPair("cap", color));
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

  // A DEFAULT title, derived from the same registry the icon itself reads,
  // so no call site has to remember to pass one (final review I2,
  // 2026-07-25: `title` was dead code -- all seventeen production call
  // sites omitted it, and both components this replaced always set one).
  // A caller that DOES pass an explicit `title` still wins -- this only
  // fills the gap, it never overrides.
  const defaultTitle = tier
    ? `${capName(tier)}${division ? ` ${divisionDigit(division)}` : ""}`
    : "Unranked";
  const resolvedTitle = title != null ? title : defaultTitle;

  const motionClass = folding ? " hat-fold" : flap ? " hat-flap" : "";
  return html`<span class=${`hat${motionClass}`} title=${resolvedTitle} style=${outerStyle}>
    <span class="hat-canvas" style=${canvasStyle}>${layers}</span>
  </span>`;
}
