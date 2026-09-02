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

__all__ = ["SECRET_ROW", "BOWSER_REDS", "SPECIAL_STAR_LABELS", "FIGHTS_LABEL",
           "SECRET_LABEL", "BOWSER_LABEL", "bowser_number",
           "hundred_coin_companion", "template_rows",
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
    ("star:21:0", COURSE_NAMES[21]),
    ("star:22:0", COURSE_NAMES[22]),
    ("star:20:0", COURSE_NAMES[20]),
    ("star:23:0", COURSE_NAMES[23]),
    # The five castle secrets, RESTORED in round 9 (2026-08-28) -- his "This
    # should collectively contain all 120 stars" reversed round 6's
    # template-faithful omission (the community sheet leaves them out
    # because they are untimed grabs in a run; his card counts them anyway).
    # They close the row so the sheet-faithful ten keep their order.
    ("star:0:0", star_name(0, 0)),
    ("star:0:1", star_name(0, 1)),
    ("star:0:2", star_name(0, 2)),
    ("star:0:3", star_name(0, 3)),
    ("star:0:4", star_name(0, 4)),
]

# The Bowser card's stars (round 21, 2026-09-02, his list: "Bowser in the
# Dark World Red Coins, Bowser 1, Bowser in the Fire Sea Red Coins, Bowser 2,
# Bowser in the Sky Red Coins, Bowser 3"): the three reds stars, each
# followed by its fight. They left the Secret card with that list -- the
# template's own labels append "Red Coins" to the course name because the
# star is named "8 Red Coins" in-game for all three. `_BOWSER_INDEX` is
# which Bowser a reds star belongs to; a FIGHT says which Bowser it is by
# its seed key (`seg:bowser-N`, `bowser_number`), never by its name.
BOWSER_REDS: list[tuple[str, str]] = [
    ("star:16:0", f"{COURSE_NAMES[16]} Red Coins"),
    ("star:17:0", f"{COURSE_NAMES[17]} Red Coins"),
    ("star:18:0", f"{COURSE_NAMES[18]} Red Coins"),
]
_BOWSER_INDEX = {key: index + 1 for index, (key, _label) in enumerate(BOWSER_REDS)}


def bowser_number(seed_key) -> int | None:
    """`seg:bowser-2` -> 2; anything else (a user-built fight, no seed) ->
    None, which the Bowser card orders after the three pairs."""
    if not isinstance(seed_key, str) or not seed_key.startswith("seg:bowser-"):
        return None
    tail = seed_key[len("seg:bowser-"):]
    return int(tail) if tail.isdigit() else None


# One dict for "this star's card label is not star_name" -- both specials
# cards' own overrides, reused by the route grouping below so a Bowser reds
# star is labelled identically whichever scope drew it.
SPECIAL_STAR_LABELS = dict(SECRET_ROW) | dict(BOWSER_REDS)


# Where an entry sits in a card, whatever order the scope reached it in
# (round 17): "the scorecard should always be ordered by star order... we
# already have this data in our system". A course's stars run in SLOT
# order -- the game's own star-select order, which is also his reference
# sheet's; a Secret-card star runs in SECRET_ROW's order; a segment
# follows the stars in the order the scope met it, since a movement has no
# slot to sort by. Applied BEFORE the 100c merge, which keeps the
# companion's position, so "Wiggler's Red Coins + 100c" lands on the reds
# star's line exactly as the sheet has it.
_SEGMENT_ORDER_BASE = 1000


