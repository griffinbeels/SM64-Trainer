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
 *  - **An AREA-parented piece stays top-level; an ENTITY-parented one with no
 *    card DISAPPEARS.** Round 22 promoted any piece whose parents were absent,
 *    on the grounds that showing a card in the wrong place beats hiding it.
 *    Griffin's ruling (round 28) splits that in two, and he is right about
 *    both halves: *"this type of segment, like Inside the Volcano down below,
 *    should never be a top level entry in the practice log, because it's
 *    associated with a specific star. So, it should always be under *that
 *    star's* practice log as a subentry. (But in this case, since I didn't
 *    select Hot Foot It, it shouldn't appear at all, because we didn't select
 *    or grab that star)."* A piece of a star is only ever meaningful beside
 *    that star; loose at the top level it claims to be a thing he practised.
 *    An `area:` parent is a PLACE rather than an entity, names no section, and
 *    keeps the round-22 behaviour -- that is item 5 of that round and is
 *    untouched.
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
  // A parent that names an ENTITY -- a star or another segment -- as opposed
  // to a castle AREA, which is a place and can never have a card.
  // A SELF-reference is not a parent: it names no other entity, so such a row
  // behaves like an unparented segment and stays visible. That guard exists to
  // stop infinite nesting, never to decide whether a card is shown.
  const wantsAParent = (sec) => (sec.parents || []).some(
    (parent) => !String(parent).startsWith("area:")
                && parent !== entityKey(sec));
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
    // ...and a piece of a STAR with no parent card on screen is not promoted
    // to the top level, it simply is not shown (round 28). Its own children,
    // if it somehow had any, would go with it -- a piece of a piece is already
    // impossible (nesting is one level), so there is nothing to strand.
    if (wantsAParent(sec) && !homesOf(sec).length) continue;
    if (!children.length && !earned(sec)) continue;
    groups.push({ sec, children });
  }
  return groups;
}
