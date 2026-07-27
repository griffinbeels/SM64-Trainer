// src/sm64_events/ui/components/caps.js — THE tier registry.
//
// Tier KEYS are external data (tools/scrape_ranks.py re-fetches them from
// xcams), so this file never renames them; it maps each key to the Mario cap
// that represents it. Colour lives here and nowhere else: the old Python
// RANK_COLORS had no runtime consumer, existing only to be mirrored, and the
// mirror is what made swapping a tier a three-edit job across two languages.
//
// Swapping a tier is ONE line. Replacing Toadsworth with Peach is:
//     Silver: { name: "Peach", color: "#f4a4d3" },
// Dropping `pattern` stops the spots rendering; `treatment` changes the
// material; `base` (default "cap") would point at different art entirely.
//
// That hex is not the first pink tried: test_ui_caps.py's
// test_every_pair_of_tiers_is_visually_distinct guard is real and will
// reject a colour that reads too close to another tier's -- a softer,
// more pastel #f19ec2 scored 184.3 against Grandmaster's #82a0b5, 0.7
// under the 185 floor (final review I6, 2026-07-25). #f4a4d3 clears every
// other tier by 8+ units. That guard doing its job on this file's OWN
// worked example is the point, not a bug in the example.
//
// Import-free on purpose, so node can unit-test it — same reason ui/entities.js
// is import-free and entityicons.js is the layer above it.

// Bronze/Toad darkened from pure white (addendum 2, 2026-07-25 -- the user's
// call, reversing the earlier "leave it" ruling): a pure-white tier fed a
// white chart gridline, rank-up dot, ladder band and a full-width white
// standards-table row in a navy design system. Master/Vanish nudged
// alongside it -- NOT cosmetic, load-bearing: a warm off-white Toad landed
// 164.3 from the old pale-cyan Vanish, under the palette guard's 185 floor.
// Moving Vanish 27 units toward saturation clears that pair at 187.4, the
// tightest surviving pair among the un-patterned tiers (verified below).
export const CAP = {
  Mario:       { name: "Mario",      color: "#e23b3b", treatment: "glow",  glyph: "M" },
  Grandmaster: { name: "Metal",      color: "#82a0b5", treatment: "metal" },
  Master:      { name: "Vanish",     color: "#7eebfc", treatment: "translucent" },
  Diamond:     { name: "Luigi",      color: "#3dc05c" },
  Platinum:    { name: "Wario",      color: "#e8af16" },
  Gold:        { name: "Waluigi",    color: "#8d42c3" },
  Silver:      { name: "Toadsworth", color: "#dad68c", pattern: "spots", patternColor: "#7a4f2a" },
  Bronze:      { name: "Toad",       color: "#efe9e2", pattern: "spots", patternColor: "#e0453f" },
  Iron:        { name: "Capless",    color: "#735648", treatment: "outline" },
};

// The ladder order IS this object's key order — hardest first, mirroring
// ranks/classify.RANK_NAMES (pinned by tests/test_ui_caps.py).
export const RANK_NAMES = Object.keys(CAP);

export const rankColor = (tier) => (CAP[tier] || {}).color || "#3a4250";
export const capName = (tier) => (CAP[tier] || {}).name || tier || "Unranked";

// Whether a tier renders TWO-TONE -- a base cap plus contrasting spots --
// today only the two patterned tiers (Toadsworth, Toad). Null for every
// flat tier, so a caller can test truthiness without special-casing which
// tiers happen to have a pattern.
export const accentColor = (tier) => {
  const spec = CAP[tier] || {};
  return spec.pattern ? spec.patternColor : null;
};

