# tests/test_poller.py
import asyncio
from datetime import datetime, timezone
from time import monotonic

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


def test_the_frame_hook_fires_after_this_tick_s_events():
    """The tracker's deferred-judgement heartbeat (live report 2026-08-02: a
    topological cancel reached the screen 27.7 s late because nothing was
    journaled meanwhile). It carries the LIVE game frame, it fires only on a
    tick that had a prev pair to dispatch, and it fires AFTER that tick's own
    events — an event on this frame may record the very move being judged."""
    b = RecordingBroadcaster()
    seen = []

    async def on_frame(frame):
        seen.append((frame, len(b.events)))

    p = Poller(StubMemory(), [EchoDetector()], b, on_frame=on_frame,
               reader=ScriptedReader([snap(1), snap(2), snap(3)]))
    asyncio.run(p.tick())            # establishing tick: no pair, no heartbeat
    assert seen == []
    asyncio.run(p.tick())
    asyncio.run(p.tick())
    assert seen == [(2, 1), (3, 2)]  # live frame, and the tick's event already out


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


class UnreadableSampler:
    """A sampler over a Project64 that has gone: every sample fails."""
    def __init__(self):
        self.calls = 0
    def sample(self):
        self.calls += 1
        return None
    def flush(self):
        pass


def test_a_dead_emulator_detaches_even_though_the_sampler_never_sees_a_frame():
    """2026-09-05, the capture layer's first install: Project64 closed and
    reopened under a live tracker, and the tracker sat 'attached' with a
    frozen snapshot for the rest of the session. On the sampler-paced path
    a snapshot was due only when the FRAME advanced, and a closed emulator
    advances nothing, so the read that raises and detaches was never
    reached. Now half a second of unreadable samples forces the read."""
    mem = StubMemory()
    b = RecordingBroadcaster()
    p = Poller(mem, [EchoDetector()], b,
               reader=ScriptedReader([MemoryReadError("gone")]),
               input_sampler=UnreadableSampler())
    for _ in range(Poller.UNREADABLE_TICKS_BEFORE_READ - 1):
        asyncio.run(p.tick())
    assert mem.detached is False           # not yet: a straddle is not a death
    asyncio.run(p.tick())
    assert mem.detached is True
    assert [e.type for e in b.events] == ["emulator_disconnected"]


def rom_header(name: bytes, country: bytes = b"E") -> bytes:
    """A cartridge header as Project64 1.6 stores it (word-swapped)."""
    header = bytearray(0x40)
    header[0:4] = b"\x80\x37\x12\x40"
    header[0x20:0x34] = name.ljust(20, b" ")
    header[0x3E] = country[0]
    return b"".join(bytes(header[at:at + 4])[::-1] for at in range(0, 0x40, 4))


class CartridgeMemory(StubMemory):
    """Attachable memory whose loaded cartridge the test chooses."""
    def __init__(self, header):
        super().__init__()
        self.header, self.attached, self.attaches = header, False, 0

    def attach(self):
        self.attaches += 1
        self.attached = True
        return True

    def detach(self):
        super().detach()
        self.attached = False

    def rom_header(self):
        return self.header


async def run_for(poller, seconds):
    task = asyncio.create_task(poller.run())
    await asyncio.sleep(seconds)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def test_a_real_run_on_another_rom_is_never_served():
    """His ruling, 2026-09-16: vanilla SM64 is a real run, not practice. The
    poller identifies the cartridge at attach and refuses it: no reads, no
    detectors, no events; the recorder's gate reads `practice_rom`."""
    b = RecordingBroadcaster()

    class NoReads:
        def read(self):
            raise AssertionError("a vanilla cartridge must not be read")

    memory = CartridgeMemory(rom_header(b"SUPER MARIO 64"))
    p = Poller(memory, [EchoDetector()], b, reader=NoReads())
    p.PRACTICE_ROM_RETRY_S = 0.01
    asyncio.run(run_for(p, 0.1))
    assert p.practice_rom is False and not memory.attached and memory.attaches >= 2
    assert b.events == []


def test_the_practice_rom_is_served_and_an_unreadable_header_still_is():
    for header, expected in [(rom_header(b"SM64 USAMUNE v1.93u"), True), (None, None)]:
        p = Poller(CartridgeMemory(header), [EchoDetector()], RecordingBroadcaster(),
                   reader=ScriptedReader([snap(5)] * 50))
        assert p._serves_loaded_rom() is True
        assert p.practice_rom is expected


