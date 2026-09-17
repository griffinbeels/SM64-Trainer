# tests/test_poller_isolation.py
import asyncio
from datetime import datetime, timezone

from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.server.poller import Poller


def snap(timer):
    return GameSnapshot(
        wall_time_utc=datetime(2026, 6, 10, tzinfo=timezone.utc),
        global_timer=timer, mario_action=0, mario_action_timer=0,
        num_stars=0, last_completed_course=0, last_completed_star=0)


class FakeMemory:
    attached = True
    def detach(self): self.attached = False


class FakeReader:
    def __init__(self): self.t = 100
    def read(self):
        self.t += 1
        return snap(self.t)


class Boom:
    def process(self, prev, curr):
        raise RuntimeError("boom")


class Emits:
    def process(self, prev, curr):
        return [Event(type="ok", frame=curr.global_timer,
                      timestamp_utc=curr.wall_time_utc, payload={})]


class Recorder:
    def __init__(self): self.events = []
    async def publish(self, event): self.events.append(event)


def test_one_bad_detector_does_not_kill_the_tick_or_starve_others():
    """The poll task must survive a detector failure: when it did not, the
    server kept answering /health ok over a dead poller and a stale course
    (his report, 2026-09: "course detection broken"). The healthy
    detector's events on that pair still publish."""
    rec = Recorder()
    poller = Poller(FakeMemory(), [Boom(), Emits()], rec, reader=FakeReader())
    asyncio.run(poller.tick())   # primes _prev; no detector runs yet
    asyncio.run(poller.tick())   # Boom raises, Emits must still publish
    assert [e.type for e in rec.events] == ["ok"]
    assert poller.memory.attached and poller.latest is not None


def test_a_failed_detector_restarts_detector_state_instead_of_retrying_it():
    """A detector may have mutated its pending state before failing, so the
    stream restarts: fresh detectors from the factory, the next pair primes
    them, and the gap hook hears why."""
    rec, gaps, built = Recorder(), [], []

    def factory():
        built.append(1)
        return [Boom() if len(built) == 1 else Emits(), Emits()]

    async def on_gap(reason):
        gaps.append(reason)

    poller = Poller(FakeMemory(), factory(), rec, reader=FakeReader(),
                    detector_factory=factory, on_gap=on_gap)
    asyncio.run(poller.tick())   # primes
    asyncio.run(poller.tick())   # Boom fails: state restarts
    assert gaps == ["detector Boom failed"] and len(built) == 2
    assert poller._prev is None   # the next pair primes the fresh detectors
    asyncio.run(poller.tick())   # primes again, no detector runs
    asyncio.run(poller.tick())   # both healthy now
    assert [e.type for e in rec.events] == ["ok", "ok", "ok"]
