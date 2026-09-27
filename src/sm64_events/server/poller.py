# src/sm64_events/server/poller.py
"""250 Hz poll loop: sample the pad every tick, snapshot once per GAME frame.

The rate is set by the CONTROLLER, not by the game. The game rewrites its
controller struct ~62% of the way THROUGH each frame
(`addresses.CONTROLLER_SETTLE_PHASE`), so a loop must look at the pad after
that point or it reads the previous frame's input. Measured with
`tools/probe_inputs.py` over four live sessions, 2026-08-20: at 60 Hz the last
look of a frame lands at 50% and reads FRESH on 0% of frames — one frame late,
every frame, invisibly. At 250 Hz it lands at 85% and reads fresh on 99%.

Nothing else needs that rate. The game produces thirty frames a second, so the
full snapshot and every detector run ONCE per game frame, late in it — which
is both fresher than the old loop (whose phase was wherever the sleep landed)
and cheaper: ~1.5% of a core against the ~20% the 60 Hz loop cost before the
snapshot's byte swap came out.

Two numbers that used to live in this docstring as folklore, both measured the
same day: the old `hz=60` was the REQUESTED rate and 45.9 Hz was the achieved
one (each tick worked for 3.4 ms and then slept a full interval), and no game
frame went unobserved at any rate tried — 60, 120, 250 or 500.

The rate is kept by `PollClock`, not by `asyncio.sleep`: see its docstring for
why a 4 ms sleep on the event loop lands every 15.6 ms.
"""
import asyncio
import ctypes
import sys
from asyncio import get_running_loop
from contextlib import asynccontextmanager
from sm64_events.core.profiling import measured
import logging
from datetime import datetime, timezone
from time import monotonic, perf_counter

from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot, SnapshotReader
from sm64_events.core.timefmt import GAME_FPS
from sm64_events.memory import addresses as A
from sm64_events.detectors.anchors import BOOT_TIMER_MAX
from sm64_events.core.onboarding import identify_rom, is_practice_rom
from sm64_events.memory.base import MemoryReadError

log = logging.getLogger("sm64.poller")

_CREATE_WAITABLE_TIMER_HIGH_RESOLUTION = 0x00000002
_TIMER_ALL_ACCESS = 0x1F0003


class _HighResolutionTimer:
    """One Windows high-resolution waitable timer (Win10 1803+), armed one
    shot at a time. Its own kernel32 instance, so these prototypes never
    change the ones `replay/` sets on the shared `ctypes.windll.kernel32`."""

    def __init__(self):
        self.handle = None
        if sys.platform != "win32":
            return
        k = ctypes.WinDLL("kernel32")
        k.CreateWaitableTimerExW.restype = ctypes.c_void_p
        k.CreateWaitableTimerExW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                            ctypes.c_uint32, ctypes.c_uint32]
        k.SetWaitableTimer.restype = ctypes.c_int
        k.SetWaitableTimer.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_longlong),
                                       ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_int]
        self._set = k.SetWaitableTimer
        self.handle = k.CreateWaitableTimerExW(
            None, None, _CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, _TIMER_ALL_ACCESS)

    def arm(self, seconds: float) -> bool:
        """Signal once, `seconds` from now."""
        due = ctypes.c_longlong(-max(1, int(seconds * 10_000_000)))
        return bool(self.handle) and bool(
            self._set(self.handle, ctypes.byref(due), 0, None, None, False))


