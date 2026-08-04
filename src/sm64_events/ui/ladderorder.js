// src/sm64_events/ui/ladderorder.js — the LEFT-to-RIGHT order of a rank
// standards table's strategy columns. Import-free, so pytest can drive it
// through node directly.
//
// SLOWEST on the left, FASTEST on the right, keyed on the Mario time (user,
// 2026-08-03). The reason is progression, not tidiness, and it is worth
// keeping because it decides the tie-breaks below: "they would start from the
// slowest strat and ranking (bottom left), and move to the top of that strat
// (top of left column), then move onto the next strat towards the right. So as
// they get better, they'd move from left to right across the rank standards."
// The table is a path, and it is read bottom-left to top-right.
//
// So a column's key is "how fast can this strategy ever go":
//   * its Mario cutoff, the fastest tier anyone has timed it at;
//   * failing that — a ladder can legitimately skip tiers — its own fastest
//     defined cutoff, which is the same question asked of a shorter ladder;
//   * failing THAT, a strategy with no times at all sorts furthest left. It is
//     not slow, it is unproven, and the left edge is where a run starts.
// Ties keep the order they arrived in (the community seed's own), so adding a
// strategy never reshuffles the ones beside it.

export function ceilingOf(ladder) {
  const values = Object.values(ladder || {}).filter(
    (v) => typeof v === "number" && Number.isFinite(v));
  if (!values.length) return Infinity;
  if (typeof (ladder || {}).Mario === "number") return ladder.Mario;
  return Math.min(...values);
}

// `ladders` is {strategy: {rank: seconds}}; a name it does not cover is a
// registered strategy with no standards yet, which `ceilingOf` already sends
// to the left edge.
export function slowestFirst(strategies, ladders) {
  return strategies
    .map((name, index) => ({ name, index, key: ceilingOf((ladders || {})[name]) }))
    // `a.key === b.key` first: two unproven strategies are both
    // Infinity, and Infinity - Infinity is NaN, which makes a
    // comparator's result undefined rather than merely wrong.
    .sort((a, b) => (a.key === b.key ? 0 : b.key - a.key)
                    || (a.index - b.index))
    .map((entry) => entry.name);
}
