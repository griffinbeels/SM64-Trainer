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

// The table's bands, each {label, names} in column order (spec
// 2026-08-03-hundred-coin-exit-variants): the server's `strategy_groups`
// ([] for every star but a 100-coin one) restricted to `names`, plus one
// "Other" band for the names no group covers. Slowest first INSIDE each band,
// and the bands by their own fastest column, so the progression still reads
// left to right without a column leaving its heading. `[]` = not banded.
export function orderedBands(names, ladders, groups) {
  const bands = (groups || [])
    .map((group) => ({
      label: group.label,
      names: group.strategies.map((s) => s.name).filter((s) => names.includes(s)),
    }))
    .filter((band) => band.names.length);
  const banded = new Set(bands.flatMap((band) => band.names));
  const loose = names.filter((s) => !banded.has(s));
  if (bands.length && loose.length) bands.push({ label: "Other", names: loose });
  for (const band of bands) band.names = slowestFirst(band.names, ladders);
  const fastest = (band) => Math.min(
    ...band.names.map((name) => ceilingOf((ladders || {})[name])));
  return bands.sort((a, b) => {
    const one = fastest(a), other = fastest(b);
    return one === other ? 0 : other - one;
  });
}

// Every name, in the table's column order -- which is every strategy list's
// order (his rule, 2026-10-17: "strategies should always be sorted in rank
// standard order"). The server orders the lists it sends with the Python
// twin, ranks/ladder_order.py; tests/test_cross_language_parity.py holds the
// two together.
export function tableOrder(names, ladders, groups) {
  const bands = orderedBands(names, ladders, groups);
  return bands.length ? bands.flatMap((band) => band.names) : slowestFirst(names, ladders);
}
