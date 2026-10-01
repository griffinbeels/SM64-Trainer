// src/sm64_events/ui/loneoption.js
// "If there is exactly ONE thing practicable here, practice it."
//
// Task 0025 (2026-07-27): "When I am in a course where, given the route,
// there's literally only one star / segment that's able to be selected, we
// should always select that one star... An example is DDD during 16 star. You
// will ONLY ever do Board Bowser's Sub. Ever." And the bound, in the same
// breath: "When there are multiple options in the route, then we cannot infer
// that the user is trying to practice a specific star / segment."
//
// WIDENED 2026-08-05, and the widening is the whole of what changed: the rule
// used to require that a ROUTE had done the narrowing, so a place that
// genuinely offers one thing was left unpicked. Griffin: "When there's only
// one valid option for a given course / area due to either there genuinely
// being only one option (like in Bowser 3 -- you can only do... the Bowser 3
// fight) OR because the route itself reduces the star/segment space down to
// only one option (like in DDD when the 16 star route is selected), we should
// automatically select that option."
//
// So WHERE the narrowing came from is no longer part of the rule -- only that
// it happened. That deliberately reverses this module's own former condition
// 1 ("a route must have done the narrowing"), which existed to stop a
// no-route list of seven DDD stars becoming a pick. His bound is untouched by
// the reversal, because it was never about routes: two options is what cannot
// be inferred from, and `length === 1` is still the whole test. A no-route
// course still holds its seven stars and still picks nothing.
//
// Import-free on purpose, so node drives it directly and the RULE is testable
// without a browser (tests/test_ui_lone_route_option.py). The hook that calls
// it lives in components/stagebanner.js, which is not import-free.
//
// 2026-09-30: a sole VISIBLE choice replaces a retained hidden target too.
// Count armed extras alongside the row's ordinary choices. Two choices still
// infer nothing; a delayed selection is bound to the observed target/place.

export function loneOption(options) {
  return (options || []).length === 1 ? options[0] : null;
}

// This tuple is the journal's target identity, shared by selector comparison
// and the automatic request's expected-target binding. Zero is a valid ID.
export function targetTuple(target) {
  const held = target || {};
  if (held.kind === "segment" && held.segment_id != null)
    return ["segment", held.segment_id];
  if (held.kind !== "segment" && held.course_id != null && held.star_id != null)
    return ["star", held.course_id, held.star_id];
  return null;
}

export function needsSoleSelection(option, target) {
  return option != null
    && JSON.stringify(targetTuple(option)) !== JSON.stringify(targetTuple(target));
}
