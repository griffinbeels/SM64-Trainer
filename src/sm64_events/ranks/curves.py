"""Evaluate serialized Overall curves without their population or fitting model.

Version 1 uses PCHIP in the timer's continuous frame coordinate. Full nodes,
not the derived eight-tier ladder, determine every score. PCHIP derivatives use
the weighted harmonic mean and shape-preserving endpoint rule documented at
https://docs.scipy.org/doc/scipy/reference/generated/scipy.interpolate.PchipInterpolator.html

The fast extension is linear (quadratic if the endpoint derivative is zero),
capped at 100. The slow extension is reciprocal, matching the endpoint slope;
a zero endpoint slope uses a reciprocal quadratic. Both remain monotone.
Inverse targets are the slowest whole game frame that actually earns the score.
Legacy payloads deliberately retain scoring.py's exact custom-cutoff semantics.
"""
from __future__ import annotations

import math
from bisect import bisect_left
from collections.abc import Mapping, Sequence
from copy import deepcopy
from functools import lru_cache
from typing import Any

from sm64_events.core.timefmt import cs_of_frame
from sm64_events.ranks import scoring
from sm64_events.ranks.curve_types import CompiledCurve
from sm64_events.ranks.timecurve import frame_position

__all__ = ["compile_curve", "from_ladder", "with_anchors", "score_for", "time_for_score", "progress_for_time"]

