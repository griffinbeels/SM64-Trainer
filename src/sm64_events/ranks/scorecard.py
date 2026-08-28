"""The scorecard card builder.

A scorecard tile prints your gap to a goal, entity by entity, and each ROW
also prints a summed gap across its tiles. Round 6 (2026-08-24) made the
composition SCOPE-DRIVEN: "whatever is in the scope is what we generate a
scorecard for... It should adapt to the route we have selected." The Overall
scope is the community template's 120-star card; a route scope composes one
row per route step-group; a course scope is that course's own row.

THE CELL RULE, his canonical pairing held verbatim (round 6): a course's
100-coin star is not its own cell -- it is combined with a companion star
("Find the 8 Red Coins + 100c"), because the 100-coin run collects that
star's objective on the way. The companion is USUALLY the course's red-coin
star; five courses pair differently and `_COMPANION_EXCEPTIONS` stores his
exact star names for them, resolved against STAR_NAMES at import and RAISING
on a miss -- his words stay the source, nobody's memory does. The merge
applies to any row: whenever a row holds a course's 100c star AND its
companion, the companion's cell is REPLACED by the combined 100c cell at the
companion's own position; a companion in scope without its 100c keeps its
own cell.

A tile missing either side skips BOTH sums, so a Sigma is always computed
over one fully comparable set; `total` stays the row's physical tile count
(the "5/6" coverage chip's denominator).

The endpoint this feeds and the persisted goal shape are `docs/api.md`'s
`GET /api/scorecard` / `PUT /api/scorecard/goal` rows; this module's own
contract is `tests/test_scorecard.py`.

Pure: no db, no I/O -- imports only `ranks.scoring`, `ranks.scopes`
(candidate_key, the one route-candidate translation) and `memory.addresses`.
"""
from sm64_events.memory.addresses import COURSE_NAMES, STAR_NAMES, star_name
from sm64_events.ranks.scopes import candidate_key
from sm64_events.ranks.scoring import (
    DIVISION_NUMERALS, DIVISIONS_PER_TIER, defined_tiers, progress_for_time,
    tier_band, time_for_score)

__all__ = ["SECRET_ROW", "hundred_coin_companion", "template_rows",
           "rows_for_course", "rows_for_route", "without_keys", "card_keys",
           "division_goal_cs", "build_card"]

_HUNDRED_COIN_SLOT = 6

# Round 6's five exceptions, his star names verbatim ("CCM: (Big Penguin
# Race + 100c)..."). Resolved against STAR_NAMES below; a rename upstream
# RAISES at import rather than silently pairing the wrong star.
_COMPANION_EXCEPTIONS = {
    4: "Big Penguin Race",              # CCM
    7: "Hot-Foot-It into the Volcano",  # LLL
    8: "Pyramid Puzzle",                # SSL
    14: "Stomp on the Thwomp",          # TTC
    15: "The Big House in the Sky",     # RR
}


def hundred_coin_companion(course_id: int) -> int:
    """The star id the course's 100-coin cell is combined with.

    Default: the course's star whose name contains "Red Coins" (his
    "usually"); the five exceptions resolve their stored name exactly."""
    names = STAR_NAMES[course_id]
    wanted = _COMPANION_EXCEPTIONS.get(course_id)
    if wanted is not None:
        return names.index(wanted)      # a rename upstream raises ValueError
    for star_id, name in enumerate(names):
        if "Red Coins" in name:
            return star_id
    raise LookupError(f"course {course_id} has no Red Coins star to pair")


# The template's Secret row, in template order. All stars since round 6
# ("Bowser stages should also should have goals based on red coins times,
# not the BITS entry for now") -- the BitDW/BitFS course-entry MOVEMENTS
# left the card with that ruling; "for now" is his, so they may return as a
# scope choice later. The two PSS stars use `star_name` (distinct in-game
# names); the three Bowser reds append "Red Coins" to the course name (the
# template's own labels -- their star is named "8 Red Coins" in-game for all
# three, which would collapse to three identical tiles); the five other
# single-star courses use the COURSE name for the same reason.
SECRET_ROW: list[tuple[str, str]] = [
    ("star:19:0", star_name(19, 0)),
    ("star:19:1", star_name(19, 1)),
    ("star:24:0", COURSE_NAMES[24]),
    ("star:16:0", f"{COURSE_NAMES[16]} Red Coins"),
    ("star:17:0", f"{COURSE_NAMES[17]} Red Coins"),
    ("star:18:0", f"{COURSE_NAMES[18]} Red Coins"),
    ("star:21:0", COURSE_NAMES[21]),
    ("star:22:0", COURSE_NAMES[22]),
    ("star:20:0", COURSE_NAMES[20]),
    ("star:23:0", COURSE_NAMES[23]),
]