// A ready-to-use CSS gradient for a LARGE flat-colour surface (the
// standards table row, the ladder band) -- addendum 2, 2026-07-25 (the
// user's idea): a flat fill on those surfaces is a lie for the two
// patterned tiers, whose actual identity is two-tone, and a white slab on
// the standards table (Toad) was the live complaint that started this.
// Driven from the registry (`color`/`patternColor`) rather than hardcoded,
// so a future tier that gains a `pattern` gets the treatment automatically.
// Null for a flat tier -- callers fall back to `rankColor(tier)`. NOT for
// small marks (13px ladder dots, chart gridlines, rank-up dots): those stay
// on the flat base colour, since a gradient in a 4px dot is mud.
export const capGradient = (tier) => {
  const accent = accentColor(tier);
  return accent ? `linear-gradient(135deg, ${rankColor(tier)} 0%, ${accent} 50%, ${rankColor(tier)} 100%)` : null;
};

// Roman is what scoring.py stores; Arabic is what every surface shows. A "III"
// is three glyphs in a sign field ~14px wide and cannot be read there, so the
// hat forced the decision and the text follows it (spec §Decisions 2).
const DIGITS = { V: "5", IV: "4", III: "3", II: "2", I: "1" };
export const divisionDigit = (numeral) => DIGITS[numeral] || "";

// Bottom of a tier first, mirroring ranks/scoring.py::DIVISION_NUMERALS --
// DIGITS' key order already WAS this list, implicitly, and naming it is what
// lets rankPosition below stop being a second opinion about the order.
// Pinned against the Python list by tests/test_ui_caps.py.
export const DIVISION_NUMERALS = Object.keys(DIGITS);
export const DIVISIONS_PER_TIER = DIVISION_NUMERALS.length;

// THE ladder coordinate, and the reason the rank bars can animate at all.
//
// A rank is drawn from three values that move together -- tier, division, and
// how far through that division you are -- and animating the third ALONE is
// what made a rank-up read as a demotion: the server's `fill` is progress
// WITHIN the current division, so crossing a boundary sends .95 -> .05 and the
// bar ran backwards (the whole of task 0012). Collapsing all three into one
// monotone number removes the possibility rather than special-casing it:
// position 41.95 -> 42.05 is a rise, always, and the bar is just the
// fractional part.
//
// The integer part is exactly `ranks/scoring.py::progression_key` (Iron V = 0,
// hardest-last), pinned against it over all 45 pairs by tests/test_ui_caps.py.
// This file is the right home for it because it already owns BOTH registries
// the arithmetic needs -- RANK_NAMES' order and DIVISION_NUMERALS' -- and is
// import-free, so node can execute it directly.
export function rankPosition(tier, numeral, fill = 0) {
  const tierIndex = RANK_NAMES.indexOf(tier);
  const divisionIndex = DIVISION_NUMERALS.indexOf(numeral);
  if (tierIndex < 0 || divisionIndex < 0) return null;
  const fromFloor = RANK_NAMES.length - 1 - tierIndex;
  return fromFloor * DIVISIONS_PER_TIER + divisionIndex
    + Math.max(0, Math.min(1, fill || 0));
}

// The inverse, for any point mid-climb: which rank is a position standing in.
// Clamped at both ends, so a curve that overshoots by a rounding error still
// names a real rank instead of returning undefined into a render. `level` is
// the CLAMPED integer part, and callers need it -- see rankFrame.
export function rankAt(position) {
  const top = RANK_NAMES.length * DIVISIONS_PER_TIER - 1;
  const level = Math.max(0, Math.min(top, Math.floor(position)));
  const tierIndex = RANK_NAMES.length - 1 - Math.floor(level / DIVISIONS_PER_TIER);
  return { level, tier: RANK_NAMES[tierIndex],
           division: DIVISION_NUMERALS[level % DIVISIONS_PER_TIER] };
}

// There used to be a `rankFrame(position)` here, turning one ladder position
// into {tier, division, fill} for the climb to draw. It existed to survive a
// specific trap -- a maxed rank is position 45, whose FRACTIONAL PART is zero,
// so taking the bar from `position - Math.floor(position)` emptied it at the
// highest rank in the game. The climb keeps its level and its bar as two
// separate values now (ui/climbplan.js: the bar is pinned full across a
// multi-rank climb, which one position could never express), so there is no
// conversion left to get wrong and nothing called it. Removed 2026-07-27.

