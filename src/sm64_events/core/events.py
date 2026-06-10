# src/sm64_events/core/events.py
"""Versioned event envelope shared by every event type."""
from dataclasses import dataclass
from datetime import datetime

SCHEMA_VERSION = 1


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
