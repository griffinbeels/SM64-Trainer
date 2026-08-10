// src/sm64_events/ui/subsections.js
//
// WHICH CARD OWNS WHICH PIECE.
//
// This module used to answer a different question -- which cells the SELECTOR
// draws, under progressive disclosure -- and round 22 (2026-08-08) retired
// that question outright in favour of a small enable/disable badge on the
// parent's own art (components/celltoggles.js). Round 31 (task 3, 2026-08-10)
// retired the badge in turn: a piece is ALWAYS tracked once enabled, so
// there is nothing left for a switch to say, and celltoggles.js is deleted.
// (It did not yet always SHOW -- an unpractised piece stayed invisible
// until round 32 the same day, below.) A [[subsection]] is never a cell and
// never a badge now -- there is no
// family for a row to expand into and no fold to come back from. What
// survives is the one thing that was always really here: the piece -> parent
// mapping, `parents.includes(<entity key>)`, read by the practice LOG so a
// piece draws inside its parent's card.
//
// Import-free apart from `entitysection.js` (itself node-driven for the same
// reason), so tests/test_ui_subsections.py drives the REAL rule under node --
// a Python reimplementation would be a second copy of exactly the thing this
// feature exists to have one of.
import { entityKey, isSegment } from "./entitysection.js";

// The row's OWN entity key. A practice-log section and a selector
// `segment_targets` row are different payloads for the same thing, and only
// the section carries `kind` -- so a segment row is keyed off `segment_id`
// directly and everything else goes through `entityKey`.
const selfKey = (row) => (row.segment_id != null ? `segment:${row.segment_id}`
                                                 : entityKey(row));

/**
 * IS THIS ROW A PIECE OF SOMETHING THAT HAS A CELL OR A CARD?
 *
 * The one definition, read by the practice log (`nestSubsections` below) AND
 * by the selector (`components/stagebanner.js`). Two answers to this question
 * is what round 30 (2026-08-09) was reported against: the star row asked it
 * one way and the castle segment row another, so a piece of a castle MOVEMENT
 * kept its own cell beside its parent instead of becoming a badge inside it --
 * *"I would therefore expect NOT to see 'Key Door (R) - Wooden Door' next to
 * BLJs, because it should instead be a button on the BLJs segment. We should
 * reuse the exact system used for stars."*
 *
 * An `area:` parent is a PLACE rather than an entity: it names no cell and no
 * card, so such a row is an ordinary top-level one (round 22 item 5). A
 * SELF-reference names no other entity either, and exists only to stop
 * infinite nesting -- it never decides whether something is shown.
 */
export const isPiece = (row) => (row.parents || []).some(
  (parent) => !String(parent).startsWith("area:") && parent !== selfKey(row));

/** The pieces a given cell or card claims -- `parents.includes(<its key>)`,
 *  which is the same test whether the parent is a star or another segment. */
export const piecesFor = (rows, parentKey) => (rows || []).filter(
  (row) => (row.parents || []).includes(parentKey));

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
 * Five rules, each answering a case his own LLL data already produces:
 *
 *  - **A piece nests WHETHER OR NOT it has earned a card of its own** (round
 *    32, 2026-08-10). Griffin: "the subsections should always be visible
 *    inside of the parent practice log card's card (i.e., we don't wait for
 *    the subsection to trigger for it to have its own card inside the
 *    parent practice log card's card). This is because I might want to
 *    check past results or rank standards (or even know that it exists). It
 *    just starts out empty in a new session." `earned` used to gate this
 *    very question, so an unpractised piece was invisible until it recorded
 *    something of its own; it still gates a PARENTLESS section's own
 *    top-level visibility (unaffected by this rule) and whether a nesting
 *    parent that earned nothing itself keeps its card (the rule right
 *    below) -- it stopped gating nesting membership alone.
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
 *
 *    A piece that is itself a STAR is the one exception to "DISAPPEARS"
 *    (decision 1, final review 2026-08-10, reds-as-subsection): it PROMOTES
 *    to an ordinary top-level card instead. A star was a top-level card with
 *    its own history and its own published ladder before this branch
 *    existed -- unlike a segment piece, which has never been a standalone
 *    card -- so hiding it as a side effect of its movement losing its OWN
 *    card (disabled, deleted, or just never armed) is a larger change than
 *    this branch asked for. Deliberately NOT generalised to a segment piece.
 *  - **A parent with a nesting child EARNS a card**, whether or not it earned
 *    one itself. Practicing only the piece would otherwise orphan it back to
 *    the top level on the very run that proves the association.
 */
export function nestSubsections(sections, earned = () => true) {
  // A DISABLED section is dropped from the log entirely (the `enabled`
  // check a few lines down), so it must never count as a home either -- a
  // parent that will never paint is not a card to nest inside. Before this
  // (final review 2026-08-10, the disabled-parent half of C1) `present` was
  // built from every section regardless of `enabled`, so disabling a
  // Bowser reds/pipe movement took its nesting reds STAR down with it: the
  // star still found "its" parent in the raw list, nested there, and the
  // parent never rendered to hold it -- both cards gone from one toggle.
  const present = new Set(
    sections.filter((sec) => sec.enabled !== false).map(entityKey));
  // EVERY parent with a card, not the first one (round 24). A piece with no
  // such parent stays top-level, which is what covers an `area:`-parented
  // castle movement and a parent that earned no card of its own.
  const homesOf = (sec) => (sec.parents || []).filter(
    (parent) => present.has(parent) && parent !== entityKey(sec));
  // A parent that names an ENTITY -- a star or another segment -- as opposed
  // to a castle AREA, which is a place and can never have a card. `isPiece`
  // above is that rule, shared with the selector so a piece cannot be one
  // thing to a card and another to a cell.
  const wantsAParent = isPiece;
  const nested = new Map();          // parent key -> [child sec]
  for (const sec of sections) {
    if (sec.enabled === false) continue;
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
    // Drawn inside at least one parent, so not ALSO at the top level --
    // WHETHER OR NOT it earned a card of its own (round 32, 2026-08-10: a
    // piece nests unconditionally now, "it just starts out empty in a new
    // session"). `earned` moving off this line is the trap the round's own
    // brief called out by name: leaving it here while the nesting loop
    // above stopped checking it would draw an unearned piece BOTH nested
    // AND at the top level, since neither guard would then exclude it.
    if (homesOf(sec).length) continue;
    // ...and a piece of a castle MOVEMENT with no parent card on screen is
    // not promoted to the top level, it simply is not shown (round 28). Its
    // own children, if it somehow had any, would go with it -- a piece of a
    // piece is already impossible (nesting is one level), so there is
    // nothing to strand.
    //
    // A piece that IS A STAR is the one exception (decision 1, final review
    // 2026-08-10, reds-as-subsection): it falls through and is treated as an
    // ordinary top-level section instead (the `earned` check two lines
    // down). A star was a top-level card with its own history and its own
    // published ladder before this branch existed, so hiding it as a side
    // effect of its movement losing its card -- disabled, deleted, or simply
    // not yet earning one -- is a larger change than this branch asked for.
    // Deliberately NOT generalised to a segment piece: that one has never
    // been a standalone card, and round 28's rule for it is untouched.
    if (wantsAParent(sec) && !homesOf(sec).length && isSegment(sec)) continue;
    if (!children.length && !earned(sec)) continue;
    groups.push({ sec, children });
  }
  return groups;
}
