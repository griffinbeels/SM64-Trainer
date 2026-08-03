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

// Segments are RTA-only by design (igt is null everywhere on them), so the
// view's clock applies to stars alone.
export const sectionClock = (sec, clock) => (isSegment(sec) ? "rta" : clock);

export const sectionPb = (sec, clock) => sec.pb[sectionClock(sec, clock)];

// A Bowser course's 8-Red-Coins star practices as two things worth timing,
// and the surface that SELECTS each half already spells out which. Both
// halves resolve through redsfamily.js -- no caller composes the suffix.
function segmentFamily(sec, courses) {
  if (sec.pipe_star_entity) {
    return { name: familyLabel(sec.pipe_star_name || "Reds", true),
             courseName: sec.pipe_star_course_name };
  }
  if (sec.is_no_reds_pipe) {
    const course = courses.find((c) => c.id === sec.course_id);
    // "No Reds" is the exact literal stagebanner.js's own row uses; the
    // segment has no paired star to borrow a name FROM.
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
