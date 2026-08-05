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
# `moment_reached` CARRIES an `igt_frames` (detectors/moment.py) and is
# deliberately NOT in that set, which asks a different question: "would a
# delta-timed attempt closing on this have been better off with Usamune's
# number", i.e. should the caveat mark it OLD CLOCK. For a moment the answer
# is usually no, and Lakitu Skip is the worked case.
#
# `SegmentEngine._close` believes a closing event's IGT only when Usamune's
# counter was zeroed on the very frame the segment armed, and Lakitu misses
# that by ONE: it arms on `spawned` (frame 2127 in the run below) and the
# reload's own `practice_reset` lands the frame after. So it banks the delta.
#
# THAT COSTS NOTHING, and the reason is a live measurement rather than an
# argument. Usamune zeroes its counter when Mario becomes CONTROLLABLE, not
# when the savestate loads: his run of 2026-08-05 21:43:34 touched the door on
# frame 2308 carrying `igt_frames` 181, which puts Usamune's zero at frame
# 2127 -- the spawn exactly, 52 frames after the `state_loaded` at 2075. The
# delta and Usamune's number are therefore the SAME 181, and the practice log
# read 0'06"03 beside an emulator reading 0'06"03.
#
# (An earlier draft of this comment claimed the two were ~1.7 s apart, from
# measuring `state_loaded` -> `spawned` and assuming Usamune zeroed at the
# load. It did not. Kept as a note because the wrong version would have
# justified a change that broke a working number.)
#
# So a Lakitu-shaped delta is how that segment is measured, permanently and
# correctly, exactly like a movement closing on a `level_changed` -- and
# marking it OLD CLOCK would warn on every subsection of that shape.


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