def _template_order(entries: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    secret_index = {key: index for index, (key, _label) in enumerate(SECRET_ROW)}

    def sort_key(pair):
        arrival, (key, _label, _clock) = pair
        parts = key.split(":")
        if parts[0] == "star":
            course_id, star_id = int(parts[1]), int(parts[2])
            if 1 <= course_id <= 15:
                return (star_id, arrival)
            return (secret_index.get(key, len(secret_index) + arrival), arrival)
        return (_SEGMENT_ORDER_BASE + arrival, arrival)

    return [entry for _key, entry in
            sorted(((index, entry) for index, entry in enumerate(entries)),
                   key=sort_key)]


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


FIGHTS_LABEL = "Bowser Fights"
# The two cards that are not courses. Round 20 merged them into one so his
# 4x4 grid held 16 things; round 21 (2026-09-02) split them again on his
# list -- the merged card was twice a course card's height and the grid
# stretched every card to match it, "impossible to take a screenshot of
# everything in a single page". Composed HERE rather than in the browser,
# for the reason the merge was: the Sigma, the CSV and the card's own
# ignore button all read one row through one path. `kind` is how the
# browser tells them apart (tint, art) without matching on a label.
SECRET_LABEL = "Secret"
BOWSER_LABEL = "Bowser"


def _secret_row(entries) -> dict:
    return {"course_id": None, "kind": "secret", "label": SECRET_LABEL,
            "entries": entries}


def _bowser_row(entries) -> dict:
    return {"course_id": None, "kind": "bowser", "label": BOWSER_LABEL,
            "entries": entries}


def _bowser_order(entries, seed_keys: dict) -> list:
    """Reds star, then its fight, per Bowser in order; a fight whose seed
    key names no Bowser, or any other entry, follows the pairs in arrival
    order. `seed_keys` maps a segment entity key to its seed key."""
    def sort_key(pair):
        arrival, (key, _label, _clock) = pair
        number = _BOWSER_INDEX.get(key)
        if number is not None:
            return (number, 0, arrival)
        number = bowser_number(seed_keys.get(key))
        if number is not None:
            return (number, 1, arrival)
        return (_SEGMENT_ORDER_BASE + arrival, 0, arrival)
    return [entry for _key, entry in
            sorted(enumerate(entries), key=sort_key)]


def _fight_entries(fight_segments) -> tuple[list, dict]:
    """`fight_segments` rows are `(entity_key, label)` or
    `(entity_key, label, seed_key)`; returns the rta entries and the
    seed-key map `_bowser_order` wants."""
    entries, seed_keys = [], {}
    for fight in fight_segments:
        key, label = fight[0], fight[1]
        seed_keys[key] = fight[2] if len(fight) > 2 else None
        entries.append((key, label, "rta"))
    return entries, seed_keys


def template_rows(fight_segments=()) -> list[dict]:
    """The Overall card set: 15 course cards in COURSE order (6 cells each --
    the 100c cell combined with its companion), then the Secret card (the
    seven secret-stage stars, the castle stars after them) and the Bowser
    card (each reds star followed by its fight -- round 9: "all 120 stars,
    plus the bowser fights"; the fights are segments, so the ROUTER finds
    them by category and this stays pure). `fight_segments` is
    [(entity_key, label)] or [(entity_key, label, seed_key)]; the seed key
    is what pairs a fight with its Bowser."""
    rows = [_course_row(course_id) for course_id in range(1, 16)]
    rows.append(_secret_row([(key, label, "igt") for key, label in SECRET_ROW]))
    fights, seed_keys = _fight_entries(fight_segments)
    reds = [(key, label, "igt") for key, label in BOWSER_REDS]
    rows.append(_bowser_row(_bowser_order(reds + fights, seed_keys)))
    return rows


def rows_for_course(course_id: int) -> list[dict]:
    """A course scope's one row: the template row for a main course; for a
    course whose card presence lives in the Secret row (the Bowser reds, the
    cap stages, PSS/SA/WMotR) that course's own Secret entries. LookupError
    for a course the card has no cells for at all."""
    if 1 <= course_id <= 15:
        return [_course_row(course_id)]
    entries = [(key, label, "igt") for key, label in SECRET_ROW + BOWSER_REDS
               if key.startswith(f"star:{course_id}:")]
    if not entries:
        raise LookupError(f"no scorecard cells for course {course_id}")
    return [{"course_id": course_id, "label": COURSE_NAMES[course_id],
             "entries": entries}]


def rows_for_route(route: dict, *, segment_labels: dict[int, str],
                   segment_courses: dict[int, int] | None = None,
                   fight_segment_ids=()) -> list[dict]:
    # `fight_segment_ids`: the fight segments' ids, as a set, or as a dict
    # {segment_id: seed_key} so the Bowser card can pair each fight with
    # its reds star (round 21).
    """Round 9 (2026-08-28): a route scope composes CARDS, not step rows --
    "Each cell is a card representing a course / category of stars /
    segments... BOB is all of the bobomb battlefield stars". One card per
    COURSE with repeat visits MERGED, in COURSE order; every one-off --
    castle secrets, cap stages, Bowser reds, the Bowser FIGHTS the caller
    identifies by category, and any in-scope segment with no course of its
    own -- collects into the Secret card; the Bowser reds and the fights
    into the Bowser card. Both close the set, Secret first (round 21).

    Round 9 ordered the course cards by when the route first TOUCHES each
    course, in his words at the time. Round 20 reversed that on his report
    against a live route card: "the order of the cards is wrong... the order
    should be displayed in COURSE order. That is Bob -> WF -> JRB -> CCM ->
    ..." The sheet he grades himself against is laid out that way, so a card
    that reorders itself per route cannot be read beside it. The lines
    INSIDE a card still follow the template (round 17).

    Duplicate entities (a route can revisit) keep their first appearance;
    the 100c companion merge applies per card. A candidate `segment_labels`
    cannot name (a deleted segment) draws no cell."""
    segment_courses = segment_courses or {}
    fight_seed_keys = (dict(fight_segment_ids) if isinstance(fight_segment_ids, dict)
                       else {segment_id: None for segment_id in fight_segment_ids})
    fight_ids = set(fight_seed_keys)
    order: list[tuple] = []
    buckets: dict[tuple, list] = {}
    seen: set[str] = set()

    def add(bucket: tuple, entry) -> None:
        if entry[0] in seen:
            return
        seen.add(entry[0])
        if bucket not in buckets:
            buckets[bucket] = []
            order.append(bucket)
        buckets[bucket].append(entry)

    for step in route.get("steps", []):
        for candidate in step.get("candidates", []):
            key = candidate_key(candidate)
            if key is None:
                continue
            parts = key.split(":")
            if parts[0] == "star":
                course_id, star_id = int(parts[1]), int(parts[2])
                label = SPECIAL_STAR_LABELS.get(
                    key, star_name(course_id, star_id))
                bucket = (("course", course_id) if 1 <= course_id <= 15
                          else ("bowser",) if key in _BOWSER_INDEX
                          else ("secret",))
                add(bucket, (key, label, "igt"))
            else:
                segment_id = int(parts[1])
                label = segment_labels.get(segment_id)
                if label is None:
                    continue
                course_id = segment_courses.get(segment_id)
                if segment_id in fight_ids:
                    bucket = ("bowser",)
                elif course_id is not None and 1 <= course_id <= 15:
                    bucket = ("course", course_id)
                else:
                    bucket = ("secret",)
                add(bucket, (key, label, "rta"))

    def row_of(bucket: tuple) -> dict:
        # Route order decides which CARDS exist and in what order; the
        # TEMPLATE decides the order of the lines inside one (round 17).
        entries = _merge_hundred_coins(_template_order(buckets[bucket]))
        return {"course_id": bucket[1], "label": COURSE_NAMES[bucket[1]],
                "entries": entries}

    # COURSE order, not the order the route first touches each course --
    # round 20, 2026-09-01, reversing round 9's "It goes in Route order".
    # His report, looking at a route card: "the order of the cards is wrong.
    # As seen in this sheet, the order should be displayed in COURSE order.
    # That is Bob -> WF -> JRB -> CCM -> ..." The reference sheet he grades
    # himself against is laid out that way, so a card that reorders itself
    # per route cannot be read against it. The lines INSIDE a card still
    # follow the template (round 17).
    course_buckets = sorted((b for b in order if b[0] == "course"),
                            key=lambda bucket: bucket[1])
    secret = _merge_hundred_coins(_template_order(buckets.get(("secret",), [])))
    seed_keys = {f"segment:{segment_id}": seed_key
                 for segment_id, seed_key in fight_seed_keys.items()}
    bowser = _bowser_order(buckets.get(("bowser",), []), seed_keys)
    return ([row_of(bucket) for bucket in course_buckets]
            + ([_secret_row(secret)] if secret else [])
            + ([_bowser_row(bowser)] if bowser else []))


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
    # No `you_rank`/`goal_rank` since round 23: the caps a line wore were
    # deleted with their toggle ("Not going to use it ever"), and with them
    # the router's per-tile grading. `scorecardgoal.js::_recomputeTile`
    # mirrors exactly this shape.
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
    """The slowest displayed centisecond that grades AT LEAST
    `(tier, division)` on this ladder -- the goal time a tile shows for that
    rank. None only when `tier` is not one this ladder defines at all.

    "At least", not "exactly" (round 12, 2026-08-29): near the fastest tiers
    a whole division can span ZERO integer centiseconds -- his own ladders
    put 3cs across the entire Metal tier of Get a Hand, so Metal 2 had no
    exact time and four tiles showed "set a time..." under a Metal goal.
    His ruling: "we should generate a reasonable [goal] based on the times.
    We should never be missing a tier like this in our system." A time that
    grades a division ABOVE the asked one still honours the goal -- reaching
    it puts you at least where you aimed -- and wherever the exact division
    IS reachable this resolves to the identical centisecond, because the
    slowest time meeting the division's entry score sits inside the division
    whenever anything does.

    `division`'s own entry score (the low edge of its five-way slice of the
    tier's score band) is exactly the score of the slowest time still at or
    inside it, so `time_for_score` inverts straight to (approximately) the
    answer; the loop only corrects the centisecond-rounding slop
    `time_for_score` and `progress_for_time` can each introduce
    independently, walking toward the last centisecond whose score still
    meets the entry. Bounded at 10 steps -- ample for rounding slop (the
    pre-round-12 bound was measured over all 4,580 seed combos)."""
    defined = defined_tiers(ladder_cs)
    if tier not in defined:
        return None
    low, high = tier_band(tier, defined)
    entry_score = low + (high - low) * DIVISION_NUMERALS.index(division) / DIVISIONS_PER_TIER
    cs = time_for_score(ladder_cs, entry_score)
    if cs is None:
        return None
    for _ in range(10):
        if progress_for_time(ladder_cs, cs)["score"] >= entry_score:
            if progress_for_time(ladder_cs, cs + 1)["score"] < entry_score:
                return cs                 # the slowest cs still meeting the entry
            cs += 1                       # still meets it -- push toward the slowest
        else:
            cs -= 1                       # scores below the entry -- speed up
    return None                           # rounding never converged (unseen in the seed)


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
                      "kind": row.get("kind", "course"),
                      "tiles": tiles, "sum": _sum_tiles(tiles)})
    all_tiles = [tile for row in rows for tile in row["tiles"]]
    return {"rows": rows, "total": _sum_tiles(all_tiles)}
