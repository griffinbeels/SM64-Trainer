"""The present-counter hunt: find the one host u32 with the REAL signature.

The world here is simulated end to end -- a fake process memory whose words
are functions of a simulated clock, so the actual sweep/confirm/classify
code runs its whole course in milliseconds. The candidates mirror what two
LIVE hunts met (2026-08-25):

- the TRUE per-present counter advances +1 per game frame, ~33 ms between
  ticks, with its tick TIMES wandering against the logic clock's edges;
- a LOCKED mirror carries the same +1/33 ms shape with zero wander -- it
  is the edge series by another name, acceptable when nothing better
  exists;
- the FAKE that the first classifier selected in production: +3 every
  100 ms, a 10 Hz FPS-style accumulator whose rate and phase look perfect
  while it quantises the frame map to 100 ms. THE regression here is that
  it must never win again.
"""
import math
import struct
import time

from sm64_events.memory.base import MemoryReadError
from sm64_events.memory.present import COOLDOWN_S, PresentHunter

REGION_BASE = 0x10000
REGION_WORDS = 2048
RDRAM_BASE = 0x9000_0000

TRUE_INDEX = 100          # +1/33 ms, tick times wandering vs the edges
LOCKED_INDEX = 200        # +1/33 ms, ticking exactly ON the edges
FAKE_INDEX = 300          # +3 every 100 ms -- the production impostor
SLOW_INDEX = 400          # a 10/s counter -- sweep-filtered


def _true_tick_time(tick_index: int) -> float:
    """Tick n lands past edge n by a wandering 2-14 ms offset."""
    return tick_index / 30 + 0.008 + 0.006 * math.sin(tick_index / 15)


def true_present_count(now: float) -> int:
    guess = int(now * 30) + 2
    while guess >= 0 and _true_tick_time(guess) > now:
        guess -= 1
    return 7777 + guess + 1


class SimClock:
    def __init__(self):
        self.now = 0.0

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeHostMemory:
    """PJ64's process seen through Pj64Memory's host accessors, simulated."""

    attached = True
    rdram_host_base = RDRAM_BASE

    def __init__(self, sim: SimClock, timer_runs: bool = True):
        self.sim = sim
        self.timer_runs = timer_runs
        self.hunt_starts = 0
        self.live_indices = {TRUE_INDEX, LOCKED_INDEX, FAKE_INDEX, SLOW_INDEX}

    def _frame(self) -> int:
        return int(self.sim.now * 30) if self.timer_runs else 0

    def _word(self, index: int) -> int:
        if index in self.live_indices:
            if index == TRUE_INDEX:
                return true_present_count(self.sim.now)
            if index == LOCKED_INDEX:
                return 4000 + self._frame()
            if index == FAKE_INDEX:
                return 600 + 3 * int(self.sim.now * 10)
            if index == SLOW_INDEX:
                return 90 + int(self.sim.now * 10)
        return (index * 2654435761) & 0xFFFFFFFF

    def host_regions(self):
        self.hunt_starts += 1
        return [(REGION_BASE, REGION_WORDS * 4), (RDRAM_BASE, 0x800000)]

    def read_host_bytes(self, address: int, size: int) -> bytes:
        if address >= RDRAM_BASE:
            raise AssertionError("the sweep must exclude the RDRAM region")
        first = (address - REGION_BASE) // 4
        return b"".join(struct.pack("<I", self._word(first + index))
                        for index in range(size // 4))

    def read_host_u32(self, address: int) -> int:
        return self._word((address - REGION_BASE) // 4)

    def read_u32(self, n64_address: int) -> int:
        return 1000 + self._frame()


def hunter_over(memory, sim) -> PresentHunter:
    return PresentHunter(memory, timer_address=0xF00D,
                         sleep=sim.sleep, clock=sim.clock)


def run_hunt(hunter) -> None:
    hunter.ensure_hunting()
    thread = hunter._thread
    if thread is not None:
        # Sim sleeps make the whole hunt take milliseconds of real time.
        thread.join(timeout=30)
        assert not thread.is_alive(), "hunt did not finish"


def test_the_hunt_prefers_the_wandering_counter_over_the_locked_mirror():
    sim = SimClock()
    memory = FakeHostMemory(sim)
    hunter = hunter_over(memory, sim)
    run_hunt(hunter)
    assert hunter.address == REGION_BASE + TRUE_INDEX * 4
    assert hunter.read() == true_present_count(sim.now)


def test_the_ten_hz_accumulator_never_wins_again():
    """The production regression (2026-08-25): 0xED1F9D4 ticked +3 every
    100 ms, passed the rate and phase bands, and quantised every map it
    touched to -1..-8 slots of press error. Step size and tick gap are
    the discriminators no earlier classifier checked."""
    sim = SimClock()
    memory = FakeHostMemory(sim)
    memory.live_indices = {FAKE_INDEX, SLOW_INDEX}
    hunter = hunter_over(memory, sim)
    run_hunt(hunter)
    assert hunter.address is None


def test_a_locked_mirror_alone_is_accepted_as_the_edge_series_by_proxy():
    """Zero wander means zero display information -- but also zero harm:
    the map it yields equals the poller's own edge stamps. Scored against
    footage (attempt 1630's A-icon) it tied the wandering families."""
    sim = SimClock()
    memory = FakeHostMemory(sim)
    memory.live_indices = {LOCKED_INDEX, SLOW_INDEX}
    hunter = hunter_over(memory, sim)
    run_hunt(hunter)
    assert hunter.address == REGION_BASE + LOCKED_INDEX * 4


def test_a_counter_inside_rdram_is_out_of_bounds():
    """gGlobalTimer itself advances once per frame; the sweep must never
    look at the emulated RAM, or the map would key on the thing it
    wobbles against."""
    sim = SimClock()
    memory = FakeHostMemory(sim)
    memory.live_indices = {SLOW_INDEX}
    hunter = hunter_over(memory, sim)
    run_hunt(hunter)                     # read_host_bytes asserts on RDRAM
    assert hunter.address is None


def test_a_paused_game_is_inconclusive_and_the_hunt_cools_down():
    sim = SimClock()
    memory = FakeHostMemory(sim, timer_runs=False)
    hunter = hunter_over(memory, sim)
    run_hunt(hunter)
    assert hunter.address is None
    first_attempts = memory.hunt_starts
    hunter.ensure_hunting()              # still cooling down: no new hunt
    if hunter._thread is not None:
        hunter._thread.join(timeout=30)
    assert memory.hunt_starts == first_attempts
    sim.sleep(COOLDOWN_S + 1)
    run_hunt(hunter)                     # cooldown passed: hunts again
    assert memory.hunt_starts > first_attempts


def test_invalidate_drops_the_address_and_read_degrades_to_none():
    sim = SimClock()
    memory = FakeHostMemory(sim)
    hunter = hunter_over(memory, sim)
    run_hunt(hunter)
    assert hunter.read() is not None
    hunter.invalidate("test")
    assert hunter.address is None and hunter.read() is None


def test_a_vanished_process_reads_none_instead_of_raising():
    sim = SimClock()
    memory = FakeHostMemory(sim)
    hunter = hunter_over(memory, sim)
    run_hunt(hunter)

    def gone(_address):
        raise MemoryReadError("process exited")

    memory.read_host_u32 = gone
    assert hunter.read() is None


def test_the_default_clock_is_real_time():
    # The injectables exist for tests; production runs on the real clock.
    hunter = PresentHunter(FakeHostMemory(SimClock()), timer_address=0)
    assert hunter._sleep is time.sleep and hunter._clock is time.perf_counter
