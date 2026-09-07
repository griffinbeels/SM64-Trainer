"""Stable statistical signature for matching the existing strategy names.

These are the historical calibration points used to associate Sheet rows with
Daily Star names. They are NOT rank standards. Keeping identity separate from
the playable ladder prevents a rank-model revision from moving saved attempts
to a different Sheet row (38 pairings changed in the initial model-3 probe).
New observations still refine this signature; changing its calibration is an
identity migration requiring its own review.
"""
from sm64_events.core.timefmt import attainable_cs, prev_attainable_cs

_POSITIONS = {"Mario": 6.7, "Grandmaster": 21.7, "Master": 45., "Diamond": 65.2,
              "Platinum": 80.4, "Gold": 89.3, "Silver": 94., "Bronze": 98.2}


def matching_profile(times):
    """The original matching calibration in seconds, never a grading ladder."""
    if not times:
        return {}
    times = sorted(times)
    out, previous = {}, None
    for rank, percent in _POSITIONS.items():
        position = percent / 100 * (len(times) - 1)
        low = int(position)
        high = min(low + 1, len(times) - 1)
        raw = times[low] + (position - low) * (times[high] - times[low])
        for lower, upper in zip(times, times[1:], strict=False):
            if lower < raw < upper and upper - lower > .04 * times[len(times) // 2]:
                raw = prev_attainable_cs(upper)
                break
        cutoff = attainable_cs(round(raw))
        if previous is None or cutoff > previous:
            out[rank] = round(cutoff / 100, 2)
            previous = cutoff
    return out
