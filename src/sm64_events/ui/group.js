// Grouping ENGINE for every collapsible library in the app: the route library
// (free-text category path), the segment library (derived origin region ->
// place), and — next — the categorized picker modal (course -> star). One
// engine, per-consumer POLICIES: a caller supplies the key/label/order
// functions, this file owns the tree building and nothing about the domain.
//
// Render the result with components/grouplist.js.

// Node keys are the "/"-joined path from the root, which is what the open-set
// localStorage entries are keyed by. "/" (not a rarer separator) so the route
// library's stored keys keep their existing meaning — its category paths
// already use "/" and already accept that a category containing one is
// ambiguous.
export const PATH_SEP = "/";

/** Compare two sort keys: numbers numerically, arrays element-wise (so a
 *  caller can rank groups before naming them), everything else as a
 *  numeric-aware, case-insensitive string — "16 Star" after "1 Star", not
 *  the lexicographic 1, 120, 16. */
function compareKeys(left, right) {
  if (Array.isArray(left) || Array.isArray(right)) {
    const leftParts = Array.isArray(left) ? left : [left];
    const rightParts = Array.isArray(right) ? right : [right];
    for (let index = 0; index < Math.max(leftParts.length, rightParts.length); index += 1) {
      const step = compareKeys(leftParts[index] ?? "", rightParts[index] ?? "");
      if (step !== 0) return step;
    }
    return 0;
  }
  if (typeof left === "number" && typeof right === "number") return left - right;
  return String(left).localeCompare(String(right),
    undefined, { numeric: true, sensitivity: "base" });
}

function groupLevel(items, levels, parentPath) {
  if (levels.length === 0) return { items, children: [] };
  const [level, ...deeper] = levels;
  const ungrouped = [];
  const buckets = new Map();
  for (const item of items) {
    const key = level.of(item);
    if (key === null || key === undefined) { ungrouped.push(item); continue; }
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(item);
  }
  const children = [...buckets.entries()]
    .sort(([leftKey], [rightKey]) =>
      compareKeys(level.order(leftKey), level.order(rightKey)))
    .map(([key, bucket]) => {
      const path = parentPath ? `${parentPath}${PATH_SEP}${key}` : String(key);
      const inner = groupLevel(bucket, deeper, path);
      return { key: path, label: level.label(key), count: bucket.length,
               items: inner.items, children: inner.children };
    });
  return { items: ungrouped, children };
}

/** Group a flat list into a node tree, one level per `levels` entry.
 *
 *  levels: [{ of(item) -> key | null, label(key) -> string, order(key) -> sortable }]
 *    `of` returning null/undefined places the item DIRECTLY at that parent
 *    (rendered above the sub-groups), which is how "a route with no
 *    sub-category" and "a segment that starts in the region itself" work.
 *  Returns { items, children } — the root carries no key or label.
 */
export function buildTree(items, levels) {
  return groupLevel(items, levels, "");
}
