"""The rank standards table's column order, which is every strategy list's order.

His rule, 2026-10-17: "strategies should always be sorted in rank standard
order" -- the card's picker, an attempt row's picker and the Lineup's strategy
buttons list strategies left to right exactly as the rank standards table
draws its columns. The table's rule (ui/ladderorder.js, 2026-08-03): SLOWEST
on the left, keyed on the Mario cutoff, because the table is a path read
bottom-left to top-right as you improve. A 100-coin star's exit-star bands
sort slowest-first inside, and the bands by their own fastest column.

The browser draws the table from ui/ladderorder.js; the server orders the
lists it sends from this module. tests/test_cross_language_parity.py feeds
both the same ladders and fails when they disagree.
"""
import math

# The table's band for strategies no exit-star group covers, the same word
# ranks/standards.py's UNGROUPED_LABEL gives its own ungrouped group.
LOOSE_LABEL = "Other"


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def ceiling_of(ladder) -> float:
    """How fast this strategy can ever go: its Mario cutoff, else its fastest
    defined cutoff, else infinity (no times at all sorts furthest left)."""
    ladder = ladder or {}
    values = [v for v in ladder.values() if _number(v) and math.isfinite(v)]
    if not values:
        return math.inf
    if _number(ladder.get("Mario")):
        return ladder["Mario"]
    return min(values)


def slowest_first(names, ladders) -> list:
    """`names` slowest first; ties keep their arrival order."""
    ladders = ladders or {}
    return [name for _, _, name in sorted(
        (-ceiling_of(ladders.get(name)), index, name) for index, name in enumerate(names))]


def ordered_bands(names, ladders, groups) -> list[dict]:
    """The table's bands, each {"label", "names"} in column order: `groups`
    (the server's strategy_groups, [] for every star but a 100-coin one)
    restricted to `names`, plus one band for the names no group covers."""
    bands = [{"label": group["label"],
              "names": [s["name"] for s in group["strategies"] if s["name"] in names]}
             for group in groups or []]
    bands = [band for band in bands if band["names"]]
    banded = {name for band in bands for name in band["names"]}
    loose = [name for name in names if name not in banded]
    if bands and loose:
        bands.append({"label": LOOSE_LABEL, "names": loose})
    ladders = ladders or {}
    for band in bands:
        band["names"] = slowest_first(band["names"], ladders)
    # Python's sort is stable, like the table's: equal bands keep their order.
    return sorted(bands, key=lambda band: -min(ceiling_of(ladders.get(name)) for name in band["names"]))


def table_order(names, ladders, groups=()) -> list:
    """Every name in `names`, in the rank standards table's column order."""
    bands = ordered_bands(names, ladders, groups)
    if not bands:
        return slowest_first(names, ladders)
    return [name for band in bands for name in band["names"]]
