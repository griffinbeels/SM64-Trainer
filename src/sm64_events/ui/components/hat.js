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

// Capless's ring, low-poly outline (round 2 of the dotted-ring addendum,
// task 8, 2026-07-26): an SVG polygon walked around the cap silhouette,
// dashed via `stroke-dasharray` -- uniform BY CONSTRUCTION, unlike the
// round-1 attempt (two intersecting CSS masks). That approach's defect was
// angular, not scalar: a LINEAR gradient's band, clipped by a CURVED
// contour, gives a dash whose apparent length depends on the angle between
// the band and the local tangent -- no period tuning fixes that, only
// measuring dash length ALONG the contour does, which is exactly what
// stroke-dasharray does natively.
//
// Points are fractions of the cap's OWN silhouette canvas (measured via
// `cv2.findContours` + `approxPolyDP` on cap.png's alpha channel, epsilon
// 0.6% of the traced perimeter -- an 11-point low-poly approximation of the
// real dome shape, close enough that the dashed ring and the solid cap
// silhouette agree on where the edge is). This canvas shares its aspect
// ratio and fractional-position convention with CANVAS/CAP_BOX/PATCH_BOX
// (verified: this polygon's own bounding box is left=0.1562/top=0.1933/
// width=0.6856/height=0.7993, matching CAP_BOX's 0.155885/0.194074/
// 0.688231/0.801481 to three decimal places) -- so these fractions convert
// to pixels the SAME way every other layer's geometry does, against
// canvasWidthPx/canvasHeightPx.
const CAP_OUTLINE_POLY = [
  [0.1562, 0.5874], [0.2520, 0.8922], [0.3027, 0.9591], [0.3926, 0.9926],
  [0.6172, 0.9926], [0.7031, 0.9591], [0.7930, 0.7955], [0.8418, 0.5911],
  [0.6035, 0.1933], [0.3926, 0.1933], [0.2754, 0.3606],
];

// Whether the patch/glyph/wings draw is a DATA rule alone -- `division !=
// null` -- with NO size floor (correction, addendum, task 8, 2026-07-26: the
// user rejected the earlier `size >= DETAIL_MIN_SIZE` gate outright --
// "if we're using the cap system, we must be using the wing system", every
// cap, every size). `division == null` still means no numeral/wings: you
// cannot draw a division you do not have, which is what keeps the
// tier-only ladder-scale marks (13px, one per band, no division passed) as
// clean silhouettes. DETAIL_MIN_SIZE (caps.js) now backs only ONE purely
// visual size tuning below, which never hides content: the outline ring's
// own fill opacity (a thin ring needs a more visible fill to survive
// downscaling).

// Ink colour for the sign-field glyph. Not part of caps.js: it is a
// rendering constant of the glyph itself (Mario red for the "M", a dark
// bronze everywhere else), not a tier's own identity colour.
const GLYPH_INK = "#1b1206";
const GLYPH_INK_MARIO = "#d81f1f";

// The glyph's own font-size, computed from the sign field's ACTUAL pixel
// geometry and the font's OWN ink metrics -- not a flat fraction of `size`
// (addendum round 2, task 8, 2026-07-26: the user's target is "as big as
// possible without overflowing the patch... about 80% of the patch, so a
// 10% margin all around" -- 80% is the estimate to aim at, not a spec value).
//
// A CSS `font-size` is an EM box, not the glyph's own ink box, and this is
// NOT a 1:1 relationship -- measured directly off SuperMario256.ttf
// (PIL `font.getbbox(ch)` at a 1000px reference size, not assumed):
// every digit's ink height is ~74.2% of the requested font-size (consistent
// across 0-9), but ink WIDTH varies per glyph -- "4" is the widest of the
// five division digits divisionDigit ever returns (76.1% of font-size),
// and "M" is a different animal entirely: 74.2% tall (same ratio) but
// 115.6% WIDE -- wider than its own em box, because it is a chunky display
// face. Sizing purely off patch HEIGHT (as a naive "ink-height ×
// height-ratio = 80% of patch height" calc would) makes "M" overflow the
// patch's width well before it reaches 80% of the height, since the patch
// is a DOME (PATCH_BOX) -- 0.6785×size wide but only 0.512×size tall, i.e.
// proportionally WIDE, and "M" is proportionally the widest glyph shown.
// So every glyph is sized by the SMALLER of a height-budget and a
// width-budget candidate: digits are height-bound (their width headroom is
// ample), "M" is width-bound (bound by its own unusual width, landing at a
// smaller font-size and therefore under 80% height -- an intentional
// trade, not a miss, verified by render rather than assumed correct).
const FONT_INK_HEIGHT_RATIO = 0.742;
const FONT_INK_WIDTH_RATIO_DIGIT = 0.761;   // "4", the widest of divisionDigit's 1-5
const FONT_INK_WIDTH_RATIO_MARIO = 1.156;   // "M" -- wider than its own em box
const GLYPH_HEIGHT_TARGET = 0.80;           // fraction of patch HEIGHT the ink should reach
const GLYPH_WIDTH_MARGIN = 0.80;            // fraction of patch WIDTH the ink must not exceed

