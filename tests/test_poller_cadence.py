"""The 250 Hz loop's cadence: sample every tick, snapshot once per GAME frame.

The rate exists to catch the pad AFTER the game rewrites it (~62% into each
frame). The game still produces thirty frames a second, so reading the whole
snapshot or running the detectors more often than that is pure waste -- and
the snapshot must be taken LATE in a frame, not on the first tick of it, or
the loop trades one arbitrary sampling phase for the earliest possible one.
"""
import asyncio
from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.memory import addresses as A
from sm64_events.server.poller import Poller

TICKS_PER_FRAME = 250 // 30                      # 8 at the shipped rate
SETTLE_TICK = round(A.CONTROLLER_SETTLE_PHASE * TICKS_PER_FRAME)


class CountingReader:
    """Answers with whatever frame the sampler last reported."""

    def __init__(self, sampler):
        self.sampler = sampler
        self.reads = 0
        self.frames_read: list[int] = []

    def read(self):
        self.reads += 1
        frame = self.sampler.last
        self.frames_read.append(frame)
        return GameSnapshot(wall_time_utc=datetime.now(timezone.utc),
                            global_timer=frame, mario_action=0,
                            mario_action_timer=0, num_stars=0,
                            last_completed_course=0, last_completed_star=0)


class ScriptedSampler:
    def __init__(self, counters):
        self.counters = list(counters)
        self.index = -1
        self.last = None

    def sample(self):
        self.index += 1
        self.last = self.counters[min(self.index, len(self.counters) - 1)]
        return self.last


class CountingDetector:
    def __init__(self):
        self.calls = 0

    def process(self, prev, curr):
        self.calls += 1
        return []


class NullBroadcaster:
    async def publish(self, event):
        return None


def drive(counters):
    sampler = ScriptedSampler(counters)
    reader = CountingReader(sampler)
    detector = CountingDetector()
    poller = Poller(memory=None, detectors=[detector],
                    broadcaster=NullBroadcaster(), hz=250, reader=reader,
                    input_sampler=sampler)
    for _ in counters:
        asyncio.run(poller.tick())
    return reader, detector


def test_the_snapshot_is_read_once_per_GAME_frame_not_once_per_tick():
    reader, detector = drive([100] * 8 + [101] * 8 + [102] * 8)
    assert reader.frames_read == [100, 101, 102]
    assert detector.calls == 2        # one consecutive pair per frame advance


def test_the_snapshot_is_taken_LATE_in_the_frame_not_on_its_first_tick():
    """Gating on "the frame changed" would snapshot at the EARLIEST moment in
    a frame, which is the worst one -- the game has not finished writing it."""
    sampler = ScriptedSampler([100] * 8 + [101] * 8)
    reader = CountingReader(sampler)
    poller = Poller(memory=None, detectors=[], broadcaster=NullBroadcaster(),
                    hz=250, reader=reader, input_sampler=sampler)
    for tick in range(8):
        asyncio.run(poller.tick())
        assert reader.reads == (1 if tick >= SETTLE_TICK else 0), (
            f"tick {tick}: the snapshot must wait until the game has written "
            f"the frame (tick {SETTLE_TICK} of {TICKS_PER_FRAME})")


def test_a_frame_that_ends_before_settling_still_forces_a_read():
    """Emulator lag (or our own stall) can end a frame in fewer ticks than the
    settle point. Reading immediately gives one observed frame instead of
    none -- and what it describes is the frame that just STARTED, which is
    why tick() takes the frame number from the snapshot rather than from the
    sampler's guess."""
    reader, _detector = drive([100, 100] + [101] * 10)
    assert reader.reads >= 1
    assert reader.frames_read[0] == 101


def test_a_short_frame_does_not_cause_its_successor_to_be_read_twice():
    reader, _detector = drive([100, 100] + [101] * 10)
    assert reader.frames_read == [101]


def test_no_sampler_leaves_the_loop_exactly_as_it_was():
    """Every existing poller test drives tick() with no sampler, so the gate
    must be invisible without one or those tests stop meaning anything."""
    sampler = ScriptedSampler([100] * 4)
    reader = CountingReader(sampler)
    poller = Poller(memory=None, detectors=[], broadcaster=NullBroadcaster(),
                    hz=250, reader=reader)
    for _ in range(4):
        asyncio.run(poller.tick())
    assert reader.reads == 4


def test_the_rate_follows_the_sampler():
    """250 Hz exists to catch the pad after the game's rewrite. Without a
    sampler every tick reads the whole snapshot, so the loop keeps the old
    60 Hz rather than reading eight snapshots per game frame for nothing --
    which is what a layout with no controller row would otherwise pay."""
    sampler = ScriptedSampler([100])
    with_pad = Poller(memory=None, detectors=[], broadcaster=NullBroadcaster(),
                      reader=CountingReader(sampler), input_sampler=sampler)
    without = Poller(memory=None, detectors=[], broadcaster=NullBroadcaster(),
                     reader=CountingReader(sampler))
    assert round(1 / with_pad.interval) == 250
    assert round(1 / without.interval) == 60
