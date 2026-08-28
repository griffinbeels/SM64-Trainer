"""A finished attempt's input track must be readable the moment it finishes.

His report, 2026-08-28: "It also cuts off too early and doesn't even
include the end of the clip???? for example in the HMC Swimming Beast in
the Cavern example, it's an 18"43 PB, but the input display is only 12"90.
It should be IDENTICAL in length."

Nothing was lost. The chunk writer buffers ten seconds of play before it
writes (`ChunkWriter.FLUSH_FRAMES`), which is right for crash loss and
wrong for reading: his attempt's last 164 frames were still in memory when
the timeline asked for them, so the timeline drew 387 of 551 frames and
clamped its playhead at the end of what it could see while the footage ran
on. Measured on his own db afterwards -- 551 rows, no gaps, the whole
attempt -- which is what makes this a visibility bug and not a capture one.
"""
import asyncio
from datetime import datetime, timezone

from sm64_events.core.events import Event
from sm64_events.tracking.service import TrackerService


class _NullBroadcaster:
    async def publish(self, event):
        return 1


def event(type_: str) -> Event:
    return Event(type=type_, frame=100,
                 timestamp_utc=datetime.now(timezone.utc), payload={})


def service_recording_settles():
    settled = []
    svc = TrackerService(db=None, broadcaster=_NullBroadcaster())
    svc.on_attempt_settled = lambda: settled.append(True)
    return svc, settled


def test_a_completed_attempt_settles_its_track_at_once():
    svc, settled = service_recording_settles()
    asyncio.run(svc.publish(event("attempt_completed")))
    assert settled, ("a finished attempt's track must reach the store before "
                     "he can open its drawer -- not up to 300 frames later")


def test_the_next_attempt_starting_settles_the_last_one():
    svc, settled = service_recording_settles()
    asyncio.run(svc.publish(event("practice_reset")))
    asyncio.run(svc.publish(event("state_loaded")))
    assert len(settled) == 2


def test_an_ordinary_event_does_not_settle():
    """A flush per event would write a chunk per frame; the buffer exists."""
    svc, settled = service_recording_settles()
    asyncio.run(svc.publish(event("jump")))
    asyncio.run(svc.publish(event("star_collected")))
    assert settled == []


def test_no_hook_wired_is_not_a_crash():
    svc = TrackerService(db=None, broadcaster=_NullBroadcaster())
    asyncio.run(svc.publish(event("attempt_completed")))


def test_the_writer_writes_whatever_it_holds_when_settled():
    """The hook is only as good as what close() does with a partial buffer."""
    from sm64_events.inputs.frame import InputFrame
    from sm64_events.inputs.store import ChunkWriter

    written = []

    class FakeStore:
        def append(self, session, frames, started, ended):
            written.append(list(frames))

    writer = ChunkWriter(FakeStore(), session_id=3, clock=lambda: "t")
    for number in range(50):            # far short of FLUSH_FRAMES
        writer.add(number, InputFrame(buttons=0, pressed=0, stick_x=0,
                                      stick_y=0))
    assert written == [], "nothing should be written before it is asked for"
    writer.close()
    assert len(written) == 1 and len(written[0]) == 50
