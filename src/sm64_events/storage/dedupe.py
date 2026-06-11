# src/sm64_events/storage/dedupe.py
"""Pure duplicate-detection logic for the journal dedupe tool.

Two servers polling the same emulator journal the same physical event twice:
identical (type, frame, payload) within milliseconds, under different
session ids. This module provides a pure function that finds those duplicate
ids so the tool (tools/dedupe_journal.py) can delete them.

The scan is deliberately strict:
  - same type + frame + payload-JSON AND wall timestamps within window_seconds
  - GAME event types only (star_collected, practice_reset, state_loaded,
    death, level_changed, game_reset); derived/meta events are left alone.
  - frame can repeat across console power-ons; the time window is the
    second guard against false positives.
  - Triples (3 servers) are handled: the first (lowest id) is kept; all
    later duplicates are flagged.
"""
from datetime import datetime, timezone

# Only physical game events are candidates for duplication.  Derived events
# (attempt_cleared, attempt_restored, session_started, target_set) belong to
# exactly one server's session and are never flagged.
GAME_EVENT_TYPES = frozenset({
    "star_collected",
    "practice_reset",
    "state_loaded",
    "death",
    "level_changed",
    "game_reset",
})


def _parse_wall(wall_time_utc: str) -> datetime:
    """Parse ISO-8601 wall-time string (with trailing Z or +00:00) to UTC datetime."""
    return datetime.fromisoformat(wall_time_utc.replace("Z", "+00:00"))


def find_duplicates(
    rows: list[tuple],
    window_seconds: float = 5.0,
) -> list[int]:
    """Return ids of duplicate journal rows to DELETE (keeping the earliest).

    rows: sequence of (id, type, frame, wall_time_utc, payload_json) tuples,
          ordered by id ascending (as stored in the journal).

    A row is a duplicate when:
      1. Its type is in GAME_EVENT_TYPES.
      2. An earlier row has the same (type, frame, payload_json) key.
      3. The wall-time difference between the two is <= window_seconds.

    Returns list of ids to delete (never includes the kept/earliest id).
    """
    # key -> (kept_id, kept_wall_time)
    kept: dict[tuple, tuple[int, datetime]] = {}
    duplicates: list[int] = []

    for row in rows:
        row_id, row_type, row_frame, row_wall, row_payload = row
        if row_type not in GAME_EVENT_TYPES:
            continue

        key = (row_type, row_frame, row_payload)
        wall = _parse_wall(row_wall)

        if key not in kept:
            kept[key] = (row_id, wall)
        else:
            kept_id, kept_wall = kept[key]
            delta = abs((wall - kept_wall).total_seconds())
            if delta <= window_seconds:
                # This row is a duplicate of the kept one — flag it.
                duplicates.append(row_id)
            else:
                # Same (type, frame, payload) but far apart in time:
                # a legitimate frame-number reuse (e.g. console power-on).
                # Promote this row as the new keeper for future duplicates.
                kept[key] = (row_id, wall)

    return duplicates
