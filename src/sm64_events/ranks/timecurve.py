"""Rank interpolation on the timer's frame grid, including the Capless tail.

The display truncates frames to 0, 3, 6, 10… centiseconds. Interpolating those
printed numbers directly can erase a division even when five frames separate
two tier cutoffs. Fractional frame coordinates preserve hand-entered cutoffs
exactly while making five-frame intervals contain five reachable divisions.
"""
import math

from sm64_events.core.timefmt import cs_of_frame, frame_at_or_after


def frame_position(cs):
    """Continuous, invertible position on the displayed frame grid."""
    if cs <= 0:
        return cs * .3
    upper = frame_at_or_after(math.ceil(cs))
    upper_cs = cs_of_frame(upper)
    if upper_cs == cs:
        return float(upper)
    lower_cs = cs_of_frame(upper - 1)
    return upper - 1 + (cs - lower_cs) / (upper_cs - lower_cs)


def display_position(frame):
    """Inverse of frame_position, rounded to a displayed centisecond."""
    if frame <= 0:
        return round(frame / .3)
    lower = math.floor(frame)
    return round(cs_of_frame(lower) + (frame - lower)
                 * (cs_of_frame(lower + 1) - cs_of_frame(lower)))


def top_neighbor(points):
    """First distinct slower cutoff; user edits and aggregates can create ties."""
    return next((point for point in points[1:] if point[0] > points[0][0]), None)


def tail_edge(points):
    """Capless IV: four divisions below the easiest cutoff, at its local pace.

    Capless V remains unbounded. Below IV the score decays toward zero, which
    stays reserved for no attempt. A one-cutoff/manual tied ladder uses one
    frame per division until more standards define a pace.
    """
    easiest, score = points[-1]
    previous = next((p for p in reversed(points[:-1]) if p[0] < easiest), None)
    step = max(1., (easiest - previous[0]) / 5) if previous else 1.
    return easiest + 4 * step, score / 5


def score_at(points, position):
    hardest, score = points[0]
    if position <= hardest:
        neighbor = top_neighbor(points)
        if neighbor is None:
            return score
        slope = (neighbor[1] - score) / (neighbor[0] - hardest)
        return min(100., score + slope * (position - hardest))
    for (fast, high), (slow, low) in zip(points, points[1:], strict=False):
        if position <= slow:
            return high + (low - high) * (position - fast) / (slow - fast)
    easiest, high = points[-1]
    end, low = tail_edge(points)
    if position <= end:
        return high + (low - high) * (position - easiest) / (end - easiest)
    return low * end / position


def position_at(points, target):
    hardest, score = points[0]
    if target >= score:
        neighbor = top_neighbor(points)
        if neighbor is None:
            return hardest
        return hardest + (target - score) * (neighbor[0] - hardest) / (neighbor[1] - score)
    for (fast, high), (slow, low) in zip(points, points[1:], strict=False):
        if target >= low:
            return fast + (target - high) * (slow - fast) / (low - high)
    if target <= 0:
        return None
    easiest, high = points[-1]
    end, low = tail_edge(points)
    if target >= low:
        return easiest + (target - high) * (end - easiest) / (low - high)
    return low * end / target
