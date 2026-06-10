# tests/test_poller.py
import asyncio
from datetime import datetime, timezone

from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.memory.base import MemoryReadError
from sm64_events.server.poller import Poller


def snap(timer: int) -> GameSnapshot:
    return GameSnapshot(
        wall_time_utc=datetime(2026, 6, 10, tzinfo=timezone.utc),
        global_timer=timer, mario_action=0, mario_action_timer=0,
        num_stars=0, last_completed_course=0, last_completed_star=0,
    )


class StubMemory:
    attached = True

    def __init__(self):
        self.detached = False

    def detach(self):
        self.detached = True


class ScriptedReader:
    def __init__(self, snapshots):
        self._snaps = list(snapshots)

    def read(self):
        item = self._snaps.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class RecordingBroadcaster:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(event)


class EchoDetector:
    """Emits one event per tick pair, tagged with both timers."""
    def process(self, prev, curr):
        return [Event(type="tick", frame=curr.global_timer,
                      timestamp_utc=curr.wall_time_utc,
                      payload={"prev": prev.global_timer})]


def test_first_tick_emits_nothing_then_detectors_run_on_pairs():
    b = RecordingBroadcaster()
    p = Poller(StubMemory(), [EchoDetector()], b,
               reader=ScriptedReader([snap(1), snap(2)]))
    asyncio.run(p.tick())
    assert b.events == []  # no prev yet
    asyncio.run(p.tick())
    assert len(b.events) == 1
    assert b.events[0].payload == {"prev": 1}
    assert p.latest.global_timer == 2


def test_read_error_detaches_and_emits_disconnected():
    mem = StubMemory()
    b = RecordingBroadcaster()
    p = Poller(mem, [EchoDetector()], b,
               reader=ScriptedReader([snap(1), MemoryReadError("gone")]))
    asyncio.run(p.tick())
    asyncio.run(p.tick())
    assert mem.detached is True
    assert [e.type for e in b.events] == ["emulator_disconnected"]
    assert p.latest is None


def test_no_stale_pair_after_reconnect():
    # after a disconnect, the next snapshot must NOT be paired with the
    # pre-disconnect one (savestate-style false edges)
    b = RecordingBroadcaster()
    p = Poller(StubMemory(), [EchoDetector()], b,
               reader=ScriptedReader([snap(1), MemoryReadError("gone"), snap(50)]))
    asyncio.run(p.tick())
    asyncio.run(p.tick())
    asyncio.run(p.tick())
    tick_events = [e for e in b.events if e.type == "tick"]
    assert tick_events == []  # snap(50) had no prev


def test_implausible_snapshot_means_layout_mismatch_and_refusal():
    # spec: never silently emit wrong star IDs — an impossible value means
    # the address layout doesn't match; detach and emit nothing
    mem = StubMemory()
    b = RecordingBroadcaster()
    bad = GameSnapshot(
        wall_time_utc=datetime(2026, 6, 10, tzinfo=timezone.utc),
        global_timer=1, mario_action=0, mario_action_timer=0,
        num_stars=29999, last_completed_course=0, last_completed_star=0,
    )
    p = Poller(mem, [EchoDetector()], b, reader=ScriptedReader([bad]))
    asyncio.run(p.tick())
    assert mem.detached is True
    assert b.events == []
    assert p.latest is None


def test_probe_accepts_plausible_layout():
    mem = StubMemory()
    p = Poller(mem, [], RecordingBroadcaster(), reader=ScriptedReader([snap(1)]))
    assert p._probe() is True
    assert mem.detached is False


def test_probe_rejects_implausible_layout_and_detaches():
    mem = StubMemory()
    bad = GameSnapshot(
        wall_time_utc=datetime(2026, 6, 10, tzinfo=timezone.utc),
        global_timer=1, mario_action=0, mario_action_timer=0,
        num_stars=29999, last_completed_course=0, last_completed_star=0,
    )
    p = Poller(mem, [], RecordingBroadcaster(), reader=ScriptedReader([bad]))
    assert p._probe() is False
    assert mem.detached is True


def test_probe_rejects_read_error_and_detaches():
    mem = StubMemory()
    p = Poller(mem, [], RecordingBroadcaster(),
               reader=ScriptedReader([MemoryReadError("gone")]))
    assert p._probe() is False
    assert mem.detached is True
