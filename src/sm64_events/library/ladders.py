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
from sm64_events.core.timefmt import attainable_cs, prev_attainable_cs
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
    cutoffs sharing one gap, which would mint a tier nobody can occupy.

    The edge is the last DISPLAYABLE time before the slow cluster starts, so
    the cluster's own members stay outside the tier."""
    span = VALLEY_FRACTION * times[len(times) // 2]
    for lower, upper in zip(times, times[1:]):
        if lower < cutoff < upper and (upper - lower) > span:
            return prev_attainable_cs(upper)
    return None


# The derivation, as named steps. Every one is replaceable without touching
# the others, which is the point: the model has already changed once (a fixed
# ratio off the Mario cutoff, replaced by percentiles) and the user's standing
# instruction is that changing it again must stay cheap.
def place_at_percentiles(times, percentiles) -> dict:
    """Step 1 — each rank lands at its percentile of this row's own times."""
    return {rank: _at_percentile(times, percentiles[rank])
            for rank in RANK_NAMES if rank in percentiles}


def avoid_valleys(times, raw: dict) -> dict:
    """Step 2 — a cutoff inside a real observation gap moves to its edge."""
    out = {}
    for rank, cutoff in raw.items():
        moved = _valley_edge(times, cutoff)
        out[rank] = moved if moved is not None else cutoff
    return out


def make_attainable(raw: dict, quantise=attainable_cs) -> dict:
    """Step 3 — every cutoff becomes a time the timer can actually show, and
    tiers the data cannot tell apart MERGE instead of being invented.

    Usamune's clock is a frame counter, so only 30 of every 100 centisecond
    values ever appear: 0, 3, 6, 10, 13, 16, 20, 23, 26, 30 … A ladder asking
    for 15.01 asks for something nobody can hit, and 2,435 of this project's
    4,656 derived cutoffs did exactly that before 2026-08-05.

    Rounds UP, never down: a cutoff is a threshold you must beat, so rounding
    down would quietly make a rank harder than the number it came from, and
    rounding up biases every derived ladder gentle (user's ruling).

    When two ranks quantise to the SAME frame, the faster rank keeps it and
    the slower one is dropped — a sparse ladder, which the whole rank system
    already supports (`defined_tiers`, and vetted ladders ship with skipped
    tiers). The previous rule pushed the slower rank one frame later instead,
    and on a 2-second door that FABRICATES the ladder: eight percentile
    targets over four distinct frames became a chain of +1s whose Bronze sat
    a median 10 cs past its intended percentile with 0.0% of the community
    slower than it — 43 such ladders, measured 2026-08-06. Eight tiers do not
    fit in four frames, and inventing thresholds slower than every recorded
    human is worse than admitting four tiers."""
    out, previous = {}, None
    for rank in RANK_NAMES:
        if rank not in raw:
            continue
        cutoff = quantise(int(round(raw[rank])))
        if previous is not None and cutoff <= previous:
            continue                       # merged into the faster rank above
        out[rank] = cutoff
        previous = cutoff
    return out


def fit_ladder(times_cs, percentiles=None, quantise=attainable_cs) -> dict:
    """{rank: seconds} for one row's community times, or {} when the row is
    too thin to say anything.

    Three steps, each replaceable on its own: place at percentiles, move out
    of observation valleys, make attainable. Pass `quantise=lambda cs: cs` to
    derive a ladder for a clock that is not Usamune's."""
    times = sorted(int(t) for t in times_cs)
    if len(times) < MIN_ENTRIES:
        return {}
    raw = place_at_percentiles(times, percentiles or LADDER_PERCENTILES)
    raw = avoid_valleys(times, raw)
    return {rank: round(cutoff / 100, 2)
            for rank, cutoff in make_attainable(raw, quantise).items()}


def row_times(item):
    """(times, which ROM version they came from) — the population a ladder is
    fitted over. NEVER a mix of two versions.

    A (JP)/(US) pair merges into ONE approach carrying both populations, and on
    JRB's stone pillar those are 10.80 and 14.50: a ladder fitted across that
    pile spans a gap no single player can be on both sides of. US is preferred
    because that is the convention `tools/scrape_ranks.py` applies to the
    vetted ladders ("US where a US time exists, else JP") and the two sources
    have to describe the same thing -- but only when US clears the floor on its
    own, because discarding 39 JP times for 6 US ones buys consistency by
    throwing the answer away."""
    entries = item["entries"]
    by_version = {}
    for entry in entries:
        by_version.setdefault(entry.get("version"), []).append(entry["time_cs"])
    if len(by_version) > 1 or (by_version and None not in by_version):
        preferred = by_version.get("us", [])
        if len(preferred) >= MIN_ENTRIES:
            return sorted(preferred), "us"
        version, times = max(by_version.items(), key=lambda kv: len(kv[1]))
        return sorted(times), version
    return sorted(entry["time_cs"] for entry in entries), None


def fit_payload(payload: dict) -> dict:
    """Stamp a fitted ladder onto every approach and subsection that can carry
    one. Returns the same payload; the caller writes it out."""
    fitted = thin = 0
    for target in payload["targets"]:
        for item in target["approaches"] + target["subsections"]:
            times, version = row_times(item)
            ladder = fit_ladder(times)
            if ladder:
                item["ladder"] = ladder
                # Which population it describes, so a JP-fitted ladder never
                # passes silently for a US one.
                item["ladder_version"] = version
                item["ladder_samples"] = len(times)
                fitted += 1
            else:
                item.pop("ladder", None)
                item.pop("ladder_version", None)
                item["ladder_samples"] = len(times)
                thin += 1
    payload["ladder_model"] = {
        "percentiles": dict(LADDER_PERCENTILES),
        "min_entries": MIN_ENTRIES,
        "source": "sheet",
        "fitted_rows": fitted,
        "rows_too_thin": thin,
    }
    return payload
