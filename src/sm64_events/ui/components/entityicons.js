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

// The PURE registries live in ../entities.js — that module imports nothing,
// so node can unit-test the picker's icon chain (entities.js::optionIcon)
// against the same table this file resolves with. Re-exported here so a
// component never has to know which of the two layers it wants (merge
// resolution 2026-07-25: two branches extracted these registries on the same
// day, one for the Rank tab's Top-N strip and one for the picker grid).
export { COURSE_ICON_PREFIXES, LEVEL_ICONS, genericStarSrc,
         isGenericArt } from "../entities.js";
import { COURSE_ICON_PREFIXES, LEVEL_ICONS, genericStarSrc,
         isGenericArt } from "../entities.js";

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