class PollClock:
    """Paces the poll loop at `interval` on the high-resolution waitable timer.

    `asyncio.sleep` cannot keep this rate on Windows: the event loop's wait is
    rounded to the 15.6 ms system timer, so a 4 ms sleep lands every 15.6 ms,
    64.5 ticks/s (measured 2026-09-27, the real loop, Python 3.13). It reached
    ~220/s only while something ELSE in the process raised the timer
    resolution -- the audio tap -- so before audio started, in capture
    reconnects and in broadcast-only servers the pad was looked at about twice
    a frame and the settle point (`_settle_ticks`, 5) was never reached.
    Windows 11 ignores timeBeginPeriod for background processes
    (`replay/video.py`); the high-resolution timer is exempt.

    The timer is waited on through the event loop's own completion port
    (`IocpProactor.wait_for_handle`, which asyncio's Windows subprocesses use
    too), so the kernel wakes the loop directly. A clock THREAD calling
    `call_soon_threadsafe` does the same job at about twice the CPU -- 5.2% of
    a core against 2.7-3.1% at 250/s, measured 2026-09-27 -- because it fights
    the loop for the interpreter lock on every tick. A loop without a
    completion port, or no timer, falls back to `asyncio.sleep` (exact off
    Windows).

    Fixed-rate: a slow tick shortens the next wait instead of adding a whole
    interval, and a tick that overran by more than an interval -- or the first
    tick after a pause, a detach or a stall -- restarts the schedule rather
    than bursting. The timer is armed one shot per wait, so while the loop is
    paused or detached (waiting in seconds) nothing runs and nothing is armed.
    """

    def __init__(self):
        self._due = 0.0
        # Created at the first wait and kept for the poller's life: a wait a
        # cancelled task left registered on it must never see it closed.
        self._timer: _HighResolutionTimer | None = None

    async def wait(self, interval: float) -> None:
        """Until the next tick of an `interval` schedule (at once if it is due)."""
        now = perf_counter()
        self._due += interval
        if self._due < now - interval:
            self._due = now
        delay = self._due - now
        proactor = getattr(get_running_loop(), "_proactor", None)
        if delay > 0 and proactor is not None:
            if self._timer is None:
                self._timer = _HighResolutionTimer()
                if not self._timer.handle:
                    log.warning("no high-resolution timer; the poll rate falls "
                                "to the 15.6 ms system tick")
            if self._timer.arm(delay):
                await proactor.wait_for_handle(self._timer.handle)
                return
        await asyncio.sleep(max(0.0, delay))


def _lifecycle_event(type_: str) -> Event:
    return Event(type=type_, frame=0,
                 timestamp_utc=datetime.now(timezone.utc), payload={})


def _plausible(snap: GameSnapshot) -> bool:
    """Layout sanity: values the real game can never produce mean the
    address registry doesn't match this ROM — refuse rather than emit
    wrong star IDs (spec: hard refusal on layout mismatch)."""
    return (0 <= snap.num_stars <= 182
            and 0 <= snap.last_completed_course <= 25
            and 0 <= snap.last_completed_star <= 7)