// A division numeral/wings/patch draws at EVERY size a division is passed --
// there is no size floor on WHETHER content draws (correction, addendum,
// task 8, 2026-07-26: the user rejected an earlier `size >= DETAIL_MIN_SIZE`
// gate outright -- "if we're using the cap system, we must be using the wing
// system", every cap, every size). This constant survives only as a size
// THRESHOLD hat.js's own rendering leans on for two purely visual tunings
// that never hide content: the Capless outline's fill needs more opacity
// below it (a thin ring can't survive downscaling on its own), and the
// glyph claims a larger share of the sign field below it (more legible with
// nothing else competing for those pixels) -- see hat.js's
// `ringNeedsMoreFill`/`glyphFraction`. Named for its origin (the old
// detail-gating floor, design contact sheet: numeral dead at 22/26,
// comfortably readable from 30) rather than renamed, since it is still the
// same size boundary, just backing weaker guarantees now.
export const DETAIL_MIN_SIZE = 30;

// THE wing policy, isolated so it can change without touching a renderer.
// Division V is the bottom of a tier and wears no wings; division I wears all
// four, which makes the top division of the top tier the actual Wing Cap.
//
// Capless (Iron) is the one tier that NEVER wears wings, at any division
// (correction, addendum, task 8, 2026-07-26 -- overrides an earlier "wings
// everywhere including Capless" instruction). Capless means you have no cap;
// wings are a thing a CAP earns, so a capless state sprouting wings is
// incoherent -- and it would undercut the dotted outline (below), whose whole
// job is to say "nothing here yet". Divisions within Capless are marked by
// the numeral on the patch alone. Isolated here, not in hat.js/medal.js/
// celebrate.js, so the policy can keep changing without a renderer knowing.
export const WING_TIERS = 4;
export function wingTiers(tier, numeral) {
  if (tier === "Iron") return 0;
  const digit = Number(divisionDigit(numeral));
  if (!digit) return 0;
  return Math.max(0, Math.min(WING_TIERS, 5 - digit));
}

// Geometry measured off the exports (see the plan's Measured constants). The
// sprite canvas is wider than the cap because it must hold the full wingspan;
// these fractions are how a renderer finds the cap and the sign field inside it.
export const CANVAS = { width: 1283, height: 675 };
export const CAP_BOX = { left: 0.155885, top: 0.194074, width: 0.688231, height: 0.801481 };
export const PATCH_BOX = { left: 0.356976, top: 0.263704, width: 0.286048, height: 0.410370 };

// Round 5 (addendum, task 8, 2026-07-26 -- "sweep every container sized for
// the old disc"): every container in the app whose width, padding or gap was
// chosen around the old square Medal/Crest is too narrow for a Hat, because
// a cap is wider than it is tall BEFORE its wings spill a further stretch
// outside that box, by design (hat.js -- ".hat" itself never clips). Both
// ratios are DERIVED from the same CAP_BOX/CANVAS geometry hat.js's own
// layout math already uses, not hand-copied, so they can't drift out of
// sync with the sprite if that geometry ever changes.
const CANVAS_WIDTH_PER_SIZE = (CANVAS.width / CANVAS.height) / CAP_BOX.height;
// ".hat"'s own declared box (hat.js's outerStyle) -- no wings yet. ~1.632.
export const HAT_LAYOUT_WIDTH_RATIO = CAP_BOX.width * CANVAS_WIDTH_PER_SIZE;
// How far a wing spills PAST that box, each side, symmetric by construction
// (CAP_BOX.left and CANVAS.width - CAP_BOX.left - CAP_BOX.width are equal to
// four decimal places). ~0.370 -- a container that only reserves the layout
// box still gets its neighbour crowded by exactly this much per side
// whenever a division actually has wings (every division except V, and
// never on Capless -- see wingTiers above).
export const HAT_WING_SPILL_RATIO = CAP_BOX.left * CANVAS_WIDTH_PER_SIZE;
