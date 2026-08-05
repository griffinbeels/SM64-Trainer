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
//   something selected -> that entity's FAMILY: the parent, then its
//                         children, and nothing else
//
// FAMILY, not "the selected thing and its children". Selecting a subsection
// keeps its parent and its siblings on screen -- see `familyRoot`.
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
 * Whose FAMILY the row is showing, given what is selected. Null with nothing
 * selected.
 *
 * The row is expanded around a parent, never around "whatever is selected" --
 * selecting a subsection keeps its parent and its siblings on screen. Keying
 * the expansion on the active entity instead collapsed the row to a single
 * cell the moment you practiced one subsection, with no way back to the
 * parent or to the others: a dead end reached by using the feature correctly.
 *
 * An active key that is not in the list is its own root. That is what keeps
 * `visibleEntities`'s off-list fallbacks working -- we cannot read a parent
 * off an entity we do not have.
 */
export function familyRoot(all, activeKey) {
  if (activeKey == null || !all || !all.length) return null;
  const active = all.find((e) => e.key === activeKey);
  return active && active.parent != null ? active.parent : activeKey;
}

/**
 * The entities the selector should draw.
 *
 * `all` is the full practicable list, each carrying `key` and `parent`.
 * `activeKey` is the current target's entity key, or null/undefined.
 */
export function visibleEntities(all, activeKey) {
  if (!all || !all.length) return [];
  const root = familyRoot(all, activeKey);
  const children = root == null ? [] : all.filter((e) => e.parent === root);
  // NO CHILDREN, NO EXPANSION -- the row stays the full top-level list.
  // Hiding the other options because you picked one is only worth doing when
  // there is something to hide them FOR; without this clause, practicing an
  // ordinary star emptied its course's row down to that one star and
  // practicing an ordinary castle movement emptied the castle's, with no
  // gesture anywhere that brought the others back.
  if (!children.length) return all.filter((e) => e.parent == null);
  // The root itself may still be missing -- it is practicable somewhere you
  // are not standing, or it was just deleted. Its children are the right
  // thing to show regardless; collapsing to the top level there would drop
  // the expanded state under the user.
  return [...all.filter((e) => e.key === root), ...children];
}

/**
 * Is the row currently expanded into one entity's subsections?
 *
 * The selector reads this to decide whether to draw the expanded treatment at
 * all -- selecting something with NO subsections must look exactly like the
 * plain row, not like a collapsed row with one item in it.
 */
export function isExpanded(all, activeKey) {
  const root = familyRoot(all, activeKey);
  if (root == null) return false;
  return all.some((e) => e.parent === root);
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
