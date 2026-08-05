"""Fitted ladders — a rank ladder derived from one library row's own times.

A ladder belongs to a library ROW, not to an entity. Asked which ENTITIES
would gain a ladder the answer is zero (all 112 the sheet maps to already carry
a vetted Daily Star one), which reads as there being nothing to do; that is the
wrong denominator. Every approach and every subsection has its own community
distribution and takes its own ladder, whether or not we map it to anything —
so a Castle Movement keeps a ladder in the library until a segment exists for
it (user, 2026-08-05).

THE PERCENTILES ARE THE DEFINITION, not an approximation of Daily Star. They
were measured as the median position of each vetted cutoff inside its own
approach's distribution over 204 matched ladders, and the user's ruling on that
measurement was to adopt them outright and label what they produce as
sheet-derived.

They cannot reproduce a vetted ladder tier for tier, and that is structural.
Held out, a fitted ladder gives a real recorded time the same tier 39-42% of
the time and lands within ONE tier 75-78%. The median gap between adjacent
vetted tiers is 2.72% (1.32% from Mario to Grandmaster) while this model's
median time error is 1.5-2%: the error is the size of a tier, so nothing fitted
from 20-150 community times can resolve them. Bucketing by duration, scaling by
the distribution's spread, and a canonical shape off the sheet best were all
measured and none helped. Do not re-litigate it by trying a fourth form.
"""
from sm64_events.ranks.classify import RANK_NAMES

# Median position of each vetted cutoff inside its approach's own distribution,
# over the 204 ladders that matched a sheet approach (2026-08-05).
LADDER_PERCENTILES = {
    "Mario": 6.7, "Grandmaster": 21.7, "Master": 45.0, "Diamond": 65.2,
    "Platinum": 80.4, "Gold": 89.3, "Silver": 94.0, "Bronze": 98.2,
}

# A FEASIBILITY floor, not an accuracy one -- the user explicitly declined an
# accuracy floor (error runs 4.19% at 20-49 entries against 1.46% at 150+, and
# he wants the ladder anyway). This is only "can a distribution this small say
# anything about eight separate tiers": below it, neighbouring percentiles land
# on the same observation and the ladder is eight copies of three numbers.
MIN_ENTRIES = 10

# An observation gap this wide, relative to the row's own median, is a real
# discontinuity rather than sampling noise -- a missed cycle, a route fork.
VALLEY_FRACTION = 0.04


def _at_percentile(times, percent):
    """Linear-interpolated quantile over a sorted list of centiseconds."""
    if percent <= 0:
        return float(times[0])
    if percent >= 100:
        return float(times[-1])
    position = percent / 100 * (len(times) - 1)
    low = int(position)
    high = min(low + 1, len(times) - 1)
    return times[low] + (position - low) * (times[high] - times[low])


def _valley_edge(times, cutoff):
    """The slow edge of the observation gap `cutoff` falls inside, or None.

    A cutoff sitting in a gap is arbitrary: everybody already recorded is on
    one side or the other, so moving it changes nobody's rank today. It decides
    the rank of a FUTURE time that lands in the gap, and it stops two adjacent
    cutoffs sharing one gap, which would mint a tier nobody can occupy."""
    span = VALLEY_FRACTION * times[len(times) // 2]
    for lower, upper in zip(times, times[1:]):
        if lower < cutoff < upper and (upper - lower) > span:
            return float(upper) - 1
    return None


def fit_ladder(times_cs, percentiles=None) -> dict:
    """{rank: seconds} for one row's sorted community times, or {} when the
    row is too thin to say anything.

    Cutoffs come out strictly increasing in whole centiseconds, because
    `ranks/classify.py` compares DISPLAYED centiseconds and two tiers sharing a
    cutoff is a tier no time can ever earn."""
    times = sorted(int(t) for t in times_cs)
    if len(times) < MIN_ENTRIES:
        return {}
    percentiles = percentiles or LADDER_PERCENTILES

    raw = {}
    for rank in RANK_NAMES:
        if rank not in percentiles:
            continue
        cutoff = _at_percentile(times, percentiles[rank])
        moved = _valley_edge(times, cutoff)
        raw[rank] = moved if moved is not None else cutoff

    ordered = [rank for rank in RANK_NAMES if rank in raw]
    out, previous = {}, None
    for rank in ordered:
        cutoff = int(round(raw[rank]))
        if previous is not None and cutoff <= previous:
            cutoff = previous + 1
        out[rank] = cutoff
        previous = cutoff
    return {rank: round(cutoff / 100, 2) for rank, cutoff in out.items()}


def row_times(item) -> list:
    return sorted(entry["time_cs"] for entry in item["entries"])


def fit_payload(payload: dict) -> dict:
    """Stamp a fitted ladder onto every approach and subsection that can carry
    one. Returns the same payload; the caller writes it out."""
    fitted = thin = 0
    for target in payload["targets"]:
        for item in target["approaches"] + target["subsections"]:
            ladder = fit_ladder(row_times(item))
            if ladder:
                item["ladder"] = ladder
                fitted += 1
            else:
                item.pop("ladder", None)
                thin += 1
    payload["ladder_model"] = {
        "percentiles": dict(LADDER_PERCENTILES),
        "min_entries": MIN_ENTRIES,
        "source": "sheet",
        "fitted_rows": fitted,
        "rows_too_thin": thin,
    }
    return payload
