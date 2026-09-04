"""The frame map READ off the picture feed's log (round 32, item 38).

With the picture feed (config.picture_feed) the ring holds one video
frame per distinct captured picture, and the sink's feeder filed every
write in the picture ledger's feed log: (wall time the write completed,
the ledger row it carried, or None for a heartbeat repeat). ffmpeg stamps
each frame at the read that write satisfied, so a clip's frame k -- at
`start_utc + frame_times[k]` -- matches one feed entry by wall time, and
that entry names the row whose RAM stamp says which game frame the
picture is. Nothing here decodes pixels or infers picture runs: the map
is bookkeeping, and the pad reader still audits it against Usamune's own
display afterwards.

The two clocks (ffmpeg's stamp of the read; our stamp of the write's
return) sit a fraction of a millisecond apart per frame, but the
sink's segment anchor can hold a small constant offset, so the bias is
MEASURED per clip as the median delta and matching runs around it --
the same discipline `mapalign.ledger_map` learned when four hand-set
constants each came out wrong. Rows are at least 20 ms apart
(ledger.MIN_ROW_GAP_S), so a match inside FEED_MATCH_TOLERANCE_S of the
bias-corrected time is unambiguous, and a feed entry answers at most one
frame.
"""
from bisect import bisect_right

from sm64_events.replay.mapalign import unwrapped_display

# A frame's stamp and its feed entry disagree by clock jitter only, once
# the clip's bias is removed; a picture period is ~33 ms and rows never
# come closer than 20 ms, so this cannot reach a neighbour.
FEED_MATCH_TOLERANCE_S = 0.012
# Fewer matched frames than this and the log does not cover the clip.
FEED_MIN_MATCHED = 3


def _nearest(times: list[float], wall: float) -> int | None:
    at = bisect_right(times, wall)
    best = None
    for candidate in (at - 1, at):
        if 0 <= candidate < len(times) and (
                best is None
                or abs(times[candidate] - wall) < abs(times[best] - wall)):
            best = candidate
    return best


def feed_map(frame_times: list[float], start_ts: float, rows: list[dict],
             feeds: list[dict], lag_frames: int = 0, *, row_value=None):
    """(frame_map, repeats, stats) for a picture-feed clip.

    `frame_times` are the clip's own per-frame timestamps, `start_ts` the
    wall time of its media origin, `rows` the ledger rows around the span
    (composition time `ts`, RAM `frame`, `phase`), `feeds` the feed-log
    entries around it (`at`, `ts` or None). By default frame_map[k] is the
    displayed game frame of video frame k (None where nothing matched).
    ``row_value`` optionally projects the EXACT matched ledger row; the CLOCK
    path uses it to recover the coherent ``(gGlobalTimer, usamune_overall)``
    stamp on the same slot without duplicating this wall-time join. repeats[k]
    is True where the sink re-fed the previous picture, and stats report
    the match so a verdict is answerable from the sidecar. frame_map is
    None when too few frames matched.
    """
    displays = unwrapped_display(rows)
    index_by_ts = {row["ts"]: index for index, row in enumerate(rows)}
    ats = [entry["at"] for entry in feeds]
    walls = [start_ts + t for t in frame_times]
    deltas = [ats[found] - wall for wall in walls
              if (found := _nearest(ats, wall)) is not None]
    stats = {"frames": len(frame_times), "matched": 0, "repeats": 0,
             "unmatched": len(frame_times), "bias_ms": None,
             "residual_ms": {"median": None, "max": None}}
    if not deltas:
        return None, [False] * len(frame_times), stats
    bias = sorted(deltas)[len(deltas) // 2]
    stats["bias_ms"] = round(bias * 1000, 2)

    out: list = []
    repeats: list[bool] = []
    residuals: list[float] = []
    used: set[int] = set()
    repeated = 0
    for wall in walls:
        found = _nearest(ats, wall + bias)
        if (found is None or found in used
                or abs(ats[found] - (wall + bias)) > FEED_MATCH_TOLERANCE_S):
            out.append(None)
            repeats.append(False)
            continue
        used.add(found)
        residuals.append(abs(ats[found] - (wall + bias)))
        row_ts = feeds[found]["ts"]
        if row_ts is None:
            # A heartbeat: the same picture again, so the same game frame.
            out.append(out[-1] if out else None)
            repeats.append(True)
            repeated += 1
            continue
        index = index_by_ts.get(row_ts)
        if index is None:
            out.append(None)
            repeats.append(False)
            continue
        if row_value is not None:
            value = row_value(rows[index])
        else:
            value = displays.get(index)
            if value is None:
                stamp = rows[index].get("frame")
                value = None if stamp is None else stamp - lag_frames
        out.append(value)
        repeats.append(False)
    matched = sum(1 for value in out if value is not None)
    stats.update({"matched": matched, "repeats": repeated,
                  "unmatched": len(out) - matched})
    if residuals:
        ordered = sorted(residuals)
        stats["residual_ms"] = {"median": round(ordered[len(ordered) // 2] * 1000, 2),
                                "max": round(ordered[-1] * 1000, 2)}
    if matched < max(FEED_MIN_MATCHED, len(out) // 2):
        return None, repeats, stats
    return out, repeats, stats
