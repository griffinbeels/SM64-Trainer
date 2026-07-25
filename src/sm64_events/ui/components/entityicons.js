// src/sm64_events/ui/components/entityicons.js — the star/segment entity ->
// icon URL resolution. Extracted from stagebanner.js (task D,
// 2026-07-25-marelo-legibility) once the Rank tab's Top-N strip needed the
// SAME course-prefix table and generic-star fallback the quick-select row
// already had: a second copy of COURSE_ICON_PREFIXES is exactly the kind of
// table that silently drifts from the asset set (see tests/test_star_icons.py,
// which pins this file's registry against the course catalog order, and
// tests/test_ui_picker_parity.py's sibling history for why a shared table
// beats a second hand-written one). `iconSrcFromStem` (the stem -> URL rule
// for a resolved icon, bundled OR user-uploaded) stays owned by
// iconpicker.js — this module only adds the layer ABOVE it: which stem an
// entity resolves to in the first place.
import { iconSrcFromStem } from "./iconpicker.js";

// Course split-icon art (t.starIcons === "course", the settings-drawer
// "Star icons" preference, the DEFAULT): ui/assets/star_icons/
// {prefix}{slot+1}.png, one per main-course star INCLUDING the 100-coin 7th
// slot. Index = course_id-1 (catalog order, pinned against the assets by
// tests/test_star_icons.py).
export const COURSE_ICON_PREFIXES = ["bob", "wf", "jrb", "ccm", "bbh", "hmc", "lll",
                                     "ssl", "ddd", "sl", "wdw", "ttm", "thi", "ttc",
                                     "rr"];

// Course-mode fallback art for a SEGMENT, by start level: the icon set has
// real art for the Bowser stages — keyed by both the course level (pipe-entry
// segments) and its fight arena. Everything else (castle segments) defaults
// to the generic star unless the user overrides it.
export const LEVEL_ICONS = { 17: "bitdw", 19: "bitfs", 21: "bits",
                             30: "bitdw", 33: "bitfs", 34: "bits" };

// Generic art is `ui/assets/star_{n}.png`; the slot index is clamped to
// STAR_IMG_COUNT, so the 100-coin/7th slot reuses star_6.
const STAR_IMG_COUNT = 6;    // star_1.png .. star_6.png in ui/assets/

export const genericStarSrc = (slot) =>
  `/ui/assets/star_${Math.min(slot + 1, STAR_IMG_COUNT)}.png`;
// generic gold-star art vs "real" art (bundled split icon OR uploaded user
// icon) — the latter gets the opaque-square `courseicon` treatment
export const isGenericArt = (src) => /\/assets\/star_\d+\.png$/.test(src);

// Cell art: user override (either mode — an explicit pick always wins) >
// course-mode art (only when the settings-drawer preference asks for it) >
// the generic gold star. `entityKey` is the same "star:course:star" /
// "segment:id" identity used everywhere else (icon_overrides, MARELO
// entities, /api/target) — the one string both callers already have on hand.
export function resolveIcon(t, entityKey, courseStem, slot) {
  const override = ((t.view || {}).icon_overrides || {})[entityKey];
  const stem = override || (t.starIcons === "course" ? courseStem : null);
  return stem ? iconSrcFromStem(stem) : genericStarSrc(slot);
}

// A load failure (missing/corrupt icon) degrades to the generic star art;
// dropping `courseicon` also removes the opaque-square styling.
export function fallbackToGenericStar(event, slot) {
  const img = event.target;
  if (!isGenericArt(img.src)) {
    img.classList.remove("courseicon");
    img.src = genericStarSrc(slot);
  }
}

// Course stem + fallback slot straight from a rankable ENTITY KEY
// ("star:course:star" or "segment:id") — the Rank tab's breakdown only
// carries the key string, not the catalog/segment objects stagebanner.js's
// callers already have in hand when they build the same stem via
// `${prefix}${star_id + 1}` inline. A segment key resolves to `null`: this
// layer has no start-level data (the /api/marelo payload doesn't carry
// segment start_levels), so it falls back to the generic star exactly the
// way stagebanner's own segCourseStem does when a segment starts nowhere
// LEVEL_ICONS knows about — same fallback, just reached for a different
// reason (missing data here vs. an unmapped level there).
function starKeyParts(entityKey) {
  const match = /^star:(\d+):(\d+)$/.exec(entityKey);
  return match ? { courseId: Number(match[1]), starId: Number(match[2]) } : null;
}

export function courseStemForEntityKey(entityKey) {
  const parts = starKeyParts(entityKey);
  if (!parts) return null;
  const prefix = COURSE_ICON_PREFIXES[parts.courseId - 1];
  return prefix ? `${prefix}${parts.starId + 1}` : null;
}

export function fallbackSlotForEntityKey(entityKey) {
  const parts = starKeyParts(entityKey);
  return parts ? parts.starId : 0;
}