class Poller:
    SAMPLING_HZ = 250        # with a pad to catch after the game's rewrite
    SNAPSHOT_HZ = 60         # without one: the old loop, every tick a read
    ATTACH_RETRY_S = 2.0
    LAYOUT_RETRY_S = 5.0
    #: How often a refused cartridge is looked at again, and the longest a
    #: served one goes without being identified. Swapping ROMs in Project64
    #: is a common move (vanilla for a real run, Usamune to practise), and
    #: nothing in RDRAM says it happened: 1.6 neither clears nor releases
    #: RDRAM between ROMs, so the old game's memory stays readable and
    #: plausible until the new one boots (Cpu.cpp CloseCpu, Memory.cpp
    #: Allocate_ROM). A header read is one small cached read (memory/pj64.py).
    PRACTICE_ROM_RETRY_S = 2.0
    PRACTICE_ROM_CHECK_S = 1.0

    def __init__(self, memory, detectors, broadcaster, hz: int | None = None,
                 reader=None, on_frame=None, input_sampler=None,
                 detector_factory=None, on_gap=None, on_snapshot=None,
):
        self.memory = memory
        self.detectors = list(detectors)
        self.detector_factory = detector_factory
        #: Whether the cartridge Project64 has open is a practice ROM: None
        #: until a header is read (or when the backend has none), then
        #: True/False. The recorder's capture gate reads it (main.py).
        self.practice_rom: bool | None = None
        self._rom_name: str | None = None
        self._rom_identified_at = float("-inf")
        self.on_gap = on_gap
        self._tick_lock = asyncio.Lock()
        self.broadcaster = broadcaster
        # The rate follows the sampler. Without one there is nothing to do
        # between game frames and every tick reads the whole snapshot -- so
        # 250 Hz would be eight snapshots per game frame for no reason, on
        # exactly the layouts (a version with no controller row yet) that
        # have the least to spend.
        if hz is None:
            hz = (self.SAMPLING_HZ if input_sampler is not None
                  else self.SNAPSHOT_HZ)
        # The input half runs EVERY tick; the snapshot and the detectors run
        # once per GAME frame. See this module's docstring for why the rate
        # moved and why that costs less than the 60 Hz loop it replaces.
        self.input_sampler = input_sampler
        self._frame_now: int | None = None      # the frame ticks are landing in
        self._snapshot_frame: int | None = None  # the last frame we snapshotted
        self._ticks_in_frame = 0
        # Consecutive ticks the sampler could not read. On the sampler-paced
        # path a snapshot is due only when the FRAME advances, and a dead
        # emulator advances nothing -- so the read that would notice it
        # (MemoryReadError -> detach -> re-attach) was never reached: the
        # tracker sat "attached" to a closed Project64 with a frozen
        # snapshot (2026-09-05, the first install of the capture layer:
        # PJ64 closed and reopened under a live tracker, and nothing came
        # back). Past UNREADABLE_TICKS_BEFORE_READ the tick reads anyway.
        self._unreadable_ticks = 0
        # Wait until the game has FINISHED writing a frame before reading it.
        # The pad's own rewrite lands ~62% in (addresses.CONTROLLER_SETTLE_PHASE,
        # measured); the phases of the other fields are UNMEASURED, so this is
        # "at least as late as the one field we checked", not a claim about all
        # of them. Snapshotting on the frame-change tick instead would take
        # every reading at the EARLIEST moment in a frame, which is worse than
        # the arbitrary phase it replaced.
        self._settle_ticks = max(
            1, round(A.CONTROLLER_SETTLE_PHASE * hz / GAME_FPS))
        # Awaited with the live game frame after each tick's events are
        # published — the tracker's deferred-judgement heartbeat (main.py wires
        # TrackerService.settle_frame). Injected rather than duck-typed off
        # `broadcaster`, which is sometimes a plain Broadcaster: this poller's
        # sink contract is publish(), and a clock consumer is a second concern.
        self.on_frame = on_frame
        # Called synchronously with each accepted reading, never awaited --
        # the ghost overlay follows this clock (server/ghost_clock.py) and
        # nothing on this path may wait for a browser. With no overlay open
        # the feed's own early return makes this one set test.
        self.on_snapshot = on_snapshot
        self.interval = 1.0 / hz
        self.reader = reader or SnapshotReader(memory)
        # Set when the reader can never read (core/snapshot.py::UnreadyReader
        # -- a version whose layout is not verified yet). The attach probe
        # then keeps the poller detached; /health carries the reason.
        self.hold_reason: str | None = getattr(self.reader, "reason", None)
        self.latest: GameSnapshot | None = None
        self._prev: GameSnapshot | None = None
        # last good global_timer, kept ACROSS a detach (unlike _prev) so a
        # console reset (F1) that detaches us mid-reset is still recognised on
        # reattach — see tick().
        self._last_timer: int | None = None
        self.paused = False  # session pause: run() idles — no reads, no events
        # per-tick detector-COMPUTE timing — the "performance over a session"
        # CPU signal the memory probes can't see. A climbing EMA = detector
        # dispatch slowing (the user's "inefficient function calls" hypothesis).
        # Fed to the perf monitor via perf_stats().
        self._tick_ms_ema = 0.0
        self._tick_ms_max = 0.0
        self._tick_count = 0
        self.clock = PollClock()
        # The rate run() actually achieved since the last perf_stats() read:
        # the number that fell to 65/s with nothing raising the timer
        # resolution, invisible until it was counted (2026-09-27).
        self._polls = 0
        self._polls_since = (0, monotonic())

    def set_paused(self, paused: bool) -> None:
        """Session pause (POST /api/pause): while paused, run() neither
        reads memory nor dispatches detectors — gameplay is intentionally
        unobserved (no events, no journal rows). On RESUME, _prev resets so
        detectors receive a fresh establishing pair: the same self-heal
        contract as emulator reattach (LevelChangeDetector re-establishes
        level state via corrective events; a stale open attempt closes by
        the normal next-anchor rules)."""
        if self.paused == paused:
            return
        self.paused = paused
        if paused:
            self._break_input_capture()
        if not paused:
            self._prev = None  # resume = fresh attach for detector streams
        log.info("session %s", "paused" if paused else "resumed")

    def _break_input_capture(self) -> None:
        try:
            if self.input_sampler is not None:
                self.input_sampler.flush()
        finally:
            self._frame_now = self._snapshot_frame = None
            self._ticks_in_frame = self._unreadable_ticks = 0

    async def prepare_recovery(self, reason: str) -> None:
        """Discard uncertain detector state; valid buffered input keeps its owner."""
        async with self.stream_boundary():
            if self.on_gap is not None:
                await self.on_gap(reason)
            self.memory.detach()

    def _reset_detectors(self):
        if self.detector_factory is not None:
            self.detectors = list(self.detector_factory())

    def bind_sampler(self, sampler) -> None:
        """Late store attachment needs the same capture cadence as a normal boot."""
        self._break_input_capture()
        self.input_sampler = sampler
        hz = self.SAMPLING_HZ if sampler is not None else self.SNAPSHOT_HZ
        self.interval = 1.0 / hz
        self._settle_ticks = max(1, round(A.CONTROLLER_SETTLE_PHASE * hz / GAME_FPS))

    @asynccontextmanager
    async def stream_boundary(self):
        """Finish in-flight publishing before changing the session owning input.

        This only suspends this server's polling; it never pauses emulation.
        The next pair establishes fresh detector state under the new owner.
        """
        async with self._tick_lock:
            try:
                self._break_input_capture()
                yield
            finally:
                self._prev = self.latest = None
                self._reset_detectors()

    def _due_for_a_snapshot(self) -> bool:
        """Sample the pad, and say whether this tick should read the game.

        Returns True at most once per game frame, on the first tick at or
        after the settle point — or on the tick that ENDS a frame which never
        reached it, because a frame nobody snapshotted is a frame whose events
        nobody saw.

        It does NOT decide which frame the read will describe. `tick()` takes
        that from the snapshot's own counter, because on the short-frame path
        the read lands in the frame that just STARTED and claiming otherwise
        would file a reading under a frame it cannot possibly hold.
        """
        frame_now = self.input_sampler.sample()
        if frame_now is None:
            self._unreadable_ticks += 1
            return False                       # straddled or unreadable
        self._unreadable_ticks = 0
        if frame_now != self._frame_now:
            ended = self._frame_now
            self._frame_now = frame_now
            self._ticks_in_frame = 0
            return ended is not None and self._snapshot_frame != ended
        self._ticks_in_frame += 1
        return (self._snapshot_frame != frame_now
                and self._ticks_in_frame >= self._settle_ticks)

    #: consecutive unreadable sampler ticks (half a second at 250 Hz) before
    #: the tick reads the snapshot regardless, so a dead emulator detaches
    UNREADABLE_TICKS_BEFORE_READ = 125

    @measured("poller.tick", interval=True)
    async def tick(self) -> None:
        async with self._tick_lock:
            await self._tick()

    async def _tick(self) -> None:
        if self.input_sampler is not None:
            due_for_a_snapshot = self._due_for_a_snapshot()
            if (not due_for_a_snapshot
                    and self._unreadable_ticks < self.UNREADABLE_TICKS_BEFORE_READ):
                return
            if not due_for_a_snapshot:
                # Half a second of unreadable samples: read the snapshot
                # anyway so a dead emulator raises and detaches below.
                self._unreadable_ticks = 0
        try:
            curr = self.reader.read()
            # Which frame this reading DESCRIBES comes from the snapshot's own
            # counter, never from the sampler's guess before the read.
            self._snapshot_frame = curr.global_timer
        except MemoryReadError:
            log.warning("lost emulator; detaching")
            self._break_input_capture()
            self.memory.detach()
            self._prev = None
            self.latest = None
            self._reset_detectors()
            if self.on_gap is not None:
                await self.on_gap("emulator disconnected")
            await self.broadcaster.publish(_lifecycle_event("emulator_disconnected"))
            return
        if not _plausible(curr):
            log.error("memory layout mismatch (impossible values read) — "
                      "refusing to emit events; check the address registry")
            self._break_input_capture()
            self.memory.detach()
            self._prev = None
            self.latest = None
            self._reset_detectors()
            if self.on_gap is not None:
                await self.on_gap("implausible memory snapshot")
            return
        # Reset-across-reattach synthesis (live gate 2026-06-15): an F1 console
        # reset makes RDRAM briefly implausible/unreadable, so the poller
        # detaches and reattaches — which nulls _prev and breaks the consecutive
        # pair GameResetDetector needs to see the backward-into-boot jump (live
        # journal: gGlobalTimer 206 -> [detach/reattach] -> 96, game_reset never
        # fired). _last_timer survives the gap: when the stream is freshly
        # (re)established (_prev is None) and the timer dropped from above the
        # boot range into it, emit the game_reset that was lost. In the no-detach
        # case _prev is not None, so GameResetDetector fires instead — exactly
        # one of the two fires (mirrors lifecycle.py / anchors.py).
        # (Residual edge: if reattach lands AFTER boot, timer >= BOOT_TIMER_MAX,
        # so a slow reattach can still miss it — acceptable; F1 reattach observed
        # in the boot range.)
        if await self._left_practice_rom(curr):
            return
        if (self._prev is None and self._last_timer is not None
                and self._last_timer >= BOOT_TIMER_MAX
                and curr.global_timer < BOOT_TIMER_MAX):
            await self.broadcaster.publish(Event(
                type="game_reset", frame=curr.global_timer,
                timestamp_utc=curr.wall_time_utc, payload={}))
        if self._prev is not None:
            # Time the synchronous detector COMPUTE only (not the awaited
            # broadcast I/O): collect, measure, then publish.
            out, failed = self._run_detectors(curr)
            for event in out:
                await self.broadcaster.publish(event)
            # AFTER this tick's events: an event on this frame may record the
            # very position change being judged, and closures-before-arming is
            # the order the whole engine keeps.
            if self.on_frame is not None:
                await self.on_frame(curr.global_timer)
            if failed is not None:
                await self._restart_detectors(curr, failed)
                return
        self._prev = curr
        self.latest = curr
        self._last_timer = curr.global_timer
        if self.on_snapshot is not None:
            self.on_snapshot(curr)

    def _run_detectors(self, curr) -> tuple[list, str | None]:
        """Every detector sees this pair; the name of the one that failed, if any.
        Times the synchronous compute only, not the awaited broadcast."""
        t0 = perf_counter()
        out: list[Event] = []
        failed = None
        for detector in self.detectors:
            try:
                out.extend(detector.process(self._prev, curr))
            except Exception:
                # Never let it escape: an escaped exception ended the poll task
                # while /health kept answering ok over stale state
                # (course-detection-poll-crash, 2026-09). The other detectors'
                # events on this pair still publish.
                log.exception("detector %s failed; restarting detector state",
                              type(detector).__name__)
                failed = type(detector).__name__
        self._record_tick_ms((perf_counter() - t0) * 1000)
        return out, failed

    async def _restart_detectors(self, curr, failed: str) -> None:
        """A detector may have mutated its pending state before failing: fresh
        detectors, the next pair primes them, and the gap hook hears why,
        rather than retrying that state at 30 Hz."""
        self._reset_detectors()
        self.latest = curr
        self._last_timer = curr.global_timer
        self._prev = None
        if self.on_gap is not None:
            await self.on_gap(f"detector {failed} failed")

    def _serves_loaded_rom(self) -> bool:
        """Identify the cartridge before serving it. Vanilla SM64, another
        hack or another game is a real run, not practice: nothing is read,
        detected, sampled or journalled for it (his ruling, 2026-09-16).
        Only a POSITIVE identification refuses. A header that cannot be read
        keeps the last identification, and with none it serves, so a failed
        scan never stops practice and a momentary miss mid-swap (the old
        image released, the new one not yet read) never serves a real run."""
        # A backend that can read the cartridge declares it on its class (a
        # forwarding proxy answers every name and proves nothing).
        read = getattr(type(self.memory), "rom_header", None)
        identity = identify_rom(read(self.memory) if callable(read) else None)
        self._rom_identified_at = monotonic()
        if identity["state"] == "missing":
            return self.practice_rom is not False
        practice = is_practice_rom(identity)
        if (practice, identity["name"]) != (self.practice_rom, self._rom_name):
            log.info("loaded ROM %r: %s", identity["name"],
                     "practice tooling on" if practice else
                     "not a practice ROM; tracking and replay stay off")
        self.practice_rom, self._rom_name = practice, identity["name"]
        return practice

    async def _left_practice_rom(self, curr) -> bool:
        """Is the served cartridge still a practice ROM? A new one starts
        without the emulator ever becoming unreadable, so the header is read
        again whenever the timer moves backward (a boot, a reset, a state
        load: every SM64 ROM starts its timer at zero) and at least every
        PRACTICE_ROM_CHECK_S (a game whose RAM says nothing). Both run before
        the detectors, so the new cartridge's first frame is never served.
        Leaving practice detaches like a lost emulator."""
        went_back = self._last_timer is not None and curr.global_timer < self._last_timer
        due = monotonic() - self._rom_identified_at >= self.PRACTICE_ROM_CHECK_S
        if not (went_back or due) or self._serves_loaded_rom():
            return False
        self._break_input_capture()
        self.memory.detach()
        self._prev = None
        self.latest = None
        self._reset_detectors()
        if self.on_gap is not None:
            await self.on_gap("not a practice ROM")
        return True

    def _record_tick_ms(self, dt_ms: float) -> None:
        self._tick_count += 1
        if dt_ms > self._tick_ms_max:
            self._tick_ms_max = dt_ms
        self._tick_ms_ema = (dt_ms if self._tick_count == 1
                             else 0.99 * self._tick_ms_ema + 0.01 * dt_ms)

    def perf_stats(self) -> dict:
        """Per-tick detector-compute timing for the perf monitor's gauges. The
        windowed max RESETS on read (one read per monitor interval); the EMA is
        a cumulative trend — a climbing EMA over a session is the 'inefficient
        calls building up' signature the memory probes can't catch.
        `poll_hz` is the rate run() achieved over the same window: the shipped
        rate is 250 while attached with a pad sampler, 0 while detached."""
        m = self._tick_ms_max
        self._tick_ms_max = 0.0
        (polls, since), now = self._polls_since, monotonic()
        self._polls_since = (self._polls, now)
        hz = (self._polls - polls) / (now - since) if now > since else 0.0
        return {"tick_ms_ema": round(self._tick_ms_ema, 3),
                "tick_ms_max": round(m, 3), "ticks": self._tick_count,
                "poll_hz": round(hz, 1)}

    def _probe(self) -> bool:
        """Post-attach layout check: refuse to serve a ROM whose reads are
        impossible for SM64 (e.g. wrong ROM loaded in the emulator)."""
        try:
            curr = self.reader.read()
        except MemoryReadError:
            self.memory.detach()
            return False
        if not _plausible(curr):
            log.error("memory layout mismatch (impossible values read) — "
                      "refusing to serve; check ROM / address registry")
            self.memory.detach()
            return False
        return True

    async def run(self) -> None:
        while True:
            if self.paused:
                await asyncio.sleep(0.2)  # bounds resume latency; zero reads
                continue
            if not self.memory.attached:
                if not self.memory.attach():
                    await asyncio.sleep(self.ATTACH_RETRY_S)
                    continue
                if not self._serves_loaded_rom():
                    self.memory.detach()
                    await asyncio.sleep(self.PRACTICE_ROM_RETRY_S)
                    continue
                if not self._probe():
                    await asyncio.sleep(self.LAYOUT_RETRY_S)
                    continue
                await self.broadcaster.publish(_lifecycle_event("emulator_connected"))
            await self.tick()
            self._polls += 1
            await self.clock.wait(self.interval)
