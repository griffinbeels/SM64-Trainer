// src/sm64_events/ui/components/hat.js — the Mario-cap rank icon renderer.
//
// Ports a design probe (2026-07-25) that was rendered and eyeballed across all
// 45 tier x division states before this shipped; the probe was a scratch file
// and is gone, but `tools/hat_sheet.py` regenerates the same contact sheet from
// the LIVE registry, which is the reproduction that matters. Its layer
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
// 0.62, not the 0.80 this shipped with: 0.80 is a fraction of PATCH_BOX, the
// RECTANGLE around the sign field, and the field itself is a dome -- so a
// digit sized to 80% of the box crossed the white onto the cap at the bottom
// and the lower-left corner, which a high-zoom render of a Wario 4 shows
// plainly (2026-07-27). Same defect the M had, one axis over; found while
// fixing the M rather than reported, because both live reports were about
// the letter.
const GLYPH_HEIGHT_TARGET = 0.62;           // fraction of patch HEIGHT the ink should reach
const GLYPH_WIDTH_MARGIN = 0.80;            // fraction of patch WIDTH the ink must not exceed
// The "M" needs a tighter budget than the digits, and the reason is the SHAPE
// of the sign field rather than anything about the letter: PATCH_BOX is the
// rectangle AROUND the patch, but the patch art itself is a DOME, so it is
// narrower than the box everywhere except its own widest line. A digit is
// height-bound and never gets near the sides; "M" is width-bound (it is 116%
// of its own em box wide) and at 80% of the BOX it was spilling off the white
// onto the cap -- "the M in the mario cap is actually slightly too big… it
// overlaps with the hat color, and it looks wrong" (live report 2026-07-27).
// Two rounds on this number, both live reports, both "still too big". 0.80 put
// the M's arms on the red; 0.66 landed it just inside the dome's widest line,
// which is not the same as looking like it has room -- the dome NARROWS above
// and below that line, so an M that technically fits still reads as crowding
// the white. 0.56 leaves visible patch on every side at every size.
//
// Digits are provably untouched by all of this: they keep GLYPH_WIDTH_MARGIN
// and are HEIGHT-bound anyway (their width headroom is ample), so this
// constant only ever moves the one glyph that is wider than its own em box.
// It lives here, in the one function every rank icon sizes its glyph through,
// so there is nowhere else to keep in sync -- pinned by
// tests/test_ui_caps.py::test_only_hat_js_sizes_the_cap_glyph.
const GLYPH_WIDTH_MARGIN_MARIO = 0.56;