def test_an_unreadable_header_keeps_the_last_identification():
    """Mid-swap Project64 has released the old image and not yet read the new
    one. A miss there must neither serve a refused real run nor stop practice."""
    memory = CartridgeMemory(rom_header(b"SUPER MARIO 64"))
    p = Poller(memory, [EchoDetector()], RecordingBroadcaster(), reader=ScriptedReader([]))
    assert p._serves_loaded_rom() is False
    memory.header = None
    assert p._serves_loaded_rom() is False and p.practice_rom is False
    memory.header = rom_header(b"SM64 USAMUNE v1.93u")
    assert p._serves_loaded_rom() is True
    memory.header = None
    assert p._serves_loaded_rom() is True and p.practice_rom is True


class BootingCartridge(CartridgeMemory):
    """Project64 with a cartridge the test swaps mid-session: each read is one
    game frame, and `insert` is closing one ROM and opening another."""
    def __init__(self, name):
        super().__init__(rom_header(name))
        self.timer = 0

    def insert(self, name, *, boots=True):
        self.header = rom_header(name)
        if boots:
            self.timer = 0           # every SM64 ROM starts gGlobalTimer at 0

    def read(self):
        self.timer += 1
        return snap(self.timer)


def test_swapping_roms_in_one_session_needs_no_restart():
    """His ask, 2026-09-16: vanilla (plain renderer) -> close -> Usamune
    (trainer) -> close -> vanilla, "without having to restart the server".
    Project64 1.6 neither clears nor releases RDRAM between ROMs, so nothing
    but the header says a swap happened; it is read before the detectors
    whenever the timer goes back and at least every PRACTICE_ROM_CHECK_S."""
    b, gaps, served = RecordingBroadcaster(), [], []
    memory = BootingCartridge(b"SUPER MARIO 64")
    memory.timer = 5000

    class Witness:
        def process(self, prev, curr):
            served.append(memory.header)
            return []

    async def on_gap(reason):
        gaps.append(reason)

    async def until(condition):
        # The deadline is a TIME, never a count of sleeps. Python 3.12's
        # monotonic clock ticks every 15.6 ms on Windows and asyncio runs a
        # timer due inside one tick at once, so 400 "5 ms" sleeps elapse in
        # under a tick: too short for the 50 ms header re-read below (3.13
        # reads a finer clock, which is why only some checkouts went red).
        deadline = monotonic() + 10
        while monotonic() < deadline:
            if condition():
                return
            await asyncio.sleep(0.005)
        raise AssertionError("the poller never got there")

    usamune, vanilla = rom_header(b"SM64 USAMUNE v1.93u"), rom_header(b"SUPER MARIO 64")

    async def session():
        p = Poller(memory, [Witness()], b, reader=memory, hz=200, on_gap=on_gap)
        p.PRACTICE_ROM_RETRY_S = 0.01
        task = asyncio.create_task(p.run())
        try:
            await until(lambda: p.practice_rom is False and memory.attaches >= 2)
            assert b.events == [] and served == []

            memory.insert(b"SM64 USAMUNE v1.93u")
            await until(lambda: len(served) >= 5)
            assert p.practice_rom is True
            assert [e.type for e in b.events] == ["emulator_connected"]

            # Back to vanilla within Usamune's first seconds: its timer never
            # left the boot range, and the swap is still caught at once.
            assert memory.timer < 120
            memory.insert(b"SUPER MARIO 64")
            await until(lambda: p.practice_rom is False)
            assert gaps == ["not a practice ROM"]
            assert vanilla not in served

            memory.insert(b"SM64 USAMUNE v1.93u")
            await until(lambda: served.count(usamune) >= 10 and p.practice_rom)
            assert [e.type for e in b.events].count("emulator_connected") == 2

            # Another game says nothing through RAM: no timer drop at all.
            p.PRACTICE_ROM_CHECK_S = 0.05
            memory.insert(b"ZELDA MAJORA'S MASK", boots=False)
            await until(lambda: p.practice_rom is False)
            assert gaps == ["not a practice ROM"] * 2
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(session())


def test_switching_to_another_rom_at_boot_stops_serving():
    """A new cartridge boots without the emulator ever becoming unreadable, so
    the timer falling back into the boot range re-identifies the ROM."""
    b, gaps = RecordingBroadcaster(), []

    async def on_gap(reason):
        gaps.append(reason)

    memory = CartridgeMemory(rom_header(b"SM64 USAMUNE v1.93u"))
    memory.attached = True
    p = Poller(memory, [EchoDetector()], b, reader=ScriptedReader([snap(900), snap(901), snap(3)]),
               on_gap=on_gap)
    asyncio.run(p.tick())
    asyncio.run(p.tick())
    assert len(b.events) == 1
    memory.header = rom_header(b"SUPER MARIO 64")
    asyncio.run(p.tick())            # boot: timer 901 -> 3
    assert p.practice_rom is False and not memory.attached
    assert gaps == ["not a practice ROM"] and len(b.events) == 1