def _merge_hundred_coins(entries: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """Apply the cell rule to one row's (key, label, clock) entries.

    Where a course's 100c star sits beside its companion, the companion's
    entry becomes the combined 100c cell IN PLACE (the template's own
    position) and the separate 100c entry drops. Either one alone keeps its
    own cell untouched."""
    keys = {entry[0] for entry in entries}
    out = []
    for key, label, clock in entries:
        parts = key.split(":")
        if parts[0] != "star":
            out.append((key, label, clock))
            continue
        course_id, star_id = int(parts[1]), int(parts[2])
        try:
            companion = hundred_coin_companion(course_id)
        except (KeyError, LookupError):
            out.append((key, label, clock))
            continue
        hundred = f"star:{course_id}:{_HUNDRED_COIN_SLOT}"
        if star_id == companion and hundred in keys:
            out.append((hundred, f"{label} + 100c", clock))
        elif star_id == _HUNDRED_COIN_SLOT and f"star:{course_id}:{companion}" in keys:
            continue                     # absorbed into the companion's cell
        else:
            out.append((key, label, clock))
    return out


def _course_row(course_id: int) -> dict:
    entries = [(f"star:{course_id}:{star_id}", star_name(course_id, star_id),
                "igt") for star_id in range(7)]
    return {"course_id": course_id, "label": COURSE_NAMES[course_id],
            "entries": _merge_hundred_coins(entries)}


def template_rows() -> list[dict]:
    """The Overall card: 15 course rows (6 cells each -- the 100c cell
    combined with its companion), then the Secret row."""
    rows = [_course_row(course_id) for course_id in range(1, 16)]
    rows.append({"course_id": None, "label": "Secret",
                 "entries": [(key, label, "igt") for key, label in SECRET_ROW]})
    return rows


def rows_for_course(course_id: int) -> list[dict]:
    """A course scope's one row: the template row for a main course; for a
    course whose card presence lives in the Secret row (the Bowser reds, the
    cap stages, PSS/SA/WMotR) that course's own Secret entries. LookupError
    for a course the card has no cells for at all."""
    if 1 <= course_id <= 15:
        return [_course_row(course_id)]
    entries = [(key, label, "igt") for key, label in SECRET_ROW
               if key.startswith(f"star:{course_id}:")]
    if not entries:
        raise LookupError(f"no scorecard cells for course {course_id}")
    return [{"course_id": course_id, "label": COURSE_NAMES[course_id],
             "entries": entries}]


def rows_for_route(route: dict, *, segment_labels: dict[int, str]) -> list[dict]:
    """One row per route step-group, in route order, the cell rule applied
    within each row. A step's own label wins; else a row whose stars share
    one course wears the course name; a lone segment wears its own name;
    anything else is "Step N". A candidate `segment_labels` cannot name (a
    deleted segment) draws no cell."""
    rows = []
    for index, step in enumerate(route.get("steps", []), start=1):
        entries = []
        courses = set()
        for candidate in step.get("candidates", []):
            key = candidate_key(candidate)
            if key is None:
                continue
            parts = key.split(":")
            if parts[0] == "star":
                course_id, star_id = int(parts[1]), int(parts[2])
                courses.add(course_id)
                entries.append((key, star_name(course_id, star_id), "igt"))
            else:
                segment_id = int(parts[1])
                label = segment_labels.get(segment_id)
                if label is None:
                    continue
                entries.append((key, label, "rta"))
        if not entries:
            continue
        merged = _merge_hundred_coins(entries)
        # A COURSE VISIT wears the course's own name, ahead of the step's
        # authored label -- round 8 (2026-08-28): "We also don't need the
        # 'DDD -- 3 stars' or 'WDW -- 7 stars' the '-- X stars' count. Just
        # the name of the course." That suffix is real and stays where it
        # belongs (`corpus_vocab._merge_label` writes it; the Run tab and the
        # route builder both show it) -- the scorecard just does not want a
        # count in a row whose own cells already are the count. Anything
        # that is not one course's stars still prefers the step's label,
        # which is what names a movement row ("→ WF", "Lakitu Skip").
        one_course = (len(courses) == 1
                      and all(entry[0].startswith("star:") for entry in merged))
        if one_course:
            label = COURSE_NAMES[courses.pop()]
        else:
            label = (step.get("label")
                     or (merged[0][1] if len(merged) == 1 else f"Step {index}"))
        rows.append({"course_id": None, "label": label, "entries": merged})
    return rows


def without_keys(rows_spec: list[dict], excluded) -> list[dict]:
    """`rows_spec` minus every entry whose key is excluded, dropping a row
    the filter empties entirely.

    Round 7 (2026-08-28): "By default, all segments should be ignored (other
    than Bowser segments / bowser fights, and other than the 100c
    segments)... This should match the route include/ignores logic -- that
    is, those are already ignored in the route ranking list, so we should
    ignore them here as well." The card must therefore read the SAME
    exclusion set every scope's rating reads (`service.rank_excluded()` over
    `scopes.default_excluded`), never a second rule of its own -- a
    scorecard that graded a movement the route rating ignores would be
    scoring a different route than the rank beside it."""
    excluded_keys = set(excluded or ())
    rows = []
    for row in rows_spec:
        entries = [entry for entry in row["entries"] if entry[0] not in excluded_keys]
        if entries:
            rows.append({**row, "entries": entries})
    return rows


def card_keys(rows_spec: list[dict]) -> list[str]:
    """Every entity key on the card, in card order, deduplicated (a route
    may visit the same entity twice; it resolves once)."""
    seen, keys = set(), []
    for row in rows_spec:
        for key, _label, _clock in row["entries"]:
            if key not in seen:
                seen.add(key)
                keys.append(key)
    return keys


def _tile(you: dict, goal: dict, key: str, label: str, clock: str) -> dict:
    you_cs = you.get(key)
    goal_cs = goal.get(key)
    delta_cs = (you_cs - goal_cs) if you_cs is not None and goal_cs is not None else None
    return {"key": key, "label": label, "clock": clock,
            "you_cs": you_cs, "goal_cs": goal_cs, "delta_cs": delta_cs}


def _sum_tiles(tiles: list[dict]) -> dict:
    """A row's (or the card's) Sigma: only tiles with both sides count, so
    the sum is always over one comparable set. `total` is the row's physical
    tile count -- the "5/6" coverage chip's denominator."""
    counted_tiles = [t for t in tiles
                      if t["you_cs"] is not None and t["goal_cs"] is not None]
    you_cs = sum(t["you_cs"] for t in counted_tiles)
    goal_cs = sum(t["goal_cs"] for t in counted_tiles)
    counted = len(counted_tiles)
    return {"you_cs": you_cs, "goal_cs": goal_cs,
            "delta_cs": (you_cs - goal_cs) if counted else None,
            "counted": counted, "total": len(tiles)}


def division_goal_cs(ladder_cs: dict[str, int], tier: str, division: str) -> int | None:
    """The slowest displayed centisecond that still grades `(tier, division)`
    on this ladder -- the goal time a tile shows for that rank. None when
    `tier` is not one this ladder defines at all, OR when no integer
    centisecond grades to this exact division at all: near the fastest
    tiers `score_for`'s extrapolation slope is steep enough that a division
    a whole cs wide in SCORE spans zero real centiseconds (measured against
    the bundled seed: `star:8:1`'s ladder never grades Mario III -- 673cs is
    Mario II, 674cs is already Mario IV). That is the same "no goal on this
    tile" case as an undefined tier, not a bug to raise on.

    `division`'s own entry score (the low edge of its five-way slice of the
    tier's score band) is exactly the score of the slowest time still inside
    it, so `time_for_score` inverts straight to (approximately) the answer;
    the loop only corrects the centisecond-rounding slop `time_for_score` and
    `progress_for_time` can each introduce independently, walking toward the
    last centisecond that still grades here. Bounded at 10 steps -- measured
    against every ladder/tier/division the bundled seed ships (4,580 combos):
    every one either converges in well under 10 steps or is genuinely
    unreachable, so a wider bound would not recover a different answer."""
    defined = defined_tiers(ladder_cs)
    if tier not in defined:
        return None
    low, high = tier_band(tier, defined)
    entry_score = low + (high - low) * DIVISION_NUMERALS.index(division) / DIVISIONS_PER_TIER
    cs = time_for_score(ladder_cs, entry_score)
    if cs is None:
        return None
    target = (tier, division)
    for _ in range(10):
        graded = progress_for_time(ladder_cs, cs)
        if (graded["tier"], graded["division"]) == target:
            beyond = progress_for_time(ladder_cs, cs + 1)
            if (beyond["tier"], beyond["division"]) != target:
                return cs
            cs += 1                       # still inside -- push toward the slowest
        elif graded["score"] > entry_score:
            cs += 1                       # graded better than this division -- slow down
        else:
            cs -= 1                       # graded worse than this division -- speed up
    return None                           # unreachable: no goal for this tile


def build_card(rows_spec: list[dict], *, you: dict[str, int],
                goal: dict[str, int]) -> dict:
    """The scorecard payload for one scope's rows: each row's tiles and
    Sigma, plus `total` over every tile. `you`/`goal` map entity key ->
    displayed centiseconds."""
    rows = []
    for row in rows_spec:
        tiles = [_tile(you, goal, key, label, clock)
                 for key, label, clock in row["entries"]]
        rows.append({"course_id": row["course_id"], "label": row["label"],
                      "tiles": tiles, "sum": _sum_tiles(tiles)})
    all_tiles = [tile for row in rows for tile in row["tiles"]]
    return {"rows": rows, "total": _sum_tiles(all_tiles)}
