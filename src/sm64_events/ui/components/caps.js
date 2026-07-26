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

export const CAP = {
  Mario:       { name: "Mario",      color: "#e23b3b", treatment: "glow",  glyph: "M" },
  Grandmaster: { name: "Metal",      color: "#82a0b5", treatment: "metal" },
  Master:      { name: "Vanish",     color: "#8fecfd", treatment: "translucent" },
  Diamond:     { name: "Luigi",      color: "#3dc05c" },
  Platinum:    { name: "Wario",      color: "#e8af16" },
  Gold:        { name: "Waluigi",    color: "#8d42c3" },
  Silver:      { name: "Toadsworth", color: "#dad68c", pattern: "spots", patternColor: "#7a4f2a" },
  Bronze:      { name: "Toad",       color: "#ffffff", pattern: "spots", patternColor: "#e0453f" },
  Iron:        { name: "Capless",    color: "#735648", treatment: "outline" },
};

// The ladder order IS this object's key order — hardest first, mirroring
// ranks/classify.RANK_NAMES (pinned by tests/test_ui_caps.py).
export const RANK_NAMES = Object.keys(CAP);

export const rankColor = (tier) => (CAP[tier] || {}).color || "#3a4250";
export const capName = (tier) => (CAP[tier] || {}).name || tier || "Unranked";

// Roman is what scoring.py stores; Arabic is what every surface shows. A "III"
// is three glyphs in a sign field ~14px wide and cannot be read there, so the
// hat forced the decision and the text follows it (spec §Decisions 2).
const DIGITS = { V: "5", IV: "4", III: "3", II: "2", I: "1" };
export const divisionDigit = (numeral) => DIGITS[numeral] || "";

// THE wing policy, isolated so it can change without touching a renderer.
// Division V is the bottom of a tier and wears no wings; division I wears all
// four, which makes the top division of the top tier the actual Wing Cap.
// Reserving wings for the top tier alone is:
//     return tier === "Mario" ? ... : 0;
export const WING_TIERS = 4;
export function wingTiers(tier, numeral) {
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
