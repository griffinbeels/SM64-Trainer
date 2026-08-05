// src/sm64_events/ui/subsections.js
//
// Which practicable things the selector draws.
//
// PROGRESSIVE DISCLOSURE (Griffin, 2026-08-05): "we don't want to display
// alllll these random subsections for a star / segment we're not even
// practicing. Perhaps selecting a star / segment hides all of the other top
// level options, and then expands out to show all of the subsections that
// could be practiced for that entity."
//
// So the row has two states and nothing between them:
//
//   nothing selected -> only top-level entities (`parent == null`)
//   something selected -> that entity, then its children, and nothing else
//
// A subsection is NEVER loose in the row. That is the whole point: a star can
// own many, and the selector's job is that you never hunt through it.
//
// ONE LEVEL DEEP, deliberately. A subsection of a subsection is not offered,
// because a single row cannot show two levels of nesting without becoming the
// scrolling hunt this exists to prevent. If that is ever wanted it is a tree
// control, not a wider row.
//
// Import-free, so tests/test_ui_subsections.py drives it under node.

/**
 * The entities the selector should draw.
 *
 * `all` is the full practicable list, each carrying `key` and `parent`.
 * `activeKey` is the current target's entity key, or null/undefined.
 */
export function visibleEntities(all, activeKey) {
  if (!all || !all.length) return [];
  if (activeKey == null) return all.filter((e) => e.parent == null);
  const active = all.filter((e) => e.key === activeKey);
  // The target itself may be missing from the list -- it is practicable
  // somewhere you are not standing, or it was just deleted. Its children are
  // still the right thing to show if any are here, and falling back to the
  // top level would silently collapse the expanded state under the user.
  const children = all.filter((e) => e.parent === activeKey);
  if (!active.length && !children.length) {
    return all.filter((e) => e.parent == null);
  }
  return [...active, ...children];
}

/**
 * Is the row currently expanded into one entity's subsections?
 *
 * The selector reads this to decide whether to draw the expanded treatment at
 * all -- selecting something with NO subsections must look exactly like the
 * plain row, not like a collapsed row with one item in it.
 */
export function isExpanded(all, activeKey) {
  if (activeKey == null || !all) return false;
  return all.some((e) => e.parent === activeKey);
}

/**
 * The subsections of one entity, in list order.
 *
 * Separate from visibleEntities because the card and the picker want the
 * children WITHOUT the parent, and deriving that by dropping the first
 * element of visibleEntities would break the moment the parent is absent.
 */
export function subsectionsOf(all, key) {
  if (!all || key == null) return [];
  return all.filter((e) => e.parent === key);
}
