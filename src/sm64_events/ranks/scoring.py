"""The MARELO 0-100 curve (spec 2026-07-24-marelo-rank-system-design section 4).

Score anchors are sm64-xcams' own player bands, read from their shipped bundle:
Mario >=95, Grandmaster >=90 ... Silver >=25 (they define no Bronze threshold,
so ours is a 10 chosen to leave the Iron tail a decade of its own). Reusing
their exact values is not cosmetic -- it is what makes THE invariant hold:

    tier_from_score(score_for(L, t), defined_tiers(L)) == classify.rank_for(L, t)

i.e. the number and the medal can never disagree, because the score passes
through each anchor exactly at that tier's cutoff time. `defined_tiers` is a
REQUIRED argument for entity-level lookups: a ladder with no Master still
interpolates through the 80-90 range, and a full-table lookup would report
Master for a time rank_for calls Diamond. Aggregates (no ladder) use the full
table by omitting it.

Pure: no I/O, no db, no standards store."""
from sm64_events.ranks.classify import RANK_NAMES

__all__ = ["RANK_NAMES", "SCORE_ANCHORS", "TOP_SCORE", "DIVISIONS_PER_TIER",
           "DIVISION_NUMERALS", "defined_tiers", "best_ladder", "score_for",
           "tier_from_score", "tier_band", "division_for", "progression_key",
           "next_tier_target", "division_progress"]

# hardest -> easiest; Iron is the implicit floor and carries NO anchor, exactly
# as it carries no threshold in classify.
SCORE_ANCHORS = {"Mario": 95.0, "Grandmaster": 90.0, "Master": 80.0,
                 "Diamond": 70.0, "Platinum": 60.0, "Gold": 45.0,
                 "Silver": 25.0, "Bronze": 10.0}
TOP_SCORE = 100.0

DIVISIONS_PER_TIER = 5
DIVISION_NUMERALS = ["V", "IV", "III", "II", "I"]   # index 0 = bottom of the tier

_TIERS = [tier for tier in RANK_NAMES if tier != "Iron"]      # hardest -> easiest


def defined_tiers(ladder_cs: dict[str, int]) -> list[str]:
    """The ladder's tiers, hardest first, Iron excluded. Same order classify
    iterates, so the invariant's two sides walk the ladder identically."""
    return [tier for tier in _TIERS if tier in ladder_cs]


def best_ladder(ladders: dict[str, dict[str, float]]) -> dict[str, int]:
    """{strat: {rank: SECONDS}} -> {rank: CENTISECONDS}, pointwise minimum.

    'The best time achievable at this tier by any known strategy' -- which is
    what an entity score must grade against, so that mastering a slow strategy
    maxes the strat score without maxing the star. The min of monotone ladders
    is monotone, so the result is always a valid ladder."""
    out = {}
    for ladder in ladders.values():
        for rank, seconds in ladder.items():
            cs = int(round(seconds * 100))
            if rank not in out or cs < out[rank]:
                out[rank] = cs
    return out


def score_for(ladder_cs: dict[str, int], time_cs: int) -> float | None:
    """0..100 for a displayed time against one ladder; None if empty.

    Piecewise linear in TIME through the anchors, so equal time savings inside
    a tier are equal score. Faster than the hardest tier extrapolates that
    tier's slope (capped at 100); slower than the easiest decays asymptotically
    so a bad run trends toward 0 without ever being a zero -- score 0 is
    reserved for 'no time at all', which is the coverage penalty."""
    points = [(ladder_cs[tier], SCORE_ANCHORS[tier]) for tier in defined_tiers(ladder_cs)]
    if not points:
        return None
    hardest_cs, hardest_score = points[0]
    if time_cs <= hardest_cs:
        if len(points) == 1:
            return hardest_score
        next_cs, next_score = points[1]
        slope = (next_score - hardest_score) / (next_cs - hardest_cs)
        return min(TOP_SCORE, hardest_score + slope * (time_cs - hardest_cs))
    for (faster_cs, faster_score), (slower_cs, slower_score) in zip(points, points[1:]):
        if time_cs <= slower_cs:
            span = slower_cs - faster_cs
            if span <= 0:
                return slower_score
            return faster_score + (slower_score - faster_score) * (time_cs - faster_cs) / span
    easiest_cs, easiest_score = points[-1]
    return easiest_score * easiest_cs / time_cs


