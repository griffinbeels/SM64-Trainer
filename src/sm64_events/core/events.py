# src/sm64_events/core/events.py
"""Versioned event envelope shared by every event type."""
from dataclasses import dataclass
from datetime import datetime

SCHEMA_VERSION = 1

# The event types whose payload carries Usamune's own IGT (`igt_frames`),
# stamped through detectors/igt_clock.py. THE list -- detectors/{star_grab,
# key, warp, death}.py are its only producers, and a consumer asking "could
# this closing event have given us Usamune's number?" must read it here rather
# than restate it (tests/test_single_source.py holds that door).
#
# It is a MOVING list, which is the whole reason it is written down: warp.py
# joined it 2026-07-31 (live report -- a pipe segment read 0'35"90 where
# Usamune showed 0'35"96), so attempts closed by `warp_entered` BEFORE that
# carry no igt in their journaled payload and never will. That is what makes a
# stored time and a fresh one incomparable, and `Attempt.closed_by` +
# `Attempt.timed_by` are what let a display say so.
IGT_BEARING_EVENT_TYPES = frozenset({
    "star_collected", "key_grabbed", "warp_entered", "death"})


@dataclass(frozen=True)
class Event:
    type: str
    frame: int  # game-frame stamp (gGlobalTimer units, 30 Hz)
    timestamp_utc: datetime
    payload: dict


def to_wire(event: Event, seq: int) -> dict:
    return {
        "v": SCHEMA_VERSION,
        "seq": seq,
        "type": event.type,
        "frame": event.frame,
        "timestamp_utc": event.timestamp_utc.isoformat().replace("+00:00", "Z"),
        "payload": event.payload,
    }
