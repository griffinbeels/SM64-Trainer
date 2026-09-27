"""The 250 Hz loop's cadence: sample every tick, snapshot once per GAME frame.

The rate exists to catch the pad AFTER the game rewrites it (~62% into each
frame). The game still produces thirty frames a second, so reading the whole
snapshot or running the detectors more often than that is pure waste -- and
the snapshot must be taken LATE in a frame, not on the first tick of it, or
the loop trades one arbitrary sampling phase for the earliest possible one.
"""
import asyncio
import statistics
import time
from datetime import datetime, timezone
from itertools import pairwise

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.memory import addresses as A
from sm64_events.server.poller import PollClock, Poller

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


class StampingSampler:
    """A pad that never changes frame, stamping when each tick looked at it."""

    def __init__(self):
        self.stamps: list[float] = []
        self.last = 7

    def sample(self):
        self.stamps.append(time.perf_counter())
        return self.last

    def flush(self):
        return None


class AttachedMemory:
    attached = True

    def detach(self):
        raise AssertionError("the loop must stay attached")


def test_the_loop_keeps_its_rate_with_nothing_raising_the_timer_resolution():
    """The loop's rate is its own, not borrowed. On Windows `asyncio.sleep`
    of 4 ms lands every 15.6 ms unless something else in the process raised
    the timer resolution -- the audio tap did -- so before audio started the
    pad was looked at twice a frame instead of eight times (64.5/s measured,
    PollClock). This process raises nothing. The median gap, not a count,
    so a loaded machine stretching a few ticks cannot turn it red."""
    sampler = StampingSampler()
    poller = Poller(memory=AttachedMemory(), detectors=[], broadcaster=NullBroadcaster(),
                    reader=CountingReader(sampler), input_sampler=sampler)

    async def run_for(seconds):
        poller.perf_stats()                     # opens the rate window here
        task = asyncio.create_task(poller.run())
        await asyncio.sleep(seconds)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return poller.perf_stats()["poll_hz"]

    poll_hz = asyncio.run(run_for(1.0))
    gaps = [b - a for a, b in pairwise(sampler.stamps)]
    assert poller.interval == 1 / Poller.SAMPLING_HZ
    assert statistics.median(gaps) <= 2 * poller.interval, (
        f"median gap {statistics.median(gaps) * 1000:.1f} ms against a "
        f"{poller.interval * 1000:.0f} ms interval")
    assert poll_hz >= Poller.SAMPLING_HZ / 2, "perf_stats reports the achieved rate"


def test_a_stalled_tick_restarts_the_schedule_instead_of_bursting():
    """Fixed-rate, but never a catch-up burst: a tick that overran by many
    intervals (a GC pause, a slow detector) gets ONE immediate tick, then the
    interval again -- not a dozen back-to-back looks at the same pad."""
    interval = 0.004

    async def stall_then_tick():
        clock = PollClock()
        await clock.wait(interval)
        time.sleep(12 * interval)  # noqa: ASYNC251 -- the loop itself stalling is the case
        started = time.perf_counter()
        await clock.wait(interval)
        immediate = time.perf_counter() - started
        started = time.perf_counter()
        for _ in range(5):
            await clock.wait(interval)
        return immediate, time.perf_counter() - started

    immediate, five = asyncio.run(stall_then_tick())
    assert immediate < interval
    assert five >= 5 * interval * 0.75, f"five ticks took {five * 1000:.1f} ms"

# The frame clock and the present watch used to be pinned here: the poller
# marked every counter edge's wall time and read PJ64's host-side present
# counter at 250 Hz, so a clip's frame map could be derived from wall
# times. THE CAPTURE LAYER stamps each picture with its own game frame, so
# nothing derives it any more -- `replay/frameclock.py`, `memory/present.py`
# and their nine tests were deleted 2026-09-05 along with the poll-loop work
# they cost every tick.
