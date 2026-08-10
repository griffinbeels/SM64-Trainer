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
           "DIVISION_NUMERALS", "defined_tiers", "best_ladder",
           "best_ladder_owners", "score_for",
           "tier_from_score", "tier_band", "division_for", "progression_key",
           "next_tier_target", "division_progress", "time_for_score",
           "progress_for_time"]

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


def best_ladder_owners(ladders: dict[str, dict[str, float]]) -> dict[str, list[str]]:
    """{strat: {rank: SECONDS}} -> {rank: [strat, ...]}, who SETS each tier of
    `best_ladder`.

    The pointwise minimum is a ladder no single strategy necessarily owns --
    one way can be fastest at Mario while another sets Bronze -- so "what does
    it take to rank up overall" is only half an answer without "and by doing
    what". Ties are real and common (two ways published to the same
    centisecond), so every winner is named rather than one picked arbitrarily;
    order is the caller's iteration order, made stable by sorting."""
    best = best_ladder(ladders)
    owners: dict[str, list[str]] = {rank: [] for rank in best}
    for strat, ladder in ladders.items():
        for rank, seconds in ladder.items():
            if int(round(seconds * 100)) == best[rank]:
                owners[rank].append(strat)
    return {rank: sorted(names) for rank, names in owners.items()}


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
    division), "next_tier", "next_division", "next_at" (the SCORE the next
    step begins at)}. next_* are None exactly when there is no next step --
    the hardest tier this ladder defines, division I -- detected by
    recomputing where the division's own ceiling score lands: if that's
    still "here", there's nowhere higher to go. `next_at` lets a caller with
    the raw ladder (this function only sees `defined`, the tier NAMES, never
    the ladder itself) convert the remaining score gap into a TIME gap via
    `time_for_score` -- see views.py's `_graded_progress`, the one place
    that wiring happens."""
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
            "next_division": None if maxed else next_division,
            "next_at": None if maxed else next_at}


def progress_for_time(ladder_cs: dict[str, int], time_cs: int) -> dict:
    """`division_progress` for a real TIME on a real ladder, plus the
    centiseconds still owed to the next step -- and THE place the ladder's
    displayed-centisecond boundary rule is applied.

    Why that rule has to exist here. `division_progress` compares SCORE
    against an exact band edge, but a division's edge falls at a FRACTIONAL
    centisecond, and `time_for_score` rounds it to the nearest one to report
    it. A time within half a centisecond of an edge therefore graded as "not
    there yet" while the gap it was handed -- `time_cs - round(edge)` -- was
    exactly 0, and the banner read "0.00s to rank up" (live report
    2026-07-29, a Waluigi II sitting on the Waluigi I edge). Zero is not a
    number anybody can chase, and it is not even reachable: IGT advances a
    whole frame at a time, ~3.33cs.

    So a step is REACHED when the displayed time reaches that step's own
    displayed cutoff, `<=` -- which is exactly the rule
    `classify.rank_for` already applies at a TIER cutoff; this extends it to
    the division edges in between. It can never move a tier on its own: a
    tier edge IS an anchor, `time_for_score` returns an anchor's cutoff
    exactly (no rounding), so a time equal to one already scores at or above
    that anchor and `division_for` has already placed it there.

    The walk is a loop because two adjacent division edges can round to the
    same centisecond on a tight ladder; it terminates because every pass
    strictly raises `next_at` and the ladder is finite. `score` is raised
    with it, so the returned dict stays self-consistent -- a caller that
    re-derives the division from the score it was handed gets the division it
    was handed too.

    Returns `division_progress`'s own fields plus `score` and `next_gap_cs`
    (centiseconds still to save, >= 1 whenever there is a next step at all --
    which is what makes "0.00s to rank up" unrepresentable rather than merely
    unlikely). `ladder_cs` must be non-empty, same precondition
    `score_for`'s callers already carry."""
    score = score_for(ladder_cs, time_cs)
    defined = defined_tiers(ladder_cs)
    progress = division_progress(score, defined)
    next_gap_cs = None
    while progress["next_at"] is not None:
        target_cs = time_for_score(ladder_cs, progress["next_at"])
        if target_cs is None:
            break
        if time_cs > target_cs:
            next_gap_cs = time_cs - target_cs
            break
        score = progress["next_at"]
        progress = division_progress(score, defined)
    return {"score": score, **progress, "next_gap_cs": next_gap_cs}


def time_for_score(ladder_cs: dict[str, int], target_score: float) -> int | None:
    """The algebraic inverse of `score_for`: the time (centiseconds) that
    earns exactly `target_score` on this ladder, or None if the ladder is
    empty. Mirrors `score_for`'s three regimes exactly -- extrapolation
    faster than the hardest tier, linear interpolation between two adjacent
    anchors, the Iron tail's asymptotic decay -- each solved for time instead
    of score, so a computed time delta can never disagree with the
    tier/division the same time would produce through `score_for` itself:

        score_for(L, time_for_score(L, s)) == s   (up to centisecond rounding)

    Needed because `division_progress` only ever deals in SCORE (it has no
    ladder to convert with) -- this is what turns its `next_at` into an
    actual "-1.60s" a runner can chase, without a JS copy of the curve."""
    points = [(ladder_cs[tier], SCORE_ANCHORS[tier]) for tier in defined_tiers(ladder_cs)]
    if not points:
        return None
    hardest_cs, hardest_score = points[0]
    if target_score >= hardest_score:
        if len(points) == 1:
            return hardest_cs
        next_cs, next_score = points[1]
        slope = (next_score - hardest_score) / (next_cs - hardest_cs)
        if slope == 0:
            return hardest_cs
        return round(hardest_cs + (target_score - hardest_score) / slope)
    for (faster_cs, faster_score), (slower_cs, slower_score) in zip(points, points[1:]):
        if target_score >= slower_score:
            span = slower_cs - faster_cs
            if span <= 0:
                return slower_cs
            frac = (target_score - faster_score) / (slower_score - faster_score)
            return round(faster_cs + frac * span)
    easiest_cs, easiest_score = points[-1]
    if target_score <= 0:
        return None
    return round(easiest_score * easiest_cs / target_score)