def tier_from_score(score: float, defined: list[str] | None = None) -> str:
    """Hardest tier in `defined` whose anchor the score reaches; Iron below all.
    Omit `defined` only for aggregates, which have no ladder."""
    for tier in (defined if defined is not None else _TIERS):
        if score >= SCORE_ANCHORS[tier]:
            return tier
    return "Iron"


def tier_band(tier: str, defined: list[str] | None = None) -> tuple[float, float]:
    """(low, high) score range the tier occupies. The top defined tier runs to
    100; Iron runs from 0 up to the easiest defined anchor."""
    present = [tier_name for tier_name in (defined if defined is not None else _TIERS)
               if tier_name in SCORE_ANCHORS]
    if tier == "Iron" or not present:
        return 0.0, (SCORE_ANCHORS[present[-1]] if present else TOP_SCORE)
    index = present.index(tier)
    high = SCORE_ANCHORS[present[index - 1]] if index > 0 else TOP_SCORE
    return SCORE_ANCHORS[tier], high


def division_for(score: float, defined: list[str] | None = None) -> tuple[str, str]:
    """(tier, numeral) -- five equal score-width slices of the tier's band,
    V at the bottom. Band edges come from `defined`, so a division can never
    name a tier the ladder does not define."""
    tier = tier_from_score(score, defined)
    low, high = tier_band(tier, defined)
    span = high - low
    if span <= 0:
        return tier, DIVISION_NUMERALS[-1]
    index = int((score - low) / span * DIVISIONS_PER_TIER)
    return tier, DIVISION_NUMERALS[max(0, min(DIVISIONS_PER_TIER - 1, index))]


def progression_key(tier: str, numeral: str) -> int:
    """Monotone rank position (higher is better) for comparing two ranks --
    THE ordering the celebration watermark stores. Iron V is 0."""
    tier_index = len(RANK_NAMES) - 1 - RANK_NAMES.index(tier)
    return tier_index * DIVISIONS_PER_TIER + DIVISION_NUMERALS.index(numeral)


def next_tier_target(score: float, defined: list[str] | None = None) -> float:
    """The score that reaching the next harder tier requires; 100 at the top,
    so a top-tier entity still shows a remaining gain instead of dropping off
    the 'what should I practice' list."""
    return tier_band(tier_from_score(score, defined), defined)[1]


def division_progress(score: float, defined: list[str] | None = None) -> dict:
    """Where the score sits within its OWN division, and what the next STEP
    is -- one division up within the same tier, or (already at the top
    division) the bottom division of the next harder tier. The near-goal
    sibling of `next_tier_target`: a whole-TIER bar barely moves after one
    good run; a whole-DIVISION bar visibly does (spec 2026-07-25 round 2,
    "the LP model" the user kept pointing at -- League-style Bronze
    V/IV/.../I sub-ranks within a tier).

    Mirrors `ranks/scopes.py::_division_progress`'s band-edge math (that one
    grades a MARELO scope aggregate, which has no ladder of its own, so it
    always uses the full rank table); this is the `defined`-aware sibling a
    per-entity ladder needs -- a ragged ladder's division band edges must
    come from the tiers it actually defines, same requirement `division_for`
    already documents.

    Returns {"tier", "division", "fill" (0..1, position inside the current
    division), "next_tier", "next_division"}. next_* are None exactly when
    there is no next step -- the hardest tier this ladder defines, division
    I -- detected by recomputing where the division's own ceiling score
    lands: if that's still "here", there's nowhere higher to go."""
    tier, division = division_for(score, defined)
    low, high = tier_band(tier, defined)
    width = (high - low) / DIVISIONS_PER_TIER
    if width <= 0:
        fill, next_at = 1.0, TOP_SCORE
    else:
        index = DIVISION_NUMERALS.index(division)
        div_low = low + index * width
        fill = max(0.0, min(1.0, (score - div_low) / width))
        next_at = min(TOP_SCORE, div_low + width)
    next_tier, next_division = division_for(next_at, defined)
    maxed = next_tier == tier and next_division == division
    return {"tier": tier, "division": division, "fill": 1.0 if maxed else fill,
            "next_tier": None if maxed else next_tier,
            "next_division": None if maxed else next_division}
