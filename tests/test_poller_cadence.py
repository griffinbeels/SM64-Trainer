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


def test_the_frame_clock_is_marked_on_the_edge_tick_once_per_frame():
    """The frame_map's precision IS this stamp (round 32 item 17): the mark
    must land on the 250 Hz tick that SEES the counter advance -- not at
    snapshot time, which is deliberately ~62% later -- and the attach frame
    is never marked, because its edge was not observed and stamping "now"
    onto it would file its picture under the wrong wall time."""

    class RecordingClock:
        def __init__(self):
            self.marks = []

        def mark(self, frame):
            self.marks.append(frame)

    sampler = ScriptedSampler([100] * 8 + [101] * 8 + [102] * 8)
    reader = CountingReader(sampler)
    clock = RecordingClock()
    poller = Poller(memory=None, detectors=[], broadcaster=NullBroadcaster(),
                    hz=250, reader=reader, input_sampler=sampler,
                    frame_clock=clock)
    ticks_at_mark = []
    for tick in range(24):
        asyncio.run(poller.tick())
        if len(ticks_at_mark) != len(clock.marks):
            ticks_at_mark.append(tick)
    assert clock.marks == [101, 102]        # the attach frame 100 is not marked
    assert ticks_at_mark == [8, 16]         # the edge ticks, not the settle ones


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


# --- the present watch (map v4: memory/present.py + frameclock presents) -----

class FakeHunter:
    def __init__(self, counts=None):
        self.counts = list(counts or [])
        self.at = -1
        self.address = 0xBEEF if counts else None
        self.ensured = 0
        self.invalidated: list[str] = []

    def read(self):
        if self.address is None:
            return None
        self.at += 1
        return self.counts[min(self.at, len(self.counts) - 1)]

    def ensure_hunting(self):
        self.ensured += 1

    def invalidate(self, reason=""):
        self.invalidated.append(reason)
        self.address = None


class PresentRecordingClock:
    def __init__(self):
        self.marks = []
        self.presents = []

    def mark(self, frame):
        self.marks.append(frame)

    def mark_present(self, count, timer):
        self.presents.append((count, timer))


def drive_presents(frames, counts):
    sampler = ScriptedSampler(frames)
    hunter = FakeHunter(counts)
    clock = PresentRecordingClock()
    poller = Poller(memory=None, detectors=[], broadcaster=NullBroadcaster(),
                    hz=250, reader=CountingReader(sampler),
                    input_sampler=sampler, frame_clock=clock,
                    present_hunter=hunter)
    for _ in frames:
        asyncio.run(poller.tick())
    return hunter, clock


def test_present_ticks_reach_the_clock_except_the_unobserved_first():
    """The first value after acquisition is mid-count (its edge was not
    seen), so it establishes state without marking -- the same rule the
    logic-edge stamp follows -- and every OBSERVED tick marks with the
    game frame read on the same poll tick."""
    frames = [100] * 8 + [101] * 8 + [102] * 8
    counts = [700] * 12 + [701] * 6 + [702] * 6
    _hunter, clock = drive_presents(frames, counts)
    assert clock.presents == [(701, 101), (702, 102)]


def test_a_frozen_present_counter_is_invalidated_by_the_watchdog():
    """Heap reuse or a plugin restart leaves a dead address that reads a
    constant. The game advancing PRESENT_FROZEN_FRAMES past the last tick
    proves it dead -- a live counter ticks every frame."""
    span = Poller.PRESENT_FROZEN_FRAMES + 3
    frames = [frame for frame in range(100, 100 + span) for _ in range(2)]
    counts = [700] * (span * 2)
    hunter, clock = drive_presents(frames, counts)
    assert hunter.invalidated, "watchdog never fired"
    assert clock.presents == []


def test_no_address_means_hunting_not_marking():
    frames = [100] * 8 + [101] * 8
    sampler = ScriptedSampler(frames)
    hunter = FakeHunter()                       # no address yet
    clock = PresentRecordingClock()
    poller = Poller(memory=None, detectors=[], broadcaster=NullBroadcaster(),
                    hz=250, reader=CountingReader(sampler),
                    input_sampler=sampler, frame_clock=clock,
                    present_hunter=hunter)
    for _ in frames:
        asyncio.run(poller.tick())
    assert hunter.ensured >= 1                  # frames advanced: hunt asked
    assert clock.presents == []


def test_without_a_hunter_the_loop_is_exactly_as_it_was():
    frames = [100] * 8 + [101] * 8
    sampler = ScriptedSampler(frames)
    reader = CountingReader(sampler)
    poller = Poller(memory=None, detectors=[], broadcaster=NullBroadcaster(),
                    hz=250, reader=reader, input_sampler=sampler)
    for _ in frames:
        asyncio.run(poller.tick())
    assert reader.reads == 2


def test_an_unreadable_address_also_trips_the_watchdog():
    """After a full emulator restart the old heap address may be unmapped:
    every read fails. That must invalidate (and re-hunt), not hang forever
    with an address nothing can read."""
    span = Poller.PRESENT_FROZEN_FRAMES + 3
    frames = [frame for frame in range(100, 100 + span) for _ in range(2)]
    hunter, clock = drive_presents(frames, [None] * (span * 2))
    assert hunter.invalidated
    assert clock.presents == []


def test_a_console_reset_rebaselines_instead_of_invalidating():
    """F1 drops the game clock backward while the emulator process -- and
    the counter's heap -- survive. Throwing the address away would cost a
    half-minute hunt after his commonest gesture; the watchdog re-baselines
    on the backward jump and only a FORWARD outrun kills the address."""
    frames = ([300] * 4                              # baseline at 300
              + [100] * 4                            # F1: clock restarts
              + [frame for frame in range(101, 201) for _ in range(2)])
    counts = [700] * len(frames)                     # counter frozen anyway
    hunter, _clock = drive_presents(frames, counts)
    assert not hunter.invalidated, \
        "a backward jump plus <120 frames of play must not invalidate"
    longer = frames + [frame for frame in range(201, 240) for _ in range(2)]
    hunter2, _clock2 = drive_presents(longer, [700] * len(longer))
    assert hunter2.invalidated                       # forward outrun DOES