_MIN_SCORE = float.fromhex("0x0.0000000000001p-1022")
# The browser must be able to represent and advance every returned frame/time.
_MAX_CS = 2**53 - 1
_MAX_FRAME = (_MAX_CS // 100) * 30


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        value = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return value


def _nodes(nodes: Sequence[Sequence[float]]) -> tuple[tuple[float, float], ...]:
    if not isinstance(nodes, (list, tuple)) or len(nodes) < 2:
        raise ValueError("PCHIP requires at least two nodes")
    result = []
    for node in nodes:
        if not isinstance(node, (list, tuple)) or len(node) != 2:
            raise ValueError("Each node must be [display_cs, score]")
        time, score = _number(node[0], "Node time"), _number(node[1], "Node score")
        if not 0 < time <= _MAX_CS or not 0 < score <= 100:
            raise ValueError("Node times must be positive safe numbers and scores in (0, 100]")
        if result and (time <= result[-1][0] or score >= result[-1][1]):
            raise ValueError("Node times must strictly increase and scores strictly decrease")
        result.append((time, score))
    return tuple(result)


def _ladder(ladder: Mapping[str, int]) -> dict[str, int]:
    if not isinstance(ladder, dict) or any(rank not in scoring.SCORE_ANCHORS for rank in ladder):
        raise ValueError("Curve ladder must contain only supported rank keys")
    result = {}
    previous = 0
    for rank in scoring.defined_tiers(ladder):
        value = _number(ladder[rank], "Ladder cutoff")
        if not 0 <= value <= _MAX_CS or not value.is_integer() or value < previous:
            raise ValueError("Ladder cutoffs must be nonnegative, ordered safe integers")
        result[rank] = int(value)
        previous = value
    return result


def _metadata(metadata: dict | None) -> dict:
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError("Curve metadata must be an object")
    return deepcopy(metadata) if metadata is not None else {}


def compile_curve(nodes: Sequence[Sequence[float]], metadata: dict | None = None) -> CompiledCurve:
    """Validate full PCHIP nodes and derive the tier ladder from their inverse.

    Fitting/calibration owns spacing nodes to make divisions reachable; this
    compiler never moves supplied evidence to manufacture that spacing.
    """
    checked = _nodes(nodes)
    prepared = _prepare(checked)
    ladder = {}
    for rank, score in scoring.SCORE_ANCHORS.items():
        cutoff = _inverse(prepared, score)
        if cutoff is None:
            raise ValueError(f"{rank} score {score:g} is unattainable on the supported frame grid")
        ladder[rank] = cutoff
    return {"schema_version": 1, "interpolation": "pchip",
            "nodes": [list(node) for node in checked], "ladder_cs": ladder,
            "metadata": _metadata(metadata)}


def from_ladder(ladder_cs: Mapping[str, int], metadata: dict | None = None) -> CompiledCurve:
    """Wrap an existing manual ladder, preserving its exact legacy grading."""
    ladder = _ladder(ladder_cs)
    return {"schema_version": 1, "interpolation": "legacy", "nodes": [],
            "ladder_cs": ladder, "metadata": _metadata(metadata)}


def with_anchors(curve: CompiledCurve, anchors_cs: Mapping[str, int]) -> CompiledCurve:
    """Pin selected tiers while retaining every unpinned tier cutoff.

    Interior nodes keep their relative frame position between neighboring tier
    anchors. Outer nodes translate with the closest tier. PCHIP pins must be
    whole-frame times; legacy pins retain exact hand-entered centiseconds.
    Reset belongs to the caller: apply its remaining pins to the generated curve.
    """
    ladder, prepared = _validate(curve)
    pins = _ladder(anchors_cs)
    if any(value <= 0 for value in pins.values()):
        raise ValueError("Pinned cutoffs must be positive")
    if not pins or all(ladder.get(rank) == value for rank, value in pins.items()):
        return deepcopy(curve)
    combined = ladder | pins
    try:
        combined = _ladder(combined)
    except ValueError as exc:
        raise ValueError("Pinned cutoffs cross a neighboring tier; choose ordered times") from exc
    if prepared is None:
        return from_ladder(combined, curve["metadata"])
    for rank, time in pins.items():
        if cs_of_frame(round(frame_position(time))) != time:
            raise ValueError(f"{rank} pin {time}cs is not attainable; choose a whole game-frame time")
    new_positions = [frame_position(value) for value in combined.values()]
    if any(a >= b for a, b in zip(new_positions, new_positions[1:], strict=False)):
        raise ValueError("Pinned cutoffs must leave distinct frames between neighboring tiers")
    scores = list(scoring.SCORE_ANCHORS.values())
    old_positions = [_anchor_position(prepared, ladder[rank], score)
                     for rank, score in scoring.SCORE_ANCHORS.items()]
    remapped = {score: time for time, score in zip(combined.values(), scores, strict=True)}
    for time, score in curve["nodes"]:
        if score in remapped:
            continue
        position = frame_position(time)
        if score > scores[0]:
            updated = position + new_positions[0] - old_positions[0]
        elif score < scores[-1]:
            updated = position + new_positions[-1] - old_positions[-1]
        else:
            i = next(i for i in range(len(scores) - 1) if scores[i] > score > scores[i + 1])
            fraction = (position - old_positions[i]) / (old_positions[i + 1] - old_positions[i])
            updated = new_positions[i] + fraction * (new_positions[i + 1] - new_positions[i])
        lower = math.floor(updated)
        # Unlike display_position this conversion retains fractional nodes.
        remapped[score] = (cs_of_frame(lower) + (updated - lower)
                           * (cs_of_frame(lower + 1) - cs_of_frame(lower))) if updated > 0 else 0
    try:
        result = compile_curve([[time, score] for score, time in sorted(remapped.items(), reverse=True)],
                               curve["metadata"])
    except ValueError as exc:
        raise ValueError(f"Pinned cutoffs cannot preserve the curve's ordered nodes: {exc}") from exc
    if result["ladder_cs"] != combined:
        raise ValueError("Pinned cutoffs cannot preserve all tier boundaries at game-frame resolution")
    _require_divisions(_prepare(_nodes(result["nodes"])))
    return result


def _require_divisions(prepared):
    """A new Overall edit cannot erase a division on the game-frame grid."""
    for tier in scoring.RANK_NAMES:
        low, high = scoring.tier_band(tier)
        for index, numeral in enumerate(scoring.DIVISION_NUMERALS):
            target = low + index * (high - low) / scoring.DIVISIONS_PER_TIER
            if target == 0:
                continue  # The positive asymptotic tail always reaches Iron V.
            goal = _inverse(prepared, target)
            if goal is None or scoring.division_for(_score(prepared, frame_position(goal))) != (tier, numeral):
                raise ValueError(f"Pinned cutoffs leave no attainable {tier} {numeral}; widen neighboring cutoffs")


def _anchor_position(prepared, cutoff, score):
    """Continuous tier crossing within its attainable inverse frame interval."""
    low = frame_position(cutoff)
    if _score(prepared, low) == score:
        return low
    high = low + 1
    for _ in range(52):
        middle = (low + high) / 2
        if _score(prepared, middle) >= score:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def _validate(curve: CompiledCurve):
    if not isinstance(curve, dict) or type(curve.get("schema_version")) is not int or curve["schema_version"] != 1:
        raise ValueError("Unsupported curve schema_version; expected 1")
    if curve.get("interpolation") not in ("legacy", "pchip"):
        raise ValueError("Unsupported curve interpolation")
    if not isinstance(curve.get("metadata"), dict):
        raise ValueError("Curve metadata must be an object")
    ladder = _ladder(curve.get("ladder_cs"))
    if curve["interpolation"] == "legacy":
        if curve.get("nodes") != []:
            raise ValueError("Legacy curves must have an empty node list")
        return ladder, None
    nodes = _nodes(curve.get("nodes"))
    if set(ladder) != set(scoring.SCORE_ANCHORS):
        raise ValueError("PCHIP curves require the complete derived tier ladder")
    return ladder, _prepare(nodes)


def _endpoint(h0, h1, d0, d1):
    # All secants are strictly negative; a nonnegative estimate is clamped.
    slope = ((2 * h0 + h1) * d0 - h0 * d1) / (h0 + h1)
    return min(0., slope)


@lru_cache(maxsize=512)
def _prepare(nodes):
    points = tuple((frame_position(time), score) for time, score in nodes)
    x, y = zip(*points, strict=True)
    h = [b - a for a, b in zip(x, x[1:], strict=False)]
    if any(gap <= 0 for gap in h):
        raise ValueError("Node times must remain distinct on the frame coordinate")
    secants = [(b - a) / gap for a, b, gap in zip(y, y[1:], h, strict=False)]
    if any(not math.isfinite(slope) or slope >= 0 for slope in secants):
        raise ValueError("Node slopes are not representable; separate the supplied nodes")
    if len(points) == 2:
        slopes = [secants[0], secants[0]]
    else:
        slopes = [_endpoint(h[0], h[1], secants[0], secants[1])]
        for i in range(1, len(points) - 1):
            w1, w2 = 2 * h[i] + h[i - 1], h[i] + 2 * h[i - 1]
            slopes.append((w1 + w2) / (w1 / secants[i - 1] + w2 / secants[i]))
        slopes.append(_endpoint(h[-1], h[-2], secants[-1], secants[-2]))
    if any(not math.isfinite(slope) for slope in slopes):
        raise ValueError("Node slopes are not representable; separate the supplied nodes")
    return x, y, slopes


def _score(prepared, position):
    x, y, slopes = prepared
    if position <= x[0]:
        distance = x[0] - position
        gain = -slopes[0] * distance if slopes[0] else (distance / (x[1] - x[0])) ** 2
        return min(100., y[0] + gain)
    if position >= x[-1]:
        distance = position - x[-1]
        if slopes[-1]:
            denominator = 1 + (-slopes[-1] / y[-1]) * distance
        else:
            ratio = distance / (x[-1] - x[-2])
            denominator = 1 + ratio * ratio
        return max(_MIN_SCORE, y[-1] / denominator)
    index = bisect_left(x, position)
    if position == x[index]:
        return y[index]  # Exact nodes own exact score boundaries.
    i = index - 1
    width = x[index] - x[i]
    t = (position - x[i]) / width
    # Hermite form preserves endpoints; clamp only floating-point overshoot.
    score = ((2 * t - 3) * t * t + 1) * y[i] + ((t - 2) * t + 1) * t * width * slopes[i]
    score += (-2 * t + 3) * t * t * y[index] + (t - 1) * t * t * width * slopes[index]
    return max(y[index], min(y[i], score))


def _inverse(prepared, target):
    if target <= 0 or target > 100 or _score(prepared, 0) < target:
        return None
    # Search integer frame indices directly, avoiding rounding a continuous
    # inverse onto a slower frame that does not earn the displayed goal.
    low, high = 0, max(1, math.ceil(prepared[0][-1]))
    high = min(high, _MAX_FRAME)
    while _score(prepared, high) >= target:
        if high == _MAX_FRAME:
            return None
        low, high = high, min(_MAX_FRAME, high * 2)
    while high - low > 1:
        middle = (low + high) // 2
        if _score(prepared, middle) >= target:
            low = middle
        else:
            high = middle
    return cs_of_frame(low)


def score_for(curve: CompiledCurve, time_cs: float) -> float | None:
    """Score one displayed time; an empty legacy curve is unrankable."""
    ladder, prepared = _validate(curve)
    time = _number(time_cs, "Time")
    if prepared is None:
        return scoring.score_for(ladder, time)
    if time < 0:
        raise ValueError("Curve time must be nonnegative")
    return _score(prepared, _time_position(time))


def time_for_score(curve: CompiledCurve, target_score: float) -> int | None:
    """Slowest attainable time earning the score, or None when unreachable.

    Legacy retains its historical displayed-centisecond inverse, including
    manual cutoffs that are not whole frames. PCHIP targets never round up.
    """
    ladder, prepared = _validate(curve)
    target = _number(target_score, "Target score")
    if prepared is None:
        return scoring.time_for_score(ladder, target)
    return _inverse(prepared, target)


def progress_for_time(curve: CompiledCurve, time_cs: float) -> dict | None:
    """The scoring.progress_for_time fields, graded against the full curve."""
    ladder, prepared = _validate(curve)
    time = _number(time_cs, "Time")
    if prepared is None:
        return scoring.progress_for_time(ladder, time) if ladder else None
    if time < 0:
        raise ValueError("Curve time must be nonnegative")
    score = _score(prepared, _time_position(time))
    progress = scoring.division_progress(score)
    target = _inverse(prepared, progress["next_at"]) if progress["next_at"] is not None else None
    return {"score": score, **progress,
            "next_gap_cs": time - target if target is not None else None}


def _time_position(time):
    # Beyond exact JS integers, fractional frames carry no information. Avoid
    # overflowing the browser's frame-display multiplication in the slow tail.
    return frame_position(time) if time <= _MAX_CS else time * .3
