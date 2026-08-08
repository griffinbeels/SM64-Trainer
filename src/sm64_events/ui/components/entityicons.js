// src/sm64_events/ui/components/entityicons.js — the ONE place a component
// turns a star/segment into art. A component calls `entityIconSrc(t, key)` (an
// entity key) or `optionIconSrc(t, kind, id)` (a picker option) and gets a URL;
// `iconContext(t)`, which assembles the resolver's context out of the store,
// is theirs to call, not a caller's. The chain itself lives in ../entities.js
// (`entityIcon`), which imports nothing and is therefore node-testable; this
// module is the layer that knows about the store.
//
// It exists because the same entity used to resolve THREE different ways
// (fixed 2026-07-26, live report: BitFS Pipe Entry drew Bowser on the
// practice banner and a plain gold star on the Rank tab's coverage strip):
//
//   - the banner passed a stem it derived itself, from the session view's
//     `start_levels` — right for segments, but it had no branch for the
//     Bowser/cap courses, whose stars fell through to a gold star;
//   - the Rank tab derived its stem from the entity KEY alone, which carries
//     no start level at all, so EVERY segment fell through to a gold star;
//   - the pickers called entities.js's own chain with a hand-built context,
//     which knew about the special courses but ignored a star's override and
//     resolved an uploaded `user:` override to a 404.
//
// Each was locally reasonable and the three disagreed. So there is now no
// per-call-site stem derivation to get right: a caller passes an entity key
// and gets a URL. `tests/test_single_source.py` is what keeps that true rather
// than aspirational: it forbids every other file from naming an art path or an
// icon registry, so a second chain cannot be written in the first place.
import { entityIcon, fallbackSlotForEntityKey, genericStarSrc, isGenericArt,
         optionIcon, segmentLevelsOf } from "../entities.js";

// The pure helpers a rendering component still needs by name: the generic-star
// slot for its img onerror, and the generic-vs-real test that decides the
// opaque-square `courseicon` styling. Re-exported so a component never has to
// know which of the two layers it wants. The TABLES themselves
// (COURSE_ICON_PREFIXES, LEVEL_ICONS, SPECIAL_COURSE_ICONS) are deliberately
// NOT re-exported any more: a component that reads one is a component
// deriving its own stem, which is the bug this module exists to prevent.
export { fallbackSlotForEntityKey, genericStarSrc,
         isGenericArt } from "../entities.js";

// A segment's icon follows the level it STARTS in, and two payloads answer
// that question. The session view's `segment_targets` carries the real
// `start_levels` off the segment's start triggers; `/api/segments` carries
// only an `origin`, whose level is derived the same way BUT can be overridden
// by hand (the library's "category" control writes it). So triggers win where
// the view has them, and origin fills in for every segment the current view
// doesn't list — which is what lets the Rank tab and the pickers, neither of
// which has a stage view, resolve the same art the banner does. Re-pointing a
// segment's library category must not silently repaint its icon.
function segmentLevels(t) {
  const view = t.view || {};
  const levels = segmentLevelsOf(t.segments);
  for (const target of view.segment_targets || [])
    if ((target.start_levels || []).length)
      levels[String(target.segment_id)] = target.start_levels;
  return levels;
}

// The corpus's own identity and grouping for each definition, straight off
// /api/segments — `seed_key` survives a rename and is identical on every
// install, `category` is the seed's classification ("Castle Movement"), not a
// second one invented in the UI. Both feed the default-art registries in
// entities.js. `origin.region` rides along for the row a user built by hand,
// which has neither of the first two: it is the castle area that segment's
// start node is reached through, stamped by the SERVER (views.stamp_origins ->
// segments.origin_view, honouring the editor's own origin override), so this
// is still the app's one answer about where a segment begins rather than a
// second one derived here. entities.js decides what it MEANS.
function segmentMeta(t) {
  return Object.fromEntries((t.segments || []).map((segment) =>
    [String(segment.id),
     { seedKey: segment.seed_key || null, category: segment.category || null,
       originRegion: (segment.origin || {}).region || null,
       // What this is a PIECE of — a subsection wears its parent's art by
       // default (round 15); entities.js decides what the key means.
       parent: segment.parent || null }]));
}

// One-entry memo. The context is derived from six store slots and rebuilding
// it means walking every segment definition; the Rank tab's coverage strip
// alone resolves ~24 entities per render and the whole page re-renders on
// every WebSocket event. Identity comparison is enough because all six are
// replaced wholesale by their fetches, never mutated in place.
let memo = null;

export function iconContext(t) {
  const view = t.view || {};
  const slots = [t.segments, view.segment_targets, view.icon_overrides,
                 t.courseIcons, t.starIcons, t.vocab];
  if (memo && slots.every((slot, index) => slot === memo.slots[index]))
    return memo.context;
  const context = {
    courseIcons: t.courseIcons || {},
    starIconsMode: t.starIcons || "course",
    iconOverrides: view.icon_overrides || {},
    courseByLevel: (t.vocab || {}).course_by_level || {},
    segmentLevels: segmentLevels(t),
    segmentMeta: segmentMeta(t),
  };
  memo = { slots, context };
  return context;
}

/** Art for one entity key ("star:8:2", "segment:6", "course:8", "level:19"),
 *  resolved against the live store. THE call every component makes. */
export function entityIconSrc(t, entityKey) {
  return entityIcon(entityKey, iconContext(t || {}));
}

/** The same, addressed the way a PICKER addresses an option: a kind plus that
 *  kind's own id. Exists so no component ever holds a context object — a
 *  picker's `iconFor` is `(id) => optionIconSrc(t, "star", id)` and there is
 *  nothing to pass wrongly. Passing the `iconContext` FUNCTION where the
 *  context belonged is a real bug this shape removes: every field destructures
 *  to its default, so the whole grid silently drew fallback art (caught by
 *  render, 2026-07-26 — every course portrait became a star icon and PSS, whose
 *  fallback stem has no .png, 404'd through to a plain gold star). */
export function optionIconSrc(t, kind, id) {
  return optionIcon(kind, id, iconContext(t || {}));
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
