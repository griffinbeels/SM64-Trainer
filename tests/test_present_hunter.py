"""The present-counter hunt: find the one host u32 with the probe's signature.

The world here is simulated end to end -- a fake process memory whose words
are functions of a simulated clock, so the REAL sweep/confirm/classify code
runs its whole course in milliseconds. The signature candidates mirror what
the live probe met (2026-08-23): the true present counter advances one tick
per game frame with its phase wandering in runs (advance pattern 1,0,2 --
flip share ~0.33), an emulation mirror advances in lockstep (+1 every
frame -- flip share ~1.0, rejected), and everything else is static.
"""
import struct
import time

from sm64_events.memory.base import MemoryReadError
from sm64_events.memory.present import (COOLDOWN_S, PresentHunter)

REGION_BASE = 0x10000
REGION_WORDS = 2048
RDRAM_BASE = 0x9000_0000

WINNER_INDEX = 100        # the wobbling present counter
MIRROR_INDEX = 200        # a 30/s lockstep mirror of the logic clock
SLOW_INDEX = 300          # a 10/s counter -- sweep-filtered


def wobble_count(frame: int) -> int:
    """Cumulative advance over the pattern (1, 0, 2): rate 30/s, phase
    against the logic clock wandering in runs -- the probe's signature."""
    cycles, remainder = divmod(frame, 3)
    return 7777 + cycles * 3 + (0, 1, 1)[remainder]


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
        self.winner_offset = WINNER_INDEX     # tests may move it

    def _frame(self) -> int:
        return int(self.sim.now * 30) if self.timer_runs else 0

    def _word(self, index: int) -> int:
        if index == self.winner_offset:
            return wobble_count(self._frame())
        if index == MIRROR_INDEX:
            return 4000 + self._frame()
        if index == SLOW_INDEX:
            return 600 + int(self.sim.now * 10)
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
    return PresentHunter(memory, timer_address=0x8032D5D4,
                         sleep=sim.sleep, clock=sim.clock)


def run_hunt(hunter) -> None:
    hunter.ensure_hunting()
    thread = hunter._thread
    if thread is not None:
        # Sim sleeps make the whole hunt take milliseconds of real time.
        thread.join(timeout=30)
        assert not thread.is_alive(), "hunt did not finish"


def test_the_hunt_finds_the_wobbling_counter_and_only_it():
    sim = SimClock()
    memory = FakeHostMemory(sim)
    hunter = hunter_over(memory, sim)
    run_hunt(hunter)
    assert hunter.address == REGION_BASE + WINNER_INDEX * 4
    # And reading it now returns the live value.
    assert hunter.read() == wobble_count(int(sim.now * 30))


def test_a_lockstep_mirror_alone_is_never_accepted():
    sim = SimClock()
    memory = FakeHostMemory(sim)
    memory.winner_offset = -1            # world with no true counter
    hunter = hunter_over(memory, sim)
    run_hunt(hunter)
    assert hunter.address is None


def test_a_counter_inside_rdram_is_out_of_bounds():
    """gGlobalTimer itself ticks at 30/s; the sweep must never look at the
    emulated RAM, or the map would key on the thing it wobbles against."""
    sim = SimClock()
    memory = FakeHostMemory(sim)
    memory.winner_offset = -1
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
