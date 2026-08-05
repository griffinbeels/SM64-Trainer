// src/sm64_events/ui/entitysection.js
//
// The kind dispatch, in ONE place.
//
// Five questions get asked of every practice section -- what is its entity
// key, what noun names it, which clock is it measured on, which PB does it
// show, and what does it call itself. StarSection and SegmentSection each
// answered all five by hand, which is the shape rule 11 exists to stop, and
// the shared LogCard cannot be written at all until they have one door.
//
// A STAR SECTION CARRIES NO `kind` KEY -- views.py omits it deliberately and
// the UI branches on its absence. Everything here asks
// `sec.kind === "segment"` and never `sec.kind === "star"`.
//
// Import-free apart from redsfamily.js (itself import-free), so
// tests/test_ui_entity_section.py drives it under node.
import { familyLabel } from "./redsfamily.js";

export const isSegment = (sec) => sec.kind === "segment";

export function entityKey(sec) {
  return isSegment(sec)
    ? `segment:${sec.segment_id}`
    : `star:${sec.course_id}:${sec.star_id}`;
}

export const entityNoun = (sec) => (isSegment(sec) ? "Segment" : "Star");

// The POST /api/strat identity for THIS entity's active strategy -- the same
// shape StarSection/SegmentSection used to build by hand, one door now that
// the practice log's own card head carries the live strategy picker
// (spec practice-log-entity-cards, amendment A2). `entityKey(sec)` above is
// the READ-side identity (a string other code compares/keys on); this is the
// WRITE-side identity /api/strat actually wants, which is shaped differently
// per kind and is not derivable from the string alone.
export function entityIdentity(sec) {
  return isSegment(sec)
    ? { kind: "segment", segment_id: sec.segment_id }
    : { course_id: sec.course_id, star_id: sec.star_id };
}

// Segments are RTA-only by design (igt is null everywhere on them), so the
// view's clock applies to stars alone.
export const sectionClock = (sec, clock) => (isSegment(sec) ? "rta" : clock);

export const sectionPb = (sec, clock) => sec.pb[sectionClock(sec, clock)];

// A Bowser course's 8-Red-Coins star practices as two things worth timing,
// and the surface that SELECTS each half already spells out which. Both
// halves resolve through redsfamily.js -- no caller composes the suffix.
//
// Round 2, item 4 (live report 2026-07-30): "Segment · BitDW — 8 Red Coins
// → Pipe" beside a cell already reading "8 Red Coins (Pipe)" -- the pinned
// card exposed the segment's raw corpus identity instead of the family voice
// the cell that selects it already uses. Same fix SHAPE as the 100-coin
// star's own card (b6640ee, "the card stopped presenting a segment and
// presented the star"), applied to naming only: the section still IS the
// segment (its own attempts/strategies/PB are untouched), only the heading
// borrows the paired star's course + name. `pipe_star_entity`/`_name`/
// `_course_name` travel together (views.py), so null-guarding on any one
// covers all three.
function segmentFamily(sec, courses) {
  if (sec.pipe_star_entity) {
    return { name: familyLabel(sec.pipe_star_name || "Reds", true),
             courseName: sec.pipe_star_course_name };
  }
  if (sec.is_no_reds_pipe) {
    // Round 2 part 2 (live report 2026-07-30, again): "the pinned card
    // still says 'BitDW Pipe Entry', not 'No Reds'" -- the reds->pipe fix
    // above reached that family and left this sibling reading its own raw
    // corpus name. It has no paired star to borrow a name FROM, so its
    // display name is the exact literal stagebanner.js's own row already
    // uses ("No Reds"). The course context resolves off the segment's OWN
    // `course_id` (rule 11) through the session's own `catalog.courses` --
    // no second course-name field for a fact the caller already has, which
    // is why `courses` is threaded in rather than looked up here.
    const course = courses.find((c) => c.id === sec.course_id);
    if (course) return { name: "No Reds", courseName: course.name };
  }
  return null;
}

/**
 * `{ context, name }` — the two lines a card's heading shows.
 *
 * `courses` is the session view's `catalog.courses`, needed only by the
 * legacy no-reds pipe segment.
 */
export function displayName(sec, courses = []) {
  if (!isSegment(sec)) {
    // The STAR half of item 4 (round 2 part 2, live report 2026-07-31): with
    // Star mode selected on the Reds cell, the pinned card read a bare
    // "8 Red Coins" while the Pipe-mode sibling already read
    // "8 Red Coins (Pipe)" and the cell's own toggle spelled the suffix a
    // THIRD way inline -- the star card disagreeing with its own cell is
    // the same bug one surface later. `pipe_segment_id` is ALREADY the
    // exact discriminator (views.py: non-null only for a Bowser course's
    // star 0, the paired reds->pipe segment's escape hatch back to this
    // section) -- no new server field, and familyLabel is the SAME composer
    // the Pipe half and the cell's own toggle already call.
    return {
      context: sec.course_name,
      name: sec.pipe_segment_id != null
        ? familyLabel(sec.star_name, false) : sec.star_name,
    };
  }
  const family = segmentFamily(sec, courses);
  return {
    // `broken` wins the context line, matching SegmentSection's own order:
    // a deleted definition is the more urgent fact than which family it is.
    context: sec.broken ? "History only"
      : family ? family.courseName : "Segment",
    name: family ? family.name : sec.name,
  };
}
