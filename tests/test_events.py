# tests/test_events.py
from datetime import datetime, timezone

from sm64_events.core.events import Event, to_wire


def test_wire_format():
    ts = datetime(2026, 6, 10, 22, 14, 3, 512000, tzinfo=timezone.utc)
    ev = Event(type="star_collected", frame=81234, timestamp_utc=ts,
               payload={"course_id": 1})
    wire = to_wire(ev, seq=412)
    assert wire == {
        "v": 1,
        "seq": 412,
        "type": "star_collected",
        "frame": 81234,
        "timestamp_utc": "2026-06-10T22:14:03.512000Z",
        "payload": {"course_id": 1},
    }
