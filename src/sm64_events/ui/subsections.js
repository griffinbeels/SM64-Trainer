// src/sm64_events/ui/subsections.js
//
// WHICH CARD OWNS WHICH PIECE.
//
// This module used to answer a different question -- which cells the SELECTOR
// draws, under progressive disclosure -- and round 22 (2026-08-08) retired
// that question outright. A [[subsection]] is never a cell now: it is a small
// enable/disable badge inside its parent's own art (components/celltoggles.js),
// so there is no family for a row to expand into and no fold to come back
// from. What survives is the one thing that was always really here: the
// piece -> parent mapping, `parents.includes(<entity key>)`, now read by the
// practice LOG so a piece draws inside its parent's card.
//
// Import-free apart from `entitysection.js` (itself node-driven for the same
// reason), so tests/test_ui_subsections.py drives the REAL rule under node --
// a Python reimplementation would be a second copy of exactly the thing this
// feature exists to have one of.
import { entityKey } from "./entitysection.js";

/**
 * A [[subsection]] renders INSIDE its parent's card, indented, never beside it
 * (round 22, 2026-08-08). Griffin: "the subsection should appear WITHIN the
 * star's practice log entry as a sub-entry... This is so that it's very very
 * very clear that this subsection was associated with the star it was a
 * subsection for... These cards should work exactly the same way as normal,
 * just inside the parent's card, and indented a bit so it's clear that it's
 * owned by the parent card."
 *
 * Returns `[{sec, children}]` in the order given, children in the same order.
 * Four rules, each answering a case his own LLL data already produces:
 *
 *  - **Disabled pieces never nest.** They are dropped from the log entirely,
 *    which is the display half of the badge he dims ("we no longer track the
 *    practice log entry for that subsection"), and it leaves the parent card
 *    looking exactly as it does today.
 *  - **A piece with SEVERAL parents nests under EVERY parent that has a card.**
 *    Round 22 drew it under the first one only, on the grounds that two cards
 *    for one entity means two strategy pickers writing one piece of state.
 *    Griffin overruled that from his own LLL data, where "Volcano Entry"
 *    belongs to both volcano stars: *"I don't see the Volcano Entry segment
 *    inside of the Practice Log card for Elevator Tour in the Volcano, it only
 *    shows up here [Hot-Foot-It] -- every segment enabled should appear as a
 *    subentry in the practice log."* He is right about what it costs: a card
 *    that silently omits one of its own pieces is wrong, and the duplicate
 *    pickers are harmless — both write the same definition and both re-read it
 *    on the next view.
 *  - **A piece whose parents are all absent stays TOP-LEVEL.** That covers
 *    item 5 for free -- an `area:`-parented piece names no section at all, so
 *    a castle movement "works the same as today, as a standalone top level
 *    practice log entry" -- and it is also the safe answer for a genuinely
 *    missing parent: showing a card in the wrong place beats hiding it.
 *  - **A parent with a nesting child EARNS a card**, whether or not it earned
 *    one itself. Practicing only the piece would otherwise orphan it back to
 *    the top level on the very run that proves the association.
 */
export function nestSubsections(sections, earned = () => true) {
  const present = new Set(sections.map(entityKey));
  // EVERY parent with a card, not the first one (round 24). A piece with no
  // such parent stays top-level, which is what covers an `area:`-parented
  // castle movement and a parent that earned no card of its own.
  const homesOf = (sec) => (sec.parents || []).filter(
    (parent) => present.has(parent) && parent !== entityKey(sec));
  const nested = new Map();          // parent key -> [child sec]
  for (const sec of sections) {
    if (sec.enabled === false) continue;
    if (!earned(sec)) continue;
    for (const home of homesOf(sec)) {
      if (!nested.has(home)) nested.set(home, []);
      nested.get(home).push(sec);
    }
  }
  const groups = [];
  for (const sec of sections) {
    const key = entityKey(sec);
    if (sec.enabled === false) continue;
    const children = nested.get(key) || [];
    // Drawn inside at least one parent, so not ALSO at the top level.
    if (homesOf(sec).length && earned(sec)) continue;
    if (!children.length && !earned(sec)) continue;
    groups.push({ sec, children });
  }
  return groups;
}
