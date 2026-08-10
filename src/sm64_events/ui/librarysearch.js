// src/sm64_events/ui/librarysearch.js — the Library's search, as a rule.
//
// Griffin, round 12: "we should add a search feature here … so that when you
// type the search query, it automatically finds the segment/star that you want
// to find. That would be a bit easier than navigating (if you so choose). It
// should query in real time, and show the terminal nodes within this library
// (e.g., the segments / stars that we'd click into)."
//
// TERMINAL NODES, so a result is always a TARGET — the page you would have
// walked two layers of grid to reach. His call at capture, choosing between
// targets-only, targets-matched-on-both, and a list mixing targets with
// individual approaches: ONE kind of row, TWO things searched. Typing "LBLJ"
// finds the target that documents it and the row says which approach matched;
// a click always lands on a target page and never on a half-address.
//
// Import-free on purpose, like `ui/loneoption.js` and `ui/subsections.js`
// before it: node drives the RULE directly (tests/test_library_search.py), so
// what the search means is proved without a browser, and the component below
// it only has to draw the answer.

/** Fold a string to what a match compares: lowercase, punctuation to spaces.
 *
 * Deliberately crude. "Bob-omb" must match "bob omb" and `Tick Tock Clock —
 * Stop Watch, Get Wet` must match "stop watch" — a runner types the words,
 * not the dashes. Folding both sides through the same function is what keeps
 * that symmetric; anything cleverer (stemming, fuzzy distance) buys wrong
 * matches at the top of a list, which is worse here than a miss: the grid is
 * still one click away.
 */
export function fold(text) {
  return String(text == null ? "" : text)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

/** Every word of the query must appear somewhere in the haystack.
 *
 * AND, not OR, and not a single substring: "ttc cage" should find "Tick Tock
 * Clock — Roll Into the Cage" even though those words are far apart and in the
 * other order, while "cage" alone must not drag in every target that merely
 * shares a letter. Prefix-matched per word, so a half-typed word still
 * narrows — which is what "query in real time" has to mean for a box you are
 * still typing into.
 */
function hasEveryWord(haystack, words) {
  return words.every((word) =>
    haystack.split(" ").some((piece) => piece.startsWith(word)));
}

/**
 * Search the Library index for targets.
 *
 * index    GET /api/library body — {sheet_revision, groups:[{group, targets:[
 *          {index, section, label, entity_key, miss_reason, approaches,
 *          subsections, entries, approach_names}]}]}
 * query    what he typed, raw
 *
 * Returns [{target, group, matched}] — `matched` is the approach name that
 * earned the row when the target's OWN name did not, else null. A row whose
 * label matched reports null rather than an approach, because saying "matched
 * approach: X" under a name that already contains the query reads as the
 * search having found something else.
 *
 * An empty or whitespace query returns [] — the CALLER decides that means
 * "draw the grid instead", which keeps this module ignorant of what a page
 * looks like.
 */
export function searchTargets(index, query) {
  const words = fold(query).split(" ").filter(Boolean);
  if (!words.length || !index || !index.groups) return [];
  const hits = [];
  for (const group of index.groups) {
    for (const target of group.targets || []) {
      // The group name is part of the target's own haystack: he thinks of a
      // star as "TTC — Stop Watch", and the row he is shown says exactly
      // that, so the thing he can see has to be the thing that matched.
      const own = fold(`${group.group} ${target.label}`);
      if (hasEveryWord(own, words)) {
        hits.push({ target, group: group.group, matched: null });
        continue;
      }
      const matched = (target.approach_names || []).find((name) =>
        hasEveryWord(fold(`${group.group} ${target.label} ${name}`), words));
      if (matched) hits.push({ target, group: group.group, matched });
    }
  }
  return hits;
}

/** How a result row's second line reads.
 *
 * One sentence per row, and it always says WHY this row is here: the approach
 * that matched when one did, otherwise how much there is to read. A row that
 * only said "9 entries" under a query the label matched would leave the person
 * to work out the connection themselves.
 */
export function resultSub(hit) {
  if (hit.matched) return `matched: ${hit.matched}`;
  const count = hit.target.entries || 0;
  return `${count} ${count === 1 ? "entry" : "entries"}`;
}
