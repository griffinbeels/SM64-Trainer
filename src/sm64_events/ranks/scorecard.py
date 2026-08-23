"""The scorecard card builder (spec docs/superpowers/specs/2026-08-23-scorecard-design.md).

A scorecard tile prints your gap to a goal, entity by entity, and each ROW
also prints a summed gap across its tiles. The one rule that makes a summed
gap meaningful: a course's 100-coin star is timed together with whichever
exit star you (or the goal) actually took, so the two sides of a Sigma can
be comparing DIFFERENT stars unless something is skipped -- same star both
sides, or the Sigma gap is not a gap. `fold` names that one shared exit star
per course; its tile still draws (with its own numbers) but never joins a
sum. A tile missing either side is the same problem in miniature -- it also
skips both sums, so a Sigma is always computed over one fully comparable set.

Pure: no db, no I/O, no imports outside `ranks.scoring` and `memory.addresses`.
"""
from sm64_events.memory.addresses import COURSE_NAMES, star_name
from sm64_events.ranks.scoring import (
    DIVISION_NUMERALS, DIVISIONS_PER_TIER, defined_tiers, progress_for_time,
    tier_band, time_for_score)

__all__ = ["SECRET_ROW", "card_keys", "division_goal_cs", "build_card"]

_SEED_PREFIX = "seed:"

# The template's Secret row, in template order (spec "Rows (template scope)").
# A `"seed:"`-prefixed key is a MOVEMENT: the caller resolves the seed_key
# (the part after the prefix) to a real `segment:<id>` key via
# `db.segment_defs()` through `resolve_seed`, which also names the label --
# there is no star/course table to derive a movement's name from. Every other
# entry is a real entity key already; its label here is what `build_card`
# would derive for it anyway (`star_name`), kept alongside so `SECRET_ROW`
# reads correctly standing alone.
SECRET_ROW: list[tuple[str, str]] = [
    ("star:19:0", star_name(19, 0)),
    ("star:19:1", star_name(19, 1)),
    ("star:24:0", COURSE_NAMES[24]),
    (f"{_SEED_PREFIX}seg:bitdw-pipe", "Bowser in the Dark World"),
    (f"{_SEED_PREFIX}seg:bitfs-pipe", "Bowser in the Fire Sea"),
    ("star:18:0", COURSE_NAMES[18]),
    ("star:21:0", COURSE_NAMES[21]),
    ("star:22:0", COURSE_NAMES[22]),
    ("star:20:0", COURSE_NAMES[20]),
    ("star:23:0", COURSE_NAMES[23]),
]


def _secret_entries(resolve_seed):
    """(entity_key, label, clock) for the Secret row, in `SECRET_ROW` order.

    A movement whose `resolve_seed` returns None (its segment was deleted)
    drops out entirely -- no dead tile drawn for it -- per the spec's
    "Rows" rule."""
    entries = []
    for raw_key, fallback_label in SECRET_ROW:
        if raw_key.startswith(_SEED_PREFIX):
            resolved = resolve_seed(raw_key[len(_SEED_PREFIX):])
            if resolved is None:
                continue
            entity_key, label = resolved
            entries.append((entity_key, label, "rta"))
        else:
            entries.append((raw_key, fallback_label, "igt"))
    return entries


def card_keys(resolve_seed) -> list[str]:
    """Every entity key on the card, in card order: 15 courses x 7 stars,
    then the Secret row (a dropped movement shortens this list)."""
    keys = [f"star:{course_id}:{star_id}"
            for course_id in range(1, 16) for star_id in range(7)]
    keys.extend(entity_key for entity_key, _label, _clock in _secret_entries(resolve_seed))
    return keys


def _tile(you: dict, goal: dict, key: str, label: str, clock: str, folded: bool) -> dict:
    you_cs = you.get(key)
    goal_cs = goal.get(key)
    delta_cs = (you_cs - goal_cs) if you_cs is not None and goal_cs is not None else None
    return {"key": key, "label": label, "clock": clock,
            "you_cs": you_cs, "goal_cs": goal_cs, "delta_cs": delta_cs,
            "folded": folded}


def _sum_tiles(tiles: list[dict]) -> dict:
    """A row's (or the card's) Sigma: only tiles that are unfolded AND have
    both sides count, so the sum is always over one comparable set. `total`
    is the row's physical tile count regardless of folding or coverage --
    it is the denominator the "6/7" coverage chip divides by, not a second
    filtered sum."""
    counted_tiles = [t for t in tiles
                      if not t["folded"] and t["you_cs"] is not None and t["goal_cs"] is not None]
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


def build_card(*, you: dict[str, int], goal: dict[str, int],
                fold: dict[int, int | None], resolve_seed) -> dict:
    """The scorecard payload: 15 course rows, then the Secret row, each with
    its tiles and a fold-aware Sigma; `total` runs the same Sigma over every
    row's tiles. `you`/`goal` map entity key -> displayed centiseconds;
    `fold` maps course_id -> the exit star id both sums skip for that
    course (None or absent = no fold)."""
    rows = []
    for course_id in range(1, 16):
        fold_star = fold.get(course_id)
        tiles = [_tile(you, goal, f"star:{course_id}:{star_id}",
                        star_name(course_id, star_id), "igt",
                        folded=(star_id == fold_star))
                 for star_id in range(7)]
        rows.append({"course_id": course_id, "label": COURSE_NAMES[course_id],
                      "tiles": tiles, "sum": _sum_tiles(tiles)})

    secret_tiles = [_tile(you, goal, key, label, clock, folded=False)
                     for key, label, clock in _secret_entries(resolve_seed)]
    rows.append({"course_id": None, "label": "Secret",
                  "tiles": secret_tiles, "sum": _sum_tiles(secret_tiles)})

    all_tiles = [tile for row in rows for tile in row["tiles"]]
    return {"rows": rows, "total": _sum_tiles(all_tiles)}
