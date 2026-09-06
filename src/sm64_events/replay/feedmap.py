"""Associate encoded pictures by their retained encoder-run and source PTS.

A regular cadence cannot reveal a whole-frame offset: fitting the median
nearest-time residual certified neighboring pictures on the Wild Blue clips.
The encoder and extractor now retain their clock. Matching is an exact key
lookup, with missing or colliding keys left unknown. No fitted offset exists.
"""
from collections import Counter


def feed_map(frame_pts: list[int] | None, run_id: str | None,
             rows: list[dict], feeds: list[dict], row_value):
    """Return (values, repeats, stats), one value per decoded video slot.

    `frame_pts` are source MPEG-TS ticks (90 kHz), recovered by the extractor
    using that source's retained origin. Feeds carry the same run ID and PTS,
    plus the captured row's timestamp. A heartbeat explicitly retains its row,
    including when the cut starts during a hold. `row_value` owns interpretation
    of the matched capture; it cannot change which capture the picture contains.
    """
    points = frame_pts or []
    stats = {"frames": len(points), "matched": 0, "repeats": 0,
             "unmatched": len(points), "method": "source_pts", "run_id": run_id}
    if not points or not run_id:
        stats["reason"] = "missing_source_clock"
        return None, [False] * len(points), stats

    # Reject every occurrence of a colliding key. Picking the first or last
    # silently chooses a picture when the source has lost that distinction.
    scoped = [entry for entry in feeds if entry.get("run_id") == run_id]
    feed_counts = Counter(entry.get("pts") for entry in scoped)
    slot_counts = Counter(points)
    row_counts = Counter(row["ts"] for row in rows)
    by_pts = {entry["pts"]: entry for entry in scoped
              if entry.get("pts") is not None and feed_counts[entry["pts"]] == 1}
    by_ts = {row["ts"]: row for row in rows if row_counts[row["ts"]] == 1}
    values, repeats = [], []
    for point in points:
        entry = by_pts.get(point) if slot_counts[point] == 1 else None
        row = by_ts.get(entry["ts"]) if entry else None
        values.append(row_value(row) if row is not None else None)
        repeats.append(bool(entry and entry.get("repeat")))
    matched = sum(value is not None for value in values)
    stats.update(matched=matched, unmatched=len(points) - matched,
                 repeats=sum(repeats))
    return (values if matched else None), repeats, stats