function glyphFontSizePx(glyphChar, patchWidthPx, patchHeightPx) {
  const heightBoundPx = (GLYPH_HEIGHT_TARGET * patchHeightPx) / FONT_INK_HEIGHT_RATIO;
  const isMario = glyphChar === "M";
  const widthRatio = isMario ? FONT_INK_WIDTH_RATIO_MARIO : FONT_INK_WIDTH_RATIO_DIGIT;
  const widthMargin = isMario ? GLYPH_WIDTH_MARGIN_MARIO : GLYPH_WIDTH_MARGIN;
  const widthBoundPx = (widthMargin * patchWidthPx) / widthRatio;
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
function tintedPair(stem, color, extraClass = "", extraStyle = "") {
  const artUrl = art(stem);
  const withSide = (base) => (extraClass ? `${base} ${extraClass}` : base);
  return [
    html`<i class=${withSide("fill")} style=${`--c:${color};--art:${artUrl}${extraStyle}`}></i>`,
    html`<i class=${withSide("shade")} style=${`--art:${artUrl}${extraStyle}`}></i>`,
  ];
}

// The twinkle thrown out of the cap when a tier crossing lands (user,
// 2026-07-27: "maybe a bit of star twinkling appears"). Positions are
// fractions of a 100x100 box centred on the icon; each sparkle has its own
// start offset so they pop in sequence rather than blinking as one block,
// and its own life is a half-sine, so it grows and fades without needing a
// keyframe or a second timer.
const SPARKLE_STAR =
  "M6 0C6.6 4 8 5.4 12 6C8 6.6 6.6 8 6 12C5.4 8 4 6.6 0 6C4 5.4 5.4 4 6 0Z";
const SPARKLES = [           // x, y, size, when it starts (0..1 of the burst)
  [18, 26, 1.5, 0.00], [82, 30, 1.8, 0.10], [50, 8, 1.3, 0.20],
  [30, 74, 1.1, 0.28], [74, 70, 1.4, 0.36],
];
const SPARKLE_LIFE = 0.55;

function Sparkles(burst) {
  const points = SPARKLES.map(([x, y, size, from]) => {
    const life = (burst - from) / SPARKLE_LIFE;
    if (life <= 0 || life >= 1) return null;
    const grow = Math.sin(life * Math.PI);          // 0 -> 1 -> 0
    const scale = (size * grow).toFixed(3);
    return html`<path d=${SPARKLE_STAR} fill="#fff" opacity=${grow.toFixed(3)}
        transform=${`translate(${x} ${y}) scale(${scale}) translate(-6 -6)`} />`;
  }).filter(Boolean);
  if (!points.length) return null;
  return html`<svg class="hat-sparkles" viewBox="0 0 100 100"
      preserveAspectRatio="xMidYMid meet">${points}</svg>`;
}

// `tuck` is 0 (fully out) to 1 (folded away behind the cap) -- ONE number
// driving both the fold and its reverse, the grow (task 0012). It rides the
// individual wing layers rather than `.hat` because the two differ in scope:
// a fold takes EVERY wing, while a grow takes only the pair the division just
// earned, and the established pairs beside it must not move. `null` leaves
// the layer inheriting `.hat`'s own value, which is what lets the legacy
// keyframe path (`.hat-fold`, the scope overlay) keep working untouched.
function wingLayers(wings, { growWings = 0, growProgress = 1, foldProgress = null } = {}) {
  const layers = [];
  // Increasing tier order, each appended after the last -- later tiers
  // paint OVER earlier ones, matching the reference sheet's verified stack.
  // Each tier is split l/r (task 2) so a flap (task 6) or a fold (task 10)
  // can turn the two wings independently; both sides of a tier render as
  // their own fill+shade pair via tintedPair. The side class (wing-l/wing-r)
  // is what index.html's flap/fold keyframes select on -- it carries no
  // styling of its own.
  for (let tier = 1; tier <= wings; tier++) {
    // The newest pairs are the highest-numbered ones (wingTiers counts up as
    // the division improves), so "the ones that just grew" is the tail.
    const justGrew = tier > wings - growWings;
    const tuck = foldProgress != null ? foldProgress
      : justGrew ? 1 - growProgress : null;
    const tuckStyle = tuck != null ? `;--wing-tuck:${tuck.toFixed(4)}` : "";
    for (const side of ["l", "r"]) {
      const stem = `wing${tier}_${side}`;
      const sideClass = side === "l" ? "wing-l" : "wing-r";
      layers.push(...tintedPair(stem, "#eef3f7", sideClass, tuckStyle));
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
// The six props below (`growWings` … `roll`) are the CLIMB's own motion state
// and come from ONE place: `useRankClimb(...).icon`, spread at the call site.
// They are never assembled by hand -- every one of them is contributed by an
// entry in ui/celebrations.js, which is where a new celebration goes, and
// tests/test_single_source.py is what keeps a second assembler from being
// written. `flap`/`foldWings` are the OLDER one-shot pair, still driven by
// CSS keyframes for the scope overlay (celebrate.js); both paths end up in
// the same `--wing-tuck`/`--wing-flap` variables, so there is one transform
// rule per wing rather than two rules racing (index.html's `.hat .wing-l`).
export function Hat({ tier, division = null, size = 18, title = null, flap = false, foldWings = 0,
                      growWings = 0, growProgress = 1, foldProgress = null,
                      flapPhase = null, roll = null,
                      squashX = null, squashY = null, shake = null, sparkle = null }) {
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
  // A climb drives the tuck/flap NUMERICALLY (one value per frame, so a
  // second rank-up can retarget mid-flight); the keyframe classes are the
  // one-shot path and must not also apply, or the animation would win over
  // the inline value and freeze the climb's wings.
  const climbDrivesTuck = foldProgress != null || growWings > 0;
  const climbDrivesFlap = flapPhase != null;

  // `size` is the CAP's own footprint, both axes -- the element Medal/Crest
  // used to occupy was exactly `size`px tall (and, being square, `size`px
  // wide too) in every fixed-height card this replaces, so the OUTER box
  // below must stay exactly the CAP's height and width, not the sprite
  // CANVAS's -- the canvas is both taller AND wider than the cap (it also
  // holds the wingspan above/beside it). (Fix round 1, live report 2026-07-25,
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
  const layers = wingLayers(wings, { growWings, growProgress, foldProgress });

  if (spec.treatment === "outline") {
    // Capless must stay visible at 13px -- on the design sheet a bare
    // outline read as a broken render on the app's navy, and this is the
    // floor tier, the most common icon in the app. A dim fill of the full
    // cap silhouette sits under the ring so there is always a shape to see
    // at small sizes, not just a thin line.
    //
    // Fix round 1 (live report, 2026-07-25): the ring is what makes this read
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
    // The reel is sized off the WIDER of the two glyphs it shows, so a "1"
    // rolling into an "M" doesn't resize the type mid-roll -- the digit and
    // the letter have very different ink widths (see the ratios above), and
    // a font-size that changes on the way past reads as a wobble rather than
    // a slot.
    const rolling = roll && roll.from !== roll.to;
    const shown = rolling ? [roll.from, roll.to] : [glyphChar];
    const glyphFontSize = Math.min(...shown.map(
      (char) => glyphFontSizePx(char, patchWidthPx, patchHeightPx)));
    const glyphStyle = `${glyphVars}font-size:${glyphFontSize}px;color:${glyphColor}`;
    layers.push(rolling
      // Two cells and a slide between them: a division tick (4 -> 3), a tier
      // crossing (1 -> 5) and a climb into Mario (1 -> M) are then all the
      // same motion, with no wrap-around to special-case.
      ? html`<i class="glyph glyph-reel" style=${`${glyphStyle};--roll:${roll.progress.toFixed(4)}`}>
          <span><b>${roll.from}</b><b>${roll.to}</b></span>
        </i>`
      : html`<i class="glyph" style=${glyphStyle}>${glyphChar}</i>`);
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

  const motionClass = climbDrivesTuck ? "" : folding ? " hat-fold"
    : (flap && !climbDrivesFlap) ? " hat-flap" : "";
  // Both climb variables live on `.hat` and inherit down to every wing; a
  // wing the climb is growing overrides `--wing-tuck` on itself (wingLayers).
  // The squash/shake pair drives the tier-crossing slam and is written on the
  // OUTER span so it scales the whole icon, wings included.
  const climbStyle = (flapPhase != null ? `--wing-flap:${flapPhase.toFixed(4)};` : "")
    + (squashX != null ? `--cap-sx:${squashX.toFixed(4)};` : "")
    + (squashY != null ? `--cap-sy:${squashY.toFixed(4)};` : "")
    + (shake != null ? `--cap-shake:${shake.toFixed(3)};` : "");
  return html`<span class=${`hat${motionClass}`}
      title=${resolvedTitle} style=${`${outerStyle}${climbStyle}`}>
    <span class="hat-canvas" style=${canvasStyle}>${layers}</span>
    ${sparkle != null && sparkle < 1 && Sparkles(sparkle)}
  </span>`;
}
