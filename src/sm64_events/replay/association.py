"""Validate retained source identities before interpreting cached timing claims.

This checks the media-to-capture link, not the plugin's pixel/state convention.
Derived maps and timers are rebuilt by the same interpreter as a fresh cut.
Legacy timestamps or a fitted bias cannot manufacture the missing source link.
"""
from datetime import datetime
import math

from sm64_events.replay.media import MEDIA_HZ, MediaRun


def _number(value) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def valid_picture_times(times) -> bool:
    """A usable ordered media clock, including safe conversion to source ticks."""
    return (isinstance(times, list) and bool(times)
            and all(_number(t) and _number(t * MEDIA_HZ) for t in times)
            and all(a < b for a, b in zip(times, times[1:], strict=False)))


def _clock_problem(meta, clock) -> str | None:
    if (type(clock.get("version")) is not int or clock["version"] != 1
            or clock.get("time_base") != MEDIA_HZ
            or not isinstance(clock.get("run_id"), str) or not clock["run_id"]
            or not _number(clock.get("origin_ts"))):
        return "invalid_source_clock"
    times, points = meta.get("frame_times"), clock.get("source_pts")
    if (not valid_picture_times(times) or not isinstance(points, list)
            or len(times) != len(points)
            or not all(type(p) is int for p in points)
            or any(a >= b for a, b in zip(points, points[1:], strict=False))):
        return "invalid_source_clock"
    try:
        start = datetime.fromisoformat(meta["start_utc"].replace("Z", "+00:00"))
        if start.tzinfo is None:
            return "invalid_source_clock"
        offset = MediaRun(clock["run_id"], clock["origin_ts"]).ticks_at(start.timestamp())
        # This is the retained origin, not an offset fitted against periodic data.
        matches = all(point == offset + round(at * MEDIA_HZ)
                      for at, point in zip(times, points, strict=True))
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        return "invalid_source_clock"
    if not matches:
        return "source_clock_mismatch"
    match = meta.get("feed_match")
    if (not isinstance(match, dict) or match.get("method") != "source_pts"
            or match.get("run_id") != clock["run_id"]):
        return "missing_source_match"
    return None


def association_problem(meta: dict) -> str | None:
    """None means the retained source link can be interpreted; else a reason."""
    clock = meta.get("media_clock")
    if not isinstance(clock, dict):
        return "missing_source_clock"
    problem = _clock_problem(meta, clock)
    if problem:
        return problem
    rows, captures = meta.get("picture_ledger"), meta.get("picture_rows")
    if (not isinstance(rows, list) or not rows
            or not all(isinstance(row, dict) for row in rows)
            or not isinstance(captures, list) or len(captures) != len(clock["source_pts"])):
        return "invalid_capture_references"
    # A preceding row can supply a picture's state without appearing in this
    # cut. Validate it too before the interpreter walks backward through it.
    for row in rows:
        if (any(row.get(key) is not None and type(row[key]) is not int
                for key in ("frame", "igt_overall"))
                or ("exact" in row and type(row["exact"]) is not bool)):
            return "invalid_capture_references"
    for capture in captures:
        if capture is None:
            continue
        if type(capture) is not int or not 0 <= capture < len(rows):
            return "invalid_capture_references"
        row = rows[capture]
        if row.get("exact") is not True or type(row.get("frame")) is not int:
            return "invalid_capture_references"
    return None
