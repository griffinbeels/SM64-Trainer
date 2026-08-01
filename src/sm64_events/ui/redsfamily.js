// src/sm64_events/ui/redsfamily.js
// A Bowser course's 8-Red-Coins star practices as two things worth timing --
// the grab alone (" (Star)") or the whole reds-then-pipe run (" (Pipe)") --
// and the pinned card, the quick-select cell, and (per views.py's own
// standards-table filtering) the rank-standards seed all have to agree on
// which literal names which half. Three surfaces each composed this suffix
// their own way until round 2 (spec 2026-07-28-multi-step-segments, live
// reports 2026-07-30/31): stagebanner.js's RedsCell built
// `${star} (${pipeMode ? "Pipe" : "Star"})` inline, practice.js's
// SegmentSection built `${star} (Pipe)` inline for the paired segment's own
// card, and the STAR half of that same card (practice.js's StarSection) had
// no family concept at all -- exactly the "three surfaces each grow their
// own honest way and disagree" shape this project's icon-art incident
// (2026-07-26) already paid for once.
//
// ONE module owns the composition now; every caller passes IDENTITY (the
// base star name + which half) and never spells the suffix itself --
// tests/test_single_source.py's own row names these two literals so a third
// call site can't reappear. Python already owns the SAME two literals
// (tracking/views.py::STAR_FAMILY_SUFFIX/PIPE_FAMILY_SUFFIX, load-bearing
// there too -- they select which half of a Bowser Reds star's strategies
// the star/segment section may grade or offer); import-free on both sides,
// so tests/test_cross_language_parity.py compares the two REAL constants
// directly rather than restating either.
export const STAR_FAMILY_SUFFIX = " (Star)";
export const PIPE_FAMILY_SUFFIX = " (Pipe)";

// baseName: the star's own display name ("8 Red Coins", course.stars[0], or
// the server's star_name) -- never re-derived here, always passed in.
export function familyLabel(baseName, isPipe) {
  return `${baseName}${isPipe ? PIPE_FAMILY_SUFFIX : STAR_FAMILY_SUFFIX}`;
}
