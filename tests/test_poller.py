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


def test_perf_stats_times_detector_compute_and_resets_max():
    """The per-tick compute timing (the CPU 'performance over a session'
    signal): only ticks WITH a prev are timed; the windowed max resets on read,
    the cumulative tick count does not."""
    p = Poller(StubMemory(), [EchoDetector()], RecordingBroadcaster(),
               reader=ScriptedReader([snap(1), snap(2), snap(3)]))
    assert p.perf_stats() == {"tick_ms_ema": 0.0, "tick_ms_max": 0.0, "ticks": 0}
    asyncio.run(p.tick())            # _prev None -> establishes, not timed
    asyncio.run(p.tick())            # _prev set -> detector compute timed
    s = p.perf_stats()
    assert s["ticks"] == 1 and s["tick_ms_max"] >= 0.0
    assert p.perf_stats()["tick_ms_max"] == 0.0   # max reset on read


def test_pause_skips_everything_and_resume_self_heals():
    """Session pause: run() must touch NOTHING while paused (no attach, no
    reads); resume resets _prev so detectors get a fresh establishing pair
    instead of a stale cross-pause pair (which would look like a giant
    timer jump to every detector)."""
    b = RecordingBroadcaster()
    p = Poller(StubMemory(), [EchoDetector()], b,
               reader=ScriptedReader([snap(1), snap(2), snap(10)]))
    asyncio.run(p.tick())            # establishes prev = snap(1)
    asyncio.run(p.tick())            # pair (1,2) -> one event
    assert len(b.events) == 1

    p.set_paused(True)
    assert p.paused

    class NeverAttach:
        attached = False
        def attach(self):
            raise AssertionError("paused run() must not touch memory")
    p.memory = NeverAttach()

    async def run_briefly():
        task = asyncio.create_task(p.run())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run_briefly())       # raises inside if memory was touched

    p.set_paused(False)
    assert p._prev is None           # resume = fresh attach for detectors
    p.memory = StubMemory()
    asyncio.run(p.tick())            # establishing tick only
    assert len(b.events) == 1        # NO event from the (2, 10) gap pair


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


def test_reset_during_reattach_synthesizes_game_reset():
    """F1 console reset makes RDRAM briefly implausible/unreadable -> the poller
    detaches and reattaches, which nulls _prev and breaks the consecutive pair
    GameResetDetector needs. _last_timer survives the gap, so a fresh read that
    dropped from above the boot range (206) into it (96 < BOOT_TIMER_MAX=120)
    synthesizes the game_reset that was lost (live gate 2026-06-15)."""
    b = RecordingBroadcaster()
    p = Poller(StubMemory(), [EchoDetector()], b,
               reader=ScriptedReader([snap(206), MemoryReadError("reset"), snap(96)]))
    asyncio.run(p.tick())            # establish _prev=206, _last_timer=206
    asyncio.run(p.tick())            # read error -> detach, _prev=None
    asyncio.run(p.tick())            # snap(96): boot-range after 206 -> game_reset
    gr = [e for e in b.events if e.type == "game_reset"]
    assert len(gr) == 1 and gr[0].frame == 96
    # the reattach snapshot has no prev pair, so NO detector (tick) event fires
    assert not any(e.type == "tick" for e in b.events)


def test_no_game_reset_when_reattach_stays_mid_game():
    """A detach/reattach that stays mid-game (closed + reopened PJ64 mid-level)
    must NOT synthesize a reset — only a drop INTO the boot range counts."""
    b = RecordingBroadcaster()
    p = Poller(StubMemory(), [EchoDetector()], b,
               reader=ScriptedReader([snap(5000), MemoryReadError("x"), snap(5000)]))
    asyncio.run(p.tick()); asyncio.run(p.tick()); asyncio.run(p.tick())
    assert not any(e.type == "game_reset" for e in b.events)
