// src/sm64_events/ui/loneoption.js
// "If the route leaves exactly ONE thing practicable here, practice it."
//
// Task 0025 (2026-07-27): "When I am in a course where, given the route,
// there's literally only one star / segment that's able to be selected, we
// should always select that one star... An example is DDD during 16 star. You
// will ONLY ever do Board Bowser's Sub. Ever." And the bound, in the same
// breath: "When there are multiple options in the route, then we cannot infer
// that the user is trying to practice a specific star / segment."
//
// Import-free on purpose, so node drives it directly and the RULE is testable
// without a browser (tests/test_ui_lone_route_option.py). The hook that calls
// it lives in components/stagebanner.js, which is not import-free.
//
// Two conditions, and both are load-bearing:
//
//   1. A ROUTE MUST HAVE DONE THE NARROWING. Standing in DDD with no route
//      active leaves seven stars, and picking one of them for him would be
//      inventing an intent. `narrowed` is the caller's route filter — null
//      when there is no active route, or when the route never visits here,
//      which the selector rows already fall back on to avoid an empty row.
//   2. EXACTLY ONE. Not "the first of the route's stars here" — two options
//      is precisely the case he named as not inferable.
//
// The third condition, an EMPTY HAND, is `handIsEmpty` below and is the rule
// that has already cost three separate bugs on this surface (a star grab, an
// arena entry, and the Bowser reds row, all 2026-08-01/02): a convenience
// default may fill an empty hand; it may not take something out of one.

export function loneRouteOption(narrowed, options) {
  if (!narrowed) return null;
  return options.length === 1 ? options[0] : null;
}

// The practice target as the server models it: `kind` plus an identity. A
// target with no identity is what the projector leaves behind when it retires
// one (entering a course that is not its origin), and it is the state the
// player sees as "No active objective" — the one an auto-pick may fill.
export function handIsEmpty(target) {
  const held = target || {};
  return held.segment_id == null && held.course_id == null;
}