function glyphFontSizePx(glyphChar, patchWidthPx, patchHeightPx) {
  const heightBoundPx = (GLYPH_HEIGHT_TARGET * patchHeightPx) / FONT_INK_HEIGHT_RATIO;
  const widthRatio = glyphChar === "M" ? FONT_INK_WIDTH_RATIO_MARIO : FONT_INK_WIDTH_RATIO_DIGIT;
  const widthBoundPx = (GLYPH_WIDTH_MARGIN * patchWidthPx) / widthRatio;
  return Math.min(heightBoundPx, widthBoundPx);
}

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
  // DATA rule alone, no size floor (see the block comment above) -- a
  // division draws at every size. wingTiers itself already returns 0 for
  // Iron/Capless regardless of division (caps.js), so nothing here needs to
  // special-case that tier.
  const hasDivision = division != null;
  // Purely visual: below this size the outline ring's own line is too thin
  // to read on its own, so its underlying fill needs more presence. Never
  // gates whether content draws, only how opaque the ring's fill is.
  const ringNeedsMoreFill = size < DETAIL_MIN_SIZE;
  const folding = foldWings > 0;
  const wings = folding ? foldWings : (hasDivision ? wingTiers(tier, division) : 0);

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
    // The fill's OWN opacity is size-dependent (`ringNeedsMoreFill`, size
    // alone -- decoupled from `hasDivision` since task 8's addendum, which
    // is a DATA rule): at DETAIL_MIN_SIZE and above the ring itself is wide
    // enough to read clearly, so the fill stays very faint and the icon
    // reads as a hollow outline. Below that the ring's line is too thin to
    // survive downscaling -- the fill is then the ONLY thing carrying
    // "findable at all", so it needs more presence there than it's allowed
    // at a size where the ring can carry it.
    const ringColor = `color-mix(in srgb, ${color} 55%, white)`;
    const fillOpacity = ringNeedsMoreFill ? 0.3 : 0.14;
    layers.push(html`<i class="fill" style=${`--c:${color};opacity:${fillOpacity};--art:${art("cap")}`}></i>`);
    // The ring reads as "you don't have this yet" rather than a thin solid
    // cap -- dashed, walked around CAP_OUTLINE_POLY (round 2 of the addendum,
    // task 8, 2026-07-26; see that constant's own comment for why an SVG
    // polygon replaced round 1's CSS mask-composite attempt, which produced
    // visibly uneven dashes -- a defect of intersecting a LINEAR gradient
    // with a CURVED contour, not fixable by tuning the gradient's period).
    // `pathLength="100"` normalizes the polygon's traced length for
    // dasharray purposes, so "4 3.5" always means the same fourteen-ish
    // dash+gap repeats regardless of the polygon's actual perimeter -- which
    // itself scales linearly with `size` via canvasWidthPx/canvasHeightPx,
    // so the dash COUNT (and so the visual rhythm) stays constant across
    // sizes, same intent as round 1's percentage-sized mask tile, done
    // correctly this time. The viewBox matches the SVG's own rendered pixel
    // size exactly (canvasWidthPx × canvasHeightPx, not a normalized 0..1
    // box) so the coordinate scale is UNIFORM in both axes -- CAP_OUTLINE_POLY
    // is not square, and a non-uniform (distorted) viewBox scale would have
    // reintroduced the exact angular defect this rewrite exists to remove,
    // just via a different mechanism. Only the RING is dashed; the dim
    // under-fill above stays solid on purpose (constraint 2 of the addendum
    // -- it is what keeps Capless findable at 13px, and dotting it too would
    // remove the one thing carrying that job).
    const ringPoints = CAP_OUTLINE_POLY
      .map(([xFrac, yFrac]) => `${xFrac * canvasWidthPx},${yFrac * canvasHeightPx}`)
      .join(" ");
    const ringStrokeWidth = Math.max(1, size * 0.05);
    layers.push(html`<svg class="dotted-ring" viewBox=${`0 0 ${canvasWidthPx} ${canvasHeightPx}`}
        preserveAspectRatio="none">
      <polygon points=${ringPoints} pathLength="100"
          style=${`stroke:${ringColor};stroke-width:${ringStrokeWidth};stroke-dasharray:4 3.5`} />
    </svg>`);
  } else {
    layers.push(...tintedPair("cap", color));
    if (spec.treatment === "metal")
      layers.push(html`<i class="highlight" style=${`--art:${art("cap")}`}></i>`);
    if (spec.pattern) {
      layers.push(html`<i class="fill" style=${`--c:${spec.patternColor};--art:${art(spec.pattern)}`}></i>`);
      layers.push(html`<i class="spot-shade" style=${`--art:${art("cap")};--mask:${art(spec.pattern)}`}></i>`);
    }
  }

  if (hasDivision) {
    layers.push(html`<i class="patch" style=${`--art:${art("patch")}`}></i>`);
    const glyphVars = `--patch-left:${PATCH_BOX.left * 100}%;--patch-top:${PATCH_BOX.top * 100}%;`
      + `--patch-width:${PATCH_BOX.width * 100}%;--patch-height:${PATCH_BOX.height * 100}%;`;
    const glyphColor = spec.glyph ? GLYPH_INK_MARIO : GLYPH_INK;
    const glyphChar = spec.glyph || divisionDigit(division);
    const patchWidthPx = PATCH_BOX.width * canvasWidthPx;
    const patchHeightPx = PATCH_BOX.height * canvasHeightPx;
    const glyphFontSize = glyphFontSizePx(glyphChar, patchWidthPx, patchHeightPx);
    layers.push(html`<i class="glyph" style=${`${glyphVars}font-size:${glyphFontSize}px;color:${glyphColor}`}>${glyphChar}</i>`);
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
